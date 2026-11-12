from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path

import pytest

from ledgerly.extract import RulesExtractor, Extraction, split_by_currency
from ledgerly.paypal import build_invoice, SandboxMock
from ledgerly.agent import Agent

SOURCE = "From: Mira <mira@archive.example>\n\n- 3 hours of proofreading at $80/hr\n"
INVOICER = {"name": "Studio", "email_address": "studio@example.com"}


@pytest.mark.parametrize("wording", ["Due: 2026-12-12", "Payment due by December 12, 2026", "Due date: 12 December 2026", "Please pay by 12 December 2026.", "Please pay on December 12, 2026."])
def test_calendar_date_remains_exact_when_draft_is_created_later(wording):
    ex = RulesExtractor().extract(SOURCE + wording)
    assert not ex.errors
    assert ex.due_date == "2026-12-12"
    assert ex.due_days is None
    assert not any("defaulted" in i.message for i in ex.issues)
    for issued in [date(2026, 11, 10), date(2026, 11, 18)]:
        body = build_invoice(ex, INVOICER, "C-1", issued)
        assert body["detail"]["payment_term"] == {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": "2026-12-12"}
    assert Extraction.from_dict(ex.to_dict()).due_date == "2026-12-12"


@pytest.mark.parametrize("wording", ["Due: 2026-02-30", "Due: 12/11/2026", "Due: 2026-12-12. Due: 2026-12-14", "Due: 2026-12-12. Net 30.", "Please pay by 03/12/2026", "Please pay by 30 February 2027"])
def test_uncertain_or_conflicting_calendar_terms_block_drafting(wording):
    ex = RulesExtractor().extract(SOURCE + wording)
    assert any(i.field == "due_date" and i.severity == "error" for i in ex.issues)
    with pytest.raises(ValueError, match="review"):
        build_invoice(ex, INVOICER, "C-2", date(2026, 11, 10))


def test_date_is_retained_when_currency_split_creates_independent_drafts():
    ex = RulesExtractor().extract(SOURCE + "\n- 2 hours of design at EUR 50/hr\nDue: 2026-12-12")
    parts = split_by_currency(ex)
    assert len(parts) == 2
    assert all(p.due_date == "2026-12-12" and p.due_days is None for p in parts)
    assert all(build_invoice(p, INVOICER, str(i), date(2026, 11, 10))["detail"]["payment_term"]["due_date"] == "2026-12-12" for i,p in enumerate(parts))


def test_agent_draft_and_provider_refresh_keep_client_calendar_date():
    provider = SandboxMock()
    agent = Agent(provider, INVOICER, today=lambda: date(2026, 11, 10))
    result = agent.tool_create_invoice(SOURCE + "Due: 2026-12-12")
    assert result["ok"], result
    entry = next(iter(agent.ledger.values()))
    assert entry.invoice_due_on == date(2026, 12, 12)
    assert entry.due_days is None
    agent.tool_get_status(entry.invoice_id)
    assert entry.due_on == date(2026, 12, 12)


def test_human_calendar_review_and_legacy_net_terms_both_work():
    spec = importlib.util.spec_from_file_location("date_review", Path(__file__).resolve().parents[1] / "web-demo/python/review.py")
    review = importlib.util.module_from_spec(spec); spec.loader.exec_module(review)
    fields = {"client_name":"Mira","client_email":"mira@archive.example","due_days":"","due_date":"2026-12-12","amount_paid":"0","line_items":[{"desc":"Proofreading","qty":"3","unit_price":"80","currency":"USD","unit":"hours"}]}
    ex = review.prepare_review(fields)
    assert not ex.errors
    assert ex.due_date == "2026-12-12" and ex.due_days is None
    fields.pop("due_date"); fields["due_days"] = "21"
    legacy = review.prepare_review(fields)
    assert not legacy.errors
    assert legacy.due_date is None and legacy.due_days == 21

def test_observed_pay_by_request_does_not_shift_to_net_thirty():
    ex = RulesExtractor().extract("From: Elian <elian@example.test>\n- 4 hours of illustration at USD 122/hr\nPlease pay by 18 November 2026.")
    assert not ex.errors
    assert ex.total() == Decimal("488")
    body = build_invoice(ex, INVOICER, "PAY-BY-1", date(2026, 11, 10))
    assert body["detail"]["payment_term"]["due_date"] == "2026-11-18"
    assert ex.due_days is None
