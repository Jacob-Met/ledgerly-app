"""Regressions from retained native invoice responses; no inference is repeated."""
import copy
import json
from decimal import Decimal
from pathlib import Path
import pytest
from ledgerly.extract import HybridExtractor, LLMExtractor, RulesExtractor

OBSERVED = json.loads((Path(__file__).parent / "fixtures" / "native-priced-prose-observations.json").read_text())

def grounded(case_id, *, source=None, raw=None):
    case = OBSERVED[case_id]
    text = case["case"]["source"] if source is None else source
    payload = case["raw"] if raw is None else raw
    return LLMExtractor(lambda prompt: json.dumps(payload), ground_prices=True, check_missing_work=True).extract(text)

def assert_invoice(extraction, case_id):
    expected = OBSERVED[case_id]["case"]["expected"]
    assert not extraction.errors, extraction.to_dict()
    for field in ("client_name", "client_email", "currency", "due_days", "due_date"):
        assert getattr(extraction, field) == expected[field]
    assert extraction.amount_paid == Decimal(expected["amount_paid"])
    assert extraction.total() == Decimal(expected["total"])
    assert len(extraction.line_items) == len(expected["items"])
    for actual, wanted in zip(extraction.line_items, expected["items"]):
        assert actual.qty == Decimal(wanted["qty"])
        assert actual.unit_price == Decimal(wanted["unit_price"])
        assert actual.currency == expected["currency"]
        assert actual.unit in wanted["unit_allowed"]
        for word in wanted["description_words"]:
            assert word.rstrip("s") in actual.desc.lower()

@pytest.mark.parametrize("case_id", ["bracken", "foxglove"])
def test_observed_complete_prose_works_without_inference(case_id):
    calls = []
    extractor = HybridExtractor(RulesExtractor(), LLMExtractor(lambda prompt: calls.append(prompt) or "{}"), threshold=.90)
    result = extractor.extract(OBSERVED[case_id]["case"]["source"])
    assert_invoice(result, case_id)
    assert calls == []

@pytest.mark.parametrize("case_id", ["bracken", "foxglove"])
def test_retained_complete_response_keeps_its_grounded_values(case_id):
    assert_invoice(grounded(case_id), case_id)

@pytest.mark.parametrize("field,value", [("unit_price", "12"), ("qty", 3), ("unit", "days")])
def test_price_partition_cannot_license_a_different_rate_count_or_unit(field, value):
    raw = copy.deepcopy(OBSERVED["bracken"]["raw"])
    raw["line_items"][0][field] = value
    assert grounded("bracken", raw=raw).errors

def test_each_priced_clause_still_requires_its_own_work_item():
    raw = copy.deepcopy(OBSERVED["bracken"]["raw"])
    raw["line_items"].pop(1)
    assert grounded("bracken", raw=raw).errors

def test_generic_description_cannot_select_a_rate_by_coincidence():
    raw = copy.deepcopy(OBSERVED["bracken"]["raw"])
    raw["line_items"][0]["desc"] = "handover"
    assert grounded("bracken", raw=raw).errors

@pytest.mark.parametrize("paid", [0, 50, 200])
def test_confirmed_payment_cannot_be_omitted_or_replaced(paid):
    raw = copy.deepcopy(OBSERVED["foxglove"]["raw"])
    raw["amount_paid"] = paid
    assert any(issue.field == "amount_paid" and issue.severity == "error"
               for issue in grounded("foxglove", raw=raw).issues)

def test_confirmed_payment_keeps_its_own_currency():
    source = OBSERVED["foxglove"]["case"]["source"].replace("AUD 100 deposit", "USD 100 deposit")
    assert any(issue.field == "amount_paid" and issue.severity == "error"
               for issue in grounded("foxglove", source=source).issues)

@pytest.mark.parametrize("wording", [
    "has not yet been received", "will be received and confirmed",
    "is still pending", "has already been received if the grant clears",
])
def test_received_payment_grammar_does_not_drop_uncertainty(wording):
    source = OBSERVED["foxglove"]["case"]["source"].replace("has already been received and confirmed", wording)
    rules = RulesExtractor().extract(source)
    assert rules.amount_paid == 0 and rules.errors
    assert grounded("foxglove", source=source).errors

@pytest.mark.parametrize("prefix", [
    "If the grant is approved, please bill",
    "For the optional website handover, please bill",
    "For the website handover only after approval, please bill",
])
def test_complete_price_list_does_not_drop_its_condition(prefix):
    source = OBSERVED["bracken"]["case"]["source"].replace("For the website handover, please bill", prefix)
    assert RulesExtractor().extract(source).errors
    assert grounded("bracken", source=source).errors

def test_conditional_named_delivery_stays_a_human_choice():
    source = OBSERVED["foxglove"]["case"]["source"].replace(
        "Please send the invoice", "If the grant is approved, please send the invoice")
    calls = []
    result = HybridExtractor(RulesExtractor(), LLMExtractor(lambda prompt: calls.append(prompt) or "{}"), threshold=.90).extract(source)
    assert result.errors and calls == []
    assert any(issue.field == "client_email" and issue.severity == "error" for issue in result.issues)

def test_comma_bill_to_keeps_the_literal_name_and_address():
    result = RulesExtractor().extract(
        "Bill to Gannet Museum, billing@gannetmuseum.example.\n"
        "Label preparation at JPY 3200.\nNet 14.")
    assert not result.errors
    assert result.client_name == "Gannet Museum"
    assert result.client_email == "billing@gannetmuseum.example"

def test_complete_counted_list_handles_other_work_nouns_and_rates():
    result = RulesExtractor().extract(
        "Bill to: Vale Press <accounts@valepress.example>\n"
        "For the magazine refresh, please invoice four translated summaries at GBP 24 per summary, "
        "six proof copies at GBP 7 per copy and the agreed GBP 18 asset pack.\nNet 21.")
    assert not result.errors, result.to_dict()
    assert result.total() == 156 and result.due_days == 21
    assert [(row.qty, row.unit_price, row.unit) for row in result.line_items] == [
        (4, 24, "summaries"), (6, 7, "copies"), (1, 18, None)]

def test_retained_unstated_work_word_still_requires_review():
    result = grounded("gannet")
    assert any(issue.field == "line_items[0].unit_price" and issue.severity == "error" for issue in result.issues)
    assert not any(issue.severity == "error" and issue.field in {
        "line_items[1].unit_price", "line_items[2].unit_price"} for issue in result.issues)
