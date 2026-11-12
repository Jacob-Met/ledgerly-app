"""Item-level proof that same-topic services cannot exchange AI invoice rates."""
import json

from ledgerly.agent import Agent
from ledgerly.extract import LLMExtractor
from ledgerly.paypal import SandboxMock


SOURCE = (
    "From: Ada Example <ada@example.com>\n"
    "Please invoice these services:\n"
    "- 1 x Audio mixing @ $50\n"
    "- 1 x Audio mastering @ $500\n"
    "Net 15\n"
)


def model(items):
    payload = {
        "client_name": "Ada Example",
        "client_email": "ada@example.com",
        "currency": "USD",
        "line_items": [
            {"desc": description, "qty": 1, "unit_price": rate, "currency": "USD"}
            for description, rate in items
        ],
        "due_days": 15,
        "amount_paid": 0,
        "confidence": 0.99,
    }
    return LLMExtractor(lambda _: json.dumps(payload), ground_prices=True)


def test_correct_prices_and_reversed_item_order_still_ground():
    for items in [
        [("Audio mixing", 50), ("Audio mastering", 500)],
        [("Audio mastering", 500), ("Audio mixing", 50)],
    ]:
        result = model(items).extract(SOURCE)
        assert not result.errors, result.issues
        assert sum(
            i.severity == "info" and i.field.endswith(".unit_price")
            for i in result.issues
        ) == 2


def test_same_topic_price_swap_cannot_create_paypal_draft():
    mock = SandboxMock()
    agent = Agent(
        mock,
        {"name": "Freelancer", "email_address": "seller@example.com"},
        extractor=model([("Audio mixing", 500), ("Audio mastering", 50)]),
    )
    result = agent.tool_create_invoice(SOURCE)
    assert result["ok"] is False
    assert sum(
        issue["severity"] == "error" and issue["field"].endswith(".unit_price")
        for issue in result["issues"]
    ) == 2
    assert not mock.requests and not agent.ledger and not agent.list_pending()


def test_generic_audio_description_cannot_select_one_of_two_rates():
    for rate in (50, 500):
        result = model([("Audio", rate)]).extract(SOURCE)
        assert any(
            issue.severity == "error" and issue.field == "line_items[0].unit_price"
            for issue in result.issues
        )


def test_unique_description_cannot_borrow_a_different_rate():
    result = model([("Audio mastering", 50)]).extract(SOURCE)
    assert any(
        issue.severity == "error" and issue.field == "line_items[0].unit_price"
        for issue in result.issues
    )

def test_actual_separate_fee_and_grammatical_service_forms_remain_grounded():
    source = ("From: Leila Noor <leila@example.test>\n"
              "I translated seven pages at EUR 24 per page, plus a separate EUR 38 terminology fee.\n"
              "Net 10")
    data = {"client_name":"Leila Noor","client_email":"leila@example.test","currency":"EUR",
            "due_days":10,"confidence":1,"line_items":[
                {"desc":"Translation of seven pages","qty":7,"unit_price":"24","currency":"EUR","unit":"pages"},
                {"desc":"Terminology fee","qty":1,"unit_price":"38","currency":"EUR"}]}
    ex=LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(source)
    assert not ex.errors,ex.issues
    assert ex.total()==206
    data["line_items"][0]["unit_price"]="38"
    assert LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(source).errors


def test_grammatical_binding_keeps_distinct_and_ambiguous_services_blocked():
    source=("From: Ada Example <ada@example.com>\n"
            "- I translated one page at USD 50 per page.\n"
            "- I transcribed one page at USD 500 per page.\nNet 15")
    correct=model([("Translation of one page",50),("Transcribed one page",500)]).extract(source)
    assert not correct.errors,correct.issues
    assert model([("Translation of one page",500)]).extract(source).errors
    repeated=source.replace("transcribed","translated")
    assert model([("Translation of one page",50)]).extract(repeated).errors


def test_explicit_second_fixed_work_has_independent_grounded_price():
    source = ("From: Jo Vale <jo@example.test>\n"
              "Please invoice three audio-editing hours at USD 70 per hour and a fixed USD 30 transcript cleanup.\nNet 15")
    data = {"client_name":"Jo Vale", "client_email":"jo@example.test", "currency":"USD", "due_days":15, "confidence":1,
            "line_items":[{"desc":"Audio editing", "qty":3, "unit_price":"70", "currency":"USD", "unit":"hours"},
                          {"desc":"Transcript cleanup", "qty":1, "unit_price":"30", "currency":"USD"}]}
    for phrase in ("a fixed", "a flat", "a one-time", "a fixed fee of"):
        result=LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(source.replace("a fixed",phrase))
        assert not result.errors,result.issues
        assert result.total()==240
    data["line_items"][0]["unit_price"]="30"
    data["line_items"][1]["unit_price"]="70"
    assert LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(source).errors


def test_fixed_work_separator_does_not_lose_qualifications_or_payment_adjustments():
    source = ("From: Jo Vale <jo@example.test>\n"
              "Please invoice three audio-editing hours at USD 70 per hour and a fixed USD 30 transcript cleanup.\nNet 15")
    data = {"client_name":"Jo Vale", "client_email":"jo@example.test", "currency":"USD", "due_days":15, "confidence":1,
            "line_items":[{"desc":"Audio editing", "qty":3, "unit_price":"70", "currency":"USD", "unit":"hours"},
                          {"desc":"Transcript cleanup", "qty":1, "unit_price":"30", "currency":"USD"}]}
    for qualified in (source.replace("Please invoice", "Optional work:"),
                      source.replace("transcript cleanup.", "transcript cleanup if approved."),
                      source.replace("transcript cleanup.", "transcript cleanup, no charge."),
                      source.replace("USD 30 transcript cleanup", "USD 30 per month transcript cleanup"),
                      source.replace("USD 30 transcript cleanup", "USD 30 deposit for transcript cleanup")):
        result=LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(qualified)
        assert result.errors,qualified
    data["line_items"][0]["qty"]=4
    assert LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(source).errors


def test_fixed_charge_description_retains_its_actual_source_prefix():
    source = ("From: Jo Vale <jo@example.test>\n"
              "Invoice 3 hours of audio editing at USD 70/hr and a fixed USD 30 transcript cleanup. Net 15.")
    data = {"client_name":"Jo Vale", "client_email":"jo@example.test", "currency":"USD",
            "due_days":15, "confidence":1, "line_items":[
                {"desc":"3 hours of audio editing", "qty":3, "unit_price":"70", "currency":"USD", "unit":"hours"},
                {"desc":"Fixed USD 30 transcript cleanup", "qty":1, "unit_price":"30", "currency":"USD"}]}
    ex = LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(source)
    assert not ex.errors, ex.issues
    assert ex.total() == 240
    # A description containing the right rate must not authorize a different field rate.
    data["line_items"][1]["unit_price"] = "70"
    assert LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(source).errors
    data["line_items"][1]["unit_price"] = "30"
    for altered in (source.replace("a fixed", "an optional fixed"),
                    source.replace("transcript cleanup.", "transcript cleanup if approved."),
                    source.replace("transcript cleanup.", "transcript cleanup, no charge.")):
        assert LLMExtractor(lambda _:json.dumps(data),ground_prices=True).extract(altered).errors


def test_repeated_fixed_service_wording_cannot_select_a_rate_by_description_money():
    source = ("From: Ada Example <ada@example.com>\n"
              "One audio edit at USD 70 and a fixed USD 30 cleanup.\n"
              "One audio edit at USD 90 and a fixed USD 50 cleanup.\nNet 15")
    ex = model([("Fixed USD 30 cleanup",30)]).extract(source)
    assert ex.errors


def test_description_price_cannot_disambiguate_generic_work():
    for rate in (50, 500):
        assert model([(f"Audio USD {rate}",rate)]).extract(SOURCE).errors

def photography_original():
    from pathlib import Path
    evidence = Path(__file__).resolve().parents[1] / "tests/fixtures/farah-first-original.json"
    original = json.loads(evidence.read_text())[0]
    return original["source"], json.loads(original["wire_response"]["response"])


def test_actual_photography_completion_keeps_missing_fee_visible():
    source, data = photography_original()
    ex = LLMExtractor(lambda _:json.dumps(data),ground_prices=True,check_missing_work=True).extract(source)
    assert ex.client_name == "Farah Quinn" and ex.due_days == 10
    assert ex.total() == 243
    assert len(ex.errors) == 1, ex.issues
    assert "not accounted for" in ex.errors[0].message and "EUR 25" in ex.errors[0].message
    assert not [i for i in ex.errors if i.field.startswith("line_items[")]


def test_photography_complete_items_ground_without_losing_optional_fee_context():
    source, data = photography_original()
    data["line_items"].append({"desc":"Delivery fee","qty":1,"unit_price":"25","currency":"EUR"})
    ex = LLMExtractor(lambda _:json.dumps(data),ground_prices=True,check_missing_work=True).extract(source)
    assert not ex.errors, ex.issues
    assert ex.total() == 268
    for altered in (source.replace("1.5 hours", "2.5 hours"),
                    source.replace("six retouched", "seven retouched"),
                    source.replace("EUR 90 per hour", "EUR 18 per hour"),
                    source.replace("A separate EUR", "Optional: A separate EUR")):
        assert LLMExtractor(lambda _:json.dumps(data),ground_prices=True,check_missing_work=True).extract(altered).errors


def test_quantity_prefix_does_not_erase_a_numbered_work_name():
    source = "From: Ada Example <ada@example.com>\nOne Phase 3 design at USD 50.\nNet 15."
    ex = model([("2 Phase design", 50)]).extract(source)
    assert ex.errors


def test_unparsed_fee_wording_cannot_disappear_from_model_invoice():
    source, data = photography_original()
    source = source.replace("A separate EUR 25 delivery fee also applies.", "There is also a EUR 25 archive preparation fee.")
    ex = LLMExtractor(lambda _:json.dumps(data),ground_prices=True,check_missing_work=True).extract(source)
    assert any("not accounted for" in i.message and "EUR 25" in i.message for i in ex.errors)
    data["line_items"].append({"desc":"Archive preparation fee","qty":1,"unit_price":"25","currency":"EUR"})
    assert not LLMExtractor(lambda _:json.dumps(data),ground_prices=True,check_missing_work=True).extract(source).errors


def test_unmatched_excluded_work_and_summary_are_not_forced_into_model_items():
    source, data = photography_original()
    source = source.replace("A separate EUR 25 delivery fee also applies.", "Optional archive preparation at EUR 25, excluded.\nTotal: EUR 243.")
    ex = LLMExtractor(lambda _:json.dumps(data),ground_prices=True,check_missing_work=True).extract(source)
    assert not ex.errors, ex.issues
