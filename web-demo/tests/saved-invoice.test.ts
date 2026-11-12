import {describe,it,expect} from 'vitest';
import {displayMoney,savedInvoiceSummaryMarkup} from '../src/saved-invoice';
describe('saved invoice financial display',()=>{
 it('pads display precision without rounding, converting or losing large decimals',()=>{
  expect(displayMoney('632.5','USD')).toBe('USD 632.50');
  expect(displayMoney('9007199254740993.125','USD')).toBe('USD 9,007,199,254,740,993.125');
  expect(displayMoney('250','JPY')).toBe('JPY 250');
  expect(displayMoney('1e+6','EUR')).toBe('EUR 1e+6');
 });
 it('keeps total paid and balance distinct and does not present cancelled money as collectible',()=>{
  const entry={invoice_number:'LDG-42',client_name:'Mira',client_email:'mira@example.test',currency:'USD',total:'675',paid_amount:'125.25',balance:'549.75',status:'PARTIALLY_PAID',due_on:'2026-12-01'};
  const markup=savedInvoiceSummaryMarkup(entry);
  expect(markup).toContain('USD 675.00');expect(markup).toContain('USD 125.25');expect(markup).toContain('USD 549.75');expect(markup).toContain('Part paid');
  const cancelled=savedInvoiceSummaryMarkup({...entry,status:'CANCELLED'});
  expect(cancelled).toContain('Cancelled amount');expect(cancelled).not.toContain('Amount due');
 });
 it('keeps a simple unpaid header concise while retaining distinct or unknown amounts',()=>{
  const entry={currency:'GBP',total:'150',balance:'150.00',paid_amount:'0.00',status:'DRAFT'};
  const markup=savedInvoiceSummaryMarkup(entry);
  expect(markup.match(/GBP 150.00/g)).toHaveLength(1);
  expect(markup).not.toContain('<dt>Paid</dt>');
  expect(savedInvoiceSummaryMarkup({...entry,balance:'125'})).toContain('<dt>Invoice total</dt>');
  expect(savedInvoiceSummaryMarkup({...entry,paid_amount:undefined})).toContain('<dt>Paid</dt>');
 });
 it('renders customer and invoice values as text rather than executable markup',()=>{
  const markup=savedInvoiceSummaryMarkup({client_name:'<img src=x onerror=alert(1)>',invoice_number:'<script>',currency:'USD',total:'1',paid_amount:'0',balance:'1',status:'SENT'});
  expect(markup).not.toContain('<img');expect(markup).not.toContain('<script>');expect(markup).toContain('&lt;img');
 });
});
