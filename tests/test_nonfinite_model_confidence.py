"""Regression: actual malformed model confidence previously produced a provider draft."""
from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from ledgerly.agent import Agent
from ledgerly.extract import LLMExtractor, RulesExtractor
from ledgerly.live import _review, run
from ledgerly.paypal import SandboxMock

SOURCE = (Path(__file__).resolve().parents[1] / "fixtures" / "01_simple_usd_hourly.txt").read_text()


def model_extractor(confidence):
    payload = RulesExtractor().extract(SOURCE).to_dict()
    payload["confidence"] = confidence
    return LLMExtractor(lambda _prompt: json.dumps(payload))


@pytest.mark.parametrize("nonfinite", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_nonfinite_model_confidence_never_creates_draft(nonfinite):
    extractor = model_extractor(nonfinite)
    ex = extractor.extract(SOURCE)
    assert ex.errors, (nonfinite, ex)
    assert ex.confidence == 0
    assert any("confidence" in issue.message for issue in ex.errors)
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Fictional Operator", "email_address": "test@ledgerly.example"},
                  extractor=extractor)
    result = agent.tool_create_invoice(SOURCE)
    assert not result["ok"]
    assert result["needs_review"] is True
    assert len(mock.invoices) == 0
    assert mock.requests == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 1.5, -0.5, None])
def test_agent_refuses_invalid_confidence_from_any_extractor(bad):
    extraction = RulesExtractor().extract(SOURCE)
    extraction.confidence = bad

    class Stub:
        def extract(self, _source):
            return extraction

    mock = SandboxMock()
    agent = Agent(mock, {"name": "Test", "email_address": "test@ledgerly.example"},
                  extractor=Stub())
    result = agent.tool_create_invoice(SOURCE)
    assert not result["ok"]
    assert not mock.requests
    assert len(mock.invoices) == 0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 2.0, None])
def test_pilot_preview_refuses_invalid_confidence(bad):
    extraction = RulesExtractor().extract(SOURCE)
    extraction.confidence = bad
    lines = []
    assert _review(extraction, SOURCE, emit=lines.append) is False
    assert any("REVIEW REQUIRED" in line for line in lines)


def test_valid_model_confidence_still_creates_reviewable_draft():
    model = model_extractor(0.92)
    ex = model.extract(SOURCE)
    assert ex.confidence == pytest.approx(0.92)
    assert not ex.errors
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Test", "email_address": "test@ledgerly.example"},
                  extractor=model)
    result = agent.tool_create_invoice(SOURCE)
    assert result["ok"]
    assert len(result["invoices"]) == 1
    assert next(iter(mock.invoices.values()))["status"] == "DRAFT"
