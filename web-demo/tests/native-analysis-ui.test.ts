import {afterEach, expect, it, vi} from 'vitest';
import {createNativeAnalysisActivity} from '../src/native-analysis-ui';

afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});
function deferred(){let resolve!:(value:any)=>void;const promise=new Promise<any>(done=>resolve=done);return {promise,resolve};}
function setup(){
 vi.useFakeTimers();
 const nodes:any[]=[];
 function element(){
  const node:any={id:'',hidden:false,disabled:false,textContent:'',className:'',children:[],handlers:{},
   setAttribute(){},append(...children:any[]){this.children.push(...children);},
   insertAdjacentElement(_position:string,child:any){this.children.push(child);},
   addEventListener(name:string,handler:any){this.handlers[name]=handler;}};
  nodes.push(node);return node;
 }
 const analyze=element();analyze.id='analyze';
 const ai=element();ai.id='analyze-ai';
 vi.stubGlobal('document',{createElement:element,querySelector:(selector:string)=>nodes.find(node=>node.id===selector.slice(1))??null});
 let source='Original client request',fields=false;
 const hooks={source:()=>source,hasFields:()=>fields,adopt:vi.fn(),applying:vi.fn()};
 const client={analysisOwner:()=>crypto.randomUUID(),resumeAnalysisOwner:vi.fn(),
  startAnalysis:vi.fn(async(_text:string,id:string)=>({ok:true,result:{analysis_job:{id,state:'running',elapsed_seconds:0}}})),
  analysisStatus:vi.fn(),dismissAnalysis:vi.fn(async()=>({ok:true})),
  applyAnalysis:vi.fn(),analyzeRules:vi.fn()};
 const activity=createNativeAnalysisActivity(client,element(),hooks);
 activity.setAvailability(true,false);
 return {activity,client,hooks,node:(id:string)=>nodes.find(node=>node.id===id),source:(value:string)=>{source=value;},fields:(value:boolean)=>{fields=value;}};
}
const flush=async()=>{await Promise.resolve();await Promise.resolve();};

it('opens manual fields during slow first analysis and ignores its late ready response',async()=>{
 const app=setup(),status=deferred(),rules=deferred();
 app.client.analysisStatus.mockReturnValue(status.promise);
 app.client.analyzeRules.mockReturnValue(rules.promise);
 await app.activity.start();
 expect(app.node('native-analysis-rules').hidden).toBe(false);
 expect(app.client.analyzeRules).not.toHaveBeenCalled();
 vi.advanceTimersByTime(1000);await flush();
 const id=app.client.startAnalysis.mock.calls[0][1];
 const opening=app.node('native-analysis-rules').handlers.click();
 expect(app.client.analyzeRules).toHaveBeenCalledExactlyOnceWith('Original client request');
 status.resolve({ok:true,result:{analysis_job:{id,state:'ready'},analysis:{source:'llm',total:'999'}}});
 await flush();
 expect(app.client.applyAnalysis).not.toHaveBeenCalled();
 expect(app.hooks.adopt).not.toHaveBeenCalled();
 rules.resolve({ok:true,result:{source:'rules',line_items:[]}});
 await opening;
 expect(app.hooks.adopt).toHaveBeenCalledExactlyOnceWith({source:'rules',line_items:[]});
 expect(app.node('native-analysis-activity').hidden).toBe(true);
 vi.advanceTimersByTime(5000);await flush();
 expect(app.client.startAnalysis).toHaveBeenCalledTimes(1);
 expect(app.client.analysisStatus).toHaveBeenCalledTimes(1);
});

it('keeps existing fields until explicit manual replacement and rejects a changed source response',async()=>{
 const app=setup(),rules=deferred();app.fields(true);app.client.analyzeRules.mockReturnValue(rules.promise);
 await app.activity.start();
 expect(app.hooks.adopt).not.toHaveBeenCalled();
 const opening=app.node('native-analysis-rules').handlers.click();
 app.source('Different source while the response was pending');
 rules.resolve({ok:true,result:{source:'rules',line_items:[]}});
 await opening;
 expect(app.hooks.adopt).not.toHaveBeenCalled();
});

it('offers explicit manual continuation when the original status is unknown',async()=>{
 const app=setup();
 app.client.startAnalysis.mockRejectedValueOnce(new Error('Response missing'));
 app.client.analyzeRules.mockResolvedValueOnce({ok:true,result:{source:'rules',line_items:[]}});
 await app.activity.start();
 expect(app.node('native-analysis-rules').hidden).toBe(false);
 expect(app.client.analyzeRules).not.toHaveBeenCalled();
 expect(app.node('native-analysis-check').hidden).toBe(false);
 await app.node('native-analysis-rules').handlers.click();
 expect(app.client.analyzeRules).toHaveBeenCalledExactlyOnceWith('Original client request');
 expect(app.hooks.adopt).toHaveBeenCalledExactlyOnceWith({source:'rules',line_items:[]});
 expect(app.client.startAnalysis).toHaveBeenCalledTimes(1);
 expect(app.client.applyAnalysis).not.toHaveBeenCalled();
});

it('continues after a missing status observation and fences a late original result',async()=>{
 const app=setup(),late=deferred();
 app.client.analysisStatus.mockRejectedValueOnce(new Error('Status missing')).mockReturnValueOnce(late.promise);
 app.client.analyzeRules.mockResolvedValueOnce({ok:true,result:{source:'rules'}});
 await app.activity.start();vi.advanceTimersByTime(1000);await flush();
 expect(app.node('native-analysis-rules').hidden).toBe(false);
 const observe=app.node('native-analysis-check').handlers.click();
 await flush();
 await app.node('native-analysis-rules').handlers.click();
 const id=app.client.startAnalysis.mock.calls[0][1];
 late.resolve({ok:true,result:{analysis_job:{id,state:'ready'},analysis:{total:'999'}}});
 await observe;await flush();
 expect(app.hooks.adopt).toHaveBeenCalledExactlyOnceWith({source:'rules'});
 expect(app.client.applyAnalysis).not.toHaveBeenCalled();
 expect(app.client.startAnalysis).toHaveBeenCalledTimes(1);
});

it('offers manual fields after confirmed failure without automatically running rules',async()=>{
 const app=setup();
 app.client.startAnalysis.mockImplementationOnce(async(_text:string,id:string)=>({ok:true,result:{analysis_job:{id,state:'failed',elapsed_seconds:90}}}));
 await app.activity.start();
 expect(app.node('native-analysis-rules').hidden).toBe(false);
 expect(app.client.analyzeRules).not.toHaveBeenCalled();
});

it('uses explicit local AI in the same job and blocks a concurrent start',async()=>{
 const app=setup();app.fields(true);
 await app.activity.start(true);
 expect(app.client.startAnalysis).toHaveBeenCalledWith('Original client request',expect.any(String),true);
 expect(app.node('analyze-ai').disabled).toBe(true);
 await app.activity.start(true);
 expect(app.client.startAnalysis).toHaveBeenCalledTimes(1);
 expect(app.hooks.adopt).not.toHaveBeenCalled();
});

it('dismisses confirmed failure while keeping an existing invoice unchanged',async()=>{
 const app=setup();app.fields(true);
 app.client.startAnalysis.mockImplementationOnce(async(_text:string,id:string)=>({ok:true,result:{analysis_job:{id,state:'failed'}}}));
 await app.activity.start(true);
 expect(app.node('native-analysis-keep').hidden).toBe(false);
 await app.node('native-analysis-keep').handlers.click();
 expect(app.node('native-analysis-activity').hidden).toBe(true);
 expect(app.client.analyzeRules).not.toHaveBeenCalled();
 expect(app.hooks.adopt).not.toHaveBeenCalled();
 expect(app.client.dismissAnalysis).toHaveBeenCalledTimes(1);
 expect(app.node('analyze-ai').disabled).toBe(false);
});
