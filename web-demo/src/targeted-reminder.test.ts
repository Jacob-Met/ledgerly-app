import {describe, expect, it} from 'vitest';
import {targetedReminderActions} from './targeted-reminder';

const invoice = (invoice_id: string, status = 'SENT', balance = '100.00', due_on: string|null = '2026-11-08') =>
  ({invoice_id, invoice_number: invoice_id, client_email: invoice_id + '@example.test',
    client_name: 'Fictional client', currency: 'USD', balance, status, due_on});
const snapshot = (ledger: unknown[], pending: unknown[] = []) =>
  ({today: '2026-11-10', ledger, pending});

describe('selected overdue invoice affordance', () => {
  it('offers one explicit approval draft only for open overdue balance', () => {
    const actions = targetedReminderActions(snapshot([
      invoice('old'), invoice('paid', 'PAID'), invoice('future', 'SENT', '1', '2026-11-15'),
      invoice('today', 'SENT', '1', '2026-11-10'), invoice('zero', 'SENT', '0'),
      invoice('undated','SENT','1',null),
    ]));
    expect([...actions.keys()]).toEqual(['old']);
    expect(actions.get('old')).toContain('data-remind="old"');
    expect(actions.get('old')).toContain('without sending');
    expect(actions.get('old')).toContain('2 days overdue');
  });

  it('says awaiting approval only on the matching native pending reminder', () => {
    const actions = targetedReminderActions(snapshot(
      [invoice('a'), invoice('b')],
      [{kind:'send_reminder',invoice_id:'a'}, {kind:'send_invoice',invoice_id:'b'}],
    ));
    expect(actions.get('a')).toContain('awaiting human approval');
    expect(actions.get('a')).not.toContain('data-remind=');
    expect(actions.get('b')).toContain('data-remind="b"');
  });

  it('keeps untrusted reference text escaped in the real button', () => {
    const id = '<evil "foo">';
    const actions = targetedReminderActions(snapshot([invoice(id)]));
    expect(actions.get(id)).toContain('data-remind="&lt;evil &quot;foo&quot;&gt;"');
    expect(actions.get(id)).not.toContain('data-remind="<evil');
  });

  it('does not offer a reminder for incomplete or malformed native ledger facts', () => {
    expect(() => targetedReminderActions(snapshot([invoice('a','SENT','-1')]))).toThrow();
    expect(() => targetedReminderActions(snapshot([invoice('a','SENT','1','2026-02-30')]))).toThrow();
    expect(() => targetedReminderActions({today:'2026-11-10', ledger:[invoice('a')]})).toThrow();
    expect(() => targetedReminderActions(snapshot([invoice('a')],
      [{kind:'send_reminder',invoice_id:123}]))).toThrow();
  });
});
