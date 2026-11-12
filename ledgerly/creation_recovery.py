"""Retain native invoice creation intent across a missing provider response.

This journal supplements the existing operator checkpoint; it never retries a
creation. Recovery reads one supplied provider ID, matches the original complete
invoice content, and queues the original unsent invoice for a new human decision.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
import tempfile

from .agent import LedgerEntry
from .recovery import SnapshotStore


class CreationJournal:
    def __init__(self, path: Path, invoicer_email: str):
        self.path = Path(path)
        self.email = invoicer_email
        self.records = {}
        if SnapshotStore(self.path)._ensure_private():
            data = json.loads(self.path.read_text())
            if data.get("version") != 1 or data.get("invoicer_email") != self.email or not isinstance(data.get("attempts"), dict):
                raise ValueError("Creation recovery does not match this operator.")
            self.records = data["attempts"]
            for key, row in self.records.items():
                if not isinstance(row, dict) or row.get("id") != key or row.get("status") not in ("UNKNOWN", "CONFIRMED", "TRACKED"):
                    raise ValueError("Creation recovery contains an unreadable attempt.")

    def save(self):
        SnapshotStore(self.path)._ensure_private()
        payload = json.dumps({"version": 1, "invoicer_email": self.email, "attempts": self.records},
                             ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > 10_000_000:
            raise ValueError("Creation history exceeds the supported private file size.")
        fd, temporary = tempfile.mkstemp(prefix=".ledgerly-creation-", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if hasattr(os, "O_DIRECTORY"):
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def begin(self, invoice, context):
        key = secrets.token_hex(16)
        self.records[key] = {"id": key, "status": "UNKNOWN", "invoice_id": None,
                             "invoice": deepcopy(invoice), "context": deepcopy(context)}
        self.save()  # Must succeed before the provider's create POST.
        return key

    def receipt(self, key, response):
        body = getattr(response, "body", None)
        if not 200 <= getattr(response, "status", 0) < 300 or not isinstance(body, dict):
            return
        identity = body.get("id")
        if "href" in body:
            href = body["href"]
            if not isinstance(href, str) or "/v2/invoicing/invoices/" not in href:
                return
            linked = href.rsplit("/", 1)[-1]
            if identity is not None and identity != linked:
                return
            identity = linked
        if not isinstance(identity, str) or not identity or any(c.isspace() or c in "/?#" for c in identity):
            return
        row = self.records[key]
        row["invoice_id"] = identity
        row["status"] = "CONFIRMED"
        self.save()  # A restart before the core checkpoint still knows this ID.

    def track(self, agent, trusted_ids=None):
        """Only trust completed core queue writes, or a freshly restored agent."""
        changed = False
        trusted = set(agent.ledger if trusted_ids is None else trusted_ids)
        for row in self.records.values():
            identity = row.get("invoice_id")
            if row["status"] == "TRACKED":
                continue
            if identity is None:
                # A core checkpoint can finish just before a journal write is
                # interrupted. Match only one exact saved draft; never allocate
                # a new invoice or treat a merely similar amount as identity.
                candidates = {a.invoice_id for a in agent.pending.values()
                              if a.invoice_id in trusted and a.kind == "send_invoice"
                              and a.payload.get("invoice") == row["invoice"]}
                if len(candidates) == 1:
                    identity = next(iter(candidates))
                    row["invoice_id"] = identity
            if identity not in trusted:
                continue
            match = next((a for a in agent.pending.values()
                          if a.invoice_id == identity and a.kind == "send_invoice"
                          and a.payload.get("invoice") == row["invoice"]), None)
            if match is not None:
                row["status"] = "TRACKED"
                changed = True
        if changed:
            self.save()

    def source_for(self, invoice_id):
        """Return only the exact source of one durably linked creation."""
        matches = [row for row in self.records.values()
                   if row.get("status") == "TRACKED" and row.get("invoice_id") == invoice_id]
        if len(matches) != 1:
            return None
        context = matches[0].get("context")
        source = context.get("source_text") if isinstance(context, dict) else None
        return source if isinstance(source, str) and source.strip() else None

    def pending(self):
        return [{key: row[key] for key in ("id", "status", "invoice_id")}
                | {"invoice_number": row["invoice"]["detail"]["invoice_number"],
                   "currency": row["invoice"]["detail"]["currency_code"]}
                for row in self.records.values() if row["status"] != "TRACKED"]

    def reconcile(self, agent, attempt_id, invoice_id):
        row = self.records.get(attempt_id)
        if row is None or row["status"] == "TRACKED":
            raise ValueError("This creation is not awaiting recovery.")
        if (not isinstance(invoice_id, str) or not invoice_id
                or any(c.isspace() or c in "/?#" for c in invoice_id)):
            raise ValueError("Copy the exact Sandbox invoice ID from PayPal.")
        if row.get("invoice_id") and row["invoice_id"] != invoice_id:
            raise ValueError("This creation already has a different confirmed invoice ID.")
        # No create/send/payment is called in this recovery operation.
        response = agent.client.get_invoice(invoice_id)
        record = response.body
        original = row["invoice"]
        if (not 200 <= response.status < 300 or not isinstance(record, dict)
                or record.get("id") != invoice_id or record.get("status") != "DRAFT"):
            raise ValueError("Recovery requires the exact existing unsent Sandbox draft.")
        if agent._send_review_content(original) != agent._send_review_content(record, provider=True):
            raise ValueError("The provider draft does not match the original recipient, invoice number, line items and terms.")
        if invoice_id in agent.ledger:
            # A prior queue may exist only in memory after its disk write failed.
            # The explicit recovery must make that core checkpoint durable first.
            agent.state_store.save(agent)
            self.track(agent)
            if row["status"] == "TRACKED":
                return {"message": "The original draft is already in the saved ledger."}
            raise ValueError("That invoice is already tracked by another local review.")
        context = row["context"]
        detail = original["detail"]
        due_date = detail.get("payment_term", {}).get("due_date")
        total = sum((Decimal(item["quantity"]) * Decimal(item["unit_amount"]["value"])
                     for item in original["items"]), Decimal(0))
        entry = LedgerEntry(invoice_id, detail["invoice_number"], context["client_name"],
                            context["client_email"], detail["currency_code"], total,
                            context["due_days"], prepaid=Decimal(context["prepaid"]),
                            invoice_due_on=date.fromisoformat(due_date) if due_date else None)
        agent.ledger[invoice_id] = entry
        summary = f"Send recovered invoice {entry.invoice_number} for {entry.currency} {entry.total} to {entry.client_email}"
        if entry.prepaid:
            summary += f" and record customer-reported {entry.currency} {entry.prepaid} deposit"
        # DurableAgent saves the ledger and pending action together. Do not mark
        # the creation resolved until that checkpoint has succeeded.
        action = agent._queue("send_invoice", invoice_id, summary,
                              {"invoice": deepcopy(original), "prepaid": str(entry.prepaid)})
        row["invoice_id"] = invoice_id
        row["status"] = "TRACKED"
        self.save()
        return {"message": "Original draft recovered. It is still unsent and needs a separate invoice-specific approval.",
                "invoice_id": invoice_id, "approval_id": action.id}
