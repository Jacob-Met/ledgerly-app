export type CorrectionDraft = {
 invoice_id:string; revision:string; recipient_name:string; recipient_email:string;
 terms_kind:string; terms_value:string; note:string;
 items:Array<{name:string;quantity:string;unit_price:string;unit:string;source_index:number|null}>;
};
type StorageLike=Pick<Storage,'getItem'|'setItem'|'removeItem'>;
export function correctionDraftStore(storage:StorageLike,scope:string){
 const key=(id:string)=>'ledgerly.correction.'+scope+'.'+encodeURIComponent(id);
 function read(id:string):CorrectionDraft|null{
  const raw=storage.getItem(key(id));if(!raw)return null;
  try{
   const v=JSON.parse(raw);
   if(v?.invoice_id!==id||typeof v.revision!=='string'||!Array.isArray(v.items)||v.items.length<1||v.items.length>100)return null;
   if(!['recipient_name','recipient_email','terms_kind','terms_value','note'].every(k=>typeof v[k]==='string'))return null;
   if(!v.items.every((r:any)=>r&&['name','quantity','unit_price','unit'].every(k=>typeof r[k]==='string')&&(r.source_index===null||Number.isInteger(r.source_index))))return null;
   return v;
  }catch{return null;}
 }
 return {read,save:(value:CorrectionDraft)=>storage.setItem(key(value.invoice_id),JSON.stringify(value)),
  remove:(id:string)=>storage.removeItem(key(id)),
  resume:(id:string,revision:string)=>{const v=read(id);if(v&&v.revision!==revision){storage.removeItem(key(id));return null;}return v;}
};
}
