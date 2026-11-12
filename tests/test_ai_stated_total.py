"""Source-total admission checks for model completions; no model calls."""
import json
from decimal import Decimal
import pytest
from ledgerly.extract import LLMExtractor

SOURCE = "From: Mina Okada <mina@example.test>\n\n3.5 hours at USD 80 per hour for copy editing.\nProofreading — USD 45\nNet 14.\n"

def proposal(*, complete=False, paid=0, qty=3.5):
    items = [{"desc": "copy editing", "qty": qty, "unit_price": 80, "currency": "USD"}]
    if complete:
        items.append({"desc": "proofreading", "qty": 1, "unit_price": 45, "currency": "USD"})
    return json.dumps({"client_name": "Mina Okada", "client_email": "mina@example.test",
                      "currency": "USD", "line_items": items, "due_days": 14,
                      "amount_paid": paid, "confidence": .9, "issues": []})

def extract(summary, **kwargs):
    raw = proposal(**kwargs)
    return LLMExtractor(lambda _: raw).extract(SOURCE + summary)

def mismatch(ex):
    return [issue for issue in ex.issues if "Stated total/budget" in issue.message]

def test_recorded_omission_conflicts_with_explicit_total():
    ex = extract("Total: USD 325")
    assert ex.total() == Decimal("280")
    assert mismatch(ex)[0].severity == "error"
    assert "325" in mismatch(ex)[0].message and "280" in mismatch(ex)[0].message

def test_complete_proposal_matches_total():
    ex = extract("Total: USD 325", complete=True)
    assert ex.total() == Decimal("325")
    assert not ex.errors
    assert not mismatch(ex)

@pytest.mark.parametrize("summary", ["> Total: USD 999", "Total: EUR 325", ""])
def test_history_other_currency_and_absent_total_do_not_claim_mismatch(summary):
    assert not mismatch(extract(summary))

def test_budget_keeps_existing_warning_policy():
    ex = extract("Budget: USD 400")
    assert mismatch(ex)[0].severity == "warning"
    assert not ex.errors

@pytest.mark.parametrize("total", [325, 280])
def test_existing_gross_or_net_prior_payment_policy_is_preserved(total):
    ex = extract(f"Already paid USD 45\nTotal: USD {total}", complete=True, paid=45)
    assert not mismatch(ex)

def test_invalid_model_quantity_does_not_enter_total_arithmetic():
    ex = extract("Total: USD 325", qty=None)
    assert ex.errors
    assert not mismatch(ex)

def test_inline_total_is_checked_without_splitting_decimal():
    raw = proposal()
    source = "From: Mina Okada <mina@example.test>\n\nCopy editing — USD 280. Total: USD 325."
    ex = LLMExtractor(lambda _: raw).extract(source)
    assert mismatch(ex)[0].severity == "error"


def test_correct_compound_model_proposal_is_not_rejected_as_ambiguous():
    raw = proposal(complete=True)
    source = "From: Mina Okada <mina@example.test>\n\nPlease invoice 3.5 hours at USD 80 per hour for copy editing, plus a separate USD 45 proofreading fee.\nTotal: USD 325\nNet 14."
    ex = LLMExtractor(lambda _: raw).extract(source)
    assert ex.total() == Decimal("325")
    assert not ex.errors
    assert not mismatch(ex)
