"""Continue retained invoice observations across the real CLI and desk."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import pytest

from ledgerly.invoice_desk import NativeSession, load_browser_bridge
from ledgerly.operator import main, make_agent
from ledgerly.paypal import SandboxMock
from ledgerly.recovery import DurableAgent
from ledgerly.statement import export_statement

ROOT = Path(__file__).resolve().parents[1]
MERCHANT = {"name": "Cedar Test Studio", "email_address": "studio@cedar.example"}
SOURCE = "From: Jun Ash <jun@example.test>\nPlease invoice 2 hours of archival indexing at USD 99 per hour. Net 14."


def agent(path, provider):
    return DurableAgent(provider, MERCHANT, state_file=path,
                        observation_provider="Synthetic provider course")


def sent(current):
    result = current.tool_create_invoice(SOURCE)
    assert result["ok"], result
    row = result["invoices"][0]
    current.approve(row["approval_id"], approver="fictional course")
    return row["invoice_id"]


def test_desk_to_cli_refresh_then_offline_desk_and_statement_keep_new_payment(tmp_path):
    state = tmp_path / "private.json"
    provider = SandboxMock()
    current = agent(state, provider)
    identity = sent(current)
    desk = NativeSession(current, load_browser_bridge(ROOT / "web-demo/python"),
                         provider="Synthetic provider course")
    provider.simulate_payer_payment(identity, Decimal("40"))
    assert desk.dispatch({"action": "refresh"}, "one")["ok"]
    first = deepcopy(current.invoice_records[identity])
    assert len(first["record"]["payments"]["transactions"]) == 1

    cli = agent(state, provider)
    second = provider.record_payment(identity, {
        "amount": {"currency_code": "USD", "value": "25.00"},
        "method": "BANK_TRANSFER", "payment_date": "2026-11-10",
    }).body["payment_id"]
    before = len(provider.requests)
    cli.tool_get_status(identity)
    assert provider.requests[before:] == [("GET", f"/v2/invoicing/invoices/{identity}", None)]
    observed = deepcopy(cli.invoice_records[identity])
    rows = observed["record"]["payments"]["transactions"]
    assert len(rows) == 2 and rows[1]["payment_id"] == second
    assert observed["record"]["payments"]["paid_amount"]["value"] == "65.00"
    assert observed["observed_at"] != first["observed_at"]
    assert cli.ledger[identity].balance == Decimal("133")

    before_bytes = state.read_bytes()
    with patch.object(provider, "get_invoice", side_effect=TimeoutError("offline")):
        reopened = NativeSession(agent(state, provider),
                                 load_browser_bridge(ROOT / "web-demo/python"),
                                 provider="Synthetic provider course")
        assert reopened.observed.calls == 0
        details = reopened.snapshot()["invoice_details"][0]
        assert details["observation"] == {
            "provider": observed["provider"], "observed_at": observed["observed_at"]}
        assert details["record"]["payments"]["transactions"] == rows
        output = export_statement(state, "jun@example.test", tmp_path / "account.html", MERCHANT)
    assert state.read_bytes() == before_bytes
    html = output.read_text()
    assert second in html and "USD 25.00" in html
    assert '<dl class="payment-summary">' not in html


def test_failed_cli_refresh_does_not_replace_last_observation_or_checkpoint(tmp_path):
    provider = SandboxMock()
    state = tmp_path / "private.json"
    current = agent(state, provider)
    identity = sent(current)
    provider.simulate_payer_payment(identity, Decimal("22"))
    current.tool_get_status(identity)
    observed = deepcopy(current.invoice_records[identity])
    before = state.read_bytes()
    wrong = provider.get_invoice(identity)
    wrong.body["id"] = "ANOTHER-INVOICE"
    for failure in (TimeoutError("offline"), None):
        kwargs = {"side_effect": failure} if failure else {"return_value": wrong}
        with patch.object(provider, "get_invoice", **kwargs):
            with pytest.raises((TimeoutError, ValueError)):
                current.tool_get_status(identity)
        assert current.invoice_records[identity] == observed
        assert state.read_bytes() == before


def test_maintained_sandbox_cli_sets_observation_source_without_extra_reads(tmp_path):
    provider = SandboxMock()
    env = {
        "LEDGERLY_ALLOW_NETWORK": "1",
        "PAYPAL_CLIENT_ID": "fictional-test-client",
        "PAYPAL_CLIENT_SECRET": "fictional-test-secret",
        "LEDGERLY_INVOICER_NAME": MERCHANT["name"],
        "LEDGERLY_INVOICER_EMAIL": MERCHANT["email_address"],
    }
    with patch("ledgerly.operator.HttpPayPalClient", return_value=provider):
        current = make_agent(True, False, "", "", env, tmp_path / "private.json")
    identity = sent(current)
    provider.simulate_payer_payment(identity, Decimal("31"))
    before = len(provider.requests)
    with patch("ledgerly.operator.HttpPayPalClient", return_value=provider), \
         patch.dict("os.environ", env):
        assert main(["--sandbox", "--state-file", str(tmp_path / "private.json"),
                     "--follow-up"]) == 0
    assert len(provider.requests) == before + 1
    restored = agent(tmp_path / "private.json", provider)
    observed = restored.invoice_records[identity]
    assert observed["provider"] == "PayPal Sandbox"
    assert datetime.fromisoformat(observed["observed_at"]).tzinfo is not None
    assert observed["record"]["payments"]["paid_amount"]["value"] == "31.00"
