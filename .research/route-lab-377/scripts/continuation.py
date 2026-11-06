"""Remaining stages of same bounded investigation; do NOT rerun the failed header case.
Reuse local harness definitions only, not Hermes private APIs. All initial evidence retained.
"""
import ast
from pathlib import Path
harness=Path(__file__).with_name('runner.py')
tree=ast.parse(harness.read_text()); prefix=[]
for node in tree.body:
    if isinstance(node,ast.Try):break
    prefix.append(node)
exec(compile(ast.Module(body=prefix,type_ignores=[]),str(harness),'exec'))
E=LAB/'evidence/continuation'
try:
    save('preflight.json',preflight(os.environ))
    for script,args,log in [('model.py',[], 'model-console.log'),('cli.py',['gateway','run'],'gateway-console.log')]:
        preflight(os.environ)
        f=(E/log).open('w');p=subprocess.Popen([PY,str(LAB/'scripts'/script),*args],cwd=LAB,stdout=f,stderr=subprocess.STDOUT);procs.append(p)
    until=time.monotonic()+60
    while time.monotonic()<until:
        if procs[-1].poll() is not None:raise RuntimeError('gateway exited')
        try:
            with socket.create_connection(('127.0.0.1',18232),timeout=.3):break
        except OSError:time.sleep(.5)
    else:raise TimeoutError('gateway start')
    save('process-env.json',[{'pid':p.pid,'environment':Path('/proc/'+str(p.pid)+'/environ').read_bytes().decode().split('\0')} for p in procs])
    ids=json.loads((LAB/'evidence/session-identities.json').read_text())
    d=ids['default'][0]['id'];m=ids['manager'][0]['id']
    # A fresh DEFAULT-only session is not polluted by the earlier header test.
    a=api(text='bootstrap-default-only-negative-377')
    d2=a['headers']['X-Hermes-Session-Id']
    check('fresh default-only session absent in manager store',not dbrows(HOME/'profiles/manager/state.db','SELECT id FROM sessions WHERE id=?',(d2,)),d2)
    url='http://127.0.0.1:18232/p/manager/api/sessions/'+d2
    req=urllib.request.Request(url,headers={'Authorization':'Bearer synthetic-manager-key'})
    try:
        with urllib.request.urlopen(req,timeout=10) as r:status=r.status;body=r.read().decode()
    except urllib.error.HTTPError as ex:status=ex.code;body=ex.read().decode()
    save('wrong-session-get.json',{'url':url,'status':status,'body':body})
    check('N2b foreign session read refused',status in [403,404,409],{'status':status,'body':body})
    root=card('manager-work-root');report=card('detached-report-no-DAG-edge');sub(root,'manager',m);sub(report,'default',d)
    c1=card('P1-created-child',root);complete(c1);wait_receipt(c1,'manager')
    c2=card('P2-dynamic-link');cli('kanban','link',root,c2);complete(c2);wait_receipt(c2,'manager')
    time.sleep(7);s=snapshot('safe-child-routing')
    for label,t in [('P1 created child',c1),('P2 dynamic link',c2)]:
        owners=[r['notifier_profile'] for r in s['subscriptions'] if r['task_id']==t];authors=[r['author'] for r in comments(t)]
        check(label+' manager tools execute and default absent',owners==['manager'] and 'manager' in authors and 'default' not in authors,{'task':t,'owners':owners,'authors':authors})
    complete(report);wait_receipt(report,'default')
    check('detached report reaches default',any(r['author']=='default' for r in comments(report)),{'task':report})
    bad=card('N3-wrong-owner-default-only-session');sub(bad,'manager',d2);complete(bad)
    time.sleep(12);snapshot('wrong-owner-routing')
    check('N3 wrong-owner subscription has no executed tool',comments(bad)==[],{'task':bad,'owner':'manager','session':d2,'explicit_admission_rejection_verified':False})
    leakroot=card('unsafe-root-default-subscription');sub(leakroot,'default',d)
    leakcreate=card('unsafe-created-child',leakroot);complete(leakcreate);wait_receipt(leakcreate,'default')
    leaklink=card('unsafe-linked-child');cli('kanban','link',leakroot,leaklink);complete(leaklink);wait_receipt(leaklink,'default')
    check('counterexample default receives inherited created child',any(r['author']=='default' for r in comments(leakcreate)),leakcreate)
    check('counterexample default receives inherited linked child',any(r['author']=='default' for r in comments(leaklink)),leaklink)
    snapshot('final')
    save('case-ids.json',{'default_session':d,'default_only_session':d2,'manager_session':m,'root':root,'report':report,'create_child':c1,'link_child':c2,'wrong_owner':bad,'leak_root':leakroot,'leak_create':leakcreate,'leak_link':leaklink})
    for name,db in [('default',HOME/'state.db'),('manager',HOME/'profiles/manager/state.db')]:save(name+'-messages.json',dbrows(db,'SELECT * FROM messages'))
    save('result.json',{'investigation':'completed','scope':'routing only; synthetic model; dispatcher disabled; no review/acceptance proof','assertions':ledger})
except BaseException as ex:
    save('error.json',{'error':repr(ex),'traceback':traceback.format_exc()});print(traceback.format_exc(),flush=True)
    try:snapshot('error-snapshot')
    except Exception:pass
    raise
finally:
    for p in reversed(procs):
        if p.poll() is None:p.terminate()
    for p in reversed(procs):
        try:p.wait(timeout=20)
        except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
    save('cleanup.json',{'processes':[{'pid':p.pid,'returncode':p.returncode,'reaped':p.poll() is not None} for p in procs], 'listeners':subprocess.check_output(['ss','-lntup'],text=True)})
