/** Explicit standalone copies of the accepted, displayed sandbox invoice. */
import {invoiceDetailsMarkup, type InvoiceDetailSnapshot} from './invoice-details';
import invoiceDetailStyles from './invoice-details.css?raw';
import {displayMoney} from './saved-invoice';
// File imports keep the font bytes inside a disconnected invoice download.
import sans400 from '../public/fonts/ibm-plex-sans-latin-400-normal.woff2?inline';
import sans500 from '../public/fonts/ibm-plex-sans-latin-500-normal.woff2?inline';
import sans600 from '../public/fonts/ibm-plex-sans-latin-600-normal.woff2?inline';
import mono400 from '../public/fonts/ibm-plex-mono-latin-400-normal.woff2?inline';
import mono500 from '../public/fonts/ibm-plex-mono-latin-500-normal.woff2?inline';
import mono600 from '../public/fonts/ibm-plex-mono-latin-600-normal.woff2?inline';
import sansLicense from '/fonts/IBM-Plex-sans-LICENSE.txt?raw';
import monoLicense from '/fonts/IBM-Plex-mono-LICENSE.txt?raw';


type Fields = Record<string, unknown>;
export interface InvoiceRecordSnapshot extends InvoiceDetailSnapshot {
  ledger?: unknown; native_operator?: unknown; provider?: unknown;
  completed_reviews?: unknown; decisions?: unknown; decision_history_available?: unknown;
}
type Announce = (message: string, kind?: string) => void;
const MAX_RECORD_BYTES = 4 * 1024 * 1024;
const escape = (value: string): string => value.replace(/[&<>"']/g,
  character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]!));
// Hex-escape every code point: document text cannot close its CSS string or style element.
function cssQuotedText(value: string): string {
  return '"' + Array.from(value, character => '\\' + character.codePointAt(0)!.toString(16) + ' ').join('') + '"';
}
const fields = (value: unknown): Fields => value !== null && typeof value === 'object' && !Array.isArray(value)
  ? value as Fields : {};
const text = (value: unknown): string => typeof value === 'string' ? value || '(empty)'
  : typeof value === 'number' && Number.isFinite(value) ? String(value) : 'Not recorded';
function filenamePart(value: string): string {
  const clean = value.normalize('NFKC').replace(/[\p{C}<>:"\/\\|?*]+/gu, '-')
    .replace(/\s+/gu, '-').replace(/^[. -]+|[. -]+$/g, '');
  const encoder = new TextEncoder();
  let result = '', bytes = 0;
  for (const character of clean) {
    const size = encoder.encode(character).byteLength;
    if (bytes + size > 80) break;
    result += character; bytes += size;
  }
  return result.replace(/[. -]+$/g, '') || 'record';
}
const summaryFields = [
  ['invoice_id', 'Invoice reference'], ['invoice_number', 'Invoice number'],
  ['client_name', 'Client'], ['client_email', 'Recipient email'], ['currency', 'Currency'],
  ['total', 'Invoice total'], ['paid_amount', 'Paid amount'], ['balance', 'Balance'],
  ['status', 'Agent ledger status'], ['sent_on', 'Sent on'], ['due_on', 'Due on'],
  ['reminders_sent', 'Reminders sent'],
] as const;

/** Select one identity; neither the latest analysis nor another invoice is a fallback. */
function selectedSnapshot(snapshot: InvoiceRecordSnapshot, invoiceId: string): InvoiceRecordSnapshot {
  if (typeof invoiceId !== 'string' || !invoiceId || typeof snapshot?.today !== 'string' || !snapshot.today)
    throw new Error('A dated sandbox snapshot and invoice reference are required.');
  const ledger = Array.isArray(snapshot.ledger) ? snapshot.ledger : [];
  const details = Array.isArray(snapshot.invoice_details) ? snapshot.invoice_details : [];
  const entries = ledger.filter(value => fields(value).invoice_id === invoiceId);
  const records = details.filter(value => fields(value).invoice_id === invoiceId);
  if (entries.length !== 1 || records.length !== 1 || fields(fields(records[0]).record).id !== invoiceId)
    throw new Error('One matching retained sandbox invoice record is required.');
  const history = snapshot.native_operator === true ? snapshot.decisions : snapshot.completed_reviews;
  const completed = (Array.isArray(history) ? history : []).map(fields)
    .filter(action => action.invoice_id === invoiceId &&
      ['APPROVED', 'REJECTED', 'FAILED', 'INTERRUPTED'].includes(String(action.status)))
    .map(action => ({id: action.id, invoice_id: invoiceId, kind: action.kind, status: action.status,
      created_at: action.created_at, summary: action.summary,
      result: {reason: fields(action.result).reason, outcome: fields(action.result).outcome}}));
  // Retain only this invoice and completed decision metadata. Proposal bodies,
  // other invoices, analysis and the pasted source never enter the document.
  return structuredClone({today: snapshot.today, ledger: [entries[0]], invoice_details: [records[0]],
    native_operator: snapshot.native_operator === true,
    completed_reviews: completed, decisions: completed,
    decision_history_available: typeof snapshot.decision_history_available === 'boolean' ? snapshot.decision_history_available : Array.isArray(history),
    provider: typeof snapshot.provider === 'string' ? snapshot.provider : undefined});
}

function decisionHistoryMarkup(snapshot: InvoiceRecordSnapshot): string {
  const history = Array.isArray(snapshot.completed_reviews) ? snapshot.completed_reviews : [];
  const rows = history.map(value => {
    const action = fields(value);
    const names: Record<string, string> = {send_invoice: 'Send invoice', send_reminder: 'Send reminder', cancel_invoice: 'Cancel invoice', update_invoice: 'Correct invoice'};
    const result = fields(action.result);
    return '<li class="record-decision"><h3>' + escape(names[String(action.kind)] || text(action.kind)) +
      ' · ' + escape(text(action.status)) + '</h3><p>' + escape(text(action.summary)) + '</p>' +
      '<dl><dt>Decision reference</dt><dd>' + escape(text(action.id)) +
      '</dd><dt>Review opened (UTC)</dt><dd>' + escape(text(action.created_at)) + '</dd></dl>' +
      (typeof result.reason === 'string' && result.reason ? '<p><strong>Recorded reason:</strong> ' + escape(result.reason) + '</p>' : '') +
      (typeof result.outcome === 'string' ? '<p><strong>Recorded outcome:</strong> ' + escape(result.outcome) + '</p>' : '') + '</li>';
  }).join('');
  return '<section aria-labelledby="record-decisions-heading"><h2 id="record-decisions-heading">Completed invoice decisions</h2>' +
    '<p class="record-metadata">These are retained review outcomes for this invoice, not a new provider confirmation. Review-opened times are not completion times. Pending proposals are excluded.</p>' +
    (rows ? '<ol class="record-decisions">' + rows + '</ol>' :
      '<p>' + (snapshot.decision_history_available ? 'No completed decisions were recorded at this snapshot.' :
        'Decision history was not included in this snapshot.') + '</p>') + '</section>';
}

const embeddedFontStyles = `
/* ${sansLicense} */
/* ${monoLicense} */
@font-face{font-family:"IBM Plex Sans";src:url("${sans400}") format("woff2");font-style:normal;font-weight:400;font-display:swap}
@font-face{font-family:"IBM Plex Sans";src:url("${sans500}") format("woff2");font-style:normal;font-weight:500;font-display:swap}
@font-face{font-family:"IBM Plex Sans";src:url("${sans600}") format("woff2");font-style:normal;font-weight:600 800;font-display:swap}
@font-face{font-family:"IBM Plex Mono";src:url("${mono400}") format("woff2");font-style:normal;font-weight:400;font-display:swap}
@font-face{font-family:"IBM Plex Mono";src:url("${mono500}") format("woff2");font-style:normal;font-weight:500;font-display:swap}
@font-face{font-family:"IBM Plex Mono";src:url("${mono600}") format("woff2");font-style:normal;font-weight:600 800;font-display:swap}
`;

const pageStyles = `
:root {
  color-scheme: light;
  --paper:#edf2fa; --ink:#182745; --muted:#526482; --line:#d3ddec;
  --brand:#173fa8; --brand-deep:#123187; --soft:#f5f8fd;
  --sans:"IBM Plex Sans",Arial,sans-serif; --mono:"IBM Plex Mono",monospace;
}
* { box-sizing:border-box; }
body {
  margin:0; padding:28px 24px 64px; background:var(--paper); color:var(--ink);
  font:400 14px/1.6 var(--sans);
}
main {
  max-width:960px; min-width:0; margin:auto; padding:42px 52px 30px;
  background:#fff; border:1px solid var(--line); box-shadow:0 10px 34px #16346c0a;
}
.record-masthead { display:flex; justify-content:space-between; align-items:center; gap:20px; }
.record-brand { display:flex; align-items:center; gap:10px; color:var(--brand); font:600 22px/1.2 var(--sans); letter-spacing:-.65px; }
.record-brand svg { width:27px; height:27px; flex-shrink:0; }
.record-stamp { color:var(--muted); font:400 11px/1.5 var(--sans); padding:4px 8px; background:var(--soft); white-space:nowrap; }
.record-label { margin:34px 0 8px; color:var(--muted); font:400 10px/1.5 var(--mono); letter-spacing:.055em; }
h1 { margin:0 0 30px; color:var(--brand-deep); font:500 43px/1.15 var(--sans); letter-spacing:-1.4px; overflow-wrap:anywhere; }
h2 { margin:28px 0 12px; font:500 21px/1.35 var(--sans); }
.record-billing { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:28px; padding:28px 0; border-top:2px solid var(--ink); border-bottom:1px solid var(--line); }
.record-billing>div { min-width:0; }
.record-billing span { color:var(--muted); font-size:11px; }
.record-billing h2 { margin:7px 0 4px; font:500 21px/1.4 var(--sans); letter-spacing:-.3px; overflow-wrap:anywhere; }
.record-billing p { margin:5px 0 0; overflow-wrap:anywhere; }
.record-balance { text-align:right; }
.record-balance strong { display:block; margin:7px 0; font:500 31px/1.25 var(--mono); letter-spacing:-1px; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.record-balance p { color:var(--muted); font-size:12px; }
.record-toolbar { max-width:960px; margin:0 auto 16px; display:flex; justify-content:flex-end; align-items:center; gap:14px; }
.record-toolbar button { border:1px solid var(--brand); border-radius:5px; background:var(--brand); color:#fff; padding:11px 17px; cursor:pointer; font:500 13px/1.3 var(--sans); }
.record-toolbar button:hover { background:var(--brand-deep); }
.record-toolbar button:focus-visible, summary:focus-visible { outline:2px solid var(--ink); outline-offset:4px; }
.record-toolbar p { margin:0; color:var(--muted); font-size:11px; }
.invoice-details { margin:28px 0 0; padding:0; border:0; }
.invoice-details>summary { display:none; }
.invoice-detail-content { font:400 13px/1.6 var(--sans); }
.invoice-paper-meta { display:grid; grid-template-columns:minmax(0,1.5fr) minmax(0,1fr) minmax(0,1fr); gap:24px; margin:0 0 30px; }
.invoice-paper-meta>div { min-width:0; }
.invoice-paper-meta dt,.invoice-paper-amounts dt { color:var(--muted); font:400 11px/1.5 var(--sans); }
.invoice-paper-meta dd { margin:7px 0 0; white-space: pre-wrap; overflow-wrap:anywhere; font:400 14px/1.6 var(--sans); }
.invoice-paper-body .invoice-detail-table { border:0; border-radius:0; margin:0; overflow:visible; }
.invoice-detail-table .invoice-paper-items { width:100%; min-width:0; table-layout:fixed; border-collapse:collapse; }
.invoice-paper-items caption { display:none; }
.invoice-paper-items th { font:400 11px/1.45 var(--sans); color:var(--muted); }
.invoice-paper-items th:nth-child(1) { width:42%; }
.invoice-paper-items th:nth-child(2) { width:10%; }
.invoice-paper-items th:nth-child(3) { width:12%; }
.invoice-paper-items th:nth-child(4),.invoice-paper-items th:nth-child(5) { width:18%; }
.invoice-paper-items th,.invoice-paper-items td { border:0; border-bottom:1px solid var(--line); padding:15px 8px; vertical-align:top; overflow-wrap:anywhere; }
.invoice-paper-items th { padding-top:0; padding-bottom:11px; border-bottom-color:#aebbd0; }
.invoice-paper-items th:first-child,.invoice-paper-items td:first-child { padding-left:0; }
.invoice-paper-items th:last-child,.invoice-paper-items td:last-child { padding-right:0; text-align:right; }
.invoice-paper-items th:nth-child(2),.invoice-paper-items th:nth-child(4),.invoice-paper-items td:nth-child(2),.invoice-paper-items td:nth-child(4) { text-align:right; }
.invoice-paper-items td { font:400 14px/1.55 var(--sans); }
.invoice-paper-items td:nth-child(2),.invoice-paper-items td:nth-child(4),.invoice-paper-items td:last-child { font:400 13px/1.65 var(--mono); font-variant-numeric:tabular-nums; }
.invoice-paper-items td:nth-child(3) { color:var(--muted); font:400 12px/1.55 var(--sans); }
.invoice-paper-items .invoice-detail-description { margin-top:5px; font:400 12px/1.55 var(--sans); color:var(--muted); }
.invoice-paper-footer { display:grid; grid-template-columns:minmax(0,1fr) minmax(240px,.8fr); gap:36px; margin:28px 0; }
.invoice-paper-note { min-width:0; }
.invoice-paper-note h4 { margin:0 0 8px; color:var(--muted); font:400 11px/1.5 var(--sans); }
.invoice-detail-note { white-space: pre-wrap; overflow-wrap:anywhere; }
.invoice-paper-note .invoice-detail-note { margin:0; font:400 13px/1.65 var(--sans); }
.invoice-paper-amounts { margin:0; min-width:0; }
.invoice-paper-amounts>div { display:flex; justify-content:space-between; gap:20px; padding:8px 0; }
.invoice-paper-amounts dd { margin:0; text-align:right; font:400 13px/1.5 var(--mono); overflow-wrap:anywhere; }
.invoice-paper-amounts>div:last-child { border-top:1px solid var(--line); margin-top:8px; padding-top:16px; }
.invoice-paper-amounts>div:last-child dd { font-size:20px; font-weight:500; }
.invoice-provider-record { border-top:1px solid var(--line); padding-top:14px; margin-top:30px; }
.invoice-provider-record>summary,.record-support>summary { cursor:pointer; color:var(--muted); font:400 12px/1.5 var(--sans); }
.record-support { margin-top:22px; padding-top:14px; border-top:1px solid var(--line); }
.record-boundary { padding:12px 14px; background:var(--soft); border-left:3px solid var(--brand); font-size:12px; overflow-wrap:anywhere; }
.record-metadata { color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
.record-summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px; background:var(--line); border:1px solid var(--line); }
.record-summary>div { padding:12px 14px; min-width:0; background:var(--soft); }
.record-summary dt { color:var(--muted); font-size:11px; }
.record-summary dd { margin:6px 0 0; white-space: pre-wrap; overflow-wrap:anywhere; font:400 12px/1.6 var(--sans); }
.record-summary [data-key=balance] dd,.record-summary [data-key=total] dd { font-family:var(--mono); }
.record-decisions { padding:0; list-style:none; }
.record-decision { margin:14px 0; padding:12px 15px; border-left:3px solid var(--brand); background:var(--soft); overflow-wrap:anywhere; }
.record-decision h3 { margin:0 0 8px; font:500 14px/1.5 var(--sans); }
.record-decision p { margin:7px 0; white-space: pre-wrap; }
.record-decision dl { color:var(--muted); font-size:11px; }
.record-decision dt { font-weight:500; }
.record-decision dd { margin:0 0 6px; }
footer { margin-top:24px; padding-top:14px; border-top:1px solid var(--line); color:var(--muted); font-size:11px; }
@media(max-width:660px) {
  body { padding:14px 10px 28px; }
  main { padding:27px 20px 24px; }
  .record-toolbar { gap:12px; justify-content:space-between; margin-bottom:13px; }
  .record-toolbar p { max-width:130px; }
  .record-brand { font-size:21px; gap:8px; }
  .record-brand svg { width:25px; height:25px; }
  .record-stamp { font-size:10px; padding:3px 6px; }
  .record-masthead { gap:10px; }
  .record-label { margin-top:28px; }
  h1 { font-size:32px; letter-spacing:-1px; margin-bottom:24px; }
  .record-billing { grid-template-columns:minmax(0,1fr); gap:19px; padding:22px 0; }
  .record-billing h2 { font-size:20px; }
  .record-balance { text-align:left; border-top:1px solid var(--line); padding-top:16px; }
  .record-balance strong { font-size:30px; }
  .invoice-paper-meta { grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; margin-bottom:26px; }
  .invoice-paper-meta>div:first-child { grid-column:1/-1; }
  .invoice-paper-meta dd { font-size:13px; }
  .invoice-paper-footer { grid-template-columns:minmax(0,1fr); gap:23px; margin-top:24px; }
  .record-summary { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
@media screen and (max-width:520px) {
  .invoice-paper-items th:first-child { width:55%; }
  .invoice-paper-items th:nth-child(2) { width:15%; }
  .invoice-paper-items th:last-child { width:30%; }
  .invoice-paper-items th,.invoice-paper-items td { padding-inline:5px; }
  .invoice-paper-items td { font-size:13px; }
  .invoice-paper-items td:nth-child(2),.invoice-paper-items td:last-child { font-size:11px; }
  .invoice-paper-items .invoice-detail-mobile-rate { font:400 10px/1.6 var(--mono); color:var(--muted); }
  .invoice-paper-items .invoice-detail-mobile-unit { font:400 10px/1.6 var(--sans); color:var(--muted); }
}
@page {
  size:letter; margin:.65in .48in .62in;
  @bottom-right { content:"Page " counter(page) " of " counter(pages); font:9pt "IBM Plex Sans",Arial,sans-serif; color:#526482; }
}
@media print {
  body { padding:0; color:var(--ink); background:#fff; font-size:10pt; }
  main { max-width:none; padding:0; border:0; box-shadow:none; }
  .record-toolbar,.record-support,.invoice-provider-record { display:none; }
  .record-label { margin-top:24px; }
  h1 { font-size:32pt; margin-bottom:23px; }
  .record-billing { grid-template-columns:minmax(0,1fr) minmax(0,1fr); padding:22px 0; gap:24px; }
  .record-balance { text-align:right; border-top:0; padding-top:0; }
  .record-balance strong { font-size:23pt; }
  .invoice-paper-meta { grid-template-columns:minmax(0,1.5fr) minmax(0,1fr) minmax(0,1fr); gap:20px; }
  .invoice-paper-meta>div:first-child { grid-column:auto; }
  .invoice-paper-footer { grid-template-columns:minmax(0,1fr) minmax(220px,.8fr); gap:28px; }
  .invoice-paper-items th:nth-child(1) { width:42%; }
  .invoice-paper-items th:nth-child(2) { width:10%; }
  .invoice-paper-items th:nth-child(3) { width:12%; }
  .invoice-paper-items th:nth-child(4),.invoice-paper-items th:nth-child(5) { width:18%; }
  .invoice-paper-items th,.invoice-paper-items td { padding:10px 6px; }
  .invoice-paper-items th { font-size:8pt; overflow-wrap:normal; }
  .invoice-paper-items td { font-size:9pt; }
  .invoice-paper-items td:nth-child(2),.invoice-paper-items td:nth-child(4),.invoice-paper-items td:last-child { font-size:8.5pt; }
  .invoice-paper-meta dd { font-size:9.5pt; }
  .invoice-paper-amounts,.record-masthead,.record-billing,.invoice-paper-meta,tr { break-inside:avoid; }
  h1,h2,h4 { break-after: avoid; }
}
`;
const printScript = `(() => {
  const openRecord = () => { document.querySelector('.invoice-details').open = true; };
  window.addEventListener('beforeprint', openRecord);
  document.querySelector('#print-invoice-record').addEventListener('click', () => {
    openRecord();
    window.print();
  });
})();`;

/** Original strings and currencies stay in separate fields; no money arithmetic. */
export function buildInvoiceRecord(
  snapshot: InvoiceRecordSnapshot, invoiceId: string, createdAt = new Date(),
): {filename: string; text: string} {
  const selected = selectedSnapshot(snapshot, invoiceId);
  const entry = fields((selected.ledger as unknown[])[0]);
  const record = fields(fields((selected.invoice_details as unknown[])[0]).record);
  const detail = fields(record.detail), due = fields(record.due_amount);
  const recipient = fields(fields((Array.isArray(record.primary_recipients)?record.primary_recipients:[])[0]).billing_info);
  const name = fields(recipient.name);
  const clientName = [name.given_name,name.surname].filter(value=>typeof value==='string').join(' ') || text(recipient.email_address);
  const dueTerm = fields(detail.payment_term);
  const deadline = typeof dueTerm.due_date==='string' ? 'Due '+dueTerm.due_date : dueTerm.term_type==='DUE_ON_RECEIPT' ? 'Due on receipt' : dueTerm.term_type==='NO_DUE_DATE' ? 'No due date' : 'Due date not recorded';
  const formattedDue = typeof due.value === 'string' && typeof due.currency_code === 'string'
    ? displayMoney(due.value, due.currency_code) : text(due.currency_code) + ' ' + text(due.value);
  const created = createdAt.toISOString();
  const native = selected.native_operator === true;
  const observation = fields(fields((selected.invoice_details as unknown[])[0]).observation);
  const observedMetadata = native
    ? 'Provider record last received (UTC): <span id="invoice-record-observed">' +
      escape(text(observation.observed_at)) + '</span><br>' : '';
  const provenance = native
    ? 'A saved copy from Ledgerly’s native invoice operator. Operator provider: ' +
      escape(text(selected.provider)) + '. This download does not create, send, record payment or refresh provider data. '
    : 'A saved copy from Ledgerly’s in-memory simulation. ';
  const rows = summaryFields.map(([key, label]) => '<div data-key="' + key + '"><dt>' + label +
    '</dt><dd data-ledger-field="' + key + '">' + escape(text(entry[key])) + '</dd></div>').join('');
  const printIdentity = 'Invoice ' + text(entry.invoice_number) + ' · ' + clientName;
  const printPageStyle = '@page { @top-left { content: ' + cssQuotedText(printIdentity) +
    '; font: 9pt system-ui,sans-serif; color: #687281; } }';
  const document = '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; font-src data:; script-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">' +
    '<title>Invoice ' + escape(text(entry.invoice_number)) + ' — ' + escape(clientName) + ' · Ledgerly Sandbox</title>' +
    '<style>' + invoiceDetailStyles + embeddedFontStyles + pageStyles + printPageStyle + '</style></head>' +
    '<body data-invoice-record-format="ledgerly.sandbox-invoice-record.v1"><div class="record-toolbar"><button id="print-invoice-record" type="button">Print this record</button><p>Save as PDF from the print dialog.</p></div><main>' +
    '<header><div class="record-masthead"><div class="record-brand"><svg viewBox="0 0 32 32" aria-hidden="true"><path fill="currentColor" d="M3 3h7v22h19v5H3zM15 3h14v5H15zM15 13h14v5H15z"/></svg>Ledgerly</div>' +
    '<div class="record-stamp">Sandbox · not payable</div></div><p class="record-label">INVOICE RECORD</p><h1>Invoice ' + escape(text(entry.invoice_number)) + '</h1>' +
    '</header><section class="record-billing"><div><span>Bill to</span><h2>'+escape(clientName)+'</h2><p>'+escape(text(recipient.email_address))+'</p></div><div class="record-balance"><span>'+ (record.status==='DRAFT'?'Draft amount due':'Amount due') +'</span><strong>'+escape(formattedDue)+'</strong><p>'+escape(deadline)+'</p></div></section>' +
    invoiceDetailsMarkup(invoiceId, selected, true, 'paper') +
    '<details class="record-support"><summary>Record details and history</summary>' +
    '<p id="invoice-record-boundary" class="record-boundary">' + provenance +
    'This is not a payable invoice, a payment receipt or a current provider statement.' +(native?'':' No real money moved.')+'</p>'+
    '<p class="record-metadata">'+observedMetadata+(native?'Ledger date: ':'Sandbox clock at source snapshot: ')+'<span id="invoice-record-snapshot">'+escape(selected.today as string)+'</span><br>File created at (UTC): <span id="invoice-record-created">'+escape(created)+'</span></p>'+
    '<section aria-labelledby="record-summary-heading"><h2 id="record-summary-heading">Original agent ledger summary</h2><dl class="record-summary">'+rows+'</dl></section>'+decisionHistoryMarkup(selected)+
    '<footer>Only the selected displayed invoice is included. Amounts, dates, items and recorded payments are copied without recalculation or a provider refresh. This HTML file cannot restore a sandbox or authorize any action.</footer></details>' +
    '</main><script>' + printScript + '</script></body></html>\n';
  if (new TextEncoder().encode(document).byteLength > MAX_RECORD_BYTES)
    throw new Error('This invoice record exceeds the 4 MiB download limit.');
  const number = typeof entry.invoice_number === 'string' && entry.invoice_number.trim() ? entry.invoice_number : invoiceId;
  return {filename: 'ledgerly-invoice-' + filenamePart(number) + '-' + filenamePart(clientName) + '.html', text: document};
}

/** Bind downloads to the accepted displayed snapshot and current availability. */
export function createInvoiceRecordDownloads(container: HTMLElement, announce: Announce = () => {}) {
  let snapshots = new Map<string, InvoiceRecordSnapshot>();
  let available = false, busy = false;
  container.addEventListener('click', event => {
    const button = (event.target as HTMLElement).closest?.<HTMLButtonElement>('button[data-save-invoice-record], button[data-print-invoice-record]');
    if (!button || !container.contains(button) || !available || busy || button.disabled) return;
    const printing = button.dataset.printInvoiceRecord !== undefined;
    const id = printing ? button.dataset.printInvoiceRecord : button.dataset.saveInvoiceRecord;
    const snapshot = id === undefined ? undefined : snapshots.get(id);
    if (!snapshot || id === undefined) return;
    let url: string | undefined;
    let anchor: HTMLAnchorElement | undefined;
    try {
      // Capture this immutable selection before allocating the download; a
      // subsequent render cannot substitute another snapshot into these bytes.
      const record = buildInvoiceRecord(snapshot, id);
      if (printing) {
        const preview = container.ownerDocument.defaultView?.open('', '_blank');
        if (!preview) throw new Error('The browser did not open the invoice print window.');
        preview.opener = null;
        preview.document.open();
        preview.addEventListener('load', () => { preview.focus(); preview.print(); }, {once: true});
        preview.document.write(record.text);
        preview.document.close();
        announce('Invoice opened for printing. Choose a printer or Save as PDF.', 'ready');
        return;
      }
      const blob = new Blob([record.text], {type: 'text/html;charset=utf-8'});
      url = URL.createObjectURL(blob);
      anchor = container.ownerDocument.createElement('a');
      anchor.href = url;
      anchor.download = record.filename;
      container.ownerDocument.body.appendChild(anchor);
      anchor.click();
      announce('Sandbox invoice record download started. This saved copy will not update with the sandbox.', 'ready');
    } catch {
      announce(printing ? 'The print window could not open. Allow pop-ups or use Download or export to save this invoice.' : 'Could not start this invoice record download. The displayed snapshot is unchanged; try again.', 'error');
    } finally {
      anchor?.remove();
      if (url !== undefined) {
        const completedUrl = url;
        setTimeout(() => URL.revokeObjectURL(completedUrl), 1000);
      }
    }
  });
  const sync = () => {
    for (const button of Array.from(container.querySelectorAll<HTMLButtonElement>('button[data-save-invoice-record], button[data-print-invoice-record]')))
      button.disabled = !available || busy || !snapshots.has(button.dataset.printInvoiceRecord ?? button.dataset.saveInvoiceRecord ?? '');
  };
  return {
    update(snapshot: InvoiceRecordSnapshot): void {
      const next = new Map<string, InvoiceRecordSnapshot>();
      for (const row of Array.isArray(snapshot?.ledger) ? snapshot.ledger : []) {
        const id = fields(row).invoice_id;
        if (typeof id !== 'string') continue;
        try { next.set(id, selectedSnapshot(snapshot, id)); }
        catch { /* Missing or ambiguous source stays unavailable, without a stale fallback. */ }
      }
      snapshots = next;
      sync();
    },
    markup(invoiceId: string, action: 'download' | 'print' = 'download'): string {
      const disabled = !available || busy || !snapshots.has(invoiceId);
      const printing = action === 'print';
      return '<div class="invoice-record-action"><button type="button" class="button button-secondary" ' +
        (printing ? 'data-print-invoice-record="' : 'data-save-invoice-record="') + escape(invoiceId) + '"' + (disabled ? ' disabled' : '') +
        '>' + (printing ? 'Print or save PDF' : 'Save sandbox invoice record') + '</button>' +
        (printing ? '' : '<p class="fine-print">Keep this invoice’s displayed details in a standalone HTML copy.</p>') + '</div>';
    },
    setAvailability(ready: boolean, inProgress: boolean): void {
      available = ready; busy = inProgress; sync();
    },
  };
}
