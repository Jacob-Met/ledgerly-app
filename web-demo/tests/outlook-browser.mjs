// Exercise the shipped application in real Chrome with an isolated profile.
// Usage: node web-demo/tests/outlook-browser.mjs http://127.0.0.1:8787/ /tmp/ledgerly-receiving
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
const observations={url,profile,payments:'simulation only'};
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
 await call('Page.enable'); await call('Runtime.enable'); await call('DOM.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url});await ready();
 const document=await call('DOM.getDocument');
 const input=await call('DOM.querySelector',{nodeId:document.root.nodeId,selector:'#email-file'});
 await call('DOM.setFileInputFiles',{nodeId:input.nodeId,files:[path.resolve('fixtures/outlook-revised-scope.eml')]});
 await until(()=>evaluate("!document.querySelector('#intake-preview').hidden"),'real MIME preview');
 observations.preview=await evaluate("document.querySelector('#intake-preview-source').textContent");
 assert(observations.preview.includes('2 hours of writing'));
 assert(!observations.preview.includes('layout'));
 await screenshot('outlook-preview-desktop.png','#intake-preview');
 await click('#intake-replace');
 await until(()=>evaluate("document.querySelectorAll('[data-review-line]').length===1&&!document.querySelector('#analyze').disabled"),'latest scope extracted');
 observations.review=await evaluate("Array.from(document.querySelectorAll('[data-review-line]')).map(r=>Object.fromEntries(Array.from(r.querySelectorAll('[data-field]')).map(i=>[i.dataset.field,i.value])))");
 assert.equal(observations.review[0].qty,'2'); assert.equal(observations.review[0].unit_price,'70');
 await checkFields(); await click('#draft');
 await until(()=>evaluate("document.querySelectorAll('#ledger-list .invoice-card').length===1"),'draft');
 const record=JSON.parse((await saved()).checkpoint);
 observations.ledger=record.agent.ledger;
 assert.equal(observations.ledger[0].total,'140');
 assert.equal(observations.ledger[0].status,'DRAFT');
 await screenshot('outlook-invoice-desktop.png','#ledger-list');
 await call('Page.reload'); await ready();
 observations.reopenedLedger=JSON.parse((await saved()).checkpoint).agent.ledger;
 assert.deepEqual(observations.reopenedLedger,observations.ledger);
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await screenshot('outlook-invoice-phone.png','#ledger-list');
 observations.width=await evaluate("({viewport:innerWidth,document:document.documentElement.scrollWidth})");
 assert.equal(observations.width.document,observations.width.viewport);
 observations.result='PASS';
 await writeFile(path.join(output,'result.json'),JSON.stringify(observations,null,2));
 console.log(JSON.stringify(observations));
}catch(error){
 observations.result='FAIL';observations.error=String(error);await writeFile(path.join(output,'result.json'),JSON.stringify(observations,null,2));throw error;
}finally{socket?.close();chrome.kill('SIGTERM');}
