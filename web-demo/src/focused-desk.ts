type DeskView = 'compose' | 'decisions' | 'invoices' | 'collections';
const titles: Record<DeskView,string> = {compose:'New invoice',decisions:'Decisions',invoices:'Invoices',collections:'Collections'};
let preferredView:DeskView='compose';
export function preferredDeskView(){return preferredView;}
export function showDeskView(view: DeskView, remember=true) {
 if(remember){preferredView=view;try{sessionStorage.setItem('ledgerly.desk-view',view);}catch{}}
 document.body.dataset.deskView=view;
 const title=document.getElementById('desk-title'); if(title)title.textContent=titles[view];
 document.querySelectorAll<HTMLElement>('.collections-nav [data-desk-view]').forEach(link=>{
  if(link.dataset.deskView===view)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
 });
}
export function revealDeskSection(element:HTMLElement) {
 if(element.closest('.approval-panel,#native-decisions'))showDeskView('decisions');
 else if(element.closest('.ledger-panel'))showDeskView('invoices');
 else if(element.closest('#receivables-view'))showDeskView('collections');
 else if(element.closest('.workflow'))showDeskView('compose');
}
export function installFocusedDesk() {
 let saved:string|null=null;try{saved=sessionStorage.getItem('ledgerly.desk-view');}catch{}
 const initial=saved&&Object.prototype.hasOwnProperty.call(titles,saved)?saved as DeskView:'compose';
 showDeskView(initial);
 const route=()=>{const target=document.getElementById(location.hash.slice(1));if(target)revealDeskSection(target);};
 if(!saved)route();
 window.addEventListener('hashchange',route);
 const files=document.querySelector('.workspace-files');
 if(files){const menu=document.createElement('details');menu.className='workspace-menu';const summary=document.createElement('summary');summary.textContent='Workspace';menu.append(summary,files);document.querySelector('.masthead')?.append(menu);}
 const compact=matchMedia('(max-width:760px)');
 const syncSource=()=>{
  const source=document.querySelector<HTMLDetailsElement>('#source-request');
  if(source && (!compact.matches || document.getElementById('invoice-document')))source.open=!compact.matches;
 };
 compact.addEventListener('change',syncSource);syncSource();
 const feedback=document.getElementById('status-message');
 document.querySelector('.desk-toolbar')?.after(feedback!);
 document.addEventListener('click',event=>{
  const link=(event.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="#"]');
  if(!link)return;
  const target=document.getElementById(link.hash.slice(1));if(!target)return;
  revealDeskSection(target);
  if(target.id==='job-email') {
   const source=document.querySelector<HTMLDetailsElement>('#source-request');if(source)source.open=true;
  }
 });
 const intakeActions=document.querySelector<HTMLElement>('.intake-files>.intake-file-actions');
 if(intakeActions){
  const menu=document.createElement('details');menu.className='source-file-menu';
  const summary=document.createElement('summary');summary.textContent='Email & files';
  intakeActions.before(menu);menu.append(summary,intakeActions);
  intakeActions.addEventListener('click',event=>{
   if((event.target as HTMLElement).closest('button'))menu.open=false;
  });
 }
 document.addEventListener('click',event=>{
  const link=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-source-start]');
  if(!link)return;
  const source=document.querySelector<HTMLTextAreaElement>('#job-email');
  const start=Number(link.dataset.sourceStart),end=Number(link.dataset.sourceEnd);
  if(!source||!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<=start||end>source.value.length||source.value.slice(start,end)!==link.dataset.sourceText)return;
  const request=document.querySelector<HTMLDetailsElement>('#source-request');if(request)request.open=true;
  showDeskView('compose',false);
  source.focus({preventScroll:true});source.setSelectionRange(start,end);
  // Measure the real wrapping so a quote in a long email remains visible.
  const style=getComputedStyle(source),mirror=document.createElement('div'),marker=document.createElement('span');
  Object.assign(mirror.style,{position:'absolute',left:'-10000px',top:'0',visibility:'hidden',boxSizing:'border-box',width:source.clientWidth+'px',font:style.font,lineHeight:style.lineHeight,letterSpacing:style.letterSpacing,padding:style.padding,whiteSpace:'pre-wrap',overflowWrap:'break-word'});
  mirror.append(document.createTextNode(source.value.slice(0,start)));marker.textContent=source.value.slice(start,end);mirror.append(marker);document.body.append(mirror);
  source.scrollTop=Math.max(0,marker.offsetTop-source.clientHeight/3);mirror.remove();
  source.scrollIntoView({block:'center',behavior:'instant'});
 });

}
export function showInvoiceDocument() {
 showDeskView('compose',false);
 const source=document.querySelector<HTMLDetailsElement>('#source-request');
 if(source)source.open=!matchMedia('(max-width:760px)').matches;
}
