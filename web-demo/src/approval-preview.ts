/** Read-only presentation of the concrete action returned by the Python queue. */
export interface PendingApproval {
  id: string;
  kind: string;
  invoice_id: string;
  summary: string;
  payload: unknown;
}

type Fields = Record<string, unknown>;

const escape = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g,
  character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]!));

function fields(value: unknown): Fields {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Fields : {};
}

function text(value: unknown, missing = 'Not provided'): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : missing;
}

function row(label: string, value: unknown): string {
  return '<div><dt>' + escape(label) + '</dt><dd>' + escape(text(value)) + '</dd></div>';
}

function person(value: unknown): string {
  const person = fields(value);
  const name = fields(person.name);
  const fullName = [text(name.given_name, ''), text(name.surname, '')].filter(Boolean).join(' ');
  const email = text(person.email_address, '');
  return [fullName, email].filter(Boolean).join(' · ') || 'Not provided';
}

function paymentTerms(value: unknown): string {
  const term = fields(value);
  const kind = text(term.term_type, '');
  const names: Record<string, string> = {
    DUE_ON_RECEIPT: 'Due on receipt',
    DUE_ON_DATE_SPECIFIED: 'Due on the stated date',
    NO_DUE_DATE: 'No due date',
  };
  const net = /^NET_([0-9]+)$/.exec(kind);
  const label = names[kind] ?? (net ? 'Net ' + net[1] + ' days' : kind || 'Not provided');
  return row('Payment terms', label) + (typeof term.due_date === 'string' ? row('Due date', term.due_date) : '');
}

function invoicePreview(payload: Fields): string {
  const invoice = fields(payload.invoice);
  const detail = fields(invoice.detail);
  const recipients = Array.isArray(invoice.primary_recipients) ? invoice.primary_recipients : [];
  const items = Array.isArray(invoice.items) ? invoice.items : [];
  const currency = text(detail.currency_code, '');
  const recipientText = recipients.map(recipient => person(fields(recipient).billing_info)).join('\n') || 'Not provided';
  const itemRows = items.map(item => {
    const line = fields(item);
    const price = fields(line.unit_amount);
    const unit = line.unit_of_measure === 'HOURS' ? 'hr' : line.unit_of_measure === 'QUANTITY' ? '' : text(line.unit_of_measure, '');
    return '<tr><td>' + escape(text(line.name)) + '</td><td>' + escape(text(line.quantity)) +
      (unit ? ' ' + escape(unit) : '') + '</td><td>' + escape(text(price.currency_code, currency)) +
      ' ' + escape(text(price.value)) + '</td></tr>';
  }).join('');
  const note = typeof detail.note === 'string'
    ? '<div class="approval-message-label">Invoice note</div><div class="approval-message" data-preview-invoice-note>' + escape(detail.note) + '</div>'
    : '';
  const priorPayment = payload.prepaid !== undefined && !/^0(?:\.0+)?$/.test(String(payload.prepaid))
    ? row('Prior payment reported', (currency ? currency + ' ' : '') + text(payload.prepaid))
    : '';
  return '<details class="approval-preview" open><summary>Review queued invoice</summary>' +
    '<div class="approval-preview-content"><dl class="approval-fields">' +
    row('Recipient', recipientText) + paymentTerms(detail.payment_term) + priorPayment + '</dl>' +
    '<div class="approval-items"><table><caption>Queued invoice items</caption><thead><tr><th scope="col">Item</th>' +
    '<th scope="col">Quantity</th><th scope="col">Unit price</th></tr></thead><tbody>' +
    (itemRows || '<tr><td colspan="3">No item details in this queued action.</td></tr>') +
    '</tbody></table></div>' + note +
    '<details class="approval-document-details"><summary>Invoice details</summary><dl class="approval-fields">' +
    row('Invoice', detail.invoice_number) + row('From', person(invoice.invoicer)) +
    row('Invoice date', detail.invoice_date) + '</dl></details></div></details>';
}

function correctionChanges(payload:Fields):string{
 let original:Fields={};
 try{original=fields(JSON.parse(String(payload.original)));}catch{return '';}
 const proposed=fields(payload.invoice);
 const changes:Array<[string,string,string]>=[];
 const add=(label:string,before:string,after:string)=>{if(before!==after)changes.push([label,before,after]);};
 const recipient=(invoice:Fields)=>(Array.isArray(invoice.primary_recipients)?invoice.primary_recipients:[]).map(r=>person(fields(r).billing_info)).join('; ');
 const terms=(invoice:Fields)=>{const term=fields(fields(invoice.detail).payment_term);const kind=text(term.term_type,'');const net=/^NET_([0-9]+)$/.exec(kind);return (net?'Net '+net[1]+' days':kind==='DUE_ON_RECEIPT'?'Due on receipt':kind==='NO_DUE_DATE'?'No due date':'Due on date')+(typeof term.due_date==='string'?' · '+term.due_date:'');};
 add('Bill to',recipient(original),recipient(proposed));
 add('Payment terms',terms(original),terms(proposed));
 add('Invoice note',text(fields(original.detail).note,'')||'No note',text(fields(proposed.detail).note,'')||'No note');
 const work=(invoice:Fields)=>(Array.isArray(invoice.items)?invoice.items:[]).map(item=>{const line=fields(item),amount=fields(line.unit_amount);return text(line.name)+' — '+text(line.quantity)+' '+(line.unit_of_measure==='HOURS'?'hours':line.unit_of_measure==='QUANTITY'?'units':text(line.unit_of_measure,'units'))+' × '+text(amount.currency_code)+' '+text(amount.value);});
 const before=work(original),after=work(proposed);
 if(JSON.stringify(before)!==JSON.stringify(after))changes.push(['Work',before.join('\n'),after.join('\n')]);
 return changes.length?'<section class="correction-changes"><h3>Changes to approve</h3>'+changes.map(([label,before,after])=>'<div class="correction-change"><h4>'+escape(label)+'</h4><dl><div><dt>Current</dt><dd>'+escape(before)+'</dd></div><div><dt>After correction</dt><dd>'+escape(after)+'</dd></div></dl></div>').join('')+'</section>':'';
}

function reminderPreview(payload: Fields): string {
  return '<section class="approval-preview approval-reminder" aria-label="Queued reminder">' +
    '<h3>Review queued reminder</h3><dl class="approval-fields">' +
    row('Subject', payload.subject) + '</dl><div class="approval-message-label">Message</div>' +
    '<div class="approval-message" data-preview-reminder-note>' + escape(text(payload.note)) + '</div></section>';
}

export function approvalCardMarkup(action: PendingApproval): string {
  const payload = fields(action.payload);
  const invoice = action.kind === 'send_invoice';
  const reminder = action.kind === 'send_reminder';
  const cancellation = action.kind === 'cancel_invoice';
  const correction = action.kind === 'update_invoice';
  const preview = correction ? '<section class="approval-preview"><h3>Correct the same unsent invoice</h3><p>Previous total: '+escape(text(payload.previous_total))+' · Corrected total: '+escape(text(payload.total))+'</p>'+correctionChanges(payload)+'<p>No notification will be sent. The corrected draft will need a new, separate send approval.</p></section>'+invoicePreview(payload) : cancellation ? '<section class="approval-preview"><h3>Cancel this unpaid invoice</h3><p>' + escape(text(payload.reason)) + '</p><p>The invoice will stop being payable and leave Collections. No refund or replacement is created. No cancellation email will be sent; notify the client separately if needed.</p></section>' : invoice ? invoicePreview(payload) : reminder ? reminderPreview(payload) : '';
  const navigation = '<div class="approval-navigation"><button type="button" class="button button-link" data-review-invoice="' + escape(action.invoice_id) + '">View this invoice</button><a class="approval-return" href="#receivables-view">Back to collections</a></div>';
  return '<article class="approval-card" data-approval-id="' + escape(action.id) + '">' +
    '<div class="approval-head"><span class="source-chip">' +
    (correction ? 'CORRECT DRAFT' : cancellation ? 'CANCEL INVOICE' : invoice ? 'SEND INVOICE' : reminder ? 'SEND REMINDER' : 'PENDING ACTION') +
    '</span><span class="mono">' + escape(text(fields(fields(payload.invoice).detail).invoice_number, action.invoice_id)) + '</span></div><p class="approval-summary">' +
    escape(action.summary) + '</p>' + preview + '<div class="button-row">' +
    '<button class="button button-approve" data-approve="' + escape(action.id) + '">' + (correction?'Apply correction':cancellation?'Cancel invoice':invoice?'Send invoice':reminder?'Send reminder':'Approve action') + '</button>' +
    '<button class="button button-reject" data-reject="' + escape(action.id) + '">Reject</button></div>' + navigation + '</article>';
}

export function approvalListMarkup(queue: readonly PendingApproval[]): string {
  return queue.length ? queue.map(approvalCardMarkup).join('') :
    '<p class="empty">No approvals waiting. Sends and reminders stay queued until you choose.</p>';
}
