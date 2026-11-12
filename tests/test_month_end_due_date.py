"""Observed month-end email deadline through shared validation and actual drafts."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from ledgerly.agent import Agent
from ledgerly.extract import RulesExtractor, validate
from ledgerly.paypal import SandboxMock, build_invoice
from ledgerly.recovery import DurableAgent

SOURCE = "From: Anika Reed <anika@example.test>\nPlease invoice 3 hours of editing at USD 80 per hour. "
MERCHANT = {"name": "Calendar test studio", "email_address": "studio@example.test"}


@pytest.mark.parametrize("wording,expected", [
    ("Payment due at the end of November 2026.", "2026-11-30"),
    ("Please pay by the end of February 2028.", "2028-02-29"),
    ("Due: end of Feb. 2027.", "2027-02-28"),
    ("Due by the end of December 2026.", "2026-12-31"),
])
def test_explicit_month_end_builds_the_actual_last_day(wording, expected):
    ex = RulesExtractor().extract(SOURCE + wording)
    assert not ex.errors, ex.issues
    assert ex.due_date == expected and ex.due_days is None
    assert ex.total() == Decimal("240")
    assert not any("defaulted" in issue.message for issue in ex.issues)
    for issued in [date(2026, 11, 10), date(2026, 11, 19)]:
        assert build_invoice(ex, MERCHANT, "MONTH-1", issued)["detail"]["payment_term"] == {
            "term_type": "DUE_ON_DATE_SPECIFIED", "due_date": expected}


def test_observed_deadline_survives_durable_draft_restart_and_later_approval(tmp_path):
    day = date(2026, 11, 10)
    provider = SandboxMock()
    path = tmp_path / "private.json"
    source = (Path(__file__).parents[1] / "fixtures/regressions/month-end-due-date.txt").read_text()
    agent = DurableAgent(provider, MERCHANT, state_file=path, today=lambda: day)
    result = agent.tool_create_invoice(source)
    assert result["ok"], result
    drafted = result["invoices"][0]
    assert agent.ledger[drafted["invoice_id"]].invoice_due_on == date(2026, 11, 30)
    restored = DurableAgent(provider, MERCHANT, state_file=path, today=lambda: day)
    day = date(2026, 11, 18)
    restored.approve(drafted["approval_id"], approver="fictional course")
    row = restored.ledger[drafted["invoice_id"]]
    assert row.due_on == date(2026, 11, 30) and row.due_days is None
    assert provider.invoices[row.invoice_id]["detail"]["payment_term"]["due_date"] == "2026-11-30"


@pytest.mark.parametrize("terms", [
    "Payment due at month end.", "Please pay by the end of November.",
    "Due: EOM.", "Due at the end of Notamonth 2026.",
    "Due at the end of November 2026. Net 14.",
    "Due at the end of November 2026. Due: 2026-12-01.",
])
def test_incomplete_or_conflicting_month_end_blocks_default_drafting(terms):
    ex = RulesExtractor().extract(SOURCE + terms)
    assert any(i.field == "due_date" for i in ex.errors)
    provider = SandboxMock()
    result = Agent(provider, MERCHANT).tool_create_invoice(SOURCE + terms)
    assert not result["ok"] and provider.invoices == {}
    if ex.due_date is None:
        assert ex.due_days is None
        assert not any("defaulted" in i.message for i in ex.issues)


def test_same_calendar_date_and_month_end_are_one_term():
    ex = RulesExtractor().extract(SOURCE + "Due at the end of November 2026. Due: 2026-11-30.")
    assert not ex.errors and ex.due_date == "2026-11-30"


def test_quoted_or_work_description_month_end_does_not_change_current_terms():
    text = SOURCE + "Net 7.\n> Payment due at month end."
    ex = RulesExtractor().extract(text)
    assert not ex.errors and ex.due_days == 7 and ex.due_date is None
    work = RulesExtractor().extract(
        "From: Anika Reed <anika@example.test>\nMonth-end report — USD 240. Net 7.")
    assert not work.errors and work.due_days == 7 and work.due_date is None


def test_shared_validation_rejects_model_fallback_that_ignores_explicit_month_end():
    source = SOURCE + "Payment due at the end of November 2026."
    ex = RulesExtractor().extract(source)
    ex.due_date, ex.due_days = None, 30
    assert any(i.field == "due_date" and i.severity == "error" for i in validate(ex, source))
    ex.due_date, ex.due_days = "2026-11-30", None
    assert not validate(ex, source)
