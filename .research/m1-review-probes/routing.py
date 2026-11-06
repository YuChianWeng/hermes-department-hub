import os, sys, threading, tempfile, pathlib
sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).parent / 'snapshot'))
from department_lib.models import Department
from department_lib.board import BoardTask, BoardError
from department_lib.routing import Router
from department_lib.orchestrator import DepartmentService
from department_lib.delivery import Delivery
from department_lib.kanban_cli import CliKanban, CommandResult
from department_lib.kanban_effects import CommentEffectPort
from department_lib.store import Store
from types import SimpleNamespace

D = Department.from_mapping({'id':'dev','name':'Dev','manager':{'profile':'dev-manager','platform':'api_server','chat_id':'dev-session'},'members':['worker'],'reviewers':['reviewer']})
O = Department.from_mapping({'id':'ops','name':'Ops','manager':{'profile':'ops-manager','platform':'api_server','chat_id':'ops-session'},'members':['worker'],'reviewers':['reviewer']})

class FakeCLI:
    def __init__(self, rows=None): self.rows=rows or {}; self.calls=[]
    def notify_list(self, tid): return self.rows.get(tid, [])
    def notify_subscribe(self, tid, **kw):
        self.calls.append((tid,kw)); self.rows[tid]=[dict(kw, notifier_profile='default')]
    def notify_unsubscribe(self, *a, **kw): raise AssertionError

r=Router(FakeCLI({'child':[{'platform':'api_server','chat_id':'dev-session','delivery_mode':'wake','notifier_profile':'default'}]})).verify(D,['child'])
print('WRONG_OWNER_EXISTING',r.ok, r.issues)
assert r.ok
rcli=FakeCLI(); r=Router(rcli).ensure(D,['child'])
print('WRONG_OWNER_NEW', r.ok, r.ensured, rcli.rows['child']['notifier_profile'] if isinstance(rcli.rows['child'],dict) else rcli.rows['child'][0]['notifier_profile'], rcli.calls)
assert r.ok and rcli.rows['child'][0]['notifier_profile']=='default'

class FakeBoard:
    def __init__(self, chains): self.chains=chains
    def get(self, tid): return BoardTask(tid,'todo','dev-manager',(),(),(),(),())
    def ancestry(self, tid):
        v=self.chains[tid]
        if v is None: raise BoardError('unavailable','unreadable ancestry')
        return v
class Port:
    def __init__(self): self.items=[]
    def append(self,k,b): self.items.append((k,b))
    def find(self,k): return 'found' if any(x[0]==k for x in self.items) else 'absent'

base=pathlib.Path(__file__).parent
with tempfile.TemporaryDirectory(dir=base) as tmp:
    s=Store(pathlib.Path(tmp)/'state.db'); s.register_department(D); s.register_department(O)
    s.bind_task('ops','ops-root',role='root'); s.bind_task('dev','dev-root',role='root')
    cli=FakeCLI(); board=FakeBoard({'ops-child':('ops-child','ops-root'),'dev-child':None})
    p=Port(); svc=DepartmentService(s,board,Delivery(s,p),Router(cli))
    report=svc.bind_department('dev','dev-root',['ops-child'])
    print('CROSS_ROUTE_UNBOUND', report.ok, report.ensured, cli.calls, 'stored_owner',s.dept_for_task('ops-child'))
    assert report.ok and any(t=='ops-child' for t,k in cli.calls)
    board.chains['late-child']=('late-child','dev-root')
    before=len(cli.calls)
    ingested=svc.ingest('late-child')
    print('INGEST_LATE_CHILD',ingested.dept_id,'new_subscriptions',cli.calls[before:])
    assert ingested.dept_id=='dev' and len(cli.calls)==before
    board.chains['late-child']=('late-child','ops-root')
    moved=svc.ingest('late-child')
    print('MOVED_CHILD_OWNERSHIP',moved.dept_id,'board_ancestry',board.chains['late-child'])
    assert moved.dept_id=='dev'
    board.chains['detached']=('detached',)
    s.bind_task('dev','detached',role='work')
    print('DETACHED_ROOT_FALLBACK',svc._root_for_task('dev','detached'))
    assert svc._root_for_task('dev','detached')=='dev-root'
    s.bind_task('dev','dev-child',role='work')
    class FailingOnce(Port):
        def __init__(self): super().__init__(); self.fail=True; self.attempts=0
        def append(self,k,b):
            self.attempts+=1
            if self.fail: self.fail=False; raise RuntimeError('synthetic write failure before effect')
            super().append(k,b)
    f=FailingOnce(); d=Delivery(s,f); task=SimpleNamespace(task_id='dev-child'); gate=SimpleNamespace(status='releasable',revision=1,verdict_run_id=1,reviewer_profile='reviewer',artifact_sha256='a'*64)
    first=d.release_final(D,task,gate,root_task_id='dev-root'); second=d.release_final(D,task,gate,root_task_id='dev-root')
    print('TRANSIENT_FAILURE_RETRY',first.action,second.action,'attempts',f.attempts,'port_find',f.find(first.key))
    assert first.action==second.action=='unknown' and f.attempts==1 and f.find(first.key)=='absent'
    res=svc.escalate('dev-child','blocked',{'token':'SYNTHETIC_SECRET','transcript':'SYNTHETIC_TRANSCRIPT'})
    print('ANCESTRY_FAILURE_TARGET',res.action,p.items[-1][1]['root_task_id'])
    print('ESCALATION_PRIVATE_FIELDS', p.items[-1][1]['detail'])
    assert p.items[-1][1]['root_task_id']=='dev-root' and p.items[-1][1]['detail']['token']=='SYNTHETIC_SECRET'
    s.close()

class PartialCli:
    def show(self, task_id): return {'comments':[{'author':'department-hub'}]}
print('MALFORMED_COMMENTS_READBACK',CommentEffectPort(PartialCli(),['root']).find('key'))
assert CommentEffectPort(PartialCli(),['root']).find('key')=='absent'

class RacingStore:
    def __init__(self, inner, barrier): self.inner=inner; self.barrier=barrier; self.first=True
    def get_delivery(self,key):
        x=self.inner.get_delivery(key)
        if self.first:
            self.first=False; self.barrier.wait(timeout=10)
        return x
    def __getattr__(self,k): return getattr(self.inner,k)
class RacePort:
    def __init__(self): self.entries=[]; self.lock=threading.Lock(); self.append_barrier=threading.Barrier(2)
    def append(self,k,b):
        self.append_barrier.wait(timeout=10)
        with self.lock: self.entries.append((k,b))
    def find(self,k): return 'found' if any(x[0]==k for x in self.entries) else 'absent'
with tempfile.TemporaryDirectory(dir=base) as tmp:
    path=pathlib.Path(tmp)/'state.db'; seed=Store(path); seed.register_department(D); seed.bind_task('dev','work',role='work'); seed.close()
    stores=[Store(path),Store(path)]; barrier=threading.Barrier(2); port=RacePort(); results=[]; errors=[]
    task=SimpleNamespace(task_id='work'); gate=SimpleNamespace(status='releasable',revision=1,verdict_run_id=1,reviewer_profile='reviewer',artifact_sha256='a'*64)
    def run(s):
        try: results.append(Delivery(RacingStore(s,barrier),port).release_final(D,task,gate,root_task_id='root').action)
        except Exception as e: errors.append(repr(e))
    threads=[threading.Thread(target=run,args=(s,)) for s in stores]
    for t in threads:t.start()
    for t in threads:t.join(timeout=20)
    print('CONCURRENT_FINAL',results,'appends',len(port.entries),'errors',errors)
    assert not errors and len(port.entries)==2
    for s in stores:s.close()
print('ALL_SYNTHETIC_ASSERTIONS_PASSED')
