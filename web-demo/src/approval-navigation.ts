import {revealDeskSection} from './focused-desk';
/** Navigation only: never approves, rejects, recreates or replays a decision. */
export function revealDeskTarget(element: HTMLElement, focusTarget: HTMLElement = element) {
  revealDeskSection(element);
  const notice = document.querySelector<HTMLElement>('.workspace-notice');
  element.style.scrollMarginTop = `${Math.ceil(notice?.getBoundingClientRect().height || 0) + 8}px`;
  for(let parent=focusTarget.parentElement;parent;parent=parent.parentElement){
    if(parent.tagName==='DETAILS')(parent as HTMLDetailsElement).open=true;
  }
  focusTarget.focus({preventScroll: true});
  element.scrollIntoView({block: 'start', behavior: 'instant'});
}
export function createApprovalNavigation(list: HTMLElement, openInvoice: (id: string) => void) {
  const outcome = document.createElement('section');
  outcome.className = 'approval-outcome';
  outcome.id = 'approval-outcome';
  outcome.hidden = true;
  outcome.tabIndex = -1;
  outcome.setAttribute('role', 'status');
  list.before(outcome);

  const queue = document.createElement('nav');
  queue.className='decision-queue';queue.setAttribute('aria-label','Pending decisions');
  list.before(queue);
  let selectedId:string|undefined;
  try{selectedId=sessionStorage.getItem('ledgerly.selected-decision')||undefined;}catch{}
  const cards=()=>Array.from(list.querySelectorAll<HTMLElement>('[data-approval-id]'));
  const select=(id:string)=>{
    selectedId=id;
    try{sessionStorage.setItem('ledgerly.selected-decision',id);}catch{}
    for(const card of cards())card.hidden=card.dataset.approvalId!==id;
    for(const button of Array.from(queue.querySelectorAll<HTMLButtonElement>('button')))
      button.setAttribute('aria-pressed',String(button.dataset.decisionId===id));
  };
  const refresh=()=>{
    const current=cards();
    if(!current.some(card=>card.dataset.approvalId===selectedId))selectedId=current[0]?.dataset.approvalId;
    queue.replaceChildren();queue.hidden=current.length<2;
    for(const card of current){
      const button=document.createElement('button');button.type='button';button.dataset.decisionId=card.dataset.approvalId;
      const kind=document.createElement('span');kind.textContent=card.querySelector('.source-chip')?.textContent||'Decision';
      const summary=document.createElement('strong');summary.textContent=card.querySelector('.approval-summary')?.textContent||card.dataset.approvalId||'';
      button.append(kind,summary);button.addEventListener('click',()=>{select(card.dataset.approvalId!);revealDeskTarget(card);});
      queue.append(button);
    }
    if(selectedId)select(selectedId);else try{sessionStorage.removeItem('ledgerly.selected-decision');}catch{}
  };
  const reveal = revealDeskTarget;
  const clearOutcome = () => { outcome.hidden = true; outcome.replaceChildren(); };
  list.addEventListener('click', event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-review-invoice]');
    if (button?.dataset.reviewInvoice) openInvoice(button.dataset.reviewInvoice);
  });
  return {
    clearOutcome,
    refresh,
    open(actionId: string, message = 'This decision is waiting for your review.') {
      const card = Array.from(list.querySelectorAll<HTMLElement>('[data-approval-id]'))
        .find(element => element.dataset.approvalId === actionId);
      if (!card) return false;
      select(actionId);
      clearOutcome();
      for (const previous of Array.from(list.querySelectorAll<HTMLElement>('[data-current-review]')))
        previous.removeAttribute('data-current-review');
      for (const previous of Array.from(list.querySelectorAll('[data-review-arrival]'))) previous.remove();
      card.dataset.currentReview = 'true';
      card.tabIndex = -1;
      const arrival = document.createElement('p');
      arrival.className = 'review-arrival';
      arrival.dataset.reviewArrival = 'true';
      arrival.textContent = message;
      card.prepend(arrival);
      const details = card.querySelector<HTMLDetailsElement>('details.approval-preview');
      if (details) details.open = true;
      reveal(card);
      return true;
    },
    outcome(message: string, invoiceId: string | undefined, invoiceLabel = '', recorded = true, record?: {approved:boolean; preview:HTMLElement|null; total:string}) {
      outcome.dataset.state = recorded ? 'recorded' : 'error';
      outcome.replaceChildren();
      const stamped=recorded && record !== undefined;
      const approved=record?.approved,preview=record?.preview;
      if(stamped){
        const header=document.createElement('header');header.className='decision-document-heading';
        const title=document.createElement('h2');title.textContent=invoiceLabel||'Decision recorded';
        const stamp=document.createElement('span');stamp.className='status-chip '+(approved?'approved':'rejected');stamp.textContent=approved?'Approved':'Rejected';
        if(record?.total){const amount=document.createElement('strong');amount.className='decision-document-total';amount.textContent=record.total;title.append(amount);}
        header.append(title,stamp);outcome.append(header);
      }
      const text = document.createElement('p');
      text.textContent = !stamped && invoiceLabel ? invoiceLabel + ' — ' + message : message;
      outcome.append(text);
      if(recorded && preview){preview.classList.add('decision-document');outcome.append(preview);}
      const actions=document.createElement('div');actions.className='decision-document-actions';outcome.append(actions);
      if (invoiceId) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'button button-secondary';
        button.textContent = 'View this invoice';
        button.dataset.outcomeInvoice = invoiceId;
        button.addEventListener('click', () => openInvoice(invoiceId));
        actions.append(button);
      }
      const collections = document.createElement('a');
      collections.className = 'approval-return';
      collections.href = '#receivables-view';
      collections.textContent = 'Back to collections';
      actions.append(collections);
      outcome.hidden = false;
      reveal(outcome);
    },
  };
}
