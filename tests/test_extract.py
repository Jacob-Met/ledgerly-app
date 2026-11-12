import json
from decimal import Decimal

import pytest

from conftest import FIXTURES, load
from ledgerly.extract import (
    HybridExtractor, LLMExtractor, RulesExtractor, split_by_currency, validate,
)

R = RulesExtractor()


def items(ex):
    return [(li.desc, li.qty, li.unit_price, li.currency) for li in ex.line_items]


def test_all_twelve_fixtures_present():
    assert sorted(path.name[:2] for path in FIXTURES.glob("[0-9][0-9]_*.txt")) == [f"{number:02d}" for number in range(1, 13)]


def test_simple_usd_hourly():
    ex = R.extract(load("01_simple_usd_hourly.txt"))
    assert ex.client_name == "Maya Chen"
    assert ex.client_email == "maya.chen@brightfern.example"
    assert ex.currency == "USD"
    assert ex.due_days == 15
    assert items(ex) == [("Copywriting", 12, 85, "USD"), ("SEO keyword audit", 1, 300, "USD")]
    assert ex.total() == Decimal("1320")
    assert ex.confidence == 1.0 and ex.issues == []


@pytest.mark.parametrize("fname,ccy,total,due", [
    ("02_gbp_proofreading.txt", "GBP", Decimal("800"), 30),
    ("03_eur_fixed_project.txt", "EUR", Decimal("2550"), 14),
    ("08_jpy_zero_decimal.txt", "JPY", Decimal("180000"), 30),
    ("09_table_cad.txt", "CAD", Decimal("740"), 10),
    ("10_billing_address_net45.txt", "USD", Decimal("2680"), 45),
])
def test_currency_totals_terms(fname, ccy, total, due):
    ex = R.extract(load(fname))
    assert ex.currency == ccy
    assert ex.total() == total
    assert ex.due_days == due
    assert not ex.errors


def test_multi_currency_flagged_and_split():
    ex = R.extract(load("04_multi_currency.txt"))
    assert ex.currencies() == ["EUR", "USD"]
    assert any(i.field == "currency" and i.severity == "warning" for i in ex.issues)
    parts = split_by_currency(ex)
    assert {p.currency: p.total() for p in parts} == {"EUR": Decimal("360"), "USD": Decimal("1900")}


def test_missing_email_is_error_and_name_from_signature():
    ex = R.extract(load("05_missing_email.txt"))
    assert ex.client_email is None
    assert ex.client_name == "Darnell Brooks"
    assert any(i.field == "client_email" and i.severity == "error" for i in ex.issues)
    assert ex.confidence < 1.0


def test_ambiguous_qty_is_error_due_on_receipt():
    ex = R.extract(load("06_ambiguous_qty.txt"))
    assert ex.due_days == 0
    assert ex.line_items[1].qty is None
    assert any(i.field == "line_items[1].qty" and i.severity == "error" for i in ex.issues)


def test_range_and_approx_qty_are_warnings():
    ex = R.extract(load("11_range_qty_approx.txt"))
    assert [li.qty for li in ex.line_items] == [10, 5]
    sev = {i.field: i.severity for i in ex.issues}
    assert sev["line_items[0].qty"] == "warning" and sev["line_items[1].qty"] == "warning"
    assert not ex.errors and 0.5 < ex.confidence < 1.0


def test_partial_payment_detected_not_a_line_item():
    ex = R.extract(load("07_partial_payment_deposit.txt"))
    assert ex.amount_paid == Decimal("1000")
    assert ex.total() == Decimal("3500")
    assert all("deposit" not in li.desc.lower() for li in ex.line_items)


def test_billing_email_preferred_over_sender():
    ex = R.extract(load("10_billing_address_net45.txt"))
    assert ex.client_email == "ap@atlasfreight.example"


def test_injection_text_not_turned_into_line_item():
    ex = R.extract(load("12_prompt_injection.txt"))
    assert items(ex) == [("Bug fixing", 5, 90, "USD")]
    assert ex.client_email == "victor@quickflip.example"


def test_table_total_row_is_not_an_item():
    ex = R.extract(load("09_table_cad.txt"))
    assert [li.desc for li in ex.line_items] == ["Social posts", "Analytics report"]


def test_stated_total_mismatch_warns():
    text = "From: A B <a@b.example>\n\n- 2 x Widgets @ $10\nTotal: $50\nNet 30\n"
    ex = R.extract(text)
    assert any("differs from computed total" in i.message for i in ex.issues)


def test_no_terms_defaults_with_warning():
    ex = R.extract("From: A B <a@b.example>\n\n- 2 x Widgets @ $10\n")
    assert ex.due_days == 30
    assert any(i.field == "due_days" for i in ex.issues)


def test_empty_text_low_confidence():
    ex = R.extract("hello")
    assert ex.confidence <= 0.5 and ex.errors


# ---------------- LLM interface (fake model, no network)

def _fake(payload):
    return lambda prompt: payload if isinstance(payload, str) else json.dumps(payload)


def test_llm_extractor_happy_path_with_code_fence():
    text = load("05_missing_email.txt") + "\nmy email: darnell.b@mailbox.example\n"
    out = {"client_name": "Darnell Brooks", "client_email": "darnell.b@mailbox.example", "currency": "USD",
           "line_items": [{"desc": "Logo concepts", "qty": 3, "unit_price": 250}],
           "due_days": 7, "amount_paid": 0, "confidence": 0.9, "issues": []}
    ex = LLMExtractor(_fake("```json\n" + json.dumps(out) + "\n```")).extract(text)
    assert ex.source == "llm" and ex.client_email == "darnell.b@mailbox.example"
    assert ex.line_items[0].currency == "USD" and ex.confidence == 0.9


def test_llm_hallucinated_email_is_rejected():
    out = {"client_name": "Darnell", "client_email": "made.up@nowhere.example", "currency": "USD",
           "line_items": [{"desc": "Logo", "qty": 1, "unit_price": 250}], "due_days": 7, "confidence": 0.99}
    ex = LLMExtractor(_fake(out)).extract(load("05_missing_email.txt"))
    assert ex.client_email is None
    assert any("hallucination" in i.message for i in ex.issues)
    assert ex.confidence < 0.99


def test_llm_garbage_output_is_zero_confidence():
    ex = LLMExtractor(_fake("Sure! Here's the invoice...")).extract("x")
    assert ex.confidence == 0.0 and ex.errors


def test_hybrid_only_calls_llm_when_needed():
    calls = []

    def complete(prompt):
        calls.append(prompt)
        return "{}"

    h = HybridExtractor(R, LLMExtractor(complete), threshold=0.8)
    h.extract(load("01_simple_usd_hourly.txt"))
    assert calls == []
    h.extract(load("05_missing_email.txt"))
    assert len(calls) == 1 and "Darnell" in calls[0]


def test_validate_flags_jpy_decimals():
    ex = R.extract("From: K <k@x.example>\n\n- 1 x Thing @ ¥100.50\nNet 30")
    assert any("does not support decimals" in i.message for i in validate(ex))

def test_unparsed_source_charge_cannot_look_like_complete_invoice():
    from ledgerly.agent import Agent
    from ledgerly.paypal import SandboxMock
    source = ("From: Elise Grant <elise@example.test>\n"
              "2.25 hours of colour grading at GBP 80 per hour.\n"
              "Eight image exports at GBP 12 each.\n"
              "GBP 30 covers archive preparation.\nNet 21.")
    ex = R.extract(source)
    assert ex.total() == 276
    assert any(i.field == "line_items" and "GBP 30" in i.message for i in ex.errors)
    provider = SandboxMock()
    agent = Agent(provider, {"name":"Studio", "email_address":"studio@example.test"})
    assert not agent.tool_create_invoice(source)["ok"]
    assert not provider.requests

def test_quoted_unparsed_charge_is_not_current_scope():
    source = "From: Elise Grant <elise@example.test>\nColour grading at GBP 180.\nNet 21.\n> There is also a GBP 30 archive preparation fee."
    assert not R.extract(source).errors


@pytest.mark.parametrize("line,currency,price,description", [
    ("There is also a GBP 30 archive preparation fee.", "GBP", "30", "archive preparation fee"),
    ("There is a EUR 25 delivery fee.", "EUR", "25", "delivery fee"),
    ("An additional CAD 40 handover fee applies.", "CAD", "40", "handover fee"),
])
def test_declarative_separate_named_fee(line, currency, price, description):
    ex = RulesExtractor().extract("From: Ada <ada@example.test>\n" + line + "\nNet 14.")
    assert len(ex.line_items) == 1
    assert ex.line_items[0].desc.lower() == description
    assert ex.line_items[0].qty == Decimal("1")
    assert ex.line_items[0].unit_price == Decimal(price)
    assert ex.line_items[0].currency == currency
    assert not ex.errors


@pytest.mark.parametrize("line", [
    "There is also a GBP 30 archive preparation fee if needed.",
    "There is no GBP 30 archive preparation fee.",
    "An additional GBP 30 monthly archive fee applies.",
    "An additional GBP 30 tax fee applies.",
])
def test_declarative_fee_normalization_does_not_authorize_other_amounts(line):
    assert RulesExtractor._explicit_standalone_fee_line(line) is None


def test_original_elise_fallback_now_keeps_every_agreed_charge():
    source = ("From: Elise Grant <elise@example.test>\n"
              "2.25 hours of colour grading at GBP 80 per hour.\n"
              "Eight image exports at GBP 12 each.\n"
              "There is also a GBP 30 archive preparation fee.\nNet 21.")
    ex = R.extract(source)
    assert ex.total() == Decimal("306")
    assert len(ex.line_items) == 3 and not ex.errors
    assert ex.line_items[0].qty == Decimal("2.25")
    assert ex.line_items[1].qty == Decimal("8")
    assert ex.due_days == 21


def test_completed_counted_clauses_and_explicit_fixed_charge():
    import json
    from pathlib import Path
    source = json.loads(Path("tests/fixtures/elena-first-native-plan.json").read_text())["source"]
    ex = R.extract(source)
    assert ex.total() == Decimal("440")
    assert [(x.qty, x.unit_price) for x in ex.line_items] == [(Decimal("2"), Decimal("140")), (Decimal("5"), Decimal("25")), (Decimal("1"), Decimal("35"))]
    assert ex.line_items[0].unit == "workshops"
    assert ex.line_items[1].unit == "files"
    assert ex.client_email == "accounts@northlake.example" and ex.due_days == 21
    assert not ex.errors


@pytest.mark.parametrize("line", [
    "The completed work was two planning workshops at EUR 140 per hour and five artwork files at EUR 25 each.",
    "The completed work was two optional planning workshops at EUR 140 per workshop and five artwork files at EUR 25 each.",
    "Please add the fixed EUR 35 courier charge if needed.",
    "Please add the fixed EUR 35 deposit charge.",
    "Please add the fixed EUR 35 tax charge.",
    "Do not add the fixed EUR 35 courier charge.",
])
def test_completed_work_grammar_preserves_qualifications_and_rate_units(line):
    ex = R.extract("From: Ada <ada@example.test>\n" + line + "\nNet 21")
    assert ex.errors


ARDEN_NOTE_SOURCE = """From: Arden Works <arden@example.test>
Consulting — 2 hours at USD 75 per hour
Research memo — USD 90
Please put purchase order AW-1042 on the invoice so accounts payable can process it.
Net 30"""


def test_original_arden_instruction_is_part_of_the_extracted_invoice():
    from ledgerly.extract import Extraction, source_references
    ex = RulesExtractor().extract(ARDEN_NOTE_SOURCE)
    assert not ex.errors
    assert ex.total() == Decimal("240")
    assert ex.note == "Purchase order AW-1042"
    assert Extraction.from_dict(ex.to_dict()).note == ex.note
    assert source_references(ex, ARDEN_NOTE_SOURCE)["note"] == {
        "text": "Please put purchase order AW-1042 on the invoice so accounts payable can process it.",
        "value": "Purchase order AW-1042",
    }


def test_literal_client_note_ignores_quoted_and_forwarded_instructions():
    from ledgerly.extract import DEFAULT_INVOICE_NOTE
    source = ARDEN_NOTE_SOURCE.split("Please put")[0] + "\nNet 30"
    for history in [
        "\n> Please put purchase order OLD-1 on the invoice.",
        "\nOn Monday, Old Client wrote:\nPlease put purchase order OLD-1 on the invoice.",
        "\n-----Original Message-----\nPO: OLD-1",
    ]:
        assert RulesExtractor().extract(source + history).note == DEFAULT_INVOICE_NOTE
    note = 'Project <North> & café'
    for instruction in ['Invoice note: ' + note, 'Please include "' + note + '" on the invoice.']:
        assert RulesExtractor().extract(source + "\n" + instruction).note == note


def test_model_note_cannot_replace_the_current_source_instruction():
    proposal = RulesExtractor().extract(ARDEN_NOTE_SOURCE).to_dict()
    proposal["note"] = "Purchase order INVENTED-9"
    ex = LLMExtractor(lambda _: json.dumps(proposal)).extract(ARDEN_NOTE_SOURCE)
    assert ex.note == "Purchase order AW-1042"
    assert ex.total() == Decimal("240")


def test_conditional_invoice_instruction_requires_a_human_without_another_model():
    from ledgerly.extract import HybridExtractor
    source = ARDEN_NOTE_SOURCE.replace("so accounts payable can process it.", "unless a new purchase order arrives.")
    calls = []
    extractor = HybridExtractor(RulesExtractor(), LLMExtractor(lambda prompt: calls.append(prompt)), threshold=1)
    ex = extractor.extract(source)
    assert any(issue.field == "note" and issue.severity == "error" for issue in ex.issues)
    assert not calls


def test_split_currency_invoices_keep_the_checked_note():
    from ledgerly.extract import split_by_currency
    ex = RulesExtractor().extract("From: Arden Works <arden@example.test>\nConsulting — USD 90\nArtwork — EUR 50\nPO: AW-1042\nNet 30")
    assert [part.note for part in split_by_currency(ex)] == ["Purchase order AW-1042", "Purchase order AW-1042"]

@pytest.mark.parametrize("line,quantity,unit,total", [
    ("Please invoice three cover studies at EUR 95 per study.", 3, "studies", 285),
    ("Invoice four audio mixes at EUR 95 per mix.", 4, "mixes", 380),
    ("Bill two archive boxes at EUR 95 per box.", 2, "boxes", 190),
    ("Please invoice six reference copies at EUR 95 per copy.", 6, "copies", 570),
    ("Three cover studies at EUR 95 per study.", 3, "studies", 285),
])
def test_counted_request_retains_rate_noun_and_quantity(line, quantity, unit, total):
    source = "Bill to: Reed Editions <accounts@reededitions.example>\n" + line + "\nNet 30."
    ex = RulesExtractor().extract(source)
    assert not ex.errors
    assert len(ex.line_items) == 1
    assert ex.line_items[0].qty == Decimal(quantity)
    assert ex.line_items[0].unit == unit
    assert ex.total() == Decimal(total)
    from ledgerly.extract import source_references
    reference = source_references(ex, source)["line_items"][0]["qty"]
    assert reference and reference["value"] == str(quantity)
    assert reference["text"] in source


@pytest.mark.parametrize("line", [
    "Cover studies at EUR 95 per study.",
    "Please invoice three cover studies at EUR 95 per hour.",
    "Invoice Three Rivers at EUR 95 per day.",
])
def test_counted_request_unresolved_rate_cannot_become_flat_fee(line):
    ex = RulesExtractor().extract("Bill to: Reed Editions <accounts@reededitions.example>\n" + line + "\nNet 30.")
    assert ex.errors
    assert not ex.line_items or ex.line_items[0].qty is None


def test_counted_request_ordinary_flat_fee_stays_one():
    ex = RulesExtractor().extract("Bill to: Reed Editions <accounts@reededitions.example>\nCover study at EUR 95.\nNet 30.")
    assert not ex.errors and ex.line_items[0].qty == 1 and ex.total() == 95


def test_counted_request_conditional_direct_recipient_needs_human_choice():
    source = ("Bill to: Reed Editions <accounts@reededitions.example>\n"
              "Please invoice three cover studies at EUR 95 per study.\n"
              "If the grant is approved, bill grants@reededitions.example instead; the grant is still pending.\nNet 30.")
    ex = RulesExtractor().extract(source)
    assert any(issue.field == "client_email" and issue.severity == "error" for issue in ex.issues)
    assert HybridExtractor.human_decision_required(ex)
    calls = []
    HybridExtractor(RulesExtractor(), LLMExtractor(lambda prompt: calls.append(prompt) or "{}"), threshold=.90).extract(source)
    assert calls == []
    assert ex.client_email != "grants@reededitions.example"


def test_counted_request_model_must_preserve_the_explicit_rate_noun():
    source = "Bill to: Reed Editions <accounts@reededitions.example>\nThree cover studies at EUR 95 per study.\nNet 30."
    payload = {"client_name":"Reed Editions","client_email":"accounts@reededitions.example","currency":"EUR",
               "line_items":[{"desc":"Cover studies","qty":3,"unit_price":"95","currency":"EUR","unit":"hours"}],
               "due_days":30,"due_date":None,"amount_paid":0,"confidence":1,"issues":[]}
    ex = LLMExtractor(_fake(payload), ground_prices=True, check_missing_work=True).extract(source)
    assert any(issue.field == "line_items[0].unit" and issue.severity == "error" for issue in ex.issues)
