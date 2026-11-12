from ledgerly.operator import run_intake, run_pending, main
from ledgerly.recovery import DurableAgent
from ledgerly.paypal import SandboxMock
from conftest import INVOICER, load


def create_only(prompt):
    assert "Type CREATE" in prompt, "send prompt was reached"
    return "CREATE"


def test_durable_draft_can_resume_without_recreating(tmp_path):
    path = tmp_path / "state.json"
    provider = SandboxMock()
    first = DurableAgent(provider, INVOICER, state_file=path)
    log = []
    assert run_intake(first, load("01_simple_usd_hourly.txt"),
                      approve_input=create_only, emit=log.append,
                      sandbox=True, draft_only=True) == 0
    assert len(provider.invoices) == 1
    inv_id, inv = next(iter(provider.invoices.items()))
    assert inv["status"] == "DRAFT" and path.exists()
    assert len(first.list_pending()) == 1
    assert any("id=" + inv_id in line for line in log)
    assert not any(p.endswith("/send") for _, p, _ in provider.requests)
    second = DurableAgent(provider, INVOICER, state_file=path)
    assert len(second.list_pending()) == 1
    assert run_pending(second, approve_input=lambda _: "SEND LDG-0001",
                       emit=log.append) == 0
    assert len(provider.invoices) == 1
    assert provider.invoices[inv_id]["status"] == "SENT"
    assert sum(p.endswith("/send") for _, p, _ in provider.requests) == 1
    assert not DurableAgent(provider, INVOICER, state_file=path).list_pending()


def test_deposit_stays_unrecorded_until_resume(tmp_path):
    path = tmp_path / "deposit.json"
    provider = SandboxMock()
    first = DurableAgent(provider, INVOICER, state_file=path)
    log = []
    assert run_intake(first, load("07_partial_payment_deposit.txt"),
                      approve_input=create_only, emit=log.append,
                      sandbox=True, draft_only=True) == 0
    assert not any(p.endswith("/send") or p.endswith("/payments")
                   for _, p, _ in provider.requests)
    assert any("AND records a prior external payment" in x for x in log)
    second = DurableAgent(provider, INVOICER, state_file=path)
    assert run_pending(second, approve_input=lambda _: "SEND+RECORD LDG-0001",
                       emit=log.append) == 0
    assert sum(p.endswith("/send") for _, p, _ in provider.requests) == 1
    assert sum(p.endswith("/payments") for _, p, _ in provider.requests) == 1
    assert not DurableAgent(provider, INVOICER, state_file=path).list_pending()


def test_invalid_mode_fails_before_provider(monkeypatch, tmp_path, capsys):
    def fail(*args, **kwargs):
        raise AssertionError("provider accessed before validating mode")
    monkeypatch.setattr("ledgerly.operator.make_agent", fail)
    assert main(["--sandbox", "--draft-only", "source.txt"]) == 2
    assert "requires --state-file" in capsys.readouterr().err
    assert main(["--sandbox", "--resume", "--draft-only",
                 "--state-file", str(tmp_path / "state.json")]) == 2
    assert "--draft-only" in capsys.readouterr().err
