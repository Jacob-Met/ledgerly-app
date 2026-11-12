import {revealDeskSection, showDeskView} from './focused-desk';
/** A read-only guide to the actual in-memory demo. No actions are executed. */
export type JourneyInput = {
  engineReady: boolean; busy: boolean; sourcePresent: boolean; analyzed: boolean;
  blocked: boolean; reviewRequiresCheck: boolean; hasPending: boolean;
  hasPayableInvoice: boolean; hasPaidInvoice: boolean; hasLedger: boolean;
  nativeMode?: boolean;
};
export type JourneyStep = {
  id: string; number: number; heading: string; detail: string;
  action: string; selector: string;
};
const step = (id: string, number: number, heading: string, detail: string,
  action: string, selector: string): JourneyStep =>
  ({id, number, heading, detail, action, selector});
export function nextJourneyStep(input: JourneyInput): JourneyStep {
  if (!input.engineReady) return step('engine', 1, 'Start your private sandbox',
    'Load the real Python invoice engine. This does not contact PayPal or a model server; on-device AI is optional and separate.',
    'Find the engine button', '#engine-start');
  if (input.hasPending) return step('approval', 4, 'The next decision is yours',
    'Read the queued sandbox invoice or reminder. Approve or reject it yourself; nothing sends automatically.',
    'Inspect pending decisions', '#approval-list');
  if (input.hasPayableInvoice) return input.nativeMode
    ? step('collections', 5, 'Follow this invoice', 'Check its provider-observed balance in Collections before deciding what is owed.',
      'Open collections', '#receivables-view')
    : step('payment', 5, 'Follow an invoice to payment',
      'Simulate a PayPal-style payment in this offline desk, or scan for overdue work.',
      'Find payment simulation', '#ledger-list .button-payment');
  if (input.hasPaidInvoice) return step('complete', 5, 'A payment is recorded',
    'Inspect the real agent event trail, then load another fictional job to explore a different outcome.',
    'Inspect the decision trail', '#audit-list');
  if (input.hasLedger) return step('ledger', 5, 'Inspect your local invoice ledger',
    'The latest sandbox invoice has no payment action. Check its status and the event trail.',
    'Inspect invoice status', '#ledger-list');
  if (!input.sourcePresent) return step('intake', 2, 'Bring in a fictional job',
    'Load an example email, or paste non-sensitive job text. This demonstration does not send it anywhere.',
    'Choose a fictional example', '#load-fixture');
  if (!input.analyzed) return step('analyze', 2, 'Extract the job details',
    'Run the actual Python rules, then inspect the proposed recipient, amounts, currency and warnings.',
    'Find the analyze button', '#analyze');
  if (input.blocked || input.reviewRequiresCheck) return step('review', 3,
    'Resolve uncertainty before drafting',
    'Open the correction form, confirm each field and run the Python check. Uncertain details cannot bypass review.',
    'Find the correction form', '#review-editor summary');
  return step('draft', 3, 'Review, then prepare a draft',
    'Inspect the extracted amounts and recipient. Creating a draft is not permission to send it.',
    'Find the draft button', '#draft');
}
export function createJourneyGuide(root: HTMLElement) {
  const count = root.querySelector<HTMLElement>('[data-journey-count]')!;
  const heading = root.querySelector<HTMLElement>('[data-journey-heading]')!;
  const detail = root.querySelector<HTMLElement>('[data-journey-detail]')!;
  const action = root.querySelector<HTMLButtonElement>('[data-journey-action]')!;
  let destination = '#engine-start';
  let previous = '';
  action.addEventListener('click', () => {
    const element = document.querySelector<HTMLElement>(destination);
    if (!element) return;
    // Focus the real workspace section before opening details or scrolling to it.
    // An invoice action hidden inside another tab cannot be used as a next step.
    if (destination === '#engine-start') showDeskView('compose');
    else if (destination === '#audit-list') showDeskView('invoices');
    else revealDeskSection(element);
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') (parent as HTMLDetailsElement).open = true;
    }
    if (!element.matches('a,button,input,textarea,select,summary,[tabindex]'))
      element.tabIndex = -1;
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    element.scrollIntoView({behavior: reduceMotion ? 'auto' : 'smooth', block: 'center'});
    element.focus({preventScroll: true});
  });
  return {
    refresh(input: JourneyInput) {
      const next = nextJourneyStep(input);
      if (previous !== next.id) {
        count.textContent = 'UP NEXT';
        heading.textContent = next.heading;
        detail.textContent = next.detail;
        action.textContent = next.action;
        root.dataset.phase = next.id;
        previous = next.id;
      }
      action.disabled = input.busy;
      destination = next.selector;
    },
  };
}
