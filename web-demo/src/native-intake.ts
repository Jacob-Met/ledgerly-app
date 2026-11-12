import {WorkspaceStore} from './workspace-store';
import {decodeIntakeFile,encodeIntakeFile,MAX_INTAKE_BYTES,copyChargeChoices,type ChargeChoice,type IntakeFile,type IntakeSnapshot} from './intake-file';
import {syncSourceResolutions} from './review';

export type NativeIntake={intake:IntakeFile;choices:ChargeChoice[]};

export function readNativeChoices(form:HTMLFormElement|null):ChargeChoice[] {
 return form?Array.from(form.querySelectorAll<HTMLElement>('[data-source-amount]')).map(row=>({
  id:row.dataset.sourceAmount!,quote:row.querySelector('p')?.textContent||'',
  selected:row.querySelector<HTMLSelectElement>('select')!.value,
  reason:row.querySelector<HTMLTextAreaElement>('textarea')!.value,
 })):[];
}

/** Tab recovery stores editable choices, never a check, token or approval. */
export function encodeNativeIntake(snapshot:IntakeSnapshot,form:HTMLFormElement|null):string {
 const intake=JSON.parse(encodeIntakeFile(snapshot).text);
 const choices=readNativeChoices(form);
 const text=JSON.stringify({format:'ledgerly-native-intake',version:1,intake,choices});
 if(new TextEncoder().encode(text).length>MAX_INTAKE_BYTES)throw new Error('Unfinished intake exceeds the tab storage limit.');
 return text;
}

export function decodeNativeIntake(raw:string):NativeIntake {
 if(new TextEncoder().encode(raw).length>MAX_INTAKE_BYTES)throw new Error('Unfinished intake exceeds the tab storage limit.');
 const value=JSON.parse(raw);
 // Existing tabs contain the ordinary intake file directly.
 if(value?.format==='ledgerly-intake'){const intake=decodeIntakeFile(raw);return {intake,choices:intake.source_choices||[]};}
 if(value?.format!=='ledgerly-native-intake'||value.version!==1||!Array.isArray(value.choices)||value.choices.length>200)
  throw new Error('Unsupported unfinished tab intake.');
 const intake=decodeIntakeFile(JSON.stringify(value.intake));
 const choices=copyChargeChoices(value.choices);
 return {intake,choices};
}

/** Apply only after restoring this snapshot's source and fields and rerendering requirements. */
export function restoreNativeChoices(form:HTMLFormElement,choices:ChargeChoice[]):void {
 syncSourceResolutions(form);
 for(const row of Array.from(form.querySelectorAll<HTMLElement>('[data-source-amount]'))){
  const match=choices.find(choice=>choice.id===row.dataset.sourceAmount&&choice.quote===(row.querySelector('p')?.textContent||''));
  if(!match)continue;
  const select=row.querySelector<HTMLSelectElement>('select')!;
  if(!Array.from(select.options).some(option=>option.value===match.selected))continue;
  select.value=match.selected;
  row.querySelector<HTMLTextAreaElement>('textarea')!.value=match.reason;
 }
 syncSourceResolutions(form);
}

/** The existing transactional workspace store keeps one native intake per operator. */
export class NativeIntakeStore {
 private revision=0;
 private comparable='';
 private pending:string|undefined;
 private writing:Promise<void>|null=null;
 private failure:Error|null=null;
 private timer:ReturnType<typeof setTimeout>|undefined;
 constructor(private readonly store:WorkspaceStore,private readonly changed:(state:'saving'|'saved'|'conflict'|'error')=>void){}
 private compare(raw:string):string{
  const value=decodeNativeIntake(raw);
  return JSON.stringify({source:value.intake.source_text,fields:value.intake.review_fields,choices:value.choices});
 }
 async load():Promise<NativeIntake|null>{
  clearTimeout(this.timer);
  if(this.writing)await this.writing;
  this.pending=undefined;this.failure=null;
  const saved=await this.store.transact(record=>({result:record}));
  const decoded=saved?decodeNativeIntake(saved.checkpoint):null;
  this.revision=saved?.revision??0;
  this.comparable=this.compare(saved?.checkpoint??encodeNativeIntake({sourceText:'',fields:null},null));
  this.changed('saved');
  return decoded;
 }
 queue(raw:string):void{
  if(this.compare(raw)===this.comparable&&this.pending===undefined&&!this.writing)return;
  this.pending=raw;
  if(this.failure)return;
  this.changed('saving');clearTimeout(this.timer);
  this.timer=setTimeout(()=>{void this.flush();},180);
 }
 async flush():Promise<boolean>{
  clearTimeout(this.timer);
  if(this.failure)return false;
  if(!this.writing){
   this.writing=(async()=>{
    try{
     do{
      const raw=this.pending;this.pending=undefined;
      const saved=await this.store.transact(record=>{
       if((record?.revision??0)!==this.revision)throw new NativeIntakeConflict();
       if(raw===undefined||this.compare(raw)===this.comparable)return {result:record};
       const next={version:1 as const,revision:this.revision+1,savedAt:new Date().toISOString(),checkpoint:raw,intake:null};
       return {record:next,result:next};
      });
      this.revision=saved?.revision??0;
      if(saved)this.comparable=this.compare(saved.checkpoint);
     }while(this.pending!==undefined);
     this.changed('saved');
    }catch(error){
     this.failure=error instanceof Error?error:new Error(String(error));
     this.changed(error instanceof NativeIntakeConflict?'conflict':'error');
    }
   })().finally(()=>{this.writing=null;});
  }
  await this.writing;
  // Input can arrive after the last transaction but before its promise settles.
  if(this.pending!==undefined&&!this.failure)return this.flush();
  return !this.failure;
 }
 async clear():Promise<boolean>{
  this.queue(encodeNativeIntake({sourceText:'',fields:null},null));
  return this.flush();
 }
}
class NativeIntakeConflict extends Error {
 constructor(){super('Another tab saved a newer unfinished request.');}
}
