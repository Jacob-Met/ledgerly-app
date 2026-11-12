"""Read-only client account statements from Ledgerly's durable Sandbox ledger."""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from html import escape
from pathlib import Path

from .agent import Agent, LedgerEntry
from .extract import EMAIL_RE, PAYPAL_CURRENCIES, ZERO_DECIMAL_CURRENCIES
from .paypal import SandboxMock
from .recovery import SnapshotStore

_STYLE = """
:root{color-scheme:light;--ink:#1e2329;--muted:#687281;--line:#cfd6df;--accent:#1e2329;--paper:#fff}
*{box-sizing:border-box}body{margin:0;background:#eef0f3;color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1080px;margin:32px auto 56px;padding:38px 48px 48px;background:var(--paper);box-shadow:0 2px 12px #1e232910}
header{display:flex;align-items:center;justify-content:space-between;gap:20px;padding-bottom:24px;border-bottom:1px solid var(--line)}
.brand{font:750 23px/1.2 system-ui,sans-serif;letter-spacing:-.04em;text-transform:uppercase}.header-actions{display:flex;align-items:center;gap:20px}.stamp{font-size:11px;font-weight:600;color:var(--muted);border:0;background:#f4f6f8;padding:3px 8px;border-radius:0}
button{background:var(--accent);color:white;border:0;border-radius:2px;padding:10px 16px;font:600 13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;cursor:pointer}button:focus-visible{outline:3px solid #ffe94a;outline-offset:3px}
.account-heading{display:flex;justify-content:space-between;align-items:start;gap:32px;margin:30px 0 24px}h1{font:650 32px/1.2 system-ui,sans-serif;letter-spacing:-.025em;margin:0 0 12px}.account-date{color:var(--muted);font-size:13px}.merchant{text-align:right;font-size:13px;padding-top:5px}.merchant b{display:block;font-size:14px}
.client{padding-bottom:24px;border-bottom:1px solid var(--line)}.eyebrow{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);font-weight:650;margin-bottom:5px}.client-name{font-size:20px;font-weight:600}.identity{overflow-wrap:anywhere}.muted{color:var(--muted)}p{margin:0}
.totals{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:30px;margin:26px 0 34px}.currency{border-bottom:1px solid var(--line);padding:0 0 15px}.currency>span{font-size:13px;font-weight:550}.currency>strong{display:block;font:650 34px/1.35 ui-monospace,monospace;font-variant-numeric:tabular-nums}.currency dl{display:grid;grid-template-columns:1fr auto;gap:4px 14px;font-size:12px;color:var(--muted);margin:10px 0 0}dt,dd{margin:0}dd{text-align:right;font-variant-numeric:tabular-nums}
.currency-group{margin-top:32px}.group-heading{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:10px}h2{font-size:18px;letter-spacing:-.02em;font-weight:650;margin:0}.group-count{font-size:12px;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;font-size:11px;font-weight:600;color:var(--muted);padding:10px 8px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:#f4f6f8}td{padding:15px 8px;border-bottom:1px solid var(--line);vertical-align:top}th:first-child,td:first-child{padding-left:0}th:last-child,td:last-child{padding-right:0}th.money,td.money{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}td.money{font-family:ui-monospace,monospace}.invoice-number{font-weight:650}.balance{font-weight:700}.state{font-size:12px;font-weight:550}.treatment{display:block;font-size:11px;color:var(--muted)}.date{white-space:nowrap}.late{display:block;color:#b83432;font-size:11px;margin-top:2px}.excluded td{color:var(--muted)}.mobile-label{display:none}
.payments-heading{margin-top:26px;font-size:14px}.payment-history{margin-top:16px}.payment-history h3{font-size:12px;font-weight:600;margin:0 0 4px;color:var(--muted)}.payment-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:16px;padding:11px 0;border-bottom:1px solid var(--line);font-size:13px}.payment-row b{font-weight:500}.reference{display:block;font-size:11px;color:var(--muted);overflow-wrap:anywhere}.payment-source{color:var(--muted)}.payment-amount{font-family:ui-monospace,monospace;font-variant-numeric:tabular-nums;white-space:nowrap;font-weight:600}
.payment-summary{display:flex;flex-wrap:wrap;gap:8px 24px;padding:10px 0;font-size:12px;color:var(--muted)}.payment-summary>div{display:flex;gap:8px}.payment-summary dd{font-weight:600;color:var(--ink)}.payment-unavailable{font-size:12px;color:var(--muted);padding:7px 0 10px}.print-context,.print-currency{display:none}
@media screen and (max-width:700px){body{background:white}main{margin:0;padding:22px 20px 32px;box-shadow:none}header{padding-bottom:18px}.brand{font-size:24px}.header-actions{gap:12px}button{padding:9px 12px;font-size:12px}.account-heading{display:block;margin:25px 0 20px}h1{font-size:30px;margin-bottom:8px}.merchant{text-align:left;margin-top:18px;padding:0}.merchant b{display:block;margin-right:0}.client{padding-bottom:20px}.client-name{font-size:19px}.totals{grid-template-columns:1fr;gap:22px;margin:22px 0 28px}.currency>strong{font-size:34px}.currency-group{margin-top:28px}.group-heading{margin-bottom:0}h2{font-size:17px}
table,tbody{display:block}thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}tbody tr{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1fr);grid-template-areas:"invoice invoice balance" "status status balance" "sent due due" "total paid paid";gap:8px 14px;padding:18px 0;border-bottom:1px solid var(--line)}td{display:block;padding:0!important;border:0;min-width:0}.invoice-cell{grid-area:invoice;overflow-wrap:anywhere;font-size:15px}.sent-cell{grid-area:sent}.due-cell{grid-area:due}.total-cell{grid-area:total}.paid-cell{grid-area:paid}.balance-cell{grid-area:balance;align-self:start}.status-cell{grid-area:status}.money.total-cell,.money.paid-cell{text-align:left}.balance-cell{font-size:19px;font-weight:650}.mobile-label{display:block;color:var(--muted);font-size:10px;font-weight:500;line-height:1.5;margin-bottom:2px}.state,.treatment{display:inline;font-size:12px}.treatment{margin-left:8px}.date{white-space:normal}.late{display:inline;margin-left:5px}.payment-row{grid-template-columns:minmax(0,1fr) auto;gap:4px 12px}.payment-source{grid-column:1;grid-row:2}.payment-amount{grid-column:2;grid-row:1/3}.payments-heading{margin-top:22px}
}
@media screen{
.payment-table,.payment-table thead,.payment-table tbody,.payment-table thead tr,.payment-table th{display:block}
.payment-table{font-size:inherit}.payment-table thead{position:static;width:auto;height:auto;overflow:visible;clip-path:none}
.payment-table th,.payment-table td{padding:0!important;border:0;background:none}
.payment-table .payment-row{grid-template-areas:none;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;padding:11px 0;border-bottom:1px solid var(--line);gap:16px}
.payment-table .payment-summary-row{display:block;padding:0;border:0}
.payment-table .payment-summary-row td{display:block}
}
@media screen and (max-width:700px){.payment-table .payment-row{grid-template-columns:minmax(0,1fr) auto;gap:4px 12px}}
@page{size:landscape;margin:15mm}
@media print{
body{background:white;font-size:10pt;line-height:1.3}
main{max-width:none;margin:0;padding:0;box-shadow:none}
.print-currency{display:inline}
header{padding-bottom:7px}.brand{font-size:22px}.toolbar{display:none}
.account-heading{margin:12px 0 10px}h1{font-size:24px;margin-bottom:6px}
.client{padding-bottom:10px}.client-name{font-size:17px}.client .eyebrow{margin-bottom:3px}
.totals{margin:12px 0 16px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:24px}
.currency{padding-bottom:9px}.currency>strong{font-size:23px}.currency dl{font-size:9pt;margin-top:6px;gap:2px 14px}
.currency-group{margin-top:16px}.group-heading{margin-bottom:6px}h2{font-size:14px}
table{font-size:9pt}th{font-size:8pt;padding:6px}td{padding:7px 6px}
.print-context{display:table-row}.print-context th{font-size:8pt;font-weight:400;border:0;background:white;padding:0 0 5px}
.state,.treatment{font-size:8pt}.late{font-size:8pt}
.payment-row{font-size:9pt;padding:6px 0}.reference{font-size:8pt}
.payments-heading{font-size:10pt;margin-top:16px}.payment-history{margin-top:10px;break-inside:auto}.payment-empty{break-inside:avoid-page}
.payment-table thead{display:table-header-group}.payment-table th{background:none;border:0;padding:0 0 3px}
.payment-table .payment-row{display:table-row}.payment-table td{padding:6px 0}
.payment-table .payment-source{width:45%}.payment-table .payment-amount{width:1%;text-align:right}
.payment-table .payment-summary-row td{padding:0;border:0}
.payment-history h3{font-size:9pt;margin-bottom:3px}.payment-summary{font-size:9pt;padding:6px 0;margin:0}
.payment-unavailable{font-size:9pt;padding:4px 0 6px}
.group-heading{break-after:avoid}.group-heading+table{break-before:avoid}
tr,.currency,.payment-row{break-inside:avoid}thead{display:table-header-group;break-after:avoid}h2,h3{break-after:avoid}
}
"""

def _money(value: Decimal, currency: str) -> str:
    places = 0 if currency in ZERO_DECIMAL_CURRENCIES else 2
    unit = Decimal(1).scaleb(-places)
    if not value.is_finite() or value < 0 or value != value.quantize(unit):
        raise ValueError("Saved money does not match its currency precision")
    return format(value, f",.{places}f")


def _unresolved(pending: dict, invoice_id: str) -> list[str]:
    return sorted({
        {"send_invoice": "invoice send", "send_reminder": "reminder send",
         "cancel_invoice": "invoice cancellation"}.get(action.kind, action.kind.replace("_", " "))
        for action in pending.values()
        if action.invoice_id == invoice_id
        and (action.status == "INTERRUPTED"
             or isinstance(action.result, dict) and action.result.get("outcome") == "UNKNOWN")
    })


def _category(entry: LedgerEntry, unresolved: list[str]) -> tuple[str, str]:
    if "invoice send" in unresolved:
        return "review", "Send unconfirmed"
    if "invoice cancellation" in unresolved and entry.status != "CANCELLED":
        return "review", "Cancellation unconfirmed"
    if entry.status in {"SENT", "UNPAID", "PARTIALLY_PAID"}:
        return "open", "Outstanding"
    if entry.status in {"PAID", "MARKED_AS_PAID"} and entry.balance == 0:
        return "settled", "Paid"
    if entry.status in {"DRAFT", "SCHEDULED"}:
        return "excluded", "Not sent"
    if entry.status == "CANCELLED":
        return "excluded", "Cancelled"
    if entry.status == "PAYMENT_PENDING":
        return "review", "Payment pending"
    if "REFUND" in entry.status:
        return "review", "Refund"
    return "review", "Review"


def _payment_history(agent: Agent, entry: LedgerEntry) -> str:
    """Explain saved payment observations without changing the ledger balance."""
    saved = getattr(agent, "invoice_records", {}).get(entry.invoice_id)
    observed = saved if isinstance(saved, dict) else {}
    record = observed.get("record")
    record = record if isinstance(record, dict) and record.get("id") == entry.invoice_id else {}
    payments = record.get("payments")
    transactions = payments.get("transactions") if isinstance(payments, dict) else None
    transactions = transactions if isinstance(transactions, list) else []
    if not transactions and not entry.paid_amount:
        return ""
    esc = lambda value: escape(str(value), quote=True)
    title = (f'<h3>{esc(entry.invoice_number)}'
             f'<span class="print-currency"> · {entry.currency}</span></h3>')
    if not transactions:
        return ('<section class="payment-history payment-empty">' + title +
                '<p class="payment-unavailable">Payment details unavailable</p></section>')
    rows = []
    total = Decimal(0)
    unavailable = 0
    for transaction in transactions:
        amount = transaction.get("amount") if isinstance(transaction, dict) else None
        try:
            if not isinstance(amount, dict) or amount.get("currency_code") != entry.currency:
                raise ValueError("payment currency unavailable")
            value = Decimal(str(amount["value"]))
            formatted = _money(value, entry.currency)
        except (KeyError, ValueError, ArithmeticError):
            unavailable += 1
            continue
        total += value
        kind = transaction.get("type")
        source = {"PAYPAL": "PayPal",
                  "EXTERNAL": "External"}.get(kind, "Method unavailable")
        method = transaction.get("method")
        if isinstance(method, str) and method and method != kind:
            source += " · " + method.replace("_", " ").capitalize()
        identity = transaction.get("payment_id") or "Reference unavailable"
        paid_on = transaction.get("payment_date") or "Date unavailable"
        rows.append(
            '<tr class="payment-row"><td><b>' + esc(paid_on) + '</b>'
            '<span class="reference">' + esc(identity) + '</span></td>'
            '<td class="payment-source">' + esc(source) + '</td>'
            '<td class="payment-amount">' + entry.currency + ' ' + formatted + '</td></tr>')
    summary = ""
    if total != entry.paid_amount or unavailable:
        summary = ('<dl class="payment-summary">'
                   f'<div><dt>Paid</dt><dd>{entry.currency} {_money(entry.paid_amount, entry.currency)}</dd></div>'
                   f'<div><dt>Listed payments</dt><dd>{entry.currency} {_money(total, entry.currency)}</dd></div>')
        if unavailable:
            summary += f'<div><dt>Unavailable entries</dt><dd>{unavailable}</dd></div>'
        summary += '</dl>'
    if summary:
        rows.append('<tr class="payment-summary-row"><td colspan="3">' + summary + '</td></tr>')
    return ('<section class="payment-history"><table class="payment-table"><thead><tr><th colspan="3">' + title +
            '</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></section>')


def render_statement(agent: Agent, client_email: str, *,
                     created_at: datetime | None = None,
                     as_of: date | None = None, source: str = "saved-sandbox") -> str:
    """Render one client's saved records, without reading a provider or changing Agent."""
    origins = {"saved-sandbox": "Sandbox", "browser-simulation": "Simulation",
               "synthetic-provider": "Test data"}
    if source not in origins:
        raise ValueError("Unknown account statement source")
    stamp = origins[source]
    client_email = client_email.strip()
    if EMAIL_RE.fullmatch(client_email) is None:
        raise ValueError("Choose one complete client email address")
    entries = [entry for entry in agent.ledger.values()
               if entry.client_email.casefold() == client_email.casefold()]
    if not entries:
        raise ValueError("No saved invoices match that client email")
    created_at = created_at or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        raise ValueError("Statement generation time must include a timezone")
    created_at = created_at.astimezone(timezone.utc)
    today = as_of or created_at.date()
    currencies = sorted({entry.currency for entry in entries})
    if any(currency not in PAYPAL_CURRENCIES for currency in currencies):
        raise ValueError("Saved ledger contains an unsupported currency")
    # Validate displayed money before generating any deliverable.
    for entry in entries:
        for value in (entry.total, entry.paid_amount, entry.balance):
            _money(value, entry.currency)
    escape_text = lambda value: escape(str(value), quote=True)
    names = sorted({entry.client_name for entry in entries if entry.client_name})
    name = " / ".join(names) or client_email
    # Page-margin content repeats even when a page contains only payment rows.
    # Hex-escape every character so account text remains literal CSS content.
    page_label = '"' + "".join(f"\\{ord(char):x} " for char in name + " · " + client_email) + '"'
    page_style = (
        '@page{@top-left{content:' + page_label + ';font:8pt sans-serif;color:#687281;'
        'width:78%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
        '@top-right{content:"Account date ' + today.isoformat() + '";font:8pt sans-serif;color:#687281;}'
        '@bottom-right{content:counter(page) " / " counter(pages);font:8pt sans-serif;color:#687281;}}'
    )
    groups = {}
    for entry in entries:
        unresolved = _unresolved(agent.pending, entry.invoice_id)
        category, explanation = _category(entry, unresolved)
        groups[entry.invoice_id] = (category, explanation, unresolved)
    totals = []
    tables = []
    for currency in currencies:
        currency_entries = sorted((entry for entry in entries if entry.currency == currency),
                                  key=lambda entry: (entry.sent_on or today, entry.invoice_number, entry.invoice_id))
        open_entries = [entry for entry in currency_entries if groups[entry.invoice_id][0] == "open"]
        outstanding = sum((entry.balance for entry in open_entries), Decimal(0))
        overdue = sum((entry.balance for entry in open_entries
                       if entry.due_on is not None and entry.due_on < today), Decimal(0))
        undated = sum((entry.balance for entry in open_entries if entry.due_on is None), Decimal(0))
        review_count = sum(groups[entry.invoice_id][0] == "review" for entry in currency_entries)
        excluded_count = sum(groups[entry.invoice_id][0] == "excluded" for entry in currency_entries)
        summary_rows = []
        for label, value in (("Overdue", overdue), ("Undated", undated)):
            if value:
                summary_rows.append(f"<dt>{label}</dt><dd>{_money(value, currency)}</dd>")
        for label, count in (("Review · excluded", review_count), ("Unsent / cancelled", excluded_count)):
            if count:
                summary_rows.append(f"<dt>{label}</dt><dd>{count}</dd>")
        totals.append(
            f'<section class="currency" data-currency="{currency}"><span>{currency} outstanding</span>'
            f'<strong data-outstanding="{currency}">{_money(outstanding, currency)}</strong>'
            '<dl>' + "".join(summary_rows) + '</dl></section>')
        rows = []
        for entry in currency_entries:
            category, explanation, unresolved = groups[entry.invoice_id]
            due = entry.due_on.isoformat() if entry.due_on else "—"
            late = ""
            if category == "open" and entry.balance > 0 and entry.due_on and entry.due_on < today:
                days_overdue = (today - entry.due_on).days
                late = f'<span class="late">{days_overdue} {"day" if days_overdue == 1 else "days"} overdue</span>'
            status = entry.status.replace("_", " ").capitalize()
            if category in {"review", "excluded"}:
                status = explanation
            treatment = '<span class="treatment">Excluded</span>' if category in {"review", "excluded"} else ""
            if "reminder send" in unresolved:
                treatment += '<span class="treatment">Reminder unconfirmed</span>'
            rows.append(
                f'<tr data-invoice="{escape_text(entry.invoice_id)}" class="{category}">'
                f'<td class="invoice-cell"><b class="invoice-number">{escape_text(entry.invoice_number)}</b></td>'
                f'<td class="sent-cell"><span class="mobile-label">Sent</span><span class="date">{escape_text(entry.sent_on.isoformat() if entry.sent_on else "—")}</span></td>'
                f'<td class="due-cell"><span class="mobile-label">Due</span><span class="date">{escape_text(due)}</span> {late}</td>'
                f'<td class="money total-cell"><span class="mobile-label">Total</span>{_money(entry.total, currency)}</td>'
                f'<td class="money paid-cell"><span class="mobile-label">Paid</span>{_money(entry.paid_amount, currency)}</td>'
                f'<td class="money balance-cell"><span class="mobile-label">Balance</span><span class="balance">{_money(entry.balance, currency)}</span></td>'
                f'<td class="status-cell"><b class="state">{escape_text(status)}</b> {treatment}</td></tr>')
        histories = "".join(_payment_history(agent, entry) for entry in currency_entries)
        tables.append(
            f'<section class="currency-group" aria-labelledby="heading-{currency}"><div class="group-heading"><h2 id="heading-{currency}">{currency} invoices</h2>'
            f'<span class="group-count">{len(currency_entries)} invoice{"" if len(currency_entries) == 1 else "s"}</span></div>'
            '<table><thead>'
            f'<tr class="print-context"><th colspan="7">{escape_text(name)} · {escape_text(client_email)}'
            f' · Account date {today.isoformat()} · {currency}</th></tr>'
            '<tr><th scope="col">Invoice</th><th scope="col">Sent</th><th scope="col">Due</th>'
            '<th scope="col" class="money">Total</th><th scope="col" class="money">Paid</th>'
            '<th scope="col" class="money">Balance</th><th scope="col">Status</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
            + ('<h2 class="payments-heading">Payments</h2>' + histories if histories else "") + '</section>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        'style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        f'<title>Account statement — {escape_text(name)} — Ledgerly</title><style>{_STYLE}{page_style}</style>'
        '</head><body><main><header><div class="brand">Ledgerly</div>'
        f'<div class="header-actions"><span class="stamp">{stamp}</span>'
        '<div class="toolbar"><button type="button" onclick="window.print()">Print / PDF</button></div></div></header>'
        '<div class="account-heading"><div><h1>Account statement</h1>'
        f'<p class="account-date">As of {today.strftime("%d %B %Y")}</p></div>'
        f'<div class="merchant identity"><b>{escape_text(agent.invoicer.get("name", ""))}</b>'
        f'{escape_text(agent.invoicer["email_address"])}</div></div>'
        f'<div class="client identity"><span class="eyebrow">Account</span><div class="client-name">{escape_text(name)}</div>'
        f'<p class="muted">{escape_text(client_email)}</p></div>'
        '<div class="totals">' + "".join(totals) + '</div>' + "".join(tables) +
        '</main></body></html>\n'
    )


def export_statement(state_file: Path, client_email: str, output: Path,
                     invoicer: dict[str, str]) -> Path:
    """Read an existing private checkpoint and exclusively create a private HTML copy."""
    state_file, output = Path(state_file), Path(output)
    if not state_file.exists():
        raise ValueError("The saved ledger does not exist; no statement was created")
    if output.suffix.lower() != ".html":
        raise ValueError("Choose an .html output filename")
    # SnapshotStore validates the original envelope, merchant, every money field,
    # and uncertain outcomes. A mock is only an inert required Agent dependency;
    # every document fact comes from the saved records, never from mock responses.
    agent = Agent(SandboxMock(), invoicer)
    SnapshotStore(state_file).load_into(agent)
    document = render_statement(agent, client_email)
    payload = document.encode("utf-8")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output
