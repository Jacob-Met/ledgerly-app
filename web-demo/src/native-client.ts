/** Same maintained interface, using the configured local operator instead of Pyodide. */
export type NativeConfig = {token: string; provider: string; model?: string | null; workspace_id?: string};
export function nativeConfig(): NativeConfig | null {
 const raw=document.querySelector('#ledgerly-native')?.textContent;
 if(!raw)return null;
 const value=JSON.parse(raw);
 if(typeof value.token!=='string'||typeof value.provider!=='string')throw new Error('Invalid local operator configuration.');
 if(value.workspace_id!==undefined&&(typeof value.workspace_id!=='string'||!/^[a-f0-9]{64}$/.test(value.workspace_id)))throw new Error('Invalid local workspace identity.');
 return value;
}
class NativeResponseMissing extends Error {}
const analysisAction = (action: string) => action === 'analyze' || action === 'analyze_rules' || action.startsWith('analysis_');

export type NativeAnalysisJob = {
 id: string;
 state: 'running' | 'ready' | 'failed' | 'dismissed' | 'applied';
 elapsed_seconds: number;
 failure_kind?: 'timeout' | 'unavailable' | null;
 message: string;
};

export class NativeClient {
 private readonly session=crypto.randomUUID();
 private analysisSession:string=this.session;
 private analysisSource:string|undefined;
 analysisOwner(){return this.analysisSession;}
 resumeAnalysisOwner(owner:string,source?:string){
  if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(owner))throw new Error('Saved analysis owner is invalid.');
  this.analysisSession=owner;this.analysisSource=source;
 }
 private previousOwner:string|undefined;
 previousAnalysisOwner(){return this.previousOwner;}
 constructor(private readonly config:NativeConfig){
  try{
   const key='ledgerly.native.review-owner.'+config.token,previous=sessionStorage.getItem(key);
   if(previous&&/^[0-9a-f-]{36}$/i.test(previous))this.previousOwner=previous;
   sessionStorage.setItem(key,this.session);
  }catch{/* Recovery still uses retained fields when tab storage is unavailable. */}
 }
 async call(action:string,payload:Record<string,unknown>={},session:string=this.session){
  let response:Response;
  const missing = () => new NativeResponseMissing(analysisAction(action)
   ? 'The local analysis response is missing. Keep your current fields and reconnect to inspect this analysis.'
   : 'The operator response is missing. Reopen the saved ledger before another action; the interrupted request is never repeated automatically.');
  try{
   response=await fetch('/api/invoice',{method:'POST',headers:{'Content-Type':'application/json','X-Ledgerly-Token':this.config.token,'X-Ledgerly-Review':session},body:JSON.stringify({action,...payload})});
  }catch{
   throw missing();
  }
  if(!response.ok)throw new Error(analysisAction(action)
   ? 'The local analysis session is unavailable. Reopen the operator’s printed address; your saved invoices are unchanged.'
   : 'The local operator session is unavailable. Reopen its printed address; do not repeat an uncertain provider action.');
  try{return await response.json();}catch{throw missing();}
 }
 private async analysisRequest(action:string,jobId:string,payload:Record<string,unknown>={}){
  try{return await this.call(action,{...payload,job_id:jobId},this.analysisSession);}
  catch(error){
   if(!(error instanceof NativeResponseMissing))throw error;
   // Observe the original request. Never start another extraction because its
   // response went missing, and never infer that dismissing cancelled inference.
   return this.analysisStatus(jobId);
  }
 }
 startAnalysis(text:string,jobId:string,preferModel=false){
  this.analysisSession=this.session;this.analysisSource=text;
  return this.analysisRequest('analysis_start',jobId,{text,...(preferModel?{prefer_model:true}:{})});
 }
 analyzeRules(text:string){
  return this.call('analyze_rules',{text});
 }
 analysisStatus(jobId:string){
  return this.call('analysis_status',{job_id:jobId},this.analysisSession);
 }
 dismissAnalysis(jobId:string){
  return this.analysisRequest('analysis_dismiss',jobId);
 }
 private async adoptInCurrentReview(response:any){
  if(!response?.ok||this.analysisSession===this.session)return response;
  if(!this.analysisSource)throw new Error('The original request is unavailable. Keep your current fields.');
  return this.call('analysis_restore',{source_owner:this.analysisSession,text:this.analysisSource});
 }
 async applyAnalysis(jobId:string){
  try{return await this.adoptInCurrentReview(await this.call('analysis_apply',{job_id:jobId},this.analysisSession));}
  catch(error){
   if(!(error instanceof NativeResponseMissing))throw error;
   const observed=await this.analysisStatus(jobId);
   if(observed.ok && observed.result?.analysis_job?.state==='applied' && observed.result?.analysis){
    return this.adoptInCurrentReview({...observed,result:observed.result.analysis});
   }
   return {ok:false,state:observed.state,
    message:'The analysis was not confirmed as applied. Keep your fields and inspect the current analysis before using it again.'};
  }
 }
}
export function configureNativeScreen(config:NativeConfig){
 document.title='Ledgerly · '+config.provider;
 document.body.classList.add('native-operator');
 const set=(selector:string,value:string)=>{const node=document.querySelector(selector);if(node)node.textContent=value;};
 set('#engine-start','Open saved invoice desk');
 set('#workspace-status','Invoices and decisions are saved on disk. Unfinished intake is saved in this browser.');
 document.getElementById('workspace-reload')?.setAttribute('hidden','');
 set('#engine-note','Connect to the local operator to reopen saved invoices. Provider actions require your separate confirmation.');
 set('.hero h1','Your invoice desk');
 set('.hero .lede','Review the client request, check the invoice, then approve its send.');
 for(const id of ['advance-clock','demo-date','payment-amount','replay-webhook','reset-sandbox']){
  const node=document.getElementById(id);if(node)node.hidden=true;
 }
 const refresh=document.createElement('button');refresh.type='button';refresh.id='native-refresh';refresh.className='button';refresh.textContent='Refresh balances';
 const collectionActions=document.createElement('div');collectionActions.className='button-row';
 const exportInvoices=document.getElementById('receivables-download');
 collectionActions.append(refresh);if(exportInvoices)collectionActions.append(exportInvoices);
 document.querySelector('.collections-heading')?.append(collectionActions);
 const refreshLink=document.createElement('a');refreshLink.href='#receivables-view';refreshLink.className='button button-secondary';refreshLink.textContent='Review and refresh balances';
 document.querySelector('.sandbox-tools')?.append(refreshLink);
 const draft=document.getElementById('draft');
 const create=document.createElement('label');create.className='native-confirm';
 create.append(document.createTextNode('Type CREATE to create this draft. '));
 const input=document.createElement('input');input.id='native-create-confirm';input.autocomplete='off';input.placeholder='CREATE';input.setAttribute('aria-label','Confirm provider draft creation');create.append(input);
 const checkNote=document.createElement('p');checkNote.id='native-create-note';checkNote.className='fine-print';checkNote.hidden=true;checkNote.textContent='Confirm and check the invoice fields above first.';create.prepend(checkNote);
 draft?.insertAdjacentElement('beforebegin',create);
 for(const id of ['advance-clock','demo-date','payment-amount','replay-webhook','reset-sandbox'])document.querySelector('label[for="'+id+'"]')?.setAttribute('hidden','');
 set('.brand small','Sandbox');
 set('.desk-toolbar p','Review client facts, create an unsent draft, then confirm its send separately.');
 set('.ledger-panel .panel-heading p','Balances come from saved provider records. Refresh them before deciding what needs attention.');
 set('.collections-heading p','Choose what needs attention in your saved Sandbox invoices. Reviewing collections sends no reminder.');
 set('.audit-panel .panel-heading p','Recorded invoice actions.');
 set('.sandbox-tools>summary','Invoice tools');
 set('.footer p','Native Python operator · '+config.provider+'. Private records stay in the operator state file; each draft and send needs separate confirmation.');
 set('.footer span',config.provider+' / native Python');
 set('#analyze','Analyze request');
 set('#analyze-ai','Use local AI');
 if(typeof config.model==='string' && config.model.trim()){
  const modelNote=document.createElement('p');modelNote.id='native-model-note';modelNote.className='native-model-note';
  modelNote.textContent='Local model: '+config.model+'. Fields still need your review.';
  document.querySelector('#analyze-ai')?.closest('.button-row')?.insertAdjacentElement('afterend',modelNote);
 }
 const calls=document.querySelector('.ledger-footer span');if(calls)calls.innerHTML='<b id="mock-requests">0</b> provider operations this process<span hidden><b id="external-calls">0</b></span>';
 document.querySelector('.payment-row')?.setAttribute('hidden','');
 const history=document.createElement('section');history.id='native-decisions';history.className='native-decisions';
 document.getElementById('approval-list')?.insertAdjacentElement('afterend',history);
}
export function renderNativeDecisions(state:any){
 const panel=document.getElementById('native-decisions');if(!panel)return;
 panel.replaceChildren();
 const attempts=state.creation_attempts||[];
 if(attempts.length){
  const heading=document.createElement('h3');heading.textContent='Recover an earlier draft creation';panel.append(heading);
  const note=document.createElement('p');note.textContent='A draft may already exist. New creation is paused. Find the original invoice in PayPal Sandbox and copy its ID below; recovery only reads it and matches the complete original invoice.';panel.append(note);
  for(const attempt of attempts){
   const row=document.createElement('div');row.className='native-recovery-row';
   const label=document.createElement('label');label.textContent=attempt.invoice_number+' · '+attempt.currency+' · '+attempt.status+' ';
   const input=document.createElement('input');input.dataset.creationId=attempt.id;input.value=attempt.invoice_id||'';input.placeholder='PayPal Sandbox invoice ID';input.setAttribute('aria-label','Recover '+attempt.invoice_number);label.append(input);
   const button=document.createElement('button');button.type='button';button.className='button';button.dataset.recoverCreation=attempt.id;button.textContent='Find the original draft';
   row.append(label,button);panel.append(row);
  }
 }
 const decisions=(state.decisions||[]).filter((x:any)=>x.status==='FAILED'||x.result?.outcome==='UNKNOWN');
 if(!decisions.length)return;
 const heading=document.createElement('h3');heading.textContent='Previous actions need inspection';panel.append(heading);
 for(const action of decisions){
  const row=document.createElement('p');row.textContent=action.invoice_id+' · '+(action.result?.outcome||action.status)+' — '+(action.result?.reason||'Inspect the provider record before preparing another action.');panel.append(row);
 }
}

export function nativeApprovalFields(actions:any[]){
 for(const action of actions){
  const button=Array.from(document.querySelectorAll<HTMLButtonElement>('button[data-approve]')).find(b=>b.dataset.approve===action.id);
  if(!button||!action.confirmation)continue;
  const label=document.createElement('label');label.className='native-confirm';
  label.append(document.createTextNode('To approve this exact action, type '+action.confirmation+' '));
  const input=document.createElement('input');input.dataset.nativeAction=action.id;input.dataset.nativeExpected=action.confirmation;input.autocomplete='off';input.setAttribute('aria-label','Confirmation for '+action.confirmation);label.append(input);
  button.insertAdjacentElement('beforebegin',label);
 }
}
export function nativeConfirmation(id:string):string|null{
 const input=Array.from(document.querySelectorAll<HTMLInputElement>('input[data-native-action]')).find(value=>value.dataset.nativeAction===id);
 return input&&input.value===input.dataset.nativeExpected?input.value:null;
}
