type AnalysisClient = {
 analysisOwner():string;resumeAnalysisOwner(owner:string,source?:string):void;
 startAnalysis(text:string,jobId:string,preferModel?:boolean):Promise<any>; analysisStatus(jobId:string):Promise<any>;
 dismissAnalysis(jobId:string):Promise<any>; applyAnalysis(jobId:string):Promise<any>;
 analyzeRules(text:string):Promise<any>;
};
type Hooks={source:()=>string;hasFields:()=>boolean;adopt:(value:any)=>void;applying:(value:boolean)=>void;checkpointKey?:string};
export function createNativeAnalysisActivity(client:AnalysisClient,mount:HTMLElement,hooks:Hooks){
 let generation=0,job:{id:string;source:string;generation:number;hadFields:boolean;state:string;reviewRequired?:boolean}|null=null;
 let recoverable=false;
 let ready=false,busy=false,applying=false,polling=false,timer:ReturnType<typeof setTimeout>|undefined;
 function checkpoint(){
  if(!hooks.checkpointKey)return;
  try{
   if(job&&['starting','running','ready','unknown'].includes(job.state))
    sessionStorage.setItem(hooks.checkpointKey,JSON.stringify({id:job.id,source:job.source,owner:client.analysisOwner()}));
   else sessionStorage.removeItem(hooks.checkpointKey);
  }catch{/* The current page remains usable if tab storage is unavailable. */}
 }
 const panel=document.createElement('section');panel.id='native-analysis-activity';panel.className='native-analysis-activity';panel.hidden=true;
 const message=document.createElement('p');message.id='native-analysis-message';message.setAttribute('role','status');
 const detail=document.createElement('p');detail.className='fine-print';
 const actions=document.createElement('div');actions.className='native-analysis-actions';
 const use=document.createElement('button');use.id='native-analysis-use';use.className='button button-primary';use.type='button';use.textContent='Use analysis';
 const keep=document.createElement('button');keep.id='native-analysis-keep';keep.className='button button-secondary';keep.type='button';keep.textContent='Keep current fields';
 const check=document.createElement('button');check.id='native-analysis-check';check.className='button button-secondary';check.type='button';check.textContent='Check analysis status';check.hidden=true;
 const rules=document.createElement('button');rules.id='native-analysis-rules';rules.className='button button-primary';rules.type='button';rules.textContent='Continue without AI';rules.hidden=true;
 actions.append(use,keep,check,rules);panel.append(message,detail,actions);mount.insertAdjacentElement('beforebegin',panel);
 function sync(){
  rules.hidden=!(['running','unknown'].includes(job?.state||'')||recoverable&&job?.state==='failed');rules.disabled=!ready||busy||applying;
  use.hidden=job?.state!=='ready';use.disabled=!ready||busy||applying;
  keep.textContent=hooks.hasFields()?'Keep current fields':'Dismiss analysis';
  keep.hidden=!job||['dismissed','applied'].includes(job.state)||(job.state==='failed'&&!hooks.hasFields());keep.disabled=applying;
  check.disabled=!ready||busy||applying||polling;
  const analyze=document.querySelector<HTMLButtonElement>('#analyze');
  if(analyze)analyze.disabled=!ready||busy||applying||Boolean(job&&['starting','running','ready','unknown'].includes(job.state));
  const ai=document.querySelector<HTMLButtonElement>('#analyze-ai');
  if(ai)ai.disabled=Boolean(analyze?.disabled);
 }
 function current(value:typeof job){return value!==null&&job===value&&value.generation===generation&&hooks.source()===value.source;}
 function show(text:string,note:string){panel.hidden=false;message.textContent=text;detail.textContent=note;sync();}
 async function dismiss(changed=false){
  if(applying)return;
  const old=job;generation++;job=null;clearTimeout(timer);check.hidden=true;if(old)checkpoint();
  if(old){
   if(old.state==='failed'&&hooks.hasFields()){panel.hidden=true;message.textContent='';detail.textContent='';}
   else show(changed?'Request or fields changed. Earlier analysis will not replace them.':'Analysis result dismissed. Current fields kept.',
    'Any model work already running may finish separately. Dismissing a result does not stop the model.');
   try{await client.dismissAnalysis(old.id);}catch{/* The local fence still forbids adoption. */}
  }
  sync();
 }
 async function apply(){
  const value=job;if(!current(value)||value!.state!=='ready'||busy||applying)return;
  applying=true;hooks.applying(true);show('Opening the analyzed fields…','A fresh field check will still be required.');sync();
  try{
   const response=await client.applyAnalysis(value!.id);
   if(!current(value))return;
   if(!response?.ok)throw new Error(response?.message||'Analysis could not be adopted. Current fields are kept.');
   job=null;checkpoint();check.hidden=true;hooks.adopt(response.result);
   if(value!.reviewRequired)show('Local AI was not run.','Review the optional work or recipient choice in these fields before creating a draft.');
   else{panel.hidden=true;message.textContent='';detail.textContent='';sync();}
  }catch(error){
   if(current(value)){value!.state='failed';checkpoint();show('Analysis could not be opened. Current fields kept.',String(error instanceof Error?error.message:error));}
  }finally{applying=false;hooks.applying(false);sync();}
 }
 async function continueWithoutAI(){
  const previous=job;
  if(!current(previous)||!(['running','unknown'].includes(previous!.state)||recoverable&&previous!.state==='failed')||!ready||busy||applying)return;
  // Fence any in-flight status response before opening the manual editor.
  // Model work may finish independently; its result cannot replace these fields.
  const value={...previous!,generation:++generation,state:'manual'};job=value;
  clearTimeout(timer);checkpoint();check.hidden=true;
  recoverable=false;applying=true;hooks.applying(true);
  show('Opening editable fields…','');sync();
  try{
   const response=await client.analyzeRules(value!.source);
   if(!current(value))return;
   if(!response?.ok)throw new Error(response?.message||'The fields could not be opened.');
   job=null;checkpoint();check.hidden=true;hooks.adopt(response.result);
   panel.hidden=true;message.textContent='';detail.textContent='';
  }catch(error){
   if(current(value))show('Could not open editable fields.',String(error instanceof Error?error.message:error));
  }finally{applying=false;hooks.applying(false);sync();}
 }
 async function accept(response:any,value:NonNullable<typeof job>){
  if(!current(value))return;
  if(response?.ok===false){
   value.state='failed';checkpoint();check.hidden=true;
   show('Analysis request was not completed. Current fields kept.',response.message||'The request was refused. You may keep editing and try again later.');return;
  }
  if(!response?.ok)throw new Error('Analysis status is unavailable.');
  const state=response.result?.analysis_job;
  if(!state||state.id!==value.id)throw new Error('The analysis response did not identify the current request.');
  value.state=state.state;value.reviewRequired=state.model_skipped_for_review===true;checkpoint();check.hidden=true;
  if(state.state==='running'){
   show('Analyzing request · '+Math.floor(state.elapsed_seconds||0)+' s',
    hooks.hasFields()?'Your current fields stay until you choose a replacement.':'You can continue without AI and enter the invoice yourself.');
   timer=setTimeout(()=>poll(value),1000);
  }else if(state.state==='ready'){
   const proposed=response.result?.analysis;
   const identity=proposed?[proposed.client_name,proposed.client_email,...(proposed.totals_by_currency||[]).map((x:any)=>x.currency+' '+x.total)].filter(Boolean).join(' · '):'A new proposal is ready.';
   use.textContent=value.reviewRequired?'Review fields':'Use analysis';
   if(value.reviewRequired)show('Local AI was not run.','This request needs your decision about optional work or the recipient. Review the fields or keep your current changes.');
   else show('Analysis ready — current fields have not changed.',identity+' Use analysis to replace the editor, or keep your current fields.');
   if(!value.hadFields&&!hooks.hasFields()&&!busy)await apply();
  }else if(state.state==='failed'){
   recoverable=true;
   show(state.failure_kind==='timeout'?'Local AI timed out.':'AI analysis unavailable.',(state.failure_kind==='timeout'?'No model result was returned. ':'')+(hooks.hasFields()?'Your current invoice is unchanged.':'Continue with editable fields and check the request yourself.'));
  }else if(state.state==='dismissed'){
   job=null;show('Analysis result dismissed. Current fields kept.','No proposal was adopted.');
  }else if(state.state==='applied'){
   // Only our explicit apply operation adopts the observed result.
   show('Analysis was already applied in this session.','Check the current editor before starting another request.');job=null;
  }
  sync();
 }
 async function poll(value:NonNullable<typeof job>){
  if(!current(value))return;if(polling){timer=setTimeout(()=>poll(value),1000);return;}polling=true;
  try{await accept(await client.analysisStatus(value.id),value);}
  catch(error){if(current(value)){value.state='unknown';check.hidden=false;show('Analysis status unavailable. Current fields kept.','Check this request or continue with editable fields. The original analysis may still finish.');}}
  finally{polling=false;sync();}
 }
 rules.addEventListener('click',continueWithoutAI);
 use.addEventListener('click',apply);keep.addEventListener('click',()=>dismiss());check.addEventListener('click',()=>{if(job)void poll(job);});
 return{
  async start(preferModel=false){
   if(!ready||busy||applying||job&&['starting','running','ready','unknown'].includes(job.state))return;
   const source=hooks.source();if(!source.trim()){show('Enter a client request first.','Current invoice fields are unchanged.');return;}
   recoverable=false;generation++;clearTimeout(timer);
   const value={id:crypto.randomUUID(),source,generation,hadFields:hooks.hasFields(),state:'starting'};job=value;
   show('Starting request analysis…','Current fields are kept. Analysis does not create or send an invoice.');
   try{const pending=client.startAnalysis(source,value.id,preferModel);checkpoint();await accept(await pending,value);}
   catch(error){if(current(value)){value.state='unknown';check.hidden=false;show('Analysis status unavailable. Current fields kept.','Check this request or continue with editable fields. The original analysis may still finish.');}}
  },
  async resume(){
   if(!hooks.checkpointKey||job||!ready)return;
   let saved:any;
   try{const raw=sessionStorage.getItem(hooks.checkpointKey);if(!raw)return;saved=JSON.parse(raw);}
   catch{return;}
   if(typeof saved?.id!=='string'||!saved.id||saved.id.length>100||typeof saved.source!=='string'||saved.source!==hooks.source()||typeof saved.owner!=='string'){
    checkpoint();return;
   }
   try{client.resumeAnalysisOwner(saved.owner,saved.source);}catch{checkpoint();return;}
   generation++;
   // A recovered proposal always needs explicit adoption; restored edits win.
   const value={id:saved.id,source:saved.source,generation,hadFields:true,state:'unknown'};job=value;
   show('Reconnecting to request analysis…','Your restored fields stay until you choose Use analysis.');
   await poll(value);
  },
  currentChanged(){if(job&&!applying)void dismiss(true);else if(!job){panel.hidden=true;message.textContent='';detail.textContent='';}},
  setAvailability(isReady:boolean,isBusy:boolean){ready=isReady;busy=isBusy;sync();},
 };
}
