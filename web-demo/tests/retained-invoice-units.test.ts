import {describe,it,expect} from 'vitest';
import {invoiceDetailsMarkup} from '../src/invoice-details';
function snapshot(description:string,unit='QUANTITY'){
 return {invoice_details:[{invoice_id:'one',record:{id:'one',items:[{name:'Consulting',description,quantity:'3',unit_of_measure:unit,unit_amount:{currency_code:'GBP',value:'225'}}]}}]};
}
describe('retained count units on customer paper',()=>{
 it('uses only the literal provider description for the named count unit',()=>{
  const paper=invoiceDetailsMarkup('one',snapshot('Unit: sessions'),true,'paper');
  expect(paper).toContain('>sessions</td>');expect(paper).not.toContain('>items</td>');
  expect(paper).toContain('GBP 675.00');expect(paper.split('<details class="invoice-provider-record">')[0]).not.toContain('class="invoice-detail-description">Unit: sessions');
  const raw=invoiceDetailsMarkup('one',snapshot('Unit: sessions'));
  expect(raw).toContain('Unit: sessions');expect(raw).toContain('QUANTITY');
 });
 it('keeps unrelated descriptions and hour facts without inferring a count noun',()=>{
  const ordinary=invoiceDetailsMarkup('one',snapshot('Three sessions agreed'),true,'paper');
  expect(ordinary).toContain('Three sessions agreed');expect(ordinary).toContain('>items</td>');
  const hours=invoiceDetailsMarkup('one',snapshot('Unit: sessions','HOURS'),true,'paper');
  expect(hours).toContain('>hours</td>');expect(hours).toContain('Unit: sessions');
 });
 it('escapes the retained unit and rejects multiline metadata interpretations',()=>{
  const paper=invoiceDetailsMarkup('one',snapshot('Unit: <sessions & guides>'),true,'paper');
  expect(paper).toContain('&lt;sessions &amp; guides&gt;');expect(paper).not.toContain('<sessions');
  expect(invoiceDetailsMarkup('one',snapshot('Unit: sessions\nOther terms'),true,'paper')).toContain('>items</td>');
 });
});
