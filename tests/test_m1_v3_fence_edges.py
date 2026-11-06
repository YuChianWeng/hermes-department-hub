"""Adversarial design-audit edge cases, preserved RED before repair."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import threading

import pytest
from department_lib.delivery import Delivery
from department_lib.orchestrator import DepartmentService
from department_lib.store import Store
from tests.test_m1_v2_regressions import setup, payload, claim, D, A
from tests.test_m1_v3_boundaries import reject


def test_already_ingested_rejection_cannot_be_erased_by_stale_pass_snapshot(setup):
    st,cli,svc,port=setup
    old=deepcopy(cli.tasks['work'])
    reject(cli); svc.ingest('work')
    assert any(e.kind=='changes_requested' and e.state=='applied' for e in st.events_for_task('work'))
    cli.tasks['work']=old
    assert svc.accept(claim()).reason=='review_rejected'
    assert svc.release(claim()).reason=='review_rejected'
    assert port.appends==0 and st.delivery_count()==0


def test_observed_new_request_is_a_monotonic_generation_fence(setup):
    st,cli,svc,port=setup
    old=deepcopy(cli.tasks['work'])
    cli.tasks['work']['events'].append({'id':4,'kind':'review_requested','created_at':200,'run_id':7,
                                      'payload':{'reviewer':'reviewer','implementer':'worker'}})
    cli.tasks['work']['runs'].append({'id':7,'profile':'worker','outcome':'review_requested',
                                    'status':'done','metadata':{'artifact_sha256':A}})
    svc.ingest('work'); cli.tasks['work']=old
    assert svc.accept(claim()).reason=='review_generation_stale'
    assert svc.release(claim()).reason=='review_generation_stale'
    assert port.appends==0


def test_outer_transaction_cannot_erase_durable_intent_after_effect(setup):
    st,cli,svc,port=setup
    assert svc.accept(claim()).status=='releasable'
    with pytest.raises(RuntimeError,match='rollback outer'):
        with st.transaction():
            result=svc.release(claim())
            assert result.action=='held' and result.reason=='effect_transaction_nested'
            assert port.appends==0 and st.delivery_count()==0
            raise RuntimeError('rollback outer')
    assert svc.release(claim()).action=='delivered' and port.appends==0


@pytest.mark.parametrize('policy',['inactive','reviewer_removed','member_removed'])
def test_department_policy_changed_before_effect_claim_is_held(setup,policy):
    st,cli,svc,port=setup
    assert svc.accept(claim()).status=='releasable'
    entered,proceed=threading.Event(),threading.Event(); results=[]; errors=[]
    class Paused(Delivery):
        def release_final(self,*args,**kw):
            entered.set(); assert proceed.wait(8)
            return super().release_final(*args,**kw)
    svc.delivery=Paused(st,port)
    def go():
        try: results.append(svc.release(claim()))
        except BaseException as e: errors.append(e)
    t=threading.Thread(target=go); t.start(); assert entered.wait(8)
    other=Store(st._path)
    try:
        if policy=='inactive': updated=replace(D,active=False)
        elif policy=='reviewer_removed': updated=replace(D,reviewers=('other',))
        else: updated=replace(D,members=('other',))
        other.register_department(updated)
    finally:
        other.close(); proceed.set(); t.join(8)
    assert not t.is_alive() and not errors
    assert results[0].action=='held'
    assert results[0].reason in ('inactive_department','department_changed')
    assert port.appends==0 and st.delivery_count()==0


def test_deep_verified_ancestry_cannot_silently_fall_back_to_work_card(setup):
    st,cli,svc,port=setup
    cli.tasks['root']=payload('root','manager'); st.bind_task('dev','root',role='root')
    previous='root'
    for i in range(10):
        name=f'middle{i}'; cli.tasks[name]=payload(name,'worker',[previous]); previous=name
    cli.tasks['work']['parents']=[previous]
    assert svc.accept(claim()).status=='releasable'
    result=svc.release(claim())
    assert result.action=='delivered' and port.appends==0
    assert st.deliveries_for_dept('dev')[0].body['root_task_id']=='root'


def test_fresh_request_can_recover_from_durable_old_generation_rejection(setup):
    st,cli,svc,port=setup
    reject(cli); svc.ingest('work')
    cli.tasks['work']['events'].extend([
        {'id':6,'kind':'review_requested','created_at':200,'run_id':8,'payload':{'reviewer':'reviewer','implementer':'worker'}},
        {'id':7,'kind':'claimed','created_at':201,'run_id':9,'payload':{'source_status':'review'}}])
    cli.tasks['work']['runs'].extend([
        {'id':8,'profile':'worker','outcome':'review_requested','status':'done','metadata':{'artifact_sha256':A}},
        {'id':9,'profile':'reviewer','outcome':'completed','status':'done','summary':'REVIEW: PASS','metadata':{'artifact_sha256':A}}])
    st.put_artifact(task_id='work',revision=2,sha256=A,algorithm='sha256',path='/synthetic/work-v2',
                    real_path='/synthetic/work-v2',size_bytes=0,producer_profile='worker',computed_at=0.0)
    c=replace(claim(),revision=2,verdict_run_id=9)
    assert svc.accept(c).status=='releasable'
    assert svc.release(c).action=='delivered' and port.appends==0
