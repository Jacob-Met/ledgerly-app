import {describe, it, expect} from 'vitest';
import {projectReceivables, collectionsFor, agingTotals, collectionsCsv, type CollectionsFilter} from './receivables';

const filter: CollectionsFilter = {email: null, due: 'all', currency: null, query: '', sort: 'oldest'};
function invoice(id: string, daysLate: number | null, extra: Record<string, unknown> = {}) {
  return {invoice_id: id, invoice_number: 'BILL-' + id, client_email: id + '@example.test', client_name: id,
    currency: 'USD', balance: '10.00', status: 'SENT',
    due_on: daysLate === null ? null : new Date(Date.UTC(2026, 10, 10) - daysLate * 86400000).toISOString().slice(0, 10),
    ...extra};
}
const snapshot = (ledger: unknown[]) => ({today: '2026-11-10', ledger});

describe('collections decisions from actual receivable records', () => {
  it('puts the oldest due invoice first across clients; undated invoices remain explicit', () => {
    const view = projectReceivables(snapshot([invoice('future', -3), invoice('recent', 1),
      invoice('undated', null), invoice('old', 91), invoice('today', 0)]));
    const selected = collectionsFor(view, filter);
    expect(selected.invoices.map(row => row.invoice_id)).toEqual(['old', 'recent', 'today', 'future', 'undated']);
    expect(selected.invoices.at(-1)?.daysOverdue).toBe(0);
  });
  it('assigns exact 30/60/90 day boundaries without treating today, future or missing dates as late', () => {
    const view = projectReceivables(snapshot([invoice('future', -1), invoice('today', 0),
      ...[1, 30, 31, 60, 61, 90, 91].map(days => invoice(String(days), days)), invoice('undated', null)]));
    const groups = agingTotals(collectionsFor(view, filter).invoices)[0];
    expect(groups.bands.map(band => [band.band, band.invoiceCount, band.balance])).toEqual([
      ['not-due', 2, '20.00'], ['1-30', 2, '20.00'], ['31-60', 2, '20.00'],
      ['61-90', 2, '20.00'], ['90-plus', 1, '10.00'], ['undated', 1, '10.00'],
    ]);
  });
  it('composes client, age, currency and search before totaling or exporting', () => {
    const view = projectReceivables(snapshot([invoice('keep', 36, {client_name: 'Aster Studio', client_email: 'ap@aster.test', balance: '83.25'}),
      invoice('recent', 4, {client_email: 'ap@aster.test'}), invoice('eur', 36, {client_email: 'ap@aster.test', currency: 'EUR'}),
      invoice('other', 36)]));
    const selected = collectionsFor(view, {...filter, email: 'ap@aster.test', due: '31-60', currency: 'USD', query: 'ASTER'});
    expect(selected.invoices.map(row => row.invoice_id)).toEqual(['keep']);
    expect(selected.totals).toEqual([{currency: 'USD', balance: '83.25', invoiceCount: 1}]);
    expect(collectionsCsv(view, selected).count).toBe(1);
    expect(collectionsCsv(view, selected).text).not.toContain('BILL-other');
  });
  it('uses remaining balances and never pools or compares different currencies as one amount', () => {
    const raw = snapshot([invoice('paid', 50, {status: 'PAID', balance: '0.00'}),
      invoice('draft', 50, {status: 'DRAFT'}), invoice('partial', 31, {status: 'PARTIALLY_PAID', balance: '0.1'}),
      invoice('usd', 31, {balance: '0.2'}), invoice('eur', 31, {currency: 'EUR', balance: '9000.00'})]);
    const view = projectReceivables(raw), before = JSON.stringify(view);
    const selected = collectionsFor(view, {...filter, sort: 'balance'});
    expect(selected.invoices.map(row => row.invoice_id)).toEqual(['eur', 'usd', 'partial']);
    expect(selected.totals).toEqual([{currency: 'EUR', balance: '9000.00', invoiceCount: 1},
      {currency: 'USD', balance: '0.3', invoiceCount: 2}]);
    expect(JSON.stringify(view)).toBe(before);
  });
  it('orders clients by the displayed name rather than their billing mailbox', () => {
    const view = projectReceivables(snapshot([invoice('zebra', 5, {client_name: 'Zebra Studio', client_email: 'accounts@zebra.test'}),
      invoice('acorn', 5, {client_name: 'Acorn Agency', client_email: 'zara@acorn.test'})]));
    expect(collectionsFor(view, {...filter, sort: 'client'}).invoices.map(row => row.invoice_id)).toEqual(['acorn', 'zebra']);
  });
  it('keeps exact large balances when choosing which invoice to review first', () => {
    const view = projectReceivables(snapshot([invoice('less', 5, {balance: '9007199254740993.01'}),
      invoice('more', 5, {balance: '9007199254740993.09'})]));
    expect(collectionsFor(view, {...filter, sort: 'balance'}).invoices.map(row => row.invoice_id)).toEqual(['more', 'less']);
  });
  it('exports literal client content, original monetary strings, snapshot date and sandbox identity', () => {
    const view = projectReceivables(snapshot([invoice('export', 31, {client_name: '=North, "Studio"', balance: '100.50'})]));
    const csv = collectionsCsv(view, collectionsFor(view, filter));
    expect(csv.filename).toBe('ledgerly-collections-sandbox-2026-11-10.csv');
    expect(csv.text).toContain('"SANDBOX","2026-11-10","export","BILL-export","\'=North, ""Studio"""');
    expect(csv.text).toContain('"USD","100.50","SENT","2026-10-10","31","31–60 days late"');
    expect(csv.text.endsWith('\r\n')).toBe(true);
  });
});
