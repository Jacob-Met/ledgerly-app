import {displayMoney} from './saved-invoice';
/** Read-only display of detached records returned by the Python sandbox. */
type Fields = Record<string, unknown>;

export interface InvoiceDetailSnapshot {
  today?: unknown;
  native_operator?: unknown;
  invoice_details?: unknown;
}

const escape = (value: string): string => value.replace(/[&<>"']/g,
  character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]!));

function fields(value: unknown): Fields {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Fields : {};
}

function text(value: unknown, missing = 'Not recorded'): string {
  if (typeof value === 'string') return value || '(empty)';
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : missing;
}

function row(label: string, value: unknown): string {
  return '<div><dt>' + escape(label) + '</dt><dd>' + escape(text(value)) + '</dd></div>';
}

function money(value: unknown): string {
  const amount = fields(value);
  return text(amount.currency_code, 'Currency not recorded') + ' ' + text(amount.value, 'amount not recorded');
}

function paperMoney(value: unknown): string {
  const amount = fields(value);
  if (amount.value == null && amount.currency_code == null) return 'Not recorded';
  return typeof amount.value === 'string' && typeof amount.currency_code === 'string'
    ? displayMoney(amount.value, amount.currency_code) : money(value);
}

function person(value: unknown): string {
  const person = fields(value), name = fields(person.name), address = fields(person.address);
  const parts = [
    [name.given_name, name.surname].filter(value => typeof value === 'string' && value.length).join(' '),
    person.email_address,
    ...['address_line_1', 'address_line_2', 'admin_area_2', 'admin_area_1', 'postal_code', 'country_code'].map(key => address[key]),
  ].filter(value => typeof value === 'string' && value.length);
  return parts.length ? parts.join('\n') : 'Not recorded';
}

function terms(value: unknown): string {
  const kind = text(value, ''), net = /^NET_([0-9]+)$/.exec(kind);
  if (kind === 'DUE_ON_RECEIPT') return 'Due on receipt';
  if (kind === 'DUE_ON_DATE_SPECIFIED') return 'Due on the stated date';
  if (kind === 'NO_DUE_DATE') return 'No due date';
  return net ? 'Net ' + net[1] + ' days' : kind || 'Not recorded';
}

/** Exact line basis only; provider totals, taxes and discounts are never inferred. */
function lineSubtotal(item: Fields): string {
  const amount = fields(item.unit_amount);
  const decimal = (value: unknown): {digits: bigint; scale: number}|null => {
    if (typeof value !== 'string' || !/^\d{1,30}(?:\.\d{1,12})?$/.test(value)) return null;
    const [whole, fraction = ''] = value.split('.');
    return {digits: BigInt(whole + fraction), scale: fraction.length};
  };
  const quantity = decimal(item.quantity), price = decimal(amount.value);
  if (!quantity || !price || typeof amount.currency_code !== 'string') return 'Not recorded';
  const scale = quantity.scale + price.scale;
  const digits = (quantity.digits * price.digits).toString().padStart(scale + 1, '0');
  const raw = scale ? digits.slice(0, -scale) + '.' + digits.slice(-scale) : digits;
  const exact = raw.includes('.') ? raw.replace(/0+$/, '').replace(/\.$/, '') : raw;
  return displayMoney(exact, amount.currency_code);
}

function itemsMarkup(value: unknown, paper = false): string {
  const items = Array.isArray(value) ? value : [];
  const rows = items.map(value => {
    const item = fields(value);
    const rawUnit = text(item.unit_of_measure);
    const retainedUnit = rawUnit === 'QUANTITY' && typeof item.description === 'string' && item.description.length <= 1000
      ? /^Unit: ([^\r\n]+)$/.exec(item.description)?.[1] : undefined;
    const namedUnit = retainedUnit && retainedUnit.trim() === retainedUnit ? retainedUnit : undefined;
    const unit = paper && rawUnit === 'QUANTITY' ? namedUnit || 'items' : paper && rawUnit === 'HOURS' ? 'hours' : rawUnit;
    const description = !(paper && namedUnit) && typeof item.description === 'string'
      ? '<span class="invoice-detail-description">' + escape(item.description) + '</span>' : '';
    return '<tr><td>' + escape(text(item.name)) + description + (paper ? '<span class="invoice-detail-mobile-rate">' + escape(paperMoney(item.unit_amount)) + ' each</span>' : '') + '</td><td>' + escape(text(item.quantity)) +
      '<span class="invoice-detail-mobile-unit">' + escape(unit) + '</span>' +
      '</td><td class="invoice-detail-unit-column">' + escape(unit) + '</td><td class="invoice-detail-rate-column">' + escape(paper ? paperMoney(item.unit_amount) : money(item.unit_amount)) + '</td>' +
      (paper ? '<td class="invoice-detail-line-subtotal">' + escape(lineSubtotal(item)) + '</td>' : '') + '</tr>';
  }).join('');
  return '<div class="invoice-detail-table" tabindex="0" role="region" aria-label="Invoice items; scroll horizontally if needed">' +
    '<table class="invoice-detail-items' + (paper ? ' invoice-paper-items' : '') + '"><caption>Invoice items</caption><thead><tr><th scope="col">Item</th><th scope="col"><span class="invoice-detail-desktop-quantity">Quantity</span><span class="invoice-detail-mobile-quantity">Qty / unit</span></th>' +
    '<th scope="col" class="invoice-detail-unit-column">Unit</th><th scope="col" class="invoice-detail-rate-column">Unit price</th>' +
    (paper ? '<th scope="col" title="Quantity × unit price, before tax or discount">Line subtotal</th>' : '') + '</tr></thead><tbody>' +
    (rows || '<tr><td colspan="' + (paper ? '5' : '4') + '">No item details recorded.</td></tr>') + '</tbody></table></div>';
}

function paymentsMarkup(value: unknown): string {
  const payments = fields(value), transactions = Array.isArray(payments.transactions) ? payments.transactions : [];
  if (!transactions.length) return '<p class="invoice-detail-empty">' +
    (Array.isArray(payments.transactions) ? 'No recorded sandbox payments.' : 'Payment details not recorded.') + '</p>';
  const rows = transactions.map(value => {
    const payment = fields(value);
    return '<tr><td>' + escape(text(payment.payment_date)) + '</td><td>' + escape(money(payment.amount)) +
      '</td><td>' + escape(text(payment.type)) + ' / ' + escape(text(payment.method)) + '</td><td>' +
      escape(text(payment.payment_id)) + '</td></tr>';
  }).join('');
  return '<div class="invoice-detail-table" tabindex="0" role="region" aria-label="Recorded sandbox payments; scroll horizontally if needed">' +
    '<table class="invoice-detail-payments"><caption>Recorded sandbox payments</caption><thead><tr><th scope="col">Date</th><th scope="col">Amount</th>' +
    '<th scope="col">Type / method</th><th scope="col">Reference</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}

function availabilityText(available: boolean, busy: boolean, native: boolean): string {
  if (native) {
    if (!available) return 'Last displayed provider record. Reopen the saved operator to continue.';
    if (busy) return 'Last displayed provider record. An action is in progress; details update when it finishes.';
    return 'Retained provider record from this operator. Opening details makes no provider request.';
  }
  if (!available) return 'Last displayed sandbox snapshot. Reload the saved workspace to continue.';
  if (busy) return 'Last displayed sandbox snapshot. An action is in progress; details update when it finishes.';
  return 'Read-only copy from this tab’s in-memory sandbox. Opening details makes no provider request.';
}

/** Exact identity matching; no analysis, pending-action or different-invoice fallback. */
export function invoiceDetailsMarkup(invoiceId: string, snapshot: InvoiceDetailSnapshot, open = false, presentation: 'record'|'paper' = 'record'): string {
  const rows = Array.isArray(snapshot.invoice_details) ? snapshot.invoice_details : [];
  const matches = rows.filter(value => fields(value).invoice_id === invoiceId);
  const record = matches.length === 1 ? fields(fields(matches[0]).record) : {};
  const available = record.id === invoiceId;
  const observation = available ? fields(fields(matches[0]).observation) : {};
  const provenance = snapshot.native_operator === true
    ? 'Provider record last received (UTC): ' + escape(text(observation.observed_at)) +
      '<br>Record source: ' + escape(text(observation.provider)) +
      '. Reopening or downloading does not refresh this record.'
    : 'Sandbox clock at snapshot: ' + escape(text(snapshot.today));
  const detail = fields(record.detail), payment = fields(detail.payment_term), payments = fields(record.payments);
  const recipients = Array.isArray(record.primary_recipients) ? record.primary_recipients : [];
  const recipient = recipients.map(value => person(fields(value).billing_info)).join('\n\n') || 'Not recorded';
  const content = available ?
    itemsMarkup(record.items) + '<dl class="invoice-detail-fields">' + row('Invoice reference', record.id) + row('Invoice number', detail.invoice_number) +
    row('Sandbox provider status', record.status) + row('Recipient', recipient) + row('From', person(record.invoicer)) +
    row('Invoice date', detail.invoice_date) + row('Payment terms', terms(payment.term_type)) + row('Due date', payment.due_date) +
    '</dl>' + '<dl class="invoice-detail-fields invoice-detail-amounts">' +
    row('Provider invoice total', money(record.amount)) + row('Provider paid amount', money(payments.paid_amount)) +
    row('Provider amount due', money(record.due_amount)) + '</dl>' +
    '<p class="invoice-detail-explanation">Amounts and payment entries are copied from the sandbox provider record. The ledger summary above is the agent’s view; these values are not recalculated or combined across currencies.</p>' +
    '<h4>Invoice note</h4><div class="invoice-detail-note">' + escape(text(detail.note)) + '</div>' + paymentsMarkup(record.payments) :
    '<p class="invoice-detail-empty">No matching retained sandbox invoice record is available. The ledger summary above may still be inspected.</p>';
  const paper = presentation === 'paper' && available;
  const paperContent = paper ?
    '<div class="invoice-paper-body"><dl class="invoice-paper-meta">' +
    row('From', person(record.invoicer)) + row('Invoice date', detail.invoice_date) +
    row('Payment terms', terms(payment.term_type)) + '</dl>' +
    itemsMarkup(record.items, true) +
    '<div class="invoice-paper-footer"><div class="invoice-paper-note"><h4>Note</h4>' +
    '<div class="invoice-detail-note">' + escape(text(detail.note)) + '</div></div>' +
    '<dl class="invoice-paper-amounts">' + row('Invoice total', paperMoney(record.amount)) +
    row('Paid', paperMoney(payments.paid_amount)) + row('Amount due', paperMoney(record.due_amount)) + '</dl></div>' +
    (Array.isArray(payments.transactions) && payments.transactions.length ? paymentsMarkup(record.payments) : '') +
    '</div><details class="invoice-provider-record"><summary>' +
    (snapshot.native_operator === true ? 'Retained provider record' : 'Sandbox record') +
    '</summary><p class="invoice-detail-provenance">' + provenance + '</p>' + content + '</details>' : content;
  // This read-only disclosure stays usable when its parent action area is disabled.
  return '<details class="invoice-details" aria-disabled="false" data-invoice-detail-id="' + escape(invoiceId) + '"' + (paper ? ' data-presentation="paper"' : '') + (open ? ' open' : '') +
    '><summary>Invoice details' + (paper ? '' : ' <span>· sandbox record</span>') + '</summary><div class="invoice-detail-content">' +
    (paper ? '' : '<p class="invoice-detail-provenance">' + provenance + '</p>') +
    '<p class="invoice-detail-state" data-invoice-details-state></p>' + paperContent + '</div></details>';
}

/** Keep open details and keyboard focus across normal whole-ledger rendering. */
export function createInvoiceDetailsView(container: HTMLElement) {
  const openIds = new Set<string>();
  let focusedId: string | undefined;
  let native = false;
  container.addEventListener('toggle', event => {
    const detail = event.target as HTMLDetailsElement;
    if (!detail.matches?.('.invoice-details')) return;
    const id = detail.dataset.invoiceDetailId;
    if (id !== undefined) { if (detail.open) openIds.add(id); else openIds.delete(id); }
  }, true);
  return {
    beforeRender(ids: readonly string[]): void {
      // Native details.open changes before the browser dispatches its queued toggle.
      // Read the current DOM before replacing it so an arriving snapshot cannot undo a click.
      for (const detail of Array.from(container.querySelectorAll<HTMLDetailsElement>('.invoice-details'))) {
        const id = detail.dataset.invoiceDetailId;
        if (id !== undefined) { if (detail.open) openIds.add(id); else openIds.delete(id); }
      }
      for (const id of openIds) if (!ids.includes(id)) openIds.delete(id);
      const focused = container.ownerDocument?.activeElement;
      const detail = focused?.closest<HTMLDetailsElement>('.invoice-details');
      focusedId = detail && container.contains(detail) ? detail.dataset.invoiceDetailId : undefined;
    },
    markup(invoiceId: string, snapshot: InvoiceDetailSnapshot): string {
      native = snapshot.native_operator === true;
      return invoiceDetailsMarkup(invoiceId, snapshot, openIds.has(invoiceId), 'paper');
    },
    afterRender(): void {
      if (focusedId === undefined) return;
      const detail = Array.from(container.querySelectorAll<HTMLDetailsElement>('.invoice-details'))
        .find(detail => detail.dataset.invoiceDetailId === focusedId);
      detail?.querySelector<HTMLElement>('summary')?.focus({preventScroll: true});
      focusedId = undefined;
    },
    setAvailability(available: boolean, busy: boolean): void {
      for (const message of Array.from(container.querySelectorAll<HTMLElement>('[data-invoice-details-state]'))) {
        message.textContent = availabilityText(available, busy, native);
        message.hidden = available && !busy;
        message.dataset.inactive = String(!available);
      }
    },
  };
}
