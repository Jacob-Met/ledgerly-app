"""Human-operated invoice intake with optional local AI and PayPal *sandbox* transport.

Examples:
  python -m ledgerly.operator fixtures/01_simple_usd_hourly.txt
  python -m ledgerly.operator --ollama --model qwen2.5:7b fixtures/05_missing_email.txt
  LEDGERLY_ALLOW_NETWORK=1 PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... \
    LEDGERLY_INVOICER_EMAIL=... python -m ledgerly.operator --sandbox job.txt

The default transport is SandboxMock. Sandbox mode never selects the live-money endpoint.
PayPal sandbox draft creation is a real external effect; sending still requires an
interactive, invoice-specific phrase. A broken/lost send response must not be retried
blindly. Nothing in this module persists confidential emails or API credentials.
"""
from __future__ import annotations

from contextlib import nullcontext

import argparse
import json
import os
import sys
from decimal import Decimal
from importlib.resources import files
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .agent import Agent
from .extract import HybridExtractor, LLMExtractor, RulesExtractor, MONEY_RE, parse_money
from .email_input import MAX_EML_BYTES, parse_eml
from .live import ReviewedExtractor, _review
from .paypal import OPEN_STATUSES, HttpPayPalClient, PayPalError, SANDBOX_BASE, SandboxMock
from .recovery import DurableAgent, exclusive_state_lock
from .review import invoice_review


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """A local inference service must not redirect confidential invoice text."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _invoice_output_format(prompt: str) -> str | dict:
    """Constrain copied rates, without authorizing their service association.

    The subsequent grounding checks still reject quoted history, summaries,
    swapped rates and unsupported quantities. Decimal strings avoid float loss.
    Generic completions and sources with no readable prices keep JSON mode.
    """
    _, marker, source = prompt.partition("EMAIL:\n<<<\n")
    if not marker:
        return "json"
    prices: set[str] = set()
    for line in source.splitlines():
        if line.lstrip().startswith(">"):
            continue
        for match in MONEY_RE.finditer(line):
            try:
                prices.add(format(parse_money(match)[1], "f"))
            except ValueError:
                continue
    if not prices:
        return "json"
    nullable = {"type": ["string", "null"]}
    item = {"desc": {"type": "string"}, "qty": {"type": ["number", "null"]},
            "unit_price": {"type": "string", "enum": sorted(prices)}, "currency": nullable, "unit": nullable}
    issue = {"field": {"type": "string"},
             "severity": {"type": "string", "enum": ["error", "warning", "info"]},
             "message": {"type": "string"}}
    properties = {
        "client_name": nullable, "client_email": nullable, "currency": nullable,
        "line_items": {"type": "array", "items": {
            "type": "object", "properties": item, "required": list(item)}},
        "due_days": {"type": ["integer", "null"]}, "due_date": nullable,
        "amount_paid": {"type": "number"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "issues": {"type": "array", "items": {
            "type": "object", "properties": issue, "required": list(issue)}},
    }
    return {"type": "object", "properties": properties, "required": list(properties)}


def _invoice_completion_contract(prompt: str) -> tuple[str, str | dict]:
    """Keep the same complete invoice prompt and decoding schema on both transports."""
    output_format = _invoice_output_format(prompt)
    request_prompt = prompt
    if isinstance(output_format, dict):
        opening, marker, rest = prompt.partition("Return ONLY a JSON object, no prose, matching:")
        _, rules_marker, rules = rest.partition("Rules:")
        if marker and rules_marker:
            request_prompt = (opening + "Return only one JSON object matching this JSON Schema:\n"
                              + json.dumps(output_format, separators=(",", ":"))
                              + "\nUse decimal strings for unit_price.\nRules:" + rules)
    return request_prompt, output_format


def _local_model_opener(model: str, base_url: str, opener: Callable | None):
    parsed = urlparse(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or not model.strip()):
        raise ValueError("Local inference requires a model and a plain HTTP loopback base URL")
    return opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _RefuseRedirect()).open


def _local_model_json(opener: Callable, request: urllib.request.Request, timeout: int) -> dict:
    with opener(request, timeout=timeout) as response:
        raw = response.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("Local model response exceeds 1 MiB")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Local model returned no response object")
    return data


class OllamaCompletion:
    """Complete the existing invoice contract through a local Ollama instance."""

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", *,
                 opener: Callable | None = None):
        self.opener = _local_model_opener(model, base_url, opener)
        self.url = base_url.rstrip("/") + "/api/generate"
        self.model = model

    def __call__(self, prompt: str) -> str:
        request_prompt, output_format = _invoice_completion_contract(prompt)
        body = json.dumps({
            "model": self.model, "prompt": request_prompt, "stream": False,
            "format": output_format, "options": {"temperature": 0},
        }).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        data = _local_model_json(self.opener, request, 90)
        if not isinstance(data.get("response"), str):
            raise ValueError("Ollama returned no completion text")
        return data["response"]


class LlamaCppCompletion:
    """Use an existing local Qwen server with the verified ChatML invoice contract.

    The server owns its model and lifetime. This client does not download models,
    start processes or change invoice grounding or provider permissions.
    """

    system_prompt = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:8080", *,
                 opener: Callable | None = None):
        self.opener = _local_model_opener(model, base_url, opener)
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/v1/chat/completions"
        self.model = model

    def _post(self, path: str, body: dict, timeout: int) -> dict:
        request = urllib.request.Request(
            self.base_url + path, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"})
        return _local_model_json(self.opener, request, timeout)

    def __call__(self, prompt: str) -> str:
        request_prompt, output_format = _invoice_completion_contract(prompt)
        messages = [{"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": request_prompt}]
        expected = ("<|im_start|>system\n" + self.system_prompt + "<|im_end|>\n"
                    "<|im_start|>user\n" + request_prompt + "<|im_end|>\n"
                    "<|im_start|>assistant\n")
        template = self._post("/apply-template", {"messages": messages}, 10)
        if template.get("prompt") != expected:
            raise ValueError("The local server needs the verified ChatML template "
                             "(--no-jinja --chat-template chatml).")
        response_format = ({"type": "json_schema", "json_schema": {
            "name": "ledgerly_invoice", "strict": True, "schema": output_format}}
            if isinstance(output_format, dict) else {"type": "json_object"})
        data = self._post("/v1/chat/completions", {
            "model": self.model, "messages": messages, "stream": False,
            "temperature": 0, "response_format": response_format,
        }, 90)
        choices = data.get("choices")
        if (not isinstance(choices, list) or len(choices) != 1
                or not isinstance(choices[0], dict)
                or choices[0].get("finish_reason") != "stop"):
            raise ValueError("The local model did not finish one complete invoice response.")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("The local model returned no completion text")
        return message["content"]


def _invoicer(environment: dict[str, str], sandbox: bool) -> dict[str, str]:
    if sandbox:
        email = environment.get("LEDGERLY_INVOICER_EMAIL", "").strip()
        name = environment.get("LEDGERLY_INVOICER_NAME", "").strip()
        if not email or not name:
            raise ValueError("Sandbox requires LEDGERLY_INVOICER_EMAIL and LEDGERLY_INVOICER_NAME")
        return {"name": name, "email_address": email}
    return {"name": "Demo Freelancer", "email_address": "demo@ledgerly.example"}


def make_agent(sandbox: bool, ollama: bool, model: str, base_url: str,
               environment: dict[str, str], state_file: Path | None = None, *,
               local_backend: str = "ollama") -> Agent:
    invoicer = _invoicer(environment, sandbox)
    if sandbox:
        if environment.get("LEDGERLY_ALLOW_NETWORK") != "1":
            raise ValueError("Set LEDGERLY_ALLOW_NETWORK=1 to arm sandbox-only API access")
        client_id, secret = environment.get("PAYPAL_CLIENT_ID"), environment.get("PAYPAL_CLIENT_SECRET")
        if not client_id or not secret:
            raise ValueError("Sandbox requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")
        # The real-money URL is intentionally not configurable.
        client = HttpPayPalClient(client_id, secret, base_url=SANDBOX_BASE)
    else:
        client = SandboxMock()
    completion_type = {"ollama": OllamaCompletion, "llama_cpp": LlamaCppCompletion}.get(local_backend)
    if completion_type is None:
        raise ValueError("Choose the Ollama or llama.cpp local backend")
    primary = RulesExtractor()
    extractor = (HybridExtractor(primary, LLMExtractor(completion_type(model, base_url), ground_prices=True, check_missing_work=True),
                                 threshold=0.90) if ollama else primary)
    if state_file is not None:
        if not sandbox:
            raise ValueError("Durable recovery requires the persistent PayPal Sandbox provider")
        return DurableAgent(client, invoicer, extractor=extractor, state_file=state_file,
                            observation_provider="PayPal Sandbox")
    return Agent(client, invoicer, extractor=extractor)



def run_intake(agent: Agent, source: str, *, approve_input: Callable[[str], str] = input,
               emit: Callable[[str], None] = print, sandbox: bool = False,
               draft_only: bool = False) -> int:
    """One intake through actual Agent paths. No automatic approvals, even in tests."""
    # Creating sandbox drafts is an external effect. Validate and ask for an
    # explicit confirmation before the first provider call, not afterward.
    extraction = agent.extractor.extract(source)
    if not _review(extraction, source, emit):
        return 2
    emit("Reported prior payment: " + str(extraction.amount_paid))
    try:
        consent = approve_input("Type CREATE to create draft invoice(s), or Enter to cancel: ")
    except EOFError:
        consent = ""
    if consent.strip() != "CREATE":
        emit("Cancelled before any provider request; no draft created.")
        return 1
    # Freeze the exact review: an inference call must not change the amounts
    # after the operator has approved their creation.
    prior_extractor = agent.extractor
    agent.extractor = ReviewedExtractor(extraction)
    try:
        result = agent.tool_create_invoice(source)
    except Exception as exc:
        for entry in agent.ledger.values():
            emit("Confirmed earlier draft: " + entry.invoice_number + " id=" + entry.invoice_id)
        emit("Draft outcome uncertain (" + type(exc).__name__ +
             "). Inspect the sandbox provider before creating or retrying drafts.")
        return 3
    finally:
        agent.extractor = prior_extractor
    emit(f"Extractor confidence: {result.get('confidence', 'unavailable')}")
    for issue in result.get("issues", []):
        emit(f"  [{issue['severity']}] {issue['field']}: {issue['message']}")
    if not result.get("ok"):
        if result.get("creation_incomplete"):
            emit("INCOMPLETE CREATION — no invoice sent by this operator.")
            emit("Batch outcome uncertain until provider drafts are inspected; do not retry blindly.")
            for draft in result["invoices"]:
                emit(f"Confirmed earlier draft {draft['currency']} {draft['invoice_number']} "
                     f"({draft['invoice_id']}); remains UNSENT.")
            failed = result["draft_failure"]
            emit(f"Failed {failed['currency']}: {failed['outcome']} at {failed['stage']}"
                 + (f" (number {failed['invoice_number']})" if failed["invoice_number"] else ""))
            emit(result["message"])
            # No send prompts, automatic retries or automatic dismissal of confirmed
            # drafts: inspect PayPal before starting another intake.
            return 3
        emit("No provider drafts confirmed. Correct the source and review again.")
        return 2
    emit(f"Created {len(result['invoices'])} {'PayPal sandbox' if sandbox else 'simulated'} draft(s).")
    refused = False
    for draft in result["invoices"]:
        action_id = draft["approval_id"]
        action = next(row for row in agent.list_pending() if row["id"] == action_id)
        emit("\n" + invoice_review(action))
        prepaid = Decimal(action["payload"].get("prepaid", "0"))
        if prepaid > 0:
            emit("REPORTED PRIOR PAYMENT: " + str(prepaid)
                 + " " + action["payload"]["invoice"]["detail"]["currency_code"]
                 + ". Approval sends the invoice AND records a prior external payment.")
        if draft_only:
            emit(f"DRAFT SAVED: {draft['invoice_number']} id={draft['invoice_id']} remains UNSENT.")
            emit("Send approval remains pending."
                 + (" Resume with --sandbox --state-file <file> --resume." if sandbox
                    else " The local mock cannot resume after exit."))
            continue
        phrase = ("SEND+RECORD " if prepaid > 0 else "SEND ") + draft["invoice_number"]
        emit("No message is sent or prior payment recorded unless you enter this invoice-specific phrase.")
        try:
            typed = approve_input(f"To send this {'sandbox' if sandbox else 'simulated'} invoice, type {phrase}: ")
        except EOFError:
            typed = ""
        if typed != phrase:
            agent.reject(action_id, reason="Not explicitly approved in operator")
            emit("Rejected; invoice remains a draft.")
            refused = True
            continue
        try:
            outcome = agent.approve(action_id, approver="operator")
        except Exception as exc:
            # A lost response may mean the outgoing send *did* happen.
            emit(f"Approval failed ({type(exc).__name__}). Inspect the provider invoice "
                 "before taking any new action; NEVER retry the send blindly.")
            return 3
        emit("Approved and sent by provider." if sandbox else "Approved and sent in simulation.")
        if outcome.get("payer_view"):
            if sandbox:
                emit("PayPal Sandbox payer view: " + str(outcome["payer_view"]))
            else:
                emit("No payer link was created; this invoice exists only in the offline simulation.")
    return 0 if not refused else 1



def run_pending(agent: Agent, *, approve_input: Callable[[str], str] = input,
                emit: Callable[[str], None] = print) -> int:
    """Resume earlier sandbox reviews; never infer consent from stored state."""
    rows = agent.list_pending()
    if isinstance(agent, DurableAgent):
        agent.state_store.save(agent)
    interrupted = [action for action in agent.pending.values()
                   if action.status == "FAILED" and isinstance(action.result, dict)
                   and action.result.get("outcome") == "UNKNOWN"]
    for action in interrupted:
        emit("UNKNOWN previous outcome for " + action.invoice_id
             + ": inspect PayPal directly. Its approval cannot be replayed.")
    if not rows:
        emit("No pending approvals to review.")
        return 3 if interrupted else 0
    for action in rows:
        if action["kind"] == "send_invoice":
            emit("\n" + invoice_review(action))
            prepaid = Decimal(action["payload"].get("prepaid", "0"))
            if prepaid > 0:
                emit("REPORTED PRIOR PAYMENT: " + str(prepaid)
                     + ". Approval sends the invoice AND records an external payment.")
            phrase = ("SEND+RECORD " if prepaid > 0 else "SEND ") + action["payload"]["invoice"]["detail"]["invoice_number"]
        else:
            emit("\nReminder: " + action["summary"])
            emit("  Subject: " + str(action["payload"]["subject"]))
            emit("  Text: " + str(action["payload"]["note"]))
            phrase = "REMIND " + action["invoice_id"]
        try:
            typed = approve_input("Type " + phrase + " to approve, REJECT " + action["invoice_id"]
                                  + " to discard, or Enter to leave pending: ")
        except EOFError:
            typed = ""
        if typed == "REJECT " + action["invoice_id"]:
            agent.reject(action["id"], reason="Explicitly declined during resumed review")
            emit("Review rejected; nothing sent.")
            continue
        if typed != phrase:
            emit("Left pending; nothing sent.")
            continue
        try:
            agent.approve(action["id"], approver="operator-resume")
        except Exception as exc:
            emit("Approval failed (" + type(exc).__name__
                 + "); check the provider before any further action.")
            return 3
        emit("Provider accepted the approved action for " + action["invoice_id"])
    return 3 if interrupted else 0



def run_follow_up(agent: Agent, *, approve_input: Callable[[str], str] = input,
                  emit: Callable[[str], None] = print) -> int:
    """Refresh persisted Sandbox invoices and offer one human-gated overdue reminder."""
    if not isinstance(agent, DurableAgent):
        raise ValueError("Follow-up requires durable PayPal Sandbox state")
    ids = list(agent.ledger)
    if not ids:
        emit("No saved invoices. Create a Sandbox invoice using --state-file first.")
        return 0
    failures = 0
    for invoice_id in ids:
        try:
            # Payment webhooks can be delayed: always fetch provider state before
            # deciding whether an invoice is due, partly paid, or closed.
            agent.tool_get_status(invoice_id)
        except (PayPalError, ValueError, OSError) as error:
            emit("Could not refresh " + invoice_id + " (" + type(error).__name__
                 + "); no reminder was prepared for it.")
            failures += 1
            continue
        entry = agent.ledger[invoice_id]
        due = entry.due_on.isoformat() if entry.due_on else "not established"
        overdue_days = entry.days_overdue(agent.today()) if entry.status in OPEN_STATUSES else 0
        late = " | " + str(overdue_days) + " days overdue" if overdue_days else ""
        emit(entry.invoice_number + " | " + entry.status + " | " + entry.currency
             + " " + str(entry.balance) + " remaining | due " + due + late)
        if (entry.status not in OPEN_STATUSES or entry.days_overdue(agent.today()) == 0
                or entry.balance <= 0):
            continue
        try:
            proposed = agent.tool_send_reminder(invoice_id)
        except (PayPalError, ValueError, OSError) as error:
            emit("Could not draft a reminder for " + invoice_id + " ("
                 + type(error).__name__ + ").")
            failures += 1
            continue
        if not proposed.get("ok"):
            emit("No new reminder for " + invoice_id + ": " + proposed["message"])
            continue
        draft = proposed["draft"]
        emit("REMINDER REVIEW for " + entry.client_email)
        emit("  Subject: " + draft["subject"])
        emit("  Message: " + draft["note"])
        phrase = "REMIND " + invoice_id
        try:
            typed = approve_input("Type " + phrase + " to send, REJECT " + invoice_id
                                  + " to discard, or Enter to leave pending: ")
        except EOFError:
            typed = ""
        if typed == "REJECT " + invoice_id:
            agent.reject(proposed["approval_id"], reason="Explicitly declined reminder")
            emit("Reminder rejected, not sent.")
            continue
        if typed != phrase:
            emit("Reminder left pending, not sent. Reopen with --resume.")
            continue
        try:
            agent.approve(proposed["approval_id"], approver="operator-follow-up")
        except Exception as error:
            emit("Reminder outcome uncertain (" + type(error).__name__
                 + "); inspect PayPal before any further action.")
            return 3
        emit("PayPal Sandbox accepted the approved reminder for " + invoice_id)
    return 3 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?", help="UTF-8 job email or .eml export path, or '-' for stdin")
    parser.add_argument("--eml", action="store_true",
                        help="Parse RFC 5322 MIME email, including stdin; .eml files auto-detect")
    parser.add_argument("--demo", action="store_true",
                        help="Run the bundled fictional example offline, without a source checkout")
    parser.add_argument("--state-file", type=Path,
                        help="Private crash-aware invoice and webhook checkpoint; PayPal Sandbox only")
    parser.add_argument("--statement", metavar="CLIENT_EMAIL",
                        help="Export one client's saved Sandbox account statement; no provider calls")
    parser.add_argument("--statement-output", type=Path, metavar="FILE.html",
                        help="New private HTML output for --statement; existing files are never overwritten")
    parser.add_argument("--resume", action="store_true",
                        help="Review pending approvals from --state-file, without new invoice intake")
    parser.add_argument("--follow-up", action="store_true",
                        help="Refresh saved Sandbox payment status and review overdue reminders")
    parser.add_argument("--draft-only", action="store_true",
                        help="After explicit CREATE, retain unsent approvals for later review")
    parser.add_argument("--sandbox", action="store_true",
                        help="Use the real PayPal SANDBOX only; drafts are real sandbox API effects")
    local_model = parser.add_mutually_exclusive_group()
    local_model.add_argument("--ollama", action="store_true",
                             help="Use local Ollama when rules extraction is uncertain")
    local_model.add_argument("--llama-cpp", action="store_true",
                             help="Use an existing local Qwen llama.cpp server when rules extraction is uncertain")
    parser.add_argument("--model", default="qwen2.5:7b-instruct", help="Installed local model name or server alias")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434",
                        help="Loopback Ollama base URL")
    parser.add_argument("--llama-cpp-url", default="http://127.0.0.1:8080",
                        help="Loopback llama.cpp base URL")
    parser.add_argument("--retainers", action="store_true",
                        help="Open the persistent monthly retainer desk; optional source uses the existing email importer")
    parser.add_argument("--retainer-store", type=Path, default=Path.home()/".local/state/ledgerly/retainers.sqlite3",
                        help="Private SQLite file for the retainer desk")
    parser.add_argument("--retainer-port", type=int, default=8768)
    args = parser.parse_args(argv)
    if args.statement is not None:
        if (args.state_file is None or args.statement_output is None or args.source is not None
                or args.sandbox or args.demo or args.resume or args.follow_up or args.draft_only
                or args.ollama or args.llama_cpp or args.eml or args.retainers):
            parser.error("--statement requires --state-file and --statement-output, without intake, provider or other modes")
        from .statement import export_statement
        try:
            path = export_statement(args.state_file, args.statement, args.statement_output,
                                    _invoicer(os.environ, True))
            print(f"Saved Sandbox account statement: {path}")
            print("Local records only; PayPal was not refreshed. No message or payment was sent.")
            return 0
        except (ValueError, OSError) as exc:
            print(f"Cannot export account statement: {exc}", file=sys.stderr)
            return 2
    if args.statement_output is not None:
        parser.error("--statement-output requires --statement")
    if args.retainers:
        if args.demo or args.resume or args.follow_up or args.state_file or args.ollama or args.llama_cpp:
            parser.error("--retainers cannot be combined with invoice demo, recovery, state or model flags")
        from .retainer import RetainerDesk
        from .retainer_web import serve
        try:
            source_text = ""
            if args.source is not None:
                if str(args.source) == "-":
                    raise ValueError("Use a saved source file when opening the retainer browser")
                if args.source.stat().st_size > MAX_EML_BYTES:
                    raise ValueError("Source exceeds the 2 MiB intake limit")
                source_text = (parse_eml(args.source.read_bytes()) if args.eml or args.source.suffix.lower() == ".eml"
                               else args.source.read_text(encoding="utf-8"))
                if len(source_text) > 12000:
                    raise ValueError("Retainer source exceeds 12,000 characters; select the relevant agreement")
            desk = RetainerDesk(args.retainer_store)
            desk.initial_source = source_text
            serve(desk, args.retainer_port)
            return 0
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.demo and (args.sandbox or args.state_file is not None or args.resume or args.follow_up):
        parser.error("--demo is offline-only; cannot use sandbox, saved state, or resume")
    if args.demo and args.eml:
        parser.error("--eml cannot be combined with --demo")
    if args.resume and args.eml:
        parser.error("--eml cannot be combined with --resume")
    if args.demo and args.source is not None:
        parser.error("Choose either --demo or a source file")
    try:
        # Validate transport prerequisites before opening an input file or using any API.
        if args.state_file is not None and not args.sandbox:
            raise ValueError("--state-file requires --sandbox (the offline mock resets each run)")
        if args.resume and (args.source is not None or args.state_file is None
                            or args.follow_up or args.draft_only):
            raise ValueError("--resume requires --state-file and no new source, --follow-up or --draft-only")
        if args.follow_up and (args.source is not None or args.state_file is None
                               or not args.sandbox or args.draft_only):
            raise ValueError("--follow-up requires --sandbox --state-file and no new source or --draft-only")
        if args.sandbox and args.draft_only and args.state_file is None:
            raise ValueError("--sandbox --draft-only requires --state-file for durable pending approvals")
        if not args.resume and not args.follow_up and args.source is None and not args.demo:
            raise ValueError("A source file is required unless --resume, --follow-up or --demo is used")
        lock = exclusive_state_lock(args.state_file) if args.state_file is not None else nullcontext()
        with lock:
            agent = make_agent(args.sandbox, args.ollama or args.llama_cpp, args.model,
                               args.llama_cpp_url if args.llama_cpp else args.ollama_url, os.environ,
                               state_file=args.state_file,
                               local_backend="llama_cpp" if args.llama_cpp else "ollama")
            if args.resume:
                return run_pending(agent)
            if args.follow_up:
                return run_follow_up(agent)
            is_eml = args.eml or (not args.demo and str(args.source) != "-"
                                  and args.source.suffix.lower() == ".eml")
            if args.demo:
                text = files("ledgerly").joinpath("sample_email.txt").read_text(encoding="utf-8")
            elif is_eml:
                if str(args.source) == "-":
                    raw = sys.stdin.buffer.read(MAX_EML_BYTES + 1)
                else:
                    if args.source.stat().st_size > MAX_EML_BYTES:
                        raise ValueError("Exported email exceeds the 2 MiB intake limit")
                    raw = args.source.read_bytes()
                text = parse_eml(raw)
                print("Imported exported email (unverified From/To headers); "
                      "confirm the client and all amounts before CREATE.")
            else:
                text = (sys.stdin.read() if str(args.source) == "-"
                        else args.source.read_text(encoding="utf-8"))
            if not text.strip():
                raise ValueError("Input email cannot be empty")
            return run_intake(agent, text, sandbox=args.sandbox, draft_only=args.draft_only)
    except (ValueError, OSError, PayPalError, RuntimeError) as exc:
        print(f"Ledgerly cannot start or create drafts: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
