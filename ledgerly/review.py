"""Shared, immutable human invoice-send review for terminal operators."""
from __future__ import annotations

import json
from decimal import Decimal

from .extract import quantize


def invoice_review(action: dict) -> str:
    """Full review terms from the queued immutable approval snapshot, not model prose."""
    data = action["payload"]["invoice"]
    lines = [
        action["summary"],
        "  Recipient: " + data["primary_recipients"][0]["billing_info"]["email_address"],
        "  Invoice #: " + data["detail"]["invoice_number"],
        "  Currency: " + data["detail"]["currency_code"],
        "  Terms: " + json.dumps(data["detail"]["payment_term"], sort_keys=True),
        "  Note: " + data["detail"].get("note", "None"),
    ]
    currency = data["detail"]["currency_code"]
    gross = Decimal(0)
    for item in data["items"]:
        unit = item["unit_amount"]
        lines.append(f"  - {item['quantity']} × {item['name']}: "
                     f"{unit['currency_code']} {unit['value']} each")
        gross += Decimal(str(item["quantity"])) * Decimal(str(unit["value"]))
    lines.append(f"  Invoice total: {currency} {quantize(gross, currency)}")
    prepaid = Decimal(str(action["payload"].get("prepaid") or "0"))
    if prepaid > 0:
        # This is a client/source claim, not a provider-confirmed payment.
        lines.append(f"  Reported prior payment (unverified): {currency} {quantize(prepaid, currency)}")
        lines.append(f"  Remaining after reported payment: "
                     f"{currency} {quantize(gross - prepaid, currency)}")
    return "\n".join(lines)
