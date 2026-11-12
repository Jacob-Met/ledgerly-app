"""Original invoice wording stays private, identity-bound and durable."""
from copy import deepcopy
import json

from test_invoice_desk import open_desk, create, checked, SOURCE, DropCreatedResponse
from ledgerly.paypal import SandboxMock


def test_saved_request_survives_new_intake_and_process_restart(tmp_path):
    path = tmp_path / "state.json"
    provider = SandboxMock()
    desk = open_desk(path, provider)
    made = create(desk)
    identity = made["state"]["ledger"][0]["invoice_id"]
    other = SOURCE.replace("Ada Vale", "Bea Rowan").replace("ada@example.test", "bea@example.test")
    assert desk.dispatch({"action": "analyze_rules", "text": other}, "later")["ok"]
    assert made["state"]["invoice_requests"] == [identity]
    assert SOURCE not in json.dumps(desk.snapshot())
    calls = len(provider.requests)
    reply = desk.dispatch({"action": "invoice_request", "invoice_id": identity}, "later")
    assert reply["ok"] and reply["result"]["source_text"] == SOURCE
    assert desk.browser("later").analysis_text == other
    assert len(provider.requests) == calls
    reopened = open_desk(path, provider)
    assert reopened.dispatch({"action": "invoice_request", "invoice_id": identity}, "fresh")["result"]["source_text"] == SOURCE
    assert reopened.observed.calls == 0
    assert path.with_name(path.name + ".creations.json").stat().st_mode & 0o077 == 0


def test_missing_or_ambiguous_source_never_falls_back_to_current_intake(tmp_path):
    path = tmp_path / "state.json"
    provider = SandboxMock()
    desk = open_desk(path, provider)
    made = create(desk)
    identity = made["state"]["ledger"][0]["invoice_id"]
    assert not desk.dispatch({"action": "invoice_request", "invoice_id": "ANOTHER"}, "one")["ok"]
    row = next(iter(desk.journal.records.values()))
    copy = deepcopy(row);copy["id"] = "duplicate"
    desk.journal.records["duplicate"] = copy
    assert not desk.dispatch({"action": "invoice_request", "invoice_id": identity}, "one")["ok"]
    del desk.journal.records["duplicate"]
    del row["context"]["source_text"]
    desk.journal.save()
    reopened = open_desk(path, provider)
    checked(reopened, "later")
    assert reopened.snapshot()["invoice_requests"] == []
    assert not reopened.dispatch({"action": "invoice_request", "invoice_id": identity}, "later")["ok"]


def test_request_is_retained_before_unknown_creation_and_available_only_after_recovery(tmp_path):
    path = tmp_path / "state.json"
    provider = DropCreatedResponse()
    desk = open_desk(path, provider)
    assert not create(desk)["ok"]
    identity = next(iter(provider.invoices))
    assert desk.journal.source_for(identity) is None
    assert next(iter(desk.journal.records.values()))["context"]["source_text"] == SOURCE
    desk = open_desk(path, provider)
    attempt = desk.snapshot()["creation_attempts"][0]
    recovered = desk.dispatch({"action": "recover_creation", "attempt_id": attempt["id"], "invoice_id": identity}, "fresh")
    assert recovered["ok"]
    assert desk.dispatch({"action": "invoice_request", "invoice_id": identity}, "fresh")["result"]["source_text"] == SOURCE
    assert len(provider.invoices) == 1
    assert not [r for r in provider.requests if r[1].endswith("/send")]


def test_source_lookup_does_not_accept_unreadable_context(tmp_path):
    desk = open_desk(tmp_path / "state.json", SandboxMock())
    made = create(desk)
    identity = made["state"]["ledger"][0]["invoice_id"]
    row = next(iter(desk.journal.records.values()))
    for value in [None, [], "other"]:
        row["context"] = value
        assert desk.journal.source_for(identity) is None

def test_browser_workspace_preserves_only_its_own_invoice_requests():
    from test_browser_workspace import make_session, reopen
    original = make_session()
    identity = next(iter(original.agent.ledger))
    source = original.invoice_sources[identity]
    original.dispatch({"action": "analyze", "text": SOURCE})
    restored = reopen(original)
    assert restored.dispatch({"action": "invoice_request", "invoice_id": identity})["result"]["source_text"] == source
    assert restored.snapshot()["invoice_requests"] == [identity]
    assert source not in json.dumps(restored.snapshot())


def test_old_browser_workspace_has_no_invented_source():
    from test_browser_workspace import make_session
    from workspace import dump_session, restore_session
    original = make_session()
    record = dump_session(original)
    del record["invoice_requests"]
    restore_session(original, record)
    assert original.snapshot()["invoice_requests"] == []
    assert original.invoice_sources == {}


def test_browser_source_identity_damage_does_not_replace_existing_workspace():
    import pytest
    from test_browser_workspace import make_session
    from workspace import dump_session, dump_session_json, restore_session
    original = make_session()
    before = dump_session_json(original)
    damaged = dump_session(original)
    damaged["invoice_requests"]["ANOTHER"] = "Other client"
    with pytest.raises(ValueError, match="invoice identity"):
        restore_session(original, damaged)
    assert dump_session_json(original) == before
