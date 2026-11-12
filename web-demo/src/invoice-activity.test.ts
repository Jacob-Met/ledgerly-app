import {describe,it,expect} from 'vitest';
import {invoiceActivityMarkup} from './invoice-activity';
describe('invoice activity',()=>{
 const queued={event:'queued',kind:'send_invoice',action:'a12',invoice:'i12',at:'2026-11-11T11:00:00Z'};
 const ledger=[{invoice_id:'i12',invoice_number:'LDG-0012',client_name:'Arden',client_email:'arden11@example.test'}];
 it('identifies the exact invoice and opens only its current pending review',()=>{
  const html=invoiceActivityMarkup([queued],{ledger,pending:[{id:'a12',invoice_id:'i12',status:'PENDING'}]});
  expect(html).toContain('Send awaiting approval');
  expect(html).toContain('No action has been taken');
  expect(html).toContain('LDG-0012 · Arden · arden11@example.test');
  expect(html).toContain('data-activity-review="a12"');
  expect(html).toContain('data-activity-invoice="i12"');
  expect(html).toContain('2026-11-11 11:00');
 });
 it('keeps the historical request truthful after rejection without reviving the action',()=>{
  const html=invoiceActivityMarkup([queued],{ledger,pending:[],completed_reviews:[{id:'a12',invoice_id:'i12',status:'REJECTED'}]});
  expect(html).toContain('Send review requested');
  expect(html).toContain('This review was rejected.');
  expect(html).not.toContain('awaiting approval');
  expect(html).not.toContain('data-activity-review');
  expect(html).toContain('data-activity-invoice="i12"');
 });
 it('does not link a stale or differently bound action as current',()=>{
  const html=invoiceActivityMarkup([queued],{pending:[{id:'a12',invoice_id:'another',status:'PENDING'}]});
  expect(html).not.toContain('data-activity-review');
  expect(html).not.toContain('awaiting approval');
 });
 it('identifies completion events through the retained action when the event has no invoice field',()=>{
  const html=invoiceActivityMarkup([{event:'rejected',action:'a12'}],{ledger,completed_reviews:[{id:'a12',invoice_id:'i12',kind:'send_invoice',status:'REJECTED'}]});
  expect(html).toContain('arden11@example.test');
  expect(html).toContain('data-activity-invoice="i12"');
  expect(html).not.toContain('data-activity-review');
 });
 it('shows the latest meaningful event first without changing the retained order',()=>{
  const rows=[queued,{event:'rejected',action:'a12'}];
  const html=invoiceActivityMarkup(rows);
  expect(html.indexOf('Review rejected')).toBeLessThan(html.indexOf('Send review requested'));
  expect(rows[0]).toBe(queued);
 });
 it('keeps failures visible while suppressing successful internal calls',()=>{
  expect(invoiceActivityMarkup([{event:'tool',ok:true}])).toContain('Invoice decisions will appear here');
  expect(invoiceActivityMarkup([{event:'tool',ok:false}])).toContain('needs attention');
 });
 it('escapes identity and timestamps without rendering unknown event names',()=>{
  const html=invoiceActivityMarkup([{event:'<img>',invoice:'i12',at:'<script>'}],{ledger:[{...ledger[0],client_email:'<script>email</script>'}]});
  expect(html).not.toContain('<script>');
  expect(html).not.toContain('<img>');
  expect(html).toContain('&lt;script&gt;email');
 });
});
