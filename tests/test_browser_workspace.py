"""Real Agent/Mock state survives a new browser-engine instance without replay."""
import copy
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web-demo" / "python"))
import bridge
from workspace import dump_session_json, restore_session_json, preview_session_json


def make_session():
    demo = bridge.Demo()
    text = (ROOT / "fixtures" / "01_simple_usd_hourly.txt").read_text()
    demo.dispatch({"action": "draft", "text": text})
    return demo


def reopen(demo):
    restored = bridge.Demo()
    restore_session_json(restored, dump_session_json(demo))
    return restored


def test_reopen_keeps_pending_send_without_authorizing_it():
    original = make_session()
    restored = reopen(original)
    assert restored.snapshot() == original.snapshot()
    pending = restored.agent.list_pending()
    assert len(pending) == 1 and pending[0]["kind"] == "send_invoice"
    assert all(e.status == "DRAFT" for e in restored.agent.ledger.values())
    assert restored.mock.requests == [list(x) for x in original.mock.requests]
    restored.dispatch({"action": "approve", "action_id": pending[0]["id"]})
    assert all(e.status == "SENT" for e in restored.agent.ledger.values())


def test_partial_payment_reopen_and_duplicate_webhook_are_exact():
    original = make_session()
    original.dispatch({"action": "approve", "action_id": original.agent.list_pending()[0]["id"]})
    invoice = next(iter(original.agent.ledger))
    original.dispatch({"action": "payment", "invoice_id": invoice, "amount": "123.45"})
    restored = reopen(original)
    before = restored.snapshot()
    response = restored.dispatch({"action": "replay"})
    assert response["result"]["duplicate"] is True
    assert restored.agent.ledger[invoice].to_dict()["paid_amount"] == "123.45"
    assert restored.snapshot()["ledger"] == before["ledger"]
    assert restored.snapshot()["invoice_details"] == before["invoice_details"]


def test_new_invoice_after_reopen_does_not_replace_existing_invoice():
    first = make_session()
    old = copy.deepcopy(first.snapshot()["invoice_details"])
    restored = reopen(first)
    restored.dispatch({"action": "draft", "text": (ROOT / "fixtures" / "01_simple_usd_hourly.txt").read_text()})
    assert len(restored.agent.ledger) == 2
    assert restored.snapshot()["invoice_details"][:1] == old
    assert len({e.invoice_number for e in restored.agent.ledger.values()}) == 2


@pytest.mark.parametrize("corrupt", [
    lambda r: r.update(version=2),
    lambda r: r["mock"].update(invoices={}),
    lambda r: r["agent"]["ledger"][0].update(total="NaN"),
    lambda r: r["mock"].update(sequence=-1),
])
def test_incomplete_or_incompatible_saved_state_leaves_current_session_intact(corrupt):
    current = make_session()
    before = dump_session_json(current)
    candidate = json.loads(before)
    corrupt(candidate)
    with pytest.raises((ValueError, TypeError)):
        restore_session_json(current, json.dumps(candidate))
    assert dump_session_json(current) == before


def test_reset_and_review_authorizations_are_not_restored():
    original = make_session()
    original.review = ("consumed-on-reopen", "old source", object())
    restored = reopen(original)
    assert restored.review is None and restored.analysis is None
    restored.dispatch({"action": "reset"})
    assert reopen(restored).snapshot()["ledger"] == []


def test_file_preview_uses_actual_restored_state_without_changing_current_work_or_review():
    current = make_session()
    checked = ("keep-current-review", "current-source", object())
    current.review = checked
    before = dump_session_json(current)
    incoming = make_session()
    incoming.dispatch({"action": "approve", "action_id": incoming.agent.list_pending()[0]["id"]})
    incoming.dispatch({"action": "payment", "invoice_id": next(iter(incoming.agent.ledger)), "amount": "123.45"})
    preview = json.loads(preview_session_json(current, dump_session_json(incoming)))
    assert preview["ledger"][0]["paid_amount"] == "123.45"
    assert current.review is checked
    assert dump_session_json(current) == before

    damaged = json.loads(dump_session_json(incoming))
    damaged["mock"]["invoices"] = {}
    with pytest.raises(ValueError):
        preview_session_json(current, json.dumps(damaged))
    assert current.review is checked
    assert dump_session_json(current) == before
