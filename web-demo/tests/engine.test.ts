import {beforeAll,expect,it} from 'vitest';
import fs from 'node:fs';import path from 'node:path';import {loadPyodide} from 'pyodide';
let py:any;const web=process.cwd();
async function call(action:string,payload:Record<string,unknown>={}){py.globals.set('request_json',JSON.stringify({action,...payload}));const raw=await py.runPythonAsync('bridge.handle_json(request_json)');py.globals.delete('request_json');return JSON.parse(String(raw));}
beforeAll(async()=>{py=await loadPyodide();py.FS.mkdirTree('/demo/ledgerly');for(const n of ['__init__.py','agent.py','extract.py','paypal.py'])py.FS.writeFile('/demo/ledgerly/'+n,fs.readFileSync(path.join(web,'public','python','ledgerly',n),'utf8'));for(const n of ['bridge.py','review.py','invoice_details.py'])py.FS.writeFile('/demo/'+n,fs.readFileSync(path.join(web,'public','python',n),'utf8'));await py.runPythonAsync("import sys; sys.path.insert(0, '/demo'); import bridge");});
it('runs the actual Python invoice workflow through approval, signed webhook and overdue reminder',async()=>{
 await call('reset');const email=fs.readFileSync(path.join(web,'public','fixtures','01_simple_usd_hourly.txt'),'utf8');const analysis=await call('analyze',{text:email});expect(analysis.result.confidence).toBeGreaterThan(0.5);expect(analysis.result.line_items.length).toBeGreaterThan(0);
 const draft=await call('draft',{text:email});expect(draft.result.unauthorized_send_blocked).toBe(true);expect(draft.state.mock_requests).toBe(3);const invoice=draft.state.ledger[0].invoice_id;const send=draft.state.pending.find((x:any)=>x.kind==='send_invoice');expect(send).toBeTruthy();
 const sent=await call('approve',{action_id:send.id});expect(sent.state.ledger[0].status).toBe('SENT');const due=new Date(`${sent.state.ledger[0].due_on}T00:00:00Z`);due.setUTCDate(due.getUTCDate()+1); const overdue=due.toISOString().slice(0,10);const payment=await call('payment',{invoice_id:invoice,amount:'100'});expect(payment.state.ledger[0].status).toBe('PARTIALLY_PAID');expect((await call('replay')).result.duplicate).toBe(true);
 await call('advance',{day:overdue});const chase=await call('chase');const reminder=chase.state.pending.find((x:any)=>x.kind==='send_reminder');expect(reminder).toBeTruthy();const approved=await call('approve',{action_id:reminder.id});expect(approved.state.ledger[0].reminders_sent).toBe(1);expect(approved.state.external_calls).toBe(0);
});
it('keeps draft sends blocked until the visitor approves or rejects the action',async()=>{await call('reset');const email=fs.readFileSync(path.join(web,'public','fixtures','01_simple_usd_hourly.txt'),'utf8');const draft=await call('draft',{text:email});expect(draft.state.ledger[0].status).toBe('DRAFT');expect(draft.result.unauthorized_send_blocked).toBe(true);expect(draft.state.mock_requests).toBe(3);const item=draft.state.pending.find((x:any)=>x.kind==='send_invoice');const rejected=await call('reject',{action_id:item.id});expect(rejected.state.ledger[0].status).toBe('DRAFT');expect(rejected.state.mock_requests).toBe(3);});

it('uses actual Python LLM extraction for a model completion, but cannot draft without checked human fields', async()=>{
 await call('reset');
 const text=fs.readFileSync(path.join(web,'public','fixtures','01_simple_usd_hourly.txt'),'utf8');
 const source=await call('analyze',{text});
 const prompt=await call('ai_prompt',{text});
 expect(prompt.ok).toBe(true);
 expect(prompt.result.prompt).toContain(text);
 const completion=JSON.stringify({...source.result, confidence:0.96,issues:[]});
 const proposed=await call('analyze_ai',{text,completion});
 expect(proposed.ok).toBe(true);
 expect(proposed.result.source).toBe('llm');
 expect(proposed.result.requires_review).toBe(true);
 expect(proposed.state.mock_requests).toBe(0);
 const unchecked=await call('draft',{text});
 expect(unchecked.ok).toBe(false);
 expect(unchecked.message).toContain('checked human fields');
 expect(unchecked.state.ledger).toHaveLength(0);
 const values=proposed.result;
 const fields={
  client_name:values.client_name,client_email:values.client_email,
  due_days:String(values.due_days??''),amount_paid:String(values.amount_paid),
  line_items:values.line_items.map((row:any)=>({
   desc:row.desc,qty:String(row.qty??''),unit_price:String(row.unit_price),
   currency:row.currency||values.currency,unit:row.unit||'',
  })),
 };
 const checked=await call('review',{text,fields,confirmed:true});
 expect(checked.ok).toBe(true);
 expect(checked.result.valid).toBe(true);
 const drafted=await call('draft',{text,review_id:checked.result.review_id});
 expect(drafted.ok).toBe(true);
 expect(drafted.result.reviewed).toBe(true);
 expect(drafted.state.ledger.length).toBeGreaterThan(0);
 expect(drafted.state.ledger[0].status).toBe('DRAFT');
 expect(drafted.state.pending.some((action:any)=>action.kind==='send_invoice')).toBe(true);
 expect(drafted.state.external_calls).toBe(0);
});

it('blocks hallucinated AI emails and malformed responses without opening a draft gate',async()=>{
 await call('reset');
 const text=fs.readFileSync(path.join(web,'public','fixtures','01_simple_usd_hourly.txt'),'utf8');
 const source=await call('analyze',{text});
 const fake=JSON.stringify({...source.result,client_email:'fabricated@invalid.example',confidence:1,issues:[]});
 const result=await call('analyze_ai',{text,completion:fake});
 expect(result.ok).toBe(true);
 expect(result.result.client_email).toBeNull();
 expect(result.result.issues.some((issue:any)=>issue.field==='client_email')).toBe(true);
 expect((await call('draft',{text})).ok).toBe(false);
 const malformed=await call('analyze_ai',{text,completion:'not JSON'});
 expect(malformed.ok).toBe(false);
 expect(malformed.message).toContain('LLM output unusable');
 expect((await call('draft',{text})).ok).toBe(false);
 expect(malformed.state.mock_requests).toBe(0);
});

it('preserves a checked revision when an AI request cannot be admitted by Python',async()=>{
 await call('reset');
 const text=fs.readFileSync(path.join(web,'public','fixtures','01_simple_usd_hourly.txt'),'utf8');
 const values=(await call('analyze',{text})).result;
 const fields={
  client_name:'Human-checked original',client_email:values.client_email,
  due_days:String(values.due_days),amount_paid:String(values.amount_paid),
  line_items:values.line_items.map((row:any)=>({
   desc:row.desc,qty:String(row.qty),unit_price:String(row.unit_price),
   currency:row.currency||values.currency,unit:row.unit||'',
  })),
 };
 const checked=await call('review',{text,fields,confirmed:true});
 expect(checked.result.valid).toBe(true);
 const prepared=await call('ai_prompt',{text});
 expect(prepared.ok).toBe(true);
 for(const completion of ['not JSON','[]','{"confidence":"bad"}']){
  const malformed=await call('analyze_ai',{text,completion});
  expect(malformed.ok).toBe(false);
  expect(malformed.message).toContain('LLM output unusable');
  expect(malformed.state).toEqual(checked.state);
 }
 const oversized=await call('analyze_ai',{text,completion:'x'.repeat(64001)});
 expect(oversized.ok).toBe(false);
 expect(oversized.message).toContain('64,000');
 expect(oversized.state).toEqual(checked.state);
 const invalidSource=await call('analyze_ai',{text:'x'.repeat(30001),completion:'{}'});
 expect(invalidSource.ok).toBe(false);
 expect(invalidSource.state).toEqual(checked.state);
 const drafted=await call('draft',{text,review_id:checked.result.review_id});
 expect(drafted.ok).toBe(true);
 expect(drafted.result.reviewed).toBe(true);
 expect(drafted.state.ledger[0].status).toBe('DRAFT');
 expect(drafted.state.pending.some((action:any)=>action.kind==='send_invoice')).toBe(true);
 expect(drafted.state.external_calls).toBe(0);
});
