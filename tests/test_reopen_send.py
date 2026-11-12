import pytest
from conftest import load


def declined(agent):
    result = agent.tool_create_invoice(load("01_simple_usd_hourly.txt"))
    row = result["invoices"][0]
    agent.reject(row["approval_id"], "Client asked to wait")
    return row


def test_reopen_retains_invoice_and_rejection(agent, mock):
    row = declined(agent)
    before = len(mock.invoices)
    fresh = agent.reopen_send_review(row["approval_id"])
    assert fresh["invoice_id"] == row["invoice_id"]
    assert fresh["approval_id"] != row["approval_id"]
    assert len(mock.invoices) == before
    assert agent.pending[row["approval_id"]].result["reason"] == "Client asked to wait"
    assert not any(r[1].endswith("/send") for r in mock.requests)
    with pytest.raises(ValueError):
        agent.reopen_send_review(row["approval_id"])
    agent.approve(fresh["approval_id"])
    assert len([r for r in mock.requests if r[1].endswith("/send")]) == 1


def test_changed_draft_cannot_reopen(agent, mock):
    row = declined(agent)
    mock.invoices[row["invoice_id"]]["items"][0]["name"] = "Changed work"
    with pytest.raises(ValueError, match="changed"):
        agent.reopen_send_review(row["approval_id"])
    assert not agent.list_pending()


def test_failed_send_cannot_be_bypassed_with_old_rejection(agent):
    row = declined(agent)
    fresh = agent.reopen_send_review(row["approval_id"])
    agent.pending[fresh["approval_id"]].status = "FAILED"
    with pytest.raises(ValueError, match="attempted"):
        agent.reopen_send_review(row["approval_id"])
