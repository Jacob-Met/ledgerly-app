from ledgerly.extract import RulesExtractor
from ledgerly.agent import Agent
from ledgerly.paypal import SandboxMock

SOURCE = "From: Avery Lane <avery@agency.example>\n\nPlease invoice Nia Gomez at nia@client.example for 3 hours of photo editing at USD 80 per hour. Net 14."

def test_explicit_client_is_actual_draft_destination():
    ex = RulesExtractor().extract(SOURCE)
    assert ex.client_name == "Nia Gomez"
    assert ex.client_email == "nia@client.example"
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert agent.tool_create_invoice(SOURCE)["ok"]
    invoice = next(iter(mock.invoices.values()))
    recipient = invoice["primary_recipients"][0]["billing_info"]
    assert recipient["email_address"] == "nia@client.example"
    assert recipient["name"] == {"given_name": "Nia", "surname": "Gomez"}
    assert invoice["status"] == "DRAFT"
    assert not any(request[1].endswith("/send") for request in mock.requests)

def test_different_named_client_without_email_is_not_sent_to_sender():
    text = SOURCE.replace(" at nia@client.example", "")
    ex = RulesExtractor().extract(text)
    assert any(issue.field == "client_email" for issue in ex.errors)
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert not agent.tool_create_invoice(text)["ok"]
    assert mock.requests == []

def test_conflicting_explicit_delivery_address_requires_review():
    ex = RulesExtractor().extract(SOURCE + "\nSend invoices to accounts@other.example")
    assert any(issue.field == "client_email" for issue in ex.errors)

def test_flat_fee_explicit_client_is_actual_draft_destination():
    text = SOURCE.replace("3 hours of photo editing at USD 80 per hour", "logo design at USD 500")
    ex = RulesExtractor().extract(text)
    assert ex.client_name == "Nia Gomez"
    assert ex.client_email == "nia@client.example"
    assert ex.line_items[0].desc == "Logo design"
    assert ex.total() == 500
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert agent.tool_create_invoice(text)["ok"]
    invoice = next(iter(mock.invoices.values()))
    assert invoice["primary_recipients"][0]["billing_info"]["email_address"] == "nia@client.example"
    assert invoice["status"] == "DRAFT"

def test_flat_fee_named_client_without_email_requires_review():
    text = SOURCE.replace(" at nia@client.example", "").replace("3 hours of photo editing at USD 80 per hour", "logo design at USD 500")
    assert any(i.field == "client_email" for i in RulesExtractor().extract(text).errors)


import pytest

@pytest.mark.parametrize("instruction,work,expected_name,expected_email,total", [
    ("Please invoice Nia Gomez at nia@client.example for ", "logo design at USD 500.", "Nia", "nia@client.example", 500),
    ("Please invoice Nia Gomez at nia@client.example for ", "3 hours of photo editing at USD 80 per hour and 2 prints at USD 15 each.", "Nia", "nia@client.example", 270),
    ("Bill to: Nia Gomez\nEmail: nia@client.example\n\n", "Logo design at USD 500.", "Nia", "nia@client.example", 500),
    ("Client: Nia Gomez\nnia@client.example\n\n", "3 hours of photo editing at USD 80 per hour.", "Nia", "nia@client.example", 240),
    ("Bill to:\nNia Gomez <nia@client.example>\n\n", "Logo design at USD 500.", "Nia", "nia@client.example", 500),
    ("Please invoice Nia Gomez <nia@client.example> for ", "logo design at USD 500.", "Nia", "nia@client.example", 500),
    ("Bill to Nia Gomez at nia@client.example\n\n", "Logo design at USD 500.", "Nia", "nia@client.example", 500),
    ("Please invoice me for ", "logo design at USD 500.", "Avery", "avery@agency.example", 500),
    ("Please invoice Avery Lane for ", "logo design at USD 500.", "Avery", "avery@agency.example", 500),
])
def test_recipient_selection_is_independent_of_work_grammar(instruction, work, expected_name, expected_email, total):
    text = "From: Avery Lane <avery@agency.example>\n\n" + instruction + work + "\nNet 14."
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    result = agent.tool_create_invoice(text)
    assert result["ok"], result
    invoice = next(iter(mock.invoices.values()))
    recipient = invoice["primary_recipients"][0]["billing_info"]
    assert recipient["email_address"] == expected_email
    assert recipient["name"]["given_name"] == expected_name
    assert RulesExtractor().extract(text).total() == total
    assert invoice["status"] == "DRAFT"
    assert not any(request[1].endswith("/send") for request in mock.requests)

@pytest.mark.parametrize("instruction", [
    "Bill to: Nia Gomez\n\n",
    "Client: Nia Gomez\n\n",
    "Bill to: Nia Gomez <nia@client.example>\nEmail: other@client.example\n\n",
    "Bill to: Nia Gomez <nia@client.example>\n\nSend invoices to accounts@other.example\n",
    "Send invoices to nia@client.example\nSend invoices to other@client.example\n",
    "Bill to: Nia / Accounts team\n\n",
])
def test_unresolved_or_conflicting_explicit_recipient_refuses_mutation(instruction):
    text = "From: Avery Lane <avery@agency.example>\n\n" + instruction + "Logo design at USD 500.\nNet 14."
    ex = RulesExtractor().extract(text)
    assert any(issue.field == "client_email" for issue in ex.errors)
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert not agent.tool_create_invoice(text)["ok"]
    assert mock.requests == []

def test_wrapped_recipient_instruction_never_reverts_to_sender():
    text = SOURCE.replace("Nia Gomez at nia@client.example for", "Nia Gomez\nat nia@client.example\nfor")
    ex = RulesExtractor().extract(text)
    assert ex.client_name == "Nia Gomez"
    assert ex.client_email == "nia@client.example"
    # Work grammar may still need review; that cannot change recipient identity.


@pytest.mark.parametrize("instruction_text", [
    'Please invoice "Nia Gomez" at nia@client.example for logo design at USD 500.',
    'Please invoice Zoë Meyer for logo design at USD 500.',
])
def test_unsupported_named_request_cannot_fall_back_to_sender(instruction_text):
    text = "From: Avery Lane <avery@agency.example>\n\n" + instruction_text + "\nNet 14."
    ex = RulesExtractor().extract(text)
    assert any(issue.field == "client_email" for issue in ex.errors)
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert not agent.tool_create_invoice(text)["ok"]
    assert mock.requests == []


@pytest.mark.parametrize("framing", ["- Please invoice", "Hi there, please invoice"])
def test_bullets_and_greeting_prose_use_actual_named_destination(framing):
    text = f"From: Owen Bell <owen@agency.example>\n\n{framing} Hana Costa at hana@client.example for illustration at USD 320.\nTotal USD 320. Net 14."
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    result = agent.tool_create_invoice(text)
    assert result["ok"], result
    invoice = next(iter(mock.invoices.values()))
    recipient = invoice["primary_recipients"][0]["billing_info"]
    assert recipient["email_address"] == "hana@client.example"
    assert recipient["name"] == {"given_name": "Hana", "surname": "Costa"}
    assert invoice["status"] == "DRAFT"
    assert not any(request[1].endswith("/send") for request in mock.requests)

def test_two_inline_named_recipients_cannot_silently_pick_one():
    text = "From: Owen Bell <owen@agency.example>\n\nHi, please invoice Hana Costa at hana@client.example for illustration at USD 320. Also please invoice Ben West at ben@other.example for editing at USD 100.\nNet 14."
    ex = RulesExtractor().extract(text)
    assert any(issue.field == "client_email" for issue in ex.errors)
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert not agent.tool_create_invoice(text)["ok"]
    assert mock.requests == []


def test_conflicting_recipient_is_unselected_and_does_not_spend_model_inference():
    from ledgerly.extract import HybridExtractor
    text = ("From: Avery Lane <avery@agency.example>\n\n"
            "Bill to: Nia Gomez <nia@client.example>\n\n"
            "Send invoices to accounts@other.example\n\n"
            "Logo design at USD 500.\nNet 14.")
    class MustNotChooseRecipient:
        def extract(self, text):
            raise AssertionError("Inference cannot decide between explicit recipients")
    result = HybridExtractor(RulesExtractor(), MustNotChooseRecipient(), threshold=0.9).extract(text)
    assert result.client_name is None and result.client_email is None
    assert result.source == "rules" and result.total() == 500
    assert any("Conflicting explicit" in i.message for i in result.errors)


def test_wrapped_valid_recipient_still_reaches_model_for_unparsed_work():
    from ledgerly.extract import HybridExtractor
    text = SOURCE.replace("Nia Gomez at nia@client.example for",
                          "Nia Gomez\nat nia@client.example\nfor")
    calls = []
    class ExistingFallback:
        def extract(self, source):
            calls.append(source)
            return RulesExtractor().extract(SOURCE)
    result = HybridExtractor(RulesExtractor(), ExistingFallback(), threshold=0.9).extract(text)
    assert calls == [text]
    assert result.client_email == "nia@client.example"
    assert result.total() == 240


@pytest.mark.parametrize("instruction", [
    "Please invoice Northfield Studio, billing email accounts@northfield.example, in CAD.",
    "Invoice Northfield Studio, billing email: accounts@northfield.example.",
    "Bill Northfield Studio, invoice e-mail address accounts@northfield.example.",
    "Please invoice Northfield Studio,\nbilling email accounts@northfield.example, in CAD.",
])
def test_named_billing_email_is_the_actual_unsent_draft_destination(instruction):
    text = "From: Mina Holt <mina@northfield.example>\n" + instruction + "\nLogo design at CAD 500.\nNet 14."
    ex = RulesExtractor().extract(text)
    assert ex.client_name == "Northfield Studio"
    assert ex.client_email == "accounts@northfield.example"
    mock = SandboxMock()
    agent = Agent(mock, {"name":"Studio", "email_address":"studio@example.test"})
    assert agent.tool_create_invoice(text)["ok"]
    invoice = next(iter(mock.invoices.values()))
    assert invoice["primary_recipients"][0]["billing_info"]["email_address"] == "accounts@northfield.example"
    assert invoice["status"] == "DRAFT"
    assert not any(request[1].endswith("/send") for request in mock.requests)


@pytest.mark.parametrize("instruction", [
    "If approved, please invoice Northfield Studio, billing email accounts@northfield.example.",
    "Please invoice Northfield Studio, billing email accounts@northfield.example if approved.",
    "Please invoice Northfield Studio, billing email missing-address.",
    "Please invoice Northfield Studio, billing email accounts@northfield.example or other@northfield.example.",
    'Please invoice "Northfield Studio", billing email accounts@northfield.example.',
    "Please invoice Northfield Studio, billing email accounts@northfield.example.\nSend invoices to other@northfield.example",
])
def test_unclear_billing_email_is_unselected_and_cannot_create_or_invoke_model(instruction):
    from ledgerly.extract import HybridExtractor
    class ForbiddenModel:
        def extract(self, text):
            raise AssertionError("An unresolved billing choice must remain human")
    text = "From: Mina Holt <mina@northfield.example>\n" + instruction + "\nLogo design at CAD 500.\nNet 14."
    extractor = HybridExtractor(RulesExtractor(), ForbiddenModel(), threshold=0.9)
    ex = extractor.extract(text)
    assert ex.client_name is None and ex.client_email is None
    assert any(issue.field == "client_email" for issue in ex.errors)
    mock = SandboxMock()
    agent = Agent(mock, {"name":"Studio", "email_address":"studio@example.test"}, extractor=extractor)
    assert not agent.tool_create_invoice(text)["ok"]
    assert not mock.requests


def test_quoted_named_billing_email_does_not_replace_current_sender():
    text = ("From: Mina Holt <mina@northfield.example>\n"
            "Logo design at CAD 500.\nNet 14.\n"
            "> Please invoice Northfield Studio, billing email old@northfield.example.")
    ex = RulesExtractor().extract(text)
    assert ex.client_name == "Mina Holt" and ex.client_email == "mina@northfield.example"

@pytest.mark.parametrize("instruction", ["invoice me in EUR", "bill us in GBP", "invoice me", "bill us"])
def test_currency_after_self_reference_is_not_a_new_recipient(instruction):
    source = "From: Farah Quinn <farah@example.test>\nPlease " + instruction + " for logo design at EUR 50.\nNet 10."
    ex = RulesExtractor().extract(source)
    assert ex.client_name == "Farah Quinn" and ex.client_email == "farah@example.test"
    assert not [i for i in ex.errors if i.field in {"client_name", "client_email"}]

def test_self_reference_does_not_override_explicit_billing_destination():
    source = "From: Farah Quinn <farah@example.test>\nPlease invoice me in EUR for logo design at EUR 50.\nBill to: Juniper Studio <accounts@example.test>\nNet 10."
    ex = RulesExtractor().extract(source)
    assert ex.client_name == "Juniper Studio" and ex.client_email == "accounts@example.test"
