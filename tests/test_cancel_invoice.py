import pytest
from decimal import Decimal
from conftest import load
from ledgerly.agent import ApprovalRequired


def sent(agent):
    row = agent.tool_create_invoice(load("01_simple_usd_hourly.txt"))["invoices"][0]
    agent.approve(row["approval_id"])
    return row["invoice_id"]


def test_cancel_is_separate_gated_decision(agent, mock):
    identity = sent(agent)
    with pytest.raises(ApprovalRequired):
        agent.client.cancel_invoice(identity, {})
    review = agent.prepare_cancellation(identity, "Wrong client agreement")
    assert agent.ledger[identity].status == "SENT"
    assert not any(r[1].endswith("/cancel") for r in mock.requests)
    result = agent.approve(review["approval_id"])
    assert result["cancelled"]
    assert agent.ledger[identity].status == "CANCELLED"
    assert agent.tool_list_overdue()["overdue"] == []
    assert mock.invoices[identity]["status"] == "CANCELLED"
    assert len([r for r in mock.requests if r[1].endswith("/cancel")]) == 1
    with pytest.raises(ValueError):
        agent.approve(review["approval_id"])


def test_payment_after_review_blocks_cancel(agent, mock):
    identity = sent(agent)
    review = agent.prepare_cancellation(identity, "Duplicate agreement")
    mock.simulate_payer_payment(identity, Decimal("10"))
    with pytest.raises(ValueError, match="changed"):
        agent.approve(review["approval_id"])
    assert agent.pending[review["approval_id"]].status == "REJECTED"
    assert not any(r[1].endswith("/cancel") for r in mock.requests)


def test_unknown_cancel_cannot_be_replayed(agent, mock, monkeypatch):
    identity = sent(agent)
    review = agent.prepare_cancellation(identity, "Duplicate agreement")
    def lost(*args):
        raise ConnectionError("lost response")
    monkeypatch.setattr(mock, "cancel_invoice", lost)
    with pytest.raises(ConnectionError):
        agent.approve(review["approval_id"])
    assert agent.pending[review["approval_id"]].status == "FAILED"
    with pytest.raises(ValueError, match="already exists"):
        agent.prepare_cancellation(identity, "Retry")


def test_reject_cancel_leaves_invoice_collectible(agent):
    identity = sent(agent)
    review = agent.prepare_cancellation(identity, "Client correction")
    agent.reject(review["approval_id"], "Keep the original")
    assert agent.ledger[identity].status == "SENT"
    assert agent.prepare_cancellation(identity, "Agreed correction")["approval_id"] != review["approval_id"]

def test_cancellation_survives_native_restart(mock, tmp_path):
    from conftest import INVOICER
    from ledgerly.recovery import DurableAgent
    path = tmp_path / "state.json"
    agent = DurableAgent(mock, INVOICER, state_file=path)
    identity = sent(agent)
    review = agent.prepare_cancellation(identity, "Wrong agreement")
    resumed = DurableAgent(mock, INVOICER, state_file=path)
    assert resumed.pending[review["approval_id"]].status == "PENDING"
    resumed.approve(review["approval_id"])
    final = DurableAgent(mock, INVOICER, state_file=path)
    assert final.ledger[identity].status == "CANCELLED"
    assert final.pending[review["approval_id"]].result["reason"] == "Wrong agreement"

def test_native_cancel_requires_invoice_specific_confirmation_and_restart(tmp_path):
    from test_invoice_desk import open_desk, create
    from ledgerly.paypal import SandboxMock
    provider = SandboxMock()
    path = tmp_path / "native.json"
    desk = open_desk(path, provider)
    create(desk)
    send = desk.snapshot()["pending"][0]
    assert desk.dispatch({"action": "approve", "action_id": send["id"],
                          "confirmation": send["confirmation"]}, "one")["ok"]
    identity = send["invoice_id"]
    prepared = desk.dispatch({"action": "prepare_cancellation", "invoice_id": identity,
                              "reason": "Purchase order replaced"}, "one")
    assert prepared["ok"], prepared
    desk = open_desk(path, provider)
    cancel = desk.snapshot()["pending"][0]
    assert cancel["confirmation"] == "CANCEL " + identity
    wrong = desk.dispatch({"action": "approve", "action_id": cancel["id"],
                           "confirmation": "CANCEL WRONG"}, "two")
    assert not wrong["ok"]
    assert provider.invoices[identity]["status"] == "SENT"
    approved = desk.dispatch({"action": "approve", "action_id": cancel["id"],
                              "confirmation": cancel["confirmation"]}, "two")
    assert approved["ok"], approved
    reopened = open_desk(path, provider)
    assert reopened.snapshot()["ledger"][0]["status"] == "CANCELLED"
    assert not reopened.snapshot()["pending"]
