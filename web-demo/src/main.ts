import {WorkspaceStore} from './workspace-store';
import {encodeNativeIntake,decodeNativeIntake,readNativeChoices,restoreNativeChoices,NativeIntakeStore,type NativeIntake} from './native-intake';
import {createBillingContactChoice} from './billing-contact';
import {createSavedRequestView} from './saved-request';
import {fieldsFromExtraction,sourceExclusionChoice,readSourceResolutions,sourceAmountMarkup,syncSourceResolutions,sourceReviewIssues,sourceReviewWarningsMarkup} from './review';
import {invoiceActivityMarkup} from './invoice-activity';
import {savedInvoiceSummaryMarkup, savedInvoiceSendReviewMarkup, createSavedInvoiceSelection, displayMoney} from './saved-invoice';
import {createDraftCorrections} from './draft-correction';
import './journey-guide.css';
import {createJourneyGuide} from './journey-guide';
import './style.css';
import './review.css';
import './intake-file.css';
import {createIntakeFileControls} from './intake-file-ui';
import {encodeEmailBytes, encodeIntakeFile, decodeIntakeFile, type IntakeFile} from './intake-file';
import './ledger-export.css';
import {createLedgerExport} from './ledger-export';
import './approval-preview.css';
import './invoice-details.css';
import {createInvoiceDetailsView} from './invoice-details';
import {createInvoiceRecordDownloads} from './invoice-record';
import {createClientStatementDownloads} from './client-statement';
import './receivables.css';
import {createReceivablesView} from './receivables';
import {targetedReminderActions} from './targeted-reminder';
import './targeted-reminder.css';
import {approvalListMarkup} from './approval-preview';
import {createApprovalNavigation, revealDeskTarget} from './approval-navigation';
import './rejection-reason.css';
import './workspace.css';
import './workspace-files.css';
import {createWorkspaceFileControls} from './workspace-files';
import './native-operator.css';
import {createNativeAnalysisActivity} from './native-analysis-ui';
import {nativeConfig, NativeClient, configureNativeScreen, renderNativeDecisions, nativeApprovalFields, nativeConfirmation} from './native-client';
import {createRejectionReasons} from './rejection-reason';
import {readReviewFields, reviewLinesMarkup, reviewMarkup, invoiceDocumentMarkup, reviewResultMarkup, syncReviewTerms, applyReviewFieldIssues, clearReviewFieldIssues} from './review';
import {WorkerClient, WorkerUnavailableError} from './worker-client';
import {startOnDeviceSession, completeInvoicePrompt, type NativeSession} from './local-ai';
import './ai-progress.css';
import './invoice-document.css';
import './document-workspace.css';
import {installFocusedDesk, showInvoiceDocument, showDeskView, preferredDeskView} from './focused-desk';
import './saved-invoice.css';
import './focused-desk.css';
const native=nativeConfig();
const nativeIntakeIdentity=native?(native.workspace_id||native.token):'';
const nativeIntakeKey=native?'ledgerly.native.intake.'+nativeIntakeIdentity:null;
const legacyNativeIntakeKey=native?'ledgerly.native.intake.'+native.token:null;
let nativeIntakeLoaded=false,nativeIntakeBlocked=false,nativeIntakeOpening=false;
let restoringNativeIntake=false;
const nativeWorker=native?new NativeClient(native):null;
if(native)configureNativeScreen(native);

const $=<T extends HTMLElement>(selector:string)=>document.querySelector(selector) as T;
const journeyGuide=createJourneyGuide($<HTMLElement>('#journey-guide'));
const ledgerExport=createLedgerExport($<HTMLButtonElement>('#ledger-export'),$<HTMLElement>('#ledger-export-note'));
const esc=(v:unknown)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const draftCorrections=createDraftCorrections($<HTMLElement>('#ledger-list'),runAction,id=>approvalNavigation.open(id,'Review this draft correction.'));
const invoiceDetails=createInvoiceDetailsView($<HTMLElement>('#ledger-list'));
const savedInvoices=createSavedInvoiceSelection($<HTMLElement>('#ledger-list'));
const savedRequests=createSavedRequestView($<HTMLElement>('#ledger-list'),runAction);
const invoiceRecords=createInvoiceRecordDownloads($<HTMLElement>('#ledger-list'),status);
const clientStatements=createClientStatementDownloads($<HTMLElement>('#ledger-list'),id=>runAction('client_statement',{invoice_id:id}));
const receivables=createReceivablesView({
 mount:$<HTMLElement>('#receivables-view'),note:$<HTMLElement>('#receivables-note'),
 client:$<HTMLSelectElement>('#receivables-client'),due:$<HTMLSelectElement>('#receivables-due'),
 reset:$<HTMLButtonElement>('#receivables-reset'),summary:$<HTMLElement>('#receivables-summary'),list:$<HTMLElement>('#receivables-list'),
 query:$<HTMLInputElement>('#receivables-query'),currency:$<HTMLSelectElement>('#receivables-currency'),
 sort:$<HTMLSelectElement>('#receivables-sort'),download:$<HTMLButtonElement>('#receivables-download'),
 onOpen:openInvoice,
});
function openInvoice(invoiceId:string){
 savedInvoices.select(invoiceId);
 const detail=Array.from(document.querySelectorAll<HTMLDetailsElement>('.invoice-details')).find(value=>value.dataset.invoiceDetailId===invoiceId);
 if(!detail)return;
 detail.open=true;
 revealDeskTarget(detail.closest<HTMLElement>('.invoice-card')||detail,detail.querySelector<HTMLElement>('summary')||detail);
}
const approvalNavigation=createApprovalNavigation($<HTMLElement>('#approval-list'),openInvoice);
const rejectionReasons=createRejectionReasons($<HTMLElement>('#approval-list'));
const worker=new WorkerClient(()=>new Worker(new URL('./engine.worker.ts',import.meta.url),{type:'module'}),{
 ready:()=>status('Python loaded locally. No external service is connected.','ready'),
 unavailable:engineUnavailable,
});
let ready=false,busy=false,analysisResult:any=null,needsFreshSandbox=false,reviewNeedsAnalysis=false;
let reviewId:string|null=null,reviewEngaged=false,canReplay=false,rawDraftUsed=false;
let latestSandboxState:any=null;
function nativeIntakeStorageStatus(state:'saving'|'saved'|'conflict'|'error'){
 const note=$<HTMLElement>('#workspace-status');
 nativeIntakeBlocked=state==='conflict'||state==='error';
 note.dataset.state=nativeIntakeBlocked?'error':state;
 note.textContent=state==='saving'?'Saving unfinished request…':state==='saved'?'Unfinished request saved in this browser.':
  state==='conflict'?'Another tab saved a newer request. Save this intake before reopening the latest.':
  'Could not save the unfinished request. Keep this tab open or use Save intake.';
 const reopen=$<HTMLButtonElement>('#workspace-reload');reopen.hidden=!nativeIntakeBlocked;reopen.textContent='Reopen saved request';
 if(nativeIntakeBlocked){
  reviewId=null;const check=$<HTMLInputElement>('#review-confirm');if(check)check.checked=false;
  const start=$<HTMLButtonElement>('#engine-start');start.disabled=false;start.textContent='Reopen saved request';
 }
 syncControls();
}
const nativeIntakeStore=native?new NativeIntakeStore(new WorkspaceStore('native:'+nativeIntakeIdentity),nativeIntakeStorageStatus):null;
const billingContacts=createBillingContactChoice($<HTMLElement>("#analysis"),()=>latestSandboxState,()=>ready&&!busy);
// Only a successfully saved creation retires the current intake. Edits make it unfinished again.
let completedIntake=false;
let documentTotals:any[]=[];
// The displayed calculation belongs to these exact fields, independently of approval.
let documentCalculationFields:string|null=null;
let documentFinancials:any[]=[];
let documentLineAmounts:(string|null)[]=[];
type AiRun = {controller: AbortController; text: string; committing: boolean; workspaceFailed: boolean; session: NativeSession | null};
let activeAi: AiRun | null = null;
function closeAiSession(run: AiRun) {
  const session = run.session;
  run.session = null;
  try { session?.destroy?.(); } catch { /* Closing local inference changes no invoice. */ }
}
function abandonAi() {
  const run = activeAi;
  if (!run) return;
  activeAi = null;
  run.controller.abort();
  closeAiSession(run);
  busy = false;
  $<HTMLElement>('#ai-progress').hidden = true;
}
function aiCurrent(run: AiRun) {
  return activeAi === run && !run.controller.signal.aborted && ready &&
    $<HTMLTextAreaElement>('#job-email').value === run.text;
}
function aiWorkspaceStopped(run: AiRun, reply: any): boolean {
  if (!reply.workspace?.error && !reply.workspace?.conflict) return false;
  run.workspaceFailed = true;
  if (activeAi === run)
    status(reply.message || 'The saved workspace needs attention before you retry AI.', 'error');
  return true;
}
function showAiProgress(run: AiRun, message: string, fraction?: number) {
  if (!aiCurrent(run)) return;
  $<HTMLElement>('#ai-progress').hidden = false;
  $<HTMLElement>('#ai-progress-text').textContent = message;
  const progress = $<HTMLProgressElement>('#ai-download-progress');
  if (fraction === undefined) progress.removeAttribute('value');
  else progress.value = fraction;
  $<HTMLButtonElement>('#cancel-ai').disabled = run.committing;
}

let workspaceConflict=false;
const pendingIds=()=>new Set<string>((latestSandboxState?.pending||[]).map((action:any)=>action.id));
function openNewReviews(previous:Set<string>,kind:string,message:string){
 const created=(latestSandboxState?.pending||[]).filter((action:any)=>action.kind===kind&&!previous.has(action.id));
 if(created.length)approvalNavigation.open(created[0].id,created.length>1?`${created.length} new decisions are ready. Review each separately; nothing has been sent.`:message);
}
function refreshJourney() {
  const ledger=latestSandboxState?.ledger||[];
  const pending=latestSandboxState?.pending||[];
  journeyGuide.refresh({
    engineReady:ready, busy, sourcePresent:Boolean($<HTMLTextAreaElement>('#job-email').value.trim()),
    analyzed:Boolean(analysisResult), blocked:!reviewId&&analysisBlocked(analysisResult),
    reviewRequiresCheck:reviewEngaged&&!reviewId,
    hasPending:pending.length>0,
    hasPayableInvoice:ledger.some((entry:any)=>['SENT','PARTIALLY_PAID','UNPAID'].includes(entry.status)&&Number(entry.balance)>0),
    nativeMode:Boolean(native),
    hasPaidInvoice:ledger.some((entry:any)=>entry.status==='PAID'),
    hasLedger:ledger.length>0,
  });
}

const intakeFiles=createIntakeFileControls($<HTMLElement>('.source-content'),{
 snapshot:()=>({sourceText:$<HTMLTextAreaElement>('#job-email').value,
  fields:$<HTMLFormElement>('#review-form')?readReviewFields($<HTMLFormElement>('#review-form')):null,
  choices:readNativeChoices($<HTMLFormElement>('#review-form'))}),
 replace:replaceIntake,
 parseEmail:async bytes=>{
  const reply=await runAction('import_email',{email_base64:encodeEmailBytes(bytes)});
  if(!reply?.ok)throw new Error(reply?.message||'The email could not be decoded. Load the local engine and try again.');
  return reply.result.source_text;
 },
});
const workspaceFiles=native?null:createWorkspaceFileControls($<HTMLElement>('#workspace-status').parentElement!,{
 run:runAction,
 restoreIntake:async(intake:any)=>{
  clearTimeout(draftSaveTimer);
  if(intake?.source_text){await replaceIntake(intake);return;}
  $<HTMLTextAreaElement>('#job-email').value=intake?.source_text||'';
  reviewId=null;rawDraftUsed=false;
  if(intake?.review_fields){
   const f=intake.review_fields;
   renderAnalysis({client_name:f.client_name,client_email:f.client_email,due_days:f.due_days,due_date:f.due_date,amount_paid:f.amount_paid,note:f.note,
    line_items:f.line_items,review_currencies:f.line_items.map((row:any)=>row.currency),
    confidence:0,source:'restored',issues:[]});
   const form=$<HTMLFormElement>('#review-form');
   for(const name of ['client_name','client_email','due_days','amount_paid'] as const)
    (form.elements.namedItem(name) as HTMLInputElement).value=f[name];
   (form.elements.namedItem('due_date') as HTMLInputElement).value=f.due_date||'';
   (form.elements.namedItem('term_basis') as HTMLSelectElement).value=f.due_date!==undefined?'date':'days';
   syncReviewTerms(form);
   $<HTMLElement>('#review-lines').innerHTML=reviewLinesMarkup(f.line_items,f.line_items.map((row:any)=>row.currency));
   markReviewDirty('Saved editable fields restored. Confirm and check them again; no earlier field check is reused.');
   reviewNeedsAnalysis=true;
  }else invalidateAnalysis('Saved source restored. Analyze it before creating an invoice.');
  syncControls();
 },
});
const nativeActivity=nativeWorker?createNativeAnalysisActivity(nativeWorker,$<HTMLElement>('#analysis'),{
 checkpointKey:native?'ledgerly.native.analysis.'+native.token:undefined,
 source:()=>$<HTMLTextAreaElement>('#job-email').value,
 hasFields:()=>Boolean($<HTMLFormElement>('#review-form')),
 applying:(value:boolean)=>{busy=value;syncControls();},
 adopt:(value:any)=>{renderAnalysis(value);reviewNeedsAnalysis=false;status('Analysis opened. Check every invoice field before creating a draft.','ready');},
}):null;
if(native){
 const copy=document.querySelector<HTMLElement>('.intake>.fine-print');
 if(copy)copy.textContent='Analyze with local operator uses its configured extractor and may contact its local Ollama server. The separate on-device AI option uses Chrome’s built-in model; first use may download model files. All proposed fields need review.';
}
async function replaceIntake(file:Pick<IntakeFile,'source_text'|'review_fields'|'source_choices'>):Promise<boolean>{
 if(!ready||busy)return false;
 const previousForm=$<HTMLFormElement>('#review-form');
 reviewId=null;rawDraftUsed=false;reviewEngaged=Boolean(previousForm);reviewNeedsAnalysis=Boolean(previousForm);
 if(previousForm){
  $<HTMLInputElement>('#review-confirm').checked=false;
  $<HTMLElement>('#review-result').textContent='The old review is retired. The incoming source is being checked; existing input remains until that check completes.';
 }else analysisResult=null;
 syncControls();
 const reply=await runAction(native?(restoringNativeIntake?'analysis_restore':'analyze_rules'):'analyze',{text:file.source_text,workspace_intake:file,...(restoringNativeIntake?{source_owner:nativeWorker?.previousAnalysisOwner()}:{})});
 if(!reply?.ok){
  if(previousForm)$<HTMLElement>('#review-result').textContent='Opening did not complete. Your previous fields remain; confirm and check them again before drafting.';
  syncControls();return false;
 }
 $<HTMLTextAreaElement>('#job-email').value=file.source_text;
 renderAnalysis(reply.result);
 reviewNeedsAnalysis=false;
 if(file.review_fields){
  const fields=file.review_fields,form=$<HTMLFormElement>('#review-form');
  for(const name of ['client_name','client_email','due_days','amount_paid'] as const)
   (form.elements.namedItem(name) as HTMLInputElement).value=fields[name];
  if(fields.note!==undefined)(form.elements.namedItem('note') as HTMLTextAreaElement).value=fields.note;
  (form.elements.namedItem('due_date') as HTMLInputElement).value=fields.due_date||'';
  (form.elements.namedItem('term_basis') as HTMLSelectElement).value=fields.due_date!==undefined?'date':'days';
  syncReviewTerms(form);
  $<HTMLElement>('#review-lines').innerHTML=reviewLinesMarkup(fields.line_items,reply.result.review_currencies||[]);
  markReviewDirty('Unfinished invoice restored. Review the request and check your changes.');
  restoreNativeChoices(form,file.source_choices||[]);
  $<HTMLDetailsElement>('#review-editor').open=true;
  $<HTMLInputElement>('#review-client-name').focus();
 }else $<HTMLDetailsElement>('#review-editor').querySelector<HTMLElement>('summary')?.focus();
 syncControls();if(native)rememberNativeIntake();return true;
}
function status(text:string,kind='info'){$<HTMLElement>('#status-message').textContent=text;$<HTMLElement>('#status-message').dataset.kind=kind;}
let draftSaveTimer:ReturnType<typeof setTimeout>|undefined;
function currentIntake(){
 const form=$<HTMLFormElement>('#review-form');
 return {source_text:$<HTMLTextAreaElement>('#job-email').value,review_fields:form?readReviewFields(form):null,source_choices:readNativeChoices(form)};
}
async function call(action:string,payload:Record<string,unknown>={}){
 if(nativeWorker){
  if(['review','draft'].includes(action)&&nativeIntakeLoaded&&!(await nativeIntakeStore!.flush()))
   throw new Error('The unfinished request could not be saved. Keep these fields or reopen the latest saved request before checking.');
  return nativeWorker.call(action,payload);
 }
 if(!action.startsWith('workspace_'))workspaceFiles?.currentChanged();
 const reply=await worker.call(action,{workspace_intake:currentIntake(),...payload});
 const saved=reply.workspace,note=$<HTMLElement>('#workspace-status');
 if(saved){
  workspaceFiles?.setMetadata(saved);
  $<HTMLButtonElement>('#workspace-reload').hidden=!saved.conflict;
  note.dataset.state=saved.error||saved.conflict?'error':'saved';
  note.textContent=saved.error?'Could not save. Your previous saved workspace is intact.':
   saved.conflict?'Another tab changed this workspace. Reload it before continuing.':
   saved.savedAt?'Saved in this browser · '+new Date(saved.savedAt).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):
   'This browser will save your invoices, pending decisions and unfinished intake.';
  if(saved.error){
   reviewId=null;rawDraftUsed=false;
   const form=$<HTMLFormElement>('#review-form');
   if(form){
    reviewEngaged=true;reviewNeedsAnalysis=true;
    $<HTMLInputElement>('#review-confirm').checked=false;
    $<HTMLElement>('#review-result').textContent='The action could not be saved. Review these retained fields and check them again before drafting.';
   }else analysisResult=null;
   syncControls();
  }
  if(saved.conflict){
   workspaceConflict=true;
   ready=false;needsFreshSandbox=true;syncControls();
   const start=$<HTMLButtonElement>('#engine-start');start.disabled=false;start.textContent='Reload saved workspace';
  }
 }
 return reply;
}
function rememberNativeIntake(){
 if(!nativeIntakeKey||!ready||restoringNativeIntake)return;
 try{
  const intake=currentIntake();
  const saved=encodeNativeIntake({sourceText:intake.source_text,fields:intake.review_fields},$<HTMLFormElement>('#review-form'));
  if(!intake.source_text&&!intake.review_fields)sessionStorage.removeItem(nativeIntakeKey);
  else sessionStorage.setItem(nativeIntakeKey,saved);
  if(nativeIntakeLoaded)nativeIntakeStore?.queue(saved);
 }catch{status('Unfinished work could not be kept in this tab. Use Save intake before leaving.','error');}
}
function scheduleIntakeSave(){
 workspaceFiles?.currentChanged();
 if(native){rememberNativeIntake();return;}
 if(!ready||busy)return;
 clearTimeout(draftSaveTimer);
 $<HTMLElement>('#workspace-status').textContent='Saving unfinished intake…';
 draftSaveTimer=setTimeout(async()=>{
  if(!ready||busy)return;
  try{const reply=await call('save_intake');if(!reply.ok)status(reply.message,'error');}
  catch(error){status('The unfinished intake could not be saved: '+String(error),'error');}
 },180);
}

function recoveryMessage(error:WorkerUnavailableError){return error.message+' Reload the saved local workspace. Your source and corrections stay here; no interrupted action will be replayed.';}
function engineUnavailable(error:WorkerUnavailableError){
 abandonAi();
 intakeFiles.currentChanged('The Python session ended. Open the file again after reviewing your retained input.');
 const form=$<HTMLFormElement>('#review-form');
 needsFreshSandbox=true;reviewNeedsAnalysis=Boolean(form);reviewId=null;canReplay=false;rawDraftUsed=false;
 if(form){
  reviewEngaged=true;
  $<HTMLInputElement>('#review-confirm').checked=false;
  $<HTMLElement>('#review-result').textContent='The previous Python session ended. Your edits are retained; after restart, confirm and check them again.';
 }else{analysisResult=null;reviewEngaged=false;}
 setControls(false);
 for(const id of ['approval-list','ledger-list'])$<HTMLElement>(`#${id}`).setAttribute('aria-disabled','true');
 const start=$<HTMLButtonElement>('#engine-start');start.disabled=false;start.textContent='Reload saved workspace';
 $<HTMLElement>('#engine-status').textContent='PYTHON UNAVAILABLE';$<HTMLElement>('#engine-dot').classList.remove('ready');
 $<HTMLElement>('#engine-note').textContent='The last displayed ledger is inactive. Reload returns to the last saved workspace and keeps your source and corrections. No interrupted action is replayed.';
 const panel=$<HTMLElement>('#analysis');
 if(!form)panel.innerHTML='<p id="engine-recovery-analysis" class="empty"></p>';
 else if(!$<HTMLElement>('#engine-recovery-analysis'))panel.insertAdjacentHTML('afterbegin','<p id="engine-recovery-analysis" class="empty"></p>');
 $<HTMLElement>('#engine-recovery-analysis').textContent=form?'The Python session is unavailable. Your corrections are retained. Restart, then confirm and check them again.':'The Python session is unavailable. Your source email is still here. Restart, then analyze it again.';
 status(recoveryMessage(error),'error');
}
function analysisBlocked(ex:any){return !ex||ex.issues?.some((x:any)=>x.severity==='error')||(ex.confidence||0)<0.5||!ex.line_items?.length;}
function syncControls(){
 const documentView=document.querySelector<HTMLElement>('#invoice-document');
 const currentForm=document.querySelector<HTMLFormElement>('#review-form');
 if(documentView&&currentForm){
  const fields=readReviewFields(currentForm);
  const calculationStale=reviewNeedsAnalysis||JSON.stringify(fields)!==documentCalculationFields;
  documentView.innerHTML=invoiceDocumentMarkup(fields,documentTotals,Boolean(reviewId),calculationStale,documentLineAmounts,$<HTMLTextAreaElement>('#job-email').value,analysisResult,documentFinancials,readSourceResolutions(currentForm));
 }
 billingContacts.refresh();
 const enabled=ready&&!busy&&!nativeIntakeOpening;
 $<HTMLButtonElement>('#cancel-ai').disabled=!activeAi||activeAi.committing;
 rejectionReasons.setAvailability(ready,busy);
 intakeFiles.setAvailability(ready,busy);
 workspaceFiles?.setAvailability(ready,busy);
 ledgerExport.setAvailable(enabled);
 invoiceDetails.setAvailability(ready,busy);
 receivables.setAvailability(ready,busy);
 for(const id of ['fixture-select','load-fixture','job-email','analyze','analyze-ai','demo-date','advance-clock','run-chase','payment-amount'])$<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement>(`#${id}`).disabled=!enabled;
 $<HTMLButtonElement>('#reset-sandbox').disabled=busy;
 $<HTMLButtonElement>('#workspace-reload').disabled=busy;
 for(const el of Array.from(document.querySelectorAll<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement|HTMLButtonElement>('#review-form input,#review-form select,#review-form textarea,#review-form button,#invoice-document [data-remove-line],#invoice-document [data-edit-unit],#invoice-document [data-edit-item],#invoice-document [data-review-charge],#invoice-document [data-source-exclude],#approval-list button,#ledger-list button')))el.disabled=!enabled;
 invoiceRecords.setAvailability(ready,busy);
 clientStatements.setAvailability(ready,busy);
 const checked=$<HTMLInputElement>('#review-confirm');if(checked)checked.disabled=!enabled;
 const reviewActions=document.querySelector<HTMLElement>('.invoice-review-actions');if(reviewActions){reviewActions.hidden=Boolean(reviewId);reviewActions.dataset.required=String(Boolean(native)||reviewEngaged);}
 const check=$<HTMLButtonElement>('#review-check');if(check)check.disabled=!enabled||!checked?.checked||nativeIntakeBlocked;
 const reviewStatus=$<HTMLElement>('#review-result');
 if(reviewStatus)reviewStatus.dataset.incomplete=String(!reviewId&&analysisBlocked(analysisResult));
 const analysisHeading=$<HTMLElement>('#review-analysis-heading');
 if(analysisHeading&&reviewEngaged)analysisHeading.textContent=reviewId?'Fields checked':'Review current fields';
 const sourceWarnings=$<HTMLDetailsElement>('#review-source-warnings');
 if(sourceWarnings){
  const reviewed=Boolean(reviewId);
  if(currentForm){
   const fields=readReviewFields(currentForm),key=JSON.stringify([fields.client_name,fields.client_email,reviewed]);
   if(sourceWarnings.dataset.recipientWarnings!==key){
    const list=$<HTMLElement>('#review-source-warning-list');
    if(list)list.innerHTML=sourceReviewWarningsMarkup(analysisResult,fields,reviewed);
    sourceWarnings.dataset.recipientWarnings=key;
   }
  }
  if(reviewed&&sourceWarnings.dataset.reviewed!=='true')sourceWarnings.open=false;
  else if(!reviewed&&(sourceWarnings.dataset.reviewed==='true'||reviewEngaged))sourceWarnings.open=true;
  sourceWarnings.dataset.reviewed=String(reviewed);
  sourceWarnings.className=reviewId?'source-warnings acknowledged':'source-warnings';
  $<HTMLElement>('#review-source-warning-heading').textContent=reviewId?'Original warnings — retained after review':'Check against the original request';
 }
 const draft=$<HTMLButtonElement>('#draft');draft.disabled=!enabled||(reviewEngaged?!reviewId:rawDraftUsed||analysisBlocked(analysisResult));
 if(native){draft.hidden=!currentForm;const confirmation=$<HTMLInputElement>('#native-create-confirm').closest('label');if(confirmation)confirmation.hidden=!currentForm||!reviewId;if(!reviewId)$<HTMLInputElement>('#native-create-confirm').value='';draft.disabled=draft.disabled||nativeIntakeBlocked||!reviewId||$<HTMLInputElement>('#native-create-confirm').value!=='CREATE'||Boolean(latestSandboxState?.creation_attempts?.length);const note=document.getElementById('native-create-note');if(note)note.textContent=latestSandboxState?.creation_attempts?.length?'Recover the earlier creation before preparing another draft.':!reviewId?'Confirm and check the invoice fields above first.':'The checked invoice is ready for a separate CREATE confirmation.';}
 draft.textContent='Create draft';
 $<HTMLButtonElement>('#replay-webhook').disabled=!enabled||!canReplay;
 nativeActivity?.setAvailability(ready&&!nativeIntakeOpening,busy);
 for(const button of Array.from(document.querySelectorAll<HTMLButtonElement>('[data-source-exclude]')))button.disabled=!enabled||reviewNeedsAnalysis;
 const resolutionForm=$<HTMLFormElement>('#review-form');if(resolutionForm)syncSourceResolutions(resolutionForm);
 refreshJourney();
}
function setControls(enabled:boolean){ready=enabled;syncControls();}
function invalidateAnalysis(message:string){
 if(activeAi){abandonAi();status(message,'ready');}
 nativeActivity?.currentChanged();
 if(native&&!completedIntake&&$<HTMLFormElement>('#review-form')){
  intakeFiles.currentChanged();reviewId=null;rawDraftUsed=false;reviewEngaged=true;reviewNeedsAnalysis=true;
  for(const input of Array.from(document.querySelectorAll<HTMLTextAreaElement|HTMLSelectElement>('[data-source-amount] textarea,[data-source-amount] select')))input.value='';
  $<HTMLInputElement>('#review-confirm').checked=false;
  $<HTMLElement>('#review-result').textContent=message+' Your previous editable fields are kept; their earlier check is no longer valid.';
  syncControls();return;
 }
 workspaceFiles?.currentChanged();
 intakeFiles.currentChanged();
 completedIntake=false;
 analysisResult=null;reviewId=null;reviewEngaged=false;rawDraftUsed=false;reviewNeedsAnalysis=false;
 $<HTMLElement>('#analysis').innerHTML=native?'':`<p class="empty">${esc(message)}</p>`;syncControls();
}
function markReviewDirty(message='Fields changed. Confirm your review and check them again.',keepSourceResolutions=false){
 completedIntake=false;
 const form=document.querySelector<HTMLFormElement>('#review-form');if(form)clearReviewFieldIssues(form);
 if(form&&!keepSourceResolutions)for(const input of Array.from(form.querySelectorAll<HTMLTextAreaElement|HTMLSelectElement>('[data-source-amount] textarea,[data-source-amount] select')))input.value='';
 nativeActivity?.currentChanged();
 workspaceFiles?.currentChanged();
 intakeFiles.currentChanged();
 reviewEngaged=true;reviewId=null;
 const check=$<HTMLInputElement>('#review-confirm');if(check)check.checked=false;
 const result=$<HTMLElement>('#review-result');if(result)result.textContent=message;
 syncControls();
}
function renderAnalysis(ex:any){
 completedIntake=false;
 showInvoiceDocument();
 intakeFiles.currentChanged();
 reviewId=null;reviewEngaged=false;rawDraftUsed=false;
 analysisResult=ex;documentFinancials=ex.financials_by_currency||[];documentTotals=ex.totals_by_currency||[{currency:ex.currency,total:ex.total}];documentLineAmounts=ex.line_amounts||[];const ai=ex.source==='llm';const panel=$<HTMLElement>('#analysis'),items=ex.line_items||[],issues=ex.issues||[];
 const headline=ai?'AI proposal · review required':analysisBlocked(ex)?'Review needed':`Rules extracted ${items.length} ${items.length===1?'item':'items'}`;
 const actionableIssues=sourceReviewIssues(ex);
 const flags=sourceReviewWarningsMarkup(ex);
 panel.innerHTML=`<section id="invoice-document" aria-label="Proposed invoice"></section><div class="analysis-head"><strong id="review-analysis-heading">${esc(headline)}</strong></div>
 ${actionableIssues.length?`<details id="review-source-warnings" class="source-warnings" aria-label="Original extraction warnings" data-reviewed="false" open><summary><span id="review-source-warning-heading">Check against the original request</span></summary><ul id="review-source-warning-list" class="issue-list">${flags}</ul></details>`:''}
 ${reviewMarkup(ex)}`;
 applyReviewFieldIssues($<HTMLFormElement>('#review-form'),issues);
 documentCalculationFields=JSON.stringify(readReviewFields($<HTMLFormElement>('#review-form')));
 if(native)reviewEngaged=true;
 syncControls();
 if(native)rememberNativeIntake();
}function renderState(state:any){
 latestSandboxState=state;
 approvalNavigation.clearOutcome();
 if(native)renderNativeDecisions(state);
 invoiceRecords.update(state);
 clientStatements.update(state);
 ledgerExport.setSnapshot(state);
 receivables.setSnapshot(state);
 canReplay=Boolean(state.can_replay);
 $<HTMLElement>('#mock-requests').textContent=String(native?(state.provider_operations??0):(state.mock_requests??0));$<HTMLElement>('#external-calls').textContent=String(state.external_calls??0);
 const dateInput=$<HTMLInputElement>('#demo-date');if(document.activeElement!==dateInput)dateInput.value=state.today||'';$<HTMLButtonElement>('#replay-webhook').disabled=!ready||!state.can_replay||busy;
 const queue=state.pending||[];$<HTMLElement>('#approval-list').innerHTML=approvalListMarkup(queue);
 rejectionReasons.afterRender(queue.map((action:any)=>action.id));
 if(native)nativeApprovalFields(queue);
 approvalNavigation.refresh();
 let reminderActions = new Map<string,string>();
 try { reminderActions = targetedReminderActions(state); }
 catch { /* The receivables board explains unsupported or inconsistent native rows. */ }
 const ledger=state.ledger||[];invoiceDetails.beforeRender(ledger.map((entry:any)=>entry.invoice_id));$<HTMLElement>('#ledger-list').innerHTML=ledger.length?ledger.map((e:any)=>{const open=['SENT','PARTIALLY_PAID','UNPAID'].includes(e.status)&&Number(e.balance)>0;return `<article class="invoice-card invoice-card-with-details" data-saved-invoice-id="${esc(e.invoice_id)}" data-due="${esc(e.due_on||'')}">${savedInvoiceSummaryMarkup(e)}<div class="saved-invoice-document-actions">${savedInvoiceSendReviewMarkup(e,queue)}${invoiceRecords.markup(e.invoice_id,'print')}</div>${open&&!native?`<button class="button button-payment" data-pay="${esc(e.invoice_id)}">Simulate sandbox payment</button>`:''}${reminderActions.get(e.invoice_id)||''}${invoiceDetails.markup(e.invoice_id,state)}${savedRequests.markup(e.invoice_id,state)}<details class="invoice-downloads"><summary>Download or export</summary>${invoiceRecords.markup(e.invoice_id)}${clientStatements.markup(e.invoice_id)}</details>${draftCorrections.markup(e,state)}${['SENT','UNPAID'].includes(e.status)&&Number(e.paid_amount)===0?`<details class="invoice-details"><summary>Cancel mistaken invoice</summary><p>For an unpaid invoice that should no longer be collected. Review first; nothing changes until you approve.</p><label>Reason <input maxlength="500" data-cancel-reason="${esc(e.invoice_id)}"></label><button class="button" data-prepare-cancel="${esc(e.invoice_id)}">Review cancellation</button></details>`:''}${(state.completed_reviews||[]).filter((a:any)=>a.invoice_id===e.invoice_id&&['send_invoice','cancel_invoice','update_invoice'].includes(a.kind)).map((a:any)=>`<div class="fine-print"><strong>${a.kind==='update_invoice'?'Draft correction':a.kind==='cancel_invoice'?'Cancellation':'Send review'}: ${esc(a.status)}</strong><p>${esc(a.result?.reason||(a.kind==='update_invoice'&&a.result?.corrected?'Corrected to '+e.currency+' '+a.result.total+'. Sending requires a separate review.':''))}</p>${a.kind==='send_invoice'&&a.status==='REJECTED'&&e.status==='DRAFT'&&!(state.pending||[]).some((p:any)=>p.invoice_id===e.invoice_id)?`<button class="button" data-reopen-send="${esc(a.id)}">Review this draft again</button><p>Uses the same unchanged invoice. Sending still needs a separate approval.</p>`:''}</div>`).join('')}<a class="collections-return" href="#receivables-view">Back to collections</a></article>`;}).join(''):'<p class="empty">No invoices in this sandbox ledger yet.</p>';
 savedInvoices.refresh(ledger);
 invoiceDetails.afterRender();invoiceDetails.setAvailability(ready,busy);
 invoiceRecords.setAvailability(ready,busy);
 clientStatements.setAvailability(ready,busy);
 $<HTMLOListElement>('#audit-list').innerHTML=invoiceActivityMarkup(state.audit||[],state);
}
async function runAction(name:string,payload:Record<string,unknown>={}){
 if(busy)return null;
 if(!ready&&name!=='init'){status('Load the local Python engine first.','error');return null;}
 if(native&&['review','draft','reset','analyze_ai','analyze_rules'].includes(name))nativeActivity?.currentChanged();
 if(['analyze','analyze_rules','review','draft','reset'].includes(name))intakeFiles.currentChanged('An intake action started. Open the file again before replacing the current input.');
 clearTimeout(draftSaveTimer);busy=true;syncControls();
 try{
  const reply=await call(name,payload);
  if(reply.state)renderState(reply.state);
  if(!reply.ok){status(reply.message||reply.error||'The sandbox action failed.','error');return reply;}
  if(reply.result?.message)status(reply.result.message,name==='refresh'&&reply.result?.errors?.length?'error':'ready');else if(reply.result?.final)status(reply.result.final,'ready');
  return reply;
 }catch(error){status(error instanceof WorkerUnavailableError?recoveryMessage(error):String(error),'error');return null;}
 finally{busy=false;syncControls();}
}
$('#workspace-reload').addEventListener('click',()=>{
 if(nativeIntakeBlocked&&!confirm('Reopen the latest saved request and replace these unfinished fields? Use Save intake first to keep this version.'))return;
 $<HTMLButtonElement>('#engine-start').click();
});
$('#engine-start').addEventListener('click',async()=>{
 const b=$<HTMLButtonElement>('#engine-start'),restarting=needsFreshSandbox,conflicted=workspaceConflict||nativeIntakeBlocked;b.disabled=true;
 status(native?'Opening the saved invoice desk...':'Loading local Python and Ledgerly source...','loading');const reply=await runAction('init');
 if(reply?.ok){
  needsFreshSandbox=false;setControls(true);b.textContent='Python engine loaded';
  $<HTMLElement>('#engine-status').textContent=native?native.provider.toUpperCase()+' / SAVED LEDGER':'PYTHON READY / OFFLINE';$<HTMLElement>('#engine-dot').classList.add('ready');
  $<HTMLElement>('#engine-note').textContent=native?'The native operator retains invoices and decisions in its private state file. Reopening does not approve or repeat an action.':'Invoices and pending decisions reopen in this browser. Clear saved workspace discards them.';
  const recovery=$<HTMLElement>('#engine-recovery-analysis');
  if(restarting&&recovery)recovery.textContent=reviewNeedsAnalysis?'Saved workspace reopened. Your corrections are retained; confirm and check them again.':'Saved workspace reopened. Analyze the retained source email again.';
  for(const id of ['approval-list','ledger-list'])$<HTMLElement>(`#${id}`).removeAttribute('aria-disabled');
  status(native?'Saved invoice desk open. Provider actions require your explicit confirmation.':reply.result?.restored?'Saved local workspace reopened. Pending sends still need your decision.':'Local workspace ready. Simulated payments; no external service is connected.','ready');
   let incoming=reply.result?.restored_intake;
   let restoredTab:NativeIntake|null=null,durableTab:NativeIntake|null=null;
   if(nativeIntakeStore){
    nativeIntakeOpening=true;syncControls();nativeIntakeLoaded=false;
    try{
     durableTab=await nativeIntakeStore.load();nativeIntakeLoaded=true;
     if(durableTab){restoredTab=durableTab;incoming=durableTab.intake;}
    }catch{
     nativeIntakeStorageStatus('error');
     status('The saved unfinished request could not be opened. Keep this tab open or use your saved intake file.','error');
    }
   }
   if(nativeIntakeKey&&!durableTab&&!restarting&&!$<HTMLTextAreaElement>('#job-email').value){
    try{const saved=sessionStorage.getItem(nativeIntakeKey)||(legacyNativeIntakeKey?sessionStorage.getItem(legacyNativeIntakeKey):null);if(saved){restoredTab=decodeNativeIntake(saved);incoming=restoredTab.intake;}}
    catch{status('The saved tab intake could not be opened. Open your saved intake file or enter the request.','error');}
   }
   if((!restarting||conflicted)&&incoming?.source_text){
    restoringNativeIntake=Boolean(native);
    try{
     const restored=await replaceIntake(incoming);
     const form=$<HTMLFormElement>('#review-form');
     if(restored&&restoredTab&&form){restoreNativeChoices(form,restoredTab.choices);syncControls();}
    }finally{restoringNativeIntake=false;}
    if(nativeIntakeLoaded&&!durableTab)rememberNativeIntake();
   }
   else if(conflicted){
    $<HTMLTextAreaElement>('#job-email').value='';
    invalidateAnalysis('Latest saved workspace reopened. Open an email or start a request.');
   }
   if(!native && reply.workspace?.restored===false && !incoming?.source_text && !$<HTMLTextAreaElement>('#job-email').value && !(reply.state?.ledger||[]).length){
    try{
     const sample=await fetch(new URL('fixtures/01_simple_usd_hourly.txt',new URL('./',window.location.href)));
     const text=sample.ok?await sample.text():'';
     if(text && !$<HTMLTextAreaElement>('#job-email').value && !busy){
      $<HTMLTextAreaElement>('#job-email').value=text;
      const proposal=await runAction('analyze',{text});if(proposal?.ok)renderAnalysis(proposal.result);
      const label=document.querySelector('#source-request>summary span');if(label)label.textContent='View source';
     }
    }catch{ /* The editable empty desk remains usable if the bundled example is unavailable. */ }
   }
   await nativeActivity?.resume();
   nativeIntakeOpening=false;syncControls();
   workspaceConflict=false;
   showDeskView(preferredDeskView());
   if(!incoming||(restarting&&!conflicted))scheduleIntakeSave();
 }else{
  b.disabled=false;b.textContent=needsFreshSandbox?'Reload saved workspace':'Retry loading engine';
  if(!needsFreshSandbox)$<HTMLElement>('#engine-note').textContent=reply?.message||'Loading did not finish. Retry loading when the runtime files are available. Your source email is preserved.';
 }
});
$('#load-fixture').addEventListener('click',async()=>{
 if(!ready||busy)return;busy=true;syncControls();
 try{const name=$<HTMLSelectElement>('#fixture-select').value;const r=await fetch(new URL(`fixtures/${name}`,new URL('./',window.location.href)));if(!r.ok)throw new Error(`Fixture HTTP ${r.status}`);$<HTMLTextAreaElement>('#job-email').value=await r.text();invalidateAnalysis('Fictional input loaded. Analyze it to review and correct its fields.');status('Fictional input loaded. Analyze it with the real Python rules.','ready');}
 catch(error){status(String(error),'error');}finally{busy=false;syncControls();scheduleIntakeSave();}
});
$('#job-email').addEventListener('input',()=>invalidateAnalysis(native?'Source text changed. Compare and check the retained fields against the new request, or analyze again.':'Source text changed. Analyze it again before reviewing or drafting.'));
$('#analyze').addEventListener('click',async()=>{
 if(nativeActivity){await nativeActivity.start();return;}
 const text=$<HTMLTextAreaElement>('#job-email').value;if(!text.trim()){status('Paste or load a job email first.','error');return;}
 invalidateAnalysis('Running the Python rules on this source...');status('Running RulesExtractor in Python...','loading');
 const reply=await runAction('analyze',{text});if(reply?.ok){renderAnalysis(reply.result);const bad=analysisBlocked(reply.result);status(bad?'Resolve the warnings in invoice fields, then check them.':native?'Check every invoice field, then create an unsent provider draft.':'Review extracted fields, then create the saved sandbox draft.','ready');}
});
$('#cancel-ai').addEventListener('click', () => {
  if (!activeAi || activeAi.committing) return;
  abandonAi();
  syncControls();
  status('AI analysis cancelled. Your intake and previous review are unchanged.', 'ready');
  $<HTMLButtonElement>('#analyze-ai').focus();
  scheduleIntakeSave();
});
$('#analyze-ai').addEventListener('click', async () => {
  if (!ready || busy) return;
  const text = $<HTMLTextAreaElement>('#job-email').value;
  if (!text.trim()) { status('Paste or load a job email first.', 'error'); return; }
  if (text.length > 30000) { status('Use at most 30,000 characters for on-device AI. Your intake is unchanged.', 'error'); return; }
  if (nativeActivity) { await nativeActivity.start(true); return; }
  const run: AiRun = {controller: new AbortController(), text, committing: false, workspaceFailed: false, session: null};
  activeAi = run;
  busy = true;
  syncControls();
  showAiProgress(run, 'Opening on-device AI…');
  status('Opening on-device AI. Your previous invoice review stays available after cancellation or failure.', 'loading');
  try {
    // Start synchronously on this user gesture, before the first await.
    run.session = await startOnDeviceSession(undefined, {
      signal: run.controller.signal,
      onDownloadProgress: fraction => showAiProgress(run, 'Downloading the on-device model · ' + Math.floor(fraction * 100) + '%', fraction),
    });
    if (!aiCurrent(run)) return;
    showAiProgress(run, 'Preparing the invoice prompt…');
    const promptReply = await call('ai_prompt', {text});
    if (aiWorkspaceStopped(run, promptReply) || !aiCurrent(run)) return;
    if (!promptReply.ok) throw new Error(promptReply.message || 'Could not prepare the local AI prompt.');
    showAiProgress(run, 'Analyzing the email on this device…');
    const completion = await completeInvoicePrompt(run.session, promptReply.result.prompt, run.controller.signal);
    if (!aiCurrent(run)) return;
    if (typeof completion !== 'string') throw new Error('The local model did not return text.');
    // The native request is finished. Admit its proposal atomically in Python;
    // this short step cannot be cancelled after it has been posted to the worker.
    run.committing = true;
    showAiProgress(run, 'Checking the proposed invoice fields…');
    const reply = await call('analyze_ai', {text, completion});
    if (aiWorkspaceStopped(run, reply) || !aiCurrent(run)) return;
    if (reply.state) renderState(reply.state);
    if (!reply.ok) throw new Error(reply.message || 'Python could not validate the model output.');
    renderAnalysis(reply.result);
    reviewEngaged = true; reviewId = null; reviewNeedsAnalysis = false;
    const editor = $<HTMLDetailsElement>('#review-editor');
    editor.open = true;
    editor.querySelector('summary')?.focus();
    // reviewMarkup keeps the proposed totals and required-review instruction together.
    status('Real local-model output checked by Python. Human review is required before a draft.', 'ready');
  } catch (error) {
    if (aiCurrent(run))
      status('On-device AI did not finish: ' + String(error) + '. Your intake and previous review are unchanged. You can retry or use Python rules.', 'error');
  } finally {
    closeAiSession(run);
    if (activeAi === run) {
      activeAi = null;
      busy = false;
      $<HTMLElement>('#ai-progress').hidden = true;
      syncControls();
      if (!run.workspaceFailed) scheduleIntakeSave();
    }
  }
});
$('#analysis').addEventListener('input',(event)=>{
 const target=event.target as HTMLInputElement;
 if(target.id==='review-saved-contact')return;
 if(target.id==='review-confirm'){completedIntake=false;nativeActivity?.currentChanged();intakeFiles.currentChanged();reviewEngaged=true;reviewId=null;$<HTMLElement>('#review-result').textContent=target.checked?'Review confirmed. Check the fields with Python to prepare a draft.':'Confirm your review before checking the fields.';syncControls();return;}
 if(!target.closest('#review-form'))return;
 if(target.id==='review-term-basis')syncReviewTerms(target.closest('#review-form') as HTMLFormElement);
 const editedLine=target.closest<HTMLElement>('[data-review-line]');
 const form=target.closest<HTMLFormElement>('#review-form')!;
 const lineIndex=editedLine?Array.from(form.querySelectorAll('[data-review-line]')).indexOf(editedLine):-1;
 if(lineIndex>=0){
  // A changed item invalidates its mapping and exclusion reasons, not other
  // unchanged items. Checking and native CREATE are still invalidated below.
  for(const row of Array.from(form.querySelectorAll<HTMLElement>('[data-source-amount]'))){
   const select=row.querySelector<HTMLSelectElement>('select')!;
   if(select.value==='line:'+(lineIndex+1)||!select.value.startsWith('line:')){
    select.value='';row.querySelector<HTMLTextAreaElement>('textarea')!.value='';
   }
  }
 }
 // Contact, terms and note edits do not change which charges were included.
 // Preserve those human decisions, while requiring a fresh check of all fields.
 const metadataOnly=['review-client-name','review-client-email','review-term-basis','review-due-days','review-due-date','review-note'].includes(target.id);
 markReviewDirty(undefined,lineIndex>=0||metadataOnly||Boolean(target.closest('[data-source-amount]')));
});
$('#analysis').addEventListener('click',(event)=>{
 const noteAction=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-edit-note]');
 if(noteAction){if(busy)return;const editor=$<HTMLDetailsElement>('#review-editor');editor.open=true;revealDeskTarget(editor,$<HTMLTextAreaElement>('#review-note'));return;}
 const exclusionAction=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-source-exclude]');
 if(exclusionAction){
  if(busy||reviewNeedsAnalysis)return;
  const form=$<HTMLFormElement>('#review-form'),id=exclusionAction.dataset.sourceExclude;
  const entry=(analysisResult?.source_amount_requirements||[]).find((item:any)=>item.id===id);
  const fields=readReviewFields(form);
  if(JSON.stringify(fields.line_items)!==JSON.stringify(fieldsFromExtraction(analysisResult).line_items))return;
  const choice=sourceExclusionChoice(entry?.quote,fields);
  const row=Array.from(form.querySelectorAll<HTMLElement>('[data-source-amount]')).find(item=>item.dataset.sourceAmount===id);
  if(!choice||!row)return;
  row.querySelector<HTMLSelectElement>('select')!.value='excluded';
  row.querySelector<HTMLTextAreaElement>('textarea')!.value=choice.reason;
  markReviewDirty('Source decision saved. Confirm and check the invoice fields.',true);
  Array.from(document.querySelectorAll<HTMLButtonElement>('[data-review-charge]')).find(button=>button.dataset.reviewCharge===id)?.focus();
  return;
 }
 const chargeAction=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-review-charge]');
 if(chargeAction){if(busy)return;const editor=$<HTMLDetailsElement>('#review-editor');editor.open=true;const target=document.getElementById('source-amount-'+chargeAction.dataset.reviewCharge);if(target)revealDeskTarget(editor,target);return;}
 const itemAction=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-edit-item]');
 if(itemAction){
  if(busy)return;
  const editor=$<HTMLDetailsElement>('#review-editor');editor.open=true;
  const row=$<HTMLFormElement>('#review-form').querySelectorAll<HTMLElement>('[data-review-line]')[Number(itemAction.dataset.editItem)];
  const field=Array.from(row?.querySelectorAll<HTMLElement>('[data-field]')||[]).find(input=>input.dataset.field===itemAction.dataset.editField);
  revealDeskTarget(editor,field||row);return;
 }
 const unitAction=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-edit-unit]');
 if(unitAction){if(busy)return;const editor=$<HTMLDetailsElement>('#review-editor');editor.open=true;revealDeskTarget(editor,$<HTMLInputElement>('#review-line-'+unitAction.dataset.editUnit+'-unit'));return;}
 if((event.target as HTMLElement).closest('[data-edit-document]')){const editor=$<HTMLDetailsElement>('#review-editor');editor.open=true;revealDeskTarget(editor,$<HTMLElement>('#review-saved-contact')||$<HTMLElement>('#review-client-name'));return;}
 const button=(event.target as HTMLElement).closest<HTMLButtonElement>('#review-add-line,button[data-remove-line]');
 if(!button||busy)return;
 const paperRemoval=Boolean(button.closest('#invoice-document'));
 const form=$<HTMLFormElement>('#review-form'),fields=readReviewFields(form);
 if(button.id==='review-add-line')fields.line_items.push({desc:'',qty:'1',unit_price:'',currency:analysisResult.currency||'',unit:''});
 else fields.line_items.splice(Number(button.dataset.removeLine),1);
 markReviewDirty();$<HTMLElement>('#review-lines').innerHTML=reviewLinesMarkup(fields.line_items,analysisResult.review_currencies||[]);syncControls();
 if(button.id==='review-add-line')form.querySelector<HTMLInputElement>(`#review-line-${fields.line_items.length-1}-desc`)?.focus();
 else if(paperRemoval)(document.querySelector<HTMLButtonElement>('#invoice-document [data-remove-line]')||$<HTMLInputElement>('#review-confirm')).focus();
 else $<HTMLButtonElement>('#review-add-line').focus();
});
$('#analysis').addEventListener('submit',async(event)=>{
 const form=event.target as HTMLFormElement;if(form.id!=='review-form')return;event.preventDefault();
 if(busy||!$<HTMLInputElement>('#review-confirm').checked)return;
 reviewEngaged=true;reviewId=null;syncControls();
 const result=$<HTMLElement>('#review-result'),text=$<HTMLTextAreaElement>('#job-email').value,fields=readReviewFields(form);
 result.textContent='Checking the corrected fields with Python...';
 if(reviewNeedsAnalysis&&!native){
  const analyzed=await runAction('analyze',{text});
  if(!analyzed?.ok){result.textContent='The source could not be checked in the new sandbox. Your edits remain here.';return;}
  analysisResult=analyzed.result;reviewNeedsAnalysis=false;
 }
 const reply=await runAction('review',{text,fields,confirmed:true,source_resolutions:readSourceResolutions(form)});
 if(reply?.ok){
  const requirements=reply.result.source_amount_requirements||[];
  const existing=Array.from(form.querySelectorAll<HTMLElement>('[data-source-amount]')).map(row=>row.dataset.sourceAmount);
  if(JSON.stringify(existing)!==JSON.stringify(requirements.map((entry:any)=>entry.id))){
   form.querySelector<HTMLElement>('.source-amount-review')!.innerHTML=sourceAmountMarkup({...fields,line_items:fields.line_items,source_amount_requirements:requirements});
  }
  if(native)reviewNeedsAnalysis=false;reviewId=reply.result.valid?reply.result.review_id:null;if(reply.result.valid){documentCalculationFields=JSON.stringify(readReviewFields($<HTMLFormElement>('#review-form')));documentFinancials=reply.result.financials_by_currency||[];documentTotals=reply.result.totals_by_currency||[];documentLineAmounts=reply.result.line_amounts||[];$<HTMLDetailsElement>('#review-editor').open=false;}result.innerHTML=reviewResultMarkup(reply.result);const invalidField=applyReviewFieldIssues(form,reply.result.issues||[]);if(reply.result.valid)$<HTMLElement>('#engine-recovery-analysis')?.remove();else{$<HTMLDetailsElement>('#review-editor').open=true;revealDeskTarget(invalidField?.closest<HTMLElement>('label')||result,invalidField||result);}}
 else if(reply?.issues?.length){result.innerHTML=reviewResultMarkup({valid:false,issues:reply.issues});const invalidField=applyReviewFieldIssues(form,reply.issues);$<HTMLDetailsElement>('#review-editor').open=true;revealDeskTarget(invalidField?.closest<HTMLElement>('label')||result,invalidField||result);}
 else result.textContent=reply?.message||'The fields could not be checked. Your edits are still here.';
 syncControls();
 if(reply?.ok&&reply.result.valid)(native?$<HTMLInputElement>('#native-create-confirm'):$<HTMLButtonElement>('#draft'))?.focus();
});
$('#draft').addEventListener('click',async()=>{
 if(!analysisResult||busy||(reviewEngaged?!reviewId:rawDraftUsed||analysisBlocked(analysisResult)))return;
 const payload:Record<string,unknown>={text:$<HTMLTextAreaElement>('#job-email').value};
 if(native){payload.confirmation=$<HTMLInputElement>('#native-create-confirm').value;$<HTMLInputElement>('#native-create-confirm').value='';}
 if(reviewEngaged){payload.review_id=reviewId;reviewId=null;}else rawDraftUsed=true;
 status(native?'Creating the reviewed unsent '+native.provider+' draft...':'Running Ledgerly Agent + RulePlanner against SandboxMock...','loading');
 const previous=pendingIds();
 const reply=await runAction('draft',payload);
 if(reply?.ok){
  completedIntake=!reply.workspace?.error&&!reply.workspace?.conflict;
  status(reply.result.unauthorized_send_blocked?'Gate verified: an unapproved send was blocked. '+reply.result.final:reply.result.final,'ready');
  if(nativeIntakeKey){
   try{sessionStorage.removeItem(nativeIntakeKey);if(legacyNativeIntakeKey)sessionStorage.removeItem(legacyNativeIntakeKey);await nativeIntakeStore?.clear();}
   catch{status('The invoice was created, but its unfinished tab copy could not be cleared.','error');}
  }
  openNewReviews(previous,'send_invoice','New invoice review ready. Sending still needs your separate approval.');
  if(reviewEngaged){$<HTMLElement>('#review-result').textContent='This checked revision was used. Review its pending send in Approvals. To prepare a different draft, edit and check the fields again.';$<HTMLInputElement>('#review-confirm').checked=false;}
 }else if(reviewEngaged){$<HTMLElement>('#review-result').textContent='Drafting did not complete. Check the sandbox ledger before preparing another revision. Your edits remain here.';$<HTMLInputElement>('#review-confirm').checked=false;}
 syncControls();
});
$('#approval-list').addEventListener('click',async(event)=>{
 const b=(event.target as HTMLElement).closest<HTMLButtonElement>('button[data-approve],button[data-reject]');
 if(!b||busy||!ready)return;
 const approving=Boolean(b.dataset.approve),id=b.dataset.approve||b.dataset.reject||'';
 const payload:Record<string,unknown>={action_id:id};
 if(approving&&native){const phrase=nativeConfirmation(id);if(!phrase){status('Enter the exact confirmation beside this decision.','error');return;}payload.confirmation=phrase;}
 if(!approving){
  const reason=rejectionReasons.read(id);
  if(!reason.ok){status(reason.message,'error');return;}
  payload.reason=reason.value;
 }
 const decision=(latestSandboxState?.pending||[]).find((action:any)=>action.id===id);
 const invoice=(latestSandboxState?.ledger||[]).find((entry:any)=>entry.invoice_id===decision?.invoice_id);
 b.disabled=true;
 const preview=(b.closest('[data-approval-id]')?.querySelector('.approval-preview-content')?.cloneNode(true) as HTMLElement|undefined) ?? null;
 const reply=await runAction(approving?'approve':'reject',payload);
 const message=reply?.ok?(reply.result.approved?(native?native.provider+' accepted the approved action.':'Approved in the local simulation.'):`Rejected; nothing was sent. Reason: ${reply.result.reason}`):($<HTMLElement>('#status-message').textContent||'The decision did not complete. Review its current state before trying again.');
 if(reply?.ok)status(message,'ready');
 const recordedInvoice=(latestSandboxState?.ledger||[]).find((entry:any)=>entry.invoice_id===decision?.invoice_id)||invoice;
 approvalNavigation.outcome(message,decision?.invoice_id,recordedInvoice?[recordedInvoice.invoice_number,recordedInvoice.client_name||recordedInvoice.client_email].filter(Boolean).join(' · '):'',Boolean(reply?.ok),{approved:Boolean(reply?.result?.approved),preview,total:recordedInvoice?displayMoney(recordedInvoice.total,recordedInvoice.currency):''});
});
$('#ledger-list').addEventListener('click',async(event)=>{const b=(event.target as HTMLElement).closest<HTMLButtonElement>('button[data-pay]');if(!b)return;b.disabled=true;const amount=$<HTMLInputElement>('#payment-amount').value.trim();const reply=await runAction('payment',{invoice_id:b.dataset.pay||'',amount:amount||null});if(reply?.ok)status(`Mock webhook verified: ${reply.result.from} -> ${reply.result.to}.`,'ready');});
$('#ledger-list').addEventListener('click',async(event)=>{
 const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button[data-remind]');
 if(!button||!ready||busy||button.disabled)return;
 const id=button.dataset.remind;
 if(!id)return;
 button.disabled=true;
 const previous=pendingIds();
 const reply=await runAction('remind_invoice',{invoice_id:id});
 if(reply?.ok){
  status('Reminder prepared. Review its full message in Approvals; nothing was sent.','ready');
  openNewReviews(previous,'send_reminder','New reminder ready for review. Nothing has been sent.');
 }
});
$('#advance-clock').addEventListener('click',async()=>{const day=$<HTMLInputElement>('#demo-date').value;if(!day){status('Choose a date for the local clock.','error');return;}await runAction('advance',{day});});
$('#run-chase').addEventListener('click',async()=>{const previous=pendingIds();status('Running the overdue scan and reminder rules...','loading');const reply=await runAction('chase');if(reply?.ok)openNewReviews(previous,'send_reminder','New reminder ready for review. Nothing has been sent.');});
$('#ledger-list').addEventListener('click',event=>{
 const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button[data-review-action]');
 if(button?.dataset.reviewAction)approvalNavigation.open(button.dataset.reviewAction,button.dataset.reviewKind==='send'?'Review this draft’s queued send. Nothing has been sent.':'Review this queued reminder. Opening it does not send anything.');
});
$('#audit-list').addEventListener('click',event=>{
 const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button');
 if(button?.dataset.activityInvoice)openInvoice(button.dataset.activityInvoice);
 if(button?.dataset.activityReview)approvalNavigation.open(button.dataset.activityReview,'Review this existing decision. Nothing has been sent.');
});
$('#replay-webhook').addEventListener('click',async()=>{const reply=await runAction('replay');if(reply?.ok)status(reply.result.duplicate?'Duplicate webhook safely ignored by Ledgerly.':'Webhook processed.','ready');});
$('#reset-sandbox').addEventListener('click',async()=>{
 if(busy||!confirm('Clear saved invoices, payment simulations, pending decisions and intake from this browser?'))return;
 const reply=await runAction('init',{discard_saved:true});
 if(reply?.ok){
  needsFreshSandbox=false;workspaceConflict=false;setControls(true);
  const start=$<HTMLButtonElement>('#engine-start');start.disabled=true;start.textContent='Python engine loaded';
  $<HTMLElement>('#engine-status').textContent='PYTHON READY / OFFLINE';$<HTMLElement>('#engine-dot').classList.add('ready');
  $<HTMLTextAreaElement>('#job-email').value='';invalidateAnalysis('Workspace cleared. Open an email or start a new request.');
  $<HTMLInputElement>('#payment-amount').value='';
  status('Saved workspace cleared. There are no invoices or pending decisions.','ready');
 }
});
document.addEventListener('click',event=>{
 if(!(event.target as HTMLElement).closest('[data-desk-view="compose"]')||!completedIntake||!ready||busy)return;
 $<HTMLTextAreaElement>('#job-email').value='';
 invalidateAnalysis('Paste or open the next client request.');
 scheduleIntakeSave();
 $<HTMLTextAreaElement>('#job-email').focus();
});
document.addEventListener('input',event=>{
 const target=event.target as HTMLElement;
 if(target.id==='job-email'||target.closest('#review-form'))scheduleIntakeSave();
});
document.addEventListener('click',event=>{
 if((event.target as HTMLElement).closest('#review-add-line,button[data-remove-line]'))scheduleIntakeSave();
});

if(native){
 document.getElementById('native-create-confirm')?.addEventListener('input',syncControls);
 document.getElementById('native-refresh')?.addEventListener('click',()=>runAction('refresh'));
}

if(native)document.getElementById('native-decisions')?.addEventListener('click',async(event)=>{
 const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button[data-recover-creation]');
 if(!button||!ready||busy)return;
 const attempt=button.dataset.recoverCreation;
 const input=Array.from(document.querySelectorAll<HTMLInputElement>('input[data-creation-id]')).find(value=>value.dataset.creationId===attempt);
 if(!input?.value.trim()){status('Copy the original Sandbox invoice ID before recovery.','error');return;}
 await runAction('recover_creation',{attempt_id:attempt,invoice_id:input.value.trim()});
});
$('#ledger-list').addEventListener('click',async(event)=>{
 const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button[data-reopen-send]');
 if(!button)return;
 button.disabled=true;
 const previous=pendingIds();
 const reply=await runAction('reopen_send_review',{action_id:button.dataset.reopenSend});
 if(reply?.ok){
  status('Same draft reopened for review. Nothing was sent; approve or reject the new decision.','ready');
  openNewReviews(previous,'send_invoice','Same unchanged draft, new review. Nothing has been sent.');
 }
});

$('#ledger-list').addEventListener('click',async(event)=>{
 const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button[data-prepare-cancel]');
 if(!button)return;
 const id=button.dataset.prepareCancel||'';
 const input=Array.from(document.querySelectorAll<HTMLInputElement>('input[data-cancel-reason]')).find(e=>e.dataset.cancelReason===id);
 const reply=await runAction('prepare_cancellation',{invoice_id:id,reason:input?.value||''});
 if(reply?.ok){status('Cancellation ready for your decision. The invoice has not changed.','ready');revealDeskTarget($('#approval-list'));}
});

installFocusedDesk();
// Local runtime startup has no invoice or provider side effects.
queueMicrotask(()=>document.getElementById('engine-start')?.click());
