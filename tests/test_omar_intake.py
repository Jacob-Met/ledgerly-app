import json
from decimal import Decimal
import pytest
from ledgerly.extract import RulesExtractor, LLMExtractor
SOURCE="""From: Omar Reed <omar@example.test>
Hi, please invoice the finished podcast work in USD with payment due in 21 days.
Podcast editing was three hours at $82 per hour; episode captions were a flat $95.
Optional work:
An audiogram would be $60 if we decide to add it. Leave that out for now.
Thanks, Omar"""

def test_actual_omar_source_preserves_both_agreed_fees_and_hours():
    ex=RulesExtractor().extract(SOURCE)
    assert not ex.errors, ex.issues
    assert (ex.client_name,ex.client_email,ex.currency,ex.due_days)==("Omar Reed","omar@example.test","USD",21)
    assert [(i.desc,i.qty,i.unit_price,i.unit) for i in ex.line_items]==[
        ("Podcast editing",Decimal(3),Decimal(82),"hours"),
        ("Episode captions",Decimal(1),Decimal(95),None)]
    assert ex.total()==Decimal(341)
    assert ex.confidence>=.75

@pytest.mark.parametrize("work",[
    "Editing was two hours at GBP 70 per hour; captions were a flat GBP 25.",
    "The editing is 2 hrs at GBP 70/hr; the captions are a flat fee of GBP 25.",
])
def test_explicit_statement_grammar_is_not_specific_to_the_failed_request(work):
    ex=RulesExtractor().extract("From: Nia <nia@example.test>\n"+work+"\nNet 14")
    assert not ex.errors, ex.issues
    assert ex.total()==Decimal(165)
    assert len(ex.line_items)==2

@pytest.mark.parametrize("work",[
    "Editing was about three hours at $82 per hour; captions were a flat $95.",
    "Editing was three days at $82 per hour; captions were a flat $95.",
    "If approved, editing was three hours at $82 per hour; captions were a flat $95.",
    "Editing was three hours at $82 per hour; captions were $95 per hour.",
])
def test_ambiguous_or_conditioned_compound_work_stays_in_review(work):
    ex=RulesExtractor().extract("From: Omar <omar@example.test>\n"+work+"\nNet 21")
    assert ex.errors

def test_for_in_a_later_sentence_does_not_create_a_named_recipient_request():
    ex=RulesExtractor().extract("From: Omar Reed <omar@example.test>\nPlease invoice the finished work in USD.\nEditing: 3 hours at $82 per hour.\nLeave optional extras for now.\nNet 21")
    assert not any(i.field=="client_email" and i.severity=="error" for i in ex.issues)

@pytest.mark.parametrize("confidence",[1.260,-0.1])
def test_model_confidence_outside_its_contract_is_rejected(confidence):
    result=LLMExtractor(lambda _:json.dumps({"line_items":[],"confidence":confidence})).extract(SOURCE)
    assert result.confidence==0
    assert any("from 0 to 1" in i.message for i in result.errors)

def test_unknown_current_fee_still_blocks_after_excluded_work_is_ignored():
    source = SOURCE.replace(
        "An audiogram would be $60 if we decide to add it. Leave that out for now.",
        "An audiogram would be $60 if we decide to add it. Leave that out for now.\nAgreed work:\n$25 covers archive preparation.")
    ex = RulesExtractor().extract(source)
    assert any("$25" in issue.message and "not included" in issue.message for issue in ex.errors)
    assert not any("$60" in issue.message for issue in ex.errors)

@pytest.mark.parametrize("instruction", ["Leave that out for now.", "Leave it out."])
def test_explicit_optional_exclusion_is_not_an_omitted_charge(instruction):
    source = SOURCE.replace("Leave that out for now.", instruction)
    ex = RulesExtractor().extract(source)
    assert not ex.errors, ex.issues
    assert ex.total() == Decimal(341)

def test_conditional_unparsed_charge_without_explicit_exclusion_still_blocks():
    source = SOURCE.replace(" Leave that out for now.", "")
    ex = RulesExtractor().extract(source)
    assert any("$60" in issue.message and "not included" in issue.message for issue in ex.errors)
