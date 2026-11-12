"""An email's payment claim must not become a merchant attestation."""
import unittest
from pathlib import Path
from ledgerly.agent import Agent
from ledgerly.paypal import SandboxMock

ROOT = Path(__file__).resolve().parents[1]


class DepositInvoiceAttestationTest(unittest.TestCase):
    def test_reported_deposit_is_not_attested_as_received_in_outgoing_invoice(self):
        mock = SandboxMock()
        agent = Agent(mock, {"name": "Fictional Studio", "email_address": "seller@example.com"})
        source = (ROOT / "fixtures/07_partial_payment_deposit.txt").read_text(encoding="utf-8")
        result = agent.tool_create_invoice(source)
        self.assertTrue(result["ok"])
        action = agent.list_pending()[0]
        invoice = mock.invoices[result["invoices"][0]["invoice_id"]]
        note = invoice["detail"]["note"]
        self.assertIn("Customer-reported prior payment: USD 1000", note)
        self.assertIn("not independently verified", note.lower())
        self.assertNotIn("received with thanks", note.lower())
        self.assertIn("customer-reported", action["summary"].lower())
        self.assertNotIn("already received", action["summary"].lower())
        self.assertEqual(action["status"], "PENDING")
        self.assertEqual([r for r in mock.requests if r[1].endswith(("/send", "/payments"))], [])

    def test_without_deposit_keeps_ordinary_note_and_summary(self):
        mock = SandboxMock()
        agent = Agent(mock, {"name": "Fictional Studio", "email_address": "seller@example.com"})
        source = (ROOT / "fixtures/01_simple_usd_hourly.txt").read_text(encoding="utf-8")
        result = agent.tool_create_invoice(source)
        self.assertTrue(result["ok"])
        invoice = mock.invoices[result["invoices"][0]["invoice_id"]]
        self.assertEqual(invoice["detail"]["note"], "Thank you for your business.")
        self.assertNotIn("customer-reported", agent.list_pending()[0]["summary"].lower())


if __name__ == "__main__":
    unittest.main()
