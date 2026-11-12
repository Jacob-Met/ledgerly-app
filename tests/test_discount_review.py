from decimal import Decimal
import pytest
from ledgerly.extract import RulesExtractor
from ledgerly.agent import Agent
from ledgerly.paypal import SandboxMock

@pytest.mark.parametrize("adjustment", ["Discount — USD 20", "Discount — USD -20", "Less loyalty discount USD 20", "Courtesy discount: USD 20", "Discount: 10%", "10% discount", "Discount (loyalty) — USD 20", "Credit — USD 20", "Less USD 20 discount", "Discount of USD 20", "Credit (client accommodation) — USD 20", "Discount (returning customer) — USD 20"])
def test_discount_never_becomes_work_or_disappears_into_draft(adjustment):
    text="From: Mira Lane <mira@example.test>\n\nSound editing — USD 190\n"+adjustment+"\nNet 14."
    ex=RulesExtractor().extract(text)
    assert ex.total()==Decimal("190")
    assert any(i.severity=="error" and "Discount or credit requires review" in i.message for i in ex.issues)
    mock=SandboxMock(); agent=Agent(mock,{"name":"Reviewer","email_address":"reviewer@example.test"})
    assert not agent.tool_create_invoice(text)["ok"]
    assert mock.requests==[] and agent.list_pending()==[]

@pytest.mark.parametrize("work", ["Discount campaign design", "Credit risk analysis"])
def test_work_about_discounts_remains_chargeable(work):
    ex=RulesExtractor().extract(f"From: Mira <mira@example.test>\n\n{work} — USD 190\nNet 14.")
    assert not ex.errors and ex.total()==190

def test_table_discount_is_not_positive_work():
    ex=RulesExtractor().extract("From: Mira <mira@example.test>\n\n| Description | Qty | Unit price |\n| Sound editing | 1 | USD 190 |\n| Discount | 1 | 20 |\nNet 14.")
    assert ex.total()==190
    assert any(i.severity=="error" and "Discount or credit requires review" in i.message for i in ex.issues)

def test_merchant_can_confirm_net_amount_without_sending(monkeypatch):
    import sys
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "web-demo" / "python"))
    from bridge import Demo
    demo = Demo()
    source = "From: Mira Lane <mira@example.test>\n\nSound editing — USD 190\nDiscount — USD 20\nNet 14."
    demo.dispatch({"action": "analyze", "text": source})
    demo.dispatch({"action": "draft", "text": source})
    assert not demo.agent.ledger and not demo.mock.requests
    checked = demo.dispatch({"action": "review", "text": source, "confirmed": True,
        "fields": {"client_name": "Mira Lane", "client_email": "mira@example.test",
            "due_days": "14", "amount_paid": "0",
            "line_items": [{"desc": "Sound editing after agreed USD 20 discount",
                "qty": "1", "unit_price": "170", "currency": "USD", "unit": ""}]}})["result"]
    assert checked["valid"]
    result = demo.dispatch({"action": "draft", "text": source, "review_id": checked["review_id"]})
    assert result["result"]["reviewed"]
    assert len(demo.agent.ledger) == 1 and len(demo.agent.list_pending()) == 1
    invoice = next(iter(demo.mock.invoices.values()))
    assert invoice["status"] == "DRAFT"
    assert invoice["amount"]["value"] == "170.00"


def test_parenthesized_credit_requires_review_and_explicit_net_recovery(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "web-demo" / "python"))
    from bridge import Demo

    source = "From: Rae Jensen <rae@example.org>\n\nLocation sound — EUR 310\nCredit (client accommodation) — EUR 35\nNet 14."
    demo = Demo()
    analyzed = demo.dispatch({"action": "analyze", "text": source})["result"]
    assert any(i["severity"] == "error" for i in analyzed["issues"])
    demo.dispatch({"action": "draft", "text": source})
    assert not demo.agent.ledger and not demo.mock.requests and not demo.agent.list_pending()
    checked = demo.dispatch({"action": "review", "text": source, "confirmed": True,
        "fields": {"client_name": "Rae Jensen", "client_email": "rae@example.org",
            "due_days": "14", "amount_paid": "0",
            "line_items": [{"desc": "Location sound after agreed credit",
                "qty": "1", "unit_price": "275", "currency": "EUR", "unit": ""}]}})["result"]
    assert checked["valid"]
    demo.dispatch({"action": "draft", "text": source, "review_id": checked["review_id"]})
    invoice = next(iter(demo.mock.invoices.values()))
    assert invoice["status"] == "DRAFT" and invoice["amount"]["value"] == "275.00"
    assert len(demo.agent.list_pending()) == 1



@pytest.mark.parametrize("adjustment", [
    "Apply the agreed 10% project discount.",
    "Please apply the agreed 10% discount to this invoice.",
    "Deduct the courtesy USD 20 credit.",
    "Subtract the agreed EUR 20 invoice discount.",
])
def test_explicit_adjustment_sentence_remains_a_review_decision(adjustment):
    source = "From: Rowan Hale <rowan@example.test>\n\nBrand identity — USD 600\n" + adjustment + "\nNet 14."
    extracted = RulesExtractor().extract(source)
    assert extracted.total() == Decimal("600")
    assert any(issue.severity == "error" and "Discount or credit requires review" in issue.message
               for issue in extracted.issues)
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert not agent.tool_create_invoice(source)["ok"]
    assert not mock.requests



@pytest.mark.parametrize("adjustment", [
    "Apply the agreed 10% project discount.",
    "Discount: 10%",
    "VAT 20%",
])
def test_grounded_model_cannot_discard_the_source_adjustment(adjustment):
    import json
    from ledgerly.extract import LLMExtractor

    source = "From: Rowan Hale <rowan@example.test>\n\nBrand identity — USD 600\n" + adjustment + "\nNet 14."
    response = {"client_name": "Rowan Hale", "client_email": "rowan@example.test",
        "currency": "USD", "due_days": 14, "amount_paid": "0", "confidence": 1,
        "line_items": [{"desc": "Brand identity", "qty": "1", "unit_price": "600",
                        "currency": "USD", "unit": None}], "issues": []}
    calls = []
    def complete(prompt):
        calls.append(prompt)
        return json.dumps(response)
    extracted = LLMExtractor(complete, ground_prices=True, check_missing_work=True).extract(source)
    assert len(calls) == 1
    assert extracted.total() == Decimal("600")
    assert any(issue.severity == "error" and "requires review:" in issue.message
               for issue in extracted.issues)
