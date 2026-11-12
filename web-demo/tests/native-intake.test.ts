import {describe,it,expect} from 'vitest';
import {decodeNativeIntake} from '../src/native-intake';
import {encodeIntakeFile} from '../src/intake-file';
const snapshot={sourceText:'Invoice Ashdown: optional GBP 85 recording.',fields:null};
const original=encodeIntakeFile(snapshot,'2026-11-12T00:00:00.000Z').text;
const choice={id:'charge-85',quote:'optional GBP 85 recording.',selected:'excluded',reason:'Not approved yet'};
const envelope=(choices:unknown[])=>JSON.stringify({format:'ledgerly-native-intake',version:1,intake:JSON.parse(original),choices});
describe('native unfinished charge choices',()=>{
 it('opens existing tab snapshots without inventing charge decisions',()=>{
  const result=decodeNativeIntake(original);
  expect(result.intake.source_text).toBe(snapshot.sourceText);
  expect(result.choices).toEqual([]);
 });
 it('retains the exact editable choice and unfinished reason without approval state',()=>{
  expect(decodeNativeIntake(envelope([choice])).choices).toEqual([choice]);
  expect(decodeNativeIntake(envelope([{...choice,selected:'',reason:'Waiting for finance'}])).choices[0].reason).toBe('Waiting for finance');
 });
 it('rejects duplicate identities, unsupported choices and injected approval fields',()=>{
  for(const choices of [[choice,choice],[{...choice,selected:'approved'}],[{...choice,review_id:'stale'}],[{...choice,selected:'line:0'}]]){
   expect(()=>decodeNativeIntake(envelope(choices))).toThrow();
  }
 });
 it('still validates the original editable intake and its size',()=>{
  const value=JSON.parse(envelope([choice]));value.intake.review_fields={approval:true};
  expect(()=>decodeNativeIntake(JSON.stringify(value))).toThrow();
  expect(()=>decodeNativeIntake(' '.repeat(2*1024*1024+1))).toThrow();
 });
});

import {NativeIntakeStore,encodeNativeIntake} from '../src/native-intake';
import type {WorkspaceStore,WorkspaceRecord} from '../src/workspace-store';

function memoryStore(){
 let record:WorkspaceRecord|null=null;
 const store={transact:async(change:any)=>{
  const next=change(record,null);
  if(next.record)record=structuredClone(next.record);
  return next.result;
 }} as WorkspaceStore;
 return {store,read:()=>record};
}
const encoded=(text:string)=>encodeNativeIntake({sourceText:text,fields:null},null);

describe('native unfinished workspace revisions',()=>{
 it('reopens the saved editable request through a new store client',async()=>{
  const memory=memoryStore(),first=new NativeIntakeStore(memory.store,()=>{});
  await first.load();first.queue(encoded('Keep this original request'));
  expect(await first.flush()).toBe(true);
  const reopened=new NativeIntakeStore(memory.store,()=>{});
  expect((await reopened.load())?.intake.source_text).toBe('Keep this original request');
 });
 it('keeps the latest input that arrives while a save is in flight',async()=>{
  const memory=memoryStore(),first=new NativeIntakeStore(memory.store,()=>{});
  await first.load();first.queue(encoded('Earlier words'));
  const saving=first.flush();first.queue(encoded('Latest corrected words'));
  expect(await saving).toBe(true);
  expect(decodeNativeIntake(memory.read()!.checkpoint).intake.source_text).toBe('Latest corrected words');
 });
 it('persists a revert to the saved value while the intervening transaction is deferred',async()=>{
  const memory=memoryStore();let release:(()=>void)|undefined;let defer=false;
  const store={transact:async(change:any)=>{
   if(defer){defer=false;await new Promise<void>(resolve=>{release=resolve;});}
   return memory.store.transact(change);
  }} as WorkspaceStore;
  const first=new NativeIntakeStore(store,()=>{});
  await first.load();first.queue(encoded('Original A'));await first.flush();
  defer=true;first.queue(encoded('Intervening B'));const saving=first.flush();
  expect(release).toBeDefined();first.queue(encoded('Original A'));release!();
  expect(await saving).toBe(true);
  const reopened=new NativeIntakeStore(memory.store,()=>{});
  expect((await reopened.load())?.intake.source_text).toBe('Original A');
 });
 it('does not let a stale tab overwrite or resurrect a retired request',async()=>{
  const memory=memoryStore(),events:string[]=[];
  const first=new NativeIntakeStore(memory.store,()=>{}),stale=new NativeIntakeStore(memory.store,s=>events.push(s));
  await first.load();first.queue(encoded('Agreed invoice'));await first.flush();
  await stale.load();expect(await first.clear()).toBe(true);
  stale.queue(encoded('Old tab edits'));expect(await stale.flush()).toBe(false);
  expect(events.at(-1)).toBe('conflict');
  expect(decodeNativeIntake(memory.read()!.checkpoint).intake.source_text).toBe('');
  expect((await stale.load())?.intake.source_text).toBe('');
 });
 it('leaves an unreadable saved checkpoint intact',async()=>{
  const record={version:1,revision:2,savedAt:'now',checkpoint:'invalid JSON',intake:null};
  const store={transact:async(change:any)=>change(record,null).result} as WorkspaceStore;
  await expect(new NativeIntakeStore(store,()=>{}).load()).rejects.toThrow();
  expect(record.checkpoint).toBe('invalid JSON');
 });
});
