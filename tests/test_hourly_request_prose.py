from decimal import Decimal
import pytest
from ledgerly.extract import RulesExtractor
from ledgerly.agent import Agent
from ledgerly.paypal import SandboxMock

def job(line):
    return "From: Inez Calder <inez@example.test>\nSubject: Editorial work\n\n" + line + "\nNet 21."

@pytest.mark.parametrize("line,quantity,price", [
    ("Please invoice 7 hours at $95 USD per hour for editorial illustration.", "7", "95"),
    ("Bill us 2.5 hrs @ £40/hr for proofreading", "2.5", "40"),
    ("Invoice seven hours of editing at $95/hr", "7", "95"),
    ("7 hours at $95/hr", "7", "95"),
])
def test_explicit_hours_survive_request_wording(line, quantity, price):
    ex=RulesExtractor().extract(job(line))
    assert len(ex.line_items)==1
    assert ex.errors==[]
    assert ex.line_items[0].qty==Decimal(quantity)
    assert ex.line_items[0].unit_price==Decimal(price)
    assert ex.total()==Decimal(quantity)*Decimal(price)

@pytest.mark.parametrize("line", [
    "Please invoice several hours at $95/hr for editing",
    "We worked seven hours at $95/hr",
    "Editorial consulting at $95 per hour",
    "Please invoice 7 hours at $95/day for editing",
    "Bill us 7 hours of editing at $95 per minute",
    "Please invoice 2 days at USD 60 per hour for illustration.",
    "Invoice 2 days of illustration at USD 60/hr",
    "Illustration: 2 days @ USD 60/hr",
])
def test_unknown_or_unsupported_hours_never_become_one(line):
    text=job(line);ex=RulesExtractor().extract(text)
    assert ex.line_items[0].qty is None
    assert any(i.field=="line_items[0].qty" for i in ex.errors)
    mock=SandboxMock();agent=Agent(mock,{"name":"Reviewer","email_address":"reviewer@example.test"})
    result=agent.tool_create_invoice(text)
    assert not result["ok"] and result["needs_review"]
    assert mock.requests==[] and agent.list_pending()==[]

@pytest.mark.parametrize("line", ["Invoice template redesign — $400", "Seven-hour workshop package — $400", "Website build — $400"])
def test_flat_fees_keep_their_single_item_quantity(line):
    ex=RulesExtractor().extract(job(line))
    assert ex.errors==[] and ex.line_items[0].qty==1 and ex.total()==400

def test_existing_approximation_policy_remains_visible():
    ex=RulesExtractor().extract(job("Please invoice about 7 hours at $95/hr for editing"))
    assert ex.line_items[0].qty==7
    assert any(i.field=="line_items[0].qty" and i.severity=="warning" for i in ex.issues)

def test_explicit_hourly_request_drafts_correct_amount_but_never_sends():
    mock=SandboxMock();agent=Agent(mock,{"name":"Reviewer","email_address":"reviewer@example.test"})
    result=agent.tool_create_invoice(job("Please invoice 7 hours at $95 USD per hour for editorial illustration."))
    assert result["ok"]
    invoice=next(iter(mock.invoices.values()))
    assert invoice["items"][0]["quantity"]=="7"
    assert Decimal(invoice["amount"]["value"])==665
    assert invoice["status"]=="DRAFT"
    assert len(agent.list_pending())==1
    assert all(not request[1].endswith('/send') for request in mock.requests)


def test_explicit_hourly_work_and_named_setup_fee_preserve_full_total():
    text=job("Invoice 4 hours of QA at USD 75/hr, plus a USD 50 setup fee.")
    ex=RulesExtractor().extract(text)
    assert [(i.qty, i.unit_price) for i in ex.line_items] == [(Decimal(4), Decimal(75)), (Decimal(1), Decimal(50))]
    assert ex.total() == Decimal(350) and not ex.errors
    mock=SandboxMock();agent=Agent(mock,{"name":"Reviewer","email_address":"reviewer@example.test"})
    result=agent.tool_create_invoice(text)
    assert result["ok"]
    invoice=next(iter(mock.invoices.values()))
    assert Decimal(invoice["amount"]["value"]) == Decimal(350)
    assert invoice["status"] == "DRAFT"
    assert all(not request[1].endswith("/send") for request in mock.requests)


def test_separating_hourly_work_and_setup_fee_recovers_full_total():
    text=job("Invoice 4 hours of QA at USD 75/hr\nSetup fee — USD 50")
    ex=RulesExtractor().extract(text)
    assert ex.errors==[] and len(ex.line_items)==2 and ex.total()==350
    mock=SandboxMock();agent=Agent(mock,{"name":"Reviewer","email_address":"reviewer@example.test"})
    assert agent.tool_create_invoice(text)["ok"]
    invoice=next(iter(mock.invoices.values()))
    assert Decimal(invoice["amount"]["value"])==350
    assert invoice["status"]=="DRAFT" and len(agent.list_pending())==1


@pytest.mark.parametrize("line,quantity,price", [
    ("Please invoice Nia Gomez at nia42@example.test for 2.5 hours of audio cleanup at USD 68 per hour.", "2.5", "68"),
    ("Bill Rowan Chen for four hours of caption editing at GBP 35/hr.", "4", "35"),
    ("Please invoice Acme Studio for 6 hrs at USD 72/hr for animation cleanup.", "6", "72"),
])
def test_named_recipient_preserves_explicit_work_quantity(line, quantity, price):
    text = job(line)
    if "Rowan Chen" in line: text = text.replace("Inez Calder", "Rowan Chen")
    if "Acme Studio" in line: text = text.replace("Inez Calder", "Acme Studio")
    ex = RulesExtractor().extract(text)
    assert not ex.errors
    assert ex.line_items[0].qty == Decimal(quantity)
    assert ex.line_items[0].unit_price == Decimal(price)
    assert ex.total() == Decimal(quantity) * Decimal(price)


def test_named_recipient_with_unspecified_hours_still_requires_review():
    text = job("Please invoice Nia Gomez at nia42@example.test for several hours of audio cleanup at USD 68 per hour.")
    ex = RulesExtractor().extract(text)
    assert ex.line_items[0].qty is None
    assert ex.errors
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert not agent.tool_create_invoice(text)["ok"]
    assert mock.requests == []


@pytest.mark.parametrize("line,total", [
    ("Please bill Nia Gomez at nia42@example.test for 2.5 hours of audio cleanup at USD 68 per hour and 3 transcript pages at USD 12 each.", "206"),
    ("Invoice Rowan Chen for four hours of caption editing at GBP 35/hr plus 2 subtitle files at GBP 18 each.", "176"),
    ("3 product photos at USD 45 each and 2 print sets at USD 20 each.", "175"),
])
def test_explicit_compound_items_keep_each_quantity_and_rate(line, total):
    text = job(line).replace("Inez Calder", "Rowan Chen") if "Rowan Chen" in line else job(line)
    ex = RulesExtractor().extract(text)
    assert len(ex.line_items) == 2
    assert not ex.errors
    assert ex.total() == Decimal(total)


@pytest.mark.parametrize("line", [
    "Invoice 4 hours of QA at USD 75/hr and several print sets at USD 20 each.",
    "Invoice 2 days of QA at USD 75/hr and 3 print sets at USD 20 each.",
])
def test_compound_unknown_quantity_or_unit_conversion_remains_blocked(line):
    text = job(line)
    ex = RulesExtractor().extract(text)
    assert ex.errors
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert not agent.tool_create_invoice(text)["ok"]
    assert mock.requests == []


@pytest.mark.parametrize("separator", ["; ", ";", ";  "])
def test_semicolon_items_preserve_both_services(separator):
    text = job("Invoice 3 hours of audio editing at USD 70/hr" + separator + "2 transcript pages at USD 15 each.")
    ex = RulesExtractor().extract(text)
    assert not ex.errors
    assert [(i.desc, i.qty, i.unit_price) for i in ex.line_items] == [
        ("Audio editing", Decimal(3), Decimal(70)),
        ("Transcript pages", Decimal(2), Decimal(15)),
    ]
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert agent.tool_create_invoice(text)["ok"]
    invoice = next(iter(mock.invoices.values()))
    assert Decimal(invoice["amount"]["value"]) == 240
    assert invoice["status"] == "DRAFT"

@pytest.mark.parametrize("second", [
    "2 transcript pages at USD 15 each if approved.",
    "several transcript pages at USD 15 each.",
    "2 days of transcription at USD 15/hr.",
])
def test_semicolon_does_not_discard_uncertainty(second):
    text = job("Invoice 3 hours of audio editing at USD 70/hr; " + second)
    mock = SandboxMock()
    agent = Agent(mock, {"name": "Reviewer", "email_address": "reviewer@example.test"})
    assert not agent.tool_create_invoice(text)["ok"]
    assert not mock.requests
