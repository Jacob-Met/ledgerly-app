"""Explicit visitor corrections for the offline browser demo.

This adapter supplies a checked extraction through the core's existing Extractor
protocol. It grants no send or payment permit and never changes the rules extractor.
"""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, DecimalException
import re
import ast
from hashlib import sha256

from ledgerly.extract import (
    Extraction, Issue, LineItem, PAYPAL_CURRENCIES, RulesExtractor,
    compute_confidence, validate, DEFAULT_INVOICE_NOTE,
)


def _text(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    return value.strip()


class ReviewFieldError(ValueError):
    """A rejected editable value with its exact form field."""
    def __init__(self, field, message):
        super().__init__(message)
        self.issues = [{"field": field, "severity": "error", "message": message}]


def _decimal(value, field, key):
    raw = _text(value, field)
    if not re.fullmatch(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", raw):
        raise ReviewFieldError(key, f"{field} needs a plain decimal number, such as 2 or 12.50.")
    number = Decimal(raw)
    if not number.is_finite():
        raise ReviewFieldError(key, f"{field} must be finite.")
    return number


def prepare_review(fields):
    """Rebuild editable fields and validate them, without trusting derived metadata."""
    allowed = {"client_name", "client_email", "due_days", "amount_paid", "line_items"}
    if (not isinstance(fields, dict) or not allowed <= set(fields)
            or set(fields) - allowed - {"due_date", "note"}):
        raise ValueError("Provide exactly the editable invoice fields.")
    note = fields.get("note", DEFAULT_INVOICE_NOTE)
    if not isinstance(note, str):
        raise ReviewFieldError("note", "The invoice note must be text.")
    if len(note) > 4000:
        raise ReviewFieldError("note", "Keep the invoice note to at most 4000 characters.")
    raw_items = fields["line_items"]
    if not isinstance(raw_items, list):
        raise ValueError("Line items must be a list.")
    items = []
    extra = []
    for index, row in enumerate(raw_items):
        prefix = f"line_items[{index}]"
        if not isinstance(row, dict) or set(row) != {"desc", "qty", "unit_price", "currency", "unit"}:
            raise ValueError(f"Line item {index + 1} has incomplete or unexpected fields.")
        desc = _text(row["desc"], f"Line item {index + 1} description")
        currency = _text(row["currency"], f"Line item {index + 1} currency").upper()
        if not desc:
            extra.append(Issue(prefix + ".desc", "error", "Describe the work on this line."))
        if currency not in PAYPAL_CURRENCIES:
            extra.append(Issue(prefix + ".currency", "error", "Choose a supported currency for this line."))
        items.append(LineItem(
            desc,
            _decimal(row["qty"], f"Line item {index + 1} quantity", prefix + ".qty"),
            _decimal(row["unit_price"], f"Line item {index + 1} unit price", prefix + ".unit_price"),
            currency or None,
            _text(row["unit"], f"Line item {index + 1} unit") or None,
        ))
    days = _text(fields["due_days"], "Payment terms")
    due_date = _text(fields.get("due_date", ""), "Due date") or None
    if not due_date and not re.fullmatch(r"[0-9]{1,3}", days):
        raise ReviewFieldError("due_days", "Choose an exact due date or a whole number of days from 0 to 365.")
    if days and not re.fullmatch(r"[0-9]{1,3}", days):
        raise ReviewFieldError("due_days", "Payment terms need a whole number of days from 0 to 365.")
    paid = _decimal(fields["amount_paid"], "Prior payment", "amount_paid")
    if paid < 0:
        extra.append(Issue("amount_paid", "error", "Prior payment cannot be negative."))
    ex = Extraction(
        _text(fields["client_name"], "Client name") or None,
        _text(fields["client_email"], "Recipient email") or None,
        items[0].currency if items else None,
        items, int(days) if days else None, paid, source="human_review", due_date=due_date, note=note,
    )
    try:
        # A visitor may supply a recipient absent from the source; this is an
        # explicit human correction, never an extractor grounding claim.
        ex.issues = extra + validate(ex)
        ex.total()  # Refuse unsupported decimal arithmetic before any draft effect.
    except DecimalException as exc:
        raise ValueError("These invoice values exceed the supported decimal precision.") from exc
    ex.confidence = compute_confidence(ex.issues)
    return ex


class ReviewableExtractor:
    """Use one reviewed value only inside a source-bound draft operation."""
    def __init__(self):
        self.rules = RulesExtractor()
        self._active = None

    def extract(self, text):
        if self._active is None:
            return self.rules.extract(text)
        source, extraction = self._active
        if text != source:
            raise ValueError("The source changed after review; analyze and check it again.")
        return deepcopy(extraction)

    @contextmanager
    def using(self, text, extraction):
        if self._active is not None:
            raise RuntimeError("A reviewed draft is already running.")
        self._active = (text, deepcopy(extraction))
        try:
            yield
        finally:
            self._active = None


def source_amount_requirements(analysis):
    """Stable requirements belong to the original analysis."""
    from ledgerly.extract import MONEY_RE, _price_clause
    requirements = []
    for issue in analysis.get("issues", []):
        message = issue.get("message", "")
        if issue.get("field") != "line_items" or not message.startswith(("A source amount", "Multiple monetary amounts on one priced line")):
            continue
        quote = message.partition("drafting: ")[2]
        if not quote:
            continue
        try:
            parsed = ast.literal_eval(quote)
            if isinstance(parsed, str):
                quote = parsed
        except (ValueError, SyntaxError):
            pass
        prices = list(MONEY_RE.finditer(quote))
        if message.startswith("Multiple monetary amounts") or len(prices) > 1:
            # Every unresolved price needs a decision, including when the
            # extractor rejected the whole compound rather than one item.
            for index, price in enumerate(prices):
                clause, _ = _price_clause(quote, price.start())
                key = sha256((message + ":" + str(index)).encode()).hexdigest()[:20]
                requirements.append({"id": key, "quote": clause.strip(),
                                     "source_issue": message, "source_issue_count": len(prices)})
        else:
            requirements.append({"id": sha256(message.encode()).hexdigest()[:20], "quote": quote})
    return requirements


def check_source_amount_resolutions(ex, analysis, resolutions, source):
    """A general acknowledgement cannot dispose of an omitted charge."""
    from ledgerly.extract import _ground_model_prices
    requirements = source_amount_requirements(analysis)
    if not isinstance(resolutions, dict):
        resolutions = {}
    for requirement in requirements:
        key, quote = requirement["id"], requirement["quote"]
        resolution = resolutions.get(key, {})
        kind = resolution.get("kind") if isinstance(resolution, dict) else None
        detail = resolution.get("detail", "") if isinstance(resolution, dict) else ""
        resolved = False
        if kind == "included" and isinstance(detail, str) and re.fullmatch(r"[1-9][0-9]*", detail.strip()):
            index = int(detail.strip()) - 1
            if index < len(ex.line_items):
                item_ex = deepcopy(ex)
                item_ex.line_items = [deepcopy(ex.line_items[index])]
                grounded = _ground_model_prices(item_ex, source)
                def same_source_clause(issue):
                    clause = issue.message.partition("drafting: ")[2].strip().casefold()
                    original = quote.strip().casefold()
                    return bool(clause and (clause in original or original in clause))
                resolved = not any(
                    issue.severity == "error" and (
                        issue.field.startswith("line_items[0]") or
                        issue.message.startswith("A source amount") and same_source_clause(issue)
                    ) for issue in grounded
                )
        elif kind == "excluded" and isinstance(detail, str) and len(detail.strip()) >= 8:
            resolved = True
        if resolved:
            ex.issues.append(Issue("line_items", "info",
                f"Source amount resolved by human review ({kind}: {detail.strip()}): {quote}"))
        else:
            ex.issues.append(Issue("source_amounts." + key, "error",
                "Choose the matching invoice item, or give a specific reason not billed."))
    ex.confidence = compute_confidence(ex.issues)
