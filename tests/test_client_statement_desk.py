"""Actual shared bridge + durable operator account export; no provider network."""
from datetime import timedelta
from pathlib import Path

import pytest

from ledgerly.invoice_desk import NativeSession, load_browser_bridge
from ledgerly.paypal import SandboxMock
from ledgerly.recovery import DurableAgent

ROOT = Path(__file__).resolve().parents[1]
MERCHANT = {"name": "Statement test studio", "email_address": "studio@example.test"}


def add_invoice(demo, email, currency, rate):
    source = f"From: Arlen <{email}>\nPlease invoice 2 hours of consulting at {currency} {rate} per hour. Net 14."
    reply = demo.dispatch({"action": "draft", "text": source})
    action = reply["state"]["pending"][-1]
    demo.dispatch({"action": "approve", "action_id": action["id"]})
    return action["invoice_id"]


def test_browser_statement_is_full_client_read_only_and_uses_demo_date():
    demo = load_browser_bridge(ROOT / "web-demo/python").Demo()
    first = add_invoice(demo, "arlen@example.test", "USD", "75")
    add_invoice(demo, "ARLEN@example.test", "EUR", "50")
    add_invoice(demo, "private-other@example.test", "USD", "999")
    demo.dispatch({"action": "payment", "invoice_id": first, "amount": "40"})
    demo.clock.day += timedelta(days=20)
    before = demo.snapshot()
    reply = demo.dispatch({"action": "client_statement", "invoice_id": first})
    result = reply["result"]
    assert result["record_count"] == 2
    assert result["client_email"] == "arlen@example.test"
    assert "Simulation" in result["html"]
    assert "Sandbox" not in result["html"]
    assert 'data-outstanding="USD">110.00' in result["html"]
    assert 'data-outstanding="EUR">100.00' in result["html"]
    assert "6 days overdue" in result["html"]
    assert f"As of {demo.clock.day.strftime('%d %B %Y')}" in result["html"]
    assert "private-other" not in result["html"] and "1,998" not in result["html"]
    assert demo.snapshot() == before


def test_native_statement_survives_process_restart_without_refresh(tmp_path):
    state = tmp_path / "account.json"
    provider = SandboxMock()
    agent = DurableAgent(provider, MERCHANT, state_file=state)
    created = agent.tool_create_invoice("From: Iris <iris@example.test>\nPlease invoice 3 hours of editing at USD 80 per hour. Net 14.")
    assert created["ok"]
    action = agent.list_pending()[0]
    agent.approve(action["id"], approver="synthetic fixture")
    invoice_id = action["invoice_id"]
    # Reopen the actual checkpoint in a fresh operator, after the original send.
    restored = DurableAgent(provider, MERCHANT, state_file=state)
    desk = NativeSession(restored, load_browser_bridge(ROOT / "web-demo/python"),
                         provider="Synthetic provider test")
    before = state.read_bytes(), list(provider.requests), desk.snapshot()
    reply = desk.dispatch({"action": "client_statement", "invoice_id": invoice_id}, "fresh-browser")
    assert reply["ok"], reply
    document = reply["result"]["html"]
    assert "Test data" in document and "Simulation" not in document
    assert 'data-outstanding="USD">240.00' in document
    assert state.read_bytes() == before[0] and provider.requests == before[1]
    assert desk.snapshot() == before[2]
    missing = desk.dispatch({"action": "client_statement", "invoice_id": "not-in-this-ledger"}, "fresh-browser")
    assert not missing["ok"] and "Choose an invoice" in missing["message"]
    assert state.read_bytes() == before[0] and provider.requests == before[1]

@pytest.mark.parametrize("cancelled_remote", [False, True])
def test_lost_cancellation_response_is_not_collectible_after_restart(tmp_path, monkeypatch, cancelled_remote):
    state = tmp_path / "account.json"
    provider = SandboxMock()
    agent = DurableAgent(provider, MERCHANT, state_file=state)
    agent.tool_create_invoice("From: Iris <iris@example.test>\nPlease invoice 3 hours of editing at USD 80 per hour. Net 14.")
    send = agent.list_pending()[0]
    agent.approve(send["id"], approver="synthetic fixture")
    invoice_id = send["invoice_id"]
    cancel = agent.prepare_cancellation(invoice_id, "Client replaced this agreement")
    actual_cancel = provider.cancel_invoice

    def lost_response(*args):
        if cancelled_remote:
            actual_cancel(*args)
        raise ConnectionError("Cancellation response was lost")

    monkeypatch.setattr(provider, "cancel_invoice", lost_response)
    with pytest.raises(ConnectionError, match="response was lost"):
        agent.approve(cancel["approval_id"], approver="synthetic fixture")
    assert agent.ledger[invoice_id].status == "SENT"
    assert provider.invoices[invoice_id]["status"] == ("CANCELLED" if cancelled_remote else "SENT")

    # Reopen from disk with a new provider that knows nothing about this invoice.
    # Export must preserve uncertainty, not use a provider refresh to mask it.
    offline = SandboxMock()
    restored = DurableAgent(offline, MERCHANT, state_file=state)
    desk = NativeSession(restored, load_browser_bridge(ROOT / "web-demo/python"),
                         provider="Synthetic provider test")
    before = state.read_bytes()
    reply = desk.dispatch({"action": "client_statement", "invoice_id": invoice_id}, "fresh-browser")
    assert reply["ok"], reply
    document = reply["result"]["html"]
    assert 'data-outstanding="USD">0.00' in document
    assert "Cancellation unconfirmed" in document
    assert "Reminder unconfirmed" not in document
    assert "<dt>Review · excluded</dt><dd>1</dd>" in document
    assert state.read_bytes() == before and offline.requests == []
