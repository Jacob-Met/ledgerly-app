// Exercise the shipped application in real Chrome with an isolated profile.
// Usage: node web-demo/tests/calendar-browser.mjs http://127.0.0.1:8787/ /tmp/ledgerly-receiving
// Requires Node 22+ and google-chrome (or CHROME_BIN). All payment activity is simulated.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,readFile,readdir,writeFile} from 'node:fs/promises';
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


 const check=async()=>{await evaluate("document.querySelector('#review-editor').open=true;const c=document.querySelector('#review-confirm');c.checked=true;c.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#review-form').requestSubmit()");
 await until(()=>evaluate("!document.querySelector('#draft').disabled"),'checked');};
 await call('Page.enable');await call('Runtime.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url});await ready();
 await call('DOM.enable');
 const document=await call('DOM.getDocument');
 const input=await call('DOM.querySelector',{nodeId:document.root.nodeId,selector:'#email-file'});
 await call('DOM.setFileInputFiles',{nodeId:input.nodeId,files:[path.resolve('fixtures/calendar-due-date.eml')]});
 await until(()=>evaluate("!document.querySelector('#intake-preview').hidden"),'real dated MIME preview');
 observations.preview=await evaluate("document.querySelector('#intake-preview-source').textContent");
 assert(observations.preview.includes('Due: 2026-12-12'));
 await click('#intake-replace');
 await until(()=>evaluate("Boolean(document.querySelector('#review-due-date'))&&!document.querySelector('#analyze').disabled"),'dated extraction');
 assert.equal(await evaluate("document.querySelector('#review-due-date').value"),'2026-12-12');
 assert.equal(await evaluate("document.querySelector('#review-term-basis').value"),'date');
 observations.extraction=await evaluate("document.querySelector('.field-row').textContent");
 assert(observations.extraction.includes('2026-12-12'));
 await evaluate("document.querySelector('#review-editor').open=true;const d=document.querySelector('#review-due-date');d.value='2026-12-14';d.dispatchEvent(new Event('input',{bubbles:true}));");
 await check();
 await screenshot('calendar-review-desktop.png','#review-form');
 // Keep the corrected date in the real saved intake before creating an invoice.
 await until(async()=>{const r=await saved();return JSON.stringify(r).includes('2026-12-14')},'corrected intake saved');
 await call('Page.reload');await ready();
 assert.equal(await evaluate("document.querySelector('#review-due-date').value"),'2026-12-14');
 assert.equal(await evaluate("document.querySelector('#review-confirm').checked"),false);
 await check();await click('#draft');await until(()=>evaluate("document.querySelectorAll('#ledger-list .invoice-card').length===1"),'date draft');
 const first=JSON.parse((await saved()).checkpoint).agent.ledger[0];observations.first=first;
 assert.equal(first.invoice_due_on,'2026-12-14');assert.equal(first.provider_due_on,'2026-12-14');assert.equal(first.due_days,null);
 await call('Page.reload');await ready();
 assert.equal(JSON.parse((await saved()).checkpoint).agent.ledger[0].invoice_due_on,'2026-12-14');
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await screenshot('calendar-invoice-phone.png','#ledger-list');
 assert.equal(await evaluate("document.documentElement.scrollWidth<=innerWidth"),true);

 // Carry a complete workspace through actual download/upload controls to a fresh origin.
 await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
 await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:path.resolve(output)});
 await click('#workspace-file-save');
 let downloaded;
 await until(async()=>{downloaded=(await readdir(output)).find(n=>n.startsWith('ledgerly-workspace-')&&n.endsWith('.json'));return Boolean(downloaded)},'workspace downloaded');
 const portable=JSON.parse(await readFile(path.join(output,downloaded),'utf8'));
 assert.equal(portable.intake.review_fields.due_date,'2026-12-14');
 observations.workspaceFile=downloaded;
 await call('Page.navigate',{url:url.replace('127.0.0.1','localhost')});await ready();
 const receivingDocument=await call('DOM.getDocument');
 const receivingInput=await call('DOM.querySelector',{nodeId:receivingDocument.root.nodeId,selector:'#workspace-file-input'});
 await call('DOM.setFileInputFiles',{nodeId:receivingInput.nodeId,files:[path.resolve(output,downloaded)]});
 await until(()=>evaluate("!document.querySelector('#workspace-file-preview').hidden"),'workspace preview');
 observations.workspacePreview=await evaluate("document.querySelector('#workspace-file-fields').innerText");
 await screenshot('calendar-workspace-preview-desktop.png','#workspace-file-preview');
 assert(observations.workspacePreview.includes('2026-12-14'),'Saved workspace preview must show the exact edited date');
 await click('#workspace-file-apply');
 await until(()=>evaluate("document.querySelector('#workspace-file-feedback').textContent.includes('Saved workspace opened')"),'workspace restored');
 await until(()=>evaluate("document.querySelector('#review-due-date')?.value==='2026-12-14'"),'restored date editor');
 assert.equal(await evaluate("document.querySelector('#review-confirm').checked"),false);
 const carried=JSON.parse((await saved()).checkpoint).agent.ledger[0];
 assert.equal(carried.invoice_due_on,'2026-12-14');assert.equal(carried.status,'DRAFT');
 observations.carriedDate=carried.invoice_due_on;
 // Explicitly choose relative terms; a previous calendar value must not leak.
 await evaluate("document.querySelector('#review-editor').open=true;const b=document.querySelector('#review-term-basis');b.value='days';b.dispatchEvent(new Event('input',{bubbles:true}));const d=document.querySelector('#review-due-days');d.value='21';d.dispatchEvent(new Event('input',{bubbles:true}));");
 await check();await click('#draft');await until(()=>evaluate("document.querySelectorAll('#ledger-list .invoice-card').length===2"),'relative draft');
 const second=JSON.parse((await saved()).checkpoint).agent.ledger[1];observations.second=second;assert.equal(second.due_days,21);assert.notEqual(second.invoice_due_on,'2026-12-14');
 observations.result='PASS';await writeFile(path.join(output,'result.json'),JSON.stringify(observations,null,2));console.log(JSON.stringify(observations));
}catch(error){observations.result='FAIL';observations.error=String(error);await writeFile(path.join(output,'result.json'),JSON.stringify(observations,null,2));throw error;}finally{socket?.close();chrome.kill('SIGTERM');}
