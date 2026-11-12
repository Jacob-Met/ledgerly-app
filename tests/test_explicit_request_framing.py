from decimal import Decimal
import pytest
from ledgerly.extract import RulesExtractor

HEADER = 'Bill to: Cedar Studio <accounts@cedar.example>\n'

@pytest.mark.parametrize('framing', ['Bill the', 'Please invoice the', 'Please bill us the'])
def test_definite_article_keeps_complete_counted_work(framing):
    ex = RulesExtractor().extract(HEADER + framing + ' three half-day editing sessions at GBP 225 each, plus twelve image touch-ups at GBP 8 each.\nNet 14.')
    assert [(x.qty, x.unit_price) for x in ex.line_items] == [(Decimal(3), Decimal(225)), (Decimal(12), Decimal(8))]
    assert ex.line_items[0].unit == 'sessions'
    assert ex.total() == Decimal(771)
    assert not ex.errors

@pytest.mark.parametrize('prefix', ["There's also a fixed", "There’s a", "There is also a separate", "Please include a"])
def test_explicit_fee_contraction_and_purpose_preserve_literal_work(prefix):
    ex = RulesExtractor().extract(HEADER + prefix + ' GBP 18 transfer fee for the final files.\nNet 14.')
    assert len(ex.line_items) == 1
    assert ex.line_items[0].desc == 'Transfer fee for the final files'
    assert ex.line_items[0].qty == 1 and ex.total() == Decimal(18)
    assert not ex.errors

@pytest.mark.parametrize('source', [
    "Please bill the three half-day editing sessions at GBP 225 each, plus twelve image touch-ups.",
    "Please bill the three half-day editing sessions at GBP 225 each if approved.",
    "There's also a fixed GBP 18 transfer fee for the final files if approved.",
    "There's also a GBP 18 transfer fee for each file.",
    "There's also a GBP 18 transfer fee for monthly delivery.",
    "There's also a fixed GBP 18 transfer fee for the final files and GBP 8 storage.",
])
def test_incomplete_conditional_or_recurring_work_stays_unready(source):
    ex = RulesExtractor().extract(HEADER + source + '\nNet 14.')
    assert ex.errors

@pytest.mark.parametrize('reference', ['Purchase order NB-2026-128.', 'PO NB-2026-128.', 'Client reference NB-2026-128.'])
def test_bare_literal_reference_does_not_hide_work_or_calendar_terms(reference):
    source = 'Please invoice 2 x USD 145 programme layout revisions. Payment due December 18, 2026. ' + reference
    ex = RulesExtractor().extract(source)
    assert [(x.qty, x.unit_price) for x in ex.line_items] == [(Decimal(2), Decimal(145))]
    assert ex.total() == Decimal(290) and ex.due_date == '2026-12-18'
    assert 'NB-2026-128' in ex.note
    assert ex.client_email is None
    assert any(issue.field == 'client_email' for issue in ex.errors)

@pytest.mark.parametrize('tail', [
    'Purchase order pending.',
    'Purchase order NB-2026-128 if approved.',
    'Purchase order NB-2026-128 or NB-2026-129.',
])
def test_unknown_or_conditional_bare_reference_is_not_detached(tail):
    from ledgerly.extract import _invoice_metadata_lines, source_invoice_note, DEFAULT_INVOICE_NOTE
    source = 'Please invoice 2 x USD 145 programme layout revisions. ' + tail
    assert _invoice_metadata_lines(source) == [source]
    assert source_invoice_note(source)[0] == DEFAULT_INVOICE_NOTE
