"""Raw producer-compatible P1 contract; isolated SQLite and synthetic ports only."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import importlib.util
import threading

import pytest
from department_lib.acceptance import evidence_from_board, evaluate, AcceptanceClaim
from department_lib.delivery import Delivery, final_key
from department_lib.models import BoardEvent, Department
from department_lib.orchestrator import DepartmentService
from department_lib.store import Store
from department_lib.board import Board
from tests.test_m1_v2_regressions import setup, payload, claim, D, A, CLI, Port
from tests.plugin_loader import load_plugin


def test_cli_free_text_rejection_without_metadata_verdict(setup):
    st,cli,svc,port=setup
    r=cli.tasks['work']['runs'][1]
    r.update(outcome='changes_requested', summary='Please fix the race before release', metadata=None)
    cli.tasks['work']['events'].append({'id':4,'kind':'changes_requested','created_at':103,'run_id':6,'payload':{'reason':'fix race'}})
    ev=evidence_from_board(D,svc.board.get('work'))
    assert ev.verdict is not None and ev.verdict.kind=='REQUEST_CHANGES'
    assert svc.accept(replace(claim(),revision=2)).reason=='review_rejected'
    assert svc.release(replace(claim(),revision=2)).reason=='review_rejected'
    assert not port.entries and st.delivery_count()==0


def test_cli_completed_with_free_text_without_structured_verdict(setup):
    """v4: only an explicit PASS token grants authority; friendly prose holds."""
    st,cli,svc,port=setup
    r=cli.tasks['work']['runs'][1]
    r['metadata'].pop('verdict'); r['summary']='All requested checks passed; details in report.'
    held=svc.accept(claim())
    assert held.status=='held' and held.reason=='review_unproven'
    assert svc.release(claim()).action=='held' and not port.entries
    r['summary']='REVIEW: PASS'
    assert svc.accept(claim()).status=='releasable'
    assert svc.release(claim()).action=='delivered' and len(port.entries)==0


@pytest.mark.parametrize('fault',['reparent','same_root_rewire','unreadable','multi_root','cycle'])
def test_full_ancestry_revalidated_after_explicit_binding(setup,fault):
    st,cli,svc,port=setup
    cli.tasks['root']=payload('root','manager')
    cli.tasks['other']=payload('other','manager')
    cli.tasks['middle']=payload('middle','worker',['root'])
    cli.tasks['work']['parents']=['middle']
    svc.bind_department('dev','root',['work'])
    st.bind_task('dev','other',role='root')
    assert svc.accept(claim()).status=='releasable'
    if fault=='reparent': cli.tasks['middle']['parents']=['other']
    elif fault=='same_root_rewire':
        cli.tasks['newmiddle']=payload('newmiddle','worker',['root'])
        cli.tasks['middle']['parents']=['newmiddle']
    elif fault=='unreadable': cli.fail.add('middle')
    elif fault=='multi_root': cli.tasks['middle']['parents']=['root','other']
    else: cli.tasks['middle']['parents']=['middle']
    a=svc.accept(claim()); r=svc.release(claim()); e=svc.escalate('work','blocked',{'round':1})
    assert a.status=='held' and a.reason in ('ownership_changed','ownership_unknown')
    assert r.action=='held' and e.action=='held'
    assert svc._root_for_task('dev','work') is None
    assert not port.entries and st.delivery_count()==0


@pytest.mark.parametrize('stage',['accept','release'])
def test_uningested_snapshot_collision_fails_closed(setup,stage):
    st,cli,svc,port=setup
    if stage=='release': assert svc.accept(claim()).status=='releasable'
    cli.tasks['work']['events'].append({'id':1,'kind':'blocked','created_at':103,'run_id':6,'payload':{}})
    result=svc.accept(claim()) if stage=='accept' else svc.release(claim())
    assert result.reason=='event_quarantined'
    assert not port.entries and st.delivery_count()==0


@pytest.mark.parametrize('ingest',[False,True])
def test_old_source_id_collision_cannot_hide_behind_timestamp(setup,ingest):
    st,cli,svc,port=setup
    old=BoardEvent('work','blocked',None,50,{},source_id=2)
    st.record_event(old,'dev')
    if ingest: svc.ingest('work')
    assert svc.accept(claim()).reason=='event_quarantined'
    assert svc.release(claim()).reason=='event_quarantined'
    assert not port.entries


def test_same_run_later_rejection_cannot_become_new_revision_pass(setup):
    st,cli,svc,port=setup
    cli.tasks['work']['events'].append({'id':4,'kind':'changes_requested','created_at':103,'run_id':6,'payload':{}})
    c=replace(claim(),revision=2)
    assert svc.accept(c).status=='held'
    assert svc.release(c).action=='held'
    assert not port.entries


def test_release_paused_after_acceptance_then_new_rejection_zero_effect(setup):
    st,cli,svc,port=setup
    assert svc.accept(claim()).status=='releasable'
    entered,proceed=threading.Event(),threading.Event(); results=[]; errors=[]
    class Paused(Delivery):
        def release_final(self,*args,**kwargs):
            entered.set(); assert proceed.wait(8)
            return super().release_final(*args,**kwargs)
    svc.delivery=Paused(st,port)
    def release():
        try: results.append(svc.release(claim()))
        except BaseException as e: errors.append(e)
    t=threading.Thread(target=release); t.start(); assert entered.wait(8)
    # A genuinely separate connection commits rejection before release resumes.
    other=Store(st._path)
    try:
        cli.tasks['work']['events'].extend([
            {'id':4,'kind':'claimed','created_at':103,'run_id':7,'payload':{'source_status':'review'}},
            {'id':5,'kind':'changes_requested','created_at':104,'run_id':7,'payload':{}}])
        cli.tasks['work']['runs'].append({'id':7,'profile':'reviewer','outcome':'changes_requested','status':'done','summary':'Please repair','metadata':None})
        DepartmentService(other,svc.board,Delivery(other,port),svc.router).ingest('work')
        assert other.active_acceptance('dev','work') is None
    finally:
        proceed.set(); t.join(8); other.close()
    assert not t.is_alive() and not errors
    assert results[0].action=='held'
    assert not port.entries and st.delivery_count()==0


def test_escalation_full_identity_conflict_returns_actual_sent_body(setup):
    st,cli,svc,port=setup
    first=svc.delivery.escalate(D,'work','blocked',{'round':1})
    second=svc.delivery.escalate(D,'work','blocked',{'round':2})
    assert first.action=='delivered'
    assert second.action=='held' and second.reason=='delivery_identity_conflict'
    assert second.delivered_identity['body']['detail']=={'round':1}
    assert len(port.entries)==1
    assert st.escalations_for_task('dev','work')==[{'reason_code':'blocked','detail':{'round':1}}]


def test_escalation_public_handler_conflict_is_not_success(setup):
    st,cli,svc,port=setup
    cli.tasks['root']=payload('root','manager'); svc.bind_department('dev','root',['work'])
    path=Path(__file__).parents[1]/'__init__.py'
    module=load_plugin()
    args={'task_id':'work','reason_code':'blocked','detail':{'round':1}}
    assert module._handle_escalate(svc,args,caller_profile='manager')['success']
    args['detail']={'round':2}
    result=module._handle_escalate(svc,args,caller_profile='manager')
    assert result['success'] is False and result['reason']=='delivery_identity_conflict'
    assert result['delivered_identity']['body']['detail']=={'round':1}
    assert len(port.entries)==1


def test_matching_identity_dead_letter_positive_control(setup):
    st,cli,svc,port=setup
    assert svc.accept(claim()).status=='releasable'
    task=svc.board.get('work')
    gate=evaluate(dept=D,task=task,claim=claim(),artifact_lookup=st.artifact_identity)
    key=final_key('dev','work'); body=svc.delivery._body_final(D,task,gate)
    st.put_delivery(key,'dev','work','final',body)
    st.set_delivery_state(key,'dead_letter','operator must intervene')
    result=svc.release(claim())
    assert result.action=='dead_letter' and result.reason=='operator must intervene'
    assert not port.entries and st.snapshot()['deliveries']=={'dead_letter':1}


def test_effect_claim_and_revalidation_share_transaction(setup):
    st,cli,svc,port=setup
    assert svc.accept(claim()).status=='releasable'
    original=svc.delivery._local_hold; observed=[]
    def checked(*args):
        observed.append(st.connection.in_transaction)
        assert st.connection.in_transaction
        return original(*args)
    svc.delivery._local_hold=checked
    assert svc.release(claim()).action=='delivered'
    assert observed==[True] and len(port.entries)==0
    assert st.get_delivery(final_key('dev','work')).state=='confirmed'
