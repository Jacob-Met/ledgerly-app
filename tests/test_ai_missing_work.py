"""Actual omitted-line regression plus unfamiliar source/correction cases."""
import json
from decimal import Decimal
import pytest
from ledgerly.extract import LLMExtractor
from ledgerly.agent import Agent
from ledgerly.paypal import SandboxMock

SOURCE = "From: Mina Okada <mina@example.test>\n\n3.5 hours at USD 80 per hour for copy editing.\nProofreading — USD 45\nNet 14."
def extract(source=SOURCE, items=None):
    data = {"client_email": "mina@example.test", "currency": "USD",
            "line_items": items or [{"desc":"copy editing","qty":3.5,"unit_price":80,"currency":"USD"}],
            "due_days":14,"confidence":.99}
    return LLMExtractor(lambda _:json.dumps(data), check_missing_work=True).extract(source)
def missing(ex):
    return [i for i in ex.errors if "Priced work in the current email" in i.message]

def test_omitted_priced_work_without_total_needs_review():
    ex=extract()
    assert ex.total()==Decimal("280")
    assert len(missing(ex))==1
    assert "325.00" in missing(ex)[0].message and "Proofreading" in missing(ex)[0].message
    provider=SandboxMock()
    class Frozen:
        def extract(self, _):return ex
    agent=Agent(provider,{"name":"Tester","email_address":"tester@example.test"},extractor=Frozen())
    assert agent.tool_create_invoice(SOURCE)["needs_review"]
    assert provider.requests==[]

def test_complete_and_consolidated_proposals_remain_usable():
    assert not missing(extract(items=[{"desc":"Editorial package","qty":1,"unit_price":325,"currency":"USD"}]))

def test_explicit_inline_separate_fee_is_preserved():
    source="From: Mina <mina@example.test>\n\nPlease invoice 3.5 hours at USD 80 per hour for copy editing, plus a separate USD 45 proofreading fee.\nNet 14."
    assert missing(extract(source))

def test_currency_cannot_mask_missing_work():
    source="From: Mina <mina@example.test>\n\nCopy editing — USD 280\nTranslation — EUR 60\nNet 14."
    issue=missing(extract(source))[0]
    assert "EUR 60.00" in issue.message and "EUR 0.00" in issue.message

@pytest.mark.parametrize("extra",[
    "> Proofreading — USD 45",
    "Optional proofreading — USD 45",
    "If approved, proofreading — USD 45",
    "Instead, proofreading — USD 45",
    "Credit — USD 45",
    "Budget: USD 900",
    "Already paid USD 45",
])
def test_history_alternatives_adjustments_and_summaries_are_not_new_work(extra):
    source="From: Mina <mina@example.test>\n\nCopy editing — USD 280\n"+extra+"\nNet 14."
    assert not missing(extract(source))

def test_unknown_quantity_and_ambiguous_amounts_defer_to_review():
    source="From: Mina <mina@example.test>\n\nCopy editing — USD 280\nSeveral proofreading hours at USD 45\nNet 14."
    assert not missing(extract(source))

def test_existing_darnell_fixture_omission_needs_review_in_app_policy():
    # This source and model response predate the fix in test_extract.py.
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/"fixtures"/"05_missing_email.txt").read_text()+"\nmy email: darnell.b@mailbox.example\n"
    data={"client_name":"Darnell Brooks","client_email":"darnell.b@mailbox.example","currency":"USD",
          "line_items":[{"desc":"Logo concepts","qty":3,"unit_price":250}],
          "due_days":7,"amount_paid":0,"confidence":.9,"issues":[]}
    ex=LLMExtractor(lambda _:json.dumps(data), check_missing_work=True).extract(source)
    assert missing(ex)
    assert "Brand colour guide" in missing(ex)[0].message

def test_unrepresentable_source_total_does_not_crash_model_review():
    source="From: Mina <mina@example.test>\n\nCopy editing — USD "+("9"*80)+"\nNet 14."
    assert not missing(extract(source))
