"""Synthetic deterministic model; real gateway/tools, NOT real reasoning."""
import json, re, time, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
LAB=Path(__file__).resolve().parents[1]
lock=threading.Lock()
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send(self,data):
        raw=json.dumps(data).encode(); self.send_response(200)
        self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self): self.send({'object':'list','data':[{'id':'route-synthetic','object':'model'}]})
    def do_POST(self):
        b=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')))); ms=b.get('messages',[])
        user=next((m.get('content','') for m in reversed(ms) if m.get('role')=='user'),'')
        if not isinstance(user,str): user=json.dumps(user)
        match=re.search(r'Task (t_[0-9a-f]+) (?:completed|blocked)',user)
        kind='final_after_tools' if ms and ms[-1].get('role')=='tool' else 'wake' if match else 'plain'
        record={'time':time.time(),'kind':kind,'model':b.get('model'),'user':user,'tools':[t.get('function',{}).get('name') for t in b.get('tools',[])], 'tool_results':[m for m in ms if m.get('role')=='tool']}
        with lock:
            with (LAB/'evidence/model.jsonl').open('a') as f: f.write(json.dumps(record)+'\n')
        msg={'role':'assistant','content':'SYNTHETIC_ACK'}; finish='stop'
        if kind=='wake':
            task=match.group(1)
            calls=[('kanban_show',{'task_id':task}),('kanban_comment',{'task_id':task,'body':'SYNTHETIC_ROUTE_RECEIPT '+task})]
            msg={'role':'assistant','content':None,'tool_calls':[{'id':'call_'+str(time.time_ns())+'_'+str(i),'type':'function','function':{'name':n,'arguments':json.dumps(a)}} for i,(n,a) in enumerate(calls)]}; finish='tool_calls'
        self.send({'id':'chatcmpl-synthetic','object':'chat.completion','created':int(time.time()),'model':b.get('model','route-synthetic'),'choices':[{'index':0,'message':msg,'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
ThreadingHTTPServer(('127.0.0.1',18231),H).serve_forever()
