"""PayPal account-wide, read-only overdue detection using current invoice GETs."""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest

from ledgerly.agent import Agent
from ledgerly.followup import main, scan_receivables
from ledgerly.paypal import HttpPayPalClient, Response, SandboxMock

FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures/01_simple_usd_hourly.txt").read_text()
OCT_1 = date(2026, 10, 1)
NOV_9 = date(2026, 11, 9)


def seed(number=3):
    provider = SandboxMock(now=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    agent = Agent(provider, {"name": "Freelancer", "email_address": "studio@example.com"},
                  today=lambda: OCT_1)
    ids = []
    for _ in range(number):
        result = agent.tool_create_invoice(FIXTURE)
        assert result["ok"]
        row = result["invoices"][0]
        ids.append(row["invoice_id"])
        agent.approve(row["approval_id"])
    return provider, ids


class PagedProvider:
    def __init__(self, backing: SandboxMock, ids=None, **changes):
        self.backing = backing
        self.ids = list(ids if ids is not None else backing.invoices)
        self.calls = []
        self.changes = changes

    def list_invoices(self, *, page, page_size, total_required):
        self.calls.append(("LIST", page, page_size, total_required))
        assert total_required is True
        total_pages = (len(self.ids) + page_size - 1) // page_size
        chunk = self.ids[(page - 1) * page_size: page * page_size]
        body = {"items": [{"id": inv} for inv in chunk],
                "total_pages": total_pages, "total_items": len(self.ids)}
        body.update(self.changes)
        return Response(200, body)

    def get_invoice(self, invoice_id):
        self.calls.append(("GET", invoice_id))
        return self.backing.get_invoice(invoice_id)


def test_discover_from_multiple_pages_and_fetch_fresh_invoice_facts():
    backing, ids = seed(3)
    backing.simulate_payer_payment(ids[1])
    client = PagedProvider(backing)
    lines = []
    code = scan_receivables(client=client, today=NOV_9, page_size=2, max_pages=3,
                            output_json=True, write=lines.append)
    assert code == 0
    result = json.loads(lines[0])
    assert result["complete"]
    assert result["invoice_ids_discovered"] == 3
    assert result["invoices_validated"] == 3
    assert result["closed_count"] == 1
    assert len(result["overdue"]) == 2
    assert {x["invoice_id"] for x in result["overdue"]} == {ids[0], ids[2]}
    assert all(x["balance"] == "1320" for x in result["overdue"])
    assert all(x["days_overdue"] == 24 for x in result["overdue"])
    assert sum(x[0] == "LIST" for x in client.calls) == 2
    assert sum(x[0] == "GET" for x in client.calls) == 3


def test_page_cap_must_never_pretend_to_find_every_overdue_invoice():
    backing, ids = seed(3)
    client = PagedProvider(backing)
    lines = []
    code = scan_receivables(client=client, today=NOV_9, page_size=2, max_pages=1,
                            output_json=True, write=lines.append)
    result = json.loads(lines[0])
    assert code == 3 and result["complete"] is False
    assert result["invoice_ids_discovered"] == 2
    assert len(result["overdue"]) == 2


def test_stale_page_count_is_falsifiable_and_never_reports_complete():
    backing, ids = seed(2)
    client = PagedProvider(backing, total_items=3)
    lines = []
    assert scan_receivables(client=client, today=NOV_9, page_size=5,
                            output_json=True, write=lines.append) == 3
    result = json.loads(lines[0])
    assert not result["complete"]
    assert any("differs" in x["reason"] for x in result["errors"])


def test_duplicate_ids_across_pages_flag_incomplete_without_double_count():
    backing, ids = seed(2)
    client = PagedProvider(backing, ids=[ids[0], ids[0]])
    lines = []
    assert scan_receivables(client=client, today=NOV_9, page_size=1, max_pages=3,
                            output_json=True, write=lines.append) == 3
    result = json.loads(lines[0])
    assert len(result["overdue"]) == 1
    assert any("multiple pages" in x["reason"] for x in result["errors"])


def test_paid_only_account_has_no_false_overdue_rows():
    backing, ids = seed(1)
    backing.simulate_payer_payment(ids[0])
    client = PagedProvider(backing)
    lines = []
    assert scan_receivables(client=client, today=NOV_9,
                            output_json=True, write=lines.append) == 0
    result = json.loads(lines[0])
    assert result["complete"]
    assert result["overdue"] == []
    assert result["closed_count"] == 1


def test_provider_read_error_returns_partial_results_not_a_clean_all_clear():
    backing, ids = seed(2)
    client = PagedProvider(backing)
    original = client.get_invoice
    def fail_second(invoice_id):
        if invoice_id == ids[1]:
            raise ValueError("bad provider value")
        return original(invoice_id)
    client.get_invoice = fail_second
    lines = []
    assert scan_receivables(client=client, today=NOV_9,
                            output_json=True, write=lines.append) == 3
    data = json.loads(lines[0])
    assert not data["complete"]
    assert len(data["overdue"]) == 1
    assert data["errors"][0]["invoice_id"] == ids[1]


def test_bad_page_and_invalid_budget_refuse_silently_skipping_results():
    backing, ids = seed(1)
    bad = PagedProvider(backing, items=None)
    with pytest.raises(ValueError, match="valid items"):
        scan_receivables(client=bad)
    with pytest.raises(ValueError, match="page-size"):
        scan_receivables(client=bad, page_size=101)
    with pytest.raises(ValueError, match="max-pages"):
        scan_receivables(client=bad, max_pages=0)
    assert all(call[0] != "GET" for call in bad.calls)


def test_cli_scan_is_read_only_and_refuses_invoice_send_flags():
    with pytest.raises(SystemExit) as error:
        main(["INV2-TEST", "--scan", "--remind"])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        main(["--scan", "--state-file", "/tmp/data.json"])
    assert error.value.code == 2


def test_text_output_warns_incomplete_and_has_no_remind_calls():
    backing, ids = seed(2)
    client = PagedProvider(backing)
    lines = []
    assert scan_receivables(client=client, today=NOV_9, page_size=1, max_pages=1,
                            write=lines.append) == 3
    assert any("INCOMPLETE SCAN" in line for line in lines)
    assert any("Read-only: no reminders were sent" in line for line in lines)
    assert all(call[0] != "REMIND" for call in client.calls)


def test_full_cli_scan_calls_real_inventory_path_and_prints_json(capsys):
    backing, ids = seed(1)
    client = PagedProvider(backing)
    with patch("ledgerly.followup._sandbox_client", return_value=client):
        assert main(["--scan", "--json", "--page-size", "2", "--max-pages", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["complete"] is True
    assert result["overdue"][0]["invoice_id"] == ids[0]
    assert all(call[0] in ("LIST", "GET") for call in client.calls)


def test_empty_merchant_inventory_is_a_true_empty_scan():
    backing, _ = seed(0)
    client = PagedProvider(backing)
    outputs = []
    assert scan_receivables(client=client, today=NOV_9,
                            output_json=True, write=outputs.append) == 0
    data = json.loads(outputs[0])
    assert data["complete"]
    assert data["invoice_ids_discovered"] == 0
    assert data["overdue"] == [] and data["open_other"] == []
    assert client.calls == [("LIST", 1, 50, True)]


def test_real_http_paypal_adapter_issues_documented_list_and_detail_get():
    backing, ids = seed(1)
    class Handler(BaseHTTPRequestHandler):
        seen = []
        def do_POST(self):
            self.__class__.seen.append((self.command, self.path))
            if self.path == "/v1/oauth2/token":
                payload = {"access_token": "safe-test-token", "expires_in": 3600}
                self._send(200, payload)
            else:
                self._send(404, {})
        def do_GET(self):
            self.__class__.seen.append((self.command, self.path))
            part = urlsplit(self.path)
            if part.path == "/v2/invoicing/invoices":
                query = parse_qs(part.query)
                assert query["page"] == ["1"]
                assert query["page_size"] == ["2"]
                assert query["total_required"] == ["true"]
                self._send(200, {"items": [{"id": ids[0]}], "total_pages": 1, "total_items": 1})
            elif part.path == "/v2/invoicing/invoices/" + ids[0]:
                self._send(200, backing.get_invoice(ids[0]).body)
            else:
                self._send(404, {})
        def _send(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass

    Handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        with patch.dict(os.environ, {"LEDGERLY_ALLOW_NETWORK": "1"}):
            client = HttpPayPalClient("safe-client", "safe-secret",
                                     base_url="http://127.0.0.1:" + str(server.server_port))
            lines = []
            assert scan_receivables(client=client, today=NOV_9, page_size=2, max_pages=3,
                                    output_json=True, write=lines.append) == 0
        result = json.loads(lines[0])
        assert result["complete"] and len(result["overdue"]) == 1
        assert ("POST", "/v1/oauth2/token") in Handler.seen
        assert any(method == "GET" and path.startswith("/v2/invoicing/invoices?") for method,path in Handler.seen)
        assert ("GET", "/v2/invoicing/invoices/"+ids[0]) in Handler.seen
        assert not any("/remind" in p for _,p in Handler.seen)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
