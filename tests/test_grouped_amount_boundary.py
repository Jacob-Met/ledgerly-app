import pytest
from decimal import Decimal
from ledgerly.extract import RulesExtractor

@pytest.mark.parametrize("amount", ["EUR 1 200", "EUR 1 2000", "EUR 12 34", "EUR 5\u200912", "EUR 4\u2009800", "EUR 4\u2007800", "4\u2009800 EUR", "4\u2007800 EUR", "EUR 1\u00a0200", "EUR 1\u202f200", "CHF 1'200.00", "CHF 1’200.00", "1 200 EUR", "1\u202f200 EUR", "1'200.00 CHF"])
@pytest.mark.parametrize("kind", ["line", "table", "total", "paid"])
def test_incomplete_grouped_amount_requires_review(amount, kind):
    line = {"line": f"Design at {amount}", "table": f"Item | Qty | Unit price\nDesign | 1 | {amount}", "total": f"Design at EUR 1200\nTotal: {amount}", "paid": f"Design at EUR 1200\nWe already paid {amount}"}[kind]
    ex = RulesExtractor().extract("From: Nia <nia@example.test>\n"+line+"\nNet 21")
    assert any(issue.severity == "error" for issue in ex.issues)
    assert not any(item.unit_price == Decimal("1") for item in ex.line_items)

@pytest.mark.parametrize("line,total", [
    ("Design at EUR 1,200.00", "1200"),
    ("Design at EUR 1200", "1200"),
    ("3 hours of editing at EUR 80/hr", "240"),
    ("Design at EUR 80 for 2 revisions", "80"),
    ("Design at 1200 EUR", "1200"),
])
def test_complete_supported_prices_remain_usable(line, total):
    ex = RulesExtractor().extract("From: Nia <nia@example.test>\n"+line+"\nNet 21")
    assert ex.total() == Decimal(total)
    assert not any(issue.severity == "error" for issue in ex.issues)
