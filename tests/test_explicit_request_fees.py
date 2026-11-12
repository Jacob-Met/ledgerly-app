import json
from decimal import Decimal
from pathlib import Path
import pytest
from ledgerly.extract import RulesExtractor

CASES=json.loads((Path(__file__).parent/'fixtures/explicit-request-fees.json').read_text())['cases']

@pytest.mark.parametrize('case',CASES,ids=lambda case:case['id'])
def test_explicit_named_fees_keep_complete_invoice(case):
    ex=RulesExtractor().extract(case['source']);want=case['expected']
    assert not ex.errors
    assert ex.total()==Decimal(want['total'])
    assert ex.client_email==want['client_email']
    assert ex.due_days==want['due_days'] and ex.due_date==want['due_date']
    assert ex.amount_paid==Decimal(want['amount_paid'])
    assert len(ex.line_items)==len(want['items'])
    for actual,expected in zip(ex.line_items,want['items']):
        assert actual.qty==Decimal(expected['qty'])
        assert actual.unit_price==Decimal(expected['unit_price'])
        assert any(word in actual.desc.lower() for word in expected['description_any'])

@pytest.mark.parametrize('fee',[
 'The optional courier fee is GBP 19.',
 'The courier fee is GBP 19 if approved.',
 'The monthly service fee is GBP 19.',
 'The courier fee is GBP 19 per hour.',
 'The refundable booking charge is GBP 19.',
 'The tax charge is GBP 19.',
 'The credit fee is GBP 19.',
 'The courier fee is GBP 19 and handling is GBP 8.',
])
def test_unresolved_fee_does_not_become_ready_one_time_charge(fee):
    ex=RulesExtractor().extract('Bill to: Studio <billing@studio.example>\nTwo hours of editing at GBP 30 per hour.\n'+fee+'\nNet 14.')
    assert ex.errors

@pytest.mark.parametrize('line,qty',[
 ('Invoice five display panels at GBP 64 per panel.',Decimal('5')),
 ('Please bill us two finished artwork files at GBP 25 per file.',Decimal('2')),
 ('Invoice 3hrs of cleanup at GBP 28 per hour.',Decimal('3')),
])
def test_counted_request_keeps_noun_and_quantity(line,qty):
    ex=RulesExtractor().extract('Bill to: Studio <billing@studio.example>\n'+line+'\nNet 14.')
    assert len(ex.line_items)==1 and not ex.errors
    assert ex.line_items[0].qty==qty

def test_counted_request_does_not_convert_rate_period():
    ex=RulesExtractor().extract('Bill to: Studio <billing@studio.example>\nInvoice five display panels at GBP 64 per hour.\nNet 14.')
    assert ex.errors and ex.line_items[0].qty is None

def test_named_total_is_not_an_extra_charge():
    ex=RulesExtractor().extract('Bill to: Studio <billing@studio.example>\nTwo hours of editing at GBP 30 per hour.\nThe agreed total fee is GBP 60.\nNet 14.')
    assert ex.total()==60 and len(ex.line_items)==1


def test_independent_price_first_work_lines_keep_both_charges():
    ex=RulesExtractor().extract('Bill to: Studio <billing@studio.example>\n3 x USD 185 brand workshops\n2 x USD 47.50 printed field guides\nNet 14.')
    assert not ex.errors and ex.currency=='USD' and ex.total()==650
    assert [(x.desc,x.qty,x.unit_price) for x in ex.line_items]==[
        ('Brand workshops',Decimal('3'),Decimal('185')),
        ('Printed field guides',Decimal('2'),Decimal('47.50'))]
    assert ex.line_items[0].unit=='workshops'

@pytest.mark.parametrize('line',[
 '3 x USD 185',
 '3 x USD 185 per hour',
 '3 x USD 185 optional brand workshops',
 '3 x USD 185 brand workshops and USD 20 courier',
])
def test_price_first_work_does_not_hide_uncertainty(line):
    ex=RulesExtractor().extract('Bill to: Studio <billing@studio.example>\n'+line+'\nNet 14.')
    assert ex.errors


@pytest.mark.parametrize('quantity',['about 3','3-5'])
def test_price_first_estimates_keep_existing_confirmation_warning(quantity):
    ex=RulesExtractor().extract('Bill to: Studio <billing@studio.example>\n'+quantity+' x USD 185 brand workshops\nNet 14.')
    assert any(issue.field=='line_items[0].qty' and issue.severity=='warning' for issue in ex.issues)
    assert ex.line_items[0].qty==3


def test_exact_independent_cedar_request_keeps_recipient_and_date():
    source=(Path(__file__).parent/'fixtures/cedar-lantern-original.txt').read_text()
    ex=RulesExtractor().extract(source)
    assert not ex.errors and ex.total()==650 and len(ex.line_items)==2
    assert ex.client_name=='Cedar Lantern Co' and ex.client_email=='billing@cedarlantern.example'
    assert ex.due_date=='2026-11-25'

@pytest.mark.parametrize("prefix", ["Please invoice", "Bill us", "Invoice"])
@pytest.mark.parametrize("terms", ["Payment due November 30, 2026.", "Pay by 2026-11-30.", "Due on 30 November 2026."])
def test_price_first_request_keeps_complete_calendar_sentence(prefix, terms):
    ex=RulesExtractor().extract("From: Oak Vale Studio <accounts@oakvale.example>\n\n"
        +prefix+" 2 x USD 120 portrait sessions. "+terms)
    assert not ex.errors and ex.total()==240
    assert ex.due_date=="2026-11-30" and ex.due_days is None
    assert [(item.desc,item.qty,item.unit_price,item.unit) for item in ex.line_items]==[
        ("Portrait sessions",Decimal("2"),Decimal("120"),"sessions")]

@pytest.mark.parametrize("line", [
    "Please invoice 2 x USD 120 optional portrait sessions. Payment due November 30, 2026.",
    "Please invoice 2 x USD 120 portrait sessions. Payment due November 30, 2026 if approved.",
    "Please invoice 2 x USD 120 per hour. Payment due November 30, 2026.",
    "Please invoice 2 x USD 120 portrait sessions and USD 20 courier. Payment due November 30, 2026.",
])
def test_inline_calendar_term_does_not_erase_uncertain_work(line):
    ex=RulesExtractor().extract("From: Oak Vale Studio <accounts@oakvale.example>\n\n"+line)
    assert ex.errors
