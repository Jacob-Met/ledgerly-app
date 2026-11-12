from decimal import Decimal
from ledgerly.extract import Extraction, LineItem, _ground_model_prices, _price_clause, MONEY_RE


def invoice():
    return Extraction("Ashdown", "accounts@example.test", "GBP", [
        LineItem("Half-day facilitation sessions", Decimal(4), Decimal(180), "GBP", "sessions"),
        LineItem("Printed field guides", Decimal(12), Decimal("7.50"), "GBP", "guides"),
    ], 21)


def test_separate_optional_sentence_does_not_merge_agreed_work():
    source = ("We're going with four half-day facilitation sessions at GBP 180 per session "
              "and twelve printed field guides at GBP 7.50 each. "
              "The optional GBP 85 recording edit has not been approved.")
    clauses = [_price_clause(source, match.start())[0] for match in MONEY_RE.finditer(source)]
    assert "field guides" not in clauses[0]
    assert "facilitation" not in clauses[1]
    assert "optional" in clauses[2]
    assert not [issue for issue in _ground_model_prices(invoice(), source) if issue.severity == "error"]
    wrong = invoice()
    wrong.line_items[0].unit_price = Decimal("7.50")
    assert any(issue.severity == "error" for issue in _ground_model_prices(wrong, source))


def test_qualification_within_same_sentence_still_requires_review():
    source = ("Optional four half-day facilitation sessions at GBP 180 per session "
              "and twelve printed field guides at GBP 7.50 each.")
    assert any(issue.severity == "error" for issue in _ground_model_prices(invoice(), source))


def test_unrelated_sentence_does_not_authorize_optional_invoice_item():
    source = ("Four half-day facilitation sessions at GBP 180 per session and "
              "twelve printed field guides at GBP 7.50 each. "
              "The optional GBP 85 recording edit has not been approved.")
    ex = invoice()
    ex.line_items.append(LineItem("Recording edit", Decimal(1), Decimal(85), "GBP", "item"))
    assert any(issue.severity == "error" and issue.field.startswith("line_items[2]")
               for issue in _ground_model_prices(ex, source))
