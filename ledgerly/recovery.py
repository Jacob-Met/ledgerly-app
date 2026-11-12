"""Crash-aware, opt-in private recovery for Ledgerly's actual Agent and PayPal sandbox.

One writer per state path. Snapshot durability does not make a provider request atomic:
a crash between creating a provider draft and recording its receipt still requires
manual provider reconciliation. Outgoing approvals are fail-closed before transport.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
import stat
import tempfile
from dataclasses import fields
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .agent import Agent, LedgerEntry, PendingAction

_LIMIT = 10_000_000
_DECIMALS = {"total", "paid_amount", "prepaid"}
_DATES = {"sent_on", "last_reminder_on", "provider_due_on", "invoice_due_on"}
_STATUSES = {"PENDING", "APPROVED", "REJECTED", "FAILED", "INTERRUPTED"}


def _entry_to_record(entry: LedgerEntry) -> dict:
    result = {}
    for field in fields(LedgerEntry):
        value = getattr(entry, field.name)
        result[field.name] = (str(value) if isinstance(value, Decimal)
                              else value.isoformat() if isinstance(value, date) else value)
    return result


def _entry_from_record(raw: object) -> LedgerEntry:
    names = {f.name for f in fields(LedgerEntry)}
    if not isinstance(raw, dict) or set(raw) != names:
        raise ValueError("invalid ledger record fields")
    data = raw.copy()
    try:
        for name in _DECIMALS:
            if not isinstance(data[name], str):
                raise ValueError("invalid money representation")
            data[name] = Decimal(data[name])
            if not data[name].is_finite() or data[name] < 0:
                raise ValueError("invalid invoice money")
        for name in _DATES:
            value = data[name]
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("invalid stored date")
                data[name] = date.fromisoformat(value)
        if not isinstance(data["invoice_id"], str) or not data["invoice_id"]:
            raise ValueError("invalid invoice ID")
        for name in ("invoice_number", "client_email", "currency", "status"):
            if not isinstance(data[name], str) or not data[name]:
                raise ValueError("invalid ledger text")
        if data["client_name"] is not None and not isinstance(data["client_name"], str):
            raise ValueError("invalid client name")
        if data["due_days"] is not None and (type(data["due_days"]) is not int or data["due_days"] < 0):
            raise ValueError("invalid due days")
        if type(data["reminders_sent"]) is not int or data["reminders_sent"] < 0:
            raise ValueError("invalid reminder count")
        for name in ("provider_due_known", "provider_receipt_pending"):
            if type(data[name]) is not bool:
                raise ValueError("invalid provider flag")
        if data["paid_amount"] > data["total"] or data["prepaid"] > data["total"]:
            raise ValueError("invalid recorded payments")
        return LedgerEntry(**data)
    except (InvalidOperation, OverflowError, TypeError) as exc:
        raise ValueError("invalid ledger record") from exc


def _decode_snapshot(record: object, invoicer: str):
    if not isinstance(record, dict):
        raise ValueError("invalid recovery envelope")
    version = record.get("version")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("unsupported recovery version")
    expected = {"version", "invoicer_email", "ledger", "pending", "seen_events", "audit"}
    if version == 2:
        expected.add("invoice_records")
    if set(record) != expected:
        raise ValueError("invalid recovery envelope")
    if record["invoicer_email"] != invoicer:
        raise ValueError("recovery file belongs to a different invoicer")
    if not isinstance(record["ledger"], list) or len(record["ledger"]) > 100_000:
        raise ValueError("invalid ledger collection")
    ledger = {}
    for row in record["ledger"]:
        entry = _entry_from_record(row)
        if entry.invoice_id in ledger:
            raise ValueError("duplicate invoice ID")
        ledger[entry.invoice_id] = entry
    rows = record["pending"]
    if not isinstance(rows, list) or len(rows) > 100_000:
        raise ValueError("invalid approval collection")
    pending = {}
    required = {f.name for f in fields(PendingAction)}
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("invalid approval record")
        if (not isinstance(row["id"], str)
                or not isinstance(row["invoice_id"], str)
                or not isinstance(row["kind"], str)
                or not isinstance(row["status"], str)):
            raise ValueError("invalid approval identity or status")
        if (not row["id"]
                or row["id"] in pending or row["invoice_id"] not in ledger
                or row["kind"] not in ("send_invoice", "send_reminder", "cancel_invoice", "update_invoice")
                or row["status"] not in _STATUSES
                or not isinstance(row["payload"], dict)
                or not isinstance(row["summary"], str)
                or not isinstance(row["created_at"], str)):
            raise ValueError("invalid approval identity or status")
        data = copy.deepcopy(row)
        if data["status"] == "INTERRUPTED":
            data["status"] = "FAILED"
            data["result"] = {"outcome": "UNKNOWN",
                              "reason": "Process stopped during outgoing approval; inspect PayPal before any new action."}
        pending[data["id"]] = PendingAction(**data)
    events = record["seen_events"]
    if not isinstance(events, dict) or len(events) > 100_000:
        raise ValueError("invalid webhook identity collection")
    for key, digest in events.items():
        if (not isinstance(key, str) or not key or key.strip() != key
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("invalid webhook digest")
    audit = record["audit"]
    if not isinstance(audit, list) or len(audit) > 200_000:
        raise ValueError("invalid audit records")
    return ledger, pending, events.copy(), copy.deepcopy(audit)


def _invoice_records(record: dict, ledger: dict) -> dict:
    """Validate retained observations separately from authoritative ledger facts."""
    rows = record.get("invoice_records", {})
    if not isinstance(rows, dict) or len(rows) > len(ledger):
        raise ValueError("invalid retained invoice records")
    for identity, saved in rows.items():
        if identity not in ledger or not isinstance(saved, dict) or set(saved) != {
            "record", "observed_at", "provider"
        }:
            raise ValueError("invalid retained invoice identity")
        invoice = saved["record"]
        if (not isinstance(invoice, dict) or invoice.get("id") != identity
                or not isinstance(invoice.get("items"), list)
                or not isinstance(invoice.get("detail"), dict)
                or not isinstance(saved["provider"], str) or not saved["provider"]
                or not isinstance(saved["observed_at"], str)):
            raise ValueError("invalid retained invoice observation")
        try:
            observed = datetime.fromisoformat(saved["observed_at"])
        except ValueError as exc:
            raise ValueError("invalid retained invoice observation time") from exc
        if observed.tzinfo is None:
            raise ValueError("retained invoice observation time needs a timezone")
    return copy.deepcopy(rows)


class SnapshotStore:
    """Atomic mode-0600 JSON checkpoint. Contains sensitive invoice and audit data."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _ensure_private(self) -> bool:
        try:
            mode = self.path.lstat().st_mode
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("recovery path must be a private regular file (mode 0600)")
        return True

    def load_into(self, agent: Agent) -> None:
        if not self._ensure_private():
            return
        with self.path.open("rb") as stream:
            raw = stream.read(_LIMIT + 1)
        if len(raw) > _LIMIT:
            raise ValueError("recovery file exceeds 10 MB")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("invalid recovery JSON") from exc
        ledger, pending, events, audit = _decode_snapshot(data, agent.invoicer["email_address"])
        invoice_records = _invoice_records(data, ledger)
        # No partial restored state if any field is invalid.
        agent.ledger, agent.pending, agent._seen_events, agent.audit = (
            ledger, pending, events, audit
        )
        agent.invoice_records = invoice_records

    def save(self, agent: Agent, *, interrupted: str | None = None) -> None:
        self._ensure_private()
        pending = [a.to_dict() for a in agent.pending.values()]
        if interrupted is not None:
            matches = [row for row in pending if row["id"] == interrupted]
            if len(matches) != 1 or matches[0]["status"] != "PENDING":
                raise ValueError("cannot checkpoint an inactive outgoing action")
            matches[0]["status"] = "INTERRUPTED"
            matches[0]["result"] = {"outcome": "UNKNOWN", "reason": "Outgoing operation in progress"}
        data = {
            "version": 1,
            "invoicer_email": agent.invoicer["email_address"],
            "ledger": [_entry_to_record(e) for e in agent.ledger.values()],
            "pending": pending,
            "seen_events": agent._seen_events.copy(),
            "audit": copy.deepcopy(agent.audit),
        }
        records = {key: value for key, value in getattr(agent, "invoice_records", {}).items()
                   if key in agent.ledger}
        if records:
            data["version"] = 2
            data["invoice_records"] = records
            _invoice_records(data, agent.ledger)
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > _LIMIT:
            raise ValueError("recovery checkpoint exceeds 10 MB")
        parent = self.path.parent
        fd, temporary = tempfile.mkstemp(prefix=".ledgerly-recovery-", dir=parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if hasattr(os, "O_DIRECTORY"):
                directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


@contextmanager
def exclusive_state_lock(path: str | Path):
    """One active CLI owner per checkpoint; OS drops its lock on process death.

    Keep the sidecar inode rather than unlinking it: removing a live lock file
    allows another process to create and lock a different inode at the same path.
    """
    target = Path(path).resolve(strict=False)
    lock_path = target.with_name(target.name + ".lock")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    acquired = False
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("checkpoint lock must be a private regular file (mode 0600)")
        if os.name == "nt":
            import msvcrt

            # Windows byte-range locks need a byte to protect. The empty
            # sidecar contains no client data; write once before taking lock.
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Checkpoint already in use by another Ledgerly operator") from exc
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Checkpoint already in use by another Ledgerly operator") from exc
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class DurableAgent(Agent):
    """Real Agent with opt-in disk recovery and fail-closed outgoing actions."""

    def __init__(self, client, invoicer, *, state_file: str | Path,
                 observation_provider: str = "Provider source not recorded", **kwargs):
        super().__init__(client, invoicer, **kwargs)
        self.observation_provider = observation_provider
        self.invoice_records = {}
        self.state_store = SnapshotStore(state_file)
        self.state_store.load_into(self)

    def _refresh_invoice(self, invoice_id):
        invoice = super()._refresh_invoice(invoice_id)
        # Retain the same full, validated read that refreshed the ledger. This
        # applies to the CLI as well as the desk, without an additional GET.
        # Partial responses cannot replace a previously retained full record.
        if invoice_id in self.ledger and isinstance(invoice.get("items"), list):
            self.invoice_records[invoice_id] = {
                "record": copy.deepcopy(invoice),
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "provider": self.observation_provider,
            }
        return invoice

    def _queue(self, kind, invoice_id, summary, payload):
        result = super()._queue(kind, invoice_id, summary, payload)
        self.state_store.save(self)
        return result

    def list_pending(self):
        before = {key: action.status for key, action in self.pending.items()}
        result = super().list_pending()
        if any(before[key] != action.status for key, action in self.pending.items()):
            self.state_store.save(self)
        return result

    def tool_get_status(self, invoice_id):
        result = super().tool_get_status(invoice_id)
        self.state_store.save(self)
        return result

    def tool_send_reminder(self, invoice_id):
        result = super().tool_send_reminder(invoice_id)
        self.state_store.save(self)
        return result

    def handle_webhook(self, raw_body, headers=None):
        result = super().handle_webhook(raw_body, headers)
        self.state_store.save(self)
        return result

    def reject(self, action_id, reason=""):
        super().reject(action_id, reason)
        self.state_store.save(self)

    def approve(self, action_id, approver="human"):
        action = self._pending_or_raise(action_id)
        # Read-only preflight first. If it fails, no outbound request was begun.
        try:
            if action.kind == "send_invoice":
                self._check_send_review(action)
            elif action.kind == "update_invoice":
                from .draft_correction import check
                check(self, action)
            elif action.kind == "cancel_invoice":
                self._check_cancellation(action)
            elif action.kind == "send_reminder":
                self._refresh_invoice(action.invoice_id)
            else:
                raise ValueError("unknown action kind")
            # Preflight may have invalidated an approval after observing newer
            # provider facts. Check pending status inside the persistence guard:
            # otherwise a restart resurrects the obsolete approval and balance.
            action = self._pending_or_raise(action_id)
        except BaseException:
            self.state_store.save(self)
            raise
        # Must be durable BEFORE the first outgoing network effect. A process
        # crash here leaves a non-replayable, operator-visible UNKNOWN record.
        self.state_store.save(self, interrupted=action.id)
        try:
            result = super().approve(action_id, approver)
        except BaseException:
            if action.status == "PENDING":
                self.state_store.save(self, interrupted=action.id)
            else:
                self.state_store.save(self)
            raise
        self.state_store.save(self)
        return result
