import {displayMoney} from './saved-invoice';

export type ReviewLine = {desc: string; qty: string; unit_price: string; currency: string; unit: string};
export type ReviewFields = {client_name: string; client_email: string; due_days: string; due_date?: string; note?: string; amount_paid: string; line_items: ReviewLine[]};

const esc = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]!));

export function fieldsFromExtraction(ex: any): ReviewFields {
  const chooseTerms = (ex.issues ?? []).some((issue: any) => issue.field === 'due_date' && issue.severity === 'error');
  return {
    client_name: String(ex.client_name ?? ''), client_email: String(ex.client_email ?? ''),
    note: String(ex.note ?? 'Thank you for your business.'),
    due_days: chooseTerms ? '' : String(ex.due_days ?? ''), ...(chooseTerms || ex.due_date ? {due_date: chooseTerms ? '' : String(ex.due_date)} : {}), amount_paid: String(ex.amount_paid ?? '0'),
    line_items: (ex.line_items ?? []).map((row: any) => ({
      desc: String(row.desc ?? ''), qty: String(row.qty ?? ''), unit_price: String(row.unit_price ?? ''),
      currency: String(row.currency ?? ex.currency ?? ''), unit: String(row.unit ?? ''),
    })),
  };
}

export function readReviewFields(form: HTMLFormElement): ReviewFields {
  const value = (name: string) => (form.elements.namedItem(name) as HTMLInputElement).value;
  return {
    client_name: value('client_name'), client_email: value('client_email'), note: value('note'),
    due_days: value('term_basis') === 'date' ? '' : value('due_days'),
    ...(value('term_basis') === 'date' ? {due_date: value('due_date')} : {}), amount_paid: value('amount_paid'),
    line_items: Array.from(form.querySelectorAll<HTMLElement>('[data-review-line]')).map(row => {
      const field = (name: string) => row.querySelector<HTMLInputElement>(`[data-field="${name}"]`)!.value;
      return {desc: field('desc'), qty: field('qty'), unit_price: field('unit_price'), currency: field('currency'), unit: field('unit')};
    }),
  };
}

export function reviewLinesMarkup(lines: ReviewLine[], currencies: string[]): string {
  if (!lines.length) return '<p class="empty">No line items. Add the work and price you have confirmed.</p>';
  return lines.map((row, index) => {
    const prefix = `review-line-${index}`;
    const options = Array.from(new Set(['', ...currencies, row.currency])).map(currency => `<option value="${esc(currency)}"${currency === row.currency ? ' selected' : ''}>${esc(currency || 'Choose currency')}</option>`).join('');
    return `<fieldset class="review-line" data-review-line><legend>Line ${index + 1}</legend>
      <label class="review-description" for="${prefix}-desc">Description<input id="${prefix}-desc" data-field="desc" value="${esc(row.desc)}" required></label>
      <label for="${prefix}-qty">Qty<input id="${prefix}-qty" data-field="qty" inputmode="decimal" value="${esc(row.qty)}" placeholder="Confirm quantity" required></label>
      <label for="${prefix}-price">Rate<input id="${prefix}-price" data-field="unit_price" inputmode="decimal" value="${esc(row.unit_price)}" required></label>
      <label for="${prefix}-currency">Currency<select id="${prefix}-currency" data-field="currency">${options}</select></label>
      <label class="review-unit" for="${prefix}-unit">Unit<input id="${prefix}-unit" data-field="unit" value="${esc(row.unit)}" placeholder="hours, items…"></label>
      <button class="button button-link review-remove" type="button" data-remove-line="${index}" aria-label="Remove line ${index + 1}">×</button>
    </fieldset>`;
  }).join('');
}


export type SourceResolution = {kind:string;detail:string};
export type SourceExclusionChoice = {kind:'work'|'payment';label:string;reason:string};
/** A visible human choice for explicit instructions; it never changes a field by itself. */
export function sourceExclusionChoice(quote:unknown, fields:ReviewFields):SourceExclusionChoice|null {
 if(typeof quote!=='string')return null;
 const text=quote.normalize('NFKC').replace(/[’‘]/g,"'").toLowerCase();
 // Mixed or conditional instructions need the full item/reason control.
 if(/\b(?:but|however|unless|except|if|once|when)\b/.test(text))return null;
 const amounts=quote.match(/(?:\b[A-Z]{3}|[$€£¥₹])\s*\d+(?:[.,]\d+)*/g)||[];
 if(amounts.length!==1)return null;
 const unapproved=/\b(?:not(?: yet)? approved|has not been approved|hasn't been approved)\b/.test(text);
 const leaveOut=/\b(?:leave\b[^.!?]{0,45}\boff(?: this)? invoice|do not bill|don't bill|leave\b[^.!?]{0,45}\bunbilled)\b/.test(text);
 if(unapproved&&leaveOut)return {kind:'work',label:'Leave this work unbilled',reason:'This work is not approved for this invoice.'};
 const zeroPaid=/^\+?0+(?:\.0+)?$/.test(fields.amount_paid.trim());
 const payment=/\b(?:transferred|transfer|payment|deposit)\b/.test(text);
 const unverified=/\b(?:unverified|not(?: yet)? verified)\b/.test(text)||
  /\b(?:do not|don't)\s+mark\b[^.!?]{0,45}\breceived\b[^.!?]{0,45}\buntil\b[^.!?]{0,45}\bverif/.test(text);
 if(zeroPaid&&payment&&unverified)return {kind:'payment',label:'Keep this transfer unrecorded',reason:'The reported payment is unverified; no received payment is recorded.'};
 return null;
}

export function readSourceResolutions(form: HTMLFormElement): Record<string,{kind:string;detail:string}> {
 return Object.fromEntries(Array.from(form.querySelectorAll<HTMLElement>('[data-source-amount]')).map(row=>{
 const selected=row.querySelector<HTMLSelectElement>('select')!.value;
 return [row.dataset.sourceAmount!,selected.startsWith('line:')?{kind:'included',detail:selected.slice(5)}:
 {kind:selected,detail:row.querySelector<HTMLTextAreaElement>('textarea')!.value}];
 }));
}

function sourceAmountOptions(fields:ReviewFields,payment=false):string {
 if(payment)return '<option value="">Choose a payment decision</option><option value="excluded">Payment not recorded</option>';
 return '<option value="">Choose an invoice item or leave unbilled</option>'+
 fields.line_items.map((row,index)=>'<option value="line:'+(index+1)+'">'+esc(row.desc||'Item '+(index+1))+' · '+esc(row.currency)+' '+esc(row.unit_price)+'</option>').join('')+
 '<option value="excluded">Not billed on this invoice</option>';
}

export function syncSourceResolutions(form:HTMLFormElement):void {
 const fields=readReviewFields(form);
 for(const row of Array.from(form.querySelectorAll<HTMLElement>('[data-source-amount]'))){
  const payment=sourceExclusionChoice(row.querySelector('p')?.textContent,fields)?.kind==='payment';
  const options=sourceAmountOptions(fields,payment);
  const select=row.querySelector<HTMLSelectElement>('select')!,value=select.value;
  if(select.innerHTML!==options){select.innerHTML=options;select.value=value;}
  row.dataset.sourceDecisionKind=payment?'payment':'work';
  const question=row.querySelector<HTMLElement>('[data-source-question]');
  if(question)question.textContent=payment?'How should this reported payment be handled?':'Which invoice item includes this amount?';
  const reason=payment?'Why this payment is not recorded':'Reason not billed';
  const label=row.querySelector<HTMLElement>('[data-source-reason-label]');
  if(label)label.textContent=reason;
  row.querySelector<HTMLTextAreaElement>('textarea')!.setAttribute('aria-label',reason);
  row.querySelector<HTMLElement>('[data-source-reason]')!.hidden=select.value!=='excluded';
 }
}

export function sourceAmountMarkup(ex:any):string {
 const fields=fieldsFromExtraction(ex);
 return (ex.source_amount_requirements||[]).map((entry:any)=>{
  const payment=sourceExclusionChoice(entry.quote,fields)?.kind==='payment';
  const reason=payment?'Why this payment is not recorded':'Reason not billed';
  return '<fieldset class="review-line" data-source-amount="'+esc(entry.id)+'" data-source-decision-kind="'+(payment?'payment':'work')+'"><legend>Account for this source amount</legend><p>'+esc(entry.quote)+'</p><label><span data-source-question>'+(payment?'How should this reported payment be handled?':'Which invoice item includes this amount?')+'</span><select id="source-amount-'+esc(entry.id)+'">'+sourceAmountOptions(fields,payment)+'</select></label><label data-source-reason hidden><span data-source-reason-label>'+reason+'</span><textarea aria-label="'+reason+'" rows="3" autocomplete="off"></textarea></label></fieldset>';
 }).join('');
}

/** Warnings already represented by an explicit work decision need no second alert. */
function issueShownWithWork(ex:any, issue:any):boolean {
 const requirements=ex.source_amount_requirements||[];
 const emptyWork=Array.isArray(ex.line_items)&&ex.line_items.length===0&&requirements.length>0;
 if(emptyWork&&issue.field==='line_items'&&issue.message==='No priced line items found.')return true;
 if(emptyWork&&issue.field==='currency'&&issue.message==='Invoice currency is required.')return true;
 if(issue.field!=='line_items')return false;
 const group=requirements.filter((entry:any)=>entry.source_issue===issue.message);
 if(group.length>1&&group.every((entry:any)=>entry.source_issue_count===group.length)&&
    new Set(group.map((entry:any)=>entry.id)).size===group.length)return true;
 return requirements.some((entry:any)=>typeof entry.quote==='string'&&(
  issue.message==="A source amount was not included in the invoice; review it before drafting: '"+entry.quote+"'"||
  issue.message==='A source amount was not included in the invoice; review it before drafting: '+JSON.stringify(entry.quote)));
}

export function sourceReviewIssues(ex:any):any[] {
 return (ex.issues||[]).filter((issue:any)=>(issue.severity==='error'||issue.severity==='warning')&&
  !(issue.severity==='error'&&/^line_items\[\d+\]\.(?:desc|qty|unit_price|currency|unit)$/.test(String(issue.field)))&&
  !issueShownWithWork(ex,issue));
}

/** Keep extraction history distinct from edited, still-unchecked recipient fields. */
export function sourceReviewWarningsMarkup(ex:any, fields:ReviewFields=fieldsFromExtraction(ex), checked=false):string {
 return sourceReviewIssues(ex).map((issue:any)=>{
  const field=String(issue.field||''),item=/^line_items\[(\d+)\]/.exec(field);
  const label=item?ex.line_items?.[Number(item[1])]?.desc||'Invoice item '+(Number(item[1])+1):
   ({client_name:'Client',client_email:'Recipient',currency:'Currency',due_days:'Payment terms',due_date:'Payment terms',amount_paid:'Prior payment',note:'Invoice note',line_items:'Invoice items'} as Record<string,string>)[field]||'Invoice';
  const recipient=field==='client_name'||field==='client_email';
  const changed=recipient&&String(fields[field as 'client_name'|'client_email']).trim()!==String(ex[field]??'').trim();
  const state=changed?(checked?'checked':'edited'):'original';
  const message=changed?(checked?'Edited '+label.toLowerCase()+' checked. Original warning retained below.':
   label+' edited. Confirm it and check the invoice fields.'):issue.message;
  return '<li class="issue '+esc(changed?'info':issue.severity)+'" data-source-warning-field="'+esc(field)+'" data-source-warning-state="'+state+'"><b>'+esc(label)+'</b><span>'+esc(message)+'</span>'+
   (changed?'<details class="source-warning-history"><summary>Original extraction warning</summary><p>'+esc(issue.message)+'</p></details>':'')+'</li>';
 }).join('');
}

export function reviewMarkup(ex: any): string {
  const fields = fieldsFromExtraction(ex);
  const dateTerms = fields.due_date !== undefined;
  const incomplete = !fields.line_items.length || (ex.confidence || 0) < 0.5 || (ex.issues ?? []).some((issue:any) => issue.severity === 'error');
  const needsWork = fields.line_items.length === 0 && (ex.source_amount_requirements || []).length > 0;
  const contextInvalid = (ex.issues || []).some((issue:any) => issue.severity === 'error' && /^(?:client_|due_|amount_paid$|note$)/.test(String(issue.field)));
  const totalsMarkup = `<ul class="review-totals" aria-label="Proposed totals by currency">${(ex.totals_by_currency ?? [{currency:ex.currency,total:ex.total}]).map((total:any)=>`<li>${esc(total.currency || 'Unclear currency')} ${esc(total.total ?? 'Unclear amount')}</li>`).join('')}</ul>`;
  const reviewStatus = `<section id="review-result" class="review-result" data-incomplete="${incomplete}" role="status" aria-live="polite"><strong>${incomplete ? 'Amount needs review' : 'Proposed total'}</strong>${needsWork?'':totalsMarkup}</section>`;
  const recipient = `<div class="review-fields review-client-fields">
        <label for="review-client-name">Bill to<input id="review-client-name" name="client_name" value="${esc(fields.client_name)}" autocomplete="off"></label>
        <label for="review-client-email">Email<input id="review-client-email" name="client_email" type="email" value="${esc(fields.client_email)}" autocomplete="off" required></label>
        </div>`;
  const terms = `<div class="review-fields review-terms-row"><div class="review-payment-terms">
          <label for="review-term-basis">Payment terms<select id="review-term-basis" name="term_basis"><option value="days"${dateTerms ? '' : ' selected'}>Days after invoice</option><option value="date"${dateTerms ? ' selected' : ''}>Calendar date</option></select></label>
          <label for="review-due-days" id="review-days-field"${dateTerms ? ' hidden' : ''}>Due in days<input id="review-due-days" name="due_days" inputmode="numeric" value="${esc(fields.due_days)}"${dateTerms ? ' disabled' : ''}></label>
          <label for="review-due-date" id="review-date-field"${dateTerms ? '' : ' hidden'}>Due date<input id="review-due-date" name="due_date" type="date" value="${esc(fields.due_date || '')}"${dateTerms ? '' : ' disabled'}></label>
        </div>
        <label for="review-amount-paid" class="review-paid-field"><span id="review-paid-label">Already paid</span><input id="review-amount-paid" name="amount_paid" inputmode="decimal" value="${esc(fields.amount_paid)}" aria-labelledby="review-paid-label" required></label>
      </div>`;
  const addLine = '<button id="review-add-line" class="button button-secondary" type="button">+ Add line item</button>';
  const addFirst = needsWork && (ex.source_amount_requirements || []).length > 1;
  const work = `<section class="review-work" aria-label="Invoice items"><div id="review-lines">${reviewLinesMarkup(fields.line_items, ex.review_currencies ?? [])}</div>
      ${addFirst?addLine:''}<div class="source-amount-review">${sourceAmountMarkup(ex)}</div>
      ${addFirst?'':addLine}</section>`;
  const note = `<div class="review-fields review-note-fields"><label for="review-note">Invoice note<textarea id="review-note" name="note" rows="3" maxlength="4000" placeholder="Purchase order, project or client instructions">${esc(fields.note)}</textarea></label></div>`;
  const context = `<details class="review-context"${contextInvalid?' open':''}><summary><strong>${esc(fields.client_name || 'Recipient')}</strong><span>Recipient, terms and note</span></summary>${recipient}${terms}${note}</details>`;
  return `<details id="review-editor" class="review-editor"><summary>Edit invoice details</summary>
    <form id="review-form" class="${needsWork?'review-missing-work':''}" novalidate>
      ${needsWork?reviewStatus+work+context:`<div class="invoice-document-head">${reviewStatus}${recipient}</div>${terms}${work}${note}`}
    </form>
  </details>
  <div class="invoice-review-actions">
    <label class="review-confirm" for="review-confirm"><input id="review-confirm" type="checkbox" form="review-form">I checked the recipient, terms, amounts, note and original warnings.</label>
    <button id="review-check" class="button button-primary" type="submit" form="review-form" disabled>Check invoice fields</button>
  </div>`;
}

const reviewFieldNames: Record<string,string> = {client_name:'Bill to',client_email:'Email',due_days:'Due in days',due_date:'Due date',amount_paid:'Already paid',note:'Invoice note',currency:'Currency',desc:'Description',qty:'Quantity',unit_price:'Rate',unit:'Unit',line_items:'Invoice items'};
function reviewFieldLabel(field: unknown): string {
 const key=String(field||'');
 const item=/^line_items\[(\d+)\](?:\.(\w+))?$/.exec(key);
 return item?'Item '+(Number(item[1])+1)+(item[2]?' · '+(reviewFieldNames[item[2]]||'Details'):''):reviewFieldNames[key]||'Invoice details';
}

/** Server review issues describe this exact field version and expire on edits. */
export function clearReviewFieldIssues(form: HTMLFormElement): void {
 for(const input of Array.from(form.querySelectorAll<HTMLElement>('[data-review-error]'))){
  const id=input.dataset.reviewError;
  const remaining=(input.getAttribute('aria-describedby')||'').split(/\s+/).filter(value=>value&&value!==id);
  if(remaining.length)input.setAttribute('aria-describedby',remaining.join(' '));else input.removeAttribute('aria-describedby');
  input.removeAttribute('aria-invalid');delete input.dataset.reviewError;
 }
 for(const error of Array.from(form.querySelectorAll('.review-field-error')))error.remove();
}

export function applyReviewFieldIssues(form: HTMLFormElement, issues: any[]): HTMLElement|null {
 clearReviewFieldIssues(form);
 const messages=new Map<HTMLElement,string[]>();
 for(const issue of issues){
  if(issue.severity!=='error')continue;
  const field=String(issue.field||'');
  const item=/^line_items\[(\d+)\]\.(desc|qty|unit_price|currency|unit)$/.exec(field);
  let input: HTMLElement|null=null;
  if(item)input=form.querySelectorAll<HTMLElement>('[data-review-line]')[Number(item[1])]?.querySelector<HTMLElement>('[data-field="'+item[2]+'"]')||null;
  else if(field.startsWith('source_amounts.'))input=form.querySelector<HTMLElement>('[data-source-amount="'+field.slice(15)+'"] select');
  else if(['client_name','client_email','due_days','due_date','amount_paid','note'].includes(field))input=form.elements.namedItem(field) as HTMLElement|null;
  if(!input)continue;
  messages.set(input,[...(messages.get(input)||[]),String(issue.message||'Check this value.')]);
 }
 let first: HTMLElement|null=null;
 for(const [input,errors] of messages){
  const id=input.id+'-error',message=form.ownerDocument.createElement('span');
  message.id=id;message.className='review-field-error';message.textContent=errors.join(' ');
  input.after(message);input.dataset.reviewError=id;input.setAttribute('aria-invalid','true');
  input.setAttribute('aria-describedby',((input.getAttribute('aria-describedby')||'')+' '+id).trim());
  first??=input;
 }
 return first;
}

export function reviewResultMarkup(result: any): string {
  const issues = (result.issues ?? []).filter((issue:any)=>!String(issue.field||'').startsWith('source_amounts.')&&!issueShownWithWork(result,issue)).map((issue: any) => `<li class="issue ${esc(issue.severity)}"><b>${esc(reviewFieldLabel(issue.field))}</b><span>${esc(issue.message)}</span></li>`).join('');
  const financials = result.financials_by_currency ?? [];
  const totals = (financials.length ? financials : result.totals_by_currency ?? []).map((total: any) => `<li>${esc(displayMoney(financials.length ? total.due : total.total,total.currency))}</li>`).join('');
  return `<strong>${result.valid ? financials.length ? 'Checked amount due' : 'Checked total' : 'Check the highlighted fields'}</strong>
    ${totals && result.valid ? `<ul class="review-totals" aria-label="Checked totals by currency">${totals}</ul>` : ''}
    ${issues ? `<ul class="issue-list">${issues}</ul>` : ''}
    `;
}

/** Show one payment basis; switching always requires the ordinary fresh review. */
export function syncReviewTerms(form: HTMLFormElement): void {
  const dated = (form.elements.namedItem('term_basis') as HTMLSelectElement).value === 'date';
  (form.querySelector('#review-days-field') as HTMLElement).hidden = dated;
  (form.querySelector('#review-date-field') as HTMLElement).hidden = !dated;
  (form.elements.namedItem('due_days') as HTMLInputElement).disabled = dated;
  (form.elements.namedItem('due_date') as HTMLInputElement).disabled = !dated;
}

export type SourceSpan = {start: number; end: number; text: string};

/** A literal quote locator. Ambiguous or absent text is deliberately not linked. */
export function sourceSpan(source: string, value: string, within?: SourceSpan): SourceSpan | null {
 if (!value.trim()) return null;
 const start = within?.start ?? 0, end = within?.end ?? source.length;
 const part = source.slice(start, end), matches: {index:number; length:number}[] = [];
 const escaped=[...value].map(character=>'\\^$.*+?()[]{}|'.includes(character)?'\\'+character:character).join('');
 const word = (character: string | undefined) => Boolean(character && /[\p{L}\p{N}_]/u.test(character));
 for (const match of part.matchAll(new RegExp(escaped,'giu'))) {
  const at=match.index!, before=part[at-1], after=part[at+match[0].length];
  if (!(word(value[0]) && word(before)) && !(word(value[value.length-1]) && word(after))) matches.push({index:at,length:match[0].length});
 }
 if (matches.length !== 1) return null;
 const absolute = start + matches[0].index, finish=absolute+matches[0].length;
 return {start:absolute,end:finish,text:source.slice(absolute,finish)};
}

/** Use the literal item wording to keep a price quote with its own source line. */
export function sourceLineForItem(source: string, description: string): SourceSpan | null {
 const match = sourceSpan(source, description);
 if (!match) return null;
 const start = source.lastIndexOf('\n', match.start - 1) + 1;
 const newline = source.indexOf('\n', match.end), end = newline < 0 ? source.length : newline;
 return {start,end,text:source.slice(start,end)};
}

function quotedMoney(source: string, value: string, currency: string, line: SourceSpan | null): SourceSpan | null {
 if (!line || !/^\d+(?:\.\d+)?$/.test(value)) return null;
 const symbols: Record<string,string> = {USD:'$',CAD:'$',AUD:'$',NZD:'$',SGD:'$',HKD:'$',EUR:'€',GBP:'£',JPY:'¥',CNY:'¥',INR:'₹'};
 const decimal=(amount:string)=>amount.replace(/^0+(?=\d)/,'').replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'');
 const matches:SourceSpan[] = [];
 const pattern = /(?:[A-Z]{3}|[$€£¥₹])\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?/g;
 for (const match of line.text.matchAll(pattern)) {
  const symbol = match[0].match(/^[A-Z]{3}|^[$€£¥₹]/)![0];
  const amount = match[0].replace(/^[A-Z]{3}|^[$€£¥₹]/,'').replaceAll(',','').trim();
  if ((symbol === currency || symbol === symbols[currency]) && decimal(amount) === decimal(value)) {
   const start=line.start+match.index!;
   matches.push({start,end:start+match[0].length,text:match[0]});
  }
 }
 return matches.length === 1 ? matches[0] : null;
}


function extractedSourceQuote(source: string, quote: any, value: string, within?: SourceSpan | null): SourceSpan | null {
 if (!quote || typeof quote.text !== 'string' || quote.value !== value || within === null) return null;
 return sourceSpan(source,quote.text,within);
}

function sourceQuantity(source: string, row: ReviewLine, line: SourceSpan | null): SourceSpan | null {
 if(!line || !row.qty) return null;
 const labels=[...(row.unit ? [row.qty+' '+row.unit] : []),row.qty+' x',row.qty+'x',row.qty+' ×',row.qty+'×'];
 const found=labels.map(label=>sourceSpan(source,label,line)).filter((quote):quote is SourceSpan=>Boolean(quote));
 const unique=found.filter((quote,index)=>found.findIndex(other=>other.start===quote.start&&other.end===quote.end)===index);
 return unique.length===1?unique[0]:null;
}
function sourceTerms(source: string, fields: ReviewFields, original: ReviewFields | null, references?: any): SourceSpan | null {
 if(!original || fields.due_days!==original.due_days || fields.due_date!==original.due_date) return null;
 if(references) return extractedSourceQuote(source,references.payment_terms,fields.due_date||fields.due_days);
 if(fields.due_date) return sourceSpan(source,fields.due_date);
 if(fields.due_days==='0') return sourceSpan(source,'Due on receipt');
 if(!/^\d+$/.test(fields.due_days)) return null;
 const matches=[...source.matchAll(new RegExp('\\bNet(?:\\s+|-)?'+fields.due_days+'\\b','gi'))];
 if(matches.length!==1) return null;
 const match=matches[0],start=match.index!;
 return {start,end:start+match[0].length,text:match[0]};
}

function sourceHighlight(label: string, quote: SourceSpan | null, field: string): string {
 if (!quote) return esc(label);
 return '<button type="button" class="source-highlight" data-source-start="'+quote.start+'" data-source-end="'+quote.end+'" data-source-text="'+esc(quote.text)+'" aria-label="Show source for '+esc(field)+': '+esc(label)+'"><mark>'+esc(label)+'</mark></button>';
}

/** One document view of the existing editable form; amounts remain server-derived. */
export function invoiceDocumentMarkup(fields: ReviewFields, totals: any[], checked: boolean, dirty: boolean, lineAmounts: (string|null)[] = [], source = '', extraction?: any, financials: any[] = [], sourceResolutions:Record<string,SourceResolution> = {}): string {
 const money=(currency:string,value:unknown)=>/^[+-]?\d+(?:\.\d*)?$/.test(String(value??''))?displayMoney(value,currency):'Amount unclear';
 const original = extraction ? fieldsFromExtraction(extraction) : null;
 const same = (key: 'client_name'|'client_email') => original?.[key] === fields[key] ? sourceSpan(source,fields[key]) : null;
 const recipientUnchanged=original?.client_name===fields.client_name&&original?.client_email===fields.client_email;
 const recipientNeedsReview=!checked&&recipientUnchanged&&(extraction?.issues||[]).some((issue:any)=>
  issue.severity==='error'&&/^client_(?:name|email)$/.test(String(issue.field)));
 const recipientConflict=recipientNeedsReview&&(extraction?.issues||[]).some((issue:any)=>
  issue.severity==='error'&&String(issue.message).startsWith('Conflicting explicit invoice recipients or delivery addresses;'));
 const recipientAction=recipientNeedsReview
  ?'<p class="document-recipient-issue">'+(recipientConflict?'The request names different billing recipients.':'Complete the recipient details.')+'</p><button type="button" class="button button-link" data-edit-document>Choose recipient</button>':'';
 const terms=fields.due_date ? 'Due '+fields.due_date : fields.due_days==='0' ? 'Due on receipt' : fields.due_days ? 'Due in '+fields.due_days+' days' : 'Payment terms needed';
 const amountBlocked=!checked&&(extraction?.issues||[]).some((issue:any)=>
  issue.severity==='error'&&/^(?:line_items(?:\[|$)|currency$|amount_paid$)/.test(String(issue.field)));
 const summary=!dirty&&!amountBlocked&&financials.length?financials:null;
 const paid=summary?.some(t=>/[1-9]/.test(String(t.paid)));
 const amounts=dirty?'Check changes':amountBlocked?'Needs review':summary?summary.map(t=>esc(money(t.currency,t.due))).join(' · '):totals.map(t=>esc(money(t.currency,t.total))).join(' · ')||'Amount needs review';
 const missingCharges=checked?'':(extraction?.source_amount_requirements||[]).map((entry:any)=>{
  const decision=sourceResolutions[entry.id];
  const excluded=decision?.kind==='excluded'&&Boolean(decision.detail.trim());
  const included=decision?.kind==='included'&&/^[1-9]\d*$/.test(decision.detail)&&Number(decision.detail)<=fields.line_items.length;
  const unchangedWork=original&&JSON.stringify(fields.line_items)===JSON.stringify(original.line_items);
  const suggested=sourceExclusionChoice(entry.quote,fields);
  const choice=!excluded&&!included&&unchangedWork?suggested:null;
  const paymentExcluded=excluded&&suggested?.kind==='payment';
  const label=excluded?(paymentExcluded?'Payment not recorded':'Not billed on this invoice'):included?'Assigned to item '+decision.detail:'Source amount needs a decision';
  const action='<div class="document-source-actions">'+
   (choice?'<button type="button" class="button button-secondary" data-source-exclude="'+esc(entry.id)+'">'+esc(choice.label)+'</button>':'')+
   '<button type="button" class="document-remove-item" data-review-charge="'+esc(entry.id)+'">'+(excluded||included?'Change decision':choice?'Other decision':'Review charge')+'</button></div>';
  return '<tr class="document-missing-charge" data-source-decision="'+(excluded?'excluded':included?'included':'unresolved')+'"><td><span class="document-item-issue">'+esc(label)+'</span>'+esc(entry.quote)+
   (excluded?'<small class="document-source-reason">'+esc(decision.detail)+'</small>':'')+action+'</td><td>'+ (excluded?(paymentExcluded?'Not recorded':'Not billed'):included?'Item '+esc(decision.detail):'Needs review')+'</td></tr>';
 }).join('');
 const rows=fields.line_items.map((row,index)=>{
  // Deleting an earlier item changes its display index, not its original warning.
  const matches=original?.line_items.map((previous,originalIndex)=>({previous,originalIndex}))
   .filter(({previous})=>Object.keys(row).every(key=>row[key as keyof ReviewLine]===previous[key as keyof ReviewLine]))||[];
  const match=matches.length===1?matches[0]:matches.find(candidate=>candidate.originalIndex===index);
  const unchanged=Boolean(match);
  const originalIndex=match?.originalIndex??-1;
  const line=unchanged?sourceLineForItem(source,row.desc):null;
  const description=line?sourceSpan(source,row.desc,line):null;
  const rate=unchanged?quotedMoney(source,row.unit_price,row.currency,line):null;
  const quantity=unchanged?(extraction?.source_references
   ?extractedSourceQuote(source,extraction.source_references.line_items?.[originalIndex]?.qty,row.qty,line)
   :sourceQuantity(source,row,line)):null;
  // Extraction errors describe the original row, not subsequently edited work.
  const reasons=unchanged&&!checked?(extraction?.issues||[]).filter((issue:any)=>
   issue.severity==='error'&&String(issue.field).startsWith('line_items['+originalIndex+'].')
  ):[];
  const rowIssues=reasons.filter((issue:any,index:number)=>reasons.findIndex((other:any)=>other.field===issue.field)===index).map((issue:any)=>{
   const message=String(issue.message||'Review this item against the request.');
   const label=message.startsWith('Source work is optional, conditional or excluded;')
    ?'Optional, conditional or excluded work.':message;
   return '<span class="document-item-issue" title="'+esc(message)+'">'+esc(label)+'</span>';
  }).join('');
  const excluded=reasons.some((issue:any)=>String(issue.message).startsWith('Source work is optional, conditional or excluded;'));
  const correctionField=String(reasons[0]?.field||'').split('.').pop()||'desc';
  const correctionLabels:Record<string,string>={qty:'quantity',unit_price:'rate',currency:'currency',desc:'description',unit:'unit'};
  const correctionLabel=correctionLabels[correctionField]||'item';
  const correctionAction=excluded
   ?'<button type="button" class="document-remove-item" data-remove-line="'+index+'" aria-label="Remove '+esc(row.desc||'item '+(index+1))+' from invoice">Remove item</button>'
   :reasons.some((issue:any)=>String(issue.field).endsWith('.unit'))
    ?'<button type="button" class="document-remove-item" data-edit-unit="'+index+'" aria-label="Edit unit for '+esc(row.desc||'item '+(index+1))+'">Edit unit</button>'
    :reasons.length?'<button type="button" class="document-remove-item" data-edit-item="'+index+'" data-edit-field="'+esc(correctionField)+'" aria-label="Edit '+correctionLabel+' for '+esc(row.desc||'item '+(index+1))+'">Edit '+correctionLabel+'</button>':'';
  return '<tr><td>'+sourceHighlight(row.desc||'Description needed',description,'item '+(index+1))+rowIssues+
   correctionAction+
   '<small>'+sourceHighlight(((row.qty||'?')+' '+row.unit).trim(),quantity,'quantity for item '+(index+1))+' × '+sourceHighlight(money(row.currency,row.unit_price),rate,'rate for item '+(index+1))+'</small></td>'+
   '<td>'+esc(dirty?'Check changes':reasons.length?'Needs review':lineAmounts[index]!==undefined&&lineAmounts[index]!==null?money(row.currency,lineAmounts[index]):'Amount unclear')+'</td></tr>';
 }).join('');
 return '<div class="invoice-letterhead"><span class="document-title">Invoice</span><span class="document-state">'+(dirty?'Review changes':checked?'Checked draft':amountBlocked||recipientNeedsReview?'Needs review':'Proposed draft')+'</span></div>'+
 '<header class="document-heading"><div><p class="document-kicker">Bill to</p><h3>'+sourceHighlight(fields.client_name||'Client name needed',same('client_name'),'client name')+'</h3><p>'+sourceHighlight(fields.client_email||'Recipient email needed',same('client_email'),'recipient email')+'</p>'+recipientAction+'</div>'+
 '<div class="document-amount"><p class="document-kicker">'+(paid?'Due if payment confirmed':summary?'Amount due':checked?'Checked total':'Proposed total')+'</p><strong>'+amounts+'</strong><span>'+sourceHighlight(terms,sourceTerms(source,fields,original,extraction?.source_references),'payment terms')+'</span></div></header>'+
 '<table class="document-items"><thead><tr><th>Description</th><th>Amount</th></tr></thead><tbody>'+((rows+missingCharges)||'<tr><td colspan="2">Add the agreed work to continue.</td></tr>')+'</tbody></table>'+
 (paid?'<dl class="document-financials">'+summary!.map(t=>'<div><dt>Invoice total</dt><dd>'+esc(money(t.currency,t.total))+'</dd></div><div><dt>Reported prior payment</dt><dd>'+esc(money(t.currency,t.paid))+'</dd></div>').join('')+'</dl><p class="document-paid">The draft keeps the full invoice total until this reported payment is separately confirmed and recorded.</p>':!summary&&Number(fields.amount_paid)>0?'<p class="document-paid">Prior payment reported: '+esc(money(new Set(fields.line_items.map(row=>row.currency).filter(Boolean)).size===1?fields.line_items.find(row=>row.currency)!.currency:'',fields.amount_paid))+'</p>':'')+
  '<section class="document-note" aria-label="Invoice note"><div><p class="document-kicker">Note</p><button type="button" class="button button-link" data-edit-note>'+(fields.note?.trim()?'Edit note':'Add note')+'</button></div>'+
  (fields.note?.trim()?'<p class="document-note-text">'+sourceHighlight(fields.note,original?.note===fields.note?extractedSourceQuote(source,extraction?.source_references?.note,fields.note):null,'invoice note')+'</p>':'')+'</section>'+
  (recipientNeedsReview?'':'<button type="button" class="button button-link" data-edit-document>Edit details</button>');
}
