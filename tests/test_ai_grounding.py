"""Provider-backed AI extraction must prove the price belongs to the billed work.

These are new failure inputs independent from the original 12 invoice fixtures.
"""
import json
from decimal import Decimal
from pathlib import Path

from ledgerly.agent import Agent
from ledgerly.extract import LLMExtractor
from ledgerly.paypal import SandboxMock

ROOT = Path(__file__).resolve().parents[1]
INVOICER = {"name": "Example Freelancer", "email_address": "freelancer@example.test"}


def completion(*, prices=("85", "300"), confidence=0.95, extra=None):
    data = {
        "client_name": "Maya Chen",
        "client_email": "maya.chen@brightfern.example",
        "currency": "USD",
        "line_items": [
            {"desc": "Copywriting", "qty": 12, "unit_price": prices[0], "currency": "USD"},
            {"desc": "SEO keyword audit", "qty": 1, "unit_price": prices[1], "currency": "USD"},
        ],
        "due_days": 15,
        "amount_paid": "0",
        "confidence": confidence,
    }
    if extra:
        data.update(extra)
    return lambda prompt: json.dumps(data)


def test_source_grounded_model_prices_pass_and_include_review_evidence():
    source = (ROOT / "fixtures/01_simple_usd_hourly.txt").read_text()
    extraction = LLMExtractor(completion(), ground_prices=True).extract(source)
    assert extraction.total() == Decimal("1320")
    assert extraction.errors == []
    assert any(issue.field == "line_items[0].unit_price" and
               issue.severity == "info" and "source line" in issue.message
               for issue in extraction.issues)


def test_swapped_prices_do_not_leak_to_paypal_drafts():
    source = (ROOT / "fixtures/01_simple_usd_hourly.txt").read_text()
    emulator = SandboxMock()
    agent = Agent(emulator, INVOICER,
                  extractor=LLMExtractor(completion(prices=("300", "85")),
                                         ground_prices=True))
    result = agent.tool_create_invoice(source)
    assert result["needs_review"] and result["ok"] is False
    errors = [i for i in result["issues"] if i["severity"] == "error"]
    assert sum(i["field"].endswith(".unit_price") for i in errors) == 2
    assert emulator.requests == []
    assert agent.ledger == {} and agent.list_pending() == []


def test_summary_amount_does_not_justify_invented_line_price():
    source = ("From: Maya Chen <maya.chen@brightfern.example>\n\n"
              "- 2 x Copywriting @ $50\n"
              "Total $100\nNet 15\n")
    # The model substitutes the total as the price; $100 does occur in the source.
    data = completion(prices=("100", "300"), extra={
        "line_items": [{"desc": "Copywriting", "qty": 2,
                        "unit_price": "100", "currency": "USD"}],
    })
    ex = LLMExtractor(data, ground_prices=True).extract(source)
    assert any(issue.field == "line_items[0].unit_price" and issue.severity == "error"
               for issue in ex.issues)


def test_quoted_history_price_cannot_authorize_current_invoice():
    source = ("From: Maya Chen <maya.chen@brightfern.example>\n\n"
              "Please invoice 2 Copywriting revisions at $50 each.\n"
              "> Last month's Copywriting was $300, no longer applicable.\n"
              "Net 15\n")
    data = completion(prices=("300", "300"), extra={
        "line_items": [{"desc": "Copywriting", "qty": 2,
                        "unit_price": "300", "currency": "USD"}],
    })
    ex = LLMExtractor(data, ground_prices=True).extract(source)
    assert any(i.severity == "error" and i.field == "line_items[0].unit_price"
               for i in ex.issues)


def test_nonfinite_model_confidence_fails_closed_before_draft():
    source = (ROOT / "fixtures/01_simple_usd_hourly.txt").read_text()
    emulator = SandboxMock()
    agent = Agent(emulator, INVOICER,
                  extractor=LLMExtractor(completion(confidence=float("nan")),
                                         ground_prices=True))
    result = agent.tool_create_invoice(source)
    assert result["needs_review"] and result["ok"] is False
    assert emulator.requests == []
    assert any(i["field"] == "*" and i["severity"] == "error" for i in result["issues"])


def test_generic_existing_extractor_policy_still_accepts_structured_values():
    source = ("From: Client <client@example.test>\n"
              "- 2 x Work @ $10\nNet 30\n")
    data = {
        "client_name": "Client", "client_email": "client@example.test",
        "currency": "USD", "line_items": [
            {"desc": "Work", "qty": "0.5", "unit_price": "85.00", "currency": "USD"}
        ], "due_days": 30, "confidence": 0.99,
    }
    ex = LLMExtractor(lambda _: json.dumps(data)).extract(source)
    assert not ex.errors


def test_actual_live_extractor_wiring_rejects_swapped_model_values(monkeypatch):
    from types import SimpleNamespace
    from ledgerly.live import _make_extractor, OpenAICompatibleCompleter

    monkeypatch.setattr(OpenAICompatibleCompleter, "__call__",
                        lambda _self, _prompt: json.dumps({
                            "client_name": "Maya Chen", "client_email": "maya.chen@brightfern.example",
                            "currency": "USD",
                            "line_items": [
                                {"desc": "Copywriting", "qty": 12, "unit_price": "300"},
                                {"desc": "SEO keyword audit", "qty": 1, "unit_price": "85"},
                            ], "due_days": 15, "confidence": 0.98,
                        }))
    options = SimpleNamespace(extractor="ai",
                              ai_url="https://api.openai.com/v1/chat/completions",
                              ai_model="local-fixture", ai_key_env="LEDGERLY_TEST_AI_KEY",
                              ai_threshold=0.75)
    llm = _make_extractor(options)
    assert llm.ground_prices
    mock = SandboxMock()
    source = (ROOT / "fixtures/01_simple_usd_hourly.txt").read_text()
    result = Agent(mock, INVOICER, extractor=llm).tool_create_invoice(source)
    assert result["needs_review"] and mock.requests == []


def test_local_operator_fallback_enables_same_grounding():
    from ledgerly.operator import make_agent

    agent = make_agent(False, True, "fixture-model", "http://127.0.0.1:11434", {})
    assert agent.extractor.fallback.ground_prices is True


def test_same_quantity_swapped_prices_preserve_totals_but_are_not_valid_invoices():
    """Global amount counts and (quantity, price) multisets cannot detect this."""
    source = (
        "From: Maya Chen <maya.chen@brightfern.example>\n"
        "Please send the invoice with these two items:\n"
        "- 1 x Copywriting @ $100\n"
        "- 1 x SEO keyword audit @ $200\n"
        "Net 15\n"
    )
    wrong = {
        "client_name": "Maya Chen", "client_email": "maya.chen@brightfern.example",
        "currency": "USD", "line_items": [
            {"desc": "Copywriting", "qty": 1, "unit_price": "200", "currency": "USD"},
            {"desc": "SEO keyword audit", "qty": 1, "unit_price": "100", "currency": "USD"},
        ], "due_days": 15, "amount_paid": 0, "confidence": 0.99,
    }
    assert sum(Decimal(i["unit_price"]) for i in wrong["line_items"]) == Decimal("300")
    from ledgerly.live import _model_value_issues
    from ledgerly.extract import RulesExtractor
    legacy = LLMExtractor(lambda _: json.dumps(wrong)).extract(source)
    baseline = RulesExtractor().extract(source)
    assert baseline.confidence >= 0.95
    assert not legacy.errors
    # Existing global price gate sees both prices, counts and totals, but no item association.
    assert _model_value_issues(legacy, source) == []

    mock = SandboxMock()
    agent = Agent(mock, INVOICER,
                  extractor=LLMExtractor(lambda _: json.dumps(wrong), ground_prices=True))
    result = agent.tool_create_invoice(source)
    assert result["needs_review"] and not result["ok"]
    assert sum(i["field"].endswith(".unit_price") and i["severity"] == "error"
               for i in result["issues"]) == 2
    assert mock.requests == []

def test_invented_recipient_is_not_described_as_quoted_history():
    source = (ROOT / "fixtures/01_simple_usd_hourly.txt").read_text()
    invented = "leila.noor@example.com"
    ex = LLMExtractor(completion(extra={"client_email": invented}), ground_prices=True).extract(source)
    assert ex.client_email is None
    assert any("does not appear" in i.message for i in ex.errors)
    assert not any("only in quoted history" in i.message for i in ex.errors)
    history = source + "\n> Prior contact: " + invented
    quoted = LLMExtractor(completion(extra={"client_email": invented}), ground_prices=True).extract(history)
    assert any("only in quoted history" in i.message for i in quoted.errors)
