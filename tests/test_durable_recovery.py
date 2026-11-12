"""End-to-end crash/restart receiving for Ledgerly's real Agent + SandboxMock."""
import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ledgerly.operator import run_pending
from ledgerly.paypal import SandboxMock, WebhookError
from ledgerly.recovery import DurableAgent, SnapshotStore, exclusive_state_lock

ROOT = Path(__file__).resolve().parents[1]
INVOICER = {"name": "Example Studio", "email_address": "studio@ledgerly.example"}
SOURCE = (ROOT / "fixtures" / "02_gbp_proofreading.txt").read_text(encoding="utf-8")


class DurableRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "private-ledger.json"
        self.mock = SandboxMock()
        self.today = lambda: date(2026, 10, 1)
        self.agent = self.new_agent()

    def new_agent(self, invoicer=INVOICER):
        return DurableAgent(self.mock, invoicer, today=self.today, state_file=self.file)

    def draft(self):
        result = self.agent.tool_create_invoice(SOURCE)
        self.assertTrue(result["ok"], result)
        return result["invoices"][0]

    def sent(self):
        invoice = self.draft()
        self.agent.approve(invoice["approval_id"])
        return invoice

    def deliver(self, agent, event, *, indent=None):
        agent.webhook_verifier = self.mock.verify_webhook_signature
        raw = json.dumps(event, indent=indent).encode()
        return agent.handle_webhook(raw, self.mock.sign(raw))

    def test_real_cli_second_process_refuses_owned_checkpoint_then_recovers(self):
        env = os.environ.copy()
        env.update({
            "LEDGERLY_ALLOW_NETWORK": "1",
            "LEDGERLY_INVOICER_NAME": INVOICER["name"],
            "LEDGERLY_INVOICER_EMAIL": INVOICER["email_address"],
            "PAYPAL_CLIENT_ID": "fictional-client",
            "PAYPAL_CLIENT_SECRET": "fictional-secret",
        })
        args = [sys.executable, "-m", "ledgerly.operator", "--sandbox",
                "--state-file", str(self.file), "--resume"]
        # Two separate OS processes, not two mocks sharing a Python lock.
        with exclusive_state_lock(self.file):
            busy = subprocess.run(args, cwd=ROOT, env=env, text=True,
                                  capture_output=True, timeout=15)
            self.assertEqual(busy.returncode, 2)
            self.assertIn("already in use", busy.stderr)
            self.assertFalse(self.file.exists())  # no checkpoint overwritten
        idle = subprocess.run(args, cwd=ROOT, env=env, text=True,
                              capture_output=True, timeout=15)
        self.assertEqual(idle.returncode, 0, idle.stderr)
        self.assertIn("No pending approvals", idle.stdout)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)

    def test_private_lock_file_refuses_insecure_sidecar(self):
        sidecar = self.file.with_name(self.file.name + ".lock")
        sidecar.write_text("untrusted")
        sidecar.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private regular"):
            with exclusive_state_lock(self.file):
                self.fail("insecure lock must not be acquired")

    def test_restart_restores_actual_ledger_and_approvable_draft(self):
        invoice = self.draft()
        entry = self.agent.ledger[invoice["invoice_id"]]
        entry.prepaid = Decimal("2.25")
        entry.invoice_due_on = date(2026, 10, 24)
        self.agent.state_store.save(self.agent)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        restored = self.new_agent()
        self.assertEqual(restored.ledger[invoice["invoice_id"]].prepaid, Decimal("2.25"))
        self.assertEqual(restored.ledger[invoice["invoice_id"]].invoice_due_on, date(2026, 10, 24))
        self.assertEqual(restored.list_pending()[0]["id"], invoice["approval_id"])
        # Pending is NOT permission: no send at restore time.
        self.assertFalse(any("/send" in path for _, path, _ in self.mock.requests))
        # Altering the reviewed prior-payment amount must invalidate approval.
        with self.assertRaisesRegex(ValueError, "already REJECTED"):
            restored.approve(invoice["approval_id"])
        self.assertEqual(restored.ledger[invoice["invoice_id"]].status, "DRAFT")

    def test_verified_webhook_repeat_after_process_restart_does_not_reread(self):
        invoice = self.sent()
        event = self.mock.simulate_payer_payment(invoice["invoice_id"])
        self.assertEqual(self.deliver(self.agent, event)["to"], "PAID")
        self.agent = self.new_agent()
        read_count = sum(method == "GET" for method, _, _ in self.mock.requests)
        self.assertTrue(self.deliver(self.agent, event, indent=2)["duplicate"])
        self.assertEqual(sum(method == "GET" for method, _, _ in self.mock.requests), read_count)
        self.assertEqual(self.agent.ledger[invoice["invoice_id"]].balance, Decimal(0))

    def test_payload_replacement_rejected_after_restart_and_signature_still_required(self):
        invoice = self.sent()
        event = self.mock.simulate_payer_payment(invoice["invoice_id"])
        self.deliver(self.agent, event)
        restored = self.new_agent()
        changed = copy.deepcopy(event)
        changed["summary"] = "replaced"
        with self.assertRaisesRegex(WebhookError, "identity conflict"):
            self.deliver(restored, changed)
        raw = json.dumps(event).encode()
        restored.webhook_verifier = self.mock.verify_webhook_signature
        with self.assertRaisesRegex(WebhookError, "signature"):
            restored.handle_webhook(raw, {})

    def test_lost_send_is_fail_closed_during_the_actual_outgoing_call(self):
        invoice = self.draft()
        real_send = self.mock.send_invoice

        def interrupted(invoice_id, body=None):
            data = json.loads(self.file.read_text())
            queued = next(row for row in data["pending"] if row["id"] == invoice["approval_id"])
            self.assertEqual(queued["status"], "INTERRUPTED")
            # Simulate the provider accepting the send, then process termination.
            real_send(invoice_id, body)
            raise KeyboardInterrupt("simulated process termination")

        self.mock.send_invoice = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.agent.approve(invoice["approval_id"])
        self.mock.send_invoice = real_send
        restored = self.new_agent()
        self.assertEqual(restored.pending[invoice["approval_id"]].status, "FAILED")
        self.assertEqual(restored.pending[invoice["approval_id"]].result["outcome"], "UNKNOWN")
        with self.assertRaisesRegex(ValueError, "already FAILED"):
            restored.approve(invoice["approval_id"])
        self.assertEqual(self.mock.invoices[invoice["invoice_id"]]["status"], "SENT")

    def test_disk_failure_prevents_outgoing_network_effect(self):
        invoice = self.draft()
        original = self.agent.state_store.save
        self.agent.state_store.save = lambda *a, **kw: (_ for _ in ()).throw(OSError("full disk"))
        with self.assertRaisesRegex(OSError, "full disk"):
            self.agent.approve(invoice["approval_id"])
        self.agent.state_store.save = original
        self.assertEqual(self.mock.invoices[invoice["invoice_id"]]["status"], "DRAFT")
        self.assertEqual(self.agent.pending[invoice["approval_id"]].status, "PENDING")

    def test_invalid_record_refuses_atomic_restore_and_merchant_confusion(self):
        invoice = self.draft()
        self.file.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private regular"):
            self.new_agent()
        self.file.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "different invoicer"):
            self.new_agent({"name": "Other Studio", "email_address": "other@example.com"})
        data = json.loads(self.file.read_text())
        data["ledger"][0]["total"] = "NaN"
        self.file.write_text(json.dumps(data))
        self.file.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "invoice money"):
            self.new_agent()
        self.assertIn(invoice["invoice_id"], self.agent.ledger)

    def test_operator_resume_requires_fresh_explicit_phrase(self):
        invoice = self.draft()
        restored = self.new_agent()
        output = []
        prompts = []
        def deny(prompt):
            prompts.append(prompt)
            return ""
        self.assertEqual(run_pending(restored, approve_input=deny, emit=output.append), 0)
        self.assertEqual(restored.ledger[invoice["invoice_id"]].status, "DRAFT")
        self.assertTrue(any("SEND " + invoice["invoice_number"] in p for p in prompts))
        self.assertEqual(run_pending(restored, approve_input=lambda _: "SEND " + invoice["invoice_number"],
                                     emit=output.append), 0)
        self.assertEqual(restored.ledger[invoice["invoice_id"]].status, "SENT")

    def test_prior_payment_resume_cannot_silently_record_external_payment(self):
        source = (next((ROOT / "fixtures").glob("07_*"))).read_text(encoding="utf-8")
        result = self.agent.tool_create_invoice(source)
        self.assertTrue(result["ok"], result)
        invoice = result["invoices"][0]
        restored = self.new_agent()
        prompts = []
        def mistaken(prompt):
            prompts.append(prompt)
            return "SEND " + invoice["invoice_number"]
        run_pending(restored, approve_input=mistaken, emit=lambda _: None)
        self.assertTrue(any("SEND+RECORD " in p for p in prompts))
        self.assertEqual(restored.pending[invoice["approval_id"]].status, "PENDING")
        self.assertEqual(restored.ledger[invoice["invoice_id"]].paid_amount, Decimal(0))
        run_pending(restored,
                    approve_input=lambda _: "SEND+RECORD " + invoice["invoice_number"],
                    emit=lambda _: None)
        self.assertEqual(restored.pending[invoice["approval_id"]].status, "APPROVED")
        self.assertGreater(restored.ledger[invoice["invoice_id"]].paid_amount, 0)

    def test_corrupt_event_digest_rejected_without_partial_restore(self):
        invoice = self.sent()
        event = self.mock.simulate_payer_payment(invoice["invoice_id"])
        self.deliver(self.agent, event)
        data = json.loads(self.file.read_text())
        data["seen_events"][event["id"]] = "G" * 64
        self.file.write_text(json.dumps(data))
        self.file.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "webhook digest"):
            self.new_agent()
        self.assertEqual(self.agent.ledger[invoice["invoice_id"]].status, "PAID")


if __name__ == "__main__":
    unittest.main()
