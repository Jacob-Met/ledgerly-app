import {afterEach, expect, it, vi} from 'vitest';
import {NativeClient} from '../src/native-client';

afterEach(()=>vi.unstubAllGlobals());
const config={token:'fictional-local-session-token',provider:'Synthetic provider'};
const response=(value:any)=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
const requests=(fetcher:ReturnType<typeof vi.fn>)=>fetcher.mock.calls.map(([,init])=>JSON.parse(init.body));

it('recovers a missing start response by observing the original browser job once',async()=>{
 const fetcher=vi.fn()
  .mockRejectedValueOnce(new TypeError('connection closed after server accepted'))
  .mockResolvedValueOnce(response({ok:true,result:{analysis_job:{id:'intake-1',state:'running',elapsed_seconds:2,message:''}},state:{}}));
 vi.stubGlobal('fetch',fetcher);
 const client=new NativeClient(config);
 const result=await client.startAnalysis('A fictional request','intake-1');
 expect(result.result.analysis_job.state).toBe('running');
 expect(requests(fetcher)).toEqual([
  {action:'analysis_start',text:'A fictional request',job_id:'intake-1'},
  {action:'analysis_status',job_id:'intake-1'}
 ]);
 const headers=fetcher.mock.calls.map(([,init])=>init.headers);
 expect(headers[0]['X-Ledgerly-Review']).toBe(headers[1]['X-Ledgerly-Review']);
});

it('recovers a missing apply response only from the original applied extraction',async()=>{
 const extraction={client_name:'Avery Lake',line_items:[{qty:4,unit_price:60,currency:'USD'}]};
 const fetcher=vi.fn()
  .mockResolvedValueOnce(new Response('{incomplete',{status:200}))
  .mockResolvedValueOnce(response({ok:true,result:{analysis_job:{id:'intake-2',state:'applied'},analysis:extraction},state:{reviewed:false}}));
 vi.stubGlobal('fetch',fetcher);
 const result=await new NativeClient(config).applyAnalysis('intake-2');
 expect(result).toEqual({ok:true,result:extraction,state:{reviewed:false}});
 expect(requests(fetcher).map(x=>x.action)).toEqual(['analysis_apply','analysis_status']);
});

it('does not treat a ready or dismissed proposal as a successfully applied invoice',async()=>{
 for(const state of ['ready','dismissed']){
  const fetcher=vi.fn().mockRejectedValueOnce(new TypeError('response lost'))
   .mockResolvedValueOnce(response({ok:true,result:{analysis_job:{id:'intake-3',state},analysis:{client_name:'Earlier proposal'}},state:{}}));
  vi.stubGlobal('fetch',fetcher);
  const result=await new NativeClient(config).applyAnalysis('intake-3');
  expect(result.ok).toBe(false);
  expect(result.message).toContain('not confirmed as applied');
  expect(result.result).toBeUndefined();
  expect(requests(fetcher).map(x=>x.action)).toEqual(['analysis_apply','analysis_status']);
 }
});

it('does not claim dismissal if the original request is still running',async()=>{
 const fetcher=vi.fn().mockRejectedValueOnce(new TypeError('response lost'))
  .mockResolvedValueOnce(response({ok:true,result:{analysis_job:{id:'intake-4',state:'running'}},state:{}}));
 vi.stubGlobal('fetch',fetcher);
 const result=await new NativeClient(config).dismissAnalysis('intake-4');
 expect(result.result.analysis_job.state).toBe('running');
 expect(requests(fetcher).map(x=>x.action)).toEqual(['analysis_dismiss','analysis_status']);
});

it('keeps a missing analysis status distinct from provider uncertainty and never loops',async()=>{
 const fetcher=vi.fn().mockRejectedValue(new TypeError('operator unreachable'));
 vi.stubGlobal('fetch',fetcher);
 await expect(new NativeClient(config).startAnalysis('Fictional text','intake-5'))
  .rejects.toThrow('local analysis response is missing');
 expect(requests(fetcher).map(x=>x.action)).toEqual(['analysis_start','analysis_status']);
});

it('does not automatically repeat or observe a missing provider effect',async()=>{
 const fetcher=vi.fn().mockRejectedValue(new TypeError('provider response unknown'));
 vi.stubGlobal('fetch',fetcher);
 await expect(new NativeClient(config).call('approve',{id:'invoice-review',confirmation:'SEND DEMO-1'}))
  .rejects.toThrow('interrupted request is never repeated automatically');
 expect(requests(fetcher).map(x=>x.action)).toEqual(['approve']);
});

it('reconnects only the saved analysis owner while human review stays in a fresh session',async()=>{
 const fetcher=vi.fn().mockImplementation(()=>Promise.resolve(response({ok:true,result:{}})));
 vi.stubGlobal('fetch',fetcher);
 const original=new NativeClient(config);
 await original.startAnalysis('Original request','original-job');
 const owner=original.analysisOwner();
 const reopened=new NativeClient(config);
 reopened.resumeAnalysisOwner(owner,'Original request');
 await reopened.analysisStatus('original-job');
 await reopened.applyAnalysis('original-job');
 await reopened.call('review',{confirmed:true,fields:{}});
 const headers=fetcher.mock.calls.map(([,init])=>init.headers['X-Ledgerly-Review']);
 expect(headers[1]).toBe(headers[0]);expect(headers[2]).toBe(headers[0]);
 expect(headers[3]).not.toBe(headers[0]);expect(headers[4]).toBe(headers[3]);
 expect(requests(fetcher)[3]).toEqual({action:'analysis_restore',source_owner:owner,text:'Original request'});
 await reopened.startAnalysis('Different request','next-job');
 expect(fetcher.mock.calls[5][1].headers['X-Ledgerly-Review']).toBe(headers[3]);
 expect(requests(fetcher).map(x=>x.action)).toEqual(['analysis_start','analysis_status','analysis_apply','analysis_restore','review','analysis_start']);
});

it('rejects malformed saved analysis ownership without dispatching a request',()=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 expect(()=>new NativeClient(config).resumeAnalysisOwner('not-a-browser-session')).toThrow('invalid');
 expect(fetcher).not.toHaveBeenCalled();
});

it('opens rules-only fields once without starting or retrying model inference',async()=>{
 const fetcher=vi.fn().mockResolvedValueOnce(response({ok:true,result:{source:'rules',line_items:[]}}));
 vi.stubGlobal('fetch',fetcher);
 const result=await new NativeClient(config).analyzeRules('Original failed request');
 expect(result.result.source).toBe('rules');
 expect(requests(fetcher)).toEqual([{action:'analyze_rules',text:'Original failed request'}]);
});
it('keeps a missing rules-only response missing without automatically repeating extraction',async()=>{
 const fetcher=vi.fn().mockRejectedValue(new TypeError('response lost'));
 vi.stubGlobal('fetch',fetcher);
 await expect(new NativeClient(config).analyzeRules('Original failed request')).rejects.toThrow('response is missing');
 expect(requests(fetcher)).toEqual([{action:'analyze_rules',text:'Original failed request'}]);
});

it('observes a missing explicit model request without repeating or dropping its original mode',async()=>{
 const fetcher=vi.fn().mockRejectedValueOnce(new TypeError('response lost'))
  .mockResolvedValueOnce(response({ok:true,result:{analysis_job:{id:'explicit-model',state:'running'}}}));
 vi.stubGlobal('fetch',fetcher);
 await new NativeClient(config).startAnalysis('Original source','explicit-model',true);
 expect(requests(fetcher)).toEqual([
  {action:'analysis_start',text:'Original source',prefer_model:true,job_id:'explicit-model'},
  {action:'analysis_status',job_id:'explicit-model'}
 ]);
});
