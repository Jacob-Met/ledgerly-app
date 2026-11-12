"""Integration checks for the opt-in Ledgerly live pilot.

The AI endpoint is a local HTTP fixture. No PayPal or paid model service is called.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ledgerly.extract import RulesExtractor
from ledgerly.live import ModelEndpointError, OpenAICompatibleCompleter, main, run
from ledgerly.paypal import SandboxMock


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "01_simple_usd_hourly.txt"


class ModelHandler(BaseHTTPRequestHandler):
    reply = ""
    requests = []
    status = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.__class__.requests.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": json.loads(body),
        })
        if self.__class__.status != 200:
            self.send_response(self.__class__.status)
            self.end_headers()
            self.wfile.write(b'{"error":"rejected"}')
            return
        payload = json.dumps({"choices": [{"message": {"content": self.__class__.reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


class LocalModel:
    def __enter__(self):
        ModelHandler.requests = []
        ModelHandler.status = 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port) + "/v1/chat/completions"
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def options(**changes):
    args = {
        "source": str(FIXTURE),
        "mode": "preview",
        "extractor": "rules",
        "ai_url": "https://api.openai.com/v1/chat/completions",
        "ai_model": "",
        "ai_key_env": "LEDGERLY_AI_API_KEY",
        "ai_threshold": 0.75,
        "invoicer_name": "Fictional Studio",
        "invoicer_email": "studio@example.com",
        "send_after_review": False,
    }
    args.update(changes)
    return argparse.Namespace(**args)


class PilotTests(unittest.TestCase):
    def test_preview_uses_actual_invoice_agent_without_external_calls(self):
        lines = []
        mock = SandboxMock()
        code = run(options(), emit=lines.append, client=mock)
        self.assertEqual(code, 0)
        self.assertTrue(any("PAYMENT PREVIEW" in line for line in lines))
        self.assertTrue(any("No invoice sent" in line for line in lines))
        self.assertTrue(any(req[1] == "/v2/invoicing/invoices" for req in mock.requests))
        self.assertFalse(any("/send" in req[1] for req in mock.requests))

    def test_remote_ai_decline_does_not_send_client_text_or_make_invoice(self):
        source = FIXTURE.read_text()
        answer = RulesExtractor().extract(source).to_dict()
        with patch("ledgerly.live.OpenAICompatibleCompleter") as adapter:
            completer = adapter.return_value
            completer.url = "https://model.example.test/v1/chat/completions"
            completer.return_value = json.dumps(answer)
            seen, messages, mock = [], [], SandboxMock()
            code = run(
                options(extractor="ai", ai_model="remote-model",
                        ai_url="https://model.example.test/v1/chat/completions"),
                confirmation=lambda prompt: seen.append(prompt) or "",
                emit=messages.append, client=mock,
            )
        self.assertEqual(code, 2)
        self.assertEqual(len(seen), 1)
        self.assertIn("SHARE-WITH-MODEL", seen[0])
        self.assertTrue(any("REMOTE AI DISCLOSURE" in m for m in messages))
        self.assertTrue(any("no client text sent" in m for m in messages))
        self.assertEqual(completer.call_count, 0)
        self.assertEqual(mock.requests, [])

    def test_remote_ai_exact_consent_runs_once_and_preview_stays_truthful(self):
        source = FIXTURE.read_text()
        answer = RulesExtractor().extract(source).to_dict()
        with patch("ledgerly.live.OpenAICompatibleCompleter") as adapter:
            completer = adapter.return_value
            completer.url = "https://model.example.test/v1/chat/completions"
            completer.return_value = json.dumps(answer)
            prompts, messages, mock = [], [], SandboxMock()
            code = run(
                options(extractor="ai", ai_model="remote-model",
                        ai_url="https://model.example.test/v1/chat/completions"),
                confirmation=lambda prompt: prompts.append(prompt) or "SHARE-WITH-MODEL",
                emit=messages.append, client=mock,
            )
        self.assertEqual(code, 0, messages)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(completer.call_count, 1)
        self.assertTrue(any("PAYMENT PREVIEW" in m for m in messages))
        self.assertFalse(any("no external calls" in m for m in messages))
        self.assertTrue(any("/v2/invoicing/invoices" in row[1] for row in mock.requests))

    def test_hybrid_high_confidence_never_requests_remote_ai_consent(self):
        with patch("ledgerly.live.OpenAICompatibleCompleter") as adapter:
            completer = adapter.return_value
            completer.url = "https://model.example.test/v1/chat/completions"
            prompts, mock = [], SandboxMock()
            code = run(
                options(extractor="hybrid", ai_model="remote-model",
                        ai_url="https://model.example.test/v1/chat/completions"),
                confirmation=lambda prompt: prompts.append(prompt) or "",
                emit=lambda _: None, client=mock,
            )
        self.assertEqual(code, 0)
        self.assertEqual(prompts, [])
        self.assertEqual(completer.call_count, 0)
        self.assertTrue(mock.requests)

    def test_sandbox_preflight_refuses_before_private_ai_or_source_read(self):
        # A missing sandbox opt-in must not spend inference or disclose a job
        # to an otherwise correctly configured model endpoint.
        with LocalModel() as model:
            with patch.dict(os.environ, {"LEDGERLY_ALLOW_NETWORK": ""}):
                with patch("ledgerly.live._read_source", side_effect=AssertionError("source opened")):
                    code = run(
                        options(mode="sandbox", extractor="ai", ai_model="local-check",
                                ai_url=model.url), emit=lambda _: None,
                    )
            self.assertEqual(code, 2)
            self.assertEqual(ModelHandler.requests, [])

    def test_sandbox_missing_credentials_refuses_before_ai_contact(self):
        with LocalModel() as model:
            with patch.dict(os.environ, {
                    "LEDGERLY_ALLOW_NETWORK": "1", "PAYPAL_CLIENT_ID": "",
                    "PAYPAL_CLIENT_SECRET": ""}):
                code = run(options(mode="sandbox", extractor="ai",
                                   ai_model="local-check", ai_url=model.url),
                           emit=lambda _: None)
            self.assertEqual(code, 2)
            self.assertEqual(ModelHandler.requests, [])

    def test_sandbox_requires_real_confirmation_before_any_paypal_draft(self):
        lines, prompts = [], []
        mock = SandboxMock()

        def decline(prompt):
            prompts.append(prompt)
            return "create"

        with patch.dict(os.environ, {"LEDGERLY_ALLOW_NETWORK": "1"}):
            code = run(options(mode="sandbox"), emit=lines.append, confirmation=decline, client=mock)
        self.assertEqual(code, 0)
        self.assertEqual(mock.requests, [])
        self.assertEqual(len(prompts), 1)
        self.assertIn("No PayPal action taken.", lines)

    def test_explicit_create_leaves_draft_unsent_by_default(self):
        mock = SandboxMock()
        lines = []
        with patch.dict(os.environ, {"LEDGERLY_ALLOW_NETWORK": "1"}):
            code = run(options(mode="sandbox"), emit=lines.append, confirmation=lambda _: "CREATE", client=mock)
        self.assertEqual(code, 0)
        self.assertTrue(any("Draft " in line for line in lines))
        self.assertFalse(any("/send" in req[1] for req in mock.requests))

    def test_send_requires_second_invoice_specific_confirmation(self):
        mock = SandboxMock()
        prompts = []

        def confirm(prompt):
            prompts.append(prompt)
            if prompt.startswith("Type CREATE"):
                return "CREATE"
            if prompt.startswith("Type SEND "):
                return prompt.split("Type ", 1)[1].split(" to send", 1)[0]
            raise AssertionError("Unexpected prompt " + prompt)

        with patch.dict(os.environ, {"LEDGERLY_ALLOW_NETWORK": "1"}):
            code = run(options(mode="sandbox", send_after_review=True), confirmation=confirm, emit=lambda _: None, client=mock)
        self.assertEqual(code, 0)
        self.assertEqual(sum("/send" in path for _, path, _ in mock.requests), 1)
        self.assertEqual(len(prompts), 2)

    def test_sandbox_send_shows_exact_queued_terms_for_each_currency(self):
        mock, messages, prompts = SandboxMock(), [], []
        source = FIXTURE.with_name("04_multi_currency.txt")
        def confirm(prompt):
            prompts.append(prompt)
            if prompt.startswith("Type CREATE"):
                return "CREATE"
            if prompt.startswith("Type SEND ") and len(prompts) == 2:
                return prompt.split("Type ", 1)[1].split(" to send", 1)[0]
            return ""

        with patch.dict(os.environ, {"LEDGERLY_ALLOW_NETWORK": "1"}):
            code = run(options(source=str(source), mode="sandbox", send_after_review=True),
                       client=mock, emit=messages.append, confirmation=confirm)
        self.assertEqual(code, 0)
        send_reviews = [message for message in messages if message.startswith("OUTGOING SEND:")]
        self.assertEqual(len(send_reviews), 2)
        for review in send_reviews:
            self.assertIn("Recipient: priya@orbitlabs.example", review)
            self.assertIn("Invoice #:", review)
            self.assertIn("Terms:", review)
            self.assertIn("Note:", review)
            self.assertIn(" each", review)
        self.assertTrue(any("Currency: EUR" in review for review in send_reviews))
        self.assertTrue(any("Currency: USD" in review for review in send_reviews))
        self.assertEqual(sorted(invoice["status"] for invoice in mock.invoices.values()),
                         ["DRAFT", "SENT"])
        self.assertEqual(sum("/send" in row[1] for row in mock.requests), 1)

    def test_reported_deposit_cannot_be_recorded_by_send_only_confirmation(self):
        deposit_file = FIXTURE.with_name("07_partial_payment_deposit.txt")
        for approve, expect_record in ((False, False), (True, True)):
            mock = SandboxMock()
            messages = []
            def confirm(prompt):
                if prompt.startswith("Type CREATE"):
                    return "CREATE"
                if approve:
                    return prompt.split("Type ", 1)[1].split(" to authorize", 1)[0]
                return "SEND " + next(iter(mock.invoices))
            with patch.dict(os.environ, {"LEDGERLY_ALLOW_NETWORK": "1"}):
                code = run(
                    options(source=str(deposit_file), mode="sandbox", send_after_review=True),
                    emit=messages.append, client=mock, confirmation=confirm,
                )
            self.assertEqual(code, 0)
            self.assertTrue(any("AND records a prior external payment" in m for m in messages))
            self.assertEqual(
                sum(path.endswith("/payments") for _, path, _ in mock.requests),
                1 if expect_record else 0,
            )
            self.assertEqual(
                sum(path.endswith("/send") for _, path, _ in mock.requests),
                1 if expect_record else 0,
            )

    def test_ai_http_model_runs_once_and_uses_reviewed_values_for_draft(self):
        source = FIXTURE.read_text()
        answer = RulesExtractor().extract(source).to_dict()
        answer["confidence"] = 0.94
        ModelHandler.reply = json.dumps(answer)
        with LocalModel() as model:
            lines, mock = [], SandboxMock()
            with patch.dict(os.environ, {"LEDGERLY_AI_API_KEY": "canary-should-not-be-logged"}):
                code = run(options(extractor="ai", ai_url=model.url, ai_model="fixture-model"),
                           emit=lines.append, client=mock)
            self.assertEqual(code, 0, lines)
            self.assertEqual(len(ModelHandler.requests), 1)
            request = ModelHandler.requests[0]
            self.assertEqual(request["authorization"], "Bearer canary-should-not-be-logged")
            self.assertEqual(request["body"]["model"], "fixture-model")
            self.assertEqual(request["path"], "/v1/chat/completions")
            self.assertTrue(any("Extraction source: llm" in line for line in lines))
            self.assertNotIn("canary-should-not-be-logged", " ".join(lines))
            self.assertTrue(any(req[1] == "/v2/invoicing/invoices" for req in mock.requests))

    def test_model_endpoint_failure_is_safe_and_does_not_create_drafts(self):
        with LocalModel() as model:
            ModelHandler.status = 401
            lines, mock = [], SandboxMock()
            with patch.dict(os.environ, {"LEDGERLY_AI_API_KEY": "never-print-this-secret"}):
                code = run(options(extractor="ai", ai_url=model.url, ai_model="fixture-model"),
                           emit=lines.append, client=mock)
            self.assertEqual(code, 2)
            self.assertIn("HTTP 401", " ".join(lines))
            self.assertNotIn("never-print-this-secret", " ".join(lines))
            self.assertEqual(mock.requests, [])

    def test_ai_http_redirect_refused_before_forwarding_invoice_or_key(self):
        """A malicious or reconfigured model endpoint must not forward data."""
        class SinkHandler(BaseHTTPRequestHandler):
            received = []
            def do_GET(self):
                self.__class__.received.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"choices":[{"message":{"content":"{}"}}]}')
            do_POST = do_GET
            def log_message(self, *_):
                pass

        sink = ThreadingHTTPServer(("127.0.0.1", 0), SinkHandler)
        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:%d/collector" % sink.server_port)
                self.end_headers()
            def log_message(self, *_):
                pass

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        workers = [
            threading.Thread(target=sink.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True),
            threading.Thread(target=redirect.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True),
        ]
        for worker in workers:
            worker.start()
        try:
            model = OpenAICompatibleCompleter(
                "http://127.0.0.1:%d/v1/chat/completions" % redirect.server_port,
                "fictional-model", api_key="fictional-canary-key",
            )
            with self.assertRaises(ModelEndpointError):
                model("PRIVATE SOURCE: deliver original fictional $850 job")
            self.assertEqual(SinkHandler.received, [], "Redirected private data/authorization")
        finally:
            for server in (sink, redirect):
                server.shutdown()
                server.server_close()
            for worker in workers:
                worker.join(timeout=3)

    def test_insecure_remote_model_endpoint_is_refused(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleCompleter("http://example.com/v1/chat/completions", "model")
        with self.assertRaises(ValueError):
            OpenAICompatibleCompleter("https://user:password@example.com/v1/chat/completions", "model")

    def test_source_size_limit_precedes_external_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "huge.txt"
            path.write_bytes(b"x" * 128_001)
            mock, lines = SandboxMock(), []
            code = run(options(source=str(path)), emit=lines.append, client=mock)
            self.assertEqual(code, 2)
            self.assertEqual(mock.requests, [])

    def test_ai_invents_high_rate_and_cannot_create_any_invoice(self):
        source = FIXTURE.read_text()
        answer = RulesExtractor().extract(source).to_dict()
        answer["line_items"][0]["unit_price"] = "9000"
        answer["confidence"] = 0.99
        answer["issues"] = []
        ModelHandler.reply = json.dumps(answer)
        with LocalModel() as model:
            messages, mock = [], SandboxMock()
            code = run(options(extractor="ai", ai_url=model.url, ai_model="fixture-model"),
                       emit=messages.append, client=mock)
        self.assertEqual(code, 2, messages)
        self.assertTrue(any("unquoted monetary amount" in line for line in messages))
        self.assertTrue(any("conflict with" in line for line in messages))
        self.assertEqual(mock.requests, [])

    def test_ai_quantity_inflation_is_not_a_valid_source_number(self):
        source = FIXTURE.read_text()
        answer = RulesExtractor().extract(source).to_dict()
        answer["line_items"][0]["qty"] = "9000"
        answer["confidence"] = 0.99
        ModelHandler.reply = json.dumps(answer)
        with LocalModel() as model:
            messages, mock = [], SandboxMock()
            code = run(options(extractor="ai", ai_url=model.url, ai_model="fixture-model"),
                       emit=messages.append, client=mock)
        self.assertEqual(code, 2, messages)
        self.assertTrue(any("unquoted quantity" in line for line in messages))
        self.assertEqual(mock.requests, [])

    def test_ai_duplicate_line_item_cannot_reuse_one_source_price(self):
        source = FIXTURE.read_text()
        answer = RulesExtractor().extract(source).to_dict()
        answer["line_items"].append(dict(answer["line_items"][0]))
        answer["confidence"] = 0.99
        ModelHandler.reply = json.dumps(answer)
        with LocalModel() as model:
            messages, mock = [], SandboxMock()
            code = run(options(extractor="ai", ai_url=model.url, ai_model="fixture-model"),
                       emit=messages.append, client=mock)
        self.assertEqual(code, 2, messages)
        self.assertTrue(any("unquoted monetary amount" in line for line in messages))
        self.assertEqual(mock.requests, [])

    def test_ai_fake_deposit_without_source_evidence_is_refused(self):
        source = FIXTURE.read_text()
        answer = RulesExtractor().extract(source).to_dict()
        answer["amount_paid"] = "500"
        answer["confidence"] = 0.99
        ModelHandler.reply = json.dumps(answer)
        with LocalModel() as model:
            messages, mock = [], SandboxMock()
            code = run(options(extractor="ai", ai_url=model.url, ai_model="fixture-model"),
                       emit=messages.append, client=mock)
        self.assertEqual(code, 2, messages)
        self.assertTrue(any("prior payment" in line.lower() for line in messages))
        self.assertEqual(mock.requests, [])

    def test_ai_quoted_old_amount_does_not_ground_new_price(self):
        source = FIXTURE.read_text() + "\n> Old request: copywriting at $9000\n"
        answer = RulesExtractor().extract(source).to_dict()
        answer["line_items"][0]["unit_price"] = "9000"
        answer["confidence"] = 0.99
        ModelHandler.reply = json.dumps(answer)
        with LocalModel() as model:
            messages, mock = [], SandboxMock()
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "request.txt"
                path.write_text(source)
                code = run(options(source=str(path), extractor="ai", ai_url=model.url, ai_model="fixture-model"),
                           emit=messages.append, client=mock)
        self.assertEqual(code, 2, messages)
        self.assertTrue(any("unquoted monetary amount" in line for line in messages))
        self.assertEqual(mock.requests, [])

    def test_sandbox_refuses_missing_network_optin(self):
        lines, mock = [], SandboxMock()
        with patch.dict(os.environ, {"LEDGERLY_ALLOW_NETWORK": ""}):
            code = run(options(mode="sandbox"), confirmation=lambda _: "CREATE",
                       emit=lines.append, client=mock)
        self.assertEqual(code, 2)
        self.assertEqual(mock.requests, [])
        self.assertIn("LEDGERLY_ALLOW_NETWORK=1", " ".join(lines))

    def test_cli_rejects_send_in_offline_preview(self):
        with self.assertRaises(SystemExit) as failure:
            main([str(FIXTURE), "--send-after-review"])
        self.assertEqual(failure.exception.code, 2)

    def test_ai_unit_price_cannot_become_a_reported_prior_payment(self):
        # Missing terms reduce rules confidence below 0.95. The source mentions
        # an $85 work rate but says nothing about money already received.
        source = FIXTURE.read_text().replace("Net 15", "")
        reference = RulesExtractor().extract(source)
        self.assertLess(reference.confidence, 0.95)
        self.assertEqual(reference.amount_paid, 0)
        answer = reference.to_dict()
        answer["amount_paid"] = str(reference.line_items[0].unit_price)
        answer["confidence"] = 0.99
        ModelHandler.reply = json.dumps(answer)
        with tempfile.TemporaryDirectory() as folder, LocalModel() as model:
            path = Path(folder) / "email.txt"
            path.write_text(source)
            messages, mock = [], SandboxMock()
            code = run(options(source=str(path), extractor="ai", ai_url=model.url,
                               ai_model="fixture-model"), emit=messages.append, client=mock)
        self.assertEqual(code, 2, messages)
        self.assertTrue(any("prior payment" in line.lower() for line in messages), messages)
        self.assertEqual(mock.requests, [])

    def test_ai_can_retain_a_genuinely_reported_deposit(self):
        source = FIXTURE.with_name("07_partial_payment_deposit.txt").read_text()
        reference = RulesExtractor().extract(source)
        self.assertGreater(reference.amount_paid, 0)
        answer = reference.to_dict()
        answer["confidence"] = 0.99
        ModelHandler.reply = json.dumps(answer)
        with LocalModel() as model:
            messages, mock = [], SandboxMock()
            code = run(options(source=str(FIXTURE.with_name("07_partial_payment_deposit.txt")),
                               extractor="ai", ai_url=model.url,
                               ai_model="fixture-model"), emit=messages.append, client=mock)
        self.assertEqual(code, 0, messages)
        self.assertTrue(any(path == "/v2/invoicing/invoices" for _, path, _ in mock.requests))


class PayPalHandler(BaseHTTPRequestHandler):
    """Loopback fake for the real HttpPayPalClient's OAuth and Invoicing v2 wire calls."""

    requests = []
    draft = None

    def _respond(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(size)
        self.__class__.requests.append((self.command, self.path, self.headers.get("Authorization")))
        if self.path == "/v1/oauth2/token":
            self._respond(200, {"access_token": "stub-bearer", "expires_in": 3600})
        elif self.path == "/v2/invoicing/generate-next-invoice-number":
            self._respond(200, {"invoice_number": "STUB-0001"})
        elif self.path == "/v2/invoicing/invoices":
            self.__class__.draft = json.loads(body)
            self._respond(201, {"id": "INV-STUB-001"})
        elif self.path == "/v2/invoicing/invoices/INV-STUB-001/send":
            self._respond(200, {"href": "https://www.sandbox.paypal.com/invoice/payerView/details/INV-STUB-001"})
        else:
            self._respond(404, {"name": "RESOURCE_NOT_FOUND"})

    def do_GET(self):
        self.__class__.requests.append((self.command, self.path, self.headers.get("Authorization")))
        if self.path == "/v2/invoicing/invoices/INV-STUB-001":
            from decimal import Decimal
            draft = self.__class__.draft
            total = sum((Decimal(item["quantity"]) * Decimal(item["unit_amount"]["value"]) for item in draft["items"]), Decimal("0"))
            money = {"currency_code": draft["detail"]["currency_code"], "value": str(total)}
            self._respond(200, {"id": "INV-STUB-001", "status": "DRAFT", **draft,
                                "amount": money, "due_amount": money})
        else:
            self._respond(404, {"name": "RESOURCE_NOT_FOUND"})

    def log_message(self, *_):
        pass


class PayPalWireTests(unittest.TestCase):
    def test_oauth_draft_current_read_and_human_approved_send(self):
        from ledgerly.paypal import HttpPayPalClient

        PayPalHandler.requests, PayPalHandler.draft = [], None
        server = ThreadingHTTPServer(("127.0.0.1", 0), PayPalHandler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        try:
            with patch.dict(os.environ, {"LEDGERLY_ALLOW_NETWORK": "1"}):
                paypal = HttpPayPalClient("stub-client", "stub-secret", base_url=base)
                prompts, lines = [], []

                def approve(prompt):
                    prompts.append(prompt)
                    if prompt.startswith("Type CREATE"):
                        return "CREATE"
                    if prompt.startswith("Type SEND "):
                        return prompt.split("Type ", 1)[1].split(" to send", 1)[0]
                    raise AssertionError(prompt)

                result = run(options(mode="sandbox", send_after_review=True),
                             confirmation=approve, client=paypal, emit=lines.append)
            self.assertEqual(result, 0, lines)
            paths = [(method, path) for method, path, _ in PayPalHandler.requests]
            self.assertIn(("POST", "/v1/oauth2/token"), paths)
            self.assertIn(("POST", "/v2/invoicing/generate-next-invoice-number"), paths)
            self.assertIn(("POST", "/v2/invoicing/invoices"), paths)
            self.assertIn(("GET", "/v2/invoicing/invoices/INV-STUB-001"), paths)
            self.assertIn(("POST", "/v2/invoicing/invoices/INV-STUB-001/send"), paths)
            self.assertEqual(len(prompts), 2)
            self.assertEqual(PayPalHandler.draft["detail"]["currency_code"], "USD")
            self.assertEqual(PayPalHandler.draft["primary_recipients"][0]["billing_info"]["email_address"],
                             "maya.chen@brightfern.example")
            self.assertTrue(any("PayPal Sandbox send accepted" in line for line in lines))
            self.assertTrue(all("stub-secret" not in line for line in lines))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

if __name__ == "__main__":
    unittest.main()


def test_ambiguous_quantity_cli_review_does_not_crash_or_create_invoice(capsys):
    """A common ambiguous quantity must produce a review path, not a TypeError."""
    from ledgerly.live import main
    fixture = str(Path(__file__).resolve().parents[1] / "fixtures" / "06_ambiguous_qty.txt")
    assert main([fixture]) == 2
    printed = capsys.readouterr().out
    assert "unconfirmed × Revisions" in printed
    assert "USD unavailable — confirm every quantity" in printed
    assert "REVIEW REQUIRED: no drafts created" in printed
    assert "Traceback" not in printed
