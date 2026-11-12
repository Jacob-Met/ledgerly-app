"""Human approval reviews must show the amount still at stake."""
from copy import deepcopy
from pathlib import Path
import unittest

from ledgerly.agent import Agent
from ledgerly.paypal import SandboxMock
from ledgerly.review import invoice_review


ROOT = Path(__file__).resolve().parents[1]


class InvoiceReviewBalanceTest(unittest.TestCase):
    def create(self, fixture):
        mock = SandboxMock()
        agent = Agent(mock, {"name": "Fictional Studio", "email_address": "studio@example.com"})
        result = agent.tool_create_invoice((ROOT / "fixtures" / fixture).read_text(encoding="utf-8"))
        self.assertTrue(result["ok"], result)
        return agent, mock, agent.list_pending()[0]

    def test_no_deposit_review_shows_confirmable_total(self):
        agent, mock, action = self.create("01_simple_usd_hourly.txt")
        original = deepcopy(action)
        calls = list(mock.requests)
        view = invoice_review(action)
        self.assertIn("Invoice total: USD 1320.00", view)
        self.assertNotIn("Remaining after reported payment:", view)
        self.assertNotIn("Reported prior payment (unverified):", view)
        self.assertEqual(action, original)
        self.assertEqual(mock.requests, calls)
        self.assertEqual(agent.list_pending()[0]["status"], "PENDING")

    def test_deposit_review_shows_source_claim_and_prospective_balance(self):
        agent, mock, action = self.create("07_partial_payment_deposit.txt")
        original = deepcopy(action)
        calls = list(mock.requests)
        view = invoice_review(action)
        self.assertIn("Invoice total: USD 3500.00", view)
        self.assertIn("Reported prior payment (unverified): USD 1000.00", view)
        self.assertIn("Remaining after reported payment: USD 2500.00", view)
        self.assertEqual(action, original)
        self.assertEqual(mock.requests, calls)
        self.assertEqual(agent.list_pending()[0]["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
