"""End-to-end Sandbox follow-up after real DurableAgent restart, no outgoing by default."""
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ledgerly.operator import run_follow_up, run_pending
from ledgerly.paypal import PayPalError, SandboxMock
from ledgerly.recovery import DurableAgent

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "fixtures" / "02_gbp_proofreading.txt").read_text()
INVOICER = {"name": "Fixture Freelancer", "email_address": "freelancer@ledgerly.example"}


class Clock:
    def __init__(self, current):
        self.current = current
    def __call__(self):
        return self.current


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_file = Path(self.tmp.name) / "private.json"
        self.clock = Clock(date(2026, 10, 1))
        self.mock = SandboxMock()
        self.agent = self.restored()

    def restored(self):
        return DurableAgent(self.mock, INVOICER, state_file=self.state_file, today=self.clock)

    def sent(self):
        item = self.agent.tool_create_invoice(SOURCE)["invoices"][0]
        self.agent.approve(item["approval_id"])
        return item

    def reminders(self):
        return [row for row in self.mock.requests if row[1].endswith("/remind")]

    def test_restart_to_real_overdue_review_and_explicit_reminder_approval(self):
        invoice = self.sent()
        self.clock.current = date(2026, 11, 10)
        restarted = self.restored()
        output, prompts = [], []
        def no(prompt):
            prompts.append(prompt)
            return ""
        self.assertEqual(run_follow_up(restarted, approve_input=no, emit=output.append), 0)
        self.assertTrue(any("days overdue" in line for line in output), output)
        self.assertTrue(any("GBP" in t and invoice["invoice_number"] in t for t in output))
        self.assertTrue(any("REMIND " + invoice["invoice_id"] in prompt for prompt in prompts))
        self.assertFalse(self.reminders())
        pending = [a for a in restarted.list_pending() if a["kind"] == "send_reminder"]
        self.assertEqual(len(pending), 1)
        self.assertIn("Subject", " ".join(output))
        next_process = self.restored()
        self.assertEqual(run_pending(next_process,
                                     approve_input=lambda _: "REMIND " + invoice["invoice_id"],
                                     emit=output.append), 0)
        self.assertEqual(len(self.reminders()), 1)
        self.assertEqual(next_process.ledger[invoice["invoice_id"]].reminders_sent, 1)
        fresh_again = self.restored()
        self.assertEqual(fresh_again.ledger[invoice["invoice_id"]].reminders_sent, 1)
        self.assertEqual(run_follow_up(fresh_again, approve_input=lambda _: "", emit=output.append), 0)
        self.assertEqual(len(self.reminders()), 1)
        self.assertFalse([a for a in fresh_again.list_pending() if a["kind"] == "send_reminder"])

    def test_paid_provider_invoice_is_not_reminded_even_if_local_cache_is_stale(self):
        invoice = self.sent()
        self.clock.current = date(2026, 11, 10)
        self.mock.simulate_payer_payment(invoice["invoice_id"])
        restarted = self.restored()
        output=[]
        self.assertEqual(run_follow_up(restarted,
                                       approve_input=lambda _: self.fail("must never ask to send a paid invoice"),
                                       emit=output.append), 0)
        self.assertEqual(restarted.ledger[invoice["invoice_id"]].status, "PAID")
        self.assertEqual(restarted.ledger[invoice["invoice_id"]].balance, Decimal(0))
        self.assertFalse(self.reminders())
        self.assertTrue(any("PAID" in row for row in output))

    def test_partial_payment_shows_provider_balance_before_any_reminder(self):
        invoice = self.sent()
        self.mock.simulate_payer_payment(invoice["invoice_id"], Decimal("100"))
        self.clock.current = date(2026, 11, 10)
        restarted = self.restored()
        output=[]
        self.assertEqual(run_follow_up(restarted,
                                       approve_input=lambda _: "WRONG PHRASE",
                                       emit=output.append), 0)
        self.assertEqual(restarted.ledger[invoice["invoice_id"]].paid_amount, Decimal("100"))
        self.assertFalse(self.reminders())
        self.assertTrue(any("remaining" in row and "PARTIALLY_PAID" in row for row in output))

    def test_explicit_rejection_discards_review_and_allows_a_new_one(self):
        invoice = self.sent()
        self.clock.current = date(2026, 11, 10)
        restarted = self.restored()
        self.assertEqual(run_follow_up(restarted,
                                       approve_input=lambda _: "REJECT " + invoice["invoice_id"],
                                       emit=lambda _: None), 0)
        old = [a for a in restarted.pending.values() if a.kind == "send_reminder"]
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0].status, "REJECTED")
        self.assertFalse(self.reminders())
        next_process = self.restored()
        self.assertEqual(run_follow_up(next_process, approve_input=lambda _: "",
                                       emit=lambda _: None), 0)
        next_reviews = [a for a in next_process.pending.values() if a.kind == "send_reminder"]
        self.assertEqual(len(next_reviews), 2)
        self.assertEqual(sum(a.status == "PENDING" for a in next_reviews), 1)
        self.assertFalse(self.reminders())

    def test_missing_provider_read_does_not_queue_any_reminder(self):
        invoice = self.sent()
        self.clock.current = date(2026, 11, 10)
        self.mock.get_invoice = lambda _: (_ for _ in ()).throw(
            PayPalError(503, "UNAVAILABLE", "Provider cannot be reached"))
        restarted = self.restored()
        output=[]
        self.assertEqual(run_follow_up(restarted, approve_input=lambda _: self.fail("no prompt"),
                                       emit=output.append), 3)
        self.assertFalse([a for a in restarted.pending.values() if a.kind == "send_reminder"])
        self.assertFalse(self.reminders())
        self.assertTrue(any("Could not refresh" in row for row in output))

    def test_unknown_invoice_collection_has_no_effect(self):
        messages=[]
        self.assertEqual(run_follow_up(self.agent, approve_input=lambda _: self.fail("no prompt"),
                                       emit=messages.append), 0)
        self.assertTrue(any("No saved invoices" in row for row in messages))
        self.assertFalse(self.mock.requests)

    def test_not_yet_due_invoice_is_not_reminded(self):
        invoice = self.sent()
        self.clock.current = date(2026, 10, 1)
        fresh = self.restored()
        self.assertEqual(run_follow_up(fresh, approve_input=lambda _: self.fail("no prompt"),
                                       emit=lambda _: None), 0)
        self.assertFalse([a for a in fresh.pending.values() if a.kind == "send_reminder"])
        self.assertFalse(self.reminders())


    def test_paid_after_skipped_review_does_not_resurrect_obsolete_approval(self):
        """A stale paid-invoice reminder must stay rejected after restart."""
        invoice = self.sent()
        self.clock.current = date(2026, 11, 10)
        first = self.restored()
        self.assertEqual(run_follow_up(first, approve_input=lambda _: "", emit=lambda _: None), 0)
        self.assertEqual(len(first.list_pending()), 1)
        self.mock.simulate_payer_payment(invoice["invoice_id"])
        second = self.restored()
        messages = []
        self.assertEqual(
            run_pending(second,
                        approve_input=lambda _: "REMIND " + invoice["invoice_id"],
                        emit=messages.append), 3)
        self.assertTrue(any("Approval failed" in text for text in messages))
        self.assertFalse(self.reminders())
        third = self.restored()
        self.assertEqual(third.ledger[invoice["invoice_id"]].status, "PAID")
        self.assertEqual(third.ledger[invoice["invoice_id"]].balance, Decimal("0"))
        self.assertFalse([action for action in third.list_pending()
                          if action["kind"] == "send_reminder"])


if __name__ == "__main__":
    unittest.main()
