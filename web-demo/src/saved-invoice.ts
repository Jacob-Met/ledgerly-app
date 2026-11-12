const esc=(v:unknown)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));

/** Format the supplied decimal text; never convert, sum or round money. */
export function displayMoney(value:unknown,currency:unknown):string{
 const raw=String(value??'—'),ccy=String(currency??'');
 if(!/^[+-]?\d+(?:\.\d*)?$/.test(raw))return ccy+' '+raw;
 let digits=0;try{digits=new Intl.NumberFormat('en',{style:'currency',currency:ccy}).resolvedOptions().minimumFractionDigits??0;}catch{return ccy+' '+raw;}
 const [whole,fraction='']=raw.split('.');
 const grouped=whole.replace(/\B(?=(\d{3})+(?!\d))/g,',');
 const decimals=fraction.padEnd(digits,'0');
 return ccy+' '+grouped+(decimals?'.'+decimals:'');
}
export function savedInvoiceSummaryMarkup(entry:any):string{
 const labels:Record<string,string>={DRAFT:'Draft',SENT:'Sent',PARTIALLY_PAID:'Part paid',PAID:'Paid',UNPAID:'Unpaid',CANCELLED:'Cancelled'};
 const status=String(entry.status??''),balanceLabel=status==='CANCELLED'?'Cancelled amount':status==='DRAFT'?'Draft amount due':'Amount due';
 const simpleUnpaid=/^0+(?:\.0*)?$/.test(String(entry.paid_amount??''))&&displayMoney(entry.total,entry.currency)===displayMoney(entry.balance,entry.currency);
 return '<header class="saved-invoice-heading"><div><p class="saved-invoice-number">'+esc(entry.invoice_number)+'</p><h3>'+esc(entry.client_name||entry.client_email||'Client')+'</h3><p class="saved-invoice-recipient">'+esc(entry.client_email||'')+'</p></div><span class="status-chip '+esc(status.toLowerCase())+'">'+esc(labels[status]||status.replaceAll('_',' '))+'</span></header>'+
 '<div class="saved-invoice-financials"><div class="saved-invoice-balance"><span>'+balanceLabel+'</span><strong>'+esc(displayMoney(entry.balance,entry.currency))+'</strong></div>'+(simpleUnpaid?'':'<dl><div><dt>Invoice total</dt><dd>'+esc(displayMoney(entry.total,entry.currency))+'</dd></div><div><dt>Paid</dt><dd>'+esc(displayMoney(entry.paid_amount,entry.currency))+'</dd></div></dl>')+'</div>'+ 
 '<div class="saved-invoice-terms"><span>Due <strong>'+esc(entry.due_on||'Not recorded')+'</strong></span>'+(entry.reminders_sent?'<span>'+esc(entry.reminders_sent)+' reminders</span>':'')+'</div>';
}
/** Open the existing send decision for this draft; never prepare or approve one. */
export function savedInvoiceSendReviewMarkup(entry:any,actions:any[]):string{
 if(entry.status!=='DRAFT')return '';
 const pending=actions.filter(action=>action.invoice_id===entry.invoice_id&&action.kind==='send_invoice'&&(!action.status||action.status==='PENDING'));
 if(pending.length!==1||!pending[0].id)return '';
 return '<div class="saved-invoice-actions"><button type="button" class="button" data-review-action="'+esc(pending[0].id)+'" data-review-kind="send">Review send</button></div>';
}
/** Keep the existing invoice records and action handlers; select only their view. */
export function createSavedInvoiceSelection(list:HTMLElement){
 const workspace=document.createElement('div');workspace.className='saved-invoice-workspace';list.before(workspace);
 const index=document.createElement('details');index.className='saved-invoice-index';
 const compact=matchMedia('(max-width:760px)');index.open=!compact.matches;compact.addEventListener('change',event=>{index.open=!event.matches;});
 const summary=document.createElement('summary');const choices=document.createElement('nav');choices.setAttribute('aria-label','Saved invoices');const search=document.createElement('label');search.className='saved-invoice-search';search.textContent='Find an invoice';
 const input=document.createElement('input');input.type='search';input.placeholder='Client, email or invoice number';input.setAttribute('aria-label','Find a saved invoice');
 search.append(input);
 const count=document.createElement('p');count.className='saved-invoice-search-result';count.setAttribute('role','status');
 index.append(summary,search,count,choices);workspace.append(index,list);
 let selected:string|undefined;try{selected=sessionStorage.getItem('ledgerly.selected-invoice')||undefined;}catch{}
 let rows:any[]=[];
 const select=(id:string)=>{
  if(!rows.some(row=>row.invoice_id===id))return false;
  selected=id;try{sessionStorage.setItem('ledgerly.selected-invoice',id);}catch{}
  for(const card of Array.from(list.querySelectorAll<HTMLElement>('[data-saved-invoice-id]')))card.hidden=card.dataset.savedInvoiceId!==id;
  for(const button of Array.from(choices.querySelectorAll<HTMLButtonElement>('button')))button.setAttribute('aria-pressed',String(button.dataset.savedInvoice===id));
  return true;
 };
 const renderChoices=()=>{
  const query=input.value.trim().normalize('NFKC').toLocaleLowerCase();
  const matching=rows.filter(row=>[row.client_name,row.client_email,row.invoice_number].some(value=>String(value||'').normalize('NFKC').toLocaleLowerCase().includes(query)));
  count.textContent=query?(matching.length?'Showing '+matching.length+' of '+rows.length:'No matching invoices. Try another name, email or number.'):'';
  count.hidden=!query;
  choices.innerHTML=matching.map(row=>'<button type="button" data-saved-invoice="'+esc(row.invoice_id)+'" aria-pressed="'+String(row.invoice_id===selected)+'"><span>'+esc(row.invoice_number)+' · '+esc(String(row.status||'').replaceAll('_',' ').toLowerCase())+'</span><strong>'+esc(row.client_name||row.client_email||'Client')+'</strong><small>'+esc(row.client_email||'')+'</small><b>'+esc(displayMoney(row.balance,row.currency))+'</b></button>').join('');
 };
 input.addEventListener('input',renderChoices);
 choices.addEventListener('click',event=>{
  const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button[data-saved-invoice]');if(!button?.dataset.savedInvoice)return;
  select(button.dataset.savedInvoice);
  if(matchMedia('(max-width:760px)').matches){index.open=false;summary.focus();}
 });
 return {select,refresh(entries:any[]){
  rows=entries;index.hidden=!rows.length;summary.textContent=`${rows.length} ${rows.length===1?'invoice':'invoices'}`;

  if(!selected||!rows.some(row=>row.invoice_id===selected))selected=rows[0]?.invoice_id;
  renderChoices();
  if(selected)select(selected);else try{sessionStorage.removeItem('ledgerly.selected-invoice');}catch{}
 }};
}
