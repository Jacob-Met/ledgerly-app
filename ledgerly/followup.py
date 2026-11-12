"""Revisit a PayPal sandbox invoice after Ledgerly's drafting process has exited.

The provider is authoritative for every invoice fact. Only the locally saved
reminder cooldown / uncertain-send outcome survives across runs. This is an
operator tool, not a scheduled reminder sender or a PayPal production client.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from .agent import Agent, LedgerEntry
from .paypal import HttpPayPalClient, OPEN_STATUSES, PayPalError, SANDBOX_BASE

SCHEMA = "ledgerly.followup.v1"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,128}$")
MAX_STATE_BYTES = 256 * 1024


@contextlib.contextmanager
def _lock_state(path: Path):
    """OS-backed nonblocking lock; OS releases it even if the process exits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    with open(lock, "a+b") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another Ledgerly follow-up is using this state file") from exc
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Another Ledgerly follow-up is using this state file") from exc
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _load_state(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("State path cannot be a symbolic link")
    if not path.exists():
        return {"schema": SCHEMA, "invoices": {}}
    if not path.is_file() or path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError("State file is not a regular supported JSON file")
    raw = path.read_bytes()
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("State file is too large")
    try:
        state = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("State file is not valid JSON") from exc
    if (not isinstance(state, dict) or set(state) != {"schema", "invoices"}
            or state["schema"] != SCHEMA or not isinstance(state["invoices"], dict)):
        raise ValueError("Unsupported Ledgerly follow-up state")
    return state


def _save_state(path: Path, state: dict) -> None:
    body = (json.dumps(state, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if len(body) > MAX_STATE_BYTES:
        raise ValueError("State would exceed the supported size")
    if path.is_symlink():
        raise ValueError("State path cannot be a symbolic link")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", prefix=".ledgerly-", suffix=".tmp",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(stream.name, 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_record(state: dict, invoice_id: str) -> dict:
    record = state["invoices"].get(invoice_id, {})
    if not isinstance(record, dict) or set(record) - {"last_reminder_on", "reminders_sent", "uncertain"}:
        raise ValueError("Unsupported reminder record for this invoice")
    if "last_reminder_on" in record:
        date.fromisoformat(record["last_reminder_on"])
    count = record.get("reminders_sent", 0)
    if type(count) is not int or count < 0:
        raise ValueError("Reminder count must be nonnegative")
    uncertain = record.get("uncertain")
    if uncertain is not None:
        if (not isinstance(uncertain, dict)
                or set(uncertain) != {"attempted_on"}
                or not isinstance(uncertain["attempted_on"], str)):
            raise ValueError("Unsupported uncertain-send record")
        date.fromisoformat(uncertain["attempted_on"])
    return record


def _make_agent(client, invoice_id: str, invoicer_name: str, today: date) -> Agent:
    agent = Agent(client, {"name": invoicer_name, "email_address": "operator@ledgerly.example"},
                  today=lambda: today)
    # Populate a fresh placeholder. Every displayed monetary/recipient fact is
    # replaced only after the full current provider response validates.
    agent.ledger[invoice_id] = LedgerEntry(
        invoice_id, "(unread)", None, "", "USD", Decimal(0), None)
    agent._refresh_invoice(invoice_id)
    return agent



def _sandbox_client() -> HttpPayPalClient:
    """Never allow a live-money endpoint or a silent offline fallback."""
    if os.environ.get("LEDGERLY_ALLOW_NETWORK") != "1":
        raise ValueError("Set LEDGERLY_ALLOW_NETWORK=1 to opt into PayPal Sandbox")
    cid, secret = os.getenv("PAYPAL_CLIENT_ID", ""), os.getenv("PAYPAL_CLIENT_SECRET", "")
    if not cid or not secret:
        raise ValueError("PayPal Sandbox credentials are missing")
    return HttpPayPalClient(cid, secret, SANDBOX_BASE)


def _invoice_page(client, page: int, page_size: int) -> dict:
    """PayPal v2 list API; list summaries are used only to discover IDs."""
    if hasattr(client, "list_invoices"):
        response = client.list_invoices(page=page, page_size=page_size, total_required=True)
    elif isinstance(client, HttpPayPalClient):
        query = urlencode({"page": page, "page_size": page_size, "total_required": "true"})
        response = client._call("GET", "/v2/invoicing/invoices?" + query)
    else:
        raise ValueError("This PayPal client does not support invoice listing")
    body = getattr(response, "body", response)
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise ValueError("PayPal invoice list returned no valid items array")
    return body


def scan_receivables(
    *,
    client=None,
    today: date | None = None,
    max_pages: int = 5,
    page_size: int = 50,
    output_json: bool = False,
    write: Callable[[str], None] = print,
) -> int:
    """Read current invoices across multiple PayPal pages. Never send anything.

    A paginated list is not an atomic snapshot. Every value reported as an
    outstanding balance comes from its own validated GET /invoices/{id}; any
    incomplete or contradictory list is signaled and exits nonzero.
    """
    if type(max_pages) is not int or not 1 <= max_pages <= 50:
        raise ValueError("--max-pages must be between 1 and 50")
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("--page-size must be between 1 and 100")
    client = client if client is not None else _sandbox_client()
    today = today or date.today()
    found: list[str] = []
    seen: set[str] = set()
    errors: list[dict] = []
    pages = 0
    reported_pages: int | None = None
    reported_items: int | None = None
    complete = False

    for page in range(1, max_pages + 1):
        body = _invoice_page(client, page, page_size)
        pages += 1
        items = body["items"]
        if len(items) > page_size:
            errors.append({"page": page, "reason": "PayPal returned more invoices than requested"})
            break
        value = body.get("total_pages")
        if value is not None:
            if type(value) is not int or not 0 <= value <= 1000:
                errors.append({"page": page, "reason": "Invalid total_pages in PayPal list"})
                break
            if reported_pages is not None and value != reported_pages:
                errors.append({"page": page, "reason": "Invoice pages changed during scan"})
                break
            reported_pages = value
        value = body.get("total_items")
        if value is not None:
            if type(value) is not int or value < 0:
                errors.append({"page": page, "reason": "Invalid total_items in PayPal list"})
                break
            if reported_items is not None and value != reported_items:
                errors.append({"page": page, "reason": "Invoice count changed during scan"})
                break
            reported_items = value
        for entry in items:
            inv_id = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(inv_id, str) or not ID_RE.fullmatch(inv_id):
                errors.append({"page": page, "reason": "Invalid invoice ID in PayPal list"})
                break
            if inv_id in seen:
                errors.append({"page": page, "reason": "Invoice appeared on multiple pages"})
                break
            seen.add(inv_id)
            found.append(inv_id)
        if errors:
            break
        if reported_pages is not None:
            if page >= reported_pages:
                complete = True
                break
            if not items:
                errors.append({"page": page, "reason": "PayPal returned an empty intermediate page"})
                break
        elif len(items) < page_size:
            complete = True
            break

    # A stale item count (or a list modified while paging) must not appear
    # complete, even if every requested GET succeeds.
    if reported_items is not None and complete and len(found) != reported_items:
        errors.append({"reason": "Invoice count differs from observed page contents"})
        complete = False

    rows: list[dict] = []
    for inv_id in found:
        try:
            agent = _make_agent(client, inv_id, "Freelancer", today)
            entry = agent.ledger[inv_id]
            rows.append({
                "invoice_id": inv_id, "invoice_number": entry.invoice_number,
                "recipient": entry.client_email, "status": entry.status,
                "currency": entry.currency, "balance": str(entry.balance),
                "due_on": entry.due_on.isoformat() if entry.due_on else None,
                "days_overdue": entry.days_overdue(today),
                "actionable": entry.status in OPEN_STATUSES and entry.balance > 0,
            })
        except (PayPalError, ValueError, TypeError, KeyError):
            errors.append({"invoice_id": inv_id, "reason": "Current provider invoice is unreadable or unsupported"})
    if errors:
        complete = False

    overdue = sorted(
        (row for row in rows if row["actionable"] and row["days_overdue"] > 0),
        key=lambda row: (-row["days_overdue"], row["invoice_id"]),
    )
    open_other = sorted(
        (row for row in rows if row["actionable"] and row["days_overdue"] == 0),
        key=lambda row: (row["due_on"] is None, row["due_on"] or "", row["invoice_id"]),
    )
    closed_count = len(rows) - len(overdue) - len(open_other)
    result = {
        "as_of": today.isoformat(),
        "complete": complete, "pages_fetched": pages,
        "page_size": page_size, "max_pages": max_pages,
        "total_items_reported": reported_items, "invoice_ids_discovered": len(found),
        "invoices_validated": len(rows), "overdue": overdue, "open_other": open_other,
        "closed_count": closed_count, "errors": errors,
    }
    if output_json:
        write(json.dumps(result, sort_keys=True))
    else:
        write(f"PAYPAL SANDBOX RECEIVABLES — {today} — {len(rows)} current invoice(s)")
        for title, section in (("OVERDUE", overdue), ("OPEN / NOT YET OVERDUE", open_other)):
            write(f"\n{title} ({len(section)})")
            for row in section:
                age = f"{row['days_overdue']} days late" if row["days_overdue"] else ("due " + (row["due_on"] or "unspecified"))
                write(f"  {row['invoice_number']} [{row['invoice_id']}]  "
                      f"{row['currency']} {row['balance']}  {age}  {row['recipient']}")
        write(f"\nClosed/paid/draft: {closed_count}")
        write(f"Pages: {pages}; provider list items: {reported_items if reported_items is not None else 'unspecified'}")
        if not complete:
            write("INCOMPLETE SCAN: some invoices or fresh detail reads were unavailable.")
            for error in errors:
                write("  " + str(error))
        else:
            write("Scan completed for the observed page range; pagination is not an atomic snapshot.")
        write("Read-only: no reminders were sent.")
    return 0 if complete else 3

def follow_up(
    invoice_id: str,
    *,
    state_path: Path | None = None,
    remind: bool = False,
    resolve: str | None = None,
    invoicer_name: str = "Freelancer",
    client=None,
    today: date | None = None,
    ask: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> int:
    if not ID_RE.fullmatch(invoice_id):
        raise ValueError("Invoice ID must be a PayPal invoice identifier")
    if (remind or resolve) and state_path is None:
        raise ValueError("--state-file is required for reminder sends and uncertain-outcome resolution")
    if remind and resolve:
        raise ValueError("Choose --remind or --resolve, not both")
    if client is None:
        client = _sandbox_client()
    today = today or date.today()
    if state_path is None:
        agent = _make_agent(client, invoice_id, invoicer_name, today)
        _show_status(agent.ledger[invoice_id], write)
        return 0

    with _lock_state(state_path):
        state = _load_state(state_path)
        record = _read_record(state, invoice_id)
        agent = _make_agent(client, invoice_id, invoicer_name, today)
        entry = agent.ledger[invoice_id]
        if "last_reminder_on" in record:
            entry.last_reminder_on = date.fromisoformat(record["last_reminder_on"])
        entry.reminders_sent = record.get("reminders_sent", 0)
        _show_status(entry, write)
        if record.get("uncertain"):
            attempt = record["uncertain"]["attempted_on"]
            write("WARNING: a previous reminder may have been sent on " + attempt)
            if not resolve:
                write("No repeat send. Check PayPal and use --resolve sent or --resolve not-sent.")
                return 3
            want = "RESOLVE " + invoice_id + (" SENT" if resolve == "sent" else " NOT_SENT")
            if ask("After independent PayPal review, type " + want + ": ").strip() != want:
                write("Resolution not recorded; uncertain send remains.")
                return 3
            if resolve == "sent":
                record["last_reminder_on"] = attempt
                record["reminders_sent"] = record.get("reminders_sent", 0) + 1
            record.pop("uncertain", None)
            state["invoices"][invoice_id] = record
            _save_state(state_path, state)
            write("Uncertain result resolved as " + resolve + "; no new send performed.")
            return 0
        if resolve:
            write("There is no uncertain reminder to resolve.")
            return 2
        if not remind:
            write("Read-only check: no reminder requested.")
            return 0
        outcome = agent.tool_send_reminder(invoice_id)
        if not outcome.get("ok"):
            write("No reminder queued: " + outcome.get("message", "not eligible"))
            return 0
        pending = agent.list_pending()
        if len(pending) != 1:
            raise RuntimeError("Expected exactly one pending reminder")
        action = pending[0]
        write("SUBJECT: " + action["payload"]["subject"])
        write("MESSAGE:\n" + action["payload"]["note"])
        want = "REMIND " + invoice_id
        if ask("Type " + want + " to send this exact reminder: ").strip() != want:
            agent.reject(action["id"], "Operator declined after preview")
            write("Reminder kept unsent.")
            return 0

        # Persist a possible-effect marker before crossing the provider send
        # boundary. Even a process crash cannot silently allow a duplicate.
        record["uncertain"] = {"attempted_on": today.isoformat()}
        state["invoices"][invoice_id] = record
        _save_state(state_path, state)
        try:
            agent.approve(action["id"], approver="explicit CLI operator")
        except Exception:
            write("Reminder outcome UNKNOWN. Check PayPal; resolution required before retry.")
            return 4
        record.pop("uncertain", None)
        record["last_reminder_on"] = today.isoformat()
        record["reminders_sent"] = record.get("reminders_sent", 0) + 1
        _save_state(state_path, state)
        write("PayPal Sandbox accepted the reminder; local cooldown recorded.")
        return 0


def _show_status(entry: LedgerEntry, write: Callable[[str], None]) -> None:
    write("PAYPAL INVOICE: " + entry.invoice_number + " / " + entry.invoice_id)
    write("Recipient: " + entry.client_email + " | status: " + entry.status)
    write(f"Money: {entry.currency} total {entry.total}; paid {entry.paid_amount}; balance {entry.balance}")
    write("Due: " + (entry.due_on.isoformat() if entry.due_on else "(none)"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("invoice_id", nargs="?", help="PayPal Sandbox invoice ID from a prior Ledgerly session")
    p.add_argument("--state-file", type=Path, help="Local JSON cooldown + unknown-outcome register")
    p.add_argument("--remind", action="store_true", help="Review and optionally send an overdue reminder")
    p.add_argument("--scan", action="store_true", help="List account receivables from current PayPal Sandbox invoice details (read-only)")
    p.add_argument("--max-pages", type=int, default=5, help="Maximum PayPal list pages to fetch (1..50, default 5)")
    p.add_argument("--page-size", type=int, default=50, help="Invoice IDs per PayPal list page (1..100, default 50)")
    p.add_argument("--json", action="store_true", help="Emit one structured JSON object for --scan")
    p.add_argument("--resolve", choices=("sent", "not-sent"),
                   help="After checking PayPal, resolve an unknown outgoing reminder without retrying")
    p.add_argument("--invoicer-name", default=os.getenv("LEDGERLY_INVOICER_NAME", "Freelancer"))
    args = p.parse_args(argv)
    if args.scan and (args.invoice_id or args.remind or args.resolve or args.state_file):
        p.error("--scan takes no invoice ID, --remind, --resolve or --state-file")
    if not args.scan and (args.invoice_id is None or args.json):
        p.error("Specify an invoice ID for follow-up, or use --scan for an account-wide read")
    try:
        if args.scan:
            return scan_receivables(max_pages=args.max_pages, page_size=args.page_size,
                                    output_json=args.json)
        return follow_up(args.invoice_id, state_path=args.state_file, remind=args.remind,
                         resolve=args.resolve, invoicer_name=args.invoicer_name)
    except (ValueError, OSError, PayPalError, RuntimeError) as exc:
        print("Ledgerly follow-up: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
