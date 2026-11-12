/**
 * Optional native browser model. No API key or cloud inference is used here.
 * Chrome may download its model after the visitor expressly selects this path.
 */
export type NativeSession = {
  prompt: (text: string, options: {responseConstraint: object; signal?: AbortSignal}) => Promise<string>;
  destroy?: () => void;
};
type DownloadMonitor = {addEventListener: (type: 'downloadprogress', listener: (event: {loaded: number}) => void) => void};
type SessionOptions = {signal?: AbortSignal; monitor?: (monitor: DownloadMonitor) => void};
export type NativeProvider = {create: (options?: SessionOptions) => Promise<NativeSession>};
export type SessionRequest = {signal?: AbortSignal; onDownloadProgress?: (fraction: number) => void};

function cancelled(): DOMException {
  return new DOMException('On-device AI analysis cancelled.', 'AbortError');
}

/** Settle our request on cancellation even if an older provider ignores its signal. */
function abortable<T>(pending: Promise<T>, signal?: AbortSignal, lateValue?: (value: T) => void): Promise<T> {
  if (!signal) return pending;
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const abort = () => {
      if (settled) return;
      settled = true;
      reject(cancelled());
    };
    signal.addEventListener('abort', abort, {once: true});
    pending.then(value => {
      signal.removeEventListener('abort', abort);
      if (settled) {
        try { lateValue?.(value); } catch { /* A late local session has no invoice authority. */ }
        return;
      }
      settled = true;
      resolve(value);
    }, error => {
      signal.removeEventListener('abort', abort);
      if (settled) return;
      settled = true;
      reject(error);
    });
    if (signal.aborted) abort();
  });
}

export const invoiceSchema = {
  type: 'object',
  properties: {
    client_name: {type: ['string', 'null']},
    client_email: {type: ['string', 'null']},
    currency: {type: ['string', 'null']},
    line_items: {type: 'array', items: {type: 'object', properties: {
      desc: {type: 'string'}, qty: {type: ['number', 'null']},
      unit_price: {type: 'number'}, currency: {type: ['string', 'null']},
    }, required: ['desc', 'qty', 'unit_price', 'currency']}},
    due_days: {type: ['integer', 'null']},
    amount_paid: {type: 'number'},
    confidence: {type: 'number'},
    issues: {type: 'array', items: {type: 'object', properties: {
      field: {type: 'string'}, severity: {type: 'string', enum: ['error', 'warning', 'info']},
      message: {type: 'string'},
    }, required: ['field', 'severity', 'message']}},
  },
  required: ['client_name', 'client_email', 'currency', 'line_items', 'due_days', 'amount_paid', 'confidence', 'issues'],
};

/** Call directly within a click handler before its user activation is lost. */
export function startOnDeviceSession(
  root: {LanguageModel?: NativeProvider} = globalThis as {LanguageModel?: NativeProvider},
  options: SessionRequest = {},
): Promise<NativeSession> {
  if (options.signal?.aborted) return Promise.reject(cancelled());
  if (!root.LanguageModel || typeof root.LanguageModel.create !== 'function')
    return Promise.reject(new Error('This browser does not expose the on-device Prompt API. Use Python rules or desktop Chrome 148+ with the built-in model enabled.'));
  try {
    const pending = root.LanguageModel.create({
      signal: options.signal,
      monitor(monitor) {
        monitor.addEventListener('downloadprogress', event => {
          if (!options.signal?.aborted && Number.isFinite(event.loaded) && event.loaded >= 0 && event.loaded <= 1)
            options.onDownloadProgress?.(event.loaded);
        });
      },
    });
    return abortable(pending, options.signal, session => session.destroy?.());
  } catch (error) { return Promise.reject(error); }
}

export function completeInvoicePrompt(session: NativeSession, prompt: string, signal?: AbortSignal): Promise<string> {
  if (signal?.aborted) return Promise.reject(cancelled());
  try { return abortable(session.prompt(prompt, {responseConstraint: invoiceSchema, signal}), signal); }
  catch (error) { return Promise.reject(error); }
}
