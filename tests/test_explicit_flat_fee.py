from decimal import Decimal
import pytest
from ledgerly.extract import RulesExtractor

def extract(body):
    return RulesExtractor().extract("From: Mina Okada <mina@example.test>\n\n" + body)

def test_original_separate_proofreading_fee_and_terms():
    ex = extract("Please invoice 3.5 hours at USD 80 per hour for copy editing, plus a separate USD 45 proofreading fee. Payment is due in 14 days.")
    assert [(i.desc, i.qty, i.unit_price) for i in ex.line_items] == [
        ("Copy editing", Decimal("3.5"), Decimal("80")),
        ("Proofreading fee", Decimal("1"), Decimal("45")),
    ]
    assert ex.total() == Decimal("325") and ex.due_days == 14
    assert ex.issues == []

@pytest.mark.parametrize("body,total,currency,due", [
    ("Bill us 2 hours at GBP 70 per hour for editing plus a separate GBP 20 delivery fee.\nNet 14", "160", "GBP", 14),
    ("Please invoice 2 hours at EUR 90 per hour for editing plus a separate 25 EUR proofreading fee. Net 15.", "205", "EUR", 15),
    ("Please invoice two hours at USD 50 per hour for editing plus a separate USD 30 production fee. Due on receipt.", "130", "USD", 0),
])
def test_explicit_fee_currency_quantity_and_terms(body, total, currency, due):
    ex = extract(body)
    assert len(ex.line_items) == 2
    assert ex.total() == Decimal(total) and ex.currency == currency and ex.due_days == due
    assert not ex.errors

@pytest.mark.parametrize("joiner", ["plus a separate ", "plus a ", "plus "])
@pytest.mark.parametrize("suffix", [
    "USD 45 proofreading fee if approved",
    "USD 45 optional proofreading fee",
    "USD 45 waived proofreading fee",
    "USD 45 complimentary proofreading fee",
    "USD 45 free proofreading fee",
    "USD 45 already included proofreading fee",
    "USD 45 non-billable proofreading fee",
    "USD 45 proofreading waiver fee",
    "USD 45 cancelled proofreading fee",
    "USD 45 canceled proofreading fee",
    "USD 45 recurring proofreading fee",
    "USD 45 monthly proofreading fee",
    "USD 45 annual proofreading fee",
    "USD 45 daily proofreading fee",
    "USD 45 proofreading fee per hour",
    "USD 45 per day proofreading fee",
    "USD 45 tax fee",
    "USD 45 deposit fee",
    "USD 45 discount fee",
    "USD 45 refundable proofreading fee",
    "USD 45 proofreading fee and USD 20 delivery",
    "USD 45 proofreading fee and additional editing",
    "USD 45 proofreading fee. Only when needed.",
    "EUR 1 2000 proofreading fee",
])
def test_uncertain_or_nonflat_second_clause_stays_blocked(suffix, joiner):
    ex = extract("Please invoice 2 hours at USD 80 per hour for editing " + joiner + suffix + ".\nNet 14")
    assert ex.errors
    assert not (len(ex.line_items) == 2 and ex.line_items[1].qty == 1)

@pytest.mark.parametrize("work", [
    "Please invoice hours at USD 80 per hour for editing",
    "Please invoice 2 days at USD 80 per hour for editing",
    "Please invoice 2 hours at USD 80 per day for editing",
    "Please invoice 2 hours at USD 80 per hour for editing if approved",
])
def test_first_work_must_have_sound_unconditional_quantity(work):
    ex = extract(work + " plus a separate USD 45 proofreading fee.\nNet 14")
    assert ex.errors

def test_bare_additional_amount_is_not_invented_fee():
    ex = extract("Please invoice 2 hours at USD 80 per hour for editing plus USD 45.\nNet 14")
    assert ex.errors

def test_total_mismatch_and_quoted_history_preserved():
    current = "Please invoice 2 hours at USD 80 per hour for editing plus a separate USD 45 proofreading fee.\nNet 14"
    ex = extract(current + "\nTotal: USD 200")
    assert ex.total() == Decimal("205")
    assert any("differs from computed total" in i.message for i in ex.issues)
    quoted = extract(current + "\n\nOn Tuesday, Old Client wrote:\n> Please invoice 3 hours at USD 99 per hour for old work plus a separate USD 50 old fee.")
    assert quoted.total() == Decimal("205") and len(quoted.line_items) == 2

@pytest.mark.parametrize("qualifier", ["discounted", "cancellation"])
def test_explicit_chargeable_description_is_not_waiver(qualifier):
    ex = extract(f"Invoice 2 hours of editing at GBP 95 per hour, plus a separate GBP 35 {qualifier} transcription fee.\nNet 14")
    assert len(ex.line_items) == 2 and ex.total() == Decimal("225")
    assert not ex.errors


@pytest.mark.parametrize("joiner", ["plus a ", "plus "])
def test_named_flat_fee_does_not_require_the_word_separate(joiner):
    ex = extract("Invoice 4 hours of QA at USD 75/hr, " + joiner + "USD 50 setup fee.\nNet 21")
    assert not ex.errors and ex.total() == Decimal("350")
    assert len(ex.line_items) == 2 and ex.due_days == 21


@pytest.mark.parametrize("joiner", ["and", "plus"])
def test_explicit_fixed_work_preserves_additional_service(joiner):
    ex = extract(f"Invoice 3 hours of audio editing at USD 70/hr {joiner} a fixed USD 30 transcript cleanup. Net 15.")
    assert not ex.errors
    assert [(i.desc, i.qty, i.unit_price) for i in ex.line_items] == [
        ("Audio editing", Decimal(3), Decimal(70)),
        ("Transcript cleanup", Decimal(1), Decimal(30)),
    ]
    assert ex.total() == 240

@pytest.mark.parametrize("suffix", ["if approved", "per hour", "monthly", "and extra editing", "unless canceled"])
def test_fixed_word_cannot_erase_qualification(suffix):
    ex = extract("Invoice 3 hours of audio editing at USD 70/hr and a fixed USD 30 transcript cleanup " + suffix + ". Net 15.")
    assert ex.errors

@pytest.mark.parametrize("phrase", ["CAD 55 for the synthesis memo", "CAD 55 for a synthesis memo", "55 CAD for synthesis memo"])
def test_explicit_amount_for_work_keeps_separate_fee(phrase):
    ex = extract("Four research interviews were completed at CAD 85 each, plus " + phrase + ".\nPayment is due in 14 days.")
    assert [(i.qty, i.unit_price) for i in ex.line_items] == [(Decimal(4), Decimal(85)), (Decimal(1), Decimal(55))]
    assert ex.line_items[1].desc == "Synthesis memo"
    assert ex.total() == 395 and ex.due_days == 14 and not ex.errors

@pytest.mark.parametrize("phrase", [
    "CAD 55 for the synthesis memo if approved",
    "CAD 55 for an optional synthesis memo",
    "CAD 55 for the synthesis memo per hour",
    "CAD 55 for the monthly synthesis memo",
    "CAD 55 for the deposit",
    "CAD 55 for the discount",
    "CAD 55 for the synthesis memo and extra research",
    "CAD 55 for two synthesis memos",
    "CAD 55 for the 2 synthesis memos",
    "CAD 55 for the synthesis memo. Only when needed.",
])
def test_amount_for_work_does_not_invent_flat_fee(phrase):
    ex = extract("Four research interviews were completed at CAD 85 each, plus " + phrase + ".\nNet 14")
    assert ex.errors

def test_amount_for_work_keeps_original_optional_work_for_human_review():
    ex = extract("Four research interviews were completed at CAD 85 each, plus CAD 55 for the synthesis memo.\nPayment is due in 14 days.\nWe discussed an optional workshop at CAD 120, but it has not been approved; leave it off this invoice.")
    assert len(ex.line_items) == 3
    assert ex.line_items[1].unit_price == 55
    assert any(i.field == "line_items[2].unit_price" and i.severity == "error" for i in ex.issues)

@pytest.mark.parametrize("phrase", ["A separate EUR 25 delivery fee also applies.", "A EUR 25 delivery fee applies.", "EUR 25 delivery fee."])
def test_named_standalone_charge_is_visible(phrase):
    ex = extract("Two prints at EUR 18 each.\n" + phrase + "\nNet 10.")
    assert ex.total() == 61 and len(ex.line_items) == 2 and not ex.errors

@pytest.mark.parametrize("phrase", ["Optional: A separate EUR 25 delivery fee also applies.", "A separate EUR 25 delivery fee applies if approved.", "A separate EUR 25 monthly delivery fee applies."])
def test_standalone_fee_normalization_does_not_remove_qualifications(phrase):
    ex = extract("Two prints at EUR 18 each.\n" + phrase + "\nNet 10.")
    assert not (ex.total() == 61 and not ex.errors)
