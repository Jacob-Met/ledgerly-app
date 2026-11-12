"""JSON continuity for the actual browser SandboxMock and Agent.

Only local simulation state is supported. Restoring never calls a provider,
replays a command, or restores a consumed human review authorization.
"""
import base64
import copy
import json
from datetime import date

from ledgerly.recovery import _decode_snapshot, _entry_to_record

FORMAT = "ledgerly-browser-sandbox"
MAX_BYTES = 10_000_000


def dump_session(session):
    agent = session.agent
    event = None
    if session.last_event is not None:
        raw, headers = session.last_event
        event = {"body": base64.b64encode(raw).decode("ascii"), "headers": dict(headers)}
    return {
        "format": FORMAT,
        "version": 1,
        "day": session.clock.day.isoformat(),
        "agent": {
            "version": 1,
            "invoicer_email": agent.invoicer["email_address"],
            "ledger": [_entry_to_record(e) for e in agent.ledger.values()],
            "pending": [a.to_dict() for a in agent.pending.values()],
            "seen_events": agent._seen_events.copy(),
            "audit": copy.deepcopy(agent.audit),
        },
        "mock": {
            "invoices": copy.deepcopy(session.mock.invoices),
            "requests": copy.deepcopy(session.mock.requests),
            "sequence": session.mock._seq,
        },
        "last_event": event,
        "invoice_requests": copy.deepcopy(session.invoice_sources),
    }


def dump_session_json(session):
    raw = json.dumps(dump_session(session), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("The saved workspace exceeds 10 MB. Export invoice records before clearing it.")
    return raw


def restore_session(session, record):
    required = {"format", "version", "day", "agent", "mock", "last_event"}
    if not isinstance(record, dict) or set(record) not in (required, required | {"invoice_requests"}):
        raise ValueError("The saved workspace is incomplete; it was not replaced.")
    if record["format"] != FORMAT or type(record["version"]) is not int or record["version"] != 1:
        raise ValueError("This saved workspace needs a different Ledgerly version.")
    candidate = type(session)()
    candidate.clock.day = date.fromisoformat(record["day"])
    ledger, pending, seen, audit = _decode_snapshot(record["agent"], candidate.agent.invoicer["email_address"])
    mock = record["mock"]
    if not isinstance(mock, dict) or set(mock) != {"invoices", "requests", "sequence"}:
        raise ValueError("The saved simulated payment records are incomplete.")
    invoices = mock["invoices"]
    if not isinstance(invoices, dict) or set(invoices) != set(ledger):
        raise ValueError("The saved invoices and ledger disagree.")
    for key, invoice in invoices.items():
        if not isinstance(invoice, dict) or invoice.get("id") != key:
            raise ValueError("A saved invoice has lost its identity.")
    requests = mock["requests"]
    if not isinstance(requests, list) or any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in requests):
        raise ValueError("The saved simulation activity is incomplete.")
    sequence = mock["sequence"]
    if type(sequence) is not int or sequence < len(invoices):
        raise ValueError("The saved invoice sequence is invalid.")
    sources = record.get("invoice_requests", {})
    if (not isinstance(sources, dict) or any(
            not isinstance(identity, str) or identity not in ledger
            or not isinstance(source, str) or not source.strip()
            for identity, source in sources.items())):
        raise ValueError("A saved client request has lost its invoice identity.")
    candidate.invoice_sources = copy.deepcopy(sources)
    candidate.agent.ledger = ledger
    candidate.agent.pending = pending
    candidate.agent._seen_events = seen
    candidate.agent.audit = audit
    candidate.mock.invoices = copy.deepcopy(invoices)
    candidate.mock.requests = copy.deepcopy(requests)
    candidate.mock._seq = sequence
    event = record["last_event"]
    if event is not None:
        if (not isinstance(event, dict) or set(event) != {"body", "headers"}
                or not isinstance(event["body"], str) or not isinstance(event["headers"], dict)
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in event["headers"].items())):
            raise ValueError("The last simulated webhook is incomplete.")
        candidate.last_event = (base64.b64decode(event["body"], validate=True), event["headers"].copy())
    # Validate the actual display path before replacing any running state.
    candidate.snapshot()
    session.__dict__.clear()
    session.__dict__.update(candidate.__dict__)


def restore_session_json(session, raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("The saved workspace cannot be read.")
    restore_session(session, json.loads(raw))


def preview_session_json(session, raw):
    """Inspect the same restored state without replacing current work or approvals."""
    candidate = type(session)()
    restore_session_json(candidate, raw)
    return json.dumps(candidate.snapshot(), ensure_ascii=False, allow_nan=False)
