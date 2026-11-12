from decimal import Decimal

import pytest

from ledgerly.extract import RulesExtractor


def request(work):
    return ("From: Oak Vale Studio <accounts@oakvale.example>\n\n"
            + work + " Payment due November 30, 2026.")


@pytest.mark.parametrize("join", [" and ", " plus ", "; "])
@pytest.mark.parametrize("prefix", ["Please invoice ", "", "Bill us "])
def test_explicit_price_first_clauses_keep_both_charges(join, prefix):
    ex = RulesExtractor().extract(request(
        prefix + "2 x USD 120 portrait sessions" + join + "3 x USD 35 print sets."))
    assert not ex.errors
    assert ex.total() == Decimal("345")
    assert ex.due_date == "2026-11-30"
    assert [(item.desc, item.qty, item.unit_price) for item in ex.line_items] == [
        ("Portrait sessions", Decimal("2"), Decimal("120")),
        ("Print sets", Decimal("3"), Decimal("35")),
    ]


def test_price_first_clause_can_follow_an_explicit_hourly_clause():
    ex = RulesExtractor().extract(request(
        "Please invoice 2 hours of retouching at USD 120 per hour and 3 x USD 35 print sets."))
    assert not ex.errors and ex.total() == 345
    assert [item.qty for item in ex.line_items] == [2, 3]


@pytest.mark.parametrize("work", [
    "Please invoice 2 x USD 120 portrait sessions and 3 x USD 35 optional print sets.",
    "Please invoice 2 x USD 120 portrait sessions and 3 x USD 35 print sets if approved.",
    "Please invoice 2 x USD 120 portrait sessions and 3 x USD 35 per hour.",
    "Please invoice 2 x USD 120 portrait sessions and 3 x USD 35 print sets plus USD 20 courier.",
    "Please invoice 2 x USD 120 portrait sessions and 3 x print sets.",
])
def test_incomplete_or_qualified_compound_does_not_become_ready(work):
    assert RulesExtractor().extract(request(work)).errors
