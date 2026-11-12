import {quoteCsvCell, type LedgerDownload} from './ledger-export';
/** Read-only client balances from one accepted native sandbox snapshot. */
export type DueState = 'overdue' | 'today' | 'later' | 'undated';
export type ReceivableInvoice = {
  invoice_id: string; invoice_number: string; client_email: string; client_name: string | null;
  currency: string; balance: string; status: string; due_on: string | null;
  dueState: DueState; daysOverdue: number; recordObservedAt?: string | null;
};
export type ClientReceivables = {email: string; names: string[]; invoices: ReceivableInvoice[]};
export type Receivables = {
  today: string; ledgerCount: number; invoiceCount: number;
  clients: ClientReceivables[]; excluded: {status: string; count: number}[];
};
export type CurrencyTotal = {currency: string; balance: string; invoiceCount: number};
export type ReceivablesSelection = {clients: ClientReceivables[]; totals: CurrencyTotal[]; invoiceCount: number};
type DecimalValue = {units: bigint; scale: number};
const OPEN = new Set(['SENT', 'UNPAID', 'PARTIALLY_PAID', 'PAYMENT_PENDING']);
const DUE: Record<DueState, string> = {
  overdue: 'Overdue', today: 'Due today', later: 'Due later', undated: 'Undated',
};
const compare = (left: string, right: string) => left < right ? -1 : left > right ? 1 : 0;

function text(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`Missing or invalid ${field}.`);
  return value;
}
function day(value: unknown, field: string): number {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000-')) {
    throw new Error(`Invalid ${field}; an ISO calendar date is required.`);
  }
  const parsed = new Date(value + 'T00:00:00.000Z');
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new Error(`Invalid ${field}; an ISO calendar date is required.`);
  }
  return parsed.getTime() / 86400000;
}
function decimal(value: unknown): DecimalValue {
  if (typeof value !== 'string' || value.length > 1100) throw new Error('Invalid native balance string.');
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d{1,4}))?$/.exec(value);
  if (!match) throw new Error('Invalid native balance string.');
  const fraction = match[3] || '';
  const exponent = Number(match[4] || 0);
  if (Math.abs(exponent) > 1000 || match[2].length + fraction.length > 1000) {
    throw new Error('Native balance exceeds the supported display range.');
  }
  let units = BigInt(match[2] + fraction);
  if (match[1] === '-') units = -units;
  if (units < 0n) throw new Error('A native balance cannot be negative.');
  let scale = fraction.length - exponent;
  if (scale < 0) { units *= 10n ** BigInt(-scale); scale = 0; }
  return {units, scale};
}
function plus(left: DecimalValue, right: DecimalValue): DecimalValue {
  const scale = Math.max(left.scale, right.scale);
  return {units: left.units * 10n ** BigInt(scale - left.scale)
    + right.units * 10n ** BigInt(scale - right.scale), scale};
}
function fixed(value: DecimalValue): string {
  const digits = value.units.toString().padStart(value.scale + 1, '0');
  return value.scale ? digits.slice(0, -value.scale) + '.' + digits.slice(-value.scale) : digits;
}
function totals(invoices: ReceivableInvoice[]): CurrencyTotal[] {
  const grouped = new Map<string, {value: DecimalValue; count: number}>();
  for (const invoice of invoices) {
    const existing = grouped.get(invoice.currency);
    const amount = decimal(invoice.balance);
    grouped.set(invoice.currency, {value: existing ? plus(existing.value, amount) : amount,
      count: (existing?.count || 0) + 1});
  }
  return [...grouped].sort(([a], [b]) => compare(a, b))
    .map(([currency, entry]) => ({currency, balance: fixed(entry.value), invoiceCount: entry.count}));
}

/** Admission is complete before any totals are exposed; native source objects are not retained. */
export function projectReceivables(snapshot: unknown): Receivables {
  if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)) throw new Error('A native snapshot is required.');
  const value = snapshot as Record<string, unknown>;
  const todayNumber = day(value.today, 'snapshot date');
  if (!Array.isArray(value.ledger)) throw new Error('A complete native ledger is required.');
  // Provider receipt time is separate from the day used to calculate aging.
  // Older/local records have no observation; never substitute the browser clock.
  const observations = new Map<string, string | null>();
  if (Array.isArray(value.invoice_details)) for (const raw of value.invoice_details) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue;
    const detail = raw as Record<string, any>, id = detail.invoice_id;
    if (typeof id !== 'string') continue;
    const at = detail.observation?.observed_at;
    const valid = typeof at === 'string' && /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(at)
      && Number.isFinite(Date.parse(at));
    observations.set(id, observations.has(id) ? null : valid ? new Date(at).toISOString() : null);
  }
  const seen = new Set<string>(), clients = new Map<string, ClientReceivables>();
  const excluded = new Map<string, number>();
  let invoiceCount = 0;
  for (const [position, raw] of value.ledger.entries()) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error(`Invalid ledger row ${position + 1}.`);
    const row = raw as Record<string, unknown>;
    const invoice_id = text(row.invoice_id, 'invoice identity');
    if (seen.has(invoice_id)) throw new Error('Duplicate invoice identity in the native ledger.');
    seen.add(invoice_id);
    const invoice_number = text(row.invoice_number, 'invoice number');
    const client_email = text(row.client_email, 'billing email');
    if (row.client_name !== null && typeof row.client_name !== 'string') throw new Error('Invalid native client name.');
    const client_name = row.client_name as string | null;
    const currency = text(row.currency, 'currency');
    if (!/^[A-Z]{3}$/.test(currency)) throw new Error('Invalid native currency code.');
    const status = text(row.status, 'invoice status');
    const amount = decimal(row.balance);
    const dueNumber = row.due_on === null ? null : day(row.due_on, 'invoice due date');
    const due_on = row.due_on as string | null;
    if (!OPEN.has(status) || amount.units === 0n) {
      const reason = OPEN.has(status) ? 'ZERO_BALANCE' : status;
      excluded.set(reason, (excluded.get(reason) || 0) + 1);
      continue;
    }
    const difference = dueNumber === null ? 0 : todayNumber - dueNumber;
    const dueState: DueState = dueNumber === null ? 'undated'
      : difference > 0 ? 'overdue' : difference === 0 ? 'today' : 'later';
    const invoice: ReceivableInvoice = {invoice_id, invoice_number, client_email, client_name,
      currency, balance: row.balance as string, status, due_on, dueState,
      daysOverdue: Math.max(0, difference), recordObservedAt: observations.get(invoice_id) ?? null};
    let client = clients.get(client_email);
    if (!client) { client = {email: client_email, names: [], invoices: []}; clients.set(client_email, client); }
    if (client_name && !client.names.includes(client_name)) client.names.push(client_name);
    client.invoices.push(invoice); invoiceCount++;
  }
  return {today: value.today as string, ledgerCount: value.ledger.length, invoiceCount,
    clients: [...clients.values()].sort((a, b) => compare(a.email, b.email)),
    excluded: [...excluded].sort(([a], [b]) => compare(a, b)).map(([status, count]) => ({status, count}))};
}

/** Filters select existing admitted rows and never mutate their source projection. */
export function receivablesFor(view: Receivables, filter: {email: string | null; due: 'all' | DueState}): ReceivablesSelection {
  if (filter.due !== 'all' && !Object.prototype.hasOwnProperty.call(DUE, filter.due)) throw new Error('Unknown due-state filter.');
  const clients = view.clients.filter(client => filter.email === null || client.email === filter.email)
    .map(client => ({email: client.email, names: [...client.names],
      invoices: client.invoices.filter(invoice => filter.due === 'all' || invoice.dueState === filter.due)
        .map(invoice => ({...invoice}))}))
    .filter(client => client.invoices.length);
  const invoices = clients.flatMap(client => client.invoices);
  return {clients, totals: totals(invoices), invoiceCount: invoices.length};
}


export type AgingBand = 'not-due' | '1-30' | '31-60' | '61-90' | '90-plus' | 'undated';
export type CollectionsDue = 'all' | DueState | AgingBand;
export type CollectionsSort = 'oldest' | 'balance' | 'client';
export type CollectionsFilter = {email: string | null; due: CollectionsDue; currency: string | null; query: string; sort: CollectionsSort};
export type CollectionsSelection = {invoices: ReceivableInvoice[]; totals: CurrencyTotal[]; invoiceCount: number};
export const AGING_BANDS: readonly AgingBand[] = ['not-due', '1-30', '31-60', '61-90', '90-plus', 'undated'];
const BAND_LABELS: Record<AgingBand, string> = {'not-due': 'Not overdue', '1-30': '1–30 days late', '31-60': '31–60 days late',
  '61-90': '61–90 days late', '90-plus': 'Over 90 days late', undated: 'No due date'};
const FILTER_LABELS: Record<CollectionsDue, string> = {...DUE, ...BAND_LABELS, all: 'All open invoices'};
export function agingBand(invoice: ReceivableInvoice): AgingBand {
  if (invoice.dueState === 'undated') return 'undated';
  if (invoice.dueState !== 'overdue') return 'not-due';
  return invoice.daysOverdue <= 30 ? '1-30' : invoice.daysOverdue <= 60 ? '31-60'
    : invoice.daysOverdue <= 90 ? '61-90' : '90-plus';
}
function amountCompare(left: string, right: string): number {
  const a = decimal(left), b = decimal(right), scale = Math.max(a.scale, b.scale);
  const difference = a.units * 10n ** BigInt(scale - a.scale) - b.units * 10n ** BigInt(scale - b.scale);
  return difference < 0n ? -1 : difference > 0n ? 1 : 0;
}
/** A worklist over admitted native balances. Largest-balance sorting stays within each currency. */
export function collectionsFor(view: Receivables, filter: CollectionsFilter): CollectionsSelection {
  const query = filter.query.trim().toLocaleLowerCase();
  const invoices = view.clients.filter(client => filter.email === null || client.email === filter.email)
    .flatMap(client => client.invoices)
    .filter(invoice => (filter.currency === null || invoice.currency === filter.currency)
      && (filter.due === 'all' || invoice.dueState === filter.due || agingBand(invoice) === filter.due)
      && (!query || [invoice.client_name, invoice.client_email, invoice.invoice_number, invoice.invoice_id]
        .some(value => value?.toLocaleLowerCase().includes(query))))
    .map(invoice => ({...invoice}));
  const identity = (a: ReceivableInvoice, b: ReceivableInvoice) =>
    compare(a.client_email, b.client_email) || compare(a.invoice_number, b.invoice_number) || compare(a.invoice_id, b.invoice_id);
  const dueOrder = (a: ReceivableInvoice, b: ReceivableInvoice) =>
    a.due_on === null ? (b.due_on === null ? 0 : 1) : b.due_on === null ? -1 : compare(a.due_on, b.due_on);
  invoices.sort((a, b) => filter.sort === 'balance'
    ? compare(a.currency, b.currency) || -amountCompare(a.balance, b.balance) || dueOrder(a, b) || identity(a, b)
    : filter.sort === 'client' ? compare((a.client_name || a.client_email).toLocaleLowerCase(), (b.client_name || b.client_email).toLocaleLowerCase()) || identity(a, b) || dueOrder(a, b)
    : dueOrder(a, b) || identity(a, b));
  return {invoices, totals: totals(invoices), invoiceCount: invoices.length};
}
export function agingTotals(invoices: ReceivableInvoice[]) {
  return totals(invoices).map(total => ({...total, bands: AGING_BANDS.map(band => {
    const rows = invoices.filter(invoice => invoice.currency === total.currency && agingBand(invoice) === band);
    return {band, invoiceCount: rows.length, balance: totals(rows)[0]?.balance ?? '0'};
  })}));
}
/** The selected worklist only; never a provider statement or a session backup. */
export function collectionsCsv(view: Receivables, selection: CollectionsSelection): LedgerDownload {
  const columns = ['Record type', 'Aging date', 'Invoice ID', 'Invoice number', 'Client name', 'Client email',
    'Currency', 'Open balance', 'Status', 'Due date', 'Days overdue', 'Aging group', 'Provider record received (UTC)'];
  const rows = selection.invoices.map(invoice => ['SANDBOX', view.today, invoice.invoice_id, invoice.invoice_number,
    invoice.client_name || '', invoice.client_email, invoice.currency, invoice.balance, invoice.status,
    invoice.due_on || '', invoice.dueState === 'undated' ? '' : String(invoice.daysOverdue), BAND_LABELS[agingBand(invoice)], invoice.recordObservedAt || 'Not recorded']);
  return {text: '\uFEFF' + [columns.map(value => quoteCsvCell(value)).join(','),
    ...rows.map(row => row.map((value, index) => quoteCsvCell(value, index === 7)).join(','))].join('\r\n') + '\r\n',
    filename: 'ledgerly-collections-sandbox-' + view.today + '.csv', count: rows.length};
}

const escape = (value: string) => value.replace(/[&<>"']/g, character =>
  ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]!));
function moneyMarkup(values: CurrencyTotal[]): string {
  return '<dl class="collections-totals">' + values.map(value => '<div><dt>' + escape(value.currency)
    + ' saved open balance</dt><dd>' + escape(value.balance) + '</dd><small>' + value.invoiceCount
    + ' invoice' + (value.invoiceCount === 1 ? '' : 's') + '</small></div>').join('') + '</dl>';
}
function agingMarkup(invoices: ReceivableInvoice[], active: CollectionsFilter): string {
  if (!invoices.length) return '';
  return '<p class="collections-aging-hint">Scroll sideways to see every age group.</p><div class="collections-aging-scroll" role="region" aria-label="Balances by days past due" tabindex="0">'
    + '<table class="collections-aging"><caption>Days past due · choose an amount to see its invoices</caption><thead><tr><th scope="col">Currency</th>'
    + AGING_BANDS.map(band => '<th scope="col">' + escape(BAND_LABELS[band]) + '</th>').join('') + '</tr></thead><tbody>'
    + agingTotals(invoices).map(row => '<tr><th scope="row">' + escape(row.currency) + '</th>'
      + row.bands.map(value => '<td><button type="button" data-collection-band="' + value.band + '" data-collection-currency="' + escape(row.currency)
        + '" aria-label="' + escape(row.currency + ' ' + value.balance + ', ' + BAND_LABELS[value.band] + ', ' + value.invoiceCount + ' invoices')
        + '" aria-pressed="' + String(active.due === value.band && active.currency === row.currency) + '"' + (value.invoiceCount ? '' : ' disabled')
        + '><strong>' + escape(value.balance) + '</strong><small>' + value.invoiceCount + '</small></button></td>').join('') + '</tr>').join('')
    + '</tbody></table></div>';
}
function worklistMarkup(selection: CollectionsSelection, canOpen: boolean): string {
  return '<div class="collections-table-wrap"><table class="collections-table"><caption class="sr-only">Selected open invoices</caption>'
    + '<thead><tr><th scope="col">Client / invoice</th><th scope="col">Due</th><th scope="col">Saved balance</th>'
    + (canOpen ? '<th scope="col"><span class="sr-only">Review</span></th>' : '') + '</tr></thead><tbody>'
    + selection.invoices.map(invoice => '<tr data-due-state="' + invoice.dueState + '"><td class="collections-client"><strong>'
      + escape(invoice.client_name || invoice.client_email) + '</strong><span>' + escape(invoice.client_email) + '</span><small>'
      + escape(invoice.invoice_number) + ' · ' + escape(invoice.status.replaceAll('_', ' ').toLowerCase()) + '</small></td>'
      + '<td class="collections-due"><strong>' + (invoice.daysOverdue ? invoice.daysOverdue + ' days late' : DUE[invoice.dueState]) + '</strong><span>'
      + escape(invoice.due_on || 'Check the agreed terms') + '</span></td><td class="collections-balance"><span>' + escape(invoice.currency)
      + '</span><strong>' + escape(invoice.balance) + '</strong><small class="collections-observation">'
      + (invoice.recordObservedAt ? 'Provider record received<br><time datetime="' + escape(invoice.recordObservedAt) + '">'
        + escape(invoice.recordObservedAt.slice(0, 16).replace('T', ' ')) + ' UTC</time>' : 'No provider receipt saved')
      + '</small></td>'
      + (canOpen ? '<td class="collections-open"><button type="button" data-collection-open="' + escape(invoice.invoice_id)
        + '" aria-label="' + escape('Review invoice ' + invoice.invoice_number + ' for ' + (invoice.client_name || invoice.client_email))
        + '">Review invoice <span aria-hidden="true">↗</span></button></td>' : '') + '</tr>').join('') + '</tbody></table></div>';
}

export type ReceivablesElements = {
  mount: HTMLElement; note: HTMLElement; client: HTMLSelectElement; due: HTMLSelectElement;
  reset: HTMLButtonElement; summary: HTMLElement; list: HTMLElement;
  currency?: HTMLSelectElement; query?: HTMLInputElement; sort?: HTMLSelectElement;
  download?: HTMLButtonElement; onOpen?: (invoiceId: string) => void;
};
/** Reads the accepted snapshot. Opening a row navigates to its existing invoice controls. */
export function createReceivablesView(elements: ReceivablesElements) {
  let prepared: Receivables | null = null, invalid = '', ready = false, busy = false;
  let filter: CollectionsFilter = {email: null, due: 'all', currency: null, query: '', sort: 'oldest'};
  let downloadData: LedgerDownload | null = null;
  let agingOpen = false;
  const {mount, note, client, due: dueSelect, reset, summary, list, currency, query, sort, download, onOpen} = elements;
  function render() {
    const usable = ready && !busy && prepared !== null && !invalid;
    const focused = summary.ownerDocument.activeElement as HTMLElement | null;
    const focusBand = focused?.dataset.collectionBand, focusCurrency = focused?.dataset.collectionCurrency;
    const agingDetails = summary.querySelector<HTMLDetailsElement>('.collections-aging-details');
    if (agingDetails) agingOpen = agingDetails.open;
    for (const control of [client, dueSelect, reset, currency, query, sort]) {
      if (control) control.disabled = !usable || !prepared?.invoiceCount;
    }
    if (download) download.disabled = true;
    downloadData = null;
    summary.innerHTML = ''; list.innerHTML = '';
    if (!usable) {
      mount.dataset.state = busy ? 'busy' : !ready ? 'unavailable' : 'invalid';
      note.textContent = busy ? 'Updating the sandbox. The worklist returns with the current balances.'
        : !ready ? 'Load the local engine to review open invoices.'
        : invalid ? 'Collections unavailable: ' + invalid : 'Waiting for the current sandbox ledger.';
      if (!prepared) { client.innerHTML = '<option value="">All clients</option>'; client.value = ''; }
      return;
    }
    const current = prepared!;
    mount.dataset.state = current.invoiceCount ? 'ready' : 'empty';
    client.innerHTML = '<option value="">All clients</option>' + current.clients.map((entry, index) =>
      '<option value="' + index + '">' + escape(entry.names[0] ? entry.names[0] + ' · ' + entry.email : entry.email) + '</option>').join('');
    const index = current.clients.findIndex(entry => entry.email === filter.email);
    client.value = filter.email === null || index < 0 ? '' : String(index);
    const currencies = [...new Set(current.clients.flatMap(entry => entry.invoices.map(invoice => invoice.currency)))].sort();
    if (currency) {
      currency.innerHTML = '<option value="">All currencies</option>' + currencies.map(value =>
        '<option value="' + value + '">' + value + '</option>').join('');
      currency.value = filter.currency || '';
    }
    dueSelect.value = filter.due;
    if (query) query.value = filter.query;
    if (sort) sort.value = filter.sort;
    const selected = collectionsFor(current, filter);
    // Keep the age breakdown visible while narrowing to a bucket, so selection retains context.
    const context = collectionsFor(current, {...filter, due: 'all'});
    const invoiceCount = selected.invoiceCount === current.invoiceCount
      ? String(selected.invoiceCount) : selected.invoiceCount + ' of ' + current.invoiceCount;
    note.textContent = 'Aging on ' + current.today + ' · ' + invoiceCount
      + ' open invoice' + (current.invoiceCount === 1 ? '' : 's')
      + (filter.due === 'all' ? '' : ' · ' + FILTER_LABELS[filter.due]) + (filter.currency ? ' · ' + filter.currency : '')
      + (filter.email ? ' · ' + (current.clients.find(entry => entry.email === filter.email)?.names[0] || filter.email) : '')
      + (filter.query.trim() ? ' · Search: ' + filter.query.trim() : '');
    summary.innerHTML = (selected.invoiceCount ? moneyMarkup(selected.totals) : '')
      + (context.invoices.length ? '<details class="collections-aging-details"' + (agingOpen ? ' open' : '')
        + '><summary>Aging by currency <span>Explore days past due</span></summary>'
        + agingMarkup(context.invoices, filter) + '</details>' : '');
    if (!current.invoiceCount) {
      list.innerHTML = '<p class="collections-empty">No open balances.</p>';
    } else if (!selected.invoiceCount) {
      list.innerHTML = '<div class="collections-empty"><p>No invoices match this search and filter selection.</p>'
        + '<button type="button" class="button button-secondary" data-collection-clear>Clear search and filters</button></div>';
    } else {
      list.innerHTML = worklistMarkup(selected, Boolean(onOpen));
      downloadData = collectionsCsv(current, selected);
      if (download) download.disabled = false;
    }
    if (focusBand) {
      Array.from(summary.querySelectorAll<HTMLElement>('[data-collection-band]')).find(button =>
        button.dataset.collectionBand === focusBand && button.dataset.collectionCurrency === focusCurrency)?.focus({preventScroll: true});
    }
  }
  client.addEventListener('change', () => {
    const index = client.value === '' ? -1 : Number(client.value);
    filter.email = prepared && Number.isInteger(index) && index >= 0 ? prepared.clients[index]?.email ?? null : null;
    render();
  });
  dueSelect.addEventListener('change', () => {
    filter.due = Object.prototype.hasOwnProperty.call(FILTER_LABELS, dueSelect.value) ? dueSelect.value as CollectionsDue : 'all';
    render();
  });
  currency?.addEventListener('change', () => { filter.currency = currency.value || null; render(); });
  query?.addEventListener('input', () => { filter.query = query.value; render(); });
  sort?.addEventListener('change', () => { filter.sort = sort.value as CollectionsSort; render(); });
  reset.addEventListener('click', () => {
    filter = {email: null, due: 'all', currency: null, query: '', sort: 'oldest'}; render();
  });
  summary.addEventListener('click', event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-collection-band]');
    if (!button || button.disabled || !ready || busy) return;
    filter.due = button.dataset.collectionBand as AgingBand;
    filter.currency = button.dataset.collectionCurrency || null;
    render();
  });
  list.addEventListener('click', event => {
    const clear = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-collection-clear]');
    if (clear && ready && !busy) { reset.click(); query?.focus(); return; }
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-collection-open]');
    if (!button || button.disabled || !ready || busy || !prepared) return;
    const id = button.dataset.collectionOpen;
    if (id && prepared.clients.some(client => client.invoices.some(invoice => invoice.invoice_id === id))) onOpen?.(id);
  });
  download?.addEventListener('click', () => {
    if (!ready || busy || !downloadData?.count) return;
    let url: string | null = null;
    const anchor = document.createElement('a');
    try {
      url = URL.createObjectURL(new Blob([downloadData.text], {type: 'text/csv;charset=utf-8'}));
      anchor.href = url; anchor.download = downloadData.filename;
      document.body.append(anchor); anchor.click();
      note.textContent = 'Downloaded ' + downloadData.count + ' selected sandbox invoices. Currency and open balance are preserved in each row.';
    } catch { note.textContent = 'The download could not start. Your filters and ledger are unchanged; try again.'; }
    finally { anchor.remove(); if (url) { const released = url; setTimeout(() => URL.revokeObjectURL(released), 1000); } }
  });
  render();
  return {
    setSnapshot(snapshot: unknown) {
      try {
        prepared = projectReceivables(snapshot); invalid = '';
        if (!prepared.clients.some(entry => entry.email === filter.email)) filter.email = null;
        if (!prepared.clients.some(entry => entry.invoices.some(invoice => invoice.currency === filter.currency))) filter.currency = null;
        if (!prepared.invoiceCount) filter = {email: null, due: 'all', currency: null, query: '', sort: 'oldest'};
      } catch (error) {
        prepared = null; invalid = error instanceof Error ? error.message : 'Invalid native snapshot.';
        filter = {email: null, due: 'all', currency: null, query: '', sort: 'oldest'};
      }
      render();
    },
    setAvailability(isReady: boolean, isBusy: boolean) {
      if (ready && !isReady) {
        prepared = null; invalid = '';
        filter = {email: null, due: 'all', currency: null, query: '', sort: 'oldest'};
      }
      ready = isReady; busy = isBusy; render();
    },
  };
}
