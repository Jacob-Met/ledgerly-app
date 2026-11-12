"""Exercise the compiled browser worker across isolated Chrome profiles.
This is transport/state integration; the separate UI course uses real file controls.
Requires Playwright and Chrome, and the production dist served at the supplied URL.
"""
import argparse, json, os
from pathlib import Path
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("--output",type=Path,required=True)
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=True)
asset=next((Path(__file__).resolve().parents[1]/"dist"/"assets").glob("engine.worker-*.js")).name

class Desk:
 def __init__(self, browser):
  self.context=browser.new_context()
  self.page=self.context.new_page()
  self.page.goto(args.url)
  self.intake={"source_text":"","review_fields":None}
  self.page.evaluate("""asset=>{
   let seq=0;const calls=new Map(),worker=new Worker('./assets/'+asset,{type:'module'});
   globalThis.portableWorker=worker;
   worker.onmessage=event=>{const m=event.data;if(m.id&&calls.has(m.id)){
    const cb=calls.get(m.id);calls.delete(m.id);m.ok?cb.resolve(m.data):cb.reject(new Error(m.error));
   }};
   globalThis.portableCall=(action,payload)=>new Promise((resolve,reject)=>{
    const id=++seq;calls.set(id,{resolve,reject});worker.postMessage({id,action,payload});
   });
  }""",asset)
  self.call("init")
 def call(self,action,**payload):
  return self.page.evaluate("p=>portableCall(p.action,p.payload)",{
   "action":action,"payload":{"workspace_intake":self.intake,**payload}})
 def saved(self):
  return self.page.evaluate("""()=>new Promise((resolve,reject)=>{
   const open=indexedDB.open('ledgerly-browser-workspace',1);
   open.onerror=()=>reject(open.error);open.onsuccess=()=>{
    const db=open.result,tx=db.transaction('workspace'),store=tx.objectStore('workspace');
    const current=store.get('current'),previous=store.get('previous'),keys=store.getAllKeys();
    tx.oncomplete=()=>{resolve({current:current.result,previous:previous.result,keys:keys.result});db.close();};
   };
  })""")
 def create(self,name,email,amount,currency="USD"):
  text=f"From: {name} <{email}>\nPlease invoice {name} at {email} for 3 hours editing at {currency} {amount} per hour.\nPayment terms: Net 14."
  fields={"client_name":name,"client_email":email,"due_days":"14","amount_paid":"0",
   "line_items":[{"desc":"Editing","qty":"3","unit_price":str(amount),"currency":currency,"unit":"hour"}]}
  self.intake={"source_text":text,"review_fields":None}
  reply=self.call("analyze",text=text);assert reply["ok"],reply
  self.intake["review_fields"]=fields
  review=self.call("review",text=text,fields=fields,confirmed=True)
  assert review["ok"] and review["result"]["valid"],review
  token=review["result"]["review_id"]
  draft=self.call("draft",text=text,review_id=token);assert draft["ok"],draft
  return draft["state"]["pending"][-1],token,text
 def restored(self,reply):
  assert reply["ok"],reply
  self.intake=reply["result"]["restored_intake"] or {"source_text":"","review_fields":None}
  return reply["state"]

with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=os.environ.get("CHROME_BIN","/usr/local/bin/google-chrome"),
  headless=True,args=["--no-sandbox","--disable-dev-shm-usage","--disable-background-networking"])
 a=Desk(browser)
 first,old_token,old_text=a.create("Nora Field","nora@example.test",90)
 sent=a.call("approve",action_id=first["id"]);assert sent["ok"],sent
 paid=a.call("payment",invoice_id=first["invoice_id"],amount="123.45");assert paid["ok"],paid
 second,_,_=a.create("Lucia Vale","lucia@example.test",80,"EUR")
 a.intake={"source_text":"Unfinished next commission for Elin\n","review_fields":{
  "client_name":"Elin","client_email":"elin@example.test","due_days":"","amount_paid":"0",
  "line_items":[{"desc":"Revision","qty":"","unit_price":"90.","currency":"USD","unit":"hour"}]}}
 exported=a.call("workspace_export");assert exported["ok"],exported
 file=exported["result"]["workspace_file"]
 (args.output/file["filename"]).write_text(file["text"])
 expected=exported["state"]
 assert len(expected["ledger"])==2 and len(expected["pending"])==1
 assert expected["ledger"][0]["paid_amount"]=="123.45"

 b=Desk(browser)
 prior,_,_=b.create("Mira Lane","mira@example.test",70)
 prior_saved=b.saved()
 preview=b.call("workspace_preview",text=file["text"]);assert preview["ok"],preview
 assert len(preview["result"]["preview"]["state"]["ledger"])==2
 assert len(preview["state"]["ledger"])==1,"Preview must not replace current invoices"
 backup=b.call("workspace_export");assert backup["ok"],backup
 restored=b.restored(b.call("workspace_restore",preview_token=preview["result"]["preview"]["token"]))
 assert restored["ledger"]==expected["ledger"]
 assert restored["pending"]==expected["pending"]
 assert restored["invoice_details"]==expected["invoice_details"]
 assert b.intake==a.intake
 assert b.saved()["previous"]["checkpoint"]==prior_saved["current"]["checkpoint"]
 old=b.call("draft",text=old_text,review_id=old_token)
 assert not old["ok"] and len(old["state"]["ledger"])==2,"A file must not restore checked draft authorization"
 replay=b.call("replay")
 assert replay["result"]["duplicate"] is True
 assert replay["state"]["ledger"][0]["paid_amount"]=="123.45"

 # A bad external file must leave the active invoice facts and pending decisions intact.
 broken=json.loads(file["text"]);inside=json.loads(broken["checkpoint"])
 inside["mock"]["invoices"]={}
 broken["checkpoint"]=json.dumps(inside)
 before=b.saved()
 failed=b.call("workspace_preview",text=json.dumps(broken))
 assert not failed["ok"]
 assert b.saved()["current"]==before["current"]
 assert len(failed["state"]["ledger"])==2

 # Return to the prior workspace; keep the imported workspace available as the previous one.
 previous=b.call("workspace_previous_preview");assert previous["ok"],previous
 assert previous["result"]["preview"]["state"]["ledger"][0]["client_name"]=="Mira Lane"
 returned=b.restored(b.call("workspace_restore",preview_token=previous["result"]["preview"]["token"]))
 assert len(returned["ledger"])==1 and returned["ledger"][0]["client_name"]=="Mira Lane"
 assert b.saved()["previous"]["checkpoint"]!=b.saved()["current"]["checkpoint"]

 # Typing after a preview retires it, even before the debounce save would run.
 preview=b.call("workspace_preview",text=file["text"]);assert preview["ok"],preview
 b.intake={**b.intake,"source_text":b.intake["source_text"]+"\nA newly typed correction."}
 stale=b.call("workspace_restore",preview_token=preview["result"]["preview"]["token"])
 assert not stale["ok"] and len(stale["state"]["ledger"])==1

 # A realistic interrupted IndexedDB replacement must roll back both archive and current writes.
 preview=b.call("workspace_preview",text=file["text"]);assert preview["ok"],preview
 before=b.saved()
 worker=b.page.workers[0]
 worker.evaluate("""()=>{const put=IDBObjectStore.prototype.put;let once=true;
  IDBObjectStore.prototype.put=function(value,key){
   if(once&&key==='current'){once=false;throw new DOMException('Injected storage quota failure','QuotaExceededError');}
   return put.call(this,value,key);
  };}""")
 failed=b.call("workspace_restore",preview_token=preview["result"]["preview"]["token"])
 assert not failed["ok"] and failed["workspace"]["error"],failed
 after=b.saved()
 assert after["current"]==before["current"] and after["previous"]==before["previous"]
 assert after["keys"]==before["keys"],"Aborted archive writes must not leak partial replacement"
 assert len(failed["state"]["ledger"])==1

 # Two live workers in the same origin must not let an old file preview overwrite newer work.
 second_page=b.context.new_page();second_page.goto(args.url)
 second_page.evaluate("""asset=>{
  let seq=0;const calls=new Map(),worker=new Worker('./assets/'+asset,{type:'module'});
  worker.onmessage=e=>{const m=e.data;if(m.id&&calls.has(m.id)){const c=calls.get(m.id);calls.delete(m.id);m.ok?c.resolve(m.data):c.reject(m.error);}};
  globalThis.otherCall=(action,payload)=>new Promise((resolve,reject)=>{const id=++seq;calls.set(id,{resolve,reject});worker.postMessage({id,action,payload});});
 }""",asset)
 second_page.evaluate("()=>otherCall('init',{})")
 preview=b.call("workspace_preview",text=file["text"]);assert preview["ok"],preview
 changed_input={"source_text":"A newer request in the other tab","review_fields":None}
 second_page.evaluate("v=>otherCall('save_intake',{workspace_intake:v})",changed_input)
 stale=b.call("workspace_restore",preview_token=preview["result"]["preview"]["token"])
 assert not stale["ok"] and stale["workspace"]["conflict"],stale
 assert b.saved()["current"]["intake"]==changed_input

 result={"kind":"actual compiled worker integration, not UI receiving","url":args.url,
  "invoices_carried":2,"currencies":["USD","EUR"],"paid_amount":"123.45","pending_sends":1,
  "unfinished_fields_preserved":True,"preview_isolated":True,"preview_survives_current_backup":True,"previous_workspace_restored":True,
  "consumed_review_refused":True,"changed_intake_refused":True,"write_failure_atomic":True,
  "concurrent_tab_refused":True,"provider":"local simulation only"}
 (args.output/"result.json").write_text(json.dumps(result,indent=2)+"\n")
 print(json.dumps(result))
 browser.close()
