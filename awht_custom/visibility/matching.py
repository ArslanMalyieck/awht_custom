"""Classify cost fields for presentation filtering, not data authorization."""

import re
from collections.abc import Mapping


_PURCHASE_DOCTYPE = re.compile(r"^(purchase (invoice|order|receipt)|supplier quotation)( item)?$")
_SELLING_DOCTYPE = re.compile(r"^(sales (invoice|order)|quotation|delivery note)( item)?$")
_SELLING = re.compile(r"\b(sales|selling)\b")
_PURCHASE_REPORT = re.compile(r"\b(purchase|buying)\b|\bsupplier quotation\b")
_COMPANY_CURRENCY = re.compile(r"^(rate|net rate|price list rate|amount|net amount) \(company currency\)$")

_PURCHASE_FIELDS = frozenset({
    "rate", "net rate", "base rate", "base net rate", "price list rate",
    "base price list rate", "stock uom rate", "amount", "net amount", "base amount",
    "base net amount", "total", "net total", "base total", "base net total",
    "grand total", "base grand total", "rounded total", "base rounded total",
    "billed amt", "billed amount", "pending amount", "received qty amount",
})
_INTRINSIC_FIELDS = frozenset({
    "incoming rate", "outgoing rate", "basic rate", "basic amount",
    "valuation", "valuation rate", "stock value", "stock value difference",
    "buying rate", "buying amount", "gross profit", "gross profit %",
    "gross profit percent", "gross profit percentage", "cost of goods sold",
})
_STOCK_BALANCE_FIELDS = frozenset({
    "bal val", "balance value", "opening val", "opening value",
    "in val", "in value", "out val", "out value", "val rate",
})
_STOCK_BALANCE_REPORTS = frozenset({"stock balance", "batch wise balance history"})


def _get(value, key):
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _normalize(value):
    return re.sub(r"[_\s]+", " ", value.lower()).strip() if isinstance(value, str) else ""


def matches(field, patterns, *, doctype=None, report_name=None, ref_doctype=None, document=None):
    """Match names/labels and known ERPNext cost columns in their report context."""
    if isinstance(field, str):
        field = {"fieldname": field, "label": field}
    if field is None:
        return False

    df = _get(field, "df")
    docfield = _get(field, "docfield")
    values = [_get(field, key) for key in ("fieldname", "field", "id", "label", "name", "content")]
    values += [_get(nested, key) for nested in (df, docfield) for key in ("fieldname", "label")]
    texts = [_normalize(value) for value in values]
    if isinstance(patterns, str):
        patterns = (patterns,)
    patterns = [_normalize(pattern) for pattern in (patterns or ())]
    if any(pattern and pattern in text for text in texts for pattern in patterns):
        return True

    names = [_normalize(value.rsplit(":", 1)[-1]) if isinstance(value, str) else "" for value in values]
    if any(name in _INTRINSIC_FIELDS for name in names):
        return True

    owners = [_get(field, "parent"), _get(field, "doctype")]
    owners += [_get(nested, key) for nested in (df, docfield) for key in ("parent", "doctype")]
    if any(_SELLING_DOCTYPE.fullmatch(_normalize(owner)) for owner in owners):
        return False

    report = _normalize(report_name).replace("-", " ")
    if report in _STOCK_BALANCE_REPORTS and any(name in _STOCK_BALANCE_FIELDS for name in names):
        return True
    if report == "warehouse wise item balance age and value" and "value" in names:
        return True

    document_doctype = _normalize(_get(document, "doctype"))
    if ("price list rate" in names and "item price" in (
        _normalize(doctype), document_doctype, _normalize(ref_doctype), *map(_normalize, owners)
    )):
        # Lists and reports cannot distinguish buying and selling rows. Hide the
        # whole rate column unless a document positively identifies a selling row.
        return document is None or _get(document, "buying") not in (False, 0, "0")

    if any(_SELLING.search(text) for text in texts):
        return False
    if not any(name in _PURCHASE_FIELDS or _COMPANY_CURRENCY.fullmatch(name) for name in names):
        return False
    if any(_PURCHASE_DOCTYPE.fullmatch(_normalize(owner)) for owner in owners):
        return True
    return bool(
        _PURCHASE_DOCTYPE.fullmatch(_normalize(doctype))
        or _PURCHASE_DOCTYPE.fullmatch(document_doctype)
        or _PURCHASE_DOCTYPE.fullmatch(_normalize(ref_doctype))
        or (_PURCHASE_REPORT.search(report) and not _SELLING.search(report))
    )
