import {describe,it,expect,vi} from 'vitest';
import {savedBillingContacts,billingContactOptions,applyBillingContact} from '../src/billing-contact';
describe('explicit saved billing contact',()=>{
 it('keeps distinct named billing contacts while deduplicating repeated invoices',()=>{
  const rows=[{client_name:'Northbank',client_email:'billing@example.test'},{client_name:'Northbank',client_email:'BILLING@example.test'},{client_name:'Northbank',client_email:'accounts@example.test'},{client_name:'Other department',client_email:'billing@example.test'},{client_name:'',client_email:'unknown@example.test'}];
  const original=structuredClone(rows),contacts=savedBillingContacts({ledger:rows});
  expect(contacts).toHaveLength(3);expect(rows).toEqual(original);
  expect(new Set(contacts.map(c=>c.key)).size).toBe(3);
  expect(savedBillingContacts(null)).toEqual([]);
 });
 it('keeps literal names and addresses in visible choices',()=>{
  const contacts=savedBillingContacts({ledger:[{client_name:'A & <B>',client_email:'billing@example.test'}]});
  const html=billingContactOptions(contacts);
  expect(html).toContain('A &amp; &lt;B&gt; · billing@example.test');
  expect(html).not.toContain('<B>');expect(html).toContain('<option value="">Choose a saved contact');
  expect(html).not.toContain('selected');
 });
 it('changes only the explicitly chosen name/email and emits an ordinary edit',()=>{
  const name={value:'Old'},email={value:'old@example.test',dispatchEvent:vi.fn()};
  const form={elements:{namedItem:(key:string)=>key==='client_name'?name:email}};
  applyBillingContact(form as unknown as HTMLFormElement,{key:'one',name:'Northbank',email:'accounts@example.test'});
  expect(name.value).toBe('Northbank');expect(email.value).toBe('accounts@example.test');
  expect(email.dispatchEvent).toHaveBeenCalledOnce();
  const event=email.dispatchEvent.mock.calls[0][0];expect(event.type).toBe('input');expect(event.bubbles).toBe(true);
 });
});
