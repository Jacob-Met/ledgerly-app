import './billing-contact.css';

export type BillingContact = {key:string; name:string; email:string};
type Snapshot = {ledger?:Array<{client_name?:unknown;client_email?:unknown}>};
const escape = (value:string) => value.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));

/** An explicit choice of a saved name/email pair; never infer a current recipient. */
export function savedBillingContacts(snapshot:Snapshot|null):BillingContact[] {
 const contacts=new Map<string,BillingContact>();
 for(const row of Array.isArray(snapshot?.ledger)?snapshot!.ledger!:[]){
  if(typeof row.client_name!=='string'||typeof row.client_email!=='string')continue;
  const name=row.client_name.trim(),email=row.client_email.trim();
  if(!name||!email)continue;
  const key=JSON.stringify([name,email.toLocaleLowerCase()]);
  if(!contacts.has(key))contacts.set(key,{key,name,email});
 }
 return [...contacts.values()].sort((a,b)=>a.name.localeCompare(b.name)||a.email.localeCompare(b.email));
}

export function billingContactOptions(contacts:BillingContact[]):string {
 return '<option value="">Choose a saved contact…</option>'+contacts.map(contact=>
 '<option value="'+escape(contact.key)+'">'+escape(contact.name)+' · '+escape(contact.email)+'</option>').join('');
}

export function applyBillingContact(form:HTMLFormElement,contact:BillingContact):void {
 const name=form.elements.namedItem('client_name') as HTMLInputElement;
 const email=form.elements.namedItem('client_email') as HTMLInputElement;
 name.value=contact.name;email.value=contact.email;
 // Reuse the ordinary human-edit invalidation and intake-save path.
 email.dispatchEvent(new Event('input',{bubbles:true}));
}

export function createBillingContactChoice(container:HTMLElement,snapshot:()=>Snapshot|null,available:()=>boolean) {
 container.addEventListener('change',event=>{
  const select=event.target as HTMLSelectElement;
  if(select.id!=='review-saved-contact'||select.disabled||!available())return;
  const contact=savedBillingContacts(snapshot()).find(value=>value.key===select.value);
  const form=select.closest<HTMLFormElement>('#review-form');
  select.value='';
  if(contact&&form)applyBillingContact(form,contact);
 });
 return {refresh(){
  const fields=container.querySelector<HTMLElement>('.review-client-fields');
  if(!fields)return;
  const contacts=savedBillingContacts(snapshot());
  let label=fields.querySelector<HTMLElement>('.saved-billing-contact');
  if(!contacts.length){label?.remove();return;}
  if(!label){
   label=container.ownerDocument.createElement('label');label.className='saved-billing-contact';
   label.setAttribute('for','review-saved-contact');
   label.innerHTML='Use a saved billing contact<select id="review-saved-contact"></select>';
   fields.prepend(label);
  }
  const select=label.querySelector<HTMLSelectElement>('select')!;
  const options=billingContactOptions(contacts);
  if(select.dataset.options!==options){select.innerHTML=options;select.dataset.options=options;}
  select.disabled=!available();
 }};
}
