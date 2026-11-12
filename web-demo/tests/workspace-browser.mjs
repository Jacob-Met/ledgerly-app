// Exercise the shipped application in real Chrome with an isolated profile.
// Usage: node web-demo/tests/workspace-browser.mjs http://127.0.0.1:8787/ /tmp/ledgerly-receiving
// Requires Node 22+ and google-chrome (or CHROME_BIN). All payment activity is simulated.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';

const url=process.argv[2]||'http://127.0.0.1:8787/';
const output=process.argv[3]||await mkdtemp(path.join(tmpdir(),'ledgerly-workspace-'));
await mkdir(output,{recursive:true});
const profile=await mkdtemp(path.join(tmpdir(),'ledgerly-chrome-'));
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const until=async(fn,label,ms=45000)=>{
 const end=Date.now()+ms;
 while(Date.now()<end){if(await fn())return;await sleep(100);}
 throw Error('Timed out: '+label);
};
const chrome=spawn(process.env.CHROME_BIN||'google-chrome',[
 '--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-background-networking',
 '--no-first-run','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank',
],{stdio:'ignore'});
let socket;
const observations={url,profile,payments:'simulation only',fault:'explicitly injected IndexedDB QuotaExceededError'};
try {
 let port;
 await until(async()=>{try{port=Number((await readFile(profile+'/DevToolsActivePort','utf8')).split('\n')[0]);return port>0;}catch{return false;}},'Chrome startup');
 const targets=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();
 socket=new WebSocket(targets.find(target=>target.type==='page').webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
 let sequence=0;
 const pending=new Map(),workers=new Map();
 const call=(method,params={},sessionId)=>new Promise((resolve,reject)=>{
  const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout '+method));},20000);
  pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));
 });
 socket.addEventListener('message',event=>{
  const value=JSON.parse(event.data);
  if(value.id&&pending.has(value.id)){
   const request=pending.get(value.id);pending.delete(value.id);clearTimeout(request.timer);
   value.error?request.reject(Error(JSON.stringify(value.error))):request.resolve(value.result);
  }
  if(value.method==='Target.attachedToTarget'&&value.params.targetInfo.type==='worker')workers.set(value.params.sessionId,value.params.targetInfo);
  if(value.method==='Target.detachedFromTarget')workers.delete(value.params.sessionId);
  if(value.method==='Page.javascriptDialogOpening')call('Page.handleJavaScriptDialog',{accept:true}).catch(()=>{});
 });
 const evaluate=async(expression,sessionId)=>{
  const reply=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},sessionId);
  if(reply.exceptionDetails)throw Error(JSON.stringify(reply.exceptionDetails));
  return reply.result?.value;
 };
 const click=selector=>evaluate('document.querySelector('+JSON.stringify(selector)+').click()');
 const ready=async()=>{
  await until(()=>evaluate("document.readyState==='complete'&&Boolean(document.querySelector('#engine-start'))"),'application document');
  await sleep(500);
  await click('#engine-start');
  await until(()=>evaluate("document.querySelector('#engine-status').textContent.includes('READY')"),'Python ready');
  await until(()=>evaluate("!document.querySelector('#analyze').disabled"),'intake enabled');
  await sleep(300);
 };
 const saved=()=>evaluate("new Promise((resolve,reject)=>{const open=indexedDB.open('ledgerly-browser-workspace',1);open.onerror=()=>reject(open.error);open.onsuccess=()=>{const db=open.result,tx=db.transaction('workspace'),read=tx.objectStore('workspace').get('current');read.onsuccess=()=>resolve(read.result);read.onerror=()=>reject(read.error);tx.oncomplete=()=>db.close();};})");
 const screenshot=async(name,selector)=>{
  if(selector)await evaluate('document.querySelector('+JSON.stringify(selector)+').scrollIntoView({block:"center"})');
  await sleep(100);await writeFile(path.join(output,name),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
 };
 const checkFields=async()=>{
  await evaluate("document.querySelector('#review-editor').open=true;document.querySelector('#review-form').scrollIntoView({block:'center'})");
  await evaluate("(()=>{const c=document.querySelector('#review-confirm');c.checked=true;c.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#review-form').requestSubmit();})()");
  await until(()=>evaluate("!document.querySelector('#draft').disabled"),'checked fields');
 };
 await call('Page.enable');await call('Runtime.enable');
 await call('Target.setAutoAttach',{autoAttach:true,waitForDebuggerOnStart:false,flatten:true});
 await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url});await ready();
 await evaluate("(()=>{const examples=document.querySelector('.example-tools');if(examples)examples.open=true;})()");
 await click('#load-fixture');
 await until(()=>evaluate("document.querySelector('#job-email').value.length>30"),'sample intake');
 await click('#analyze');
 await until(()=>evaluate("Boolean(document.querySelector('#review-form'))&&!document.querySelector('#analyze').disabled"),'real Python extraction');
 await checkFields();await click('#draft');
 await until(()=>evaluate("document.querySelectorAll('[data-approve]').length===1"),'pending send');
 let record=await saved(),checkpoint=JSON.parse(record.checkpoint);
 assert.equal(checkpoint.agent.ledger.length,1);assert.equal(checkpoint.agent.ledger[0].status,'DRAFT');
 observations.pendingBeforeReload=checkpoint.agent.ledger;
 const pendingRevision=record.revision;
 await call('Page.reload');await ready();
 assert.equal((await saved()).revision,pendingRevision,'Merely reopening must not change the saved revision.');
 assert.equal(await evaluate("document.querySelectorAll('[data-approve]').length"),1);
 assert.equal(await evaluate("document.querySelector('#review-confirm').checked"),false);
 assert.equal(await evaluate("document.querySelector('#draft').disabled"),true);
 await click('[data-approve]');
 await until(()=>evaluate("document.querySelectorAll('[data-pay]').length===1&&!document.querySelector('#analyze').disabled"),'approved simulated invoice');
 await evaluate("document.querySelector('#payment-amount').value='23.45'");
 await click('[data-pay]');
 await until(()=>evaluate("document.querySelector('#status-message').textContent.includes('PARTIALLY_PAID')"),'partial payment');
 record=await saved();const paid=JSON.parse(record.checkpoint).agent.ledger;
 assert.equal(paid[0].paid_amount,'23.45');
 await call('Page.reload');await ready();await click('#replay-webhook');
 await until(()=>evaluate("document.querySelector('#status-message').textContent.includes('Duplicate webhook')"),'duplicate ignored after reopening');
 assert.deepEqual(JSON.parse((await saved()).checkpoint).agent.ledger,paid);
 observations.paymentAfterReplay=paid;
 // A checked revision cannot remain usable after an action fails to commit.
 await checkFields();
 await until(()=>Promise.resolve(workers.size>0),'actual worker session');
 const worker=[...workers.keys()].at(-1);
 await evaluate("self.__workspaceOriginalPut=IDBObjectStore.prototype.put;IDBObjectStore.prototype.put=function(){throw new DOMException('Injected test quota exhaustion','QuotaExceededError')}",worker);
 await click('#draft');
 await until(()=>evaluate("document.querySelector('#workspace-status').textContent.startsWith('Could not save')"),'visible failed save');
 assert.equal(await evaluate("document.querySelector('#review-confirm').checked"),false);
 assert.equal(await evaluate("document.querySelector('#draft').disabled"),true);
 assert.deepEqual(JSON.parse((await saved()).checkpoint).agent.ledger,paid);
 await screenshot('failed-save.png','#review-form');
 await evaluate("IDBObjectStore.prototype.put=self.__workspaceOriginalPut;delete self.__workspaceOriginalPut",worker);
 await checkFields();await click('#draft');
 await until(()=>evaluate("document.querySelectorAll('#ledger-list .invoice-card').length===2"),'fresh check after failed save');
 assert.equal(JSON.parse((await saved()).checkpoint).agent.ledger.length,2);
 await call('Page.reload');await ready();
 assert.equal(await evaluate("document.querySelectorAll('#ledger-list .invoice-card').length"),2);
 await screenshot('saved-invoices.png','#ledger-list');
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await screenshot('saved-workspace-phone.png','#engine-start');
 assert.equal(await evaluate("document.documentElement.scrollWidth<=innerWidth"),true);
 // Simulate an incompatible record from a future version in this isolated profile.
 await evaluate("new Promise((resolve,reject)=>{const r=indexedDB.open('ledgerly-browser-workspace',1);r.onsuccess=()=>{const db=r.result,t=db.transaction('workspace','readwrite');t.objectStore('workspace').put({version:999,revision:9,checkpoint:'unreadable'},'current');t.oncomplete=()=>{db.close();resolve(true)};t.onabort=()=>reject(t.error)};})");
 await call('Page.reload');
 await until(()=>evaluate("document.readyState==='complete'&&Boolean(document.querySelector('#engine-start'))"),'incompatible workspace page');
 await sleep(500);await click('#engine-start');
 await until(()=>evaluate("document.querySelector('#workspace-status').textContent.startsWith('Could not save')"),'unreadable saved state refused');
 assert.equal(await evaluate("document.querySelector('#analyze').disabled"),true);
 assert.equal((await saved()).version,999);
 await click('#reset-sandbox');
 await until(()=>evaluate("document.querySelector('#status-message').textContent.includes('Saved workspace cleared')"),'explicit clear of incompatible saved state');
 assert.deepEqual(JSON.parse((await saved()).checkpoint).agent.ledger,[]);
 observations.result='PASS';
 observations.cases=['pending send stays pending after reload','reopening does not create a write conflict','corrections need rechecking','partial payment remains exact','duplicate webhook stays deduplicated','failed transaction leaves prior saved ledger intact','failed checked revision is retired','fresh recheck can draft once','390px no horizontal overflow','unreadable saved state is refused and explicit clear succeeds'];
 await writeFile(path.join(output,'result.json'),JSON.stringify(observations,null,2));
 console.log(JSON.stringify(observations));
}catch(error){
 observations.result='FAIL';observations.error=String(error);await writeFile(path.join(output,'result.json'),JSON.stringify(observations,null,2));throw error;
}finally{socket?.close();chrome.kill('SIGTERM');}
