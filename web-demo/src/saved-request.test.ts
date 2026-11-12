import {describe,it,expect} from 'vitest';
import {savedRequestMarkup} from './saved-request';
describe('saved request selection',()=>{
 it('offers only an explicitly retained source for the selected invoice',()=>{
  const state={native_operator:true,invoice_requests:['one']};
  expect(savedRequestMarkup('one',state)).toContain('data-saved-request="one"');
  expect(savedRequestMarkup('another',state)).toBe('');
  expect(savedRequestMarkup('one',{...state,native_operator:false})).toContain('data-saved-request');
 });
 it('does not guess from an active analysis or an old invoice',()=>{
  expect(savedRequestMarkup('one',{native_operator:true,analysis:{source_text:'later request'}})).toBe('');
 });
 it('escapes invoice identity without placing source text in the card',()=>{
  const id='"><img src=x>';
  const html=savedRequestMarkup(id,{native_operator:true,invoice_requests:[id],source_text:'private original'});
  expect(html).toContain('&quot;&gt;&lt;img');
  expect(html).not.toContain('<img');
  expect(html).not.toContain('private original');
 });
});
