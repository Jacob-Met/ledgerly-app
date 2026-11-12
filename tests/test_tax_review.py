from decimal import Decimal
from pathlib import Path

import pytest

from ledgerly.agent import Agent
from ledgerly.extract import RulesExtractor
from ledgerly.paypal import SandboxMock


@pytest.mark.parametrize("adjustment", [
    "VAT 20%", "Plus 20% VAT", "Sales tax: 8.25%", "Tax: 5%", "GST 10%",
])
def test_percentage_tax_cannot_silently_disappear(adjustment):
    source = f"From: Mira <mira@example.test>\n\nSound editing — USD 190\n{adjustment}\nNet 14."
    extracted = RulesExtractor().extract(source)
    assert extracted.total() == Decimal("190")
    assert any(i.severity == "error" and "Tax percentage requires review" in i.message
               for i in extracted.issues)
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert not agent.tool_create_invoice(source)["ok"]
    assert not mock.requests and not agent.list_pending()


def test_tax_related_work_is_still_chargeable():
    extracted = RulesExtractor().extract(
        "From: Mira <mira@example.test>\n\nVAT training materials — USD 190\nNet 14.")
    assert not extracted.errors and extracted.total() == Decimal("190")


def test_confirmed_final_amount_can_be_drafted_without_sending(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "web-demo" / "python"))
    from bridge import Demo

    demo = Demo()
    source = "From: Mira <mira@example.test>\n\nSound editing — USD 190\nVAT 20%\nNet 14."
    demo.dispatch({"action": "analyze", "text": source})
    demo.dispatch({"action": "draft", "text": source})
    assert not demo.agent.ledger and not demo.mock.requests
    checked = demo.dispatch({"action": "review", "text": source, "confirmed": True,
        "fields": {"client_name": "Mira", "client_email": "mira@example.test",
            "due_days": "14", "amount_paid": "0",
            "line_items": [{"desc": "Sound editing, agreed final amount",
                "qty": "1", "unit_price": "228", "currency": "USD", "unit": ""}]}})["result"]
    assert checked["valid"]
    result = demo.dispatch({"action": "draft", "text": source, "review_id": checked["review_id"]})
    assert result["result"]["reviewed"]
    assert len(demo.agent.ledger) == 1 and len(demo.agent.list_pending()) == 1
    invoice = next(iter(demo.mock.invoices.values()))
    assert invoice["status"] == "DRAFT" and invoice["amount"]["value"] == "228.00"

def test_table_percentage_tax_requires_review():
    source = "From: Mira <mira@example.test>\n\n| Description | Qty | Unit price |\n| Sound editing | 1 | USD 190 |\n| VAT | 1 | 20% |\nNet 14."
    extracted = RulesExtractor().extract(source)
    assert extracted.total() == Decimal("190")
    assert any(i.severity == "error" and "Tax percentage requires review" in i.message
               for i in extracted.issues)
