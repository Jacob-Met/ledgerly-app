"""Explicit correction of retained unsent invoice items."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from datetime import date
import json
import hashlib
from .extract import quantize, EMAIL_RE
from .paypal import _split_name, payment_term, TERM_TYPES, retained_unit_label

def _eligible(agent, invoice_id, current_action=None):
    entry = agent.ledger[invoice_id]
    if entry.status != "DRAFT" or entry.paid_amount or entry.prepaid:
        raise ValueError("Only an unpaid, unsent draft can be corrected.")
    for action in agent.pending.values():
        if action.invoice_id != invoice_id or action.id == current_action:
            continue
        if action.status in {"FAILED", "INTERRUPTED"} or (action.status == "APPROVED" and action.kind != "update_invoice") or (
            action.status == "PENDING" and action.kind != "send_invoice"
        ):
            raise ValueError("An attempted or pending action must be resolved before correcting this draft.")
    return entry

def inspect(agent, invoice_id):
    entry = _eligible(agent, invoice_id)
    original = agent.client.get_invoice(invoice_id).body
    if original.get("id") != invoice_id or original.get("status") != "DRAFT":
        raise ValueError("This provider invoice is no longer an unsent draft.")
    original_content = agent._send_review_content(original, provider=True)
    previous = [a for a in agent.pending.values() if a.invoice_id == invoice_id and a.kind == "send_invoice"]
    if not previous or agent._send_review_content(previous[-1].payload["invoice"]) != original_content:
        raise ValueError("The provider draft changed outside this review. Reconcile it before correcting.")
    proposed = json.loads(original_content)
    recipients = proposed.get("primary_recipients", [])
    if len(recipients) != 1:
        raise ValueError("Correcting a draft requires exactly one billing recipient.")
    billing = recipients[0]["billing_info"]
    name = billing.get("name") or {}
    return {"client_name": " ".join(part for part in (name.get("given_name"), name.get("surname")) if part),
            "client_email": billing["email_address"], "invoice_id": invoice_id, "invoice_number": entry.invoice_number,
            "currency": entry.currency, "total": str(entry.total),
            "payment_term": proposed["detail"].get("payment_term", {}),
            "note": proposed["detail"].get("note", ""),
            "revision": hashlib.sha256(original_content.encode()).hexdigest(),
            "items": [{"name": row["name"], "quantity": row["quantity"],
                       "unit_price": row["unit_amount"]["value"],
                       "unit": row.get("unit_of_measure")} for row in proposed["items"]]}

def prepare(agent, invoice_id, items, revision, recipient=None, terms=None, note=None):
    viewed = inspect(agent, invoice_id)
    if not isinstance(revision, str) or revision != viewed["revision"]:
        raise ValueError("The displayed draft changed. Reopen its correction before continuing.")
    entry = agent.ledger[invoice_id]
    previous = [a for a in agent.pending.values() if a.invoice_id == invoice_id and a.kind == "send_invoice"]
    original_content = agent._send_review_content(previous[-1].payload["invoice"])
    proposed = json.loads(original_content)
    if recipient is not None:
        if not isinstance(recipient, dict) or set(recipient) != {"name", "email"}:
            raise ValueError("Provide the billing name and one email address.")
        name, email = recipient["name"], recipient["email"]
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Provide a billing name of at most 200 characters.")
        if not isinstance(email, str) or len(email) > 254 or not EMAIL_RE.fullmatch(email.strip()):
            raise ValueError("Provide one valid billing email address.")
        billing = proposed["primary_recipients"][0]["billing_info"]
        billing["name"] = _split_name(name.strip())
        billing["email_address"] = email.strip()
    if terms is not None:
        if not isinstance(terms, dict) or set(terms) != {"kind", "value"}:
            raise ValueError("Choose the agreed payment terms.")
        kind, value = terms["kind"], terms["value"]
        invoice_date = date.fromisoformat(proposed["detail"]["invoice_date"])
        if kind == "date":
            if not isinstance(value, str):
                raise ValueError("Provide an exact due date.")
            try:
                due = date.fromisoformat(value)
            except ValueError:
                raise ValueError("Provide an exact due date.") from None
            if due.isoformat() != value or due < invoice_date:
                raise ValueError("The due date must be on or after the invoice date.")
            term = payment_term(None, invoice_date, value)
        elif kind == "net":
            if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 365:
                raise ValueError("Payment days must be a whole number from 1 to 365.")
            term = payment_term(int(value), invoice_date)
        elif kind in {"receipt", "none"} and value == "":
            term = payment_term(0 if kind == "receipt" else None, invoice_date)
        else:
            raise ValueError("Choose the agreed payment terms.")
        proposed["detail"]["payment_term"] = term
    if note is not None:
        # PayPal invoice detail.note is optional and allows 1–4000 characters.
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError("Keep the invoice note to at most 4000 characters.")
        if note.strip():
            proposed["detail"]["note"] = note
        else:
            proposed["detail"].pop("note", None)
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("Keep between one and 100 invoice items.")
    original_items = proposed["items"]
    replacement = []
    used_indices = set()
    for position, edited in enumerate(items):
        required = {"name", "quantity", "unit_price"}
        if (not isinstance(edited, dict) or not required <= set(edited)
                or set(edited) - required - {"unit", "source_index"}):
            raise ValueError("Each correction needs its description, quantity and unit price.")
        # Older clients send positional edits. New rows explicitly use null;
        # retained rows carry their original position even after removals.
        index = edited.get("source_index", position)
        if index is None:
            old = {"unit_amount": {"currency_code": entry.currency},
                   "unit_of_measure": "QUANTITY"}
        else:
            if type(index) is not int or not 0 <= index < len(original_items) or index in used_indices:
                raise ValueError("Reopen the draft correction; an original item reference is invalid.")
            used_indices.add(index)
            old = deepcopy(original_items[index])
        if "unit" in edited:
            unit = edited["unit"]
            if unit != old.get("unit_of_measure") and unit not in ("HOURS", "QUANTITY"):
                raise ValueError("Choose Hours or Quantity for the corrected unit.")
            if unit is not None and not isinstance(unit, str):
                raise ValueError("The corrected unit must be text.")
            if unit is not None:
                if unit != old.get("unit_of_measure") and retained_unit_label(old) is not None:
                    old.pop("description", None)
                old["unit_of_measure"] = unit
        name = edited["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Each invoice item needs a description of at most 200 characters.")
        values = {}
        for key in ("quantity", "unit_price"):
            raw = edited[key]
            try:
                if not isinstance(raw, str) or len(raw) > 30:
                    raise ValueError()
                value = Decimal(raw)
                if not value.is_finite() or value <= 0 or value > Decimal("100000000"):
                    raise ValueError()
            except (InvalidOperation, ValueError):
                raise ValueError("Quantity and unit price must be positive finite decimal values.") from None
            values[key] = value
        if quantize(values["unit_price"], entry.currency) != values["unit_price"]:
            raise ValueError("The unit price exceeds this currency's decimal precision.")
        old["name"] = name.strip()
        old["quantity"] = str(values["quantity"])
        old["unit_amount"]["value"] = str(values["unit_price"])
        replacement.append(old)
    proposed["items"] = replacement
    total = quantize(sum(Decimal(i["quantity"]) * Decimal(i["unit_amount"]["value"]) for i in proposed["items"]), entry.currency)
    if total <= 0 or total > Decimal("100000000"):
        raise ValueError("The corrected total is outside the supported range.")
    if agent._send_review_content(proposed) == original_content:
        raise ValueError("No invoice details changed.")
    # Retire old sends before publishing the correction review: a second tab must
    # never authorize the old amount while a replacement is awaiting approval.
    for old in previous:
        if old.status == "PENDING":
            old.status, old.result = "REJECTED", {"reason": "Superseded by a draft correction review; nothing was sent."}
    recipient_changed = proposed["primary_recipients"] != json.loads(original_content)["primary_recipients"]
    terms_changed = proposed["detail"]["payment_term"] != json.loads(original_content)["detail"]["payment_term"]
    note_changed = proposed["detail"].get("note") != json.loads(original_content)["detail"].get("note")
    new_email = proposed["primary_recipients"][0]["billing_info"]["email_address"]
    action = agent._queue("update_invoice", invoice_id,
        (f"Correct billing recipient on unsent invoice {entry.invoice_number}: {entry.client_email} → {new_email}"
         if recipient_changed else f"Correct payment terms on unsent invoice {entry.invoice_number}"
         if terms_changed else f"Correct invoice note on unsent invoice {entry.invoice_number}"
         if note_changed else f"Correct work details on unsent invoice {entry.invoice_number} ({entry.currency} {total})"
         if total == entry.total else f"Correct unsent invoice {entry.invoice_number}: {entry.currency} {entry.total} → {total}"),
        {"original": original_content, "invoice": proposed, "previous_total": str(entry.total),
         "total": str(total), "prepaid": "0"})
    agent._log("draft_correction_prepared", action=action.id, invoice=invoice_id)
    return {"ok": True, "approval_id": action.id, "invoice_id": invoice_id}

def check(agent, action):
    _eligible(agent, action.invoice_id, action.id)
    current = agent.client.get_invoice(action.invoice_id).body
    if (current.get("id") != action.invoice_id or current.get("status") != "DRAFT"
            or agent._send_review_content(current, provider=True) != action.payload["original"]):
        action.status, action.result = "REJECTED", {"reason": "Invoice changed since correction review; no update attempted."}
        raise ValueError(action.result["reason"])

def apply(agent, action):
    with agent.client.permit("update_invoice", action.invoice_id):
        response = agent.client.update_invoice(action.invoice_id, deepcopy(action.payload["invoice"]))
    if not 200 <= response.status < 300:
        raise ValueError("The provider did not confirm the correction.")
    current = agent.client.get_invoice(action.invoice_id).body
    if (current.get("id") != action.invoice_id or current.get("status") != "DRAFT"
            or agent._send_review_content(current, provider=True) != agent._send_review_content(action.payload["invoice"])):
        raise ValueError("The provider correction could not be verified; inspect the invoice before another action.")
    entry = agent.ledger[action.invoice_id]
    entry.total = Decimal(action.payload["total"])
    facts = agent._invoice_presentation(action.invoice_id, current)
    entry.client_name = facts["client_name"]
    entry.client_email = facts["client_email"]
    term = current["detail"]["payment_term"]
    entry.due_days = next((days for days, kind in TERM_TYPES.items() if kind == term.get("term_type")), None)
    entry.invoice_due_on = date.fromisoformat(term["due_date"]) if term.get("due_date") else None
    entry.provider_due_on = facts["provider_due_on"]
    entry.provider_due_known = facts["provider_due_known"]
    entry.provider_receipt_pending = facts["provider_receipt_pending"]
    result = {"corrected": True, "invoice_id": action.invoice_id, "total": str(entry.total), "notification_sent": False}
    # DurableAgent._queue saves immediately. Never save an already-dispatched
    # correction as pending while publishing the separate new send review.
    action.status, action.result = "APPROVED", result
    fresh = agent._queue("send_invoice", action.invoice_id,
        f"Send invoice {entry.invoice_number} for {entry.currency} {entry.total} to {entry.client_email}",
        {"invoice": deepcopy(action.payload["invoice"]), "prepaid": "0"})
    result["approval_id"] = fresh.id
    return result
