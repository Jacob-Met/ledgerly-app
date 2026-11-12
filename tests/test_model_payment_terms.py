"""Regressions for real AI invoice review: deadlines and explicit currency."""
from __future__ import annotations

import argparse
import pytest
from datetime import date
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from ledgerly.extract import LLMExtractor
from ledgerly.live import _model_value_issues, run
from ledgerly.paypal import SandboxMock

SOURCE = (
    "From: Example Client <client@example.com>\n"
    "Please invoice billing@example.com for:\n"
    "- 2 x poster concepts @ $75 each\n"
    "Net 15 days.\n"
)
MODEL = {
    "client_name": "Example Client",
    "client_email": "billing@example.com",
    "currency": "USD",
    "line_items": [{"desc": "poster concepts", "qty": 2,
                    "unit_price": 75, "currency": "USD"}],
    "due_days": 15,
    "amount_paid": 0,
    "confidence": 0.96,
    "issues": [],
}


def ex(values, source=SOURCE):
    return LLMExtractor(lambda _: json.dumps(values)).extract(source)


def issues(values, source=SOURCE):
    return _model_value_issues(ex(values, source), source)


def test_invented_net_term_rejected_even_if_every_line_item_is_correct():
    assert any(issue.field == "due_days" and issue.severity == "error"
               for issue in issues({**MODEL, "due_days": 45}))
    assert not any(issue.field == "due_days" for issue in issues(MODEL))


def test_model_deadline_without_source_evidence_rejected():
    source = SOURCE.replace("Net 15 days.\n", "")
    assert any(issue.field == "due_days" for issue in issues(MODEL, source))


def test_explicit_currency_wins_even_when_the_model_keeps_a_dollar_sign():
    source = SOURCE + "All prices in CAD.\n"
    assert any(issue.field == "currency" and issue.severity == "error"
               for issue in issues(MODEL, source))


def test_operator_rejects_invented_due_date_before_creating_any_invoice():
    lied = {**MODEL, "due_days": 45}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "job.txt"
        path.write_text(SOURCE)
        client = SandboxMock()
        output = []
        args = argparse.Namespace(
            source=str(path), mode="preview", extractor="ai",
            ai_url="https://example.com/v1/chat/completions",
            ai_model="test", ai_key_env="LEDGERLY_AI_API_KEY",
            ai_threshold=0.75, invoicer_name="", invoicer_email="",
            send_after_review=False,
        )
        with patch("ledgerly.live.OpenAICompatibleCompleter", return_value=lambda _: json.dumps(lied)):
            result = run(args, client=client, emit=output.append,
                         confirmation=lambda _: "SHARE-WITH-MODEL")
        assert result == 2
        assert client.requests == []
        assert any("due_days" in line for line in output)
        assert any("REVIEW REQUIRED" in line for line in output)


@pytest.mark.parametrize(("term", "days"), [
    ("Payment is due 14 days after the invoice date.", 14),
    ("Payable 21 days from invoice.", 21),
])
def test_live_model_check_accepts_the_shared_invoice_relative_term(term, days):
    source = SOURCE.replace("Net 15 days.", term)
    assert not any(issue.field == "due_days"
                   for issue in issues({**MODEL, "due_days": days}, source))
    assert any(issue.field == "due_days" and issue.severity == "error"
               for issue in issues({**MODEL, "due_days": 30}, source))


@pytest.mark.parametrize("term", [
    "Payment is due 14 business days after the invoice date.",
    "Payment is due 14 days after invoice approval.",
    "Payment is due 14 days after the invoice is received.",
    "Payment is due 14 days after the invoice date if approved.",
])
def test_live_model_check_keeps_other_invoice_relative_conditions_blocked(term):
    source = SOURCE.replace("Net 15 days.", term)
    assert any(issue.field == "due_days" and issue.severity == "error"
               for issue in issues({**MODEL, "due_days": 14}, source))


def test_live_model_entry_preserves_invoice_relative_term_through_unsent_creation():
    source = SOURCE.replace("Net 15 days.", "Payment is due 14 days after the invoice date.")
    values = {**MODEL, "due_days": 14}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "job.txt"
        path.write_text(source)
        client = SandboxMock()
        output = []
        args = argparse.Namespace(
            source=str(path), mode="preview", extractor="ai",
            ai_url="https://example.com/v1/chat/completions",
            ai_model="test", ai_key_env="LEDGERLY_AI_API_KEY",
            ai_threshold=0.75, invoicer_name="", invoicer_email="",
            send_after_review=False,
        )
        with patch("ledgerly.live.OpenAICompatibleCompleter",
                   return_value=lambda _: json.dumps(values)):
            result = run(args, client=client, emit=output.append,
                         confirmation=lambda _: "SHARE-WITH-MODEL")
        assert result == 0
        assert len(client.invoices) == 1
        detail = next(iter(client.invoices.values()))["detail"]
        assert (date.fromisoformat(detail["payment_term"]["due_date"])
                - date.fromisoformat(detail["invoice_date"])).days == 14
        assert not [request for request in client.requests
                    if request[1].endswith(("/send", "/payments"))]
