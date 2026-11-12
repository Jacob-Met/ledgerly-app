import {expect,it} from 'vitest';
import {startOnDeviceSession,completeInvoicePrompt,invoiceSchema} from './local-ai';

it('starts model creation synchronously from the click event and requests constrained JSON', async()=>{
 const events:string[]=[];
 const session={
  async prompt(text:string,options:{responseConstraint:object}){
   events.push('prompt');expect(text).toContain('EMAIL:');
   expect(options.responseConstraint).toEqual(invoiceSchema);
   return '{"client_email":null}';
  },
  destroy(){events.push('destroy');},
 };
 const promise=startOnDeviceSession({LanguageModel:{create(){
  events.push('create');return Promise.resolve(session);
 }}});
 expect(events).toEqual(['create']);
 const created=await promise;
 expect(await completeInvoicePrompt(created,'EMAIL: test')).toBe('{"client_email":null}');
 created.destroy?.();
 expect(events).toEqual(['create','prompt','destroy']);
});

it('rejects absent browser support without attempting any model or network fallback', async()=>{
 await expect(startOnDeviceSession({})).rejects.toThrow('does not expose the on-device Prompt API');
});

it('surfaces native model initialization errors instead of pretending inference worked', async()=>{
 await expect(startOnDeviceSession({LanguageModel:{create(){throw new Error('model unavailable');}}})).rejects.toThrow('model unavailable');
});

it('reports real fractional progress and cancels a late creation without leaking its session', async()=>{
 const controller=new AbortController(), progress:number[]=[];
 let report!:(event:{loaded:number})=>void;
 let finish!:(value:{prompt:()=>Promise<string>;destroy:()=>void})=>void;
 let destroyed=0, passedSignal:AbortSignal|undefined;
 const pending=startOnDeviceSession({LanguageModel:{create(options){
  passedSignal=options?.signal;
  options?.monitor?.({addEventListener(_name,listener){report=listener;}});
  return new Promise(resolve=>{finish=resolve;});
 }}},{signal:controller.signal,onDownloadProgress:value=>progress.push(value)});
 expect(passedSignal).toBe(controller.signal);
 report({loaded:0.37});report({loaded:NaN});report({loaded:9});
 expect(progress).toEqual([0.37]);
 controller.abort();
 await expect(pending).rejects.toHaveProperty('name','AbortError');
 report({loaded:1});
 finish({prompt:async()=>'',destroy(){destroyed++;}});
 await Promise.resolve();
 expect(progress).toEqual([0.37]);
 expect(destroyed).toBe(1);
});

it('settles a cancelled prompt even when a provider ignores its signal and later resolves', async()=>{
 const controller=new AbortController();
 let finish!:(value:string)=>void, passedSignal:AbortSignal|undefined;
 const session={prompt(_text:string,options:{responseConstraint:object;signal?:AbortSignal}){
  passedSignal=options.signal;
  return new Promise<string>(resolve=>{finish=resolve;});
 }};
 const pending=completeInvoicePrompt(session,'EMAIL: original',controller.signal);
 expect(passedSignal).toBe(controller.signal);
 controller.abort();
 await expect(pending).rejects.toHaveProperty('name','AbortError');
 finish('{"late":"proposal"}');
 await Promise.resolve();
});

it('never starts a native operation when its request was already cancelled', async()=>{
 const controller=new AbortController();controller.abort();
 let calls=0;
 const session={async prompt(){calls++;return '{}';}};
 await expect(startOnDeviceSession({LanguageModel:{async create(){calls++;return session;}}},{signal:controller.signal})).rejects.toHaveProperty('name','AbortError');
 await expect(completeInvoicePrompt(session,'EMAIL: ignored',controller.signal)).rejects.toHaveProperty('name','AbortError');
 expect(calls).toBe(0);
});
