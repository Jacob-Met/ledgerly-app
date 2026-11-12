from decimal import Decimal

import pytest

from ledgerly.extract import RulesExtractor, source_references

JUNIPER = ("Please invoice Juniper Field School at accounts@juniper.example for six "
           "illustrated field cards at USD 42.50 each and two editing sessions at USD 85 each. "
           "Payment is due November 30, 2026. Please include purchase order JFS-204 on the invoice.")


def test_original_audience_paragraph_keeps_both_work_items():
    ex = RulesExtractor().extract(JUNIPER)
    assert not ex.errors
    assert ex.total() == Decimal("425")
    assert [(item.desc, item.qty, item.unit_price) for item in ex.line_items] == [
        ("Illustrated field cards", Decimal("6"), Decimal("42.50")),
        ("Editing sessions", Decimal("2"), Decimal("85")),
    ]
    assert ex.client_name == "Juniper Field School"
    assert ex.client_email == "accounts@juniper.example"
    assert ex.due_date == "2026-11-30"


def test_original_audience_paragraph_keeps_literal_purchase_order():
    ex = RulesExtractor().extract(JUNIPER)
    assert ex.note == "Purchase order JFS-204"
    assert source_references(ex, JUNIPER)["note"] == {
        "text": "Please include purchase order JFS-204 on the invoice.",
        "value": "Purchase order JFS-204",
    }


@pytest.mark.parametrize("metadata", [
    "Payment due November 30, 2026. Please include purchase order JFS-204 on the invoice.",
    "Please include purchase order JFS-204 on the invoice. Payment is due November 30, 2026.",
    "Net 21. Please include purchase order JFS-204 on the invoice.",
    "Please include purchase order JFS-204 on the invoice. Net 21.",
])
def test_complete_metadata_sentences_do_not_hide_priced_work(metadata):
    ex = RulesExtractor().extract(JUNIPER.split("Payment is due")[0] + metadata)
    assert not ex.errors and ex.total() == 425
    assert ex.note == "Purchase order JFS-204"


@pytest.mark.parametrize("source", [
    JUNIPER.replace("two editing sessions at USD 85 each", "two editing sessions"),
    JUNIPER.replace("two editing sessions", "two optional editing sessions"),
    JUNIPER + " Only if the project is approved.",
    JUNIPER.replace("on the invoice.", "on the invoice unless a replacement order arrives."),
    JUNIPER.replace("purchase order JFS-204", "a 10% discount"),
])
def test_metadata_boundaries_keep_incomplete_or_conditional_work_blocked(source):
    assert RulesExtractor().extract(source).errors


def test_quoted_paragraph_does_not_introduce_an_invoice_note():
    source = ("From: Current Client <current@example.test>\nArtwork — USD 90\nNet 21\n> " + JUNIPER)
    ex = RulesExtractor().extract(source)
    assert ex.note == "Thank you for your business."
    assert ex.total() == 90
