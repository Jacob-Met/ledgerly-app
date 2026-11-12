/** Single-invoice reminder affordances from the same admitted receivables projection. */
import {projectReceivables} from './receivables';

const escape = (value: string): string => value.replace(/[&<>"']/g, character =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]!));

/**
 * Display only overdue invoices with a positive, native-projected balance.
 * The Python Agent, not this view, decides final eligibility at click time.
 */
export function targetedReminderActions(snapshot: unknown): Map<string,string> {
  if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot))
    throw new Error('A native sandbox snapshot is required.');
  const record = snapshot as Record<string, unknown>;
  if (!Array.isArray(record.pending))
    throw new Error('The native pending queue must be available.');
  const pending = new Map<string,string|null>();
  for (const action of record.pending) {
    if (!action || typeof action !== 'object' || Array.isArray(action))
      throw new Error('An incomplete native approval action cannot be displayed.');
    const entry = action as Record<string,unknown>;
    if (entry.kind === 'send_reminder') {
      if (typeof entry.invoice_id !== 'string')
        throw new Error('A pending reminder has no invoice reference.');
      pending.set(entry.invoice_id, typeof entry.id === 'string' ? entry.id : null);
    }
  }
  const projected = projectReceivables(snapshot);
  const actions = new Map<string,string>();
  for (const client of projected.clients) {
    for (const invoice of client.invoices) {
      if (invoice.dueState !== 'overdue') continue;
      const id = escape(invoice.invoice_id);
      if (pending.has(invoice.invoice_id)) {
        actions.set(invoice.invoice_id,
          '<div class="invoice-reminder-pending"><p>Reminder awaiting human approval. No message sent.</p>' +
          (pending.get(invoice.invoice_id) ? '<button type="button" class="button button-secondary" data-review-action="' + escape(pending.get(invoice.invoice_id)!) + '">Review this reminder</button>' : '') + '</div>');
      } else {
        actions.set(invoice.invoice_id,
          '<div class="invoice-reminder-action"><button type="button" class="button button-secondary" data-remind="' + id +
          '" aria-label="Prepare one sandbox reminder for invoice ' + escape(invoice.invoice_number) +
          '">Prepare overdue reminder</button><p>' + invoice.daysOverdue +
          ' day' + (invoice.daysOverdue === 1 ? '' : 's') +
          ' overdue · drafts one reminder for human review, without sending.</p></div>');
      }
    }
  }
  return actions;
}
