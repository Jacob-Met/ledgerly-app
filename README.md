# Ledgerly

From a client's email to an invoice you have actually checked.

Ledgerly is a local invoicing desk for freelancers and small studios. Clients rarely send purchase orders; they send an email that says "please invoice us" with the quantities and prices somewhere in a paragraph. Ledgerly turns that message into proposed invoice fields, shows where each one came from, checks the arithmetic with fixed rules, and only then lets you create and send a PayPal invoice.

## Try the simulated demo

No PayPal account, keys or AI model needed. Needs Node.js 22 or newer.

```sh
cd web-demo && npm ci && npm run build && npm run preview
```

Open <http://localhost:4173>. The first load fetches the Python engine into your browser (a few seconds).

1. Under **Client request**, open **Try a fictional example**, pick "USD / hourly" and choose **Load example**.
2. Choose **Extract invoice**. Proposed invoice fields appear for review (bill-to, two lines, USD 1,320.00 due).
3. Choose **Create draft**. The draft appears under Approvals; open it and choose **Send invoice**.
4. Choose **View this invoice**, then **Simulate sandbox payment**. The invoice turns PAID with USD 0.00 due.
5. Open **Sandbox tools** to see the count of simulated PayPal-style calls (and zero external calls).

Everything here is simulated: the Python rules engine runs inside the page (Pyodide) and PayPal is the repository's in-memory Sandbox mock, so no network request leaves your browser after the page loads. This path uses the rules extractor; the optional **Use on-device AI** button needs desktop Chrome's built-in model and is not part of the walkthrough. The real PayPal Sandbox setup with a local model is described below.

## How it uses AI

For free-form requests, a local language model proposes the bill-to name, line items, quantities, rates and payment terms. Its output is schema-validated and grounded against the client's text before you see it. Every proposed field is marked for review and links to the exact words it came from. If the model cannot find something, such as the recipient's address, Ledgerly asks instead of guessing.

The model cannot create, send or remind. After you confirm the fields, a deterministic Python rules engine recomputes the totals.

Structured requests are handled by rules alone, with no model. It was built and tested with `qwen2.5:7b` served by Ollama; a llama.cpp server also works.

## How it uses PayPal

The native operator talks to the PayPal REST API in **Sandbox**:

- OAuth 2.0 client credentials (`/v1/oauth2/token`), kept on the server only
- Invoicing v2: `POST /v2/invoicing/generate-next-invoice-number`, `POST /v2/invoicing/invoices` to create a draft, `POST /v2/invoicing/invoices/{id}/send`, `GET /v2/invoicing/invoices/{id}` for payment state, and `POST /v2/invoicing/invoices/{id}/remind`
- Creating a draft requires typing `CREATE`. Sending requires a separate confirmation naming that invoice. Reminders have their own confirmation
- Creation and sending are journaled before the request. An uncertain response is recorded as unknown and recovered by lookup, never by a blind retry

Network access needs an explicit opt-in, and the adapter is fixed to the Sandbox.

## Run it

Requires Python 3.12 or newer and Node.js 22 or newer (to build the interface once).

```sh
cd web-demo
npm ci
npm run build
cd ..
```

Explore the offline simulation in the browser (no accounts, no network; the same Python rules run in the page):

```sh
cd web-demo && npm run preview
```

Run the native operator with PayPal Sandbox and a local model:

```sh
ollama pull qwen2.5:7b

export PAYPAL_CLIENT_ID="your sandbox client id"
export PAYPAL_CLIENT_SECRET="your sandbox client secret"
export LEDGERLY_INVOICER_NAME="Your Sandbox business name"
export LEDGERLY_INVOICER_EMAIL="your-sandbox-business@example.com"
export LEDGERLY_ALLOW_NETWORK=1

mkdir -p "$HOME/.ledgerly"
python3 -m ledgerly.invoice_desk \
  --web-root web-demo/dist \
  --state-file "$HOME/.ledgerly/invoices.json" \
  --port 8787 --no-browser \
  --ollama --model qwen2.5:7b --ollama-url http://127.0.0.1:11434
# open http://127.0.0.1:8787
```

Create Sandbox credentials at <https://developer.paypal.com/dashboard/applications/sandbox> and enable **Invoicing** for the application; without it PayPal refuses invoice creation. Keep the state file outside the served web folder.

## A two-minute walkthrough

1. Paste a client's email into **Original email or job details**.
2. Choose **Use local AI**. Proposed lines appear, each marked "needs review".
3. Use **Show source** on any line to see the client's words. Open the correction form to fix anything, including a missing recipient.
4. Tick the review box and choose **Check invoice fields**. The amount due is computed by the rules engine.
5. Type `CREATE` to create the Sandbox draft, then review and send it with its own confirmation.
6. Follow payment state under **Invoices** and **Collections**.

`fixtures/` contains example requests.

## Tests

```sh
pip install pytest pytest-xdist pypdf
python -m pytest tests

cd web-demo && npm test
```

## License

MIT. See [LICENSE](LICENSE). Third-party notices for the web interface are in `web-demo/THIRD_PARTY_NOTICES.md`.

Source: <https://github.com/Jacob-Met/ledgerly-app>

## Light and dark mode

The interface follows the system colour scheme, and the round button in the bottom-right corner switches between light and dark. The second colour sheet is generated from the first with `tools/retheme.py`, so the two always share the same layout.
