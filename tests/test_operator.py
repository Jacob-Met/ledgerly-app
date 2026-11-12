import io
import json
import urllib.request

import pytest

from ledgerly.agent import Agent
from ledgerly.operator import OllamaCompletion, invoice_review, make_agent, run_intake
from ledgerly.paypal import SANDBOX_BASE, SandboxMock

from conftest import INVOICER, load


def test_operator_offline_reject_does_not_send():
    mock = SandboxMock()
    agent = Agent(mock, INVOICER)
    messages = []
    prompts = []
    def reject(prompt):
        prompts.append(prompt)
        return "CREATE" if prompt.startswith("Type CREATE") else ""
    result = run_intake(agent, load("01_simple_usd_hourly.txt"),
                        approve_input=reject, emit=messages.append)
    assert result == 1
    assert len(prompts) == 2 and "Type CREATE" in prompts[0] and "SEND LDG-0001" in prompts[1]
    assert "Recipient" in "\n".join(messages) and "Currency" in "\n".join(messages)
    assert [method for method, path, _ in mock.requests if path.endswith("/send")] == []
    assert list(agent.pending.values())[0].status == "REJECTED"


def test_operator_offline_exact_approval_not_shortcut():
    mock = SandboxMock()
    agent = Agent(mock, INVOICER)
    assert run_intake(agent, load("01_simple_usd_hourly.txt"),
                      approve_input=lambda p: "CREATE" if p.startswith("Type CREATE") else "send LDG-0001",
                      emit=lambda _: None) == 1
    assert not any(path.endswith("/send") for _, path, _ in mock.requests)


def test_operator_offline_explicit_send_uses_approval_gate():
    mock = SandboxMock()
    agent = Agent(mock, INVOICER)
    printed = []
    assert run_intake(agent, load("01_simple_usd_hourly.txt"),
                      approve_input=lambda p: "CREATE" if p.startswith("Type CREATE") else "SEND LDG-0001",
                      emit=printed.append) == 0
    assert agent.ledger[next(iter(agent.ledger))].status == "SENT"
    assert sum(path.endswith("/send") for _, path, _ in mock.requests) == 1
    assert "Approved and sent in simulation." in printed


def test_operator_no_draft_on_invalid_client_email():
    mock = SandboxMock()
    agent = Agent(mock, INVOICER)
    assert run_intake(agent, load("05_missing_email.txt"),
                      approve_input=lambda _: pytest.fail("should not ask"), emit=lambda _: None) == 2
    assert not mock.requests


def test_live_sandbox_requires_flags_and_invoicer(monkeypatch):
    with pytest.raises(ValueError, match="LEDGERLY_INVOICER"):
        make_agent(True, False, "unused", "", {})
    invoicer = {"LEDGERLY_INVOICER_EMAIL": "merchant@example.com",
                "LEDGERLY_INVOICER_NAME": "Merchant"}
    with pytest.raises(ValueError, match="LEDGERLY_ALLOW_NETWORK"):
        make_agent(True, False, "unused", "", invoicer)
    with pytest.raises(ValueError, match="PAYPAL_CLIENT_ID"):
        make_agent(True, False, "unused", "", {**invoicer, "LEDGERLY_ALLOW_NETWORK": "1"})


def test_live_transport_cannot_be_swapped_to_production(monkeypatch):
    class RecordingClient:
        def __init__(self, client_id, client_secret, base_url):
            assert base_url == SANDBOX_BASE
    monkeypatch.setattr("ledgerly.operator.HttpPayPalClient", RecordingClient)
    env = {"LEDGERLY_ALLOW_NETWORK": "1", "PAYPAL_CLIENT_ID": "cid",
           "PAYPAL_CLIENT_SECRET": "sec", "LEDGERLY_INVOICER_EMAIL": "merchant@example.com",
           "LEDGERLY_INVOICER_NAME": "Merchant"}
    agent = make_agent(True, False, "unused", "", env)
    assert isinstance(agent.client._inner, RecordingClient)


def test_ollama_requires_local_http():
    for base in ("https://example.com", "http://evil.local:11434", "http://localhost:11434/other",
                 "http://user:password@localhost:11434", "file:///tmp", "https://localhost:11434"):
        with pytest.raises(ValueError):
            OllamaCompletion("model", base)


def test_ollama_json_response(monkeypatch):
    sent = []
    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    def fake_open(request, timeout):
        sent.append((request.full_url, request.get_method(), json.loads(request.data), timeout))
        return FakeResponse(json.dumps({"response": '{"client_email": null}'}).encode())
    completed = OllamaCompletion("local-model", opener=fake_open)("Review this fictional email")
    assert completed == '{"client_email": null}'
    assert sent[0][0] == "http://127.0.0.1:11434/api/generate"
    assert sent[0][1] == "POST" and sent[0][2]["stream"] is False
    assert sent[0][2]["format"] == "json" and sent[0][2]["options"]["temperature"] == 0


def test_invoice_review_does_not_use_untrusted_summary_as_full_review():
    agent = Agent(SandboxMock(), INVOICER)
    rows = agent.tool_create_invoice(load("01_simple_usd_hourly.txt"))["invoices"]
    review = invoice_review(agent.pending[rows[0]["approval_id"]].to_dict())
    assert "Invoice #" in review and "Currency: USD" in review
    assert "Recipient: " in review and "Terms: " in review


def test_sandbox_transport_operator_full_http_contract_without_real_network(monkeypatch):
    """The real HTTP client, not a stub Client, performs sandbox OAuth and API calls.

    urllib is replaced at the lowest transport boundary; we can verify the actual
    endpoint, method, request bodies and human approval path while making zero
    internet requests. This is not a claim of PayPal sandbox connectivity.
    """
    from ledgerly.paypal import HttpPayPalClient

    monkeypatch.setenv("LEDGERLY_ALLOW_NETWORK", "1")
    emulator = SandboxMock()
    calls = []

    class HttpReply(io.BytesIO):
        def __init__(self, status, body):
            super().__init__(json.dumps(body).encode() if body is not None else b"")
            self.status = status
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        assert request.full_url.startswith(SANDBOX_BASE)
        path = request.full_url.removeprefix(SANDBOX_BASE)
        method = request.get_method()
        body = json.loads(request.data) if request.data and path != "/v1/oauth2/token" else None
        calls.append((method, path, body))
        if path == "/v1/oauth2/token":
            assert method == "POST"
            assert request.get_header("Authorization").startswith("Basic ")
            return HttpReply(200, {"access_token": "synthetic-test-token", "expires_in": 3600})
        assert request.get_header("Authorization") == "Bearer synthetic-test-token"
        if path == "/v2/invoicing/generate-next-invoice-number":
            response = emulator.generate_next_invoice_number()
        elif path == "/v2/invoicing/invoices":
            response = emulator.create_draft_invoice(body)
        elif path.endswith("/send"):
            response = emulator.send_invoice(path.split("/")[-2], body)
        elif path.startswith("/v2/invoicing/invoices/") and method == "GET":
            response = emulator.get_invoice(path.rsplit("/", 1)[-1])
        else:
            pytest.fail(f"unrecognized HTTP request: {method} {path}")
        return HttpReply(response.status, response.body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    real_client = HttpPayPalClient("synthetic-client", "synthetic-secret")
    agent = Agent(real_client, INVOICER)
    log = []
    status = run_intake(agent, load("01_simple_usd_hourly.txt"),
                        approve_input=lambda p: "CREATE" if p.startswith("Type CREATE") else "SEND LDG-0001",
                        emit=log.append, sandbox=True)
    assert status == 0
    assert len([x for x in calls if x[1] == "/v1/oauth2/token"]) == 1
    assert sum(x[1].endswith("/send") for x in calls) == 1
    assert sum(x[0] == "GET" and x[1].startswith("/v2/invoicing/invoices/") for x in calls) == 1
    assert next(iter(agent.ledger.values())).status == "SENT"
    assert any("Approved and sent by provider." in line for line in log)
    assert all(x[1].startswith("/v1/oauth2/") or x[1].startswith("/v2/invoicing/")
               for x in calls)


def test_operator_deposit_requires_send_and_record_confirmation():
    for confirmation, expect_sent in [("SEND LDG-0001", False), ("SEND+RECORD LDG-0001", True)]:
        mock = SandboxMock()
        agent = Agent(mock, INVOICER)
        messages = []
        prompts = []
        def confirm(prompt):
            prompts.append(prompt)
            return "CREATE" if prompt.startswith("Type CREATE") else confirmation
        status = run_intake(
            agent, load("07_partial_payment_deposit.txt"),
            approve_input=confirm, emit=messages.append
        )
        assert status == (0 if expect_sent else 1)
        assert any("AND records a prior external payment" in m for m in messages)
        assert any("SEND+RECORD LDG-0001" in m for m in prompts)
        assert any(path.endswith("/payments") for _, path, _ in mock.requests) is expect_sent
        assert any(path.endswith("/send") for _, path, _ in mock.requests) is expect_sent


def test_declining_draft_prevents_all_provider_calls():
    mock = SandboxMock()
    agent = Agent(mock, INVOICER)
    logs = []
    assert run_intake(agent, load("01_simple_usd_hourly.txt"),
                      approve_input=lambda _: "", emit=logs.append, sandbox=True) == 1
    assert mock.requests == []
    assert not agent.ledger and not agent.pending
    assert any("before any provider request" in message for message in logs)

def test_draft_requires_exact_create_phrase():
    mock = SandboxMock()
    agent = Agent(mock, INVOICER)
    assert run_intake(agent, load("01_simple_usd_hourly.txt"),
                      approve_input=lambda _: "create", emit=lambda _: None) == 1
    assert mock.requests == []

def test_preview_is_frozen_and_model_not_recalled_at_creation():
    from ledgerly.extract import RulesExtractor
    class CountingExtractor:
        def __init__(self):
            self.calls = 0
        def extract(self, source):
            self.calls += 1
            return RulesExtractor().extract(source)
    mock = SandboxMock()
    extractor = CountingExtractor()
    agent = Agent(mock, INVOICER, extractor=extractor)
    result = run_intake(agent, load("01_simple_usd_hourly.txt"),
                        approve_input=lambda p: "CREATE" if p.startswith("Type CREATE") else "",
                        emit=lambda _: None)
    assert result == 1
    assert extractor.calls == 1
    assert len(agent.ledger) == 1

def test_partial_draft_failure_keeps_confirmed_invoice_ids_visible():
    class FailAfterOne(SandboxMock):
        def __init__(self):
            super().__init__()
            self.number_calls = 0
        def generate_next_invoice_number(self):
            self.number_calls += 1
            if self.number_calls == 2:
                raise OSError("synthetic lost provider response")
            return super().generate_next_invoice_number()
    provider = FailAfterOne()
    agent = Agent(provider, INVOICER)
    output = []
    outcome = run_intake(agent, load("04_multi_currency.txt"),
                         approve_input=lambda _: "CREATE", emit=output.append, sandbox=True)
    assert outcome == 3
    assert len(agent.ledger) == 1
    invoice = next(iter(agent.ledger.values()))
    assert any(invoice.invoice_id in line and "Confirmed earlier draft" in line for line in output)
    assert any("outcome uncertain" in line for line in output)
    assert not any(path.endswith("/send") for _, path, _ in provider.requests)

def test_local_model_redirect_is_rejected_without_remote_forwarding(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import urllib.error

    class Redirect(BaseHTTPRequestHandler):
        seen = 0
        def log_message(self, *args):
            pass
        def do_POST(self):
            Redirect.seen += 1
            self.send_response(307)
            self.send_header("Location", "https://example.invalid/never-send-job-email")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("HTTP_PROXY", "http://example.invalid:9999")
        model = OllamaCompletion("fixture", "http://127.0.0.1:" + str(server.server_port))
        with pytest.raises(urllib.error.HTTPError) as raised:
            model("Private invoice content")
        assert raised.value.code == 307
        assert Redirect.seen == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

def test_actual_operator_entrypoint_requires_distinct_create_and_send_inputs():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    fixture = root / "fixtures" / "01_simple_usd_hourly.txt"
    cmd = [sys.executable, "-m", "ledgerly.operator", str(fixture)]
    decline = subprocess.run(cmd, input="\n", text=True, capture_output=True,
                             cwd=root, timeout=20)
    assert decline.returncode == 1
    assert "Cancelled before any provider request" in decline.stdout
    assert "Created 1" not in decline.stdout
    accepted = subprocess.run(cmd, input="CREATE\nSEND LDG-0001\n", text=True,
                              capture_output=True, cwd=root, timeout=20)
    assert accepted.returncode == 0, accepted.stderr
    assert "Created 1 simulated draft" in accepted.stdout
    assert "Approved and sent in simulation" in accepted.stdout


def test_installed_offline_demo_keeps_create_and_send_approval_separate():
    import subprocess
    import sys
    from pathlib import Path

    cwd = Path(__file__).resolve().parents[1]
    def cmd(data, *extra):
        return subprocess.run(
            [sys.executable, "-m", "ledgerly.operator", "--demo", *extra],
            input=data, text=True, capture_output=True, cwd=cwd, timeout=20,
        )

    cancelled = cmd("")
    assert cancelled.returncode == 1
    assert "Cancelled before any provider request" in cancelled.stdout
    assert "Created 1 simulated draft" not in cancelled.stdout

    draft = cmd("CREATE\n")
    assert draft.returncode == 1
    assert "Created 1 simulated draft" in draft.stdout
    assert "Rejected; invoice remains a draft" in draft.stdout

    sent = cmd("CREATE\nSEND LDG-0001\n")
    assert sent.returncode == 0, sent.stderr
    assert "Approved and sent in simulation" in sent.stdout
    assert "No payer link was created; this invoice exists only in the offline simulation." in sent.stdout
    assert "sandbox.paypal.com" not in sent.stdout

    forbidden = cmd("", "--sandbox")
    assert forbidden.returncode == 2
    assert "--demo is offline-only" in forbidden.stderr

    forbidden_resume = cmd("", "--resume")
    assert forbidden_resume.returncode == 2
    assert "--demo is offline-only" in forbidden_resume.stderr


def test_ollama_rate_choices_copy_decimal_source_values_not_quantity_or_history():
    from ledgerly.extract import LLM_PROMPT
    from ledgerly.operator import _invoice_output_format
    source = ("From: Client <client@example.test>\n"
              "Four hours of artwork at USD 75.25 and proofreading USD 35.10.\n"
              "> Earlier artwork USD 15. Net 30.")
    schema = _invoice_output_format(LLM_PROMPT.replace("{text}", source))
    price = schema["properties"]["line_items"]["items"]["properties"]["unit_price"]
    assert price == {"type": "string", "enum": ["35.10", "75.25"]}
    item = schema["properties"]["line_items"]["items"]
    assert item["properties"]["unit"] == {"type": ["string", "null"]}
    assert "unit" in item["required"]
    assert _invoice_output_format(LLM_PROMPT.replace("{text}", "Price to be agreed")) == "json"


def test_native_transport_sends_source_price_schema_without_relaxing_grounding():
    from ledgerly.extract import LLM_PROMPT
    sent = []
    def fake_open(request, timeout):
        sent.append(json.loads(request.data))
        return io.BytesIO(b'{"response": "{}"}')
    OllamaCompletion("fixture", opener=fake_open)(LLM_PROMPT.replace("{text}", "Artwork USD 75. Proofread USD 35."))
    schema = sent[0]["format"]
    assert schema["properties"]["line_items"]["items"]["properties"]["unit_price"]["enum"] == ["35", "75"]
    assert make_agent(False, True, "fixture", "http://127.0.0.1:11434", {}).extractor.fallback.ground_prices

def test_native_prompt_and_schema_agree_and_keep_original_source():
    from ledgerly.extract import LLM_PROMPT
    source = "From: Jo <jo@example.test>\nEditing was four hours at USD 75; captions were a flat USD 35."
    sent = []
    def fake_open(request, timeout):
        sent.append(json.loads(request.data))
        return io.BytesIO(b'{"response":"{}"}')
    OllamaCompletion("fixture", opener=fake_open)(LLM_PROMPT.replace("{text}", source))
    body = sent[0]
    schema_text = body["prompt"].split("JSON Schema:\n", 1)[1].split("\nUse decimal strings", 1)[0]
    assert json.loads(schema_text) == body["format"]
    assert '"unit_price": number' not in body["prompt"]
    assert body["prompt"].split("EMAIL:\n<<<\n", 1)[1] == source + "\n>>>"
    assert "Do not use 1 for" in body["prompt"]
