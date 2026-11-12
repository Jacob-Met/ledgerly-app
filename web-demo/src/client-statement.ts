import './client-statement.css';
/** Download the selected client's full account through the active Python session. */
type Row = {invoice_id?: unknown; client_email?: unknown};
type Snapshot = {ledger?: Row[]};
type Reply = {ok?: boolean; message?: string; result?: {html?: unknown; filename?: unknown; client_email?: unknown; record_count?: unknown}};
type Request = (invoiceId: string) => Promise<Reply | null>;
const escape = (value: string) => value.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));

export function createClientStatementDownloads(container: HTMLElement, request: Request) {
  let clients = new Map<string, string>();
  let ready = false, busy = false, downloading = false;
  const buttons = () => Array.from(container.querySelectorAll<HTMLButtonElement>('button[data-client-statement]'));
  const sync = () => {
    for (const button of buttons())
      button.disabled = !ready || busy || downloading || !clients.has(button.dataset.clientStatement || '');
  };
  const announce = (id: string, message: string, failed = false) => {
    const note = Array.from(container.querySelectorAll<HTMLElement>('[data-statement-note]'))
      .find(node => node.dataset.statementNote === id);
    if (note) { note.textContent = message; note.dataset.kind = failed ? 'error' : 'ready'; }
  };
  container.addEventListener('click', async event => {
    const button = (event.target as HTMLElement).closest?.<HTMLButtonElement>('button[data-client-statement]');
    if (!button || !container.contains(button) || button.disabled || !ready || busy || downloading) return;
    const id = button.dataset.clientStatement || '', email = clients.get(id);
    if (!email) return;
    downloading = true; sync();
    announce(id, 'Preparing this client’s saved account…');
    let url: string | undefined;
    let anchor: HTMLAnchorElement | undefined;
    try {
      const reply = await request(id), document = reply?.result;
      if (!reply?.ok) throw new Error(reply?.message || 'The account could not be read. Reopen the saved workspace and try again.');
      if (!document || typeof document.html !== 'string' || typeof document.filename !== 'string' ||
          typeof document.client_email !== 'string' || document.client_email.toLowerCase() !== email.toLowerCase())
        throw new Error('The returned account does not match this client. No file was downloaded.');
      const blob = new Blob([document.html], {type: 'text/html;charset=utf-8'});
      url = URL.createObjectURL(blob);
      anchor = container.ownerDocument.createElement('a');
      anchor.href = url; anchor.download = document.filename;
      container.ownerDocument.body.appendChild(anchor); anchor.click();
      announce(id, 'Statement download started for ' + email + '. Open the HTML file to read or print it.');
    } catch (error) {
      announce(id, 'Could not download the statement. ' + (error instanceof Error ? error.message + ' ' : '') + 'Try again.', true);
    } finally {
      anchor?.remove();
      if (url) { const completedUrl = url; setTimeout(() => URL.revokeObjectURL(completedUrl), 1000); }
      downloading = false; sync();
      buttons().find(node => node.dataset.clientStatement === id)?.focus({preventScroll: true});
    }
  });
  return {
    update(snapshot: Snapshot) {
      clients = new Map((Array.isArray(snapshot?.ledger) ? snapshot.ledger : [])
        .filter(row => typeof row.invoice_id === 'string' && typeof row.client_email === 'string' && row.client_email.trim())
        .map(row => [row.invoice_id as string, row.client_email as string]));
      sync();
    },
    markup(invoiceId: string): string {
      const email = clients.get(invoiceId);
      if (!email) return '';
      const noteId = 'client-statement-' + encodeURIComponent(invoiceId);
      return '<div class="client-statement-action"><button type="button" class="button button-secondary" ' +
        'data-client-statement="' + escape(invoiceId) + '" aria-describedby="' + escape(noteId) + '"' +
        (!ready || busy || downloading ? ' disabled' : '') + '>Download client statement</button>' +
        '<p class="fine-print">All saved invoices for ' + escape(email) + ', with separate currency totals.</p>' +
        '<p class="fine-print" role="status" aria-live="polite" id="' + escape(noteId) +
        '" data-statement-note="' + escape(invoiceId) + '"></p></div>';
    },
    setAvailability(available: boolean, inProgress: boolean) { ready = available; busy = inProgress; sync(); },
  };
}
