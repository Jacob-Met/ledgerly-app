"""Original quotations for normalized invoice fields, through shared parsing."""
from copy import deepcopy
from decimal import Decimal

import pytest

from ledgerly.extract import Extraction, RulesExtractor, source_references

SOURCE = """From: Mina Holt <mina@northfield.example>
Subject: 🧾 Research invoice
Please invoice Northfield Studio, billing email accounts@northfield.example, in CAD.
Four research interviews were completed at CAD 85 each, plus CAD 55 for the synthesis memo.
Payment is due in 14 days.
We discussed an optional workshop at CAD 120, but it has not been approved; leave it off this invoice.
Thanks, Mina
"""


def test_count_words_and_natural_terms_quote_the_original_request():
    extraction = RulesExtractor().extract(SOURCE)
    references = source_references(extraction, SOURCE)
    assert references["line_items"][0]["qty"] == {"text": "Four", "value": "4"}
    assert references["payment_terms"] == {"text": "due in 14 days", "value": "14"}
    assert extraction.line_items[1].desc.lower() == "synthesis memo"
    assert references["line_items"][1]["qty"] is None  # Flat fee, no stated count.


def test_reference_generation_does_not_change_the_extraction():
    extraction = RulesExtractor().extract(SOURCE)
    before = deepcopy(extraction.to_dict())
    source_references(extraction, SOURCE)
    assert extraction.to_dict() == before


@pytest.mark.parametrize("change,value", [
    ("qty", Decimal(5)), ("unit_price", Decimal(86)),
    ("currency", "USD"), ("unit", "hours"),
])
def test_a_different_value_cannot_borrow_the_source_quantity(change, value):
    extraction = RulesExtractor().extract(SOURCE)
    setattr(extraction.line_items[0], change, value)
    assert source_references(extraction, SOURCE)["line_items"][0]["qty"] is None


@pytest.mark.parametrize("work", [
    "about 4 interviews at CAD 85 each",
    "a few interviews at CAD 85 each",
    "4-6 interviews at CAD 85 each",
    "Interview at CAD 85",
])
def test_estimates_and_implicit_single_items_do_not_get_quantity_quotes(work):
    text = "From: Mina <mina@example.test>\n" + work
    extraction = RulesExtractor().extract(text)
    assert extraction.line_items
    assert all(row["qty"] is None for row in source_references(extraction, text)["line_items"])


def test_repeated_or_historical_work_does_not_supply_a_unique_quote():
    line = "Four research interviews at CAD 85 each"
    source = line + "\n" + line
    extraction = RulesExtractor().extract(source)
    assert all(row["qty"] is None for row in source_references(extraction, source)["line_items"])
    extraction = RulesExtractor().extract(line)
    assert source_references(extraction, "> " + line)["line_items"][0]["qty"] is None


@pytest.mark.parametrize("terms", [
    "", "Net 14. Net 14.", "Net 14. Net 30.",
    "Delivery within 14 days.", "If approved, payment is due in 14 days.",
    "Net 14. Payment due on 2026-12-03.",
])
def test_default_repeated_conflicting_or_qualified_terms_have_no_quote(terms):
    source = "From: Mina <mina@example.test>\n2 hours editing at USD 75\n" + terms
    extraction = RulesExtractor().extract(source)
    assert source_references(extraction, source)["payment_terms"] is None


@pytest.mark.parametrize("terms,value", [
    ("Payment is due within 14 days.", "14"),
    ("Please pay within 21 days.", "21"),
    ("14-day payment terms.", "14"),
    ("Payable immediately.", "0"),
    ("Payment due on December 3, 2026.", "2026-12-03"),
    ("Payment due at the end of November 2026.", "2026-11-30"),
])
def test_one_explicit_payment_instruction_has_a_literal_quote(terms, value):
    source = "From: Mina <mina@example.test>\nTwo hours editing at USD 75\n" + terms
    extraction = RulesExtractor().extract(source)
    references = source_references(extraction, source)
    assert references["line_items"][0]["qty"] == {"text": "Two hours", "value": "2"}
    assert references["payment_terms"]["value"] == value
    assert references["payment_terms"]["text"] in terms


def test_model_citations_are_recomputed_from_source_and_literal_work_wording():
    extraction = RulesExtractor().extract(SOURCE)
    data = extraction.to_dict()
    data["source_references"] = {"line_items": [{"qty": {"text": "invented", "value": "4"}}]}
    data["line_items"][0]["desc"] = "Research interviews"
    model = Extraction.from_dict(data)
    assert source_references(model, SOURCE)["line_items"][0]["qty"] == {"text": "Four", "value": "4"}

def test_count_range_keeps_its_work_and_ambiguity_instead_of_becoming_a_separator():
    source = "From: Mina <mina@example.test>\n4-6 interviews at CAD 85 each. Net 14."
    extraction = RulesExtractor().extract(source)
    assert extraction.line_items[0].desc == "Interviews"
    assert extraction.line_items[0].qty == Decimal(4)
    assert any(issue.field == "line_items[0].qty" and "range" in issue.message
               for issue in extraction.issues)
    assert source_references(extraction, source)["line_items"][0]["qty"] is None

def test_explicit_multiplier_is_kept_when_count_and_price_share_the_same_number():
    source = "From: Mina <mina@example.test>\n1 x printed sticker at CAD 1. Net 14."
    extraction = RulesExtractor().extract(source)
    assert source_references(extraction, source)["line_items"][0]["qty"] == {
        "text": "1 x", "value": "1",
    }
