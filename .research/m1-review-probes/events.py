import tempfile, threading
from pathlib import Path
from department_lib.models import BoardEvent, Department
from department_lib.board import BoardTask, BoardRun, Board
from department_lib.store import Store
from department_lib.orchestrator import DepartmentService
from department_lib.acceptance import AcceptanceClaim

ROOT = Path(__file__).parent
SHA = 'a' * 64
DEPT = Department.from_mapping({'id':'dev', 'name':'Dev', 'manager':{'profile':'manager','platform':'api_server','chat_id':'test'},'members':['worker'],'reviewers':['reviewer']})
class FakeBoard:
    cli = None
    def __init__(self, snap): self.snap = snap
    def get(self, tid): return self.snap
    def ancestry(self, tid, limit=8): return (tid,)
def snap(events, runs=(), status='done'):
    return BoardTask('task',status,'worker',(),(),(),tuple(events),tuple(runs))
def setup(path, s):
    store=Store(path); store.register_department(DEPT); store.bind_task('dev','task',role='work')
    return store, DepartmentService(store,FakeBoard(s),None)

def late_ingest(path):
    ev=[BoardEvent('task','review_requested',1,10,{'implementer':'worker','reviewer':'reviewer'}),BoardEvent('task','changes_requested',2,20,{'reason':'fix'}),BoardEvent('task','review_requested',3,30,{'implementer':'worker','reviewer':'reviewer'})]
    runs=[BoardRun(1,'worker','done','review_requested',None,None),BoardRun(2,'reviewer','done','changes_requested',None,None),BoardRun(3,'worker','done','review_requested',None,None),BoardRun(4,'reviewer','done','completed',None,{'artifact_sha256':SHA})]
    store,svc=setup(path,snap(ev,runs)); claim=AcceptanceClaim('task',2,4,SHA,'manager')
    assert svc.accept(claim).status=='releasable'; assert store.active_acceptance('dev','task')
    report=svc.ingest('task'); assert store.active_acceptance('dev','task') is None
    try: svc.accept(claim)
    except Exception as e: print('late_ingest:',[x.status for x in report.decisions], 'active=None; reaccept:',type(e).__name__,str(e))
    else: raise AssertionError('expected reaccept crash')
    store.close()

def conflict_recovery(path):
    a=BoardEvent('task','changes_requested',2,20,{'reason':'original'}); b=BoardEvent('task','changes_requested',2,20,{'reason':'altered'})
    store,svc=setup(path,snap([b])); assert store.record_event(a,'dev')=='new'
    assert svc.ingest('task').decisions[0].status=='conflict'
    svc.board.snap=snap([a]); report=svc.ingest('task'); row=store.events_for_task('task')[0]
    print('conflict_recovery:',report.decisions[0].status,'stored_state=',row.state,'rounds=',store.rounds('dev','task'),'receipt=',store.has_receipt(a.event_id))
    assert report.decisions[0].status=='applied' and row.state=='conflict'; store.close()

def concurrent(path):
    event=BoardEvent('task','changes_requested',2,20,{'reason':'fix'})
    store,svc=setup(path,snap([event])); start=threading.Event(); done=threading.Event(); orig=svc._apply_event
    def controlled(dept,snap,event):
        if threading.current_thread().name=='first':
            start.set(); assert done.wait(5)
        return orig(dept,snap,event)
    svc._apply_event=controlled; results=[]; errors=[]
    def run():
        try: results.append((threading.current_thread().name,svc.ingest('task').decisions[0].status))
        except Exception as e: errors.append(repr(e))
        finally:
            if threading.current_thread().name=='second': done.set()
    a=threading.Thread(target=run,name='first'); b=threading.Thread(target=run,name='second'); a.start(); assert start.wait(5); b.start(); a.join(); b.join()
    print('concurrent:',sorted(results),'errors=',errors,'rounds=',store.rounds('dev','task'),'receipts=',store.receipt_count())
    assert not errors and store.rounds('dev','task')==2 and store.receipt_count()==1; store.close()

def collision(path):
    # CLI omits event ids; task-scoped blocked events within one second can share run_id=None.
    raw={'task':{'id':'task','status':'blocked','assignee':'worker'},'parents':[],'children':[],'comments':[],'runs':[],'events':[{'kind':'blocked','created_at':100,'run_id':None,'payload':{'kind':'needs_input','reason':None,'recurrences':1,'source_status':'ready'}},{'kind':'blocked','created_at':100,'run_id':None,'payload':{'kind':'needs_input','reason':None,'recurrences':2,'source_status':'ready'}}]}
    class Cli:
        def show(self,tid): return raw
    board=Board(Cli()); store,svc=setup(path,board.get('task')); svc.board=board
    result=svc.ingest('task'); print('collision:',[x.status for x in result.decisions],'board_events=',len(board.get('task').events),'receipts=',store.receipt_count()); assert [x.status for x in result.decisions]==['applied','conflict'] and store.receipt_count()==1
    store.close()

def rollback_restart(path):
    event=BoardEvent('task','changes_requested',2,20,{'reason':'fix'})
    store,svc=setup(path,snap([event])); original=svc._apply_event
    def fail_after_apply(*args):
        original(*args)
        raise RuntimeError('injected crash before commit')
    svc._apply_event=fail_after_apply
    try: svc.ingest('task')
    except RuntimeError: pass
    else: raise AssertionError('fault not injected')
    assert not store.events_for_task('task') and store.receipt_count()==0 and store.rounds('dev','task')==0
    store.close(); store=Store(path); svc=DepartmentService(store,FakeBoard(snap([event])),None)
    report=svc.ingest('task'); again=svc.ingest('task')
    print('rollback_restart:',report.decisions[0].status,again.decisions[0].status,'rounds=',store.rounds('dev','task'),'receipts=',store.receipt_count())
    assert report.decisions[0].status=='applied' and again.decisions[0].status=='duplicate' and store.rounds('dev','task')==1; store.close()

def status_authority(path):
    from department_lib.acceptance import evaluate
    request=BoardEvent('task','review_requested',1,10,{'implementer':'worker','reviewer':'reviewer'})
    runs=[BoardRun(1,'worker','done','review_requested',None,None),BoardRun(2,'reviewer','done','completed',None,{'artifact_sha256':SHA})]
    claim=AcceptanceClaim('task',1,2,SHA,'manager')
    store,svc=setup(path,snap([request],runs,status='done')); assert svc.accept(claim).status=='releasable'
    svc.board.snap=snap([request],runs,status='blocked'); gate=evaluate(dept=DEPT,task=svc.board.snap,claim=claim); report=svc.ingest('task')
    print('status_authority:',gate.status,gate.reason,'ingest_board_status=',report.board_status)
    assert gate.status=='held' and gate.reason=='verdict_not_final' and report.board_status=='blocked'; store.close()

with tempfile.TemporaryDirectory(dir=ROOT, prefix='repro-db-') as d:
    for name,fun in [('late',late_ingest),('conflict',conflict_recovery),('threads',concurrent),('collision',collision),('restart',rollback_restart),('status',status_authority)]: fun(Path(d)/f'{name}.db')
