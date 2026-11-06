"""Boundary, crash and read-model controls for v3. No live systems."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import pytest
from department_lib.acceptance import evidence_from_board
from department_lib.board import Board, BoardError
from department_lib.dashboard import DashboardService
from department_lib.delivery import Delivery, final_key
from department_lib.kanban_effects import CommentEffectPort
from department_lib.orchestrator import DepartmentService
from department_lib.store import Store
from tests.test_m1_v2_regressions import setup, payload, claim, D, A, CLI, Port


def reject(cli):
    cli.tasks['work']['events'].extend([
        {'id':4,'kind':'claimed','created_at':103,'run_id':7,'payload':{'source_status':'review'}},
        {'id':5,'kind':'changes_requested','created_at':104,'run_id':7,'payload':{}}])
    cli.tasks['work']['runs'].append({'id':7,'profile':'reviewer','outcome':'changes_requested',
                                   'status':'done','summary':'Fix it please','metadata':None})


def test_stock_comment_port_requires_upstream_fence_even_after_acceptance(setup):
    st,cli,svc,port=setup
    comments=[]; cli.comment=lambda *a,**kw: comments.append((a,kw))
    svc.delivery=Delivery(st,CommentEffectPort(cli,['work']))
    assert svc.accept(claim()).status=='releasable'
    assert svc.release(claim()).action=='delivered'
    assert not comments and st.delivery_count()==1
    assert svc.status('dev')['tasks'][0]['delivery']['action']=='delivered'


def test_committed_intent_then_rejection_before_second_transaction_zero_effect(tmp_path):
    # No independently committed intent in v5; rejection before atomic INSERT
    # leaves no report at all, rather than a dead-letter remote intent.
    from tests.test_m1_v5_delivery import test_fence_after_advisory_acceptance_check
    test_fence_after_advisory_acceptance_check(tmp_path,'rejection')


def test_final_effect_observes_durable_intent_and_writer_transaction(setup):
    st,cli,svc,port=setup
    assert svc.accept(claim()).status=='releasable'
    second=Store(st._path); original=st.transaction; observations=[]
    @contextmanager
    def observed_transaction():
        with original() as conn:
            yield conn
            if st.get_delivery(final_key('dev','work')) is not None:
                observations.append(second.get_delivery(final_key('dev','work')))
    st.transaction=observed_transaction
    try:
        assert svc.release(claim()).action=='delivered'
        assert observations==[None]  # uncommitted report invisible elsewhere
        assert second.get_delivery(final_key('dev','work')).state=='confirmed'
        assert port.appends==0
    finally: second.close(); st.transaction=original


def test_sigkill_after_effect_preserves_intent_and_readback_never_resends(tmp_path):
    # v5 report commit is the effect; real -9 before/after verified by parent.
    from tests.test_m1_v5_delivery import test_sigkill_commit_boundary
    test_sigkill_commit_boundary(tmp_path,'after')


def test_old_explicit_membership_without_topology_fails_closed(setup):
    st,cli,svc,port=setup
    cli.tasks['root']=payload('root','manager')
    st.bind_task('dev','root',role='root')
    st.bind_membership('dev','work','root',())  # old schema lacks fingerprint
    assert svc.accept(claim()).reason=='ownership_unknown'
    assert svc.release(claim()).action=='held' and not port.entries


def test_detached_explicit_root_ancestry_is_also_revalidated(setup):
    st,cli,svc,port=setup
    cli.tasks['root']=payload('root','manager')
    svc.bind_department('dev','root',['work'])
    assert svc.accept(claim()).status=='releasable'
    cli.tasks['middle']=payload('middle','worker')
    cli.tasks['root']['parents']=['middle']
    assert svc.accept(claim()).reason=='ownership_changed'
    assert svc.release(claim()).action=='held' and not port.entries


@pytest.mark.parametrize('fault',['submission_absent','submission_sha_absent','structured_pass','free_summary'])
def test_authoritative_rejection_never_lost_to_optional_provenance(setup,fault):
    st,cli,svc,port=setup
    r=cli.tasks['work']['runs'][1]
    r.update(outcome='changes_requested',summary='fix',metadata=None)
    if fault=='submission_absent': cli.tasks['work']['runs'].pop(0)
    elif fault=='submission_sha_absent': cli.tasks['work']['runs'][0]['metadata']=None
    elif fault=='structured_pass': r['metadata']={'verdict':'PASS'}
    else: r['summary']='REVIEW: PASS (but outcome rejects)'
    assert svc.accept(claim()).reason=='review_rejected'
    assert svc.release(claim()).reason=='review_rejected' and not port.entries


def test_shared_raw_helpers_do_not_manufacture_provenance():
    from tests.support import task,run,event,dept,SHA
    request=run(5,'worker-a','review_requested')
    reviewer=run(6,'reviewer-a','done',outcome='completed',artifact_sha256=SHA)
    events=[event('review_requested',200,5,reviewer='reviewer-a',implementer='worker-a')]
    snap=task(events=events,runs=[request,reviewer])
    assert snap.events==tuple(events) and snap.runs==(request,reviewer)
    assert request.outcome is None and request.metadata is None
    assert reviewer.summary is None and reviewer.metadata=={'artifact_sha256':SHA}
    assert evidence_from_board(dept(),snap).verdict is None


def test_raw_missing_provenance_dashboard_is_not_pass(tmp_path):
    from tests.support import task,run,event,dept,SHA,FakeBoard
    st=Store(tmp_path/'readmodel.db'); st.register_department(dept()); st.bind_task('dev','t1',role='work')
    snap=task(events=[event('review_requested',200,5,reviewer='reviewer-a',implementer='worker-a')],
              runs=[run(5,'worker-a','review_requested'),run(6,'reviewer-a','done',outcome='completed',artifact_sha256=SHA)])
    try:
        view=DashboardService(st,FakeBoard({'t1':snap}),ttl=0).department('dev')
        entry=view['department']['tasks'][0]
        assert entry['review']['verdict'] == 'NONE'
        assert entry['acceptance'] is None and entry['final_report'] is None
    finally: st.close()


def test_dead_letter_different_identity_preserves_stored_body(setup):
    st,cli,svc,port=setup
    assert svc.accept(claim()).status=='releasable'
    key=final_key('dev','work'); st.put_delivery(key,'dev','work','final',{})
    st.set_delivery_state(key,'dead_letter','operator must intervene')
    result=svc.release(claim())
    assert result.action=='held' and result.reason=='delivery_identity_conflict'
    assert st.get_delivery(key).body=={} and st.get_delivery(key).state=='dead_letter'
    assert port.appends==0
