import {displayMoney} from './saved-invoice';
import {decodeWorkspaceFile, MAX_WORKSPACE_FILE_BYTES} from './workspace-file';

type Preview = {token:string;saved_at:string;state:any;current_invoice_count:number;intake?:any};
type Hooks = {run:(action:string,payload?:Record<string,unknown>)=>Promise<any>;restoreIntake:(intake:any)=>void|Promise<unknown>};
const make=<K extends keyof HTMLElementTagNameMap>(tag:K,text?:string)=>{
 const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;
};
export function createWorkspaceFileControls(mount:HTMLElement,hooks:Hooks){
 let ready=false,busy=false,working=false,hasPrevious=false,generation=0;
 let preview:Preview|null=null,previewIntake:any=null,returning=false;
 const section=make('section');section.className='workspace-files';section.id='workspace-files';
 section.setAttribute('aria-label','Saved workspace files');
 const actions=make('div');actions.className='workspace-file-actions';
 const save=make('button','Save workspace'),open=make('button','Open workspace'),previous=make('button','Return to previous workspace');
 for(const button of [save,open,previous]){button.type='button';button.className='button button-secondary';}
 save.id='workspace-file-save';open.id='workspace-file-open';previous.id='workspace-file-previous';previous.hidden=true;
 const file=make('input');file.type='file';file.accept='.json,application/json';file.hidden=true;file.id='workspace-file-input';
 const note=make('p','Keep invoices, pending decisions and unfinished intake together in a private file.');note.className='fine-print';
 const feedback=make('p');feedback.id='workspace-file-feedback';feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');
 const panel=make('section');panel.id='workspace-file-preview';panel.hidden=true;panel.setAttribute('aria-labelledby','workspace-file-heading');
 const heading=make('h3','Saved workspace');heading.id='workspace-file-heading';heading.tabIndex=-1;
 const summary=make('p');summary.id='workspace-file-summary';
 const saved=make('time');saved.className='workspace-file-saved';
 const list=make('div');list.className='workspace-file-invoices';
 list.tabIndex=0;list.setAttribute('role','region');list.setAttribute('aria-label','Saved invoices');
 const intake=make('pre');intake.id='workspace-file-intake';
 const editable=make('div');editable.id='workspace-file-fields';
 const warning=make('p');warning.className='workspace-file-warning';
 const apply=make('button','Open workspace'),cancel=make('button','Cancel');
 apply.type=cancel.type='button';apply.id='workspace-file-apply';cancel.id='workspace-file-cancel';
 apply.className='button button-primary';cancel.className='button button-secondary';
 const choices=make('div');choices.className='workspace-file-actions workspace-file-choices';choices.append(apply,cancel);
 const unfinished=make('details');unfinished.className='workspace-file-unfinished';
 unfinished.append(make('summary','Unfinished invoice'),intake,editable);
 panel.append(heading,summary,saved,warning,choices,list,unfinished);
 actions.append(save,open,previous,file);section.append(actions,note,feedback,panel);mount.insertAdjacentElement('afterend',section);
 function sync(){
  for(const button of [save,open,previous])button.disabled=!ready||busy||working;
  file.disabled=!ready||busy||working;previous.hidden=!hasPrevious;
  apply.disabled=!ready||busy||working||!preview;cancel.disabled=working;
 }
 function clear(message=''){
  generation++;preview=null;previewIntake=null;panel.hidden=true;file.value='';
  if(message)feedback.textContent=message;sync();
 }
 function render(value:Preview,source:any,isPrevious:boolean){
  preview=value;previewIntake=source;returning=isPrevious;
  heading.textContent=isPrevious?'Previous workspace':'Saved workspace';
  const ledger=Array.isArray(value.state?.ledger)?value.state.ledger:[];
  const pending=Array.isArray(value.state?.pending)?value.state.pending:[];
  summary.textContent=ledger.length+' invoices · '+pending.length+' pending decisions';
  const savedDate=new Date(value.saved_at);saved.dateTime=value.saved_at;saved.title=value.saved_at;
  saved.textContent='Saved '+(Number.isNaN(savedDate.getTime())?value.saved_at:savedDate.toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'}));
  list.replaceChildren();
  if(!ledger.length)list.append(make('p','No invoices in this saved workspace.'));
  else{
   const invoices=make('ol');invoices.className='workspace-file-ledger';
   for(const row of ledger){
    const item=make('li'),title=make('div');title.className='workspace-file-invoice-heading';
    const client=make('strong',String(row.client_name||row.invoice_number||row.invoice_id||'Invoice'));
    const status=make('span',String(row.status||'Not recorded').replaceAll('_',' ').toLowerCase());status.className='workspace-file-invoice-status';
    title.append(client,status);
    const identity=make('p',[row.invoice_number||row.invoice_id,row.client_email].filter(Boolean).join(' · '));identity.className='workspace-file-invoice-identity';
    const amounts=make('dl');amounts.className='workspace-file-amounts';
    for(const [label,key] of [['Total','total'],['Paid','paid_amount'],['Balance','balance']]){
     const entry=make('div');entry.append(make('dt',label),make('dd',displayMoney(row[key],row.currency)));amounts.append(entry);
    }
    item.append(title,identity,amounts);invoices.append(item);
   }
   list.append(invoices);
  }
  const sourceText=source?.source_text||'No unfinished source text.';
  const fields=source?.review_fields;
  unfinished.hidden=!source?.source_text&&!fields;unfinished.open=false;
  intake.textContent=sourceText.slice(0,2000)+(sourceText.length>2000?'\n[Preview shortened; the full source will open.]':'');
  editable.replaceChildren();
  if(fields){
   editable.append(make('h4','Saved invoice corrections'));
   editable.append(make('p',[fields.client_name,fields.client_email].filter(Boolean).join(' · ')));
   const timing=fields.due_date!==undefined?'Payment due on '+(fields.due_date||'a date not yet set'):'Payment due in '+(fields.due_days||'unspecified')+' days';
   editable.append(make('p',timing+' · Reported prior payment: '+(fields.amount_paid||'0')));
   const lines=make('ul');
   for(const row of fields.line_items||[])lines.append(make('li',row.desc+' — '+row.qty+' '+(row.unit||'units')+' × '+row.currency+' '+row.unit_price));
   editable.append(lines,make('p','Unfinished fields only. Confirm and check them before creating an invoice.'));
  }
  warning.textContent='Your current workspace ('+value.current_invoice_count+' invoices) is kept so you can return.';
  apply.textContent=isPrevious?'Return to workspace':'Open workspace';
  panel.hidden=false;feedback.textContent='';sync();heading.focus();list.scrollTop=0;list.scrollLeft=0;
 }
 async function inspect(text?:string){
  if(!ready||busy||working)return;
  const ticket=++generation;working=true;sync();
  try{
   const decoded=text===undefined?null:decodeWorkspaceFile(text);
   const reply=await hooks.run(text===undefined?'workspace_previous_preview':'workspace_preview',text===undefined?{}:{text});
   if(ticket!==generation)return;
   if(!reply?.ok||!reply.result?.preview)throw new Error(reply?.message||'Could not preview this workspace.');
   render(reply.result.preview,reply.result.preview.intake??decoded?.intake,text===undefined);
  }catch(error){clear(String(error instanceof Error?error.message:error));}
  finally{working=false;sync();}
 }
 open.addEventListener('click',()=>{if(!open.disabled){file.value='';file.click();}});
 file.addEventListener('change',async()=>{
  const selected=file.files?.[0];if(!selected)return;
  if(selected.size>MAX_WORKSPACE_FILE_BYTES){clear('Choose a workspace file no larger than 24 MB.');return;}
  const ticket=++generation;
  try{const text=await selected.text();if(ticket===generation)await inspect(text);}catch{clear('Could not read that workspace file.');}
 });
 previous.addEventListener('click',()=>inspect());
 cancel.addEventListener('click',()=>{if(!working){clear('Current workspace kept.');open.focus();}});
 save.addEventListener('click',async()=>{
  if(save.disabled)return;working=true;sync();let url:string|undefined;
  try{
   const reply=await hooks.run('workspace_export');const result=reply?.result?.workspace_file;
   if(!reply?.ok||!result)throw new Error(reply?.message||'Could not save the complete workspace.');
   url=URL.createObjectURL(new Blob([result.text],{type:'application/json'}));
   const a=make('a');a.href=url;a.download=result.filename;document.body.append(a);a.click();a.remove();
   feedback.textContent='Complete workspace download started. Keep this file private; it contains client records.';
  }catch(error){feedback.textContent=String(error instanceof Error?error.message:error);}
  finally{if(url){const completed=url;setTimeout(()=>URL.revokeObjectURL(completed),1000);}working=false;sync();}
 });
 apply.addEventListener('click',async()=>{
  if(apply.disabled||!preview)return;working=true;sync();
  try{
   const reply=await hooks.run('workspace_restore',{preview_token:preview.token});
   if(!reply?.ok)throw new Error(reply?.message||'The workspace did not open. Preview it again before retrying.');
   await hooks.restoreIntake(reply.result?.restored_intake??null);
   clear(returning?'Previous workspace reopened. Pending decisions still need your approval.':'Saved workspace opened. Your prior workspace is available to return to.');
  }catch(error){clear(String(error instanceof Error?error.message:error));}
  finally{working=false;sync();}
 });
 sync();
 return{
  setAvailability(isReady:boolean,isBusy:boolean){ready=isReady;busy=isBusy;sync();},
  setMetadata(value:any){hasPrevious=Boolean(value?.hasPrevious);sync();},
  currentChanged(){if(preview)clear('Current workspace changed. Preview the file again before opening it.');else generation++;},
 };
}
