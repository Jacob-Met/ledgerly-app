type Activity = {event?:string;kind?:string;invoice?:string;action?:string;at?:string;ok?:boolean};
type ActivityState = {ledger?:any[];pending?:any[];completed_reviews?:any[];decisions?:any[]};
const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
export function invoiceActivityMarkup(rows: Activity[], state:ActivityState={}): string {
 const visible = rows.filter(row => row.event !== 'tool' || row.ok === false).reverse();
 if (!visible.length) return '<li class="empty">Invoice decisions will appear here.</li>';
 return visible.map(row => {
  const known=[...(state.pending||[]),...(state.completed_reviews||[]),...(state.decisions||[])].find(item=>item.id===row.action&&(!row.invoice||item.invoice_id===row.invoice));
  const invoiceId=row.invoice||known?.invoice_id,kind=row.kind||known?.kind;
  const action = kind === 'send_invoice' ? 'Send' : kind === 'update_invoice' ? 'Draft correction' : kind === 'cancel_invoice' ? 'Cancellation' : kind === 'send_reminder' ? 'Reminder' : 'Review';
  const pending=(state.pending||[]).find(item=>item.id===row.action&&item.invoice_id===invoiceId&&(!item.status||item.status==='PENDING'));
  const completed=[...(state.completed_reviews||[]),...(state.decisions||[])].find(item=>item.id===row.action&&item.invoice_id===invoiceId);
  const entry=(state.ledger||[]).find(item=>item.invoice_id===invoiceId);
  const labels: Record<string,string> = {
   queued: action + (pending?' awaiting approval':' review requested'),
   approved: action + ' approved',
   rejected: 'Review rejected',
   approve_failed: 'Action needs attention',
   tool: 'Invoice operation needs attention',
   send_review_reopened: 'Send review reopened',
   invoice_send_invalidated: 'Send review no longer current',
   reminder_invalidated: 'Reminder review needs another check',
   cancellation_invalidated: 'Cancellation review needs another check',
   webhook: 'Invoice update received',
   webhook_rejected: 'Invoice update rejected',
   webhook_unknown_invoice: 'Update has no matching invoice'
  };
  const label=labels[row.event || ''] || 'Invoice activity recorded';
  const detail=row.event==='invoice_send_invalidated' ? (typeof completed?.result?.reason==='string'?completed.result.reason:'The invoice changed after this send review was prepared.') : row.event==='queued' ? (pending?'No action has been taken.':completed?.status==='REJECTED'?'This review was rejected.':completed?.status==='APPROVED'?'This review was approved.':'') : row.event==='approve_failed' || (row.event==='tool' && row.ok===false) ? 'Check the recorded result before trying again.' : '';
  const identity=entry?'<p class="activity-invoice">'+escape([entry.invoice_number,entry.client_name,entry.client_email].filter(Boolean).join(' · '))+'</p>':invoiceId?'<p class="activity-invoice">'+escape(invoiceId)+'</p>':'';
  const links=(entry?'<button type="button" class="button button-link" data-activity-invoice="'+escape(invoiceId)+'">View invoice</button>':'')+(pending?'<button type="button" class="button button-link" data-activity-review="'+escape(pending.id)+'">Review '+escape(action.toLowerCase())+'</button>':'');
  return '<li class="invoice-activity-row"><div><strong>'+escape(label)+'</strong>'+identity+(detail?'<p>'+escape(detail)+'</p>':'')+(links?'<div class="activity-links">'+links+'</div>':'')+'</div><time datetime="'+escape(row.at)+'">'+escape((row.at||'').slice(0,10)+' '+(row.at||'').slice(11,16))+'</time></li>';
 }).join('');
}
