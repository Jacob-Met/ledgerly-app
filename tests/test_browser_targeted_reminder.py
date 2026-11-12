"""One-invoice browser action uses native reminder facts, without a batch scan."""
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web-demo" / "python"))
from bridge import Demo
from ledgerly.agent import ApprovalRequired

SOURCE = (ROOT / "fixtures" / "01_simple_usd_hourly.txt").read_text()


class BrowserTargetedReminder(unittest.TestCase):
    def two_sent(self, overdue=True):
        demo = Demo()
        demo.clock.day = date(2026, 10, 8)
        ids = []
        for index in range(2):
            source = SOURCE.replace("Landing page copy - let's go", f"Different invoice {index}")
            drafted = demo.dispatch({"action": "draft", "text": source})
            self.assertTrue(drafted["result"]["unauthorized_send_blocked"])
            invoice_action = drafted["state"]["pending"][0]
            self.assertEqual(invoice_action["kind"], "send_invoice")
            demo.dispatch({"action": "approve", "action_id": invoice_action["id"]})
            ids.append(invoice_action["invoice_id"])
        if overdue:
            due = demo.agent.ledger[ids[0]].due_on
            demo.dispatch({"action": "advance", "day": (due + timedelta(days=2)).isoformat()})
        return demo, ids

    @staticmethod
    def sent_reminders(demo):
        return [(path, body) for method, path, body in demo.mock.requests
                if method == "POST" and path.endswith("/remind")]

    def test_single_selection_does_not_queue_other_clients_or_send(self):
        demo, (first, second) = self.two_sent()
        result = demo.dispatch({"action": "remind_invoice", "invoice_id": second})
        self.assertTrue(result["result"]["ok"])
        self.assertEqual(result["result"]["approval_id"], result["state"]["pending"][0]["id"])
        pending = result["state"]["pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["invoice_id"], second)
        self.assertEqual(pending[0]["kind"], "send_reminder")
        self.assertEqual(self.sent_reminders(demo), [])
        with self.assertRaises(ApprovalRequired):
            demo.agent.client.remind_invoice(second)
        self.assertEqual(self.sent_reminders(demo), [])
        demo.dispatch({"action": "approve", "action_id": pending[0]["id"]})
        reminders = self.sent_reminders(demo)
        self.assertEqual(len(reminders), 1)
        self.assertTrue(reminders[0][0].endswith("/" + second + "/remind"))
        self.assertEqual(demo.agent.ledger[first].reminders_sent, 0)
        self.assertEqual(demo.agent.ledger[second].reminders_sent, 1)

    def test_pending_and_cooldown_are_not_replayed_or_overridden(self):
        demo, (_, second) = self.two_sent()
        original = demo.dispatch({"action": "remind_invoice", "invoice_id": second})["state"]["pending"]
        with self.assertRaisesRegex(ValueError, "awaiting approval"):
            demo.dispatch({"action": "remind_invoice", "invoice_id": second})
        self.assertEqual(demo.agent.list_pending(), original)
        self.assertEqual(self.sent_reminders(demo), [])
        demo.dispatch({"action": "approve", "action_id": original[0]["id"]})
        with self.assertRaisesRegex(ValueError, "cooling down"):
            demo.dispatch({"action": "remind_invoice", "invoice_id": second})
        self.assertEqual(len(self.sent_reminders(demo)), 1)

    def test_rechecks_due_state_payment_and_unknown_identity(self):
        demo, (first, second) = self.two_sent(overdue=False)
        with self.assertRaisesRegex(ValueError, "not overdue"):
            demo.dispatch({"action": "remind_invoice", "invoice_id": first})
        with self.assertRaisesRegex(ValueError, "unknown invoice"):
            demo.dispatch({"action": "remind_invoice", "invoice_id": "unknown_invoice"})
        with self.assertRaisesRegex(ValueError, "Choose one invoice"):
            demo.dispatch({"action": "remind_invoice", "invoice_id": None})
        due = demo.agent.ledger[first].due_on
        demo.dispatch({"action": "advance", "day": (due + timedelta(days=2)).isoformat()})
        demo.mock.simulate_payer_payment(second)
        with self.assertRaisesRegex(ValueError, "no reminder needed"):
            demo.dispatch({"action": "remind_invoice", "invoice_id": second})
        self.assertEqual(demo.agent.ledger[second].status, "PAID")
        self.assertEqual(demo.agent.list_pending(), [])
        self.assertEqual(self.sent_reminders(demo), [])
        # The other unpaid invoice still admits its own approval draft.
        pending = demo.dispatch({"action": "remind_invoice", "invoice_id": first})["state"]["pending"]
        self.assertEqual([action["invoice_id"] for action in pending], [first])

    def test_bulk_scan_remains_available_independently(self):
        demo, (first, second) = self.two_sent()
        demo.dispatch({"action": "remind_invoice", "invoice_id": first})
        scanned = demo.dispatch({"action": "chase"})
        self.assertEqual([a["invoice_id"] for a in scanned["state"]["pending"]], [first, second])
        self.assertEqual(self.sent_reminders(demo), [])


if __name__ == "__main__":
    unittest.main()
