import {describe, it, expect} from 'vitest';
import {nextJourneyStep, type JourneyInput} from './journey-guide';
const base: JourneyInput = {
  engineReady: true, busy: false, sourcePresent: true, analyzed: true,
  blocked: false, reviewRequiresCheck: false, hasPending: false,
  hasPayableInvoice: false, hasPaidInvoice: false, hasLedger: false,
};
const stage = (changes: Partial<JourneyInput>) => nextJourneyStep({...base, ...changes});
describe('read-only guided invoice journey', () => {
  it('starts with runtime and distinguishes offline simulation', () => {
    const found = stage({engineReady: false});
    expect(found.id).toBe('engine');
    expect(found.detail).toContain('does not contact PayPal');
  });
  it('invites intake only when source is empty', () => {
    expect(stage({sourcePresent: false, analyzed: false}).id).toBe('intake');
    expect(stage({analyzed: false}).id).toBe('analyze');
  });
  it('never suggests drafting when fields are unverified or blocked', () => {
    expect(stage({blocked: true}).id).toBe('review');
    expect(stage({reviewRequiresCheck: true}).id).toBe('review');
    expect(stage({}).id).toBe('draft');
  });
  it('prioritizes pending human consent above unfinished intake', () => {
    const pending = stage({hasPending: true, sourcePresent: false, analyzed: false,
      hasPayableInvoice: true});
    expect(pending.id).toBe('approval');
    expect(pending.selector).toBe('#approval-list');
    expect(pending.detail).toContain('Approve or reject');
  });
  it('only offers payment after a payable invoice exists', () => {
    expect(stage({hasPayableInvoice: true, hasLedger: true}).id).toBe('payment');
    expect(stage({hasPaidInvoice: true, hasLedger: true}).id).toBe('complete');
    expect(stage({hasLedger: true}).id).toBe('ledger');
  });
  it('sends a live operator to provider-observed Collections rather than a simulated payment control', () => {
    const native = stage({nativeMode: true, hasLedger: true, hasPayableInvoice: true});
    expect(native.id).toBe('collections');
    expect(native.selector).toBe('#receivables-view');
    expect(native.detail).not.toContain('Simulate');
  });
    it('offers navigation selectors, never an auto-action endpoint', () => {
    for (const input of [
      {engineReady: false}, {sourcePresent: false}, {analyzed: false},
      {blocked: true}, {reviewRequiresCheck: true}, {hasPending: true},
      {hasPayableInvoice: true, hasLedger: true},
      {hasPaidInvoice: true, hasLedger: true}, {hasLedger: true}, {},
    ]) {
      const found = stage(input);
      expect(found.selector.startsWith('#')).toBe(true);
      expect(found).not.toHaveProperty('execute');
    }
  });
});
