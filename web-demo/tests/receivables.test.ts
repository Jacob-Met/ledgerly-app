import {describe, it, expect} from 'vitest';
import {projectReceivables, receivablesFor, collectionsCsv, collectionsFor} from '../src/receivables';
import {receivablesCases} from './receivables-cases';
describe('client receivables', () => {
  for (const entry of receivablesCases) it(entry.name, () => entry.run(projectReceivables, receivablesFor, collectionsCsv, collectionsFor));
});

describe('saved collection observation time', () => {
  const row = {invoice_id:'invoice-a',invoice_number:'A-1',client_email:'a@example.test',client_name:'A',
    currency:'GBP',balance:'728',status:'PARTIALLY_PAID',due_on:'2026-12-10'};
  const detail = (at: unknown, invoice_id='invoice-a') => ({invoice_id,observation:{observed_at:at}});
  const project = (invoice_details: unknown[], today='2026-11-12') => projectReceivables({today,ledger:[row],invoice_details});
  it('retains the received time across a later-day reload and carries it into the exported row', () => {
    const before = project([detail('2026-11-10T10:34:56+02:00')]);
    const reopened = project([detail('2026-11-10T10:34:56+02:00')], '2026-11-13');
    expect(before.clients[0].invoices[0].recordObservedAt).toBe('2026-11-10T08:34:56.000Z');
    expect(reopened.clients[0].invoices[0].recordObservedAt).toBe(before.clients[0].invoices[0].recordObservedAt);
    const selected = collectionsFor(reopened,{email:null,due:'all',currency:null,query:'',sort:'oldest'});
    const csv = collectionsCsv(reopened,selected).text;
    expect(csv).toContain('Provider record received (UTC)');
    expect(csv).toContain('2026-11-10T08:34:56.000Z');
    expect(selected.invoices[0].balance).toBe('728');
  });
  it('does not borrow another invoice receipt or invent one for old, invalid or ambiguous metadata', () => {
    for (const details of [[],[detail('2026-11-10T08:34:56Z','invoice-b')],[detail('not a date')],
      [detail('2026-11-10T08:34:56')],[detail('2026-11-10T08:34:56Z'),detail('2026-11-11T08:34:56Z')]]) {
      expect(project(details).clients[0].invoices[0].recordObservedAt).toBeNull();
    }
  });
  it('updates only when the accepted snapshot contains a newer actual receipt', () => {
    const view=project([detail('2026-11-12T05:16:14Z')]);
    expect(view.clients[0].invoices[0].recordObservedAt).toBe('2026-11-12T05:16:14.000Z');
  });
});
