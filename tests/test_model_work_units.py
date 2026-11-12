import json
from decimal import Decimal

from ledgerly.extract import LLMExtractor, LLM_PROMPT

SOURCE = (
    "From: Mira Chen <mira@example.com>\n"
    "Please invoice in USD. The landing page was five hours at $67 per hour; "
    "accessibility review was a flat $180.\nNet 14\n"
)

def extract(unit=None, source=SOURCE, qty=5):
    data = {"client_name":"Mira Chen", "client_email":"mira@example.com",
            "currency":"USD", "due_days":14, "confidence":0.9,
            "line_items":[{"desc":"Landing page","qty":qty,"unit_price":"67","unit":unit},
                          {"desc":"Accessibility review","qty":1,"unit_price":"180"}]}
    return LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(source)

def test_prompt_requests_the_existing_unit_field():
    assert '"unit": string|null' in LLM_PROMPT

def test_omitted_hours_are_recovered_from_the_unique_grounded_work_clause():
    result=extract()
    assert not result.errors, result.issues
    assert result.line_items[0].unit == "hours"
    assert result.line_items[1].unit is None
    assert result.total() == Decimal("515")

def test_equivalent_hour_abbreviation_is_preserved():
    result=extract("hr")
    assert not result.errors
    assert result.line_items[0].unit == "hr"

def test_conflicting_unit_is_flagged_instead_of_silently_rewritten():
    result=extract("days")
    assert result.line_items[0].unit == "days"
    assert any(i.field=="line_items[0].unit" and i.severity=="error" for i in result.issues)

def test_rate_period_mismatch_does_not_invent_a_conversion():
    result=extract(source=SOURCE.replace("five hours","five days"))
    assert any(i.field=="line_items[0].unit" and i.severity=="error" for i in result.issues)
    assert result.line_items[0].qty == Decimal("5")
    assert result.line_items[0].unit is None

def test_unproven_quantity_does_not_borrow_a_source_unit():
    result=extract(qty=6)
    assert any(i.field=="line_items[0].qty" and i.severity=="error" for i in result.issues)
    assert result.line_items[0].unit is None

def test_quoted_old_unit_cannot_override_current_work():
    source=SOURCE.replace("five hours at $67 per hour","five items at $67 each") + "> Landing page was five hours at $67 per hour.\n"
    result=extract(source=source)
    assert not result.errors, result.issues
    assert result.line_items[0].unit == "items"


def test_work_words_between_quantity_and_unit_preserve_hours_and_reject_days():
    source = ("From: Mira Chen <mira@example.com>\n"
              "Please invoice five landing-page hours at USD 67 per hour; accessibility review was a flat USD 180.\nNet 14")
    correct=extract("hours",source=source)
    assert not correct.errors,correct.issues
    missing=extract(source=source)
    assert missing.line_items[0].unit=="hours"
    wrong=extract("days",source=source)
    assert any(i.severity=="error" and i.field=="line_items[0].unit" for i in wrong.issues)


def test_interposed_work_unit_does_not_borrow_rate_period():
    source = ("From: Mira Chen <mira@example.com>\n"
              "Please invoice five landing-page items at USD 67 per hour; accessibility review was a flat USD 180.\nNet 14")
    result=extract("hours",source=source)
    assert any(i.severity=="error" and i.field=="line_items[0].unit" for i in result.issues)
