import {afterEach, describe, expect, it, vi} from 'vitest';
import {buildInvoiceRecord, createInvoiceRecordDownloads} from '../src/invoice-record';
import {invoiceDetailsMarkup} from '../src/invoice-details';
import detailStyles from '../src/invoice-details.css?raw';

const id = 'INV/one?<exact>';
const stamp = new Date('2026-10-08T14:15:16.123Z');
function source() {
  return {
    today: '2026-10-08',
    ledger: [{invoice_id: id, invoice_number: 'LDG-0001', client_name: '海 😀 Client',
      client_email: 'client@example.test', currency: 'USD', total: '4.20', paid_amount: '1.01',
      balance: '3.19', status: 'PARTIALLY_PAID', sent_on: '2026-10-07', due_on: '2026-10-30', reminders_sent: 2}],
    invoice_details: [{invoice_id: id, record: {
      id, status: 'PROVIDER-STATUS', detail: {invoice_number: 'PROVIDER-0001', invoice_date: '2026-10-01',
        payment_term: {term_type: 'NET_29', due_date: '2026-10-30'}, note: 'First line\n海 😀 & <literal> "quoted"'},
      primary_recipients: [{billing_info: {name: {given_name: 'Original', surname: 'Recipient'},
        email_address: 'retained@example.test', address: {address_line_1: 'Building <A>', admin_area_2: '東京'}}}],
      invoicer: {name: {given_name: 'Sandbox', surname: 'Issuer'}, email_address: 'issuer@example.test'},
      items: [{name: 'Original service', description: 'Two\nliteral lines', quantity: '3.000',
        unit_of_measure: 'HOURS', unit_amount: {currency_code: 'USD', value: '9007199254740993.01'}}],
      amount: {currency_code: 'USD', value: '9007199254740993.03'},
      payments: {paid_amount: {currency_code: 'USD', value: '3.3300'}, transactions: [
        {payment_date: '2026-10-07', amount: {currency_code: 'USD', value: '1.0100'}, type: 'OTHER', method: 'CASH', payment_id: 'PAY-ONE'},
        {payment_date: '2026-10-08', amount: {currency_code: 'USD', value: '2.3200'}, type: 'OTHER', method: 'BANK_TRANSFER', payment_id: 'PAY-TWO'},
      ]},
      due_amount: {currency_code: 'USD', value: '9007199254740989.7000'},
    }}],
    analysis: {client_name: 'PRIVATE ANALYSIS'},
    pending: [{proposal: 'PRIVATE PENDING'}],
    source_email: 'PRIVATE SOURCE EMAIL',
  };
}
const escape = (value: string) => value.replace(/[&<>"']/g,
  character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]!));

describe('standalone retained invoice documents', () => {
  it('keeps the exact existing detail renderer and its local styles beside the distinct ledger summary', () => {
    const snapshot = source(), before = structuredClone(snapshot);
    const output = buildInvoiceRecord(snapshot, id, stamp);
    expect(output.text).toContain(invoiceDetailsMarkup(id, snapshot, true, 'paper'));
    expect(output.text).toContain('<style>' + detailStyles);
    const displayed = [...output.text.matchAll(/<dd data-ledger-field="([^"]+)">([^]*?)<\/dd>/g)];
    expect(displayed.map(match => [match[1], match[2]])).toEqual(Object.entries(snapshot.ledger[0])
      .map(([key, value]) => [key, escape(String(value))]));
    for (const literal of ['9007199254740993.01', '9007199254740993.03', '9007199254740989.7000',
      '3.3300', '1.0100', '2.3200', 'PAY-ONE', 'PAY-TWO', 'PROVIDER-STATUS', 'PROVIDER-0001'])
      expect(output.text).toContain(literal);
    expect(snapshot).toEqual(before);
    expect(output.text).toContain('not a payable invoice');
    expect(output.text).toContain('Sandbox · not payable');
    const lead=output.text.split('<section class="record-billing">')[1].split('</section>')[0];
    expect(lead).toContain('Original Recipient');
    expect(lead).toContain('USD 9,007,199,254,740,989.7000');
    expect(lead).not.toContain('3.19');
    expect(output.text.indexOf('<div class="invoice-paper-body">')).toBeLessThan(output.text.indexOf('<details class="record-support">'));

    expect(output.text).toContain('break-after: avoid');
    expect(output.text).toContain('id="invoice-record-snapshot">2026-10-08</span>');
    expect(output.text).toContain('id="invoice-record-created">2026-10-08T14:15:16.123Z</span>');
    expect(output.filename).toBe('ledgerly-invoice-LDG-0001-Original-Recipient.html');
    expect(output.text).toContain('<title>Invoice LDG-0001 — Original Recipient · Ledgerly Sandbox</title>');
  });

  it('distinguishes native provider copies from default simulation without claiming export effects', () => {
    const simulated = buildInvoiceRecord(source(), id, stamp).text;
    expect(simulated).toContain('saved copy from Ledgerly’s in-memory simulation');
    expect(simulated).toContain('No real money moved.');
    expect(simulated).toContain('Sandbox clock at source snapshot:');
    expect(simulated).not.toContain('Operator provider:');
    for (const provider of ['PayPal Sandbox', 'Synthetic test provider <script>private()</script>']) {
      const native = buildInvoiceRecord({...source(), native_operator: true, provider}, id, stamp).text;
      expect(native).toContain('saved copy from Ledgerly’s native invoice operator');
      expect(native).toContain('Operator provider: ' + escape(provider));
      expect(native).toContain('does not create, send, record payment or refresh provider data');
      expect(native).toContain('not a payable invoice, a payment receipt or a current provider statement');
      expect(native).toContain('Ledger date:');
      expect(native).not.toContain('in-memory simulation');
      expect(native).not.toContain('No real money moved.');
      expect(native).not.toContain('<script>private()');
      expect(native).not.toContain('PRIVATE SOURCE EMAIL');
    }
  });

  it('contains only the selected record, without private source, current analysis, proposals or another invoice', () => {
    const snapshot = source();
    const other = structuredClone(snapshot.invoice_details[0]);
    other.invoice_id = other.record.id = 'INV-other'; other.record.detail.note = 'OTHER INVOICE SECRET';
    snapshot.invoice_details.push(other);
    snapshot.ledger.push({...snapshot.ledger[0], invoice_id: 'INV-other', client_name: 'OTHER LEDGER SECRET'});
    const output = buildInvoiceRecord(snapshot, id, stamp).text;
    for (const absent of ['PRIVATE ANALYSIS', 'PRIVATE PENDING', 'PRIVATE SOURCE EMAIL',
      'OTHER INVOICE SECRET', 'OTHER LEDGER SECRET', 'INV-other']) expect(output).not.toContain(absent);
    expect((output.match(/class="invoice-details"/g) || []).length).toBe(1);
    expect(output).not.toMatch(/<script[^>]+\bsrc=|<link\b|<iframe\b|<form\b|<a\b|\bfetch\s*\(/i);
    expect((output.match(/<button\b/g) || []).length).toBe(1);
    expect(output).toContain('id="print-invoice-record"');
  });

  it('keeps Unicode, multiline descriptions and markup literal without adding executable elements', () => {
    const snapshot = source();
    const hostile = '海 😀 "</script><img src=x onerror=alert(1)>\nsecond\u2028third\u2029fourth';
    snapshot.invoice_details[0].record.detail.note = hostile;
    snapshot.invoice_details[0].record.items[0].description = hostile;
    snapshot.ledger[0].client_name = hostile;
    snapshot.ledger[0].invoice_number = 'LDG "</style><img src=x> line\n2';
    snapshot.invoice_details[0].record.primary_recipients[0].billing_info.name.given_name = hostile;
    const output = buildInvoiceRecord(snapshot, id, stamp).text;
    expect(output).toContain(escape(hostile));
    expect((output.match(/<\/script>/g) || []).length).toBe(1);
    expect(output).not.toContain('<img');
    expect((output.match(/<\/style>/g) || []).length).toBe(1);
    expect(output).toContain('white-space: pre-wrap');
    expect(output).toContain('@media print');
    expect(output).toContain("window.addEventListener('beforeprint', openRecord)");
    expect(output).toContain('window.print()');
  });

  it.each([['USD', '10.20'], ['EUR', '10.2000'], ['JPY', '5000']])(
    'preserves %s original amount text %s without currency conversion', (currency, amount) => {
      const snapshot = source();
      snapshot.ledger[0].currency = currency; snapshot.ledger[0].total = amount;
      snapshot.invoice_details[0].record.amount = {currency_code: currency, value: amount};
      const output = buildInvoiceRecord(snapshot, id, stamp).text;
      expect(output).toContain('<dd data-ledger-field="currency">' + currency + '</dd>');
      expect(output).toContain('<dd data-ledger-field="total">' + amount + '</dd>');
      expect(output).toContain('<dt>Provider invoice total</dt><dd>' + currency + ' ' + amount + '</dd>');
    });


  it.each([
    ['USD', '30', 'USD 30.00'],
    ['JPY', '5000', 'JPY 5,000'],
    ['EUR', '10.2000', 'EUR 10.2000'],
    ['KWD', '1.234', 'KWD 1.234'],
  ])('formats the %s customer headline without rounding its retained value', (currency, value, displayed) => {
    const snapshot = source(), before = structuredClone(snapshot);
    snapshot.invoice_details[0].record.due_amount = {currency_code:currency,value};
    const html = buildInvoiceRecord(snapshot, id, stamp).text;
    const lead = html.split('<section class="record-billing">')[1].split('</section>')[0];
    expect(lead).toContain('<strong>' + displayed + '</strong>');
    expect(html).toContain('<dt>Provider amount due</dt><dd>' + currency + ' ' + value + '</dd>');
    expect(snapshot.ledger).toEqual(before.ledger);
    expect(snapshot.invoice_details[0].record.due_amount).toEqual({currency_code:currency,value});
  });

  it('refuses missing, ambiguous and mismatched retained identities instead of a fallback', () => {
    const cases: any[] = [
      {...source(), ledger: []}, {...source(), invoice_details: []},
      {...source(), ledger: [source().ledger[0], source().ledger[0]]},
      {...source(), invoice_details: [source().invoice_details[0], source().invoice_details[0]]},
      {...source(), invoice_details: [{invoice_id: id, record: {id: 'OTHER'}}]},
      {...source(), invoice_details: [{invoice_id: 'OTHER', record: source().invoice_details[0].record}]},
      {...source(), ledger: {invoice_id: id}}, {...source(), invoice_details: {invoice_id: id}},
    ];
    for (const snapshot of cases) expect(() => buildInvoiceRecord(snapshot, id, stamp)).toThrow(/matching retained/);
    for (const today of [undefined, '', 42]) expect(() => buildInvoiceRecord({...source(), today}, id, stamp)).toThrow(/dated sandbox/);
    expect(() => buildInvoiceRecord(source(), '', stamp)).toThrow(/dated sandbox/);
  });

  it('refuses an oversized literal document or invalid creation time without changing its source', () => {
    const snapshot = source();
    snapshot.invoice_details[0].record.detail.note = '海'.repeat(1_500_000);
    const before = structuredClone(snapshot);
    expect(() => buildInvoiceRecord(snapshot, id, stamp)).toThrow(/4 MiB/);
    expect(snapshot).toEqual(before);
    expect(() => buildInvoiceRecord(source(), id, new Date('invalid'))).toThrow();
  });
});

afterEach(() => {
  if (vi.isFakeTimers()) vi.runOnlyPendingTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
function delivery() {
  vi.useFakeTimers();
  const button = {dataset: {saveInvoiceRecord: id}, disabled: false};
  const messages: string[] = [], blobs: Blob[] = [], downloaded: Array<{href: string; download: string}> = [];
  const allocations = vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => {
    blobs.push(blob); return 'blob:owned/' + blobs.length;
  });
  const revocations = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  let receive!: (event: any) => void;
  const anchors: any[] = [];
  const container = {
    addEventListener: (_name: string, listener: (event: any) => void) => {receive = listener;},
    contains: (node: any) => node === button,
    querySelectorAll: () => [button],
    ownerDocument: {
      defaultView: {open: vi.fn()},
      body: {appendChild: vi.fn()},
      createElement: (name: string) => {
        expect(name).toBe('a');
        const anchor = {href: '', download: '', remove: vi.fn(),
          click() {downloaded.push({href: this.href, download: this.download});}};
        anchors.push(anchor); return anchor;
      },
    },
  };
  const controller = createInvoiceRecordDownloads(container as unknown as HTMLElement, message => messages.push(message));
  const click = () => receive({target: {closest: () => button}});
  return {container, button, messages, blobs, downloaded, allocations, revocations, anchors, controller, click};
}

describe('explicit downloads from the accepted displayed snapshot', () => {
  it('detaches the accepted record and keeps completed bytes fixed across later source and display mutation', async () => {
    const fixture = delivery(), snapshot = source(), accepted = structuredClone(snapshot);
    fixture.controller.update(snapshot); fixture.controller.setAvailability(true, false);
    snapshot.ledger[0].total = 'CHANGED';
    snapshot.invoice_details[0].record.detail.note = 'CHANGED';
    fixture.click();
    expect(fixture.downloaded).toHaveLength(1);
    const saved = await fixture.blobs[0].text();
    expect(saved).toContain(invoiceDetailsMarkup(id, accepted, true, 'paper'));
    expect(saved).toContain('<dd data-ledger-field="total">4.20</dd>');
    fixture.controller.update({...snapshot, ledger: [], invoice_details: []});
    expect(await fixture.blobs[0].text()).toBe(saved);
    expect(fixture.blobs[0].type).toBe('text/html;charset=utf-8');
    expect(fixture.anchors[0].remove).toHaveBeenCalledOnce();
    vi.runOnlyPendingTimers();
    expect(fixture.revocations).toHaveBeenCalledWith(fixture.downloaded[0].href);
  });

  it('keeps native provenance from the accepted snapshot through download and later mode changes', async () => {
    const fixture = delivery();
    const snapshot = {...source(), native_operator: true, provider: 'Synthetic provider A'};
    fixture.controller.update(snapshot);
    fixture.controller.setAvailability(true, false);
    snapshot.native_operator = false; snapshot.provider = 'Changed provider B';
    fixture.click();
    const saved = await fixture.blobs[0].text();
    expect(saved).toContain('Operator provider: Synthetic provider A');
    expect(saved).not.toContain('Changed provider B');
    expect(saved).not.toContain('in-memory simulation');
    fixture.controller.update(source());
    fixture.click();
    expect(await fixture.blobs[1].text()).toContain('in-memory simulation');
    expect(await fixture.blobs[0].text()).toBe(saved);
  });

  it('blocks forged clicks while busy or inactive, while an unchanged ready snapshot can still be explicitly saved', () => {
    const fixture = delivery(); fixture.controller.update(source());
    for (const state of [[false, false], [true, true], [false, true]]) {
      fixture.controller.setAvailability(state[0], state[1]);
      expect(fixture.button.disabled).toBe(true);
      fixture.button.disabled = false; fixture.click();
      expect(fixture.allocations).not.toHaveBeenCalled();
    }
    fixture.controller.setAvailability(true, false);
    expect(fixture.button.disabled).toBe(false);
    fixture.click(); expect(fixture.downloaded).toHaveLength(1);
  });

  it('clears old availability after an identity gap, an empty reset or an unknown selection', () => {
    const fixture = delivery(); fixture.controller.update(source()); fixture.controller.setAvailability(true, false);
    const validMarkup = fixture.controller.markup(id);
    expect(validMarkup).toContain('data-save-invoice-record="' + escape(id) + '"');
    expect(validMarkup).not.toContain(' disabled');
    for (const next of [
      {...source(), invoice_details: []},
      {...source(), ledger: [source().ledger[0], source().ledger[0]]},
      {today: '2026-10-08', ledger: [], invoice_details: []},
    ]) {
      fixture.controller.update(next);
      expect(fixture.button.disabled).toBe(true);
      expect(fixture.controller.markup(id)).toContain(' disabled');
      fixture.button.disabled = false; fixture.click();
      expect(fixture.allocations).not.toHaveBeenCalled();
    }
    fixture.controller.update(source());
    fixture.button.dataset.saveInvoiceRecord = 'UNKNOWN'; fixture.button.disabled = false; fixture.click();
    expect(fixture.allocations).not.toHaveBeenCalled();
  });

  it('reports a refused Blob URL allocation and allows a later explicit retry against the same accepted record', async () => {
    const fixture = delivery(), snapshot = source(), before = structuredClone(snapshot);
    fixture.controller.update(snapshot); fixture.controller.setAvailability(true, false);
    fixture.allocations.mockImplementationOnce(() => {throw new Error('Injected allocation refusal');});
    fixture.click();
    expect(fixture.downloaded).toEqual([]);
    expect(fixture.messages.at(-1)).toContain('Could not start');
    expect(fixture.messages.at(-1)).toContain('snapshot is unchanged');
    expect(snapshot).toEqual(before);
    fixture.click();
    expect(fixture.downloaded).toHaveLength(1);
    expect(await fixture.blobs[0].text()).toContain(invoiceDetailsMarkup(id, snapshot, true, 'paper'));
    expect(fixture.messages.at(-1)).toContain('download started');
  });
});

it('retains the selected observation date through download selection without conflating export time', () => {
  const snapshot = {...source(), today: '2026-11-13', native_operator: true, provider: 'Current operator'};
  Object.assign(snapshot.invoice_details[0], {observation: {
    observed_at: '2026-11-10T12:34:56+00:00', provider: 'Saved original provider',
  }});
  const before = structuredClone(snapshot);
  const html = buildInvoiceRecord(snapshot, id, new Date('2026-11-14T15:16:17Z')).text;
  expect(html).toContain('Ledger date: <span id="invoice-record-snapshot">2026-11-13</span>');
  expect(html).toContain('id="invoice-record-observed">2026-11-10T12:34:56+00:00</span>');
  expect(html).toContain('Provider record last received (UTC): 2026-11-10T12:34:56+00:00');
  expect(html).toContain('Record source: Saved original provider');
  expect(html).toContain('Operator provider: Current operator');
  expect(html).toContain('id="invoice-record-created">2026-11-14T15:16:17.000Z</span>');
  expect(html).not.toContain('Date at source snapshot');
  expect(html).not.toContain('Sandbox clock');
  expect(snapshot).toEqual(before);
});

describe('completed invoice decision handoff', () => {
  it('retains selected completed reasons without leaking pending payloads or other invoices', () => {
    const completed_reviews = [
      {id: 'declined-1', invoice_id: id, kind: 'send_invoice', status: 'REJECTED',
       created_at: '2026-10-07T11:00:00Z', summary: 'Send reviewed invoice',
       result: {reason: 'Wait for <purchase order>'}, payload: {private: 'PROPOSAL SECRET'}},
      {id: 'cancel-1', invoice_id: id, kind: 'cancel_invoice', status: 'APPROVED',
       created_at: '2026-10-08T11:00:00Z', summary: 'Cancel unpaid invoice',
       result: {reason: 'Duplicate <script>attack()</script>'}},
      {id: 'other', invoice_id: 'OTHER', kind: 'cancel_invoice', status: 'APPROVED',
       result: {reason: 'OTHER DECISION SECRET'}},
      {id: 'pending', invoice_id: id, kind: 'send_invoice', status: 'PENDING',
       summary: 'PENDING DECISION SECRET'},
    ];
    for (const snapshot of [
      {...source(), completed_reviews},
      {...source(), native_operator: true, decisions: completed_reviews},
    ]) {
      const html = buildInvoiceRecord(snapshot, id, stamp).text;
      for (const present of ['Completed invoice decisions', 'declined-1', 'cancel-1',
        'Wait for &lt;purchase order&gt;', 'Duplicate &lt;script&gt;attack()&lt;/script&gt;',
        'Review opened (UTC)', 'not completion times']) expect(html).toContain(present);
      for (const absent of ['PROPOSAL SECRET', 'OTHER DECISION SECRET', 'PENDING DECISION SECRET',
        '<script>attack()']) expect(html).not.toContain(absent);
    }
  });
  it('distinguishes absent history from an available empty record', () => {
    expect(buildInvoiceRecord(source(), id, stamp).text).toContain('Decision history was not included');
    expect(buildInvoiceRecord({...source(), completed_reviews: []}, id, stamp).text)
      .toContain('No completed decisions were recorded');
  });
});

it('keeps completed decision metadata through the actual cached download controller', async () => {
  for (const native of [false, true]) {
    const fixture = delivery();
    const history = [{id: 'saved-decision', invoice_id: id, kind: 'cancel_invoice',
      status: 'APPROVED', created_at: '2026-10-08T12:00:00Z', summary: 'Cancel original invoice',
      result: {reason: 'Order cancelled after client review'}, payload: {secret: 'OMIT PAYLOAD'}}];
    const snapshot = {...source(), native_operator: native, completed_reviews: history, decisions: history};
    fixture.controller.update(snapshot); fixture.controller.setAvailability(true, false);
    history[0].result.reason = 'LATER CHANGE';
    fixture.click();
    const saved = await fixture.blobs[0].text();
    expect(saved).toContain('saved-decision');
    expect(saved).toContain('Order cancelled after client review');
    expect(saved).not.toContain('LATER CHANGE');
    expect(saved).not.toContain('OMIT PAYLOAD');
    vi.restoreAllMocks(); vi.useRealTimers();
  }
});

it('uses a bounded readable invoice and client filename without path or control characters', () => {
  const snapshot = source();
  snapshot.ledger[0].invoice_number = '../PO/42:*?';
  snapshot.invoice_details[0].record.primary_recipients[0].billing_info.name = {
    given_name: '東京 / Stage',
    surname: 'Company',
  };
  const output = buildInvoiceRecord(snapshot, id, stamp);
  expect(output.filename).toContain('PO-42');
  expect(output.filename).toContain('東京');
  expect(output.filename).not.toMatch(/[<>:"\/\\\\|?*\u0000-\u001f\u007f]/);
  expect(output.filename.endsWith('.html')).toBe(true);
  snapshot.ledger[0].invoice_number = '海'.repeat(200);
  snapshot.invoice_details[0].record.primary_recipients[0].billing_info.name.given_name = '😀'.repeat(200);
  expect(new TextEncoder().encode(buildInvoiceRecord(snapshot, id, stamp).filename).byteLength).toBeLessThan(200);
});

it('opens only the accepted selected record for printing and leaves the invoice unchanged', () => {
  const fixture = delivery(), snapshot = source(), accepted = structuredClone(snapshot);
  const write = vi.fn(), print = vi.fn(), focus = vi.fn();
  let loaded: (() => void) | undefined;
  const preview = {opener: {}, document: {open: vi.fn(() => {loaded = undefined;}), write, close: vi.fn()}, focus, print,
    addEventListener: (_event: string, callback: () => void) => {loaded = callback;}};
  fixture.container.ownerDocument.defaultView.open.mockReturnValue(preview);
  Object.assign(fixture.button.dataset, {printInvoiceRecord: id});
  fixture.controller.update(snapshot); fixture.controller.setAvailability(true, false);
  snapshot.invoice_details[0].record.detail.note = 'LATER EDIT';
  fixture.click();
  expect(write).toHaveBeenCalledOnce();
  expect(write.mock.calls[0][0]).toContain(invoiceDetailsMarkup(id, accepted, true, 'paper'));
  expect(write.mock.calls[0][0]).not.toContain('LATER EDIT');
  expect(write.mock.calls[0][0]).not.toContain('PRIVATE SOURCE EMAIL');
  expect(preview.opener).toBeNull();
  loaded?.();
  expect(print).toHaveBeenCalledOnce();
  expect(fixture.downloaded).toEqual([]);
  expect(fixture.messages.at(-1)).toContain('Save as PDF');
});

it('keeps a blocked print window recoverable through the same explicit download', () => {
  const fixture = delivery();
  Object.assign(fixture.button.dataset, {printInvoiceRecord: id});
  fixture.controller.update(source()); fixture.controller.setAvailability(true, false);
  fixture.container.ownerDocument.defaultView.open.mockReturnValue(null);
  fixture.click();
  expect(fixture.messages.at(-1)).toContain('Allow pop-ups');
  expect(fixture.allocations).not.toHaveBeenCalled();
  delete (fixture.button.dataset as Record<string, string>).printInvoiceRecord;
  fixture.click();
  expect(fixture.downloaded).toHaveLength(1);
});
