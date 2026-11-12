"""Omitted source charges require their own explicit disposition before effects."""
import sys
import json
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web-demo" / "python"))
import bridge

SOURCE = """From: Elise Grant <elise@agency.example>
Bill to: Seabright Studio <accounts@seabright.example>
2.25 hours of colour grading at GBP 80 per hour.
Eight image exports at GBP 12 each.
There is also a GBP 30 archive preparation fee.
Net 21, please."""
def setup():
    demo = bridge.Demo()
    analysis = demo.dispatch({"action":"analyze","text":SOURCE})["result"]
    # Retain the actual initial rules omission even after the parser improves.
    # This exercises the review boundary with the original incomplete proposal.
    analysis["line_items"] = analysis["line_items"][:2]
    analysis["issues"] = [{"field":"line_items","severity":"error","message":
        "A source amount was not included in the invoice; review it before drafting: "
        "'There is also a GBP 30 archive preparation fee.'"}]
    demo.analysis = analysis
    analysis["source_amount_requirements"] = bridge.source_amount_requirements(analysis)
    fields = {"client_name":analysis["client_name"],"client_email":analysis["client_email"],
              "due_days":"21","amount_paid":"0","line_items":[
                  {key:str(row.get(key) or "") for key in ("desc","qty","unit_price","currency","unit")}
                  for row in analysis["line_items"]]}
    requirement = analysis["source_amount_requirements"][0]
    return demo, fields, requirement["id"]

def check(demo, fields, resolutions=None):
    return demo.dispatch({"action":"review","text":SOURCE,"fields":fields,
                          "confirmed":True,"source_resolutions":resolutions})["result"]

def test_blanket_acknowledgement_cannot_check_partial_total():
    demo, fields, key = setup()
    result = check(demo, fields)
    assert not result["valid"] and result["review_id"] is None
    assert any(i["field"] == "source_amounts."+key for i in result["issues"])
    assert demo.mock.requests == []

def test_wrong_line_cannot_account_for_omitted_fee():
    demo, fields, key = setup()
    assert not check(demo, fields, {key:{"kind":"included","detail":"1"}})["valid"]
    assert demo.mock.requests == []

def test_added_fee_must_be_individually_confirmed_and_matches_source():
    demo, fields, key = setup()
    fields["line_items"].append({"desc":"archive preparation fee","qty":"1",
                                "unit_price":"30","currency":"GBP","unit":""})
    assert not check(demo, fields)["valid"]
    checked = check(demo, fields, {key:{"kind":"included","detail":"3"}})
    assert checked["valid"] and checked["total"] == "306.00"
    assert demo.mock.requests == []
    fields["line_items"][-1]["unit_price"] = "31"
    assert not check(demo, fields, {key:{"kind":"included","detail":"3"}})["valid"]

def test_deliberate_nonbilling_needs_specific_recorded_reason():
    demo, fields, key = setup()
    assert not check(demo,fields,{key:{"kind":"excluded","detail":""}})["valid"]
    checked = check(demo,fields,{key:{"kind":"excluded","detail":"Archive fee billed separately by agreement"}})
    assert checked["valid"]
    assert any("Archive fee billed separately" in i["message"] for i in checked["issues"])
    assert demo.mock.requests == []


def test_resolution_keeps_document_currency_context_for_dollar_fee():
    from review import prepare_review, check_source_amount_resolutions, source_amount_requirements
    source = "Please invoice in CAD.\nA separate $30 archive preparation fee applies."
    analysis = {"issues":[{"field":"line_items","severity":"error","message":
        "A source amount was not included in the invoice; review it before drafting: "
        "'A separate $30 archive preparation fee applies.'"}]}
    fields = {"client_name":"Ada","client_email":"ada@example.test","due_days":"14",
              "amount_paid":"0","line_items":[{"desc":"archive preparation fee",
              "qty":"1","unit_price":"30","currency":"CAD","unit":""}]}
    ex = prepare_review(fields)
    key = source_amount_requirements(analysis)[0]["id"]
    check_source_amount_resolutions(ex,analysis,{key:{"kind":"included","detail":"1"}},source)
    assert not ex.errors
    fields["line_items"][0]["currency"] = "USD"
    wrong = prepare_review(fields)
    check_source_amount_resolutions(wrong,analysis,{key:{"kind":"included","detail":"1"}},source)
    assert wrong.errors


def test_every_price_in_an_unresolved_sentence_needs_a_disposition():
    source = ("From: Bennett Cole <bennett@larkspur.example>\n"
              "Bill to: Larkspur Studio <accounts@larkspur.example>\n"
              "Please bill the three half-day editing sessions at GBP 225 each, "
              "plus twelve image touch-ups at GBP 8 each.\n"
              "There's also a fixed GBP 18 transfer fee for the final files.\n"
              "Payment is due 14 days after the invoice date.")
    demo = bridge.Demo()
    analysis = demo.dispatch({"action":"analyze","text":source})["result"]
    # Keep the original omitted proposal as the review-boundary fixture even
    # when ordinary Rules intake learns this exact request framing.
    retained = json.loads((Path(__file__).parent / "fixtures/bennett-original-omission.json").read_text())
    assert retained["source"] == source
    analysis.update(retained["analysis"])
    analysis["source_amount_requirements"] = bridge.source_amount_requirements(analysis)
    demo.analysis = analysis
    requirements = analysis["source_amount_requirements"]
    assert len(requirements) == 3
    fields = {"client_name":"Larkspur Studio","client_email":"accounts@larkspur.example",
              "due_days":"14","amount_paid":"0","line_items":[
              {"desc":"half-day editing sessions","qty":"1","unit_price":"225","currency":"GBP","unit":""},
              {"desc":"transfer fee","qty":"1","unit_price":"18","currency":"GBP","unit":""}]}
    req = {"action":"review","text":source,"fields":fields,"confirmed":True,
           "source_resolutions":{requirements[2]["id"]:{"kind":"included","detail":"2"}}}
    failed = demo.dispatch(req)["result"]
    assert not failed["valid"] and demo.mock.requests == []
    fields["line_items"][0]["qty"] = "3"
    fields["line_items"].insert(1,{"desc":"image touch-ups","qty":"12",
                                  "unit_price":"8","currency":"GBP","unit":""})
    req["source_resolutions"] = {item["id"]:{"kind":"included","detail":str(i+1)}
                                for i,item in enumerate(requirements)}
    checked = demo.dispatch(req)["result"]
    assert checked["valid"] and checked["total"] == "789"
    assert demo.mock.requests == []

@pytest.mark.parametrize("prefix", [
    "A source amount was not included in the invoice; review it before drafting: ",
    "Multiple monetary amounts on one priced line; review and separate every fee before drafting: ",
])
def test_each_unresolved_price_is_reviewable_regardless_of_parser_issue(prefix):
    from review import source_amount_requirements
    quote = "Three editing sessions at GBP 225 each, plus twelve touch-ups at GBP 8 each."
    analysis = {"issues": [{"field": "line_items", "severity": "error",
                            "message": prefix + repr(quote)}]}
    requirements = source_amount_requirements(analysis)
    assert len(requirements) == 2
    assert len({item["id"] for item in requirements}) == 2
    assert all(item["source_issue"] == prefix + repr(quote) for item in requirements)
    assert all(item["source_issue_count"] == 2 for item in requirements)
    assert "GBP 225" in requirements[0]["quote"]
    assert "GBP 8" in requirements[1]["quote"]
    assert requirements == source_amount_requirements(analysis)


def test_single_omitted_charge_preserves_its_existing_review_identity():
    from hashlib import sha256
    from review import source_amount_requirements
    message = ("A source amount was not included in the invoice; review it before drafting: "
               + repr("A GBP 18 transfer fee."))
    assert source_amount_requirements({"issues": [
        {"field": "line_items", "message": message}]}) == [
            {"id": sha256(message.encode()).hexdigest()[:20],
             "quote": "A GBP 18 transfer fee."}]
