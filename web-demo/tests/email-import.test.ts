import {beforeAll, expect, it} from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import {loadPyodide} from 'pyodide';
import {encodeEmailBytes} from '../src/intake-file';

let py: any;
async function call(action: string, payload: Record<string, unknown> = {}) {
  py.globals.set('request_json', JSON.stringify({action, ...payload}));
  try {
    return JSON.parse(String(await py.runPythonAsync('bridge.handle_json(request_json)')));
  } finally { py.globals.delete('request_json'); }
}
beforeAll(async () => {
  py = await loadPyodide();
  py.FS.mkdirTree('/demo/ledgerly');
  for (const name of ['__init__.py', 'agent.py', 'extract.py', 'paypal.py', 'email_input.py'])
    py.FS.writeFile('/demo/ledgerly/' + name, fs.readFileSync(path.join('public/python/ledgerly', name), 'utf8'));
  for (const name of ['bridge.py', 'review.py', 'invoice_details.py'])
    py.FS.writeFile('/demo/' + name, fs.readFileSync(path.join('public/python', name), 'utf8'));
  await py.runPythonAsync("import sys; sys.path.insert(0, '/demo'); import bridge");
});

const multipart = [
  'From: Morgan Vale <morgan@example.test>',
  'To: Studio <studio@example.test>',
  'Subject: Approved illustration scope',
  'MIME-Version: 1.0',
  'Content-Type: multipart/mixed; boundary="job"',
  '',
  '--job',
  'Content-Type: text/plain; charset="utf-8"',
  '',
  'Please invoice 3 hours of illustration at USD 85 per hour.',
  'Payment is due net 14 days.',
  '',
  'On Monday, a former client wrote:',
  'Old campaign USD 9900. Not this job.',
  '--job',
  'Content-Type: text/plain',
  'Content-Disposition: attachment; filename="old-prices.txt"',
  '',
  'Attached prices USD 8800.',
  '--job--',
  '',
].join('\r\n');

it('previews the actual MIME body without adding financial actions or modifying current analysis', async () => {
  await call('reset');
  const current = fs.readFileSync('public/fixtures/01_simple_usd_hourly.txt', 'utf8');
  const analyzed = await call('analyze', {text: current});
  const ex = analyzed.result;
  const fields = {
    client_name: ex.client_name, client_email: ex.client_email,
    due_days: String(ex.due_days), amount_paid: String(ex.amount_paid),
    line_items: ex.line_items.map((row: any) => ({
      desc: row.desc, qty: String(row.qty), unit_price: String(row.unit_price),
      currency: row.currency || ex.currency, unit: row.unit || '',
    })),
  };
  const checked = await call('review', {text: current, fields, confirmed: true});
  expect(checked.result.valid).toBe(true);
  const imported = await call('import_email', {email_base64: Buffer.from(multipart).toString('base64')});
  expect(imported.ok).toBe(true);
  expect(imported.result.source_text).toContain('3 hours of illustration at USD 85');
  expect(imported.result.source_text).not.toContain('9900');
  expect(imported.result.source_text).not.toContain('8800');
  expect(imported.state).toEqual(checked.state);
  const rejected = await call('import_email', {email_base64: Buffer.from(multipart.replace('From: Morgan Vale <morgan@example.test>', 'From: One <one@example.test>, Two <two@example.test>')).toString('base64')});
  expect(rejected.ok).toBe(false);
  expect(rejected.state).toEqual(checked.state);
  // Previewing even an invalid replacement must preserve the current checked intake.
  const originalDraft = await call('draft', {text: current, review_id: checked.result.review_id});
  expect(originalDraft.ok).toBe(true);
  expect(originalDraft.state.pending).toHaveLength(1);
  expect(originalDraft.state.ledger[0].status).toBe('DRAFT');
});

it('preserves exported legacy character encodings and excludes executable HTML', async () => {
  await call('reset');
  const header = Buffer.from('From: Cafe <cafe@example.test>\r\nMIME-Version: 1.0\r\nContent-Type: text/html; charset=iso-8859-1\r\nContent-Transfer-Encoding: 8bit\r\n\r\n', 'ascii');
  const body = Buffer.from('<p>Caf\u00e9 illustration: 2 hours at USD 75 per hour.</p><script>steal()</script><img src="https://never.example.test/track"><blockquote>Older USD 9000</blockquote>', 'latin1');
  const raw = Buffer.concat([header, body]);
  const transferred = encodeEmailBytes(Uint8Array.from(raw).buffer);
  expect(Buffer.from(transferred, 'base64')).toEqual(raw);
  const imported = await call('import_email', {email_base64: transferred});
  expect(imported.ok).toBe(true);
  expect(imported.result.source_text).toContain('Café illustration');
  expect(imported.result.source_text).not.toMatch(/steal|9000|https:/);
  expect(imported.state.ledger).toEqual([]);
  expect(imported.state.pending).toEqual([]);
  expect(imported.state.mock_requests).toBe(0);
});

it('applies file limits before transport while preserving every byte across chunks', () => {
  expect(() => encodeEmailBytes(new ArrayBuffer(0))).toThrow('empty');
  expect(() => encodeEmailBytes(new ArrayBuffer(2097153))).toThrow('2 MiB');
  const bytes = Uint8Array.from({length: 75000}, (_, index) => index % 256);
  expect(Buffer.from(encodeEmailBytes(bytes.buffer), 'base64')).toEqual(Buffer.from(bytes));
});
