"""Offline audit only. Read lab SQLite, actual model/tool logs; no Hermes import/start."""
import json,sqlite3,os,hashlib
from pathlib import Path
L=Path(__file__).resolve().parents[1]; E=L/'evidence'; F=E/'final-stage'; checks=[]
def readrows(path,q,args=()):
    c=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:return [dict(r) for r in c.execute(q,args)]
    finally:c.close()
def check(name,ok,detail=None):
    checks.append({'assertion':name,'pass':bool(ok),'detail':detail})
    assert ok,(name,detail)
ids=json.loads((F/'case-ids.json').read_text())
board=L/'board/kanban.db'
events=readrows(board,'SELECT * FROM task_events ORDER BY id');subs=readrows(board,'SELECT * FROM kanban_notify_subs');comments=readrows(board,'SELECT * FROM task_comments')
messages={};sessions={}
for profile in ['default','manager']:
    db=L/'os-home/.hermes'/('profiles/manager' if profile=='manager' else '')/'state.db'
    sessions[profile]=readrows(db,'SELECT id,profile_name,source FROM sessions')
    messages[profile]=readrows(db,'SELECT id,session_id,role,content,tool_call_id,tool_calls,tool_name,timestamp FROM messages ORDER BY id')
model=[json.loads(x) for x in (E/'model.jsonl').read_text().splitlines()]
summary=[]
for key in ['create_child','link_child','report','wrong_owner','leak_create','leak_link']:
    task=ids[key];owner='manager' if key in ['create_child','link_child'] else 'default'
    rows=[s for s in subs if s['task_id']==task];ev=[e for e in events if e['task_id']==task and e['kind']=='completed'];assert len(ev)==1
    wake_rows={p:[m for m in messages[p] if m['role']=='user' and '[kanban] Task '+task+' completed' in (m['content'] or '')] for p in messages}
    def targets(m):
        try:
            v=json.loads(m['content'] or '{}')
            return v.get('task_id')==task or v.get('task',{}).get('id')==task
        except (ValueError,TypeError,AttributeError): return False
    tool_rows={p:[m for m in messages[p] if m['role']=='tool' and targets(m)] for p in messages}
    receipts=[c for c in comments if c['task_id']==task and c['body']=='SYNTHETIC_ROUTE_RECEIPT '+task]
    row={'case':key,'task':task,'completed_event_id':ev[0]['id'],'subscriptions':rows,'wake_messages':wake_rows,'tool_results':tool_rows,'comment_receipts':receipts}
    summary.append(row)
    if key=='wrong_owner':
        check('wrong-owner route has no transcript or tool execution',not any(wake_rows.values()) and not any(tool_rows.values()) and not receipts)
        check('wrong-owner event remains pending (not acknowledged)',len(rows)==1 and rows[0]['last_event_id']<ev[0]['id'],{'cursor':rows[0]['last_event_id'],'event_id':ev[0]['id']})
        continue
    check(key+' exact owner has one wake',len(wake_rows[owner])==1,wake_rows)
    check(key+' tool result round trip',len(tool_rows[owner])>=2)
    check(key+' durable receipt actual author',len(receipts)==1 and receipts[0]['author']==owner)
    check(key+' live model saw wake and tool return',any(r['kind']=='wake' and task in r['user'] and 'kanban_show' in r['tools'] and 'kanban_comment' in r['tools'] for r in model) and any(r['kind']=='final_after_tools' and task in r['user'] and len(r['tool_results'])>=2 for r in model))
    if key in ['create_child','link_child']:
        check(key+' default did not receive child wake or tools',not wake_rows['default'] and not tool_rows['default'])
        check(key+' exact manager original session',all(m['session_id']==ids['manager_session'] for m in wake_rows['manager']+tool_rows['manager']))
    check(key+' event claim covers completion',any(s['last_event_id']>=ev[0]['id'] for s in rows))
check('correct manager identity exists',any(r['id']==ids['manager_session'] and r['profile_name']=='manager' for r in sessions['manager']))
check('default-only wrong-owner target never adopted by manager',not any(r['id']==ids['default_only_session'] for r in sessions['manager']))
check('wrong key refused HTTP401',any(json.loads(x).get('status')==401 for x in (E/'http.jsonl').read_text().splitlines()))
check('foreign session lookup refused HTTP404',json.loads((E/'continuation/wrong-session-get.json').read_text())['status']==404)
check('initial negative header failure retained',any(not r['pass'] for r in json.loads((E/'assertions.json').read_text())))
check('all sandbox stage listeners closed',all(not json.loads((d/'cleanup.json').read_text())['listeners'].splitlines()[1:] for d in [E,E/'continuation',F]))
# Only this audit may be live; no gateway/model/runner/unshare from this lab.
alive=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit() or int(p.name)==os.getpid():continue
    try:args=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
    except (OSError,PermissionError):continue
    if str(L/'scripts') in args and any(n in args for n in ['runner.py','model.py','cli.py','continuation.py','final_stage.py','sandbox.sh','sandbox-final.sh','sandbox-continuation.sh']) and ' -c ' not in args:alive.append({'pid':p.name,'args':args})
check('no lab runtime process remains',not alive,alive)
result={'scope':'offline corroboration of actual gateway/model/tool evidence; NOT new live tests','checks':checks,'case_summary':summary,'sessions':sessions,'all_messages':messages,'source_commit':'f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab','p0_pass':False,'unverified':['atomic subscription policy','review/rework/fresh PASS/acceptance','crash/receipt/recovery','Desktop','public HTTP identity metadata readback (export stopped before this step)']}
(E/'audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
print(json.dumps({'audit_checks':len(checks),'passed':sum(x['pass'] for x in checks),'cases':[{k:x[k] for k in ['case','task','completed_event_id']} for x in summary],'runtime_processes':alive,'p0_pass':False},indent=2))
# Manifest excludes itself, audit contains no binary identity columns.
manifest=[]
for p in sorted(L.rglob('*')):
    if p.is_file() and p.name!='sha256-manifest.json':
        manifest.append({'path':str(p.relative_to(L)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
(E/'sha256-manifest.json').write_text(json.dumps(manifest,indent=2))
