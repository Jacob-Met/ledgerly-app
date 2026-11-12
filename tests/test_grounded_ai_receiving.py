"""Held-out invoice requests that bypass a global price/quantity list."""
import json


from ledgerly.agent import Agent
from ledgerly.extract import LLMExtractor, RulesExtractor
from ledgerly.paypal import SandboxMock


SOURCE = ("From: Alex Example <alex@example.com>\n"
          "- 2 x artwork @ $50 each\nTotal: $100\nNet 15\n")
GOOD = {
    "client_name": "Alex Example", "client_email": "alex@example.com",
    "currency": "USD", "line_items": [{"desc": "artwork", "qty": 2,
    "unit_price": 50, "currency": "USD"}], "due_days": 15,
    "amount_paid": 0, "confidence": 1, "issues": []
}


def extract(model, source=SOURCE):
    return LLMExtractor(lambda _: json.dumps(model), ground_prices=True).extract(source)


def test_existing_source_grounding_still_accepts_a_correct_item():
    result = extract(GOOD)
    assert result.errors == []
    assert any(i.field == "line_items[0].unit_price" and
               i.severity == "info" and "source line" in i.message for i in result.issues)


def test_invented_unit_price_cannot_borrow_same_line_total():
    source = "From: Alex Example <alex@example.com>\n- 2 x artwork @ $50 each, total $100\n"
    wrong = dict(GOOD, line_items=[dict(GOOD["line_items"][0], unit_price=100)])
    assert any(x.field.endswith("unit_price") and x.severity == "error"
               for x in extract(wrong, source).issues)
    assert extract(GOOD, source).errors == []


def test_budget_and_deposit_on_same_line_cannot_become_the_item_rate():
    source = ("From: Alex Example <alex@example.com>\n"
              "Budget $100; 2 x artwork @ $50 each, deposit $25 due later\n")
    for value in (100, 25):
        wrong = dict(GOOD, line_items=[dict(GOOD["line_items"][0], unit_price=value)])
        assert any(x.field.endswith("unit_price") for x in extract(wrong, source).errors)
    assert extract(GOOD, source).errors == []


def test_currency_swapping_is_a_financial_error():
    changed = dict(GOOD, currency="EUR",
                   line_items=[dict(GOOD["line_items"][0], currency="EUR")])
    assert any(x.field.endswith("unit_price") for x in extract(changed).errors)


def test_vague_quantity_never_authorizes_exact_model_count():
    source = "From: Alex Example <alex@example.com>\n- several artwork revisions @ $50 each\n"
    claimed = dict(GOOD, line_items=[{
        "desc": "artwork revisions", "qty": 2, "unit_price": 50, "currency": "USD"
    }])
    assert any(x.field.endswith("qty") for x in extract(claimed, source).errors)


def test_unstated_quantity_only_allows_a_single_unit():
    source = "From: Alex Example <alex@example.com>\n- artwork — $50\n"
    one = dict(GOOD, line_items=[dict(GOOD["line_items"][0], qty=1)])
    many = dict(GOOD, line_items=[dict(GOOD["line_items"][0], qty=3)])
    assert extract(one, source).errors == []
    assert any(x.field.endswith("qty") for x in extract(many, source).errors)


def test_adjacent_work_items_cannot_exchange_prices_or_counts():
    source = ("From: Alex Example <alex@example.com>\n"
              "- 2 x logos @ $50; 10 x animation @ $100\n")
    swapped = dict(GOOD, line_items=[{
        "desc": "logos", "qty": 10, "unit_price": 100, "currency": "USD"
    }])
    assert any(x.field.endswith("unit_price") for x in extract(swapped, source).errors)
    valid = dict(GOOD, line_items=[
        {"desc": "logos", "qty": 2, "unit_price": 50, "currency": "USD"},
        {"desc": "animation", "qty": 10, "unit_price": 100, "currency": "USD"},
    ])
    assert extract(valid, source).errors == []


def test_quote_only_recipient_is_not_current_approval():
    source = "From: Alex Example\n> Previous customer alex@example.com\n- 1 x artwork @ $50\n"
    item = dict(GOOD, line_items=[dict(GOOD["line_items"][0], qty=1)])
    assert any(x.field == "client_email" for x in extract(item, source).errors)


def test_deposit_due_cannot_be_relabelled_already_paid():
    source = "From: Alex Example <alex@example.com>\n- 1 x artwork @ $250\nDeposit due: $250\n"
    claimed = dict(GOOD, line_items=[
        {"desc":"artwork", "qty":1,"unit_price":250,"currency":"USD"}
    ], amount_paid=250)
    assert any(x.field == "amount_paid" for x in extract(claimed, source).errors)


def test_explicit_confirmed_deposit_is_accepted():
    source = ("From: Alex Example <alex@example.com>\n"
              "- 1 x artwork @ $250 each\nAlready paid a $100 deposit.\n")
    confirmed = dict(GOOD, line_items=[
        {"desc":"artwork", "qty":1,"unit_price":250,"currency":"USD"}
    ], amount_paid=100)
    assert extract(confirmed, source).errors == []


def test_hallucinated_item_never_requests_provider_invoice_number():
    mock = SandboxMock()
    wrong = dict(GOOD, line_items=[dict(GOOD["line_items"][0], unit_price=100)])
    agent = Agent(mock, {"name":"Seller","email_address":"seller@example.com"},
                  extractor=LLMExtractor(lambda _:json.dumps(wrong),ground_prices=True))
    result = agent.tool_create_invoice(SOURCE)
    assert result["needs_review"] and not result["ok"]
    assert mock.requests == [] and agent.ledger == {} and agent.list_pending() == []


def test_separate_priced_sentences_bind_each_service_without_swapping():
    source = ("From: Alex Example <alex@example.com>\n"
              "Please invoice 2 x artwork at USD 50. Please add 3 x animation at USD 100. Net 15.")
    valid = dict(GOOD, line_items=[
        {"desc": "artwork", "qty": 2, "unit_price": 50, "currency": "USD"},
        {"desc": "animation", "qty": 3, "unit_price": 100, "currency": "USD"},
    ])
    assert extract(valid, source).errors == []
    swapped = dict(valid, line_items=[dict(valid["line_items"][0], unit_price=100),
                                      dict(valid["line_items"][1], unit_price=50)])
    assert len([i for i in extract(swapped, source).errors if i.field.endswith("unit_price")]) == 2


def test_cross_sentence_pronoun_still_requires_human_rate_review():
    source = ("From: Alex Example <alex@example.com>\n"
              "We finished the artwork. It took four hours at USD 75. "
              "Please add USD 35 for animation. Net 15.")
    proposed = dict(GOOD, line_items=[
        {"desc": "artwork", "qty": 4, "unit_price": 75, "currency": "USD"},
        {"desc": "animation", "qty": 1, "unit_price": 35, "currency": "USD"},
    ])
    errors = extract(proposed, source).errors
    assert any(i.field == "line_items[0].unit_price" for i in errors)
    assert not any(i.field == "line_items[1].unit_price" for i in errors)



def test_qualified_work_cannot_be_billed_from_quoted_rate():
    item = dict(GOOD, line_items=[dict(GOOD["line_items"][0], qty=1)])
    for qualifier in ("Optional: ", "If needed: ", "Do not bill ", "Cancelled: "):
        source = "From: Alex Example <alex@example.com>\n" + qualifier + "1 x artwork @ $50\nNet 15\n"
        assert any("optional, conditional or excluded" in issue.message
                   for issue in extract(item, source).errors), qualifier
    source = "From: Alex Example <alex@example.com>\n1 x artwork @ $50\nNet 15\n"
    assert extract(item, source).errors == []



def test_optional_section_heading_qualifies_work_until_next_heading():
    model = dict(GOOD, line_items=[
        {"desc":"artwork", "qty":1, "unit_price":50, "currency":"USD"},
        {"desc":"animation", "qty":1, "unit_price":100, "currency":"USD"},
        {"desc":"proofreading", "qty":2, "unit_price":25, "currency":"USD"}])
    for heading in ("Optional extras:", "## Optional extras", "Excluded work:", "Conditional work:"):
        source = ("From: Alex Example <alex@example.com>\n"
                  "Agreed work:\n- 1 x artwork @ $50\n" + heading +
                  "\n- 1 x animation @ $100\nConfirmed work:\n"
                  "- 2 x proofreading @ $25\nNet 15\n")
        errors = extract(model, source).errors
        assert len(errors) == 1, [(issue.field, issue.message) for issue in errors]
        assert errors[0].field == "line_items[1].unit_price"
        assert "optional, conditional or excluded" in errors[0].message


def test_rules_qualification_keeps_table_rows_and_confirmed_sections_distinct():
    from ledgerly.extract import RulesExtractor
    source = ("From: Alex Example <alex@example.com>\nOptional extras:\n"
              "| Item | Qty | Unit price |\n| --- | --- | --- |\n"
              "| Animation | 1 | USD 100 |\n"
              "Confirmed work:\n- 1 x artwork @ USD 50\nNet 15\n")
    result = RulesExtractor().extract(source)
    assert len(result.line_items) == 2
    flagged = [i for i in result.errors if "optional" in i.message]
    assert [i.field for i in flagged] == ["line_items[0].unit_price"]


def test_rules_quote_does_not_qualify_current_required_work():
    from ledgerly.extract import RulesExtractor
    source = ("From: Alex Example <alex@example.com>\n"
              "> Optional extras:\n> - 1 x animation @ USD 100\n"
              "Confirmed work:\n- 1 x artwork @ USD 50\nNet 15\n")
    result = RulesExtractor().extract(source)
    assert not result.errors and result.total() == 50


def test_low_confidence_unparsed_work_still_reaches_configured_fallback():
    from ledgerly.extract import HybridExtractor, RulesExtractor
    calls = []
    class ExistingFallback:
        def extract(self, text):
            calls.append(text)
            return RulesExtractor().extract("From: Alex Example <alex@example.com>\n- 1 x artwork @ $50\nNet 15")
    source = "From: Alex Example <alex@example.com>\nPlease invoice the work we discussed."
    result = HybridExtractor(RulesExtractor(), ExistingFallback(), threshold=0.85).extract(source)
    assert calls == [source] and result.total() == 50


def test_default_rules_path_cannot_bill_optional_section_work():
    source = ("From: Alex Example <alex@example.com>\n"
              "Agreed work:\n- 1 x artwork @ $50\nOptional extras:\n"
              "- 1 x animation @ $100\nConfirmed work:\n- 2 x proofreading @ $25\nNet 15\n")
    ex = RulesExtractor().extract(source)
    assert len(ex.errors) == 1
    assert ex.errors[0].field == "line_items[1].unit_price"
    mock = SandboxMock()
    agent = Agent(mock, {"name":"Seller","email_address":"seller@example.com"})
    result = agent.tool_create_invoice(source)
    assert result["needs_review"] and not result["ok"]
    assert mock.requests == [] and agent.list_pending() == []



def test_priced_table_preserves_optional_heading_and_row_qualification():
    for heading, label in (("Optional extras:", "Animation"), ("Confirmed work:", "Optional animation")):
        source = ("From: Alex Example <alex@example.com>\n" + heading +
                  "\n| Item | Qty | Price |\n| " + label + " | 1 | $100 |\nNet 15\n")
        ex = RulesExtractor().extract(source)
        assert any(issue.field == "line_items[0].unit_price" for issue in ex.errors)
    confirmed = RulesExtractor().extract(
        "From: Alex Example <alex@example.com>\nConfirmed work:\n"
        "| Item | Qty | Price |\n| Animation | 1 | $100 |\nNet 15\n")
    assert confirmed.errors == []


def test_actual_model_quantity_annotation_matches_written_source_count():
    source = ("From: Mira Chen <mira@example.com>\n"
              "The landing page was five hours at $67 per hour; "
              "accessibility review was a flat $180.\n"
              "Optional work:\nThe animation pass would be $120 if we decide to add it.")
    model = dict(GOOD, client_name="Mira Chen", client_email="mira@example.com",
                 line_items=[
                     {"desc": "Landing page (5 hours)", "qty": 5, "unit_price": 67, "currency": "USD"},
                     {"desc": "Accessibility review", "qty": 1, "unit_price": 180, "currency": "USD"},
                 ])
    result = extract(model, source)
    assert result.errors == []
    assert result.total() == 515
    for description, quantity, rate in (
        ("Landing page (6 hours)", 5, 67),
        ("Landing page (5 pages)", 5, 67),
        ("Landing page (5 hours)", 6, 67),
        ("Landing page (5 hours)", 5, 180),
        ("Landing page phase 5 (5 hours)", 5, 67),
    ):
        wrong = dict(model, line_items=[dict(model["line_items"][0],
                     desc=description, qty=quantity, unit_price=rate)])
        assert extract(wrong, source).errors, (description, quantity, rate)


def test_quantity_annotation_cannot_disambiguate_shared_work_by_price():
    source = ("From: Alex Example <alex@example.com>\n"
              "Audio mixing was five hours at $67 per hour; "
              "audio mastering was five hours at $90 per hour.")
    model = dict(GOOD, line_items=[
        {"desc": "Audio (5 hours)", "qty": 5, "unit_price": 67, "currency": "USD"}
    ])
    assert any(i.field.endswith("unit_price") for i in extract(model, source).errors)


def test_quantity_annotation_does_not_erase_numbered_service_names():
    source = ("From: Alex Example <alex@example.com>\n"
              "Phase 1 design was five hours at $67 per hour; "
              "Phase 2 design was five hours at $90 per hour.")
    good = dict(GOOD, line_items=[
        {"desc": "Phase 2 design (5 hours)", "qty": 5, "unit_price": 90, "currency": "USD"},
        {"desc": "Phase 1 design (5 hours)", "qty": 5, "unit_price": 67, "currency": "USD"},
    ])
    assert extract(good, source).errors == []
    wrong = dict(good, line_items=[dict(good["line_items"][0], unit_price=67), good["line_items"][1]])
    assert any(i.field == "line_items[0].unit_price" for i in extract(wrong, source).errors)


def test_retained_harbor_four_hours_remains_grounded_across_long_work_wording():
    from pathlib import Path
    from decimal import Decimal
    fixture=json.loads((Path(__file__).parent/'fixtures/harbor-original-native.json').read_text())
    result=LLMExtractor(lambda _:json.dumps(fixture['model_response']),
                        ground_prices=True,check_missing_work=True).extract(fixture['source'])
    assert result.errors == [], result.issues
    assert result.total() == Decimal('404')
    assert result.line_items[1].qty == Decimal('4')
    assert result.line_items[1].unit == 'hour'


def test_retained_harbor_grounding_still_rejects_changed_counts_estimates_and_periods():
    from pathlib import Path
    fixture=json.loads((Path(__file__).parent/'fixtures/harbor-original-native.json').read_text())
    # Keep the service wording constant so each negative case tests its count
    # or period, rather than failing earlier because 'Four' changed in the name.
    model=json.loads(json.dumps(fixture['model_response']))
    model['line_items'][1]['desc']='audio cleanup'
    assert not LLMExtractor(lambda _:json.dumps(model),ground_prices=True,
                            check_missing_work=True).extract(fixture['source']).errors
    for replacement,field in [
        ('Five hours','qty'), ('About four hours','qty'), ('Four days','unit'),
        ('If approved, four hours','unit_price'),
    ]:
        source=fixture['source'].replace('Four hours',replacement)
        result=LLMExtractor(lambda _:json.dumps(model),
                            ground_prices=True,check_missing_work=True).extract(source)
        assert any(issue.field=='line_items[1].'+field for issue in result.errors), (replacement,result.issues)
