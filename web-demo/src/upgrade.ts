import './upgrade.css';
import {parseSpoken,stem,type Known,type Rate} from './spoken-note';
import {readNote,sampleNoteDataUrl,SAMPLE_REGIONS,type Region} from './photo-note';

const esc=(v:unknown)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const $=<T extends HTMLElement>(s:string)=>(document.querySelector(s)||root.querySelector(s)||photo.querySelector(s)||sheet?.querySelector(s)||null) as T|null;
const LISTEN='http://127.0.0.1:5621/listen';
const MEMORY_KEY='ledgerly.up.clients';
const SAMPLE_PHRASE='invoice Tidewater for two shoot days at nine fifty and forty retouched images';
const SAMPLE_CLIENTS:Known[]=[
 {name:'Tidewater Studio',email:'accounts@tidewater.example',rates:[{desc:'Shoot day',unit_price:'950',currency:'USD'},{desc:'Retouched image',unit_price:'18',currency:'USD'}]},
 {name:'Brightfern Co',email:'maya.chen@brightfern.example',rates:[{desc:'Copywriting',unit_price:'85',currency:'USD',unit:'hours'}]},
];
const SAMPLE_INVOICES=[
 {invoice_id:'sample-1',invoice_number:'0042',client_name:'Tidewater Studio',client_email:'accounts@tidewater.example',currency:'USD',total:'2620.00',balance:'2620.00',status:'SENT',due_on:'2026-11-01'},
 {invoice_id:'sample-2',invoice_number:'0043',client_name:'Brightfern Co',client_email:'maya.chen@brightfern.example',currency:'USD',total:'1320.00',balance:'0.00',status:'PAID',due_on:'2026-11-20'},
 {invoice_id:'sample-3',invoice_number:'0044',client_name:'Harbor Bakery',client_email:'hello@harbor.example',currency:'USD',total:'480.00',balance:'480.00',status:'DRAFT',due_on:null},
];

/* ---------- remembered clients ---------- */
let sampleOnly=true;
function load():Known[]{try{const v=JSON.parse(localStorage.getItem(MEMORY_KEY)||'null');if(Array.isArray(v)&&v.length){sampleOnly=false;return v;}}catch{}sampleOnly=true;return SAMPLE_CLIENTS;}
let clients:Known[]=load();
function remember(state:any){
 let list:Known[]=[];try{const v=JSON.parse(localStorage.getItem(MEMORY_KEY)||'[]');if(Array.isArray(v))list=v;}catch{}
 const details=state?.invoice_details||{};
 for(const e of state?.ledger||[]){
  if(!e.client_email)continue;
  const email=String(e.client_email).trim(),low=email.toLowerCase();
  let c=list.find(x=>x.email.toLowerCase()===low);
  if(!c){c={name:String(e.client_name||email),email,rates:[]};list.push(c);}
  if(e.client_name)c.name=String(e.client_name);
  const d=details[e.invoice_id];
  for(const it of d?.items||[]){
   const desc=String(it.name||it.description||'').trim(),price=String(it.unit_amount?.value??it.unit_price??'').trim();
   if(!desc||!/^\d+(\.\d+)?$/.test(price))continue;
   const rate:Rate={desc,unit_price:price.replace(/\.00$/,''),currency:String(it.unit_amount?.currency_code||e.currency||'USD')};
   c.rates=[rate,...c.rates.filter(r=>r.desc.toLowerCase()!==desc.toLowerCase())].slice(0,6);
  }
 }
 if(list.length){try{localStorage.setItem(MEMORY_KEY,JSON.stringify(list));}catch{}}
 clients=load();
}

/* ---------- source text helpers ---------- */
const area=()=>$<HTMLTextAreaElement>('#job-email')!;
function setSource(text:string){
 const a=area();a.value=text;a.dispatchEvent(new Event('input',{bubbles:true}));
 const src=$<HTMLDetailsElement>('#source-request');if(src)src.open=true;
}
function setHeader(c:Known){
 const a=area();const body=a.value.replace(/^From:.*\n\n?/,'');
 setSource('From: '+c.name+' <'+c.email+'>\n\n'+body);
}
function addRateLine(c:Known,r:Rate){
 const a=area();const hasFrom=/^From:/m.test(a.value);
 const base=hasFrom?a.value:'From: '+c.name+' <'+c.email+'>\n\n'+a.value;
 setSource(base.replace(/\n*$/,'\n')+'- 1 x '+r.desc+' @ $'+r.unit_price+' each\n');
}

let sheet:HTMLElement;
/* ---------- intake enhancer ---------- */
const root=document.createElement('section');root.id='up-intake';root.className='up-intake';root.setAttribute('aria-label','Ways to add the job');
root.innerHTML=`<div class="up-ways">
<label class="up-way" for="up-photo-input"><span class="up-ico" aria-hidden="true">&#128247;</span><b>Snap a note</b><small>Photo of a job note or timesheet</small></label>
<input id="up-photo-input" type="file" accept="image/*" capture="environment" hidden>
<button class="up-way" id="up-mic" type="button" aria-pressed="false"><span class="up-ico" aria-hidden="true">&#127908;</span><b data-label>Say it</b><small>Speak the job out loud</small></button>
</div>
<div class="up-drop" id="up-drop" tabindex="0">Drop a photo here, or <button type="button" class="up-link" id="up-sample-photo">try the sample note</button> &middot; <button type="button" class="up-link" id="up-sample-voice">try a spoken example</button></div>
<p class="up-status" id="up-status" role="status" aria-live="polite"></p>
<div class="up-heard" id="up-heard" hidden></div>
<div class="up-chips-wrap" id="up-clients"></div>`;
let regions:Region[]=[];
const photo=document.createElement('section');photo.id='up-photo';photo.className='up-photo';photo.hidden=true;photo.setAttribute('aria-label','Photo and what was read from it');

function mount(){return true;}
const say=(m:string,kind='')=>{const s=$('#up-status')!;s.textContent=m;s.dataset.kind=kind;};

function renderClientChips(){
 const host=$('#up-clients')!;
 const a=area().value;const m=/^From:.*<([^>]+)>/m.exec(a);
 const sel=m?clients.find(c=>c.email.toLowerCase()===m[1].toLowerCase()):null;
 host.innerHTML='<div class="up-chips-head">Clients you invoice'+(sampleOnly?' <em>sample</em>':'')+'</div><div class="up-chips" role="group" aria-label="Remembered clients">'+
  clients.map(c=>`<button type="button" class="up-chip" data-up-client="${esc(c.email)}" aria-pressed="${sel?.email===c.email}"><span class="up-av" aria-hidden="true">${esc(c.name[0]||'?')}</span>${esc(c.name)}</button>`).join('')+'</div>'+
  (sel&&sel.rates.length?'<div class="up-chips-head">Usual rates for '+esc(sel.name)+'</div><div class="up-chips">'+sel.rates.map((r,i)=>`<button type="button" class="up-chip up-rate" data-up-rate="${i}">${esc(r.desc)} <b>${esc(r.currency==='USD'?'$':r.currency+' ')}${esc(r.unit_price)}</b></button>`).join('')+'</div>':'');
}
root.addEventListener('click',e=>{
 const t=e.target as HTMLElement;
 const cb=t.closest<HTMLElement>('[data-up-client]');
 if(cb){const c=clients.find(x=>x.email===cb.dataset.upClient);if(c){setHeader(c);renderClientChips();say(c.name+' is the bill-to. Email '+c.email+' filled from your last invoice.','ok');}return;}
 const rb=t.closest<HTMLElement>('[data-up-rate]');
 if(rb){const m=/^From:.*<([^>]+)>/m.exec(area().value);const c=clients.find(x=>x.email.toLowerCase()===m?.[1].toLowerCase());if(c)addRateLine(c,c.rates[Number(rb.dataset.upRate)]);return;}
});
area()?.addEventListener('input',()=>{if(root.isConnected)renderClientChips();});

/* ---- photo ---- */
function paintPhoto(url:string,pending:boolean){
 photo.hidden=false;
 photo.innerHTML=`<div class="up-pic"><div class="up-frame"><img id="up-img" alt="Photo of the job note" src="${url}">${regions.map((r,i)=>`<i class="up-box" data-up-region="${i}" style="left:${r.box[0]*100}%;top:${r.box[1]*100}%;width:${(r.box[2]-r.box[0])*100}%;height:${(r.box[3]-r.box[1])*100}%"><span class="up-tag"></span></i>`).join('')}${pending?'<div class="up-scan"><span></span></div>':''}</div></div>
 <div class="up-read"><h3>${pending?'Reading the note…':'What it says'}</h3>${pending?'<p class="up-quiet">The local vision model is reading your handwriting. Nothing leaves this machine.</p>':`<ol class="up-lines">${regions.map((r,i)=>`<li><button type="button" data-up-region-btn="${i}">${esc(r.text)}</button></li>`).join('')}</ol><p class="up-quiet">This is now the source text. Fix any word below, then extract. Proposed invoice lines point back to the words they came from.</p><div class="up-actions"><button class="button button-primary up-go" type="button" id="up-extract">Extract invoice</button><button class="button button-link" type="button" id="up-clear-photo">Remove photo</button></div>`}</div>`;
 relink();
}
function highlight(i:number|null){
 photo.querySelectorAll('.up-box').forEach(b=>b.classList.toggle('on',i!==null&&(b as HTMLElement).dataset.upRegion===String(i)));
 photo.querySelectorAll('[data-up-region-btn]').forEach(b=>b.classList.toggle('on',i!==null&&(b as HTMLElement).dataset.upRegionBtn===String(i)));
 if(i!==null)photo.querySelector('.up-box.on')?.scrollIntoView({block:'nearest',behavior:'smooth'});
}
photo.addEventListener('click',e=>{
 const t=e.target as HTMLElement;
 const b=t.closest<HTMLElement>('[data-up-region-btn]');if(b)highlight(Number(b.dataset.upRegionBtn));
 if(t.closest('#up-extract'))$<HTMLButtonElement>('#analyze')?.click();
 if(t.closest('#up-clear-photo')){regions=[];photo.hidden=true;photo.innerHTML='';relink();}
});
async function ingestPhoto(blob:Blob,canned:boolean){
 const url=canned?sampleNoteDataUrl():URL.createObjectURL(blob);
 regions=[];paintPhoto(url,true);say('Reading the photo…');
 try{
  if(canned){await new Promise(r=>setTimeout(r,700));regions=SAMPLE_REGIONS;}
  else{const ac=new AbortController();const to=setTimeout(()=>ac.abort(),90000);try{regions=await readNote(blob,ac.signal);}finally{clearTimeout(to);}}
  const lines=regions.map(r=>r.text.replace(/^[-*\u2022]\s*/,''));
  const names=clients.find(c=>lines.join(' ').toLowerCase().includes(c.name.toLowerCase().split(/\s+/)[0]));
  const body=lines.filter(l=>/^\d/.test(l)).map(l=>'- '+l);
  const terms=lines.map(l=>/net\s*\d+/i.exec(l)?.[0]).filter(Boolean).map(t=>'Payment terms '+t+'.');
  const text=(names?'From: '+names.name+' <'+names.email+'>\n\n':'')+(body.length?body:lines).join('\n')+'\n'+terms.join('\n');
  setSource(text.replace(/\n+$/,'\n'));
  paintPhoto(url,false);renderClientChips();
  say(names?'Read '+regions.length+' lines. '+names.name+' recognised, so the recipient is already filled.':'Read '+regions.length+' lines. Check the words, then extract.','ok');
 }catch(err){
  regions=[];paintPhoto(url,false);
  photo.querySelector('.up-read')!.innerHTML='<h3>Could not read this photo here</h3><p class="up-quiet">'+esc(String((err as Error).message||err))+'. The local vision model may be off. The picture stays beside the box below: type what it says, or use the sample note.</p>';
  say('Photo kept; type the note below.','warn');area().focus();
 }
}
$('#up-photo-input')!.addEventListener('change',e=>{const f=(e.target as HTMLInputElement).files?.[0];if(f)ingestPhoto(f,false);(e.target as HTMLInputElement).value='';});
$('#up-sample-photo')!.addEventListener('click',()=>ingestPhoto(new Blob(),true));
const drop=$('#up-drop')!;
for(const ev of ['dragenter','dragover'])root.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('over');});
for(const ev of ['dragleave','drop'])root.addEventListener(ev,()=>drop.classList.remove('over'));
root.addEventListener('drop',e=>{e.preventDefault();const f=(e as DragEvent).dataTransfer?.files?.[0];if(f&&f.type.startsWith('image/'))ingestPhoto(f,false);else say('That file is not a picture. Drop a photo of the note.','warn');});

/* ---- voice ---- */
function showHeard(transcript:string){
 const p=parseSpoken(transcript,clients);
 const h=$('#up-heard')!;h.hidden=false;
 h.innerHTML='<div class="up-quote">&ldquo;'+esc(transcript)+'&rdquo;</div>'+(p.lines.length?'<ul class="up-heard-lines">'+p.lines.map(l=>`<li><span>${esc(String(l.qty))} &times; ${esc(l.desc)}</span><b>${l.price!==null?'$'+esc(String(l.price)):'rate?'}</b>${l.filled?'<em>usual rate</em>':''}</li>`).join('')+'</ul>':'<p class="up-quiet">Could not hear any quantities. Say it again, or type below.</p>');
 if(!p.lines.length)return;
 setSource(p.text);renderClientChips();
 say(p.client?p.client.name+' recognised; email '+p.client.email+' filled from your last invoice. Check the lines, then extract.':(p.clientWord?'Client "'+p.clientWord+'" is new: add their email below.':'Check the lines, then extract.'),'ok');
}
$('#up-sample-voice')!.addEventListener('click',()=>{showHeard(SAMPLE_PHRASE);});
let rec:MediaRecorder|null=null,chunks:Blob[]=[];
const mic=$<HTMLButtonElement>('#up-mic')!;
function micLabel(on:boolean){mic.setAttribute('aria-pressed',String(on));mic.classList.toggle('rec',on);mic.querySelector('[data-label]')!.textContent=on?'Tap to stop':'Say it';}
mic.addEventListener('click',async()=>{
 if(rec&&rec.state==='recording'){rec.stop();return;}
 if(!navigator.mediaDevices?.getUserMedia){say('No microphone here. Using the spoken example instead.','warn');showHeard(SAMPLE_PHRASE);return;}
 try{
  const stream=await navigator.mediaDevices.getUserMedia({audio:true});
  chunks=[];rec=new MediaRecorder(stream);
  rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
  rec.onstop=async()=>{
   stream.getTracks().forEach(t=>t.stop());micLabel(false);
   const blob=new Blob(chunks,{type:rec?.mimeType||'audio/webm'});
   say('Listening back…');
   try{
    const r=await fetch(LISTEN,{method:'POST',body:blob});if(!r.ok)throw new Error('HTTP '+r.status);
    const j=await r.json();const text=(j.lines||[]).map((l:any)=>l.text).join(' ').trim();
    if(!text)throw new Error('nothing heard');showHeard(text);
   }catch(err){say('The local speech-to-text did not answer ('+String((err as Error).message)+'). Type the job below, or try the spoken example.','warn');}
  };
  rec.start();micLabel(true);say('Listening… say who to invoice and what for, then tap again.');
 }catch{say('Microphone not available. Using the spoken example instead.','warn');showHeard(SAMPLE_PHRASE);}
});

/* ---- link proposed lines to photo regions ---- */
const tokensOf=(s:string)=>s.toLowerCase().split(/[^a-z0-9.]+/).filter(w=>w.length>0).map(stem);
function bestRegion(line:HTMLElement):number|null{
 if(!regions.length)return null;
 const v=(f:string)=>(line.querySelector<HTMLInputElement>(`[data-field=${f}]`)?.value||'');
 const want=new Set([...tokensOf(v('desc')),...tokensOf(v('qty')),...tokensOf(v('unit_price'))].filter(w=>w!=='x'));
 let best=-1,score=0;
 regions.forEach((r,i)=>{const have=new Set(tokensOf(r.text));let s=0;for(const w of want)if(have.has(w))s+=/\d/.test(w)?1.5:1;if(s>score){score=s;best=i;}});
 return score>=1?best:null;
}
function relink(){
 const lines=Array.from(document.querySelectorAll<HTMLElement>('fieldset[data-review-line]'));
 photo.querySelectorAll('.up-tag').forEach(t=>t.textContent='');
 lines.forEach((line,n)=>{
  let b=line.querySelector<HTMLButtonElement>('.up-locate');
  const idx=bestRegion(line);
  if(idx===null){b?.remove();return;}
  if(!b){b=document.createElement('button');b.type='button';b.className='up-locate';line.append(b);}
  b.dataset.region=String(idx);b.textContent='From the photo: line '+(idx+1);
  const tag=photo.querySelector(`.up-box[data-up-region="${idx}"] .up-tag`);if(tag)tag.textContent=(tag.textContent?tag.textContent+',':'')+(n+1);
 });
}
document.addEventListener('click',e=>{
 const b=(e.target as HTMLElement).closest<HTMLElement>('.up-locate');if(b){highlight(Number(b.dataset.region));photo.scrollIntoView({block:'nearest',behavior:'smooth'});}
});
document.addEventListener('mouseover',e=>{const b=(e.target as HTMLElement).closest<HTMLElement>('.up-locate');if(b)highlight(Number(b.dataset.region));});
document.addEventListener('focusin',e=>{const l=(e.target as HTMLElement).closest<HTMLElement>('fieldset[data-review-line]');const b=l?.querySelector<HTMLElement>('.up-locate');if(b)highlight(Number(b.dataset.region));});

/* ---- recipient chips inside the review form ---- */
function reviewChips(){
 const f=$('.review-client-fields');if(!f)return;
 let box=f.querySelector<HTMLElement>('.up-review-chips');
 if(!box){box=document.createElement('div');box.className='up-review-chips';f.prepend(box);
  box.addEventListener('click',e=>{
   const b=(e.target as HTMLElement).closest<HTMLElement>('[data-up-fill]');if(!b)return;
   const c=clients.find(x=>x.email===b.dataset.upFill);const form=$<HTMLFormElement>('#review-form');if(!c||!form)return;
   (form.elements.namedItem('client_name') as HTMLInputElement).value=c.name;
   const em=form.elements.namedItem('client_email') as HTMLInputElement;em.value=c.email;em.dispatchEvent(new Event('input',{bubbles:true}));
   fillRates(c);
  });}
 const cur=(f.closest('form')?.elements.namedItem('client_email') as HTMLInputElement|null)?.value.toLowerCase()||'';
 const html='<span>Bill to</span>'+clients.map(c=>`<button type="button" class="up-chip" data-up-fill="${esc(c.email)}" aria-pressed="${c.email.toLowerCase()===cur}">${esc(c.name)}</button>`).join('');
 if(box.dataset.h!==html+cur){box.dataset.h=html+cur;box.innerHTML=html;}
}
/** Fill a missing rate from the chosen client's usual rates. */
function fillRates(c:Known){
 for(const line of Array.from(document.querySelectorAll<HTMLElement>('fieldset[data-review-line]'))){
  const price=line.querySelector<HTMLInputElement>('[data-field=unit_price]'),desc=line.querySelector<HTMLInputElement>('[data-field=desc]');
  if(!price||!desc||price.value.trim())continue;
  const words=tokensOf(desc.value);
  const hit=c.rates.find(r=>tokensOf(r.desc).some(w=>words.includes(w)));
  if(hit){price.value=hit.unit_price;price.dispatchEvent(new Event('input',{bubbles:true}));}
 }
}
let tick:number|undefined;
new MutationObserver(()=>{clearTimeout(tick);tick=window.setTimeout(()=>{reviewChips();relink();},60);}).observe($('#analysis')||document.body,{childList:true,subtree:true});
document.addEventListener('input',e=>{if((e.target as HTMLElement).closest?.('#review-form')){clearTimeout(tick);tick=window.setTimeout(()=>{relink();reviewChips();},120);}});

/* ---------- home: invoice cards ---------- */
const home=document.createElement('section');home.id='up-home';home.className='up-home';home.setAttribute('aria-label','Home');
const cardsHost=document.createElement('div');cardsHost.id='up-cards-host';home.append(root,photo,cardsHost);
let ledger:any[]=[],today=new Date().toISOString().slice(0,10),isSample=true;
const money=(v:any,c:string)=>{const n=Number(v);try{return new Intl.NumberFormat('en',{style:'currency',currency:c||'USD'}).format(n);}catch{return c+' '+v;}};
function mark(e:any):{k:string;label:string}{
 const open=['SENT','PARTIALLY_PAID','UNPAID'].includes(e.status)&&Number(e.balance)>0;
 if(e.status==='PAID'||(e.status!=='DRAFT'&&e.status!=='CANCELLED'&&Number(e.balance)===0))return {k:'paid',label:'Paid'};
 if(e.status==='DRAFT')return {k:'draft',label:'Draft'};
 if(e.status==='CANCELLED')return {k:'draft',label:'Cancelled'};
 if(open&&e.due_on&&e.due_on<today)return {k:'late',label:'Overdue'};
 return {k:'sent',label:e.status==='PARTIALLY_PAID'?'Part paid':'Sent'};
}
function renderHome(){
 const rows=ledger.length?ledger:SAMPLE_INVOICES;isSample=!ledger.length;
 const open=rows.filter(e=>['SENT','PARTIALLY_PAID','UNPAID'].includes(e.status)&&Number(e.balance)>0);
 const owed=new Map<string,number>();for(const e of open)owed.set(e.currency,(owed.get(e.currency)||0)+Number(e.balance));
 const order={late:0,sent:1,draft:2,paid:3} as Record<string,number>;
 const sorted=[...rows].sort((a,b)=>order[mark(a).k]-order[mark(b).k]);
 cardsHost.innerHTML=`<header class="up-home-head"><div><p class="up-eyebrow">${isSample?'Sample desk &middot; your invoices appear here':'Your desk'}</p><h2 id="up-home-title">${owed.size?'Waiting on you to get paid':'Nothing waiting on payment'}</h2></div><div class="up-owed">${[...owed].map(([c,n])=>`<strong>${esc(money(n,c))}</strong>`).join('')||'<strong>All settled</strong>'}</div></header>
 <div class="up-cards">${sorted.map(e=>{const m=mark(e);const nudge=m.k==='late'||m.k==='sent';return `<article class="up-card" data-mark="${m.k}" data-up-card="${esc(e.invoice_id)}"><button type="button" class="up-card-main" data-up-open="${esc(e.invoice_id)}"><span class="up-av lg" aria-hidden="true">${esc((e.client_name||e.client_email||'?')[0])}</span><span class="up-card-text"><b>${esc(e.client_name||e.client_email)}</b><small>#${esc(e.invoice_number)}${e.due_on?' &middot; due '+esc(e.due_on):''}</small></span><span class="up-card-amt"><b>${esc(money(m.k==='paid'?e.total:(m.k==='draft'?e.total:e.balance),e.currency))}</b><i class="up-mark ${m.k}">${m.label}</i></span></button>${nudge?`<button type="button" class="up-nudge" data-up-nudge="${esc(e.invoice_id)}">Nudge</button>`:''}</article>`;}).join('')}</div>`;
}
const placeHome=()=>{const w=$('.workflow');if(w&&!home.isConnected)w.before(home);};
home.addEventListener('click',e=>{
 const t=e.target as HTMLElement;
 const n=t.closest<HTMLElement>('[data-up-nudge]');if(n){openNudge((isSample?SAMPLE_INVOICES:ledger).find(x=>x.invoice_id===n.dataset.upNudge));return;}
 const o=t.closest<HTMLElement>('[data-up-open]');
 if(o&&!isSample){
  $<HTMLElement>('.collections-nav [data-desk-view=invoices]')?.click();
  setTimeout(()=>document.querySelector<HTMLElement>(`[data-saved-invoice="${CSS.escape(o.dataset.upOpen!)}"]`)?.click(),30);
 }else if(o){say2('Sample card. Create an invoice to see your real ones here.');}
});
function say2(m:string){const s=$('#up-nudge-note');if(s)s.textContent=m;else {const el=document.createElement('p');el.className='up-quiet up-home-note';el.id='up-nudge-note';el.textContent=m;cardsHost.append(el);setTimeout(()=>el.remove(),3500);}}

/* ---- nudge sheet ---- */
sheet=document.createElement('div');sheet.id='up-sheet';sheet.className='up-sheet';sheet.hidden=true;
sheet.innerHTML='<div class="up-sheet-card" role="dialog" aria-modal="true" aria-labelledby="up-sheet-title"><div class="up-grab"></div><h3 id="up-sheet-title">Reminder draft</h3><p class="up-quiet" id="up-sheet-sub"></p><textarea id="up-sheet-text" rows="9" aria-label="Reminder text"></textarea><div class="up-actions"><button class="button button-primary" type="button" id="up-copy">Copy message</button><button class="button button-link" type="button" id="up-sheet-close">Close</button></div><p class="up-quiet">Draft only. Nothing is sent from here. Sending a real reminder still goes through its own approval.</p></div>';
document.body.append(sheet);
function nudgeText(e:any):string{
 const first=String(e.client_name||'there').split(/\s+/)[0];
 const link='https://www.sandbox.paypal.com/invoice/p/#'+encodeURIComponent(e.invoice_id.replace(/^sample-/,'SAMPLE-INV-'));
 const late=e.due_on&&e.due_on<today;
 const part=Number(e.balance)<Number(e.total);
 return `Hi ${first},\n\n${late?`A friendly nudge: invoice #${e.invoice_number} for ${money(e.balance,e.currency)} was due on ${e.due_on}.`:`Just a quick note that invoice #${e.invoice_number} for ${money(e.balance,e.currency)} is ${e.due_on?'due on '+e.due_on:'ready for you'}.`}${part?' Thank you for the part payment already received.':''}\nYou can view and pay it here: ${link}\n\nIf it has already crossed paths with your payment, please ignore this. Thanks so much!`;
}
function openNudge(e:any){
 if(!e)return;
 $('#up-sheet-sub')!.textContent='To '+(e.client_name||'')+' <'+(e.client_email||'')+'> about #'+e.invoice_number;
 $<HTMLTextAreaElement>('#up-sheet-text')!.value=nudgeText(e);
 sheet.hidden=false;$<HTMLTextAreaElement>('#up-sheet-text')!.focus();
}
sheet.addEventListener('click',async ev=>{
 const t=ev.target as HTMLElement;
 if(t===sheet||t.closest('#up-sheet-close')){sheet.hidden=true;return;}
 if(t.closest('#up-copy')){const v=$<HTMLTextAreaElement>('#up-sheet-text')!.value;try{await navigator.clipboard.writeText(v);$('#up-copy')!.textContent='Copied';}catch{$<HTMLTextAreaElement>('#up-sheet-text')!.select();$('#up-copy')!.textContent='Select and copy';}setTimeout(()=>{const b=$('#up-copy');if(b)b.textContent='Copy message';},1800);}
});
document.addEventListener('keydown',e=>{if(e.key==='Escape')sheet.hidden=true;});

/* ---------- wire up ---------- */
window.addEventListener('ledgerly:state',(ev:any)=>{
 const s=ev.detail||{};ledger=s.ledger||[];today=s.today||today;remember(s);
 placeHome();renderHome();renderClientChips();reviewChips();
});
function boot(){mount();placeHome();renderHome();renderClientChips();}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
