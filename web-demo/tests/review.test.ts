import {sourceExclusionChoice,fieldsFromExtraction, reviewMarkup, sourceSpan, invoiceDocumentMarkup, sourceReviewIssues, sourceReviewWarningsMarkup, reviewResultMarkup} from '../src/review';
import {beforeAll, expect, it} from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import {loadPyodide} from 'pyodide';

let py: any;
const web = process.cwd();
const fixture = (name: string) => fs.readFileSync(path.join(web, 'public', 'fixtures', name), 'utf8');
async function call(action: string, payload: Record<string, unknown> = {}) {
  py.globals.set('request_json', JSON.stringify({action, ...payload}));
  try { return JSON.parse(String(await py.runPythonAsync('bridge.handle_json(request_json)'))); }
  finally { py.globals.delete('request_json'); }
}
function editable(ex: any) {
  return {
    client_name: ex.client_name || 'Fictional client', client_email: ex.client_email || 'billing@example.test',
    due_days: String(ex.due_days ?? 30), amount_paid: String(ex.amount_paid ?? '0'),
    line_items: ex.line_items.map((row: any) => ({desc: row.desc, qty: String(row.qty ?? '1'), unit_price: String(row.unit_price), currency: row.currency || ex.currency || '', unit: row.unit || ''})),
  };
}
async function analyze(name = '05_missing_email.txt') {
  await call('reset');
  const text = fixture(name);
  const response = await call('analyze', {text});
  expect(response.ok).toBe(true);
  return {text, original: response.result, fields: editable(response.result)};
}
beforeAll(async () => {
  py = await loadPyodide();
  py.FS.mkdirTree('/demo/ledgerly');
  for (const name of ['__init__.py', 'agent.py', 'extract.py', 'paypal.py'])
    py.FS.writeFile('/demo/ledgerly/' + name, fs.readFileSync(path.join(web, 'public', 'python', 'ledgerly', name), 'utf8'));
  for (const name of ['bridge.py', 'review.py', 'invoice_details.py']) {
    const source = path.join(web, 'public', 'python', name);
    if (fs.existsSync(source)) py.FS.writeFile('/demo/' + name, fs.readFileSync(source, 'utf8'));
  }
  await py.runPythonAsync("import sys; sys.path.insert(0, '/demo'); import bridge");
});

it('lets a visitor correct a missing recipient without changing the extraction or approving a send', async () => {
  const {text, original, fields} = await analyze();
  expect(original.issues.some((issue: any) => issue.field === 'client_email' && issue.severity === 'error')).toBe(true);
  const blocked = await call('draft', {text});
  expect(blocked.state.ledger).toHaveLength(0);
  fields.due_days = '0';
  const checked = await call('review', {text, fields, confirmed: true});
  expect(checked.ok).toBe(true);
  expect(checked.result.valid).toBe(true);
  expect(checked.result.source).toBe('human_review');
  expect(checked.result.original_issues).toEqual(original.issues);
  expect(checked.state.mock_requests).toBe(0);
  expect(checked.state.pending).toHaveLength(0);
  const draft = await call('draft', {text, review_id: checked.result.review_id});
  expect(draft.result.unauthorized_send_blocked).toBe(true);
  expect(draft.state.mock_requests).toBe(3);
  expect(draft.state.ledger).toHaveLength(1);
  expect(draft.state.ledger[0]).toMatchObject({client_email: fields.client_email, status: 'DRAFT', total: original.total});
  const pending = draft.state.pending[0];
  expect(pending.kind).toBe('send_invoice');
  const sent = await call('approve', {action_id: pending.id});
  expect(sent.state.ledger[0].status).toBe('SENT');
  expect(sent.state.ledger[0].due_on).toBe(sent.state.today);
  expect(sent.state.external_calls).toBe(0);
  const originalAgain = await call('analyze', {text});
  expect(originalAgain.result.issues).toEqual(original.issues);
  expect(originalAgain.result.client_email).toEqual(original.client_email);
});

it('requires explicit confirmation and refuses client-supplied derived metadata', async () => {
  const {text, fields} = await analyze();
  for (const confirmed of [false, 'true', 1, null]) {
    const result = await call('review', {text, fields, confirmed});
    expect(result.ok).toBe(false);
    expect(result.state.mock_requests).toBe(0);
  }
  const forged = await call('review', {text, fields: {...fields, confidence: 1, issues: [], source: 'rules'}, confirmed: true});
  expect(forged.ok).toBe(false);
  expect(forged.state.ledger).toHaveLength(0);
});

it('keeps invalid recipient, quantity, price, currency and terms out of the draft queue', async () => {
  const {text, fields} = await analyze();
  const mutations = [
    (f: any) => {f.client_email = 'not-an-email';},
    (f: any) => {f.line_items[0].qty = '0';},
    (f: any) => {f.line_items[0].qty = '-2';},
    (f: any) => {f.line_items[0].unit_price = '0';},
    (f: any) => {f.line_items[0].currency = 'ZZZ';},
    (f: any) => {f.line_items[0].desc = '';},
    (f: any) => {f.due_days = '366';},
    (f: any) => {f.amount_paid = '-1';},
    (f: any) => {f.line_items = [];},
  ];
  for (const mutate of mutations) {
    const invalid = structuredClone(fields); mutate(invalid);
    const result = await call('review', {text, fields: invalid, confirmed: true});
    expect(result.ok).toBe(true);
    expect(result.result.valid).toBe(false);
    expect(result.result.review_id).toBeNull();
    expect(result.result.issues.some((issue: any) => issue.severity === 'error')).toBe(true);
    expect(result.state.ledger).toHaveLength(0);
    expect(result.state.mock_requests).toBe(0);
  }
});

it('refuses non-finite and ambiguous numeric edits before invoice side effects', async () => {
  const {text, fields} = await analyze();
  for (const number of ['NaN', 'Infinity', '-Infinity', '1e10000', '1,200', '', 'two', 'true']) {
    const invalid = structuredClone(fields); invalid.line_items[0].qty = number;
    const result = await call('review', {text, fields: invalid, confirmed: true});
    expect(result.ok).toBe(false);
    expect(result.issues[0].field).toBe('line_items[0].qty');
    expect(result.state.mock_requests).toBe(0);
  }
  for (const days of ['1.5', '-1', 'NaN']) {
    const result = await call('review', {text, fields: {...fields, due_days: days}, confirmed: true});
    expect(result.ok).toBe(false);
    expect(result.issues[0].field).toBe('due_days');
    expect(result.state.pending).toHaveLength(0);
  }
});

it('consumes a checked revision once and refuses a stale revision without duplicate drafts', async () => {
  const {text, fields} = await analyze();
  const checked = await call('review', {text, fields, confirmed: true});
  expect(checked.result.valid).toBe(true);
  const payload = {text, review_id: checked.result.review_id};
  const first = await call('draft', payload);
  expect(first.state.ledger).toHaveLength(1);
  const repeated = await call('draft', payload);
  expect(repeated.ok).toBe(false);
  expect(repeated.state.ledger).toHaveLength(1);
  expect(repeated.state.mock_requests).toBe(first.state.mock_requests);
  const next = await call('review', {text, fields, confirmed: true});
  const changed = await call('draft', {text: text + '\nChanged source.', review_id: next.result.review_id});
  expect(changed.ok).toBe(false);
  expect(changed.state.ledger).toHaveLength(1);
  expect((await call('draft', {text, review_id: next.result.review_id})).ok).toBe(false);
});

it('invalidates checked fields after another analysis, failed correction or reset', async () => {
  const {text, fields} = await analyze();
  for (const invalidate of ['analyze', 'review', 'reset']) {
    await call('analyze', {text});
    const checked = await call('review', {text, fields, confirmed: true});
    expect(checked.result.valid).toBe(true);
    if (invalidate === 'review') await call('review', {text, fields: {...fields, client_email: ''}, confirmed: true});
    else await call(invalidate, {text});
    const stale = await call('draft', {text, review_id: checked.result.review_id});
    expect(stale.ok).toBe(false);
    expect(stale.state.ledger).toHaveLength(0);
    expect(stale.state.mock_requests).toBe(0);
  }
});

it('keeps per-currency totals separate and gates every reviewed split invoice', async () => {
  const {text, fields} = await analyze('04_multi_currency.txt');
  const checked = await call('review', {text, fields, confirmed: true});
  expect(checked.result.valid).toBe(true);
  expect(checked.result.totals_by_currency).toHaveLength(2);
  expect(checked.result.financials_by_currency.every((row:any)=>row.paid==='0'&&row.due===row.total)).toBe(true);
  const draft = await call('draft', {text, review_id: checked.result.review_id});
  expect(draft.state.ledger).toHaveLength(2);
  expect(draft.state.ledger.every((entry: any) => entry.status === 'DRAFT')).toBe(true);
  expect(draft.result.unauthorized_send_blocked).toBe(true);
  expect(draft.state.pending).toHaveLength(2);
  expect(draft.state.external_calls).toBe(0);
  const deposit = await call('review', {text, fields: {...fields, amount_paid: '1'}, confirmed: true});
  expect(deposit.result.valid).toBe(false);
  expect(deposit.result.financials_by_currency).toEqual([]);
  expect(deposit.result.issues.some((issue: any) => issue.field === 'amount_paid' && issue.severity === 'error')).toBe(true);
});

it('preserves zero-decimal currency checks and requires a confirmed quantity', async () => {
  const {text, fields} = await analyze('08_jpy_zero_decimal.txt');
  fields.line_items[0].unit_price = '10.50';
  const fraction = await call('review', {text, fields, confirmed: true});
  expect(fraction.result.valid).toBe(false);
  expect(fraction.state.mock_requests).toBe(0);
  const ambiguous = await analyze('06_ambiguous_qty.txt');
  const checked = await call('review', {...ambiguous, confirmed: true});
  expect(checked.result.valid).toBe(true);
  const draft = await call('draft', {text: ambiguous.text, review_id: checked.result.review_id});
  expect(draft.state.ledger.length).toBeGreaterThan(0);
  expect(draft.result.unauthorized_send_blocked).toBe(true);
});

it('leaves conflicting calendar terms unselected until a person chooses a date or days', () => {
  const issue = {field: 'due_date', severity: 'error', message: 'Conflicting payment terms'};
  for (const due_days of [null, 45]) {
    const fields = fieldsFromExtraction({due_date: '2026-12-03', due_days, issues: [issue]});
    expect(fields.due_date).toBe('');
    expect(fields.due_days).toBe('');
    const markup = reviewMarkup({due_date: '2026-12-03', due_days, issues: [issue]});
    expect(markup).toContain('<option value="date" selected>');
    expect(markup).toContain('name="due_date" type="date" value=""');
  }
  expect(fieldsFromExtraction({due_date: '2026-12-03', due_days: null}).due_date).toBe('2026-12-03');
});

it('locates only unambiguous literal source text with original Unicode offsets', () => {
 const source='👩🏽‍💻 İpek wrote: Maya Chen <maya@example.test>\n2 x Editing [A+B] @ $85';
 const quote=sourceSpan(source,'Maya Chen');
 expect(quote).toEqual({start:source.indexOf('Maya Chen'),end:source.indexOf('Maya Chen')+9,text:'Maya Chen'});
 expect(sourceSpan(source,'Editing [A+B]')?.text).toBe('Editing [A+B]');
 expect(sourceSpan('Price185 and1850','85')).toBeNull();
 expect(sourceSpan('Maya Chen, ask Maya Chen','Maya Chen')).toBeNull();
 expect(sourceSpan(source,'')).toBeNull();
});

it('ties exact rates to their own source item and drops source claims after edits', () => {
 const source='From: Maya Chen <maya@example.test>\n2 x Copywriting @ $85\n1 x Audit @ $300';
 const ex={client_name:'Maya Chen',client_email:'maya@example.test',due_days:15,amount_paid:'0',line_items:[
  {desc:'Copywriting',qty:'2',unit_price:'85',currency:'USD',unit:''},
  {desc:'Audit',qty:'1',unit_price:'300',currency:'USD',unit:''}]};
 const fields=fieldsFromExtraction(ex);
 const initial=invoiceDocumentMarkup(fields,[{currency:'USD',total:'470'}],false,false,['170','300'],source,ex);
 expect(initial).toContain('data-source-text="$85"');
 expect(initial).toContain('data-source-text="$300"');
 expect(initial).toContain('data-source-text="Maya Chen"');
 fields.line_items[0].unit_price='300';
 fields.client_name='Changed recipient';
 const edited=invoiceDocumentMarkup(fields,[],false,true,[],source,ex);
 expect(edited).not.toContain('data-source-text="$85"');
 expect(edited).not.toContain('data-source-text="Copywriting"');
 expect(edited).not.toContain('data-source-text="Maya Chen"');
 expect(edited).toContain('data-source-text="$300"');
 expect(edited).toContain('Check changes');
});

it('does not attach ambiguous prices or a different decimal amount to a source link', () => {
 const ex={client_name:'Client',client_email:'client@example.test',due_days:0,amount_paid:'0',line_items:[
  {desc:'Review',qty:'1',unit_price:'85',currency:'USD',unit:''}]};
 const fields=fieldsFromExtraction(ex);
 const repeated=invoiceDocumentMarkup(fields,[],false,false,[],'Review $85; former estimate $85',ex);
 expect(repeated).not.toContain('data-source-text="$85"');
 fields.line_items[0].unit_price='9007199254740993.12';ex.line_items[0].unit_price=fields.line_items[0].unit_price;
 const exact=invoiceDocumentMarkup(fields,[{currency:'USD',total:'9007199254740993.12'}],false,false,['9007199254740993.12'],'Review USD 9007199254740992.12',ex);
 expect(exact).not.toContain('data-source-text="USD');
 expect(exact).toContain('USD 9,007,199,254,740,993.12');
 expect(exact).not.toContain('USD 9,007,199,254,740,992');
});

it('keeps source attributes and literal invoice text escaped', () => {
 const desc='Review <img src=x onerror=alert(1)> "quoted"';
 const ex={client_name:'A & B',client_email:'client@example.test',due_days:0,amount_paid:'0',line_items:[
  {desc,qty:'1',unit_price:'85',currency:'USD',unit:''}]};
 const html=invoiceDocumentMarkup(fieldsFromExtraction(ex),[],false,false,[],'A & B\n'+desc+' @ $85',ex);
 expect(html).not.toContain('<img');
 expect(html).toContain('&lt;img src=x onerror=alert(1)&gt; &quot;quoted&quot;');
 expect(html).toContain('data-source-text="A &amp; B"');
});

it('links explicitly stated quantities and net terms without inventing source for defaults', () => {
 const ex={client_name:'Client',client_email:'client@example.test',due_days:21,amount_paid:'0',line_items:[
  {desc:'Editing',qty:'2',unit_price:'85',currency:'USD',unit:'hours'}]};
 const fields=fieldsFromExtraction(ex);
 const source='2 hours of Editing at $85. Net 21.';
 const html=invoiceDocumentMarkup(fields,[],false,false,[],source,ex);
 expect(html).toContain('data-source-text="2 hours"');
 expect(html).toContain('data-source-text="Net 21"');
 const repeated=invoiceDocumentMarkup(fields,[],false,false,[],source+' Net 21.',ex);
 expect(repeated).not.toContain('data-source-text="Net 21"');
 fields.due_days='7';
 expect(invoiceDocumentMarkup(fields,[],false,true,[],source,ex)).not.toContain('data-source-text="Net 21"');
 fields.line_items[0].qty='1';fields.line_items[0].unit='';ex.line_items[0].qty='1';ex.line_items[0].unit='';
 expect(invoiceDocumentMarkup(fields,[],false,false,[],'Editing at $1',ex)).not.toContain('data-source-text="1"');
});

it('shows exact amount due after a prior payment and removes stale financials on edit', async () => {
  const {text, fields} = await analyze('07_partial_payment_deposit.txt');
  const checked = await call('review', {text, fields, confirmed: true});
  expect(checked.result.valid).toBe(true);
  expect(checked.result.financials_by_currency).toEqual([{currency:'USD',total:'3500',paid:'1000',due:'2500'}]);
  const markup = invoiceDocumentMarkup(fields,checked.result.totals_by_currency,true,false,checked.result.line_amounts,'',undefined,checked.result.financials_by_currency);
  expect(markup).toContain('Due if payment confirmed');
  expect(markup).toContain('separately confirmed and recorded');
  expect(markup).toContain('USD 2,500.00');
  expect(markup).toContain('Invoice total</dt><dd>USD 3,500.00');
  expect(markup).toContain('Reported prior payment</dt><dd>USD 1,000.00');
  const dirty = invoiceDocumentMarkup({...fields,amount_paid:'1250'},checked.result.totals_by_currency,false,true,checked.result.line_amounts,'',undefined,checked.result.financials_by_currency);
  expect(dirty).toContain('Check changes');
  expect(dirty).not.toContain('USD 2,500.00');
  expect(dirty).not.toContain('document-financials');
  const amended = await call('review',{text,fields:{...fields,amount_paid:'1250'},confirmed:true});
  expect(amended.result.financials_by_currency[0].due).toBe('2250');
  expect(amended.state.mock_requests).toBe(0);
});

it('keeps exact decimal and zero-decimal amount-due calculations in the shared core', async () => {
  const {text, fields} = await analyze('07_partial_payment_deposit.txt');
  const pennies = {...fields,amount_paid:'0.10',line_items:[{desc:'Small item',qty:'1',unit_price:'0.30',currency:'USD',unit:''}]};
  const checked = await call('review',{text,fields:pennies,confirmed:true});
  expect(checked.result.valid).toBe(true);
  expect(checked.result.financials_by_currency).toEqual([{currency:'USD',total:'0.30',paid:'0.10',due:'0.20'}]);
  const yen = {...fields,amount_paid:'300',line_items:[{desc:'Yen item',qty:'1',unit_price:'1000',currency:'JPY',unit:''}]};
  const checkedYen = await call('review',{text,fields:yen,confirmed:true});
  expect(checkedYen.result.valid).toBe(true);
  expect(checkedYen.result.financials_by_currency).toEqual([{currency:'JPY',total:'1000',paid:'300',due:'700'}]);
  expect(invoiceDocumentMarkup(yen,checkedYen.result.totals_by_currency,true,false,checkedYen.result.line_amounts,'',undefined,checkedYen.result.financials_by_currency)).toContain('JPY 700');
});


it('invoice reasons stay on the original affected row and clear after editing or checking',()=>{
 const ex={client_name:'Alex',client_email:'alex@example.test',currency:'USD',due_days:15,
  line_items:[{desc:'artwork',qty:1,unit_price:50,currency:'USD'},{desc:'animation',qty:1,unit_price:100,currency:'USD'}],
  issues:[{field:'line_items[1].unit_price',severity:'error',message:"Source work is optional, conditional or excluded; review the client's instruction before including this charge."}]};
 const fields=fieldsFromExtraction(ex);
 const render=(checked=false)=>invoiceDocumentMarkup(fields,[],checked,true,[],'',ex);
 const markup=render();
 expect(markup).toContain('Optional, conditional or excluded work.');
 expect(markup.indexOf('animation')).toBeLessThan(markup.indexOf('document-item-issue'));
 expect(render(true)).not.toContain('document-item-issue');
 fields.line_items[1].desc='Confirmed animation';
 expect(render()).not.toContain('document-item-issue');
});

it('keeps an excluded compound fee out of the displayed invoice amount until review', () => {
 const ex={client_name:'Nia Santos',client_email:'nia@example.test',currency:'GBP',due_days:30,amount_paid:'0',
  line_items:[{desc:'Editing',qty:'2',unit_price:'75',currency:'GBP'},
   {desc:'No-charge transcription fee',qty:'1',unit_price:'25',currency:'GBP'}],
  issues:[{field:'line_items[1].unit_price',severity:'error',message:"Source work is optional, conditional or excluded; review the client's instruction before including this charge."}]};
 const fields=fieldsFromExtraction(ex);
 const original=invoiceDocumentMarkup(fields,[{currency:'GBP',total:'175'}],false,false,['150','25'],'',ex);
 expect(original).toContain('<strong>Needs review</strong>');
 expect(original).toContain('<td>GBP 150.00</td>');
 expect(original).toContain('<td>Needs review</td>');
 expect(original).not.toContain('GBP 175.00');
 fields.line_items.pop();
 const corrected=invoiceDocumentMarkup(fields,[{currency:'GBP',total:'150'}],true,false,['150'],'',ex);
 expect(corrected).toContain('<strong>GBP 150.00</strong>');
 expect(corrected).not.toContain('Needs review');
});

it('keeps the unchanged second excluded item actionable after removing the first',()=>{
 const ex={client_name:'Nia',client_email:'nia@example.test',currency:'GBP',due_days:30,
  line_items:[{desc:'Editing',qty:2,unit_price:75,currency:'GBP'},
   {desc:'No-charge transcription fee',qty:1,unit_price:25,currency:'GBP'},
   {desc:'Optional animation fee',qty:1,unit_price:40,currency:'GBP'}],
  issues:[1,2].map(index=>({field:'line_items['+index+'].unit_price',severity:'error',
   message:"Source work is optional, conditional or excluded; review the client's instruction before including this charge."}))};
 const fields=fieldsFromExtraction(ex);
 fields.line_items.splice(1,1);
 const render=()=>invoiceDocumentMarkup(fields,[],false,true,[],'',ex);
 expect(render().match(/document-item-issue/g)).toHaveLength(1);
 expect(render()).toContain('data-remove-line="1"');
 expect(render()).not.toContain('No-charge transcription');
 fields.line_items[1].desc='Approved animation';
 expect(render()).not.toContain('document-item-issue');
 expect(render()).not.toContain('document-remove-item');
});

it('preserves original row warnings for identical items before any removal',()=>{
 const line={desc:'Animation',qty:1,unit_price:40,currency:'GBP'};
 const ex={line_items:[line,line],issues:[{field:'line_items[1].unit_price',severity:'error',message:'Review this quoted charge.'}]};
 const html=invoiceDocumentMarkup(fieldsFromExtraction(ex),[],false,false,[],'',ex);
 expect(html.match(/document-item-issue/g)).toHaveLength(1);
 expect(html).toContain('data-edit-item="1"');
 expect(html).toContain('data-edit-field="unit_price"');
});


it('puts the unresolved recipient decision on the invoice and clears it after correction',()=>{
 const ex={client_name:null,client_email:null,currency:'USD',due_days:14,amount_paid:'0',
  line_items:[{desc:'Logo design',qty:'1',unit_price:'500',currency:'USD'}],
  issues:[{field:'client_email',severity:'error',message:'Conflicting explicit invoice recipients or delivery addresses; confirm the bill-to before drafting.'}]};
 const fields=fieldsFromExtraction(ex);
 const before=invoiceDocumentMarkup(fields,[{currency:'USD',total:'500'}],false,false,['500'],'',ex);
 expect(before).toContain('Needs review');
 expect(before).toContain('The request names different billing recipients.');
 expect(before).toContain('Choose recipient</button>');
 expect(before).not.toContain('Edit details</button>');
 const changed={...fields,client_name:'Nia Gomez',client_email:'nia@client.example'};
 const after=invoiceDocumentMarkup(changed,[],false,true,[],'',ex);
 expect(after).not.toContain('Choose recipient</button>');
 expect(after).toContain('Review changes');
 expect(after).toContain('Edit details</button>');
 const checked=invoiceDocumentMarkup(changed,[{currency:'USD',total:'500'}],true,false,['500'],'',ex);
 expect(checked).toContain('Checked draft');
 expect(checked).not.toContain('different billing recipients');
});

it('uses shared normalized quotations with browser Unicode offsets and drops them after edits', () => {
 const source='🧾 Request\nFour research interviews at CAD 85 each.\nPayment is due in 14 days.';
 const ex={client_name:'Client',client_email:'client@example.test',due_days:14,line_items:[
  {desc:'Research interviews',qty:'4',unit_price:'85',currency:'CAD',unit:''}],
  source_references:{line_items:[{qty:{text:'Four',value:'4'}}],
   payment_terms:{text:'due in 14 days',value:'14'}}};
 const fields=fieldsFromExtraction(ex);
 const render=()=>invoiceDocumentMarkup(fields,[],false,false,[],source,ex);
 expect(render()).toContain('data-source-start="'+source.indexOf('Four')+'"');
 expect(render()).toContain('data-source-text="Four"');
 expect(render()).toContain('data-source-text="due in 14 days"');
 fields.line_items[0].qty='5';fields.due_days='30';
 expect(render()).not.toContain('data-source-text="Four"');
 expect(render()).not.toContain('data-source-text="due in 14 days"');
});

it('refuses stale, ambiguous and explicitly absent normalized quotations', () => {
 const ex={client_name:'Client',client_email:'client@example.test',due_days:14,line_items:[
  {desc:'Interviews',qty:'4',unit_price:'85',currency:'CAD',unit:''}],
  source_references:{line_items:[{qty:{text:'Four',value:'5'}}],payment_terms:null}};
 const fields=fieldsFromExtraction(ex);
 const render=(source:string)=>invoiceDocumentMarkup(fields,[],false,false,[],source,ex);
 expect(render('Four Interviews at CAD 85. Net 14.')).not.toContain('data-source-text="Four"');
 expect(render('Four Interviews at CAD 85. Net 14.')).not.toContain('data-source-text="Net 14"');
 ex.source_references.line_items[0].qty.value='4';
 expect(render('Four Interviews at CAD 85; Four deliverables.')).not.toContain('data-source-text="Four"');
 expect(render('Three Interviews at CAD 85.')).not.toContain('data-source-text="Four"');
});

it('keeps normalized quantity evidence with the original row after removing earlier work', () => {
 const source='Two layouts at CAD 40.\nFour Interviews at CAD 85.';
 const ex={due_days:14,line_items:[
  {desc:'Layouts',qty:'2',unit_price:'40',currency:'CAD',unit:''},
  {desc:'Interviews',qty:'4',unit_price:'85',currency:'CAD',unit:''}],
  source_references:{line_items:[{qty:{text:'Two',value:'2'}},{qty:{text:'Four',value:'4'}}],payment_terms:null}};
 const fields=fieldsFromExtraction(ex);fields.line_items.shift();
 const html=invoiceDocumentMarkup(fields,[],false,true,[],source,ex);
 expect(html).toContain('data-source-text="Four"');
 expect(html).not.toContain('data-source-text="Two"');
});

it('keeps an explicit count link when the unit price repeats its number', () => {
 const source='1 x printed sticker at CAD 1. Net 14.';
 const ex={due_days:14,line_items:[{desc:'Printed sticker',qty:'1',unit_price:'1',currency:'CAD',unit:''}],
  source_references:{line_items:[{qty:{text:'1 x',value:'1'}}],payment_terms:{text:'Net 14',value:'14'}}};
 const html=invoiceDocumentMarkup(fieldsFromExtraction(ex),[],false,false,[],source,ex);
 expect(html).toContain('data-source-text="1 x"');
 expect(html).toContain('data-source-text="CAD 1"');
});

it('offers a quantity correction rather than deleting valid work with missing hours',()=>{
 const ex={line_items:[{desc:'Design work',qty:null,unit_price:80,currency:'USD'}],
  issues:[{field:'line_items[0].qty',severity:'error',message:'Hourly rate found without a supported quantity; confirm the hours before drafting.'}]};
 const html=invoiceDocumentMarkup(fieldsFromExtraction(ex),[],false,false,[],'',ex);
 expect(html).toContain('data-edit-item="0"');
 expect(html).toContain('data-edit-field="qty"');
 expect(html).toContain('Edit quantity for Design work');
 expect(html).not.toContain('data-remove-line');
});


it('carries the original purchase order through visible review and checked creation', async () => {
  await call('reset');
  const text='From: Arden Works <arden@example.test>\nConsulting — 2 hours at USD 75 per hour\nResearch memo — USD 90\nPlease put purchase order AW-1042 on the invoice so accounts payable can process it.\nNet 30';
  const analyzed=await call('analyze',{text});
  expect(analyzed.ok).toBe(true);
  const fields=fieldsFromExtraction(analyzed.result);
  expect(fields.note).toBe('Purchase order AW-1042');
  const paper=invoiceDocumentMarkup(fields,analyzed.result.totals_by_currency,false,false,analyzed.result.line_amounts,text,analyzed.result);
  expect(paper).toContain('Purchase order AW-1042');
  expect(paper).toContain('Show source for invoice note');
  expect(reviewMarkup(analyzed.result)).toContain('name="note"');
  const checked=await call('review',{text,fields,confirmed:true});
  expect(checked.result.valid).toBe(true);
  fields.note='Changed after check';
  const created=await call('draft',{text,review_id:checked.result.review_id});
  expect(created.state.pending[0].payload.invoice.detail.note).toBe('Purchase order AW-1042');
  expect(created.state.ledger[0].total).toBe('240');
});

it('keeps note edits literal and drops their original quotation after editing', () => {
  const original={client_name:'Arden',client_email:'arden@example.test',due_days:30,amount_paid:'0',note:'Purchase order AW-1042',line_items:[],
    source_references:{note:{text:'PO: AW-1042',value:'Purchase order AW-1042'}}};
  const fields=fieldsFromExtraction(original);
  fields.note='Project <North> & café\nSecond line';
  const paper=invoiceDocumentMarkup(fields,[],false,true,[],'PO: AW-1042',original);
  expect(paper).toContain('Project &lt;North&gt; &amp; café\nSecond line');
  expect(paper).not.toContain('Show source for invoice note');
});
it('keeps missing-price work blocked while its quote replaces duplicate generic alerts', async () => {
 await call('reset');
 const text='From: Oak Vale Studio <accounts@oakvale.example>\n\nPlease invoice 2 x USD 120 portrait sessions and 3 x print sets. Payment due November 30, 2026.';
 const analyzed=await call('analyze',{text}),ex=analyzed.result,original=structuredClone(ex);
 expect(ex.line_items).toHaveLength(0);
 expect(ex.source_amount_requirements).toHaveLength(1);
 expect(sourceReviewIssues(ex)).toEqual([]);
 const form=reviewMarkup(ex);
 expect(form).toContain(ex.source_amount_requirements[0].quote);
 expect(form).toContain('Account for this source amount');
 expect(form).not.toContain('Unknown currency 0');
 const checked=await call('review',{text,fields:fieldsFromExtraction(ex),confirmed:true,source_resolutions:[]});
 expect(checked.result.valid).toBe(false);
 expect(checked.result.review_id).toBeNull();
 const rendered=reviewResultMarkup(checked.result);
 expect(rendered).not.toContain('No priced line items found.');
 expect(rendered).not.toContain('Invoice currency is required.');
 expect(checked.result.issues.some((issue:any)=>String(issue.field).startsWith('source_amounts.'))).toBe(true);
 expect(ex).toEqual(original);
 const refused=await call('draft',{text});
 expect(refused.state.ledger).toHaveLength(0);
 expect(refused.state.mock_requests).toBe(0);
});

it('retains recipient and unmatched source warnings when work alerts are represented elsewhere', () => {
 const quote='Please invoice 2 x USD 120 portraits and 3 x print sets.';
 const represented={field:'line_items',severity:'error',message:"A source amount was not included in the invoice; review it before drafting: '"+quote+"'"};
 const recipient={field:'client_email',severity:'error',message:'Confirm the billing recipient.'};
 const unmatched={...represented,message:"A source amount was not included in the invoice; review it before drafting: 'Separate courier fee USD 19.'"};
 const ex={line_items:[],source_amount_requirements:[{id:'work',quote}],issues:[represented,recipient,unmatched]};
 expect(sourceReviewIssues(ex)).toEqual([recipient,unmatched]);
 expect(sourceReviewIssues({...ex,source_amount_requirements:[]})).toEqual(ex.issues);
 expect(reviewResultMarkup({...ex,valid:false})).toContain('Confirm the billing recipient.');
 expect(reviewResultMarkup({...ex,valid:false})).toContain('Separate courier fee USD 19.');
});

it('places adding work before several source decisions and hides only their complete original group', async () => {
 await call('reset');
 const text="From: Bennett Cole <bennett@larkspur.example>\nBill to: Larkspur Studio <accounts@larkspur.example>\nPlease bill the three half-day editing sessions at GBP 225 each, plus twelve image touch-ups at GBP 8 each.\nThere's also a fixed GBP 18 transfer fee for the final files.\nPayment is due 14 days after the invoice date.";
 const ex=(await call('analyze',{text})).result;
 const retained=JSON.parse(fs.readFileSync(path.join(web,'../tests/fixtures/bennett-original-omission.json'),'utf8'));
 expect(retained.source).toBe(text);
 Object.assign(ex,retained.analysis);
 py.globals.set('retained_analysis_json',JSON.stringify(ex));
 try {
  await py.runPythonAsync('bridge.SESSION.analysis = bridge.json.loads(retained_analysis_json); bridge.SESSION.analysis["source_amount_requirements"] = bridge.source_amount_requirements(bridge.SESSION.analysis)');
  Object.assign(ex,JSON.parse(String(await py.runPythonAsync('bridge.json.dumps(bridge.SESSION.analysis)'))));
 } finally {py.globals.delete('retained_analysis_json');}
 expect(ex.source_amount_requirements).toHaveLength(3);
 const original=structuredClone(ex),form=reviewMarkup(ex);
 expect(form.indexOf('id="review-add-line"')).toBeLessThan(form.indexOf('class="source-amount-review"'));
 const warnings=sourceReviewIssues(ex);
 expect(warnings.some((issue:any)=>issue.field==='line_items')).toBe(false);
 expect(warnings.some((issue:any)=>issue.field==='due_days')).toBe(true);
 const missing={...ex,source_amount_requirements:ex.source_amount_requirements.slice(1)};
 expect(sourceReviewIssues(missing).some((issue:any)=>issue.message.includes('GBP 225'))).toBe(true);
 expect(ex).toEqual(original);
 const checked=await call('review',{text,fields:fieldsFromExtraction(ex),confirmed:true,source_resolutions:{}});
 expect(checked.result.valid).toBe(false);
 expect(checked.result.issues.filter((issue:any)=>String(issue.field).startsWith('source_amounts.'))).toHaveLength(3);
 expect(checked.state.mock_requests).toBe(0);
});


it('distinguishes edited recipient fields from retained extraction warnings without treating them as checked', async () => {
 const {text,original,fields}=await analyze();
 const before=JSON.stringify(original),html=sourceReviewWarningsMarkup(original,fields);
 expect(html).toContain('data-source-warning-field="client_email" data-source-warning-state="edited"');
 expect(html).toContain('Recipient edited. Confirm it and check the invoice fields.');
 expect(html).toContain('<summary>Original extraction warning</summary>');
 expect(html).not.toContain('<span>No client email found');
 const invalid=await call('review',{text,fields:{...fields,client_email:'not-an-email'},confirmed:true});
 expect(invalid.result.valid).toBe(false);
 expect(invalid.state.mock_requests).toBe(0);
 const checked=await call('review',{text,fields,confirmed:true});
 expect(checked.result.valid).toBe(true);
 expect(sourceReviewWarningsMarkup(original,fields,true)).toContain('Edited recipient checked.');
 expect(checked.state.mock_requests).toBe(0);
 expect(JSON.stringify(original)).toBe(before);
});

it('restores the original warning when recipient edits are cleared and retains unrelated conditions',()=>{
 const ex={client_name:'',client_email:'',line_items:[],issues:[
  {field:'client_email',severity:'error',message:'No client email found; PayPal needs a recipient email to send an invoice.'},
  {field:'due_days',severity:'error',message:'Payment begins after approval; confirm the agreed deadline.'},
 ]};
 const original=fieldsFromExtraction(ex),edited={...original,client_email:'accounts@example.test'};
 const html=sourceReviewWarningsMarkup(ex,edited);
 expect(html).toContain('<span>Payment begins after approval; confirm the agreed deadline.</span>');
 expect(html).toContain('data-source-warning-state="edited"');
 const cleared=sourceReviewWarningsMarkup(ex,{...edited,client_email:'  '});
 expect(cleared).not.toContain('data-source-warning-state="edited"');
 expect(cleared).toContain('<span>No client email found; PayPal needs a recipient email to send an invoice.</span>');
});


it('offers explicit source exclusions while preserving the unchanged invoice and mandatory field check', async () => {
 await call('reset');
 const text="Hi Rowan, could you invoice Ashdown Learning at accounts@ashdown.example for the workshop project? We're going with four half-day facilitation sessions at GBP 180 per session and twelve printed field guides at GBP 7.50 each. There's a fixed GBP 18 courier charge for the packs. The optional GBP 85 recording edit has not been approved, so leave that off this invoice. Accounts say they transferred GBP 100 yesterday; please don't mark it received until you can verify it. Use purchase order AL-2026-319. Payment is due 21 days after the invoice date.";
 const ex=(await call('analyze',{text})).result,original=structuredClone(ex),fields=fieldsFromExtraction(ex);
 const requirements=ex.source_amount_requirements;
 expect(requirements).toHaveLength(2);
 const form=reviewMarkup(ex);
 const paymentRow=form.split('data-source-decision-kind="payment"')[1].split('</fieldset>')[0];
 expect(paymentRow).toContain('How should this reported payment be handled?');
 expect(paymentRow).toContain('Payment not recorded');
 expect(paymentRow).toContain('Why this payment is not recorded');
 expect(paymentRow).not.toContain('value="line:');
 expect(paymentRow).not.toContain('Not billed');
 expect(form).toContain('Which invoice item includes this amount?');

 const choices=requirements.map((entry:any)=>sourceExclusionChoice(entry.quote,fields));
 expect(choices.map((choice:any)=>choice?.label)).toEqual(['Leave this work unbilled','Keep this transfer unrecorded']);
 const resolutions=Object.fromEntries(requirements.map((entry:any,index:number)=>[entry.id,{kind:'excluded',detail:choices[index]!.reason}]));
 const before=invoiceDocumentMarkup(fields,ex.totals_by_currency,false,false,ex.line_amounts,text,ex);
 expect(before.match(/data-source-exclude=/g)).toHaveLength(2);
 const chosen=invoiceDocumentMarkup(fields,ex.totals_by_currency,false,false,ex.line_amounts,text,ex,[],resolutions);
 expect(chosen.match(/data-source-decision="excluded"/g)).toHaveLength(2);
 expect(chosen).toContain('Change decision');
 expect(chosen).toContain('Payment not recorded');
 expect(chosen.match(/Not billed on this invoice/g)).toHaveLength(1);
 expect(chosen).toContain('Needs review');
 expect(chosen).not.toContain('data-source-exclude=');
 const unchecked=await call('review',{text,fields,confirmed:false,source_resolutions:resolutions});
 expect(Boolean(unchecked.result?.valid)).toBe(false);
 expect(unchecked.state.ledger).toHaveLength(0);
 expect(unchecked.state.mock_requests).toBe(0);
 const checked=await call('review',{text,fields,confirmed:true,source_resolutions:resolutions});
 expect(checked.result.valid).toBe(true);
 expect(checked.result.total).toBe('828.00');
 expect(checked.result.amount_paid).toBe('0');
 expect(checked.state.ledger).toHaveLength(0);
 expect(checked.state.mock_requests).toBe(0);
 expect(ex).toEqual(original);
});

it('keeps conflicting, mixed and already-paid source decisions in the full existing controls', () => {
 const fields=fieldsFromExtraction({line_items:[],amount_paid:'0'});
 const payment="Accounts transferred GBP 100; don't mark it received until you can verify it.";
 expect(sourceExclusionChoice(payment,fields)?.label).toBe('Keep this transfer unrecorded');
 expect(sourceExclusionChoice(payment,{...fields,amount_paid:'100'})).toBeNull();
 expect(sourceExclusionChoice(payment,{...fields,amount_paid:''})).toBeNull();
 for(const quote of [
  'Optional GBP 85 work is not approved but is now approved; leave it off this invoice.',
  'GBP 85 work is not approved; leave it off this invoice unless the client agrees.',
  'GBP 85 work is not approved; leave it off this invoice and add GBP 20 delivery.',
  'GBP 85 work is not approved.',
  'A GBP 100 payment was verified yesterday.',
  'A fixed GBP 85 fee is agreed.',
 ])expect(sourceExclusionChoice(quote,fields)).toBeNull();
});

it('removes the source shortcut when invoice work changes and escapes the recorded human reason', () => {
 const quote='Optional GBP 85 work has not been approved, so leave it off this invoice.';
 const ex={client_name:'Studio',client_email:'accounts@example.test',amount_paid:'0',due_days:7,
  line_items:[{desc:'Sessions',qty:'2',unit_price:'100',currency:'GBP',unit:'sessions'}],
  source_amount_requirements:[{id:'optional',quote}],issues:[]};
 const fields=fieldsFromExtraction(ex);
 expect(invoiceDocumentMarkup(fields,[],false,false,[],'',ex)).toContain('data-source-exclude="optional"');
 const changed={...fields,line_items:[...fields.line_items,{desc:'Optional work',qty:'1',unit_price:'85',currency:'GBP',unit:''}]};
 const paper=invoiceDocumentMarkup(changed,[],false,true,[],'',ex);
 expect(paper).not.toContain('data-source-exclude=');
 expect(paper).toContain('Review charge');
 const excluded=invoiceDocumentMarkup(fields,[],false,false,[],'',ex,[],{optional:{kind:'excluded',detail:'Client rejected <optional> work & requested no charge.'}});
 expect(excluded).toContain('Client rejected &lt;optional&gt; work &amp; requested no charge.');
});
