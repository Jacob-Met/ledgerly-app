"""Client account statements through real checkpoint and operator entry points."""
import os
import stat
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
import pytest
from ledgerly.agent import Agent, LedgerEntry, PendingAction
from ledgerly.operator import main
from ledgerly.paypal import SandboxMock
from ledgerly.recovery import SnapshotStore
from ledgerly.statement import export_statement, render_statement

INVOICER = {"name": "Moss Studio", "email_address": "studio@example.test"}
AT = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
CLIENT = "billing@quinn.example"

def entry(number, total, paid="0", currency="USD", status="SENT", email=CLIENT):
    return LedgerEntry(number, "NOV-" + number, "Quinn Press", email, currency,
        Decimal(total), 30, status=status, sent_on=date(2026, 9, 1), paid_amount=Decimal(paid))

@pytest.fixture
def saved(tmp_path):
    agent = Agent(SandboxMock(), INVOICER)
    rows = [entry("A","1200","300",status="PARTIALLY_PAID"),
        entry("B","250","250",status="PAID"), entry("C","125",currency="EUR"),
        entry("D","8000","1000",currency="JPY",status="PARTIALLY_PAID"),
        entry("E","400",status="DRAFT"), entry("F","80",status="CANCELLED"),
        entry("G","50",status="PAYMENT_PENDING"), entry("H","60",status="REFUNDED"),
        entry("PRIVATE-OTHER","950",email="other@example.test")]
    agent.ledger = {row.invoice_id: row for row in rows}
    agent.audit = [{"private-source":"Never include the internal complaint or a different client"}]
    path = tmp_path / "private.json"
    SnapshotStore(path).save(agent)
    return agent, path

def test_account_totals_keep_currencies_and_exclude_unsent_and_adjustments(saved):
    agent,_ = saved
    html = render_statement(agent,CLIENT,created_at=AT)
    assert 'data-outstanding="USD">900.00</strong>' in html
    assert 'data-outstanding="EUR">125.00</strong>' in html
    assert 'data-outstanding="JPY">7,000</strong>' in html
    assert "7000.00" not in html and "40 days overdue" in html
    for text in ["Payment pending","Partially paid","Refund",
                 "Not sent","Cancelled",
                 "Paid"]:
        assert text in html
    for text in ["PRIVATE-OTHER","other@example.test","private-source","internal complaint"]:
        assert text not in html

def test_export_reads_exact_checkpoint_without_changes_or_provider(saved,tmp_path):
    agent,state = saved
    before = state.read_bytes()
    output = tmp_path / "quinn.html"
    with patch("socket.socket",side_effect=AssertionError("No network")), \
         patch("ledgerly.paypal.HttpPayPalClient",side_effect=AssertionError("No provider")):
        export_statement(state,CLIENT,output,INVOICER)
    assert state.read_bytes() == before
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "Account statement" in output.read_text() and "Sandbox" in output.read_text()
    assert agent.client._inner.requests == []

def test_cli_exports_without_network_opt_in_or_provider_credentials(saved,tmp_path,monkeypatch):
    _,state = saved
    output = tmp_path / "cli.html"
    monkeypatch.setenv("LEDGERLY_INVOICER_EMAIL",INVOICER["email_address"])
    monkeypatch.setenv("LEDGERLY_INVOICER_NAME",INVOICER["name"])
    for key in ["LEDGERLY_ALLOW_NETWORK","PAYPAL_CLIENT_ID","PAYPAL_CLIENT_SECRET"]:
        monkeypatch.delenv(key,raising=False)
    with patch("ledgerly.operator.make_agent",side_effect=AssertionError("No provider")), \
         patch("socket.socket",side_effect=AssertionError("No network")):
        assert main(["--state-file",str(state),"--statement",CLIENT,
                     "--statement-output",str(output)]) == 0
    assert "Sandbox" in output.read_text()

def test_uncertain_send_stays_out_of_outstanding_after_restored_interruption(saved,tmp_path):
    agent,state = saved
    agent.pending["unknown"] = PendingAction("unknown","send_invoice","A","private summary",
        {"sensitive":"Never export"},status="INTERRUPTED",result={"outcome":"UNKNOWN"})
    SnapshotStore(state).save(agent)
    output = tmp_path / "unknown.html"
    export_statement(state,CLIENT,output,INVOICER)
    html = output.read_text()
    assert 'data-outstanding="USD">0.00</strong>' in html
    assert "Send unconfirmed" in html
    assert "Never export" not in html and "private summary" not in html

def test_unknown_reminder_is_visible_without_erasing_invoice_balance(saved):
    agent,_ = saved
    agent.pending["reminder"] = PendingAction("reminder","send_reminder","A","Reminder",
        {},status="FAILED",result={"outcome":"UNKNOWN"})
    html = render_statement(agent,CLIENT,created_at=AT)
    assert "Reminder unconfirmed" in html
    assert 'data-outstanding="USD">900.00</strong>' in html

def test_unknown_due_date_is_not_overdue_or_due_today(saved):
    agent,_ = saved
    agent.ledger["A"].provider_due_known = True
    agent.ledger["A"].provider_due_on = None
    html = render_statement(agent,CLIENT,created_at=AT)
    usd = html.split('data-currency="USD"')[1].split("</section>")[0]
    assert "<dt>Overdue</dt>" not in usd
    assert "<dt>Undated</dt><dd>900.00</dd>" in usd

@pytest.mark.parametrize("client",["Quinn","quinn.example","billing","missing@example.test"])
def test_client_must_be_complete_and_match_saved_records(saved,tmp_path,client):
    _,state = saved
    output = tmp_path / "missing.html"
    with pytest.raises(ValueError):
        export_statement(state,client,output,INVOICER)
    assert not output.exists()

def test_case_insensitive_full_email_matches_without_other_clients(saved):
    agent,_ = saved
    html = render_statement(agent,CLIENT.upper(),created_at=AT)
    assert 'data-outstanding="USD">900.00</strong>' in html and "PRIVATE-OTHER" not in html

def test_existing_destination_and_checkpoint_can_never_be_overwritten(saved,tmp_path):
    _,state = saved
    output = tmp_path / "statement.html"
    output.write_text("earlier reviewed statement")
    with pytest.raises(FileExistsError):
        export_statement(state,CLIENT,output,INVOICER)
    assert output.read_text() == "earlier reviewed statement"
    with pytest.raises(ValueError,match=".html"):
        export_statement(state,CLIENT,state,INVOICER)

def test_wrong_merchant_and_corrupt_state_do_not_make_a_statement(saved,tmp_path):
    _,state = saved
    output = tmp_path / "no.html"
    with pytest.raises(ValueError,match="different invoicer"):
        export_statement(state,CLIENT,output,{**INVOICER,"email_address":"different@example.test"})
    state.write_text('{"version":1}')
    with pytest.raises(ValueError,match="envelope"):
        export_statement(state,CLIENT,output,INVOICER)
    assert not output.exists()

def test_export_contains_literal_text_and_no_active_client_markup(saved):
    agent,_ = saved
    agent.ledger["A"].client_name = '<script>alert("client")</script>'
    agent.ledger["A"].invoice_number = '<img src="https://bad.example/leak">'
    html = render_statement(agent,CLIENT,created_at=AT)
    assert '<script>alert' not in html and '<img src=' not in html
    assert '&lt;script&gt;' in html and '&lt;img src=' in html
    assert "default-src 'none'" in html and "window.print()" in html

def test_cli_refuses_combining_export_with_effectful_modes(saved,tmp_path):
    _,state = saved
    for flag in ["--sandbox","--resume","--follow-up","--retainers","--demo","--draft-only","--ollama"]:
        with pytest.raises(SystemExit) as error:
            main(["--state-file",str(state),"--statement",CLIENT,
                  "--statement-output",str(tmp_path / "no.html"),flag])
        assert error.value.code == 2
    assert not (tmp_path / "no.html").exists()

def retain_payments(agent, invoice_id="A", values=("40.00", "260.00")):
    if not hasattr(agent, "invoice_records"):
        agent.invoice_records = {}
    agent.invoice_records[invoice_id] = {
        "record": {"id": invoice_id, "items": [], "detail": {"currency_code": "USD"},
                   "payments": {"transactions": [
                       {"payment_id": "PAY-CLIENT-1", "payment_date": "2026-10-05",
                        "type": "PAYPAL", "method": "PAYPAL",
                        "amount": {"currency_code": "USD", "value": values[0]}},
                       {"payment_id": "EXTERNAL-CLIENT-2", "payment_date": "2026-10-08",
                        "type": "EXTERNAL", "method": "BANK_TRANSFER",
                        "amount": {"currency_code": "USD", "value": values[1]}}
                   ]}},
        "observed_at": "2026-11-09T18:00:00+00:00",
        "provider": "Synthetic provider test",
    }


def test_export_uses_retained_payment_rows_without_network_or_changing_balance(saved, tmp_path):
    agent, state = saved
    retain_payments(agent)
    retain_payments(agent, "PRIVATE-OTHER")
    agent.invoice_records["PRIVATE-OTHER"]["record"]["payments"]["transactions"][0]["payment_id"] = "PRIVATE-PAYMENT"
    SnapshotStore(state).save(agent)
    before = state.read_bytes()
    output = tmp_path / "with-payments.html"
    with patch("socket.socket", side_effect=AssertionError("No network")):
        export_statement(state, CLIENT, output, INVOICER)
    html = output.read_text()
    assert state.read_bytes() == before
    assert "PAY-CLIENT-1" in html and "EXTERNAL-CLIENT-2" in html
    assert "2026-10-05" in html and "2026-10-08" in html
    assert "External" in html and "Bank transfer" in html
    assert html.count("PAY-CLIENT-1") == 1 and html.count("EXTERNAL-CLIENT-2") == 1
    assert '<dl class="payment-summary">' not in html
    assert 'data-outstanding="USD">900.00</strong>' in html
    assert "PRIVATE-PAYMENT" not in html and "PRIVATE-OTHER" not in html


def test_older_payment_observation_is_explicit_without_replacing_paid_balance(saved):
    agent, _ = saved
    retain_payments(agent, values=("40.00", "25.00"))
    html = render_statement(agent, CLIENT, created_at=AT)
    assert "<dt>Listed payments</dt><dd>USD 65.00</dd>" in html
    assert "<dt>Paid</dt><dd>USD 300.00</dd>" in html
    assert 'data-outstanding="USD">900.00</strong>' in html


def test_legacy_paid_invoice_discloses_missing_detail_without_inventing_rows(saved):
    agent, _ = saved
    html = render_statement(agent, CLIENT, created_at=AT)
    assert "Payment details unavailable" in html
    assert '<li class="payment-row">' not in html
    assert "payment transaction history are not present" not in html


def test_retained_payment_text_is_literal_and_other_currency_is_not_relabelled(saved):
    agent, _ = saved
    retain_payments(agent)
    rows = agent.invoice_records["A"]["record"]["payments"]["transactions"]
    rows[0]["payment_id"] = '<img src="https://example.test/private">'
    rows[1]["amount"]["currency_code"] = "EUR"
    html = render_statement(agent, CLIENT, created_at=AT)
    assert '<img src=' not in html and '&lt;img src=' in html
    assert "<dt>Unavailable entries</dt><dd>1</dd>" in html
    assert "<dt>Listed payments</dt><dd>USD 40.00</dd>" in html
