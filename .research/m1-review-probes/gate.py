"""Offline, synthetic Board.get -> service acceptance/release probes."""
import socket

def denied(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.getaddrinfo = denied

from copy import deepcopy
from department_lib.acceptance import AcceptanceClaim, is_sha256
from department_lib.board import Board
from department_lib.delivery import Delivery
from department_lib.models import Department
from department_lib.orchestrator import DepartmentService
from department_lib.store import Store

A = 'a' * 64
B = 'b' * 64
D = Department.from_mapping({'id':'dev','name':'dev','manager':{'profile':'manager','platform':'api_server','chat_id':'synthetic'},'members':['worker'],'reviewers':['reviewer']})

base = {'task': {'id':'t_test','status':'done','assignee':'reviewer'}, 'parents':[], 'children':[], 'comments':[],
        'events':[{'kind':'review_requested','created_at':100,'run_id':5,'payload':{'implementer':'worker','reviewer':'reviewer'}},
                  {'kind':'claimed','created_at':101,'run_id':6,'payload':{'source_status':'review'}},
                  {'kind':'completed','created_at':102,'run_id':6,'payload':{}}],
        'runs':[{'id':5,'profile':'worker','status':'review','outcome':'review_requested','metadata':{'artifact_sha256':A}},
                {'id':6,'profile':'reviewer','status':'done','outcome':'completed','summary':'PASS','metadata':{'artifact_sha256':A}}]}

class FakeCLI:
    def __init__(self, data): self.data=data
    def show(self, task_id): return deepcopy(self.data)
class Port:
    def __init__(self): self.items={}
    def append(self, key, body): self.items[key]=body
    def find(self,key): return 'found' if key in self.items else 'absent'

def run(label, payload, sha=A):
    store=Store(':memory:')
    store.register_department(D); store.bind_task('dev','t_test',role='work')
    board=Board(FakeCLI(payload)); port=Port()
    svc=DepartmentService(store,board,Delivery(store,port))
    claim=AcceptanceClaim('t_test',1,6,sha,'manager')
    gate=svc.accept(claim); result=svc.release(claim)
    print(f'{label}: accept={gate.status}/{gate.reason!r} release={result.action}/{result.reason!r} delivered={len(port.items)}')
    if port.items: print('  body.verdict=', next(iter(port.items.values()))['verdict'])
    store.close()
    return gate.status, result.action

# Control.
assert run('control',base)==('releasable','delivered')
# Completion summary and explicit metadata contradict PASS.
p=deepcopy(base); p['runs'][1]['summary']='REJECT: unsafe; changes required'; p['runs'][1]['metadata']['verdict']='REQUEST_CHANGES'
assert run('explicit_rejection',p)==('releasable','delivered')
# Current handoff's declared artifact differs from reviewer's declared artifact.
p=deepcopy(base); p['runs'][1]['metadata']['artifact_sha256']=B
assert run('wrong_handoff_artifact',p,B)==('releasable','delivered')
# Completion was claimed from ready, not review: outcome alone is not a review.
p=deepcopy(base); p['events'][1]['payload']['source_status']='ready'
assert run('not_a_review_run',p)==('releasable','delivered')
# An absent request run makes provenance unprovable.
p=deepcopy(base); p['runs'].pop(0)
assert run('missing_request_run',p)==('releasable','delivered')
# $ accepts one final newline, despite exact 64 lowercase hex contract.
p=deepcopy(base); p['runs'][1]['metadata']['artifact_sha256']=A+'\n'
print('newline_digest_valid=',is_sha256(A+'\n'))
assert run('newline_digest',p,A+'\n')==('releasable','delivered')

# Fresh second review and acceptance: task-scoped delivery key hides old body.
store=Store(':memory:'); store.register_department(D); store.bind_task('dev','t_test',role='work')
cli=FakeCLI(deepcopy(base)); board=Board(cli); port=Port(); svc=DepartmentService(store,board,Delivery(store,port))
old=AcceptanceClaim('t_test',1,6,A,'manager')
assert svc.accept(old).status=='releasable'; assert svc.release(old).action=='delivered'
cli.data['events'].extend([
    {'kind':'review_requested','created_at':200,'run_id':7,'payload':{'implementer':'worker','reviewer':'reviewer'}},
    {'kind':'claimed','created_at':201,'run_id':8,'payload':{'source_status':'review'}},
    {'kind':'completed','created_at':202,'run_id':8,'payload':{}}])
cli.data['runs'].extend([
    {'id':7,'profile':'worker','status':'review','outcome':'review_requested','metadata':{'artifact_sha256':B}},
    {'id':8,'profile':'reviewer','status':'done','outcome':'completed','summary':'PASS','metadata':{'artifact_sha256':B}}])
new=AcceptanceClaim('t_test',1,8,B,'manager')
g=svc.accept(new); r=svc.release(new); body=next(iter(port.items.values()))
print('new_accepted_run: accept=',g.status,'release=',r.action,'stored_run=',body['verdict']['run_id'],'stored_sha=',body['verdict']['artifact_sha256'])
assert g.status=='releasable' and r.action=='already_delivered' and body['verdict']['run_id']==6 and body['verdict']['artifact_sha256']==A
store.close()
