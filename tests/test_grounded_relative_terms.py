import json
import pytest
from ledgerly.extract import LLMExtractor, RulesExtractor

def extract(term, source):
    data = {"client_name":"Ada","client_email":"ada@example.test","currency":"EUR",
            "line_items":[{"desc":"Editing","qty":1,"unit_price":"20"}],
            "due_days":term,"amount_paid":0,"confidence":1}
    return LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(
        "From: Ada <ada@example.test>\nEditing EUR 20.\n"+source)

def test_missing_explicit_payment_term_is_recovered_with_provenance():
    ex=extract(None,"Please make payment due in 10 days.")
    assert ex.due_days == 10
    assert any("recovered" in i.message for i in ex.issues)

def test_wrong_term_is_blocked_not_silently_replaced():
    ex=extract(30,"Net 10")
    assert ex.due_days == 30
    assert any(i.field=="due_days" and i.severity=="error" for i in ex.issues)

def test_conflicting_current_terms_are_not_chosen():
    ex=extract(None,"Net 10 or Net 30")
    assert ex.due_days is None
    assert any(i.field=="due_days" and i.severity=="error" for i in ex.issues)

def test_quoted_old_term_and_delivery_window_are_not_payment_evidence():
    assert extract(None,"> Net 10\nDeliver within 12 days.").due_days is None

def test_conditional_terms_need_a_decision():
    ex=extract(None,"Optional payment terms: Net 10 if approved.")
    assert ex.due_days is None
    assert any(i.field=="due_days" and i.severity=="error" for i in ex.issues)

def test_absent_term_is_not_defaulted():
    assert extract(None,"Thank you.").due_days is None


@pytest.mark.parametrize(("source", "days"), [
    ("Payment is due 14 days after the invoice date.", 14),
    ("Payment due 7 days after invoice.", 7),
    ("Payable 21 days from the invoice date.", 21),
    ("Payment is due 1 day from invoice.", 1),
])
def test_invoice_relative_term_is_preserved_by_rules_and_grounded_model(source, days):
    rules = RulesExtractor().extract(
        "From: Ada <ada@example.test>\nEditing EUR 20.\n" + source)
    assert rules.due_days == days
    assert not any("defaulted" in issue.message for issue in rules.issues)
    recovered = extract(None, source)
    assert recovered.due_days == days
    assert any("recovered" in issue.message for issue in recovered.issues)
    assert not any(issue.field == "due_days" and issue.severity == "error"
                   for issue in extract(days, source).issues)


def test_invoice_relative_model_disagreement_and_conflict_still_block():
    wrong = extract(30, "Payment is due 14 days after the invoice date.")
    assert wrong.due_days == 30
    assert any(issue.field == "due_days" and issue.severity == "error"
               for issue in wrong.issues)
    conflict = extract(None, "Net 30.\nPayment is due 14 days after the invoice date.")
    assert conflict.due_days is None
    assert any(issue.field == "due_days" and issue.severity == "error"
               for issue in conflict.issues)


@pytest.mark.parametrize("source", [
    "Payment is due 0 days after the invoice date.",
    "Payment is due 14 business days after the invoice date.",
    "Payment is due 14 days after invoice approval.",
    "Payment is due 14 days after the invoice is received.",
    "Payment is due 14 days after the invoice date if approved.",
    "If approved, payment is due 14 days after the invoice date.",
    "Deliver the artwork 14 days after the invoice date.",
    "> Payment is due 14 days after the invoice date.",
])
def test_other_start_events_and_conditional_or_old_terms_are_not_invoice_date_terms(source):
    assert extract(None, source).due_days is None
    rules = RulesExtractor().extract(
        "From: Ada <ada@example.test>\nEditing EUR 20.\n" + source)
    assert rules.due_days == 30
    assert any("defaulted" in issue.message for issue in rules.issues)
