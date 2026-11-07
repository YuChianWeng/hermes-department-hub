"""P1 regressions using raw synthetic public board payloads and isolated SQLite.

No provenance-enriching legacy helpers: each required field is explicit here.
"""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import importlib.util
import json
from pathlib import Path
import threading

import pytest
from department_lib.acceptance import AcceptanceClaim, is_sha256
from department_lib.board import Board, BoardError
from department_lib.delivery import Delivery
from department_lib.models import BoardEvent, Department
from department_lib.orchestrator import DepartmentService
from department_lib.routing import Router
from department_lib.store import Store
from tests.plugin_loader import load_plugin

A, B = 'a'*64, 'b'*64
D = Department.from_mapping({'id':'dev','name':'Dev',
    'manager':{'profile':'manager','platform':'api_server','chat_id':'session'},
    'members':['worker'],'reviewers':['reviewer']})


def payload(tid='work', assignee='reviewer', parents=()):
    return {'task':{'id':tid,'status':'done','assignee':assignee},
        'parents':list(parents),'children':[],'comments':[],
        'events':[
            {'id':1,'kind':'review_requested','created_at':100,'run_id':5,
             'payload':{'reviewer':'reviewer','implementer':'worker'}},
            {'id':2,'kind':'claimed','created_at':101,'run_id':6,'payload':{'source_status':'review'}},
            {'id':3,'kind':'completed','created_at':102,'run_id':6,'payload':{}}],
        'runs':[
            {'id':5,'profile':'worker','outcome':'review_requested','status':'review',
             'metadata':{'artifact_sha256':A}},
            {'id':6,'profile':'reviewer','outcome':'completed','status':'done','summary':'REVIEW: PASS',
             'metadata':{'verdict':'PASS','artifact_sha256':A}}]}


class CLI:
    def __init__(self, tasks): self.tasks=tasks; self.subscriptions={}; self.calls=[]; self.fail=set()
    def show(self, tid):
        if tid in self.fail or tid not in self.tasks: raise BoardError('unavailable','synthetic unavailable')
        return deepcopy(self.tasks[tid])
    def notify_list(self, tid): return deepcopy(self.subscriptions.get(tid, []))
    def notify_subscribe(self, tid, **kw):
        self.calls.append((tid,kw)); self.subscriptions[tid]=[dict(kw)]


class Port:
    def __init__(self): self.entries=[]; self.appends=0
    def append(self, key, body): self.appends+=1; self.entries.append((key,body))
    def find(self, key): return 'found' if any(k==key for k,b in self.entries) else 'absent'


@pytest.fixture
def setup(tmp_path):
    st=Store(tmp_path/'state.db'); st.register_department(D); st.bind_task('dev','work',role='work')
    st.put_artifact(task_id='work', revision=1, sha256=A, algorithm='sha256',
                    path='/synthetic/work', real_path='/synthetic/work', size_bytes=0,
                    producer_profile='worker', computed_at=0.0)
    cli=CLI({'work':payload()}); board=Board(cli); port=Port()
    svc=DepartmentService(st,board,Delivery(st,port),Router(cli))
    yield st,cli,svc,port
    st.close()


def claim(sha=A): return AcceptanceClaim('work',1,6,sha,'manager')


def test_normal_explicit_and_canonical_pass_remain_releasable(setup):
    st,cli,svc,port=setup
    assert svc.accept(claim()).status=='releasable'
    assert svc.release(claim()).action=='delivered'
    assert svc.release(claim()).action=='already_delivered'
    assert len(port.entries)==0
    cli.tasks['work']['runs'][1]['metadata'].pop('verdict')
    assert svc.accept(claim()).status=='releasable'


@pytest.mark.parametrize('summary,verdict',[
    ('REVIEW: REQUEST_CHANGES','REQUEST_CHANGES'),
    ('REVIEW: PASS; REQUEST_CHANGES','PASS'), ('PASS','REQUEST_CHANGES'),
    ('REJECT: unsafe','PASS'), ('not approved','PASS'), ('looks good',None),
    ('PASS','pass'), ('PASS',{'nested':'PASS'})])
def test_rejection_mixed_and_noncanonical_verdicts_held(setup, summary, verdict):
    st,cli,svc,port=setup; run=cli.tasks['work']['runs'][1]
    run['summary']=summary
    if verdict is None: run['metadata'].pop('verdict')
    else: run['metadata']['verdict']=verdict
    if summary == 'looks good' and verdict is None:
        # v4: friendly prose is not an explicit positive token (P1-A).
        held = svc.accept(claim())
        assert held.status=='held' and held.reason=='review_unproven'
        assert svc.release(claim()).action=='held' and not port.entries
        return
    assert svc.accept(claim()).status=='held'
    assert svc.release(claim()).action=='held' and not port.entries


@pytest.mark.parametrize('mutation', ['no_claim','ready_claim','early_claim','missing_request_run',
    'bad_request_outcome','different_review_sha','wrong_implementer',
    'duplicate_claim','new_running_run','stale_request'])
def test_provenance_is_not_inferred_from_completed_outcome(setup, mutation):
    st,cli,svc,port=setup; p=cli.tasks['work']
    if mutation=='no_claim': p['events'].pop(1)
    elif mutation=='ready_claim': p['events'][1]['payload']['source_status']='ready'
    elif mutation=='early_claim': p['events'][1]['created_at']=99
    elif mutation=='missing_request_run': p['runs'].pop(0)
    elif mutation=='bad_request_outcome': p['runs'][0]['outcome']='completed'
    elif mutation=='different_review_sha': p['runs'][1]['metadata']['artifact_sha256']=B
    elif mutation=='wrong_implementer': p['events'][0]['payload']['implementer']='outsider'
    elif mutation=='duplicate_claim': p['events'].append(dict(p['events'][1], id=4))
    elif mutation=='new_running_run': p['runs'].append({'id':7,'profile':'reviewer','status':'running'})
    elif mutation=='stale_request': p['events'].append(dict(p['events'][0],id=4,created_at=110,run_id=7))
    assert svc.accept(claim()).status=='held'
    assert svc.release(claim()).action=='held' and not port.entries


@pytest.mark.parametrize('sha',[A+'\n',A+' ', ' '+A, 'A'*64,'g'*64,A[:-1],A+'a'])
def test_sha_is_exact_even_if_every_caller_agrees(setup, sha):
    st,cli,svc,port=setup
    for run in cli.tasks['work']['runs']: run['metadata']['artifact_sha256']=sha
    assert not is_sha256(sha)
    assert svc.accept(claim(sha)).status=='held' and not port.entries


def test_same_source_id_different_coordinates_same_payload_quarantined_before_effect(setup):
    st,cli,svc,port=setup
    cli.tasks['work']['events']=[
        {'id':9,'kind':'blocked','created_at':200,'run_id':None,'payload':{}},
        {'id':9,'kind':'completed','created_at':201,'run_id':None,'payload':{}}]
    report=svc.ingest('work')
    assert [d.status for d in report.decisions]==['conflict','conflict']
    assert st.receipt_count()==0


def test_conflict_is_sticky_across_reopen_and_blocks_current_release(setup):
    st,cli,svc,port=setup
    assert svc.accept(claim()).status=='releasable'
    a=BoardEvent('work','blocked',6,103,{'reason':'one'},source_id=9)
    b=replace(a,payload={'reason':'two'})
    assert st.record_event(a,'dev')=='new'
    assert st.record_event(b,'dev')=='conflict'
    assert st.record_event(a,'dev')=='conflict' and not st.has_receipt(a.event_id)
    assert svc.release(claim()).reason=='event_quarantined'
    assert svc.accept(claim()).reason=='event_quarantined' and not port.entries
    reopened=Store(st._path)
    assert reopened.record_event(a,'dev')=='conflict'
    reopened.close()


def test_event_receipt_round_and_disposition_rollback_together(setup):
    st,cli,svc,port=setup
    cli.tasks['work']['events']=[{'id':9,'kind':'changes_requested','created_at':103,'run_id':6,'payload':{}}]
    original=svc._apply_event
    def fail(*args): original(*args); raise RuntimeError('synthetic rollback')
    svc._apply_event=fail
    with pytest.raises(RuntimeError, match='synthetic rollback'): svc.ingest('work')
    assert st.receipt_count()==0 and st.rounds('dev','work')==0 and not st.events_for_task('work')
    svc._apply_event=original
    assert svc.ingest('work').decisions[0].status=='applied'
    assert svc.ingest('work').decisions[0].status=='duplicate'
    assert st.rounds('dev','work')==1


def test_unknown_membership_never_inferred_from_dependency(setup):
    st,cli,svc,port=setup
    cli.tasks['root']=payload('root','manager'); st.bind_task('dev','root',role='root')
    cli.tasks['foreign']=payload('foreign','worker',['root'])
    assert not svc.ingest('foreign').dept_id
    report=svc.route('dev',['foreign'])
    assert not report.ok and not cli.calls


def test_explicit_non_dag_membership_and_movement_fail_closed(setup):
    st,cli,svc,port=setup
    cli.tasks['root']=payload('root','manager'); cli.tasks['root']['events']=[]; cli.tasks['root']['runs']=[]
    # No dependency edge to root: explicit manager association supplies membership.
    report=svc.bind_department('dev','root',['work'])
    assert not report.ok and any(i.code=='session_owner_unverified' for i in report.issues)
    assert st.membership('work')==('root',())
    assert svc.accept(claim()).status=='releasable'
    assert svc._root_for_task('dev','work')=='root'
    cli.tasks['work']['parents']=['elsewhere']
    assert svc.accept(claim()).reason=='ownership_unknown'
    assert svc.release(claim()).action=='held'
    assert svc.escalate('work','blocked',{}).action=='held' and not port.entries


@pytest.mark.parametrize('fault',['root_owner','root_unreadable','multi_root','ancestry_unreadable'])
def test_legacy_explicit_bindings_are_revalidated(setup, fault):
    st,cli,svc,port=setup
    cli.tasks['root']=payload('root','manager'); st.bind_task('dev','root',role='root')
    cli.tasks['work']['parents']=['root']
    if fault=='root_owner': cli.tasks['root']['task']['assignee']='outsider'
    elif fault=='root_unreadable': cli.fail.add('root')
    elif fault=='multi_root':
        cli.tasks['root2']=payload('root2','manager'); st.bind_task('dev','root2',role='root')
        cli.tasks['work']['parents'].append('root2')
    else: cli.tasks['work']['parents']=['missing']
    assert svc.accept(claim()).reason == {
        'root_owner':'ownership_changed', 'root_unreadable':'ownership_unknown',
        'multi_root':'ownership_changed', 'ancestry_unreadable':'ownership_unknown'}[fault]
    assert svc.escalate('work','blocked',{}).action=='held'
    assert not svc.route('dev',['work']).ok and not cli.calls and not port.entries


@pytest.mark.parametrize('different',[False,True])
def test_suspended_unique_claim_survives_second_connection_and_reconcile(tmp_path,different):
    # v5 has no suspended external sender; same two-connection contract is tested
    # at the writer boundary, including differing identity readback.
    from tests.test_m1_v5_delivery import test_two_connections_race
    test_two_connections_race(tmp_path,different)


@pytest.mark.parametrize('detail',[{'token':'PRIVATE_SENTINEL'}, {'round':{'nested':'PRIVATE_SENTINEL'}},
    {'round':'\\u0050RIVATE_SENTINEL'}, {'unknown':[{'deep':'PRIVATE_SENTINEL'}]}, [], {'round':True}])
def test_reject_private_detail_at_public_handler_and_low_level_without_echo(setup, detail):
    st,cli,svc,port=setup
    path=Path(__file__).parents[1]/'__init__.py'
    module=load_plugin()
    result=module._handle_escalate(svc,{'task_id':'work','reason_code':'blocked','detail':detail},caller_profile='manager')
    assert result['success'] is False and result['error']=='escalation_detail_invalid'
    assert 'PRIVATE_SENTINEL' not in json.dumps(result)
    with pytest.raises(ValueError,match='escalation_detail_invalid'):
        svc.delivery.escalate(D,'work','blocked',detail)
    assert not port.entries and st.escalation_count('dev','work')==0
    assert port.appends == 0 and st.delivery_count() == 0


def test_unknown_reason_code_is_not_shared(setup):
    st,cli,svc,port=setup
    with pytest.raises(ValueError,match='escalation_reason_invalid'):
        svc.delivery.escalate(D,'work','PRIVATE_SENTINEL',{})
    assert not port.entries
    assert port.appends == 0 and st.delivery_count() == 0
    assert st.escalation_count('dev','work') == 0
