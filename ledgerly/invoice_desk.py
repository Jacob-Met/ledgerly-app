"""The maintained browser connected to the real, durable Sandbox invoice operator.

Browser extraction/review uses the same bridge as the offline browser. Provider
operations use operator.make_agent and DurableAgent, with no browser credentials,
mock payment controls, automatic sends, or retries after a missing response.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from .operator import make_agent, OllamaCompletion, LlamaCppCompletion
from .recovery import exclusive_state_lock
from .creation_recovery import CreationJournal
from .extract import HybridExtractor, RulesExtractor, source_references


def load_browser_bridge(directory: Path):
    """Load the packaged, shared browser Python modules, never remote code."""
    directory = Path(directory).resolve()
    for name in ("review", "invoice_details"):
        spec = importlib.util.spec_from_file_location(name, directory / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    spec = importlib.util.spec_from_file_location("ledgerly_browser_bridge", directory / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    return bridge


class ObservedClient:
    """Retain read/draft records for the existing invoice-details projection."""
    def __init__(self, client, journal, records, provider):
        self.inner = client
        self.journal = journal
        self.context = {}
        self.records = records
        self.provider = provider
        self.invoices = {identity: deepcopy(saved["record"]) for identity, saved in records.items()}
        self.calls = 0

    def __getattr__(self, name):
        value = getattr(self.inner, name)
        if not callable(value):
            return value
        def invoke(*args, **kwargs):
            attempt = self.journal.begin(args[0], self.context) if name == 'create_draft_invoice' else None
            self.calls += 1
            response = value(*args, **kwargs)
            if attempt is not None:
                self.journal.receipt(attempt, response)
            body = getattr(response, "body", None)
            # Only full invoice observations belong here. Payment receipts also
            # have IDs and must never replace the invoice with a partial response.
            if (name in ("get_invoice", "create_draft_invoice")
                    and 200 <= getattr(response, "status", 0) < 300
                    and isinstance(body, dict) and isinstance(body.get("id"), str)
                    and isinstance(body.get("items"), list) and isinstance(body.get("detail"), dict)
                    and (name != "get_invoice" or body["id"] == args[0])):
                self.invoices[body["id"]] = deepcopy(body)
                self.records[body["id"]] = {
                    "record": deepcopy(body),
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "provider": self.provider,
                }
            return response
        return invoke


class NativeSession:
    """One native agent, serialized requests, and per-browser review sessions."""
    def __init__(self, agent, bridge, *, provider="PayPal Sandbox"):
        self.agent = agent
        self.bridge = bridge
        self.provider = provider
        completion = getattr(getattr(agent.extractor, "fallback", None), "complete", None)
        self.local_model = completion.model if isinstance(completion, (OllamaCompletion, LlamaCppCompletion)) else None
        agent.observation_provider = provider
        if not hasattr(agent, 'state_store'):
            raise ValueError('The native invoice desk requires the durable operator state file.')
        path = agent.state_store.path
        # Browser recovery belongs to this ledger/account, not the rotating
        # HTTP access token. This opaque namespace is never authorization.
        # Moving the state path intentionally starts a separate browser cache.
        identity = json.dumps(["ledgerly-native-workspace-v1", str(path.resolve()),
                               agent.invoicer["email_address"], provider],
                              separators=(",", ":")).encode()
        self.workspace_id = hashlib.sha256(identity).hexdigest()
        self.journal = CreationJournal(path.with_name(path.name + '.creations.json'), agent.invoicer['email_address'])
        self.journal.track(agent)  # The agent was freshly restored from its checkpoint.
        self.observed = ObservedClient(agent.client._inner, self.journal, agent.invoice_records, provider)
        agent.client._inner = self.observed
        self.extractor = bridge.ReviewableExtractor()
        self.extractor.rules = agent.extractor
        agent.extractor = self.extractor
        self.lock = threading.RLock()
        self.sessions = {}
        self.analysis_jobs = {}
        self.active_analysis = None
        # Pending draft contents are retained by the existing durable operator.
        # No provider GET is needed merely to reopen the local workspace.
        for action in agent.pending.values():
            invoice = action.payload.get("invoice")
            if isinstance(invoice, dict) and action.invoice_id not in self.observed.invoices:
                self.observed.invoices[action.invoice_id] = {**deepcopy(invoice), "id": action.invoice_id}

    def snapshot(self):
        pending = self.agent.list_pending()
        for saved in self.agent.pending.values():
            invoice = saved.payload.get('invoice')
            if saved.kind == 'send_invoice' and isinstance(invoice, dict) and saved.invoice_id not in self.observed.invoices:
                self.observed.invoices[saved.invoice_id] = {**deepcopy(invoice), 'id': saved.invoice_id}
        for identity, record in self.observed.invoices.items():
            entry = self.agent.ledger.get(identity)
            if entry is not None:
                record["status"] = entry.status
                record.setdefault("amount", {"currency_code": entry.currency, "value": str(entry.total)})
                record["due_amount"] = {"currency_code": entry.currency, "value": str(entry.balance)}
        for action in pending:
            action["confirmation"] = self.confirmation(action)
        ledger = []
        for entry in self.agent.ledger.values():
            row = entry.to_dict()
            if entry.status == "DRAFT" and not row["due_on"] and entry.invoice_due_on is not None:
                row["due_on"] = entry.invoice_due_on.isoformat()
            ledger.append(row)
        details = self.bridge.snapshot_invoice_details(self.observed.invoices, self.agent.ledger)
        for row in details:
            saved = self.agent.invoice_records.get(row["invoice_id"])
            # A successful full-provider observation has its own retained time
            # and source. Reopening or a failed refresh must not invent freshness.
            if saved is not None and row["record"] is not None:
                row["observation"] = {key: saved[key] for key in ("observed_at", "provider")}
        return {
            "today": self.agent.today().isoformat(),
            "ledger": ledger,
            "invoice_details": details,
            "invoice_requests": [entry.invoice_id for entry in self.agent.ledger.values()
                                 if self.journal.source_for(entry.invoice_id) is not None],
            "pending": pending,
            "creation_attempts": self.journal.pending(),
            "decisions": [a.to_dict() for a in self.agent.pending.values() if a.status != "PENDING"],
            "completed_reviews": [a.to_dict() for a in self.agent.pending.values() if a.status != "PENDING"],
            "audit": [{k: row[k] for k in ("at", "event", "action", "kind", "invoice", "ok")
                       if k in row} for row in [row for row in self.agent.audit if row.get("event") != "tool" or row.get("ok") is False][-18:]],
            "mock_requests": 0,
            "external_calls": self.observed.calls if self.provider == "PayPal Sandbox" else 0,
            "provider_operations": self.observed.calls,
            "can_replay": False,
            "native_operator": True,
            "provider": self.provider,
            "durable": hasattr(self.agent, "state_store"),
        }

    def confirmation(self, action):
        if action["kind"] == "send_invoice":
            invoice = action["payload"]["invoice"]
            entry = self.agent.ledger[action["invoice_id"]]
            prefix = "SEND+RECORD " if entry.prepaid > 0 else "SEND "
            return prefix + invoice["detail"]["invoice_number"]
        if action["kind"] == "update_invoice":
            return "CORRECT " + action["invoice_id"]
        if action["kind"] == "cancel_invoice":
            return "CANCEL " + action["invoice_id"]
        if action["kind"] == "send_reminder":
            return "REMIND " + action["invoice_id"]
        raise ValueError("This saved action cannot be sent from the invoice desk.")

    def browser(self, session_id):
        if session_id not in self.sessions:
            if len(self.sessions) >= 64:
                # Retire the oldest browser review rather than stopping a long-running desk.
                # Its stale approval token remains invalid; saved ledger decisions are unaffected.
                self.sessions.pop(next(iter(self.sessions)))
            native = self
            class ReviewSession(self.bridge.Demo):
                statement_source = "saved-sandbox" if native.provider == "PayPal Sandbox" else "synthetic-provider"
                def __init__(self):
                    self.clock = SimpleNamespace(day=date.today())
                    self.mock = native.observed
                    self.extractor = native.extractor
                    self.agent = native.agent
                    self.last_event = None
                    self.analysis_text = None
                    self.analysis = None
                    self.analysis_result = None
                    self.review = None
                    self.ai_requires_review = False
                    self.analysis_revision = 0

                def draft(self, text):
                    result = self.agent.tool_create_invoice(text)
                    native.journal.track(self.agent, [row["invoice_id"] for row in result.get("invoices", [])])
                    if not result.get("ok"):
                        failure = result.get("draft_failure", {})
                        return {"creation_incomplete": True, "invoices": result.get("invoices", []),
                                "draft_failure": {key: failure[key] for key in ("currency", "invoice_number", "stage", "outcome") if key in failure},
                                "unattempted_currencies": result.get("unattempted_currencies", []),
                                "message": "Draft creation did not complete. Inspect the retained creation attempt and confirmed invoices; do not recreate this job."}
                    return {"final": f"Created {len(result['invoices'])} unsent {native.provider} draft(s). Review each send separately.",
                            "pending": self.agent.list_pending(), "created": result["invoices"]}

                def snapshot(self):
                    return native.snapshot()
            self.sessions[session_id] = ReviewSession()
        return self.sessions[session_id]

    def _analysis_result(self, extraction, text):
        analysis = {**extraction.to_dict(), "source_references": source_references(extraction, text)}
        result = {**analysis, "review_currencies": sorted(self.bridge.PAYPAL_CURRENCIES),
                  "totals_by_currency": self.bridge.Demo.totals(extraction),
                  "financials_by_currency": self.bridge.Demo.financials(extraction),
                  "line_amounts": self.bridge.Demo.line_amounts(extraction),
                  "source_amount_requirements": self.bridge.source_amount_requirements(analysis)}
        # Finish materializing the proposal before changing a checked invoice.
        json.dumps(result, allow_nan=False)
        return analysis, result

    @staticmethod
    def _analysis_text(request):
        text = request.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Paste or load a request before local analysis.")
        return text

    def _analysis_public(self, job):
        elapsed = (job.get("finished_at") or time.monotonic()) - job["started_at"]
        value = {"id": job["id"], "state": job["state"],
                 "elapsed_seconds": round(elapsed, 1), "message": job.get("message", ""),
                 "model_skipped_for_review": job.get("model_skipped_for_review", False),
                 "failure_kind": job.get("failure_kind")}
        result = {"analysis_job": value}
        if job["state"] in ("ready", "applied"):
            result["analysis"] = deepcopy(job["result"])
        return result

    def _analysis_job(self, session_id, job_id):
        job = self.analysis_jobs.get(session_id)
        if job is None or job["id"] != job_id:
            raise ValueError("This analysis is no longer available in this browser. Your saved invoices are unchanged.")
        return job

    def _retire_analysis(self, session_id, browser):
        browser.analysis_revision += 1
        job = self.analysis_jobs.get(session_id)
        if job and job["state"] in ("running", "ready", "applied"):
            job["state"] = "dismissed"
            job["message"] = "Current invoice changed. The earlier analysis will not replace it."

    def _start_analysis(self, request, session_id, browser):
        text = self._analysis_text(request)
        identity = request.get("job_id")
        if not isinstance(identity, str) or not identity or len(identity) > 100:
            raise ValueError("Start local analysis with its browser request identifier.")
        prefer_model = request.get("prefer_model", False)
        if not isinstance(prefer_model, bool):
            raise ValueError("Choose whether this is an explicit local AI request.")
        previous = self.analysis_jobs.get(session_id)
        if previous and previous["id"] == identity:
            if previous["text"] != text or previous.get("prefer_model", False) != prefer_model:
                raise ValueError("This analysis identifier already belongs to another request.")
            return self._analysis_public(previous)
        extractor = self.extractor.rules
        immediate = None
        if prefer_model:
            if isinstance(extractor, HybridExtractor):
                first = extractor.primary.extract(text)
                if extractor.human_decision_required(first):
                    immediate = self._analysis_result(first, text)
                elif extractor.fallback is None:
                    raise ValueError("No local AI model is configured. Continue with the invoice fields.")
                else:
                    extractor = extractor.fallback
            elif isinstance(extractor, RulesExtractor):
                raise ValueError("No local AI model is configured. Continue with the invoice fields.")
        if self.active_analysis is not None and immediate is None:
            # A model request occupies the model, not the deterministic parser.
            # Keep the configured hybrid threshold and never invoke a second
            # fallback while the original inference is still finishing.
            if not prefer_model and isinstance(extractor, HybridExtractor) and isinstance(extractor.primary, RulesExtractor):
                first = extractor.primary.extract(text)
                if extractor.primary_ready(first):
                    immediate = self._analysis_result(first, text)
            elif not prefer_model and isinstance(extractor, RulesExtractor):
                immediate = self._analysis_result(extractor.extract(text), text)
            if immediate is None:
                raise ValueError("The previous local analysis is still finishing. You can keep editing and use checked invoice fields meanwhile.")
        self._retire_analysis(session_id, browser)
        job = {"id": identity, "text": text, "prefer_model": prefer_model, "state": "running",
               "revision": browser.analysis_revision, "started_at": time.monotonic(),
               "model_skipped_for_review": bool(prefer_model and immediate is not None)}
        self.analysis_jobs[session_id] = job
        if immediate is not None:
            job.update(state="ready", analysis=immediate[0], result=immediate[1],
                       finished_at=time.monotonic())
            return self._analysis_public(job)
        self.active_analysis = job
        # Use the configured base extractor, not the temporary human-review
        # override that another browser can use while creating its invoice.

        def run():
            try:
                extracted = extractor.extract(text)
                if prefer_model and (not extracted.line_items or any(
                        issue.message.startswith("LLM output unusable:") for issue in extracted.issues)):
                    raise ValueError("The model did not return usable invoice work.")
                analysis, result = self._analysis_result(extracted, text)
                with self.lock:
                    if job["state"] == "running":
                        job.update(state="ready", analysis=analysis, result=result)
            except Exception as exc:
                with self.lock:
                    if job["state"] == "running":
                        job.update(state="failed", failure_kind="timeout" if isinstance(exc, TimeoutError) else "unavailable", message=(
                            "Local analysis did not finish (" + type(exc).__name__ +
                            "). Your current invoice fields are kept. No provider request was made by this analysis."))
            finally:
                with self.lock:
                    job["finished_at"] = time.monotonic()
                    if self.active_analysis is job:
                        self.active_analysis = None

        worker = threading.Thread(target=run, name="ledgerly-local-analysis", daemon=True)
        try:
            worker.start()
        except Exception:
            self.active_analysis = None
            job.update(state="failed", finished_at=time.monotonic(),
                       message="Local analysis could not start. Your current invoice fields are kept.")
            raise
        return self._analysis_public(job)

    def _apply_analysis(self, request, session_id, browser):
        job = self._analysis_job(session_id, request.get("job_id"))
        if job["state"] == "applied" and job.get("applied_revision") == browser.analysis_revision:
            return deepcopy(job["result"])
        if job["state"] != "ready" or job["revision"] != browser.analysis_revision:
            raise ValueError("This analysis cannot replace the current invoice. Analyze the current request again.")
        browser.analysis_text = job["text"]
        browser.analysis = deepcopy(job["analysis"])
        browser.analysis_result = deepcopy(job["result"])
        browser.review = None
        browser.ai_requires_review = False
        browser.analysis_revision += 1
        job.update(state="applied", applied_revision=browser.analysis_revision)
        return deepcopy(job["result"])

    def _analyze_synchronously(self, request, browser, *, rules_only=False):
        # Keep compatibility for direct callers while preserving their current
        # checked fields if extraction fails. Browser clients use the background
        # path so a slow model never holds the operator lock.
        text = self._analysis_text(request)
        extractor = RulesExtractor() if rules_only else self.extractor.rules
        analysis, result = self._analysis_result(extractor.extract(text), text)
        browser.analysis_text = text
        browser.analysis = analysis
        browser.analysis_result = deepcopy(result)
        browser.review = None
        browser.ai_requires_review = False
        return result

    def dispatch(self, request, session_id):
        with self.lock:
            try:
                if not isinstance(request, dict):
                    raise ValueError("An invoice action object is required.")
                action = request.get("action")
                browser = self.browser(session_id)
                browser.clock.day = self.agent.today()
                if action == "init":
                    result = {"ready": True, "engine": "Native Python / " + self.provider,
                              "message": "Saved invoices are open. Review each provider action before confirming it."}
                elif action in ("payment", "replay", "advance", "reset", "workspace_clear", "workspace_import"):
                    raise ValueError("Simulation and workspace-reset controls are unavailable in the native operator.")
                elif action == "invoice_request":
                    identity = request.get("invoice_id")
                    if not isinstance(identity, str) or identity not in self.agent.ledger:
                        raise ValueError("Open a saved invoice to read its original request.")
                    source = self.journal.source_for(identity)
                    if source is None:
                        raise ValueError("The original request was not saved with this invoice.")
                    entry = self.agent.ledger[identity]
                    result = {"invoice_id": identity, "invoice_number": entry.invoice_number,
                              "client_name": entry.client_name, "source_text": source}
                elif action == "analysis_restore":
                    text = self._analysis_text(request)
                    previous = self.sessions.get(request.get("source_owner"))
                    self._retire_analysis(session_id, browser)
                    if previous and previous.analysis_text == text and previous.analysis_result is not None:
                        browser.analysis_text = text
                        browser.analysis = deepcopy(previous.analysis)
                        browser.analysis_result = deepcopy(previous.analysis_result)
                        browser.review = None
                        browser.ai_requires_review = False
                        result = deepcopy(browser.analysis_result)
                    else:
                        result = self._analyze_synchronously(request, browser, rules_only=True)
                elif action == "analysis_start":
                    result = self._start_analysis(request, session_id, browser)
                elif action == "analysis_status":
                    result = self._analysis_public(self._analysis_job(session_id, request.get("job_id")))
                elif action == "analysis_apply":
                    result = self._apply_analysis(request, session_id, browser)
                elif action == "analysis_dismiss":
                    job = self._analysis_job(session_id, request.get("job_id"))
                    if job["state"] in ("running", "ready", "failed"):
                        job["state"] = "dismissed"
                        job["message"] = "Analysis dismissed. Your current invoice fields are kept."
                    result = self._analysis_public(job)
                elif action in ("analyze", "analyze_rules"):
                    self._retire_analysis(session_id, browser)
                    result = self._analyze_synchronously(request, browser, rules_only=action == "analyze_rules")
                elif action == "draft":
                    self._retire_analysis(session_id, browser)
                    if self.journal.pending():
                        raise ValueError("Resolve the earlier creation attempt before creating another draft. Reopening never repeats it.")
                    if request.get("confirmation") != "CREATE":
                        raise ValueError("Type CREATE to create unsent Sandbox drafts.")
                    if not request.get("review_id"):
                        raise ValueError("Confirm and check every invoice field before creating a provider draft.")
                    checked = browser.review
                    if checked is None or checked[0] != request["review_id"] or checked[1] != request.get("text"):
                        raise ValueError("This checked review is stale. Check the original fields again.")
                    extraction = checked[2]
                    self.observed.context = {"client_name": extraction.client_name, "client_email": extraction.client_email,
                                             "due_days": extraction.due_days, "prepaid": str(extraction.amount_paid if len(extraction.currencies()) == 1 else 0),
                                             "source_text": checked[1]}
                    result = browser.dispatch(request)["result"]
                elif action == "recover_creation":
                    result = self.journal.reconcile(self.agent, request.get("attempt_id"), request.get("invoice_id"))
                elif action == "approve":
                    selected = next((a for a in self.agent.list_pending() if a["id"] == request.get("action_id")), None)
                    if selected is None:
                        raise ValueError("This decision is no longer pending. Reopen the saved ledger; never repeat an unknown send.")
                    if request.get("confirmation") != self.confirmation(selected):
                        raise ValueError("Enter the exact displayed invoice-specific confirmation.")
                    outcome = self.agent.approve(selected["id"], approver="native browser operator")
                    result = {"approved": True, "kind": selected["kind"], "invoice_id": selected["invoice_id"],
                              "provider_result": outcome, "message": self.provider + " accepted the approved action."}
                elif action == "refresh":
                    errors = []
                    for invoice_id in list(self.agent.ledger):
                        try:
                            self.agent.tool_get_status(invoice_id)
                        except Exception as exc:
                            errors.append({"invoice_id": invoice_id, "error": type(exc).__name__})
                    result = {"message": "Provider records refreshed." if not errors else "Some provider records could not refresh. Saved balances remain visible; no message was sent.",
                              "errors": errors}
                elif action == "review":
                    self._retire_analysis(session_id, browser)
                    text = self._analysis_text(request)
                    if browser.analysis is None or browser.analysis_text != text:
                        # A human can keep/edit fields after changing the source,
                        # including while the configured model is slow or failed.
                        # Refresh source warnings with the maintained local rules;
                        # checking fields must never start another model request.
                        analysis, analysis_result = self._analysis_result(RulesExtractor().extract(text), text)
                        browser.analysis_text = text
                        browser.analysis = analysis
                        browser.analysis_result = deepcopy(analysis_result)
                        browser.review = None
                        browser.ai_requires_review = False
                    result = browser.dispatch(request)["result"]
                elif action in ("client_statement", "import_email", "ai_prompt", "analyze_ai", "reject", "reopen_send_review", "remind_invoice", "prepare_cancellation", "inspect_draft_correction", "prepare_draft_correction", "chase"):
                    if action == "analyze_ai":
                        browser.analysis_result = None
                        self._retire_analysis(session_id, browser)
                    result = browser.dispatch(request)["result"]
                else:
                    raise ValueError("Unknown native invoice action.")
                return {"ok": not result.get("creation_incomplete", False), "message": result.get("message"), "result": result, "state": self.snapshot()}
            except (ValueError, TypeError, KeyError) as exc:
                return {"ok": False, "message": str(exc), "state": self.snapshot(),
                        **({"issues": exc.issues} if isinstance(exc, self.bridge.ReviewFieldError) else {})}
            except Exception as exc:
                if request.get("action") in ("analyze", "analyze_rules"):
                    return {"ok": False, "message": "Local analysis did not finish (" + type(exc).__name__ +
                            "). Your current invoice fields are kept. No provider request was made by this analysis.",
                            "state": self.snapshot()}
                # Provider responses can contain private data; keep them off logs
                # and report the saved result through the actual operator state.
                return {"ok": False, "message": f"{self.provider} action did not complete ({type(exc).__name__}). Inspect the saved ledger and decision history before another action; nothing is retried.",
                        "state": self.snapshot()}


class DeskHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".wasm": "application/wasm", ".mjs": "text/javascript"}

    def log_message(self, *_args):
        pass

    def allowed(self):
        hosts = (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")
        return self.headers.get("Host") in hosts and self.headers.get("Origin") in (None, *(f"http://{host}" for host in hosts))

    def send_bytes(self, code, data, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def reply(self, code, value):
        self.send_bytes(code, json.dumps(value).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {"ok": False, "message": "Open the operator using its printed local address."})
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            page = (self.server.web / "index.html").read_text()
            config = json.dumps({"token": self.server.token, "provider": self.server.session.provider,
                                 "model": self.server.session.local_model,
                                 "workspace_id": self.server.session.workspace_id}).replace("<", "\\u003c")
            page = page.replace("</head>", '<script id="ledgerly-native" type="application/json">' + config + "</script></head>")
            return self.send_bytes(200, page.encode(), "text/html; charset=utf-8")
        if path.startswith("/api/"):
            return self.reply(404, {"ok": False, "message": "Unknown endpoint."})
        return super().do_GET()

    def do_POST(self):
        if not self.allowed() or self.headers.get("X-Ledgerly-Token") != self.server.token:
            return self.reply(403, {"ok": False, "message": "The local operator session must be reopened."})
        if self.path != "/api/invoice":
            return self.reply(404, {"ok": False, "message": "Unknown endpoint."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 3_000_000:
                raise ValueError("The invoice request is empty or exceeds the email import limit.")
            session = self.headers.get("X-Ledgerly-Review", "")
            if not session or len(session) > 100:
                raise ValueError("A browser review session is required.")
            request = json.loads(self.rfile.read(length))
            self.reply(200, self.server.session.dispatch(request, session))
        except (ValueError, TypeError) as exc:
            self.reply(400, {"ok": False, "message": str(exc)})


def server(session, web: Path, port=0):
    web = Path(web).resolve()
    if not (web / "index.html").is_file():
        raise ValueError("Build or unzip the complete Ledgerly browser application first.")
    http = ThreadingHTTPServer(("127.0.0.1", port), partial(DeskHandler, directory=str(web)))
    http.web = web
    http.session = session
    http.token = secrets.token_hex(24)
    return http


def serve(web: Path, state_file: Path, port=8787, *, no_browser=False, ollama=False,
          model="qwen2.5:7b-instruct", ollama_url="http://127.0.0.1:11434",
          llama_cpp=False, llama_cpp_url="http://127.0.0.1:8080"):
    import webbrowser
    if ollama and llama_cpp:
        raise ValueError("Choose one local model backend")
    with exclusive_state_lock(state_file):
        agent = make_agent(True, ollama or llama_cpp, model,
                           llama_cpp_url if llama_cpp else ollama_url, os.environ,
                           state_file=state_file,
                           local_backend="llama_cpp" if llama_cpp else "ollama")
        bridge = load_browser_bridge(Path(web) / "python")
        session = NativeSession(agent, bridge)
        with server(session, web, port) as http:
            address = f"http://127.0.0.1:{http.server_port}/"
            print("Ledgerly native PayPal Sandbox operator: " + address, flush=True)
            print("Saved invoices are private. CREATE and invoice-specific SEND confirmations remain separate.", flush=True)
            if not no_browser:
                webbrowser.open(address)
            try:
                http.serve_forever()
            except KeyboardInterrupt:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-root", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true")
    local_model = parser.add_mutually_exclusive_group()
    local_model.add_argument("--ollama", action="store_true")
    local_model.add_argument("--llama-cpp", action="store_true",
                             help="Use an existing local Qwen llama.cpp server")
    parser.add_argument("--model", default="qwen2.5:7b-instruct")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--llama-cpp-url", default="http://127.0.0.1:8080")
    args = parser.parse_args(argv)
    try:
        serve(args.web_root, args.state_file, args.port, no_browser=args.no_browser,
              ollama=args.ollama, model=args.model, ollama_url=args.ollama_url,
              llama_cpp=args.llama_cpp, llama_cpp_url=args.llama_cpp_url)
    except (ValueError, OSError, RuntimeError) as exc:
        print("Cannot open the native Sandbox operator: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
