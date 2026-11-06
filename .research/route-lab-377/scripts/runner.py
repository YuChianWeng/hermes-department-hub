"""ONE bounded live probe. Only public CLI/HTTP mutate lab; SQLite read-only evidence."""
import os, sys, json, subprocess, time, urllib.request, urllib.error, sqlite3, traceback, socket
from pathlib import Path
LAB=Path(__file__).resolve().parents[1]; E=LAB/'evidence'; SRC=Path('/home/ubuntu/.hermes/hermes-agent'); PY=str(SRC/'venv/bin/python')
HOME=Path(os.environ['HERMES_HOME']); procs=[]; ledger=[]
def save(n,v): (E/n).write_text(json.dumps(v,indent=2,ensure_ascii=False))
def check(name,ok,detail=None):
    row={'assertion':name,'pass':bool(ok),'detail':detail}; ledger.append(row); save('assertions.json',ledger); print(json.dumps(row),flush=True)
    if not ok: raise AssertionError(name)
def within(p): return Path(p).resolve().is_relative_to(LAB)
def preflight(env):
    fields=['HOME','HERMES_HOME','HERMES_KANBAN_HOME','HERMES_KANBAN_DB','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_RUNTIME_DIR','TMPDIR','TMP','TEMP']
    for k in fields: assert k in env and within(env[k]), (k,env.get(k))
    for k in ['HERMES_KANBAN_TASK','HERMES_KANBAN_RUN_ID','HERMES_GATEWAY_SESSION','HERMES_DELEGATED_CHILD_CONTEXT','DBUS_SESSION_BUS_ADDRESS','SSH_AUTH_SOCK','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY']:
        assert k not in env,k
    assert env['HERMES_DISABLE_LAZY_INSTALLS']=='1' and env['UV_OFFLINE']=='1'
    assert not Path('/home/ubuntu/.hermes/kanban.db').exists()
    assert not Path('/home/ubuntu/.hermes/profiles').exists()
    assert not Path('/run/user/1000').exists()
    assert within(HOME/'gateway.sock')
    assert subprocess.check_output(['ip','-j','route']).strip()==b'[]'
    interfaces=json.loads(subprocess.check_output(['ip','-j','link']))
    assert [i['ifname'] for i in interfaces]==['lo']
    return {'environment':{k:env[k] for k in fields},'network_namespace':os.readlink('/proc/self/ns/net'),'mount_namespace':os.readlink('/proc/self/ns/mnt'),'interfaces':interfaces,'routes':[], 'production_home_masked':True,'source_read_only':True,'pid':os.getpid()}
def cli(*args, profile=None, expect=0):
    env=os.environ.copy()
    if profile: env['HERMES_HOME']=str(HOME/'profiles'/profile)
    preflight(env)
    cmd=[PY,str(LAB/'scripts/cli.py'),*args]
    p=subprocess.run(cmd,env=env,cwd=LAB,text=True,capture_output=True,timeout=45)
    with (E/'cli.jsonl').open('a') as f: f.write(json.dumps({'argv':args,'profile':profile or 'default','rc':p.returncode,'stdout':p.stdout,'stderr':p.stderr})+'\n')
    if p.returncode!=expect: raise RuntimeError((args,p.returncode,p.stdout,p.stderr))
    return p.stdout

def api(profile='default',text='bootstrap',sid=None,key=None):
    headers={'Authorization':'Bearer '+(key or ('synthetic-manager-key' if profile=='manager' else 'synthetic-default-key')),'Content-Type':'application/json'}
    if sid: headers['X-Hermes-Session-Id']=sid
    url='http://127.0.0.1:18232'+('/p/manager' if profile=='manager' else '')+'/v1/chat/completions'
    req=urllib.request.Request(url,data=json.dumps({'model':'route-synthetic','messages':[{'role':'user','content':text}],'stream':False}).encode(),headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=50) as r: status=r.status; body=json.loads(r.read()); rh=dict(r.headers)
    except urllib.error.HTTPError as ex: status=ex.code; body=ex.read().decode(); rh=dict(ex.headers)
    result={'profile':profile,'text':text,'requested_session':sid,'status':status,'body':body,'headers':rh}
    with (E/'http.jsonl').open('a') as f: f.write(json.dumps(result)+'\n')
    return result

def dbrows(db,q,args=()):
    if not Path(db).exists(): return []
    c=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True); c.row_factory=sqlite3.Row
    try: return [dict(r) for r in c.execute(q,args)]
    finally:c.close()
def card(title,parent=None):
    a=['kanban','create',title,'--assignee','manager','--json']
    if parent: a+=['--parent',parent]
    raw=cli(*a); obj=json.loads(raw)
    return obj.get('id') or obj.get('task_id') or obj['task']['id']
def sub(task,owner,sid):
    cli('kanban','notify-subscribe',task,'--platform','api_server','--chat-id',sid,'--notifier-profile',owner,'--delivery-mode','wake')
def complete(task): cli('kanban','complete',task,'--force','--result','SYNTHETIC_ONLY')
def comments(task):return dbrows(LAB/'board/kanban.db','SELECT * FROM task_comments WHERE task_id=?',(task,))
def wait_receipt(task,author,timeout=45):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        rows=comments(task)
        if any(r.get('author')==author and r.get('body')=='SYNTHETIC_ROUTE_RECEIPT '+task for r in rows): return rows
        time.sleep(.5)
    raise TimeoutError(('no receipt',task,author,rows))
def snapshot(name):
    result={'subscriptions':dbrows(LAB/'board/kanban.db','SELECT * FROM kanban_notify_subs'),'events':dbrows(LAB/'board/kanban.db','SELECT * FROM task_events'),'comments':dbrows(LAB/'board/kanban.db','SELECT * FROM task_comments'),'sessions':{p:dbrows(HOME/('profiles/manager' if p=='manager' else '')/'state.db','SELECT id,profile_name,source FROM sessions') for p in ['default','manager']}}
    save(name+'.json',result); return result
try:
    save('preflight.json',preflight(os.environ))
    # Negative pre-start guard: leaked pins and socket/runtime paths cannot start a process.
    for key,bad in [('HERMES_KANBAN_DB','/home/ubuntu/.hermes/kanban.db'),('XDG_RUNTIME_DIR','/run/user/1000')]:
        badenv=os.environ.copy();badenv[key]=bad
        try:preflight(badenv)
        except AssertionError:check('fail-before-start rejects '+key,True)
        else:raise AssertionError('guard accepted '+key)
    config={'_config_version':52,'model':{'default':'route-synthetic','provider':'custom','base_url':'http://127.0.0.1:18231/v1','api_key':'synthetic-not-credential','streaming':False,'context_length':200000,'max_tokens':1000},'gateway':{'multiplex_profiles':True,'api_server':{'enabled':True,'host':'127.0.0.1','port':18232,'key':'synthetic-default-key'}},'kanban':{'dispatch_in_gateway':False,'notify_in_gateway':True,'auto_subscribe_on_create':False,'auto_decompose':False,'done_sub_retention_days':0},'plugins':{'enabled':[],'disabled':[]},'agent':{'max_iterations':6},'display':{'streaming':False},'logging':{'level':'INFO'},'platform_toolsets':{'api_server':['kanban']}}
    for p in [HOME,HOME/'profiles/manager']:
        p.mkdir(parents=True,exist_ok=True);(p/'config.yaml').write_text(json.dumps(config));(p/'SOUL.md').write_text('Synthetic route lab. No real inference. Only Kanban tools.');(p/'.no-bundled-skills').touch()
    (HOME/'profiles/manager/.env').write_text('API_SERVER_KEY=synthetic-manager-key\n')
    cli('kanban','init')
    cli('profile','list')
    cli('kanban','notify-subscribe','--help')
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
    a=api(text='bootstrap-default-377');b=api('manager','bootstrap-manager-377')
    check('default API creates session',a['status']==200,a)
    check('manager API creates session',b['status']==200,b)
    # Read authoritative separately-created identities, never copy state DBs.
    ds=dbrows(HOME/'state.db','SELECT id,profile_name,source FROM sessions');ms=dbrows(HOME/'profiles/manager/state.db','SELECT id,profile_name,source FROM sessions')
    save('session-identities.json',{'default':ds,'manager':ms})
    d=next(x['id'] for x in ds if x['source']=='api_server');m=next(x['id'] for x in ms if x['source']=='api_server')
    check('distinct true profile identities',d!=m and all(x['profile_name']=='default' for x in ds) and all(x['profile_name']=='manager' for x in ms),{'default':d,'manager':m})
    badkey=api('manager','negative-wrong-key',key='synthetic-default-key')
    check('N1 wrong owner credential rejected',badkey['status']==401,badkey)
    badsession=api('manager','negative-wrong-session',sid=d)
    check('N2 default session cannot execute in manager',badsession['status'] in [403,404,409],badsession)
    # SAFE candidate: coordinator subscribes only to a detached report card, never the work DAG.
    root=card('manager-work-root'); report=card('detached-report-no-DAG-edge');sub(root,'manager',m);sub(report,'default',d)
    c1=card('P1-created-child',root);complete(c1);wait_receipt(c1,'manager')
    c2=card('P2-dynamic-link');cli('kanban','link',root,c2);complete(c2);wait_receipt(c2,'manager')
    time.sleep(7)
    s=snapshot('safe-child-routing')
    for label,t in [('P1 created child',c1),('P2 dynamic link',c2)]:
        owners=[r['notifier_profile'] for r in s['subscriptions'] if r['task_id']==t]
        authors=[r['author'] for r in comments(t)]
        check(label+' manager tools execute and default absent',owners==['manager'] and 'manager' in authors and 'default' not in authors,{'task':t,'owners':owners,'authors':authors})
    # A real coordinator control receipt proves the default endpoint is live, not silent/broken.
    complete(report);wait_receipt(report,'default')
    check('detached report reaches default',any(r['author']=='default' for r in comments(report)),{'task':report})
    # N3 subscription owner points at a session in the wrong profile; CLI may accept but no wake permitted.
    bad=card('N3-wrong-owner-default-session');sub(bad,'manager',d);complete(bad)
    time.sleep(12);snapshot('wrong-owner-routing')
    check('N3 wrong-owner subscription has no executed tool',comments(bad)==[],{'task':bad,'owner':'manager','session':d,'admission_rejection_verified':False})
    # Counterexample: root default subscription will leak BOTH newly created and linked children.
    leakroot=card('unsafe-root-default-subscription');sub(leakroot,'default',d)
    leakcreate=card('unsafe-created-child',leakroot);complete(leakcreate);wait_receipt(leakcreate,'default')
    leaklink=card('unsafe-linked-child');cli('kanban','link',leakroot,leaklink);complete(leaklink);wait_receipt(leaklink,'default')
    check('counterexample default receives inherited created child',any(r['author']=='default' for r in comments(leakcreate)),leakcreate)
    check('counterexample default receives inherited linked child',any(r['author']=='default' for r in comments(leaklink)),leaklink)
    s=snapshot('final')
    save('case-ids.json',{'default_session':d,'manager_session':m,'root':root,'report':report,'create_child':c1,'link_child':c2,'wrong_owner':bad,'leak_root':leakroot,'leak_create':leakcreate,'leak_link':leaklink})
    for name,db in [('default',HOME/'state.db'),('manager',HOME/'profiles/manager/state.db')]:
        save(name+'-messages.json',dbrows(db,'SELECT * FROM messages'))
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
    save('cleanup.json',{'processes':[{'pid':p.pid,'returncode':p.returncode,'reaped':p.poll() is not None} for p in procs], 'listeners':subprocess.check_output(['ss','-lntup'],text=True) if Path('/usr/bin/ss').exists() else 'ss unavailable'})
