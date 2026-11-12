import './draft-correction.css';
import {correctionDraftStore} from './correction-draft';
import {nativeConfig} from './native-client';
const esc=(value:unknown)=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
function unitOptions(unit:unknown):string{
 const current=String(unit??'');
 const choices=[['QUANTITY','Quantity'],['HOURS','Hours']];
 if(!choices.some(([value])=>value===current))choices.unshift([current,current||'Not recorded']);
 return choices.map(([value,label])=>'<option value="'+esc(value)+'"'+(value===current?' selected':'')+'>'+esc(label)+'</option>').join('');
}
function rowMarkup(item:any,index:number|null):string{
 return '<fieldset data-correction-item data-source-index="'+(index===null?'':index)+'"><legend>'+(index===null?'Added item':'Item '+(index+1))+'</legend><label>Work description<input name="name" value="'+esc(item.name)+'" required maxlength="200"></label><div class="correction-values"><label>Quantity<input name="quantity" inputmode="decimal" value="'+esc(item.quantity)+'" required></label><label>Unit price<input name="unit_price" inputmode="decimal" value="'+esc(item.unit_price)+'" required></label><label>Unit<select name="unit">'+unitOptions(item.unit)+'</select></label></div><button class="button button-link" type="button" data-remove-correction-item>Remove item</button></fieldset>';
}
function termsMarkup(term:any):string{
 const type=String(term?.term_type||'NO_DUE_DATE');
 const kind=type==='DUE_ON_RECEIPT'?'receipt':type==='NO_DUE_DATE'?'none':type.startsWith('NET_')?'net':'date';
 const value=kind==='net'?type.slice(4):kind==='date'?String(term?.due_date||''):'';
 return '<fieldset><legend>Payment terms</legend><label>Due<select name="terms-kind">'+[['net','Net days'],['date','On a date'],['receipt','On receipt'],['none','No due date']].map(([v,l])=>'<option value="'+v+'"'+(v===kind?' selected':'')+'>'+l+'</option>').join('')+'</select></label><label data-terms-value-label'+(['none','receipt'].includes(kind)?' hidden':'')+'><span data-terms-label>'+(kind==='date'?'Due date':'Days from invoice date')+'</span><input name="terms-value" type="'+(kind==='date'?'date':'number')+'" value="'+esc(value)+'" min="1" max="365"></label></fieldset>';
}
type Run=(action:string,payload:Record<string,unknown>)=>Promise<any>;
export function createDraftCorrections(mount:HTMLElement,run:Run,openReview:(id:string)=>boolean){
 const dialog=document.createElement('dialog');dialog.className='draft-correction';
 dialog.setAttribute('aria-label','Correct an unsent invoice');document.body.append(dialog);
 let viewed:any=null;
 const scope=nativeConfig()?.token||'browser';
 const store=()=>correctionDraftStore(sessionStorage,scope);
 function remember(){
  if(!viewed||!dialog.open)return;
  const value=(name:string)=>dialog.querySelector<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>('[name="'+name+'"]')!.value;
  try{store().save({invoice_id:viewed.invoice_id,revision:viewed.revision,recipient_name:value('recipient-name'),recipient_email:value('recipient-email'),terms_kind:value('terms-kind'),terms_value:value('terms-value'),note:value('invoice-note'),
   items:Array.from(dialog.querySelectorAll<HTMLElement>('[data-correction-item]')).map(row=>{const read=(name:string)=>row.querySelector<HTMLInputElement|HTMLSelectElement>('[name="'+name+'"]')!.value;return {name:read('name'),quantity:read('quantity'),unit_price:read('unit_price'),unit:read('unit'),source_index:row.dataset.sourceIndex===''?null:Number(row.dataset.sourceIndex)};})});
  }catch{dialog.querySelector<HTMLElement>('.correction-error')!.textContent='These unfinished changes could not be kept in this tab. Keep this form open until you review them.';}
 }
 function forget(){if(viewed)try{store().remove(viewed.invoice_id);}catch{/* Reopening still checks the original revision. */}}

 mount.addEventListener('click',async event=>{
  const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button[data-correct-draft]');
  if(!button)return;
  button.disabled=true;
  const reply=await run('inspect_draft_correction',{invoice_id:button.dataset.correctDraft});
  if(!reply?.ok){button.disabled=false;return;}
  viewed=reply.result;
  dialog.innerHTML='<form><header><p class="correction-reference">Unsent invoice '+esc(viewed.invoice_number)+'</p><h2>Correct this draft</h2><p>Current total: <strong>'+esc(viewed.currency)+' '+esc(viewed.total)+'</strong></p></header>'+
   '<div class="correction-fields"><fieldset><legend>Bill to</legend><label>Name<input name="recipient-name" value="'+esc(viewed.client_name)+'" required maxlength="200"></label><label>Email<input name="recipient-email" type="email" value="'+esc(viewed.client_email)+'" required maxlength="254"></label></fieldset>'+
   termsMarkup(viewed.payment_term)+'<fieldset><legend>Invoice note</legend><label>Client reference or instructions<textarea name="invoice-note" rows="3" maxlength="4000" placeholder="Purchase order, project reference or client instructions">'+esc(viewed.note||'')+'</textarea></label></fieldset><div data-correction-rows>'+viewed.items.map((item:any,index:number)=>rowMarkup(item,index)).join('')+'</div>'+
   '<button class="button button-link" type="button" data-add-correction-item>Add item</button>'+
   '</div><footer class="correction-actions"><label class="correction-confirm"><input type="checkbox" required> I checked these corrections against the agreed work.</label><p class="correction-error" role="status"></p><div class="button-row"><button class="button" type="submit">Review correction</button><button class="button button-link" type="button" data-close-correction>Keep current draft</button></div></footer></form>';
  let restored=false,stale=false;
  try{
   const prior=store().read(viewed.invoice_id),saved=store().resume(viewed.invoice_id,viewed.revision);stale=Boolean(prior&&!saved);
   if(saved){
    const values:Record<string,string>={'recipient-name':saved.recipient_name,'recipient-email':saved.recipient_email,'terms-kind':saved.terms_kind,'terms-value':saved.terms_value,'invoice-note':saved.note};
    const terms=dialog.querySelector<HTMLSelectElement>('[name=terms-kind]')!;terms.value=saved.terms_kind;terms.dispatchEvent(new Event('change',{bubbles:true}));
    for(const [name,value] of Object.entries(values))dialog.querySelector<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>('[name="'+name+'"]')!.value=value;
    dialog.querySelector('[data-correction-rows]')!.innerHTML=saved.items.map(row=>rowMarkup(row,row.source_index)).join('');
    restored=true;
   }
  }catch{dialog.querySelector<HTMLElement>('.correction-error')!.textContent='Unfinished changes could not be reopened. The current invoice is shown.';}
  if(restored||stale){const notice=document.createElement('p');notice.className='correction-resumed';notice.textContent=restored?'Unfinished changes restored. Check them before review.':'This invoice changed. Earlier unfinished changes were not restored.';dialog.querySelector('header')!.append(notice);}
  dialog.showModal();
 });
 dialog.addEventListener('click',event=>{
  const target=event.target as HTMLElement;
  if(target.closest('[data-close-correction]')){forget();dialog.close();return;}
  const rows=dialog.querySelector<HTMLElement>('[data-correction-rows]');
  if(!rows)return;
  if(target.closest('[data-add-correction-item]')){
   rows.insertAdjacentHTML('beforeend',rowMarkup({name:'',quantity:'1',unit_price:'',unit:'QUANTITY'},null));
   rows.lastElementChild?.querySelector<HTMLInputElement>('input')?.focus();
  }else if(target.closest('[data-remove-correction-item]')){
   if(rows.children.length<=1){dialog.querySelector<HTMLElement>('.correction-error')!.textContent='Keep at least one item on this invoice.';return;}
   const row=target.closest<HTMLElement>('[data-correction-item]')!;
   const next=row.nextElementSibling||row.previousElementSibling;row.remove();
   next?.querySelector<HTMLInputElement>('input')?.focus();
  }else return;
  dialog.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked=false;
  dialog.querySelector<HTMLElement>('.correction-error')!.textContent='';remember();
 });

 dialog.addEventListener('change',event=>{
  if((event.target as HTMLSelectElement).name!=='terms-kind')return;
  const kind=(event.target as HTMLSelectElement).value;
  const label=dialog.querySelector<HTMLElement>('[data-terms-value-label]')!;
  const input=dialog.querySelector<HTMLInputElement>('[name=terms-value]')!;
  label.hidden=['receipt','none'].includes(kind);input.type=kind==='date'?'date':'number';input.value='';
  if(kind==='date'){input.removeAttribute('min');input.removeAttribute('max');}else{input.min='1';input.max='365';}
  dialog.querySelector('[data-terms-label]')!.textContent=kind==='date'?'Due date':'Days from invoice date';
  dialog.querySelector<HTMLInputElement>('input[type=checkbox]')!.checked=false;
 });
 dialog.addEventListener('input',event=>{if((event.target as HTMLInputElement).type!=='checkbox'){const confirm=dialog.querySelector<HTMLInputElement>('input[type="checkbox"]');if(confirm)confirm.checked=false;}});
 dialog.addEventListener('input',remember);
 dialog.addEventListener('change',remember);
 dialog.addEventListener('submit',async event=>{
  event.preventDefault();
  const form=dialog.querySelector('form')!;
  if(!form.reportValidity()||!viewed)return;
  const items=Array.from(form.querySelectorAll<HTMLElement>('[data-correction-item]')).map(row=>{
   const value=(name:string)=>row.querySelector<HTMLInputElement>('input[name="'+name+'"]')!.value;
   const unit=row.querySelector<HTMLSelectElement>('select[name="unit"]')!.value;
   return {name:value('name'),quantity:value('quantity'),unit_price:value('unit_price'),unit:unit||null,source_index:row.dataset.sourceIndex===''?null:Number(row.dataset.sourceIndex)};
  });
  const submit=form.querySelector<HTMLButtonElement>('button[type="submit"]')!;submit.disabled=true;
  const reply=await run('prepare_draft_correction',{invoice_id:viewed.invoice_id,revision:viewed.revision,items,note:form.querySelector<HTMLTextAreaElement>('[name=invoice-note]')!.value,terms:{kind:form.querySelector<HTMLSelectElement>('[name=terms-kind]')!.value,value:form.querySelector<HTMLInputElement>('[name=terms-value]')!.value},recipient:{name:form.querySelector<HTMLInputElement>('[name=recipient-name]')!.value,email:form.querySelector<HTMLInputElement>('[name=recipient-email]')!.value}});
  if(reply?.ok){
   forget();dialog.close();
   openReview(reply.result.approval_id);
  }else{
   form.querySelector<HTMLElement>('.correction-error')!.textContent=reply?.message||'The correction could not be prepared. Your current invoice was not replaced.';
   submit.disabled=false;
  }
 });
 return {markup:(entry:any,state:any)=>{
  let unfinished=false;try{unfinished=Boolean(store().read(entry.invoice_id));}catch{}
  const attempted=(state.completed_reviews||[]).some((a:any)=>a.invoice_id===entry.invoice_id&&['FAILED','INTERRUPTED'].includes(a.status));
  const updating=(state.pending||[]).some((a:any)=>a.invoice_id===entry.invoice_id&&a.kind!=='send_invoice');
  return entry.status==='DRAFT'&&Number(entry.paid_amount)===0&&!attempted&&!updating
   ? '<button class="button" data-correct-draft="'+esc(entry.invoice_id)+'">'+(unfinished?'Resume correction':'Correct unsent draft')+'</button>' : '';
 }};
}
