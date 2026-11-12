import {expect,it} from 'vitest';
import {correctionDraftStore,type CorrectionDraft} from './correction-draft';
const draft:CorrectionDraft={invoice_id:'INV-A',revision:'original',recipient_name:'Arden',recipient_email:'arden@example.test',terms_kind:'net',terms_value:'30',note:'PO AW-1042\n<Project> & café',items:[{name:'Consulting',quantity:'2',unit_price:'85',unit:'HOURS',source_index:0},{name:'Extra work',quantity:'1',unit_price:'90',unit:'QUANTITY',source_index:null}]};
function storage(){const data=new Map<string,string>();return {getItem:(k:string)=>data.get(k)??null,setItem:(k:string,v:string)=>{data.set(k,v);},removeItem:(k:string)=>{data.delete(k);}};}
it('reopens exact unfinished fields without carrying confirmation or provider actions',()=>{
 const data=storage();correctionDraftStore(data,'one').save(draft);
 const resumed=correctionDraftStore(data,'one').resume('INV-A','original');
 expect(resumed).toEqual(draft);expect(resumed).not.toHaveProperty('confirmed');
});
it('isolates invoices and native operator sessions',()=>{
 const data=storage(),store=correctionDraftStore(data,'one');store.save(draft);
 expect(store.resume('INV-B','original')).toBeNull();
 expect(correctionDraftStore(data,'two').resume('INV-A','original')).toBeNull();
 expect(store.read('INV-A')).toEqual(draft);
});
it('retires stale edits and explicit discards without changing the saved invoice',()=>{
 const data=storage(),store=correctionDraftStore(data,'one');store.save(draft);
 expect(store.resume('INV-A','new revision')).toBeNull();expect(store.read('INV-A')).toBeNull();
 store.save(draft);store.remove('INV-A');expect(store.read('INV-A')).toBeNull();
});
it('does not reopen malformed retained fields',()=>{
 const data=storage(),store=correctionDraftStore(data,'one');
 data.setItem('ledgerly.correction.one.INV-A','{');expect(store.read('INV-A')).toBeNull();
 data.setItem('ledgerly.correction.one.INV-A',JSON.stringify({...draft,items:[{...draft.items[0],source_index:'wrong'}]}));
 expect(store.read('INV-A')).toBeNull();
});
