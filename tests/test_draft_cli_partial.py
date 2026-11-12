"""Actual operator paths retain confirmed invoices after a later draft fails."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ledgerly.agent import Agent
from ledgerly.live import run as live_run
from ledgerly.operator import run_intake
from test_draft_creation_outcomes import InterruptedMock, INVOICER, SOURCE
from test_live_pilot import options


class OperatorInterruptedCreation(unittest.TestCase):
    def test_interactive_operator_shows_retained_draft_and_unknown(self):
        client = InterruptedMock("lost_response", at=2)
        messages = []
        agent = Agent(client, dict(INVOICER))
        code = run_intake(agent, SOURCE, approve_input=lambda prompt: ("CREATE" if prompt.startswith("Type CREATE") else self.fail("No send prompt allowed")),
                          emit=messages.append, sandbox=True)
        self.assertEqual(3, code)
        self.assertEqual(2, len(client.invoices))  # later attempted provider effect occurred
        self.assertEqual(1, len(agent.pending))    # only first draft was confirmed
        output = "\n".join(messages)
        self.assertIn("Confirmed earlier draft EUR LDG-0001", output)
        self.assertIn("UNKNOWN", output)
        self.assertIn("Inspect provider drafts", output)
        self.assertNotIn("No provider drafts created", output)
        self.assertFalse(any(path.endswith("/send") for _, path, _ in client.requests))

    def test_live_pilot_shows_retained_draft_and_unknown(self):
        client = InterruptedMock("lost_response", at=2)
        messages = []
        with tempfile.TemporaryDirectory() as directory:
            source_file = Path(directory) / "job.txt"
            source_file.write_text(SOURCE)
            with patch.dict(os.environ, {"LEDGERLY_ALLOW_NETWORK": "1"}):
                code = live_run(options(source=str(source_file), mode="sandbox"),
                                emit=messages.append, confirmation=lambda _: "CREATE", client=client)
        self.assertEqual(3, code)
        output = "\n".join(messages)
        self.assertIn("Confirmed draft EUR LDG-0001", output)
        self.assertIn("UNKNOWN", output)
        self.assertIn("Inspect provider drafts", output)
        self.assertNotIn("No drafts created", output)
        self.assertFalse(any(path.endswith("/send") for _, path, _ in client.requests))

    def test_number_failure_with_no_draft_still_refuses_and_warns(self):
        client = InterruptedMock("number_failure", at=1)
        messages = []
        code = run_intake(Agent(client, dict(INVOICER)), SOURCE,
                          approve_input=lambda prompt: ("CREATE" if prompt.startswith("Type CREATE") else self.fail("No send prompt allowed")),
                          emit=messages.append, sandbox=True)
        self.assertEqual(3, code)
        self.assertEqual([], list(client.invoices))
        output = "\n".join(messages)
        self.assertIn("NOT_ATTEMPTED", output)
        self.assertIn("Inspect provider drafts", output)


if __name__ == "__main__":
    unittest.main()
