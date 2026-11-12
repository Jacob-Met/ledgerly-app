"""An actual exported job paragraph must not become a clean partial invoice."""
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

import pytest

from ledgerly.extract import RulesExtractor

HEADER = "From: Tessa Rowan <tessa@example.org>\n\n"
WORK = ("Please invoice Tessa Rowan at tessa@example.org for 3 hours of "
        "photography at USD 125 per hour and 2 print sets at USD 40 each.")
JOB = HEADER + WORK + " Total USD 455. Payment terms: Net 14."


@pytest.mark.parametrize("separator", [" ", "\n", "  "])
@pytest.mark.parametrize("label", ["Total", "Invoice total:", "Grand total", "The total"])
def test_explicit_compound_work_matches_stated_total_without_losing_quantities(agent, mock, separator, label):
    text = HEADER + WORK + separator + label + " USD 455. Net 14."
    ex = RulesExtractor().extract(text)
    assert [item.qty for item in ex.line_items] == [Decimal("3"), Decimal("2")]
    assert [item.unit_price for item in ex.line_items] == [Decimal("125"), Decimal("40")]
    assert ex.total() == Decimal("455")
    assert ex.errors == []
    result = agent.tool_create_invoice(text)
    assert result["ok"]
    invoice = next(iter(mock.invoices.values()))
    assert Decimal(invoice["amount"]["value"]) == Decimal("455")
    assert invoice["status"] == "DRAFT"
    assert not [request for request in mock.requests if request[1].endswith("/send")]


@pytest.mark.parametrize("separator", [" ", "\n", "  "])
@pytest.mark.parametrize("label", ["Total", "Invoice total:", "Grand total", "The total"])
def test_actual_compound_total_disagreement_still_blocks_draft(agent, mock, separator, label):
    text = HEADER + WORK + separator + label + " USD 490. Net 14."
    ex = RulesExtractor().extract(text)
    assert ex.total() == Decimal("455")
    assert any(i.field == "line_items" and i.severity == "error"
               and "490" in i.message and "455" in i.message for i in ex.issues)
    assert not agent.tool_create_invoice(text)["ok"]
    assert mock.requests == []
    assert agent.list_pending() == []


def test_matching_inline_total_does_not_turn_summary_into_work():
    ex = RulesExtractor().extract(
        HEADER + "Photography — USD 125. Total USD 125. Net 14.")
    assert ex.total() == Decimal("125")
    assert len(ex.line_items) == 1
    assert ex.issues == []


def test_decimal_price_and_quoted_old_total_keep_their_meaning():
    ex = RulesExtractor().extract(
        HEADER + "Photography — USD 125.50. Total USD 125.50. Net 14.\n"
        "> Total USD 455.")
    assert ex.total() == Decimal("125.50")
    assert ex.issues == []


def test_mixed_currency_totals_are_not_added_or_compared_across_currencies():
    ex = RulesExtractor().extract(
        HEADER + "Photography — USD 125\nPrints — EUR 40\nTotal USD 125. Net 14.")
    assert ex.currencies() == ["EUR", "USD"]
    assert not any("differs from computed total" in i.message for i in ex.issues)


@pytest.mark.parametrize("label", ["Budget cap", "Subtotal", "Balance due"])
def test_non_total_summary_retains_nonblocking_warning(label):
    ex = RulesExtractor().extract(
        HEADER + "Photography — USD 125\n" + label + ": USD 455\nNet 14.")
    assert not ex.errors
    assert any(i.severity == "warning" and "differs" in i.message for i in ex.issues)


def test_browser_review_recovers_explicit_455_invoice_without_sending():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "web-demo" / "python"))
    spec = importlib.util.spec_from_file_location(
        "stated_total_test_bridge", root / "web-demo" / "python" / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    demo = bridge.Demo()
    mismatch = JOB.replace("Total USD 455", "Total USD 490")
    analysis = demo.dispatch({"action": "analyze", "text": mismatch})
    assert any(i["severity"] == "error" for i in analysis["result"]["issues"])
    demo.dispatch({"action": "draft", "text": mismatch})
    assert demo.mock.requests == []
    fields = {
        "client_name": "Tessa Rowan", "client_email": "tessa@example.org",
        "due_days": "14", "amount_paid": "0",
        "line_items": [
            {"desc": "Photography", "qty": "3", "unit_price": "125",
             "currency": "USD", "unit": "hours"},
            {"desc": "Print sets", "qty": "2", "unit_price": "40",
             "currency": "USD", "unit": "sets"},
        ],
    }
    reviewed = demo.dispatch({"action": "review", "text": mismatch,
                              "confirmed": True, "fields": fields})["result"]
    assert reviewed["valid"]
    assert reviewed["totals_by_currency"] == [{"currency": "USD", "total": "455"}]
    assert demo.mock.requests == []
    drafted = demo.dispatch({"action": "draft", "text": mismatch,
                            "review_id": reviewed["review_id"]})
    assert Decimal(drafted["state"]["ledger"][0]["total"]) == Decimal("455.00")
    assert len(drafted["state"]["pending"]) == 1
    assert not [r for r in demo.mock.requests if r[1].endswith("/send")]


@pytest.mark.parametrize("terms,days", [
    ("Payment terms Net 21.", 21), ("Payment terms: Net 14.", 14),
    ("Net 7.", 7), ("Terms: Net 30 days.", 30),
    ("Due on receipt.", 0),
])
def test_compound_work_before_complete_payment_terms_remains_complete(agent, mock, terms, days):
    text = HEADER + "Please invoice 3 hours of launch consulting at $120/hour and 2 articles at $75 each. " + terms
    ex = RulesExtractor().extract(text)
    assert ex.total() == Decimal("510")
    assert [item.qty for item in ex.line_items] == [Decimal("3"), Decimal("2")]
    assert ex.due_days == days
    assert not ex.errors
    assert agent.tool_create_invoice(text)["ok"]
    assert next(iter(mock.invoices.values()))["status"] == "DRAFT"
    assert not [r for r in mock.requests if r[1].endswith("/send")]


@pytest.mark.parametrize("suffix", [
    "Payment terms Net 21 if approved.",
    "Payment terms Net 21 and 1 illustration at $80 each.",
    "Net 21 or Net 30.",
])
def test_qualified_or_work_bearing_terms_do_not_open_compound_admission(agent, mock, suffix):
    text = HEADER + "3 hours of launch consulting at $120/hour and 2 articles at $75 each. " + suffix
    assert RulesExtractor().extract(text).errors
    assert not agent.tool_create_invoice(text)["ok"]
    assert mock.requests == []
