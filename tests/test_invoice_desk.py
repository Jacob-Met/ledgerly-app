"""Native browser journeys through the real core and durable checkpoint.

The provider is an explicit SandboxMock; these tests do not assert PayPal network
compatibility. They test creation/restart/reconciliation and separate approval.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from ledgerly.invoice_desk import NativeSession, load_browser_bridge
from ledgerly.paypal import SandboxMock
from ledgerly.recovery import DurableAgent

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "From: Ada Vale <ada@example.test>\nPlease invoice 2 hours of consulting at USD 75 per hour. Net 14."
INVOICER = {"name": "Test Studio", "email_address": "studio@example.test"}


def open_desk(path, provider):
    agent = DurableAgent(provider, INVOICER, state_file=path)
    return NativeSession(agent, load_browser_bridge(ROOT / "web-demo/python"),
                         provider="Synthetic provider test")


def checked(desk, session="one", rate="75"):
    reply = desk.dispatch({"action": "analyze", "text": SOURCE}, session)
    assert reply["ok"], reply
    fields = {"client_name": "Ada Vale", "client_email": "ada@example.test",
              "due_days": "14", "amount_paid": "0",
              "line_items": [{"desc": "Consulting", "qty": "2", "unit_price": rate,
                              "currency": "USD", "unit": "hours"}]}
    reply = desk.dispatch({"action": "review", "text": SOURCE, "fields": fields,
                           "confirmed": True}, session)
    assert reply["ok"] and reply["result"]["valid"], reply
    return reply["result"]["review_id"]


def create(desk, session="one", rate="75"):
    review_id = checked(desk, session, rate)
    return desk.dispatch({"action": "draft", "text": SOURCE, "review_id": review_id,
                          "confirmation": "CREATE"}, session)


class DropCreatedResponse(SandboxMock):
    def create_draft_invoice(self, invoice, prefer_representation=False):
        super().create_draft_invoice(invoice, prefer_representation)
        raise TimeoutError("provider accepted the draft; response lost")


def test_create_and_send_are_separate_and_restart_keeps_the_original(tmp_path):
    path = tmp_path / "state.json"
    provider = SandboxMock()
    desk = open_desk(path, provider)
    review = checked(desk)
    no = desk.dispatch({"action": "draft", "text": SOURCE, "review_id": review,
                        "confirmation": ""}, "one")
    assert not no["ok"] and not provider.invoices
    reply = desk.dispatch({"action": "draft", "text": SOURCE, "review_id": review,
                           "confirmation": "CREATE"}, "one")
    assert reply["ok"], reply
    identity = reply["state"]["ledger"][0]["invoice_id"]
    assert provider.invoices[identity]["status"] == "DRAFT"
    assert reply["state"]["invoice_details"][0]["record"]["items"]
    assert not reply["state"]["creation_attempts"]
    desk = open_desk(path, provider)
    pending = desk.snapshot()["pending"][0]
    wrong = desk.dispatch({"action": "approve", "action_id": pending["id"],
                          "confirmation": "SEND WRONG"}, "fresh")
    assert not wrong["ok"] and provider.invoices[identity]["status"] == "DRAFT"
    sent = desk.dispatch({"action": "approve", "action_id": pending["id"],
                         "confirmation": pending["confirmation"]}, "fresh")
    assert sent["ok"] and provider.invoices[identity]["status"] == "SENT"
    desk = open_desk(path, provider)
    assert not desk.snapshot()["pending"]
    repeated = desk.dispatch({"action": "approve", "action_id": pending["id"],
                             "confirmation": pending["confirmation"]}, "fresh")
    assert not repeated["ok"]


def test_unknown_creation_survives_restart_and_recovers_without_reposting(tmp_path):
    path = tmp_path / "state.json"
    provider = DropCreatedResponse()
    desk = open_desk(path, provider)
    failed = create(desk)
    assert not failed["ok"] and len(provider.invoices) == 1
    assert failed["result"]["draft_failure"]["outcome"] == "UNKNOWN"
    desk = open_desk(path, provider)
    assert not desk.snapshot()["ledger"]
    attempt = desk.snapshot()["creation_attempts"][0]
    assert attempt["status"] == "UNKNOWN"
    assert not create(desk, "new")["ok"]
    assert len(provider.invoices) == 1
    identity = next(iter(provider.invoices))
    original = deepcopy(provider.invoices[identity])
    provider.invoices[identity]["items"][0]["unit_amount"]["value"] = "999"
    mismatch = desk.dispatch({"action": "recover_creation", "attempt_id": attempt["id"],
                              "invoice_id": identity}, "new")
    assert not mismatch["ok"] and not desk.snapshot()["pending"]
    provider.invoices[identity] = original
    recovered = desk.dispatch({"action": "recover_creation", "attempt_id": attempt["id"],
                               "invoice_id": identity}, "new")
    assert recovered["ok"], recovered
    assert len(provider.invoices) == 1 and not recovered["state"]["creation_attempts"]
    assert recovered["state"]["pending"][0]["invoice_id"] == identity
    desk = open_desk(path, provider)
    assert len(desk.snapshot()["pending"]) == 1
    assert not desk.snapshot()["creation_attempts"]
    assert provider.invoices[identity]["status"] == "DRAFT"


def test_saved_core_draft_resolves_journal_write_loss_on_restart(tmp_path):
    path = tmp_path / "state.json"
    provider = DropCreatedResponse()
    desk = open_desk(path, provider)
    assert not create(desk)["ok"]
    desk = open_desk(path, provider)
    attempt = desk.snapshot()["creation_attempts"][0]
    identity = next(iter(provider.invoices))
    with patch.object(desk.journal, "save", side_effect=OSError("disk interruption")):
        failed = desk.dispatch({"action": "recover_creation", "attempt_id": attempt["id"],
                                "invoice_id": identity}, "recovery")
    assert not failed["ok"]
    # The core checkpoint succeeded before the separate journal replacement.
    desk = open_desk(path, provider)
    assert len(desk.snapshot()["pending"]) == 1
    assert not desk.snapshot()["creation_attempts"]
    assert len(provider.invoices) == 1


def test_checked_revisions_are_bound_to_their_own_browser(tmp_path):
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    alice = checked(desk, "alice", "75")
    checked(desk, "bob", "120")
    wrong = desk.dispatch({"action": "draft", "text": SOURCE, "review_id": alice,
                           "confirmation": "CREATE"}, "bob")
    assert not wrong["ok"] and not provider.invoices
    good = desk.dispatch({"action": "draft", "text": SOURCE, "review_id": alice,
                          "confirmation": "CREATE"}, "alice")
    assert good["ok"] and good["state"]["ledger"][0]["total"] == "150"
    repeated = desk.dispatch({"action": "draft", "text": SOURCE, "review_id": alice,
                              "confirmation": "CREATE"}, "alice")
    assert not repeated["ok"] and len(provider.invoices) == 1


def test_native_operator_refuses_simulation_actions(tmp_path):
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    for action in ("payment", "replay", "advance", "reset", "workspace_clear"):
        assert not desk.dispatch({"action": action}, "one")["ok"]
    assert not provider.requests


def test_declined_native_draft_reopens_after_restart_with_separate_confirmation(tmp_path):
    path = tmp_path / "state.json"
    provider = SandboxMock()
    desk = open_desk(path, provider)
    created = create(desk)
    original = created["state"]["pending"][0]
    identity = original["invoice_id"]
    reason = "I need to confirm the recipient before sending."
    rejected = desk.dispatch({"action": "reject", "action_id": original["id"], "reason": reason}, "one")
    assert rejected["ok"], rejected

    desk = open_desk(path, provider)
    reopened = desk.dispatch({"action": "reopen_send_review", "action_id": original["id"]}, "fresh")
    assert reopened["ok"], reopened
    pending = reopened["state"]["pending"][0]
    assert pending["id"] != original["id"] and pending["invoice_id"] == identity
    history = reopened["state"]["completed_reviews"]
    assert len(history) == 1 and history[0]["result"]["reason"] == reason
    assert len(provider.invoices) == 1 and provider.invoices[identity]["status"] == "DRAFT"

    desk = open_desk(path, provider)
    retained = desk.snapshot()["pending"][0]
    assert retained["id"] == pending["id"]
    refused = desk.dispatch({"action": "approve", "action_id": retained["id"], "confirmation": ""}, "fresh")
    assert not refused["ok"] and provider.invoices[identity]["status"] == "DRAFT"
    sent = desk.dispatch({"action": "approve", "action_id": retained["id"],
                          "confirmation": retained["confirmation"]}, "fresh")
    assert sent["ok"] and provider.invoices[identity]["status"] == "SENT"
    assert len(provider.invoices) == 1
    assert not desk.dispatch({"action": "reopen_send_review", "action_id": original["id"]}, "fresh")["ok"]


def test_refreshed_payment_record_survives_restart_without_provider_call(tmp_path):
    path = tmp_path / "state.json"
    provider = SandboxMock()
    desk = open_desk(path, provider)
    created = create(desk)
    action = created["state"]["pending"][0]
    identity = action["invoice_id"]
    sent = desk.dispatch({"action": "approve", "action_id": action["id"],
                         "confirmation": action["confirmation"]}, "one")
    assert sent["ok"]
    provider.simulate_payer_payment(identity, "60")
    refreshed = desk.dispatch({"action": "refresh"}, "one")
    assert refreshed["ok"]
    original = refreshed["state"]["invoice_details"]
    saved = deepcopy(desk.agent.invoice_records[identity])
    assert original[0]["record"]["payments"]["transactions"][0]["amount"]["value"] == "60.00"

    # Reopen against a provider that cannot be contacted at all.
    with patch.object(provider, "get_invoice", side_effect=TimeoutError("offline")):
        reopened = open_desk(path, provider)
        assert reopened.observed.calls == 0
        assert reopened.snapshot()["invoice_details"] == original
        assert reopened.agent.invoice_records[identity] == saved
        failed_refresh = reopened.dispatch({"action": "refresh"}, "two")
        assert failed_refresh["result"]["errors"]
        assert failed_refresh["state"]["invoice_details"] == original
    assert saved["provider"] == "Synthetic provider test"
    assert saved["observed_at"].endswith("+00:00")
    assert reopened.agent.ledger[identity].paid_amount == 60
    assert not reopened.snapshot()["pending"]


def test_retained_invoice_record_is_private_and_bound_to_saved_identity(tmp_path):
    import json
    import pytest
    path = tmp_path / "state.json"
    provider = SandboxMock()
    desk = open_desk(path, provider)
    created = create(desk)
    identity = created["state"]["ledger"][0]["invoice_id"]
    assert desk.dispatch({"action": "refresh"}, "one")["ok"]
    raw = json.loads(path.read_text())
    assert raw["version"] == 2
    assert path.stat().st_mode & 0o077 == 0
    raw["invoice_records"][identity]["record"]["id"] = "ANOTHER-INVOICE"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="retained invoice observation"):
        open_desk(path, provider)


def test_legacy_checkpoint_remains_readable_with_original_review_details(tmp_path):
    import json
    path = tmp_path / "state.json"
    provider = SandboxMock()
    desk = open_desk(path, provider)
    created = create(desk)
    assert desk.dispatch({"action": "refresh"}, "one")["ok"]
    raw = json.loads(path.read_text())
    raw.pop("invoice_records")
    raw["version"] = 1
    path.write_text(json.dumps(raw))
    reopened = open_desk(path, provider)
    assert reopened.snapshot()["invoice_details"][0]["record"]["items"]
    assert reopened.agent.invoice_records == {}
    assert "observation" not in reopened.snapshot()["invoice_details"][0]
    assert reopened.observed.calls == 0


# These use the real native desk/durable core with a controlled extractor.
# They test the observed slow-model lifecycle, not inference quality.
def _wait_analysis(desk, identity, states, session="one"):
    import time
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        reply = desk.dispatch({"action": "analysis_status", "job_id": identity}, session)
        assert reply["ok"], reply
        if reply["result"]["analysis_job"]["state"] in states:
            return reply
        time.sleep(0.005)
    raise AssertionError("Analysis did not reach the expected lifecycle state.")


def _wait_analysis_idle(desk):
    import time
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with desk.lock:
            if desk.active_analysis is None:
                return
        time.sleep(0.005)
    raise AssertionError("Controlled analysis worker did not finish.")


class _PausedExtractor:
    def __init__(self, failure=None):
        import threading
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = 0
        self.failure = failure

    def extract(self, text):
        from ledgerly.extract import RulesExtractor
        self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("controlled extractor was not released")
        if self.failure:
            raise self.failure
        return RulesExtractor().extract(text)


def test_slow_analysis_keeps_checked_work_and_other_invoice_actions_usable(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    pending = create(desk)["state"]["pending"][0]
    prior_review = checked(desk, rate="80")
    prior_analysis = deepcopy(desk.browser("one").analysis)
    slow = _PausedExtractor()
    desk.extractor.rules = slow
    start = desk.dispatch({"action": "analysis_start", "job_id": "slow-one",
                           "text": SOURCE.replace("Ada Vale", "Bea Ray")}, "one")
    assert start["ok"] and slow.entered.wait(1)
    try:
        assert desk.browser("one").review[0] == prior_review
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(desk.dispatch, {
                "action": "approve", "action_id": pending["id"],
                "confirmation": pending["confirmation"]}, "one")
            approved = future.result(timeout=2)
        assert approved["ok"] and provider.invoices[pending["invoice_id"]]["status"] == "SENT"
        dismissed = desk.dispatch({"action": "analysis_dismiss", "job_id": "slow-one"}, "one")
        assert dismissed["result"]["analysis_job"]["state"] == "dismissed"
    finally:
        slow.release.set()
    _wait_analysis_idle(desk)
    assert desk.browser("one").analysis == prior_analysis
    assert desk.browser("one").review[0] == prior_review
    assert not desk.dispatch({"action": "analysis_apply", "job_id": "slow-one"}, "one")["ok"]
    created = desk.dispatch({"action": "draft", "text": SOURCE, "review_id": prior_review,
                             "confirmation": "CREATE"}, "one")
    assert created["ok"] and len(provider.invoices) == 2
    assert any(row["total"] == "160" for row in created["state"]["ledger"])


def test_ready_analysis_needs_explicit_apply_and_new_human_check(tmp_path):
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    prior_review = checked(desk)
    changed = SOURCE.replace("Ada Vale", "Bea Ray").replace("ada@example.test", "bea@example.test")
    assert desk.dispatch({"action": "analysis_start", "job_id": "ready-one", "text": changed}, "one")["ok"]
    ready = _wait_analysis(desk, "ready-one", {"ready"})
    assert ready["result"]["analysis"]["client_name"] == "Bea Ray"
    assert desk.browser("one").review[0] == prior_review
    assert not provider.requests
    applied = desk.dispatch({"action": "analysis_apply", "job_id": "ready-one"}, "one")
    assert applied["ok"] and desk.browser("one").review is None
    assert desk.dispatch({"action": "analysis_apply", "job_id": "ready-one"}, "one")["result"] == applied["result"]
    refused = desk.dispatch({"action": "draft", "text": changed, "review_id": prior_review,
                             "confirmation": "CREATE"}, "one")
    assert not refused["ok"] and not provider.requests
    assert not desk.dispatch({"action": "analysis_apply", "job_id": "ready-one"}, "one")["ok"]
    retired = desk.dispatch({"action": "analysis_status", "job_id": "ready-one"}, "one")
    assert retired["result"]["analysis_job"]["state"] == "dismissed"
    assert "analysis" not in retired["result"]


def test_new_review_retires_a_late_analysis_result(tmp_path):
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    old_review = checked(desk)
    slow = _PausedExtractor()
    desk.extractor.rules = slow
    assert desk.dispatch({"action": "analysis_start", "job_id": "late-one",
                          "text": SOURCE.replace("Ada Vale", "Bea Ray")}, "one")["ok"]
    assert slow.entered.wait(1)
    fields = {"client_name": "Ada Vale", "client_email": "ada@example.test",
              "due_days": "14", "amount_paid": "0",
              "line_items": [{"desc": "Consulting", "qty": "2", "unit_price": "99",
                              "currency": "USD", "unit": "hours"}]}
    try:
        reviewed = desk.dispatch({"action": "review", "text": SOURCE,
                                  "fields": fields, "confirmed": True}, "one")
        assert reviewed["ok"] and reviewed["result"]["valid"]
        new_review = reviewed["result"]["review_id"]
        assert new_review != old_review
    finally:
        slow.release.set()
    _wait_analysis_idle(desk)
    assert not desk.dispatch({"action": "analysis_apply", "job_id": "late-one"}, "one")["ok"]
    assert desk.browser("one").review[0] == new_review
    created = desk.dispatch({"action": "draft", "text": SOURCE, "review_id": new_review,
                             "confirmation": "CREATE"}, "one")
    assert created["ok"] and created["state"]["ledger"][0]["total"] == "198"


def test_analysis_timeout_keeps_previous_review_and_reports_no_provider_uncertainty(tmp_path):
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    prior_review = checked(desk)
    before = deepcopy(desk.browser("one").analysis)
    failed = _PausedExtractor(TimeoutError("real local model timeout reproduced"))
    failed.release.set()
    desk.extractor.rules = failed
    assert desk.dispatch({"action": "analysis_start", "job_id": "failed-one", "text": SOURCE}, "one")["ok"]
    result = _wait_analysis(desk, "failed-one", {"failed"})
    message = result["result"]["analysis_job"]["message"]
    assert "Local analysis" in message and "No provider request was made by this analysis" in message
    assert "Inspect the saved ledger" not in message
    assert desk.browser("one").analysis == before and desk.browser("one").review[0] == prior_review
    legacy = desk.dispatch({"action": "analyze", "text": SOURCE}, "one")
    assert not legacy["ok"] and "Local analysis" in legacy["message"]
    assert desk.browser("one").analysis == before and desk.browser("one").review[0] == prior_review
    assert not provider.requests


def test_analysis_request_recovery_does_not_repeat_inference_or_cross_browsers(tmp_path):
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    checked(desk)
    slow = _PausedExtractor()
    desk.extractor.rules = slow
    request = {"action": "analysis_start", "job_id": "retained-one", "text": SOURCE}
    assert desk.dispatch(request, "one")["ok"] and slow.entered.wait(1)
    try:
        repeated = desk.dispatch(request, "one")
        assert repeated["ok"] and slow.calls == 1
        assert not desk.dispatch({**request, "job_id": "another"}, "two")["ok"]
        for action in ("analysis_status", "analysis_apply", "analysis_dismiss"):
            assert not desk.dispatch({"action": action, "job_id": "retained-one"}, "two")["ok"]
        assert desk.dispatch({"action": "analysis_dismiss", "job_id": "retained-one"}, "one")["ok"]
    finally:
        slow.release.set()
    _wait_analysis_idle(desk)
    recovered = desk.dispatch(request, "one")
    assert recovered["result"]["analysis_job"]["state"] == "dismissed" and slow.calls == 1
    assert not provider.requests


def test_changed_source_can_be_checked_manually_without_repeating_a_slow_model(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    for fails in (False, True):
        provider = SandboxMock()
        desk = open_desk(tmp_path / ("failed.json" if fails else "running.json"), provider)
        old_review = checked(desk)
        changed = SOURCE.replace("Ada Vale", "Nora Finch").replace("ada@example.test", "nora@example.test")
        model = _PausedExtractor(TimeoutError("controlled timeout") if fails else None)
        if fails:
            model.release.set()
        desk.extractor.rules = model
        assert desk.dispatch({"action": "analysis_start", "job_id": "manual-source", "text": changed}, "one")["ok"]
        assert model.entered.wait(1)
        if fails:
            _wait_analysis(desk, "manual-source", {"failed"})
        fields = {"client_name": "Nora Finch", "client_email": "nora@example.test",
                  "due_days": "14", "amount_paid": "0",
                  "line_items": [{"desc": "Consulting", "qty": "2.5", "unit_price": "87",
                                  "currency": "USD", "unit": "hours"}]}
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                reviewed = executor.submit(desk.dispatch, {
                    "action": "review", "text": changed, "fields": fields, "confirmed": True
                }, "one").result(timeout=2)
            assert reviewed["ok"] and reviewed["result"]["valid"], reviewed
            assert reviewed["result"]["review_id"] != old_review
            assert model.calls == 1
            assert not provider.requests
            stale = desk.dispatch({"action": "draft", "text": changed, "review_id": old_review,
                                   "confirmation": "CREATE"}, "one")
            assert not stale["ok"] and not provider.requests
            created = desk.dispatch({"action": "draft", "text": changed,
                                     "review_id": reviewed["result"]["review_id"],
                                     "confirmation": "CREATE"}, "one")
            assert created["ok"], created
            assert created["state"]["ledger"][0]["total"] == "217.5"
            assert model.calls == 1
            assert len(provider.invoices) == 1
        finally:
            model.release.set()
        _wait_analysis_idle(desk)
        assert not desk.dispatch({"action": "analysis_apply", "job_id": "manual-source"}, "one")["ok"]


def test_manual_native_review_still_requires_confirmation_and_valid_fields(tmp_path):
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    fields = {"client_name": "Nora Finch", "client_email": "nora@example.test",
              "due_days": "14", "amount_paid": "0",
              "line_items": [{"desc": "Consulting", "qty": "0", "unit_price": "87",
                              "currency": "USD", "unit": "hours"}]}
    model = _PausedExtractor()
    desk.extractor.rules = model
    unconfirmed = desk.dispatch({"action": "review", "text": SOURCE,
                                 "fields": fields, "confirmed": False}, "one")
    assert not unconfirmed["ok"]
    invalid = desk.dispatch({"action": "review", "text": SOURCE,
                            "fields": fields, "confirmed": True}, "one")
    assert invalid["ok"] and not invalid["result"]["valid"], invalid
    assert desk.browser("one").review is None
    assert model.calls == 0 and not provider.requests


def test_native_file_intake_uses_rules_without_waiting_for_the_configured_model(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    old_review = checked(desk)
    model = _PausedExtractor()
    desk.extractor.rules = model
    assert desk.dispatch({"action": "analysis_start", "job_id": "old-model",
                          "text": SOURCE}, "one")["ok"]
    assert model.entered.wait(1)
    imported = SOURCE.replace("Ada Vale", "Imported Client").replace("ada@example.test", "imported@example.test")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            reply = executor.submit(desk.dispatch, {"action": "analyze_rules", "text": imported},
                                    "one").result(timeout=2)
        assert reply["ok"] and reply["result"]["client_name"] == "Imported Client", reply
        assert reply["result"]["source"] == "rules"
        assert model.calls == 1 and not provider.requests
        assert desk.browser("one").analysis_text == imported
        assert desk.browser("one").review is None
        assert not desk.dispatch({"action": "analysis_apply", "job_id": "old-model"}, "one")["ok"]
        refused = desk.dispatch({"action": "draft", "text": imported, "review_id": old_review,
                                 "confirmation": "CREATE"}, "one")
        assert not refused["ok"] and not provider.requests
    finally:
        model.release.set()
    _wait_analysis_idle(desk)


def test_native_record_observation_is_distinct_from_later_ledger_day(tmp_path):
    from datetime import datetime, timedelta
    path = tmp_path / "state.json"
    provider = SandboxMock()
    desk = open_desk(path, provider)
    created = create(desk)
    action = created["state"]["pending"][0]
    identity = action["invoice_id"]
    assert desk.dispatch({"action": "approve", "action_id": action["id"],
                          "confirmation": action["confirmation"]}, "one")["ok"]
    provider.simulate_payer_payment(identity, "60")
    assert desk.dispatch({"action": "refresh"}, "one")["ok"]
    captured = deepcopy(desk.agent.invoice_records[identity])
    later = datetime.fromisoformat(captured["observed_at"]).date() + timedelta(days=3)
    checkpoint = path.read_bytes()

    with patch.object(provider, "get_invoice", side_effect=TimeoutError("offline")):
        reopened = open_desk(path, provider)
        reopened.agent.today = lambda: later
        snapshot = reopened.snapshot()
        assert snapshot["today"] == later.isoformat()
        assert snapshot["invoice_details"][0]["observation"] == {
            "observed_at": captured["observed_at"], "provider": captured["provider"]}
        assert reopened.observed.calls == 0
        assert path.read_bytes() == checkpoint
        snapshot["invoice_details"][0]["observation"]["provider"] = "Changed display only"
        assert reopened.agent.invoice_records[identity] == captured
        failed = reopened.dispatch({"action": "refresh"}, "two")
        assert failed["result"]["errors"]
        assert failed["state"]["invoice_details"][0]["observation"]["observed_at"] == captured["observed_at"]
        assert path.read_bytes() == checkpoint

    refreshed = reopened.dispatch({"action": "refresh"}, "two")
    assert refreshed["ok"]
    assert refreshed["state"]["invoice_details"][0]["observation"]["observed_at"] != captured["observed_at"]


def test_rules_fast_intake_stays_usable_while_original_model_finishes(tmp_path):
    from ledgerly.extract import HybridExtractor, RulesExtractor
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    old_review = checked(desk)
    model = _PausedExtractor()
    desk.extractor.rules = HybridExtractor(RulesExtractor(), model, threshold=0.85)
    slow = ("From: Nora Wells <nora@example.test>\n\n"
            "Please invoice me for the copy editing we finished. You spent four hours on it, "
            "and we agreed USD 75 per hour. Please add USD 35 for the final proofread. "
            "Payment is due in fourteen days.")
    fast = ("From: Mina Okada <mina@example.test>\n\n"
            "3.5 hours at USD 80 per hour for copy editing.\nProofreading — USD 45\nNet 14.")
    assert desk.dispatch({"action": "analysis_start", "job_id": "original-model", "text": slow}, "one")["ok"]
    assert model.entered.wait(1)
    try:
        desk.dispatch({"action": "analysis_dismiss", "job_id": "original-model"}, "one")
        new = desk.dispatch({"action": "analysis_start", "job_id": "new-fast", "text": fast}, "one")
        assert new["ok"] and new["result"]["analysis_job"]["state"] == "ready", new
        assert new["result"]["analysis"]["source"] == "rules"
        assert [(row["currency"], Decimal(row["total"])) for row in new["result"]["analysis"]["totals_by_currency"]] == [("USD", Decimal("325"))]
        # The original model slot remains occupied; this fast result cannot
        # accidentally permit another model or replace its active-work pointer.
        refused = desk.dispatch({"action": "analysis_start", "job_id": "another-model",
                                 "text": slow}, "two")
        assert not refused["ok"] and "still finishing" in refused["message"]
        assert model.calls == 1 and desk.active_analysis is not None
        applied = desk.dispatch({"action": "analysis_apply", "job_id": "new-fast"}, "one")
        assert applied["ok"] and desk.browser("one").analysis_text == fast
        assert desk.browser("one").review is None
        assert not desk.dispatch({"action": "draft", "text": fast, "review_id": old_review,
                                  "confirmation": "CREATE"}, "one")["ok"]
        assert not provider.requests
    finally:
        model.release.set()
    _wait_analysis_idle(desk)
    assert desk.browser("one").analysis_text == fast
    assert desk.browser("one").analysis["client_name"] == "Mina Okada"
    assert not desk.dispatch({"action": "analysis_apply", "job_id": "original-model"}, "one")["ok"]
    assert model.calls == 1 and not provider.requests


def test_native_checked_deposit_document_uses_exact_core_amount_due(tmp_path):
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    source = (ROOT / "fixtures/07_partial_payment_deposit.txt").read_text()
    analysis = desk.dispatch({"action": "analyze_rules", "text": source}, "one")["result"]
    assert analysis["financials_by_currency"] == [{"currency": "USD", "total": "3500", "paid": "1000", "due": "2500"}]
    fields = {"client_name": analysis["client_name"], "client_email": analysis["client_email"],
              "due_days": str(analysis["due_days"]), "amount_paid": "1250",
              "line_items": [{**{k: str(row[k]) for k in ("desc", "qty", "unit_price", "currency")},
                              "unit": row.get("unit") or ""} for row in analysis["line_items"]]}
    checked = desk.dispatch({"action": "review", "text": source, "fields": fields, "confirmed": True}, "one")
    assert checked["result"]["valid"]
    assert checked["result"]["financials_by_currency"] == [{"currency": "USD", "total": "3500", "paid": "1250", "due": "2250"}]
    overpaid = desk.dispatch({"action": "review", "text": source,
                             "fields": {**fields, "amount_paid": "4000"}, "confirmed": True}, "one")
    assert not overpaid["result"]["valid"]
    assert overpaid["result"]["financials_by_currency"] == []
    assert not provider.requests


def test_ordinary_native_optional_section_requires_review_without_model(tmp_path):
    from ledgerly.extract import HybridExtractor, RulesExtractor

    class UnneededModel:
        def extract(self, text):
            raise AssertionError("A model cannot authorize explicitly optional work")

    source = ("From: Alex Example <alex@example.com>\n"
              "- 1 x artwork @ $50\nOptional extras:\n"
              "- 1 x animation @ $100\nNet 15\n")
    provider = SandboxMock()
    desk = open_desk(tmp_path / "qualified.json", provider)
    desk.extractor.rules = HybridExtractor(RulesExtractor(), UnneededModel(), threshold=0.85)
    result = desk.dispatch({"action": "analyze", "text": source}, "one")["result"]
    assert any(i["field"] == "line_items[1].unit_price" and i["severity"] == "error"
               and "optional" in i["message"] for i in result["issues"])
    assert result["financials_by_currency"] == []
    assert provider.requests == []
    fields = {key: result[key] for key in ("client_name", "client_email", "due_days", "amount_paid")}
    fields["due_days"] = str(fields["due_days"])
    fields["line_items"] = [{**result["line_items"][0], "unit": ""}]
    reply = desk.dispatch({"action": "review", "text": source, "fields": fields,
                           "confirmed": True}, "one")
    assert reply["ok"], reply
    checked = reply["result"]
    assert checked["valid"] and checked["financials_by_currency"][0]["due"] == "50"
    drafted = desk.dispatch({"action": "draft", "text": source, "review_id": checked["review_id"],
                             "confirmation": "CREATE"}, "one")
    assert drafted["ok"]
    assert Decimal(next(iter(provider.invoices.values()))["amount"]["value"]) == 50
    assert all(not request[1].endswith("/send") for request in provider.requests)

def test_ordinary_native_nocharge_item_requires_correction(tmp_path):
    from ledgerly.extract import HybridExtractor, RulesExtractor

    class UnneededModel:
        def extract(self, text):
            raise AssertionError("Explicit no-charge work needs review, not inferred consent")

    source = ("From: Nia Santos <nia@example.test>\n"
              "- 2 hours editing @ GBP 75\n"
              "- 1 x no-charge transcription @ GBP 25\nNet 30\n")
    provider = SandboxMock()
    desk = open_desk(tmp_path / "nocharge.json", provider)
    desk.extractor.rules = HybridExtractor(RulesExtractor(), UnneededModel(), threshold=0.85)
    result = desk.dispatch({"action": "analyze", "text": source}, "one")["result"]
    assert any(i["field"] == "line_items[1].unit_price" and i["severity"] == "error"
               for i in result["issues"])
    assert result["financials_by_currency"] == []
    fields = {key: result[key] for key in ("client_name", "client_email", "due_days", "amount_paid")}
    fields["due_days"] = str(fields["due_days"])
    fields["line_items"] = [{**result["line_items"][0], "unit": "hours"}]
    checked = desk.dispatch({"action": "review", "text": source, "fields": fields,
                             "confirmed": True}, "one")["result"]
    assert checked["valid"]
    assert checked["financials_by_currency"] == [
        {"currency": "GBP", "total": "150", "paid": "0", "due": "150"}]
    assert not provider.requests

def test_explicit_local_ai_uses_configured_fallback_once_and_keeps_prior_review(tmp_path):
    from ledgerly.extract import HybridExtractor, RulesExtractor
    desk = open_desk(tmp_path / "explicit.json", SandboxMock())
    previous = checked(desk)
    model = _PausedExtractor()
    desk.extractor.rules = HybridExtractor(RulesExtractor(), model)
    request = {"action": "analysis_start", "job_id": "explicit", "text": SOURCE, "prefer_model": True}
    assert desk.dispatch(request, "one")["ok"]
    assert model.entered.wait(1)
    try:
        assert desk.dispatch(request, "one")["ok"]
        assert model.calls == 1
        assert desk.browser("one").review[0] == previous
        changed_mode = desk.dispatch({**request, "prefer_model": False}, "one")
        assert not changed_mode["ok"]
        concurrent = desk.dispatch({**request, "job_id": "other"}, "two")
        assert not concurrent["ok"] and model.calls == 1
    finally:
        model.release.set()
    _wait_analysis_idle(desk)
    assert desk.browser("one").review[0] == previous
    assert desk.dispatch({"action": "analysis_apply", "job_id": "explicit"}, "one")["ok"]
    assert desk.browser("one").review is None


def test_explicit_local_ai_cannot_resolve_optional_or_recipient_decisions(tmp_path):
    from ledgerly.extract import HybridExtractor, RulesExtractor
    class ForbiddenModel:
        def extract(self, text):
            raise AssertionError("Model must not decide source authorization or recipient")
    desk = open_desk(tmp_path / "human.json", SandboxMock())
    desk.extractor.rules = HybridExtractor(RulesExtractor(), ForbiddenModel())
    for index, source in enumerate([
        "From: Alex <alex@example.test>\n- 1 x artwork @ USD 50\nOptional extras:\n- 1 x animation @ USD 100",
        "From: Alex <alex@example.test>\nBill to: Jamie <jamie@example.test>\nSend invoices to sam@example.test\n- 1 x artwork @ USD 50",
    ]):
        reply = desk.dispatch({"action": "analysis_start", "job_id": str(index),
                               "text": source, "prefer_model": True}, "one")
        assert reply["ok"], reply
        assert reply["result"]["analysis_job"]["state"] == "ready"
        assert reply["result"]["analysis_job"]["model_skipped_for_review"] is True
        observed = desk.dispatch({"action":"analysis_status", "job_id":str(index)}, "one")
        assert observed["result"]["analysis_job"]["model_skipped_for_review"] is True
        assert any(i["severity"] == "error" for i in reply["result"]["analysis"]["issues"])
        assert desk.active_analysis is None


def test_explicit_unusable_model_keeps_current_fields_and_review(tmp_path):
    from ledgerly.extract import HybridExtractor, LLMExtractor, RulesExtractor
    desk = open_desk(tmp_path / "bad-model.json", SandboxMock())
    previous = checked(desk)
    desk.extractor.rules = HybridExtractor(RulesExtractor(), LLMExtractor(lambda _: '{"confidence":1.260,"line_items":[]}'))
    reply = desk.dispatch({"action": "analysis_start", "job_id": "bad-model",
                           "text": SOURCE, "prefer_model": True}, "one")
    assert reply["ok"]
    _wait_analysis_idle(desk)
    result = desk.dispatch({"action": "analysis_status", "job_id": "bad-model"}, "one")
    assert result["result"]["analysis_job"]["state"] == "failed"
    assert desk.browser("one").review[0] == previous
    assert not desk.dispatch({"action": "analysis_apply", "job_id": "bad-model"}, "one")["ok"]


def test_native_desk_exposes_only_the_configured_model_without_starting_it(tmp_path):
    from ledgerly.operator import make_agent
    configured = make_agent(False, True, 'qwen2.5:7b', 'http://127.0.0.1:11439', {})
    provider = SandboxMock()
    agent = DurableAgent(provider, INVOICER, extractor=configured.extractor,
                         state_file=tmp_path / 'model-label.json')
    def unexpected_request(*args, **kwargs):
        raise AssertionError('Opening the desk must not start inference')
    configured.extractor.fallback.complete.opener = unexpected_request
    desk = NativeSession(agent, load_browser_bridge(ROOT / 'web-demo/python'))
    assert desk.local_model == 'qwen2.5:7b'
    assert not provider.requests
    assert open_desk(tmp_path / 'rules-label.json', SandboxMock()).local_model is None

def test_native_analysis_includes_original_normalized_field_quotes(tmp_path):
    desk = open_desk(tmp_path / "source-quotes.json", SandboxMock())
    source = ("From: Mina <mina@example.test>\n"
              "Four research interviews at CAD 85 each.\nPayment is due in 14 days.")
    response = desk.dispatch({"action": "analyze_rules", "text": source}, "source-quotes")
    assert response["ok"], response
    assert response["result"]["source_references"] == {
        "line_items": [{"qty": {"text": "Four", "value": "4"}}],
        "payment_terms": {"text": "due in 14 days", "value": "14"},
    }
    assert response["state"]["provider_operations"] == 0


def test_restore_original_analysis_keeps_fresh_approval_and_exact_source(tmp_path):
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    old_review = checked(desk, "original")
    original = desk.browser("original")
    original.analysis_result["source"] = "llm"
    restored = desk.dispatch({"action": "analysis_restore", "text": SOURCE,
                             "source_owner": "original"}, "reopened")
    assert restored["ok"] and restored["result"]["source"] == "llm"
    assert desk.browser("reopened").review is None
    denied = desk.dispatch({"action": "draft", "text": SOURCE, "review_id": old_review,
                            "confirmation": "CREATE"}, "reopened")
    assert not denied["ok"] and not provider.requests
    changed = SOURCE.replace("75", "90")
    fallback = desk.dispatch({"action": "analysis_restore", "text": changed,
                              "source_owner": "original"}, "changed")
    assert fallback["ok"] and fallback["result"]["source"] == "rules"
    assert fallback["result"]["line_items"][0]["unit_price"] == "90"
    assert not provider.requests


def test_successful_reads_do_not_displace_invoice_activity(tmp_path):
    native = open_desk(tmp_path / "activity.json", SandboxMock())
    browser = load_browser_bridge(ROOT / "web-demo/python").Demo()
    for desk in (native, browser):
        desk.agent.audit = [{"at": "2026-11-11T12:00:00Z", "event": "queued", "kind": "send_invoice", "action": "review-a", "invoice": "invoice-a"}]
        desk.agent.audit += [{"event": "tool", "ok": True} for _ in range(30)]
        desk.agent.audit += [{"event": "tool", "ok": False}]
        visible = desk.snapshot()["audit"]
        assert visible[0]["action"] == "review-a"
        assert visible[-1] == {"event": "tool", "ok": False}
        assert len(desk.agent.audit) == 32



def test_initial_client_instruction_survives_checked_creation_and_restart(tmp_path):
    from ledgerly.extract import RulesExtractor
    source = """From: Arden Works <arden@example.test>
Consulting — 2 hours at USD 75 per hour
Research memo — USD 90
Please put purchase order AW-1042 on the invoice so accounts payable can process it.
Net 30"""
    path = tmp_path / "state.json"
    provider = SandboxMock()
    desk = open_desk(path, provider)
    analyzed = desk.dispatch({"action": "analyze_rules", "text": source}, "one")
    assert analyzed["ok"] and analyzed["result"]["note"] == "Purchase order AW-1042"
    ex = analyzed["result"]
    fields = {"client_name": ex["client_name"], "client_email": ex["client_email"],
              "due_days": str(ex["due_days"]), "amount_paid": ex["amount_paid"],
              "line_items": [{key: str(value or "") for key, value in row.items()} for row in ex["line_items"]]}
    omitted = desk.dispatch({"action": "review", "text": source, "fields": fields, "confirmed": True}, "one")
    assert not omitted["ok"] and omitted["issues"][0]["field"] == "note"
    fields["note"] = ex["note"]
    reviewed = desk.dispatch({"action": "review", "text": source, "fields": fields, "confirmed": True}, "one")
    assert reviewed["ok"] and reviewed["result"]["valid"], reviewed
    checked_note = reviewed["result"]["note"]
    fields["note"] = "Changed after checking"
    created = desk.dispatch({"action": "draft", "text": source,
                             "review_id": reviewed["result"]["review_id"], "confirmation": "CREATE"}, "one")
    assert created["ok"], created
    identity = created["state"]["ledger"][0]["invoice_id"]
    assert provider.invoices[identity]["detail"]["note"] == checked_note
    assert provider.invoices[identity]["status"] == "DRAFT"
    assert desk.agent.ledger[identity].total == Decimal("240")
    restored = open_desk(path, provider)
    assert restored.snapshot()["pending"][0]["payload"]["invoice"]["detail"]["note"] == checked_note
    assert not any(request[1].endswith("/send") for request in provider.requests)


def test_checked_note_can_be_edited_or_removed_before_creation(tmp_path):
    for index, note in enumerate(["Project <North> & café\nPurchase order AW-1042", ""]):
        provider = SandboxMock()
        desk = open_desk(tmp_path / ("state-" + str(index) + ".json"), provider)
        analyzed = desk.dispatch({"action": "analyze_rules", "text": SOURCE}, "one")
        ex = analyzed["result"]
        fields = {"client_name": ex["client_name"], "client_email": ex["client_email"],
                  "due_days": str(ex["due_days"]), "amount_paid": ex["amount_paid"], "note": note,
                  "line_items": [{key: str(value or "") for key, value in row.items()} for row in ex["line_items"]]}
        reviewed = desk.dispatch({"action": "review", "text": SOURCE, "fields": fields, "confirmed": True}, "one")
        assert reviewed["ok"] and reviewed["result"]["valid"], reviewed
        created = desk.dispatch({"action": "draft", "text": SOURCE,
                                 "review_id": reviewed["result"]["review_id"], "confirmation": "CREATE"}, "one")
        assert created["ok"], created
        record = next(iter(provider.invoices.values()))
        assert record["detail"].get("note", "") == note
        if not note:
            assert "note" not in record["detail"]


def test_invalid_note_cannot_allocate_an_invoice_number(tmp_path):
    from ledgerly.extract import RulesExtractor
    from ledgerly.agent import Agent
    class WithNote:
        def __init__(self, note): self.note = note
        def extract(self, text):
            ex = RulesExtractor().extract(text)
            ex.note = self.note
            return ex
    for note in [None, 17, "x" * 4001]:
        provider = SandboxMock()
        agent = Agent(provider, INVOICER, extractor=WithNote(note))
        result = agent.tool_create_invoice(SOURCE)
        assert not result["ok"] and result["needs_review"]
        assert not provider.requests


def test_full_note_and_reported_payment_are_admitted_before_any_provider_effect(tmp_path):
    from ledgerly.extract import RulesExtractor
    from ledgerly.agent import Agent
    class WithNote:
        def extract(self, text):
            ex = RulesExtractor().extract(text)
            ex.note = "x" * 4000
            ex.amount_paid = Decimal("10")
            return ex
    provider = SandboxMock()
    agent = Agent(provider, INVOICER, extractor=WithNote())
    result = agent.tool_create_invoice(SOURCE)
    assert not result["ok"] and result["issues"][0]["field"] == "note"
    assert not provider.requests



def test_reported_payment_note_overflow_is_highlighted_during_review(tmp_path):
    provider = SandboxMock()
    desk = open_desk(tmp_path / "state.json", provider)
    analyzed = desk.dispatch({"action": "analyze_rules", "text": SOURCE}, "one")
    ex = analyzed["result"]
    fields = {"client_name": ex["client_name"], "client_email": ex["client_email"],
              "due_days": str(ex["due_days"]), "amount_paid": "10", "note": "x" * 4000,
              "line_items": [{key: str(value or "") for key, value in row.items()} for row in ex["line_items"]]}
    reviewed = desk.dispatch({"action": "review", "text": SOURCE, "fields": fields, "confirmed": True}, "one")
    assert reviewed["ok"] and not reviewed["result"]["valid"]
    assert any(issue["field"] == "note" and issue["severity"] == "error" for issue in reviewed["result"]["issues"])
    assert not provider.requests
