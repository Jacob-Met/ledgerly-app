import {describe,expect,it} from 'vitest';
import {parseSpoken,wordsToNumber,type Known} from './spoken-note';
const tide:Known={name:'Tidewater Studio',email:'accounts@tidewater.example',rates:[{desc:'Shoot day',unit_price:'950',currency:'USD'},{desc:'Retouched image',unit_price:'18',currency:'USD'}]};
describe('spoken note',()=>{
 it('reads spoken prices',()=>{expect(wordsToNumber(['nine','fifty'])).toBe(950);expect(wordsToNumber(['forty'])).toBe(40);expect(wordsToNumber(['two','hundred'])).toBe(200);});
 it('turns the sentence into lines and fills a missing rate from memory',()=>{
  const p=parseSpoken('invoice Tidewater for two shoot days at nine fifty and forty retouched images',[tide]);
  expect(p.client?.email).toBe('accounts@tidewater.example');
  expect(p.lines.map(l=>[l.qty,l.price,l.filled])).toEqual([[2,950,false],[40,18,true]]);
  expect(p.text).toContain('From: Tidewater Studio <accounts@tidewater.example>');
  expect(p.text).toContain('- 2 x Shoot days @ $950 each');
 });
 it('leaves the rate open for an unknown client',()=>{
  const p=parseSpoken('invoice Harbor for 3 hours of editing',[]);
  expect(p.lines[0].price).toBeNull();expect(p.text).toContain('Client (said aloud): Harbor');
 });
});
