"""Cross-process operator reminder flow against actual Ledgerly agent and mock."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ledgerly.agent import Agent
from ledgerly.followup import _lock_state, follow_up
from ledgerly.paypal import PayPalError, SandboxMock

FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures/01_simple_usd_hourly.txt").read_text()
OCT_1 = date(2026, 10, 1)
NOV_9 = date(2026, 11, 9)


def seeded_provider(cls=SandboxMock):
    provider = cls(now=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    agent = Agent(provider, {"name": "Freelancer", "email_address": "studio@example.com"},
                  today=lambda: OCT_1)
    result = agent.tool_create_invoice(FIXTURE)
    assert result["ok"]
    invoice_id = result["invoices"][0]["invoice_id"]
    agent.approve(result["invoices"][0]["approval_id"])
    assert provider.get_invoice(invoice_id).body["status"] == "SENT"
    return provider, invoice_id


def request_count(provider, fragment):
    return sum(fragment in path for _, path, _ in provider.requests)


def test_resume_reads_real_provider_invoice_from_new_agent():
    provider, inv = seeded_provider()
    lines = []
    assert follow_up(inv, client=provider, today=NOV_9, write=lines.append) == 0
    assert any("maya.chen@brightfern.example" in line for line in lines)
    assert any("balance 1320" in line for line in lines)
    assert any("Due: 2026-10-16" in line for line in lines)
    assert request_count(provider, "/remind") == 0


def test_exact_confirm_creates_one_real_provider_reminder_and_durable_cooldown(tmp_path):
    provider, inv = seeded_provider()
    path = tmp_path / "state.json"
    prompts = []
    def approve(prompt):
        prompts.append(prompt)
        return "REMIND " + inv

    lines = []
    assert follow_up(inv, state_path=path, remind=True, client=provider, today=NOV_9,
                     ask=approve, write=lines.append) == 0
    assert request_count(provider, "/remind") == 1
    assert any("SUBJECT:" in line for line in lines)
    assert any("MESSAGE:" in line for line in lines)
    assert len(prompts) == 1 and inv in prompts[0]
    assert json.loads(path.read_text())["invoices"][inv] == {
        "last_reminder_on": "2026-11-09", "reminders_sent": 1}
    assert not path.stat().st_mode & 0o077

    # New call, fresh Agent and same provider; a calendar-day cooldown survives.
    lines.clear()
    assert follow_up(inv, state_path=path, remind=True, client=provider, today=NOV_9,
                     ask=lambda _: pytest.fail("Should not ask for duplicate reminder"),
                     write=lines.append) == 0
    assert request_count(provider, "/remind") == 1
    assert any("cooling down" in line for line in lines)


def test_declined_send_preserves_no_effect_and_no_cooldown(tmp_path):
    provider, inv = seeded_provider()
    path = tmp_path / "state.json"
    lines = []
    assert follow_up(inv, state_path=path, remind=True, client=provider,
                     today=NOV_9, ask=lambda _: "REMIND someone-else",
                     write=lines.append) == 0
    assert request_count(provider, "/remind") == 0
    assert not path.exists()
    assert any("unsent" in line for line in lines)


def test_lost_response_blocks_repeat_until_evidence_based_resolution(tmp_path):
    class LostResponseProvider(SandboxMock):
        def remind_invoice(self, invoice_id, body=None):
            super().remind_invoice(invoice_id, body)
            raise RuntimeError("Response disappeared after PayPal acted")

    provider, inv = seeded_provider(LostResponseProvider)
    path = tmp_path / "state.json"
    lines = []
    assert follow_up(inv, state_path=path, remind=True, client=provider,
                     today=NOV_9, ask=lambda _: "REMIND " + inv,
                     write=lines.append) == 4
    assert request_count(provider, "/remind") == 1
    saved = json.loads(path.read_text())["invoices"][inv]
    assert saved["uncertain"] == {"attempted_on": "2026-11-09"}
    lines.clear()
    assert follow_up(inv, state_path=path, remind=True, client=provider, today=NOV_9,
                     ask=lambda _: pytest.fail("Must not prompt to resend"), write=lines.append) == 3
    assert request_count(provider, "/remind") == 1
    assert any("previous reminder may have been sent" in line for line in lines)

    # Human investigates PayPal and records a sent outcome, without re-sending.
    assert follow_up(inv, state_path=path, resolve="sent", client=provider, today=NOV_9,
                     ask=lambda _: "RESOLVE " + inv + " SENT",
                     write=lambda _: None) == 0
    assert request_count(provider, "/remind") == 1
    saved = json.loads(path.read_text())["invoices"][inv]
    assert saved["last_reminder_on"] == "2026-11-09"
    assert "uncertain" not in saved


def test_reconcile_not_sent_explicitly_allows_later_new_attempt(tmp_path):
    provider, inv = seeded_provider()
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "schema": "ledgerly.followup.v1",
        "invoices": {inv: {"uncertain": {"attempted_on": "2026-11-08"}}}}))
    assert follow_up(inv, state_path=path, resolve="not-sent", client=provider,
                     today=NOV_9, ask=lambda _: "RESOLVE " + inv + " NOT_SENT") == 0
    assert "uncertain" not in json.loads(path.read_text())["invoices"][inv]
    assert request_count(provider, "/remind") == 0


def test_refuse_paid_invoice_and_missing_due_date(tmp_path):
    provider, inv = seeded_provider()
    provider.simulate_payer_payment(inv)
    lines = []
    assert follow_up(inv, state_path=tmp_path/"state.json", client=provider,
                     today=NOV_9, remind=True, write=lines.append,
                     ask=lambda _: pytest.fail("Paid invoice must not prompt")) == 0
    assert request_count(provider, "/remind") == 0
    assert any("no reminder needed" in line for line in lines)


def test_no_reminder_before_due_even_when_user_requests_one(tmp_path):
    provider, inv = seeded_provider()
    lines = []
    assert follow_up(inv, state_path=tmp_path/"state.json", client=provider, today=OCT_1,
                     remind=True, write=lines.append,
                     ask=lambda _: pytest.fail("No reminder before due")) == 0
    assert any("not overdue" in line for line in lines)
    assert request_count(provider, "/remind") == 0


def test_no_provider_calls_on_invalid_identity_or_missing_durable_state():
    provider = SandboxMock()
    with pytest.raises(ValueError, match="identifier"):
        follow_up("../bad-id", client=provider)
    with pytest.raises(ValueError, match="state-file"):
        follow_up("INV2-EXAMPLE", client=provider, remind=True)
    assert provider.requests == []


def test_invalid_state_never_opens_send(tmp_path):
    provider, inv = seeded_provider()
    path = tmp_path / "state.json"
    path.write_text('{"schema":"not-ledgerly","invoices":{}}')
    with pytest.raises(ValueError, match="Unsupported"):
        follow_up(inv, client=provider, state_path=path, remind=True)
    assert request_count(provider, "/remind") == 0


def test_concurrent_followup_cannot_bypass_cooldown_file(tmp_path):
    provider, inv = seeded_provider()
    path = tmp_path / "state.json"
    with _lock_state(path):
        with pytest.raises(RuntimeError, match="Another Ledgerly"):
            follow_up(inv, state_path=path, remind=True, client=provider, today=NOV_9)
    assert request_count(provider, "/remind") == 0


def test_provider_read_failure_is_not_interpreted_as_sent(tmp_path):
    provider, inv = seeded_provider()
    def fail(invoice_id):
        raise PayPalError(503, "SERVICE_UNAVAILABLE", "provider unavailable")
    provider.get_invoice = fail
    with pytest.raises(PayPalError):
        follow_up(inv, state_path=tmp_path/"state.json", remind=True, client=provider, today=NOV_9)
    assert request_count(provider, "/remind") == 0
