import './saved-request.css';
const esc=(value:string)=>value.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
type Run=(action:string,payload:Record<string,unknown>)=>Promise<any>;

export function savedRequestMarkup(invoiceId:string,state:any):string {
 if(!Array.isArray(state?.invoice_requests)||!state.invoice_requests.includes(invoiceId))return '';
 return '<button type="button" class="saved-request-open" data-saved-request="'+esc(invoiceId)+'">Original client request</button>';
}

/** Read the selected creation's private source; never replace the active intake. */
export function createSavedRequestView(container:HTMLElement,run:Run){
 const dialog=document.createElement('dialog');dialog.className='saved-request-dialog';
 dialog.setAttribute('aria-labelledby','saved-request-title');
 const heading=document.createElement('h2');heading.id='saved-request-title';heading.textContent='Original client request';
 const identity=document.createElement('p');identity.className='saved-request-identity';
 const source=document.createElement('pre');source.className='saved-request-text';
 const close=document.createElement('button');close.type='button';close.className='button';close.textContent='Close';close.setAttribute('aria-label','Close request');
 const header=document.createElement('header');header.className='saved-request-header';header.append(heading,close);
 dialog.append(header,identity,source);document.body.append(dialog);
 let selected:string|undefined;
 close.addEventListener('click',()=>dialog.close());
 dialog.addEventListener('close',()=>{
  const button=Array.from(container.querySelectorAll<HTMLButtonElement>('[data-saved-request]')).find(value=>value.dataset.savedRequest===selected);
  button?.focus();selected=undefined;
 });
 container.addEventListener('click',async event=>{
  const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button[data-saved-request]');
  if(!button||button.disabled||!button.dataset.savedRequest)return;
  const invoiceId=button.dataset.savedRequest;selected=invoiceId;
  identity.textContent='Opening saved request…';source.textContent='';dialog.showModal();close.focus();
  try{
   const reply=await run('invoice_request',{invoice_id:invoiceId});
   if(!dialog.open||selected!==invoiceId)return;
   if(!reply?.ok||reply.result?.invoice_id!==invoiceId||typeof reply.result?.source_text!=='string'){
    identity.textContent=reply?.message||'This saved request could not be opened. Close and try again.';return;
   }
   identity.textContent=[reply.result.invoice_number,reply.result.client_name].filter(Boolean).join(' · ');
   source.textContent=reply.result.source_text;
  }catch{
   if(dialog.open&&selected===invoiceId)identity.textContent='This saved request could not be opened. Close and try again.';
  }
 });
 return {markup:savedRequestMarkup};
}
