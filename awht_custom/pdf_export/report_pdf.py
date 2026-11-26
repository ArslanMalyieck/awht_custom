"""Authenticated, server-rendered branded PDFs for Frappe Query Reports."""

import base64
import json
import math
import os
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from urllib.parse import unquote, urlsplit

import frappe
import nh3
from bs4 import BeautifulSoup
from frappe.desk.query_report import get_column_as_dict, get_report_doc
from frappe.utils import cint, formatdate, nowdate


QUERY_REPORT_TYPES = frozenset({"Query Report", "Script Report", "Custom Report"})
MAX_FILTER_BYTES = 8192
MAX_FILTERS = 40
MAX_FILTER_VALUES = 50
MAX_COLUMNS = 64
MAX_ROWS = 50000
MAX_CELL_LENGTH = 2048
MAX_RENDERED_CELLS_LENGTH = 50000000
MAX_COLUMN_LABEL_LENGTH = 256
MAX_LETTER_HEAD_LENGTH = 16000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 512000

_FILTER_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_FILE_IMAGE = re.compile(r"/(?:private/)?files/[^/\\%?#<>\"'\x00-\x1f]{1,128}\Z")
_FILE_ID = re.compile(r"fid=([A-Za-z0-9_-]{1,64})\Z")
_NUMBER = re.compile(r"-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,8})?\Z")
_COLOR = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\Z")
_SIZE = re.compile(r"(?:[1-9]|[1-9]\d|[1-3]\d\d|400)px\Z")
_SAFE_STYLES = {
    "text-align": re.compile(r"(?:left|right|center|justify)\Z"),
    "font-weight": re.compile(r"(?:normal|bold|[1-9]00)\Z"),
    "font-size": _SIZE,
    "color": _COLOR,
    "background-color": _COLOR,
    "width": re.compile(r"(?:[1-9]|[1-9]\d|[1-3]\d\d|400)px|100%\Z"),
    "height": _SIZE,
}
_BLOCKED_FILTERS = {
    "user", "ignore_prepared_report", "prepared_report_name", "custom_columns", "js_filters"
}
_LINK_FILTERS = {
    "company": "Company", "finance_book": "Finance Book", "account": "Account",
    "party_account": "Account", "cost_center": "Cost Center", "project": "Project",
    "customer": "Customer", "supplier": "Supplier", "customer_group": "Customer Group",
    "supplier_group": "Supplier Group", "territory": "Territory",
    "sales_person": "Sales Person", "sales_partner": "Sales Partner",
}


def _authorized_report(report_name):
    if (not isinstance(report_name, str) or not report_name or len(report_name) > 140
            or frappe.session.user == "Guest"):
        frappe.throw("Branded PDF is not available for this report", frappe.PermissionError)

    report = frappe.get_doc("Report", report_name)
    if (
        report.name != report_name
        or report.report_type not in QUERY_REPORT_TYPES
        or not report.ref_doctype
        or cint(report.disabled)
        or not report.is_permitted()  # Includes Report roles and custom role overrides.
        or not frappe.has_permission(report.ref_doctype, "report")
        or not frappe.has_permission(report.ref_doctype, "print")
    ):
        frappe.throw("Branded PDF is not available for this report", frappe.PermissionError)

    if cint(report.prepared_report):
        frappe.throw("Prepared reports cannot be synchronously exported", frappe.ValidationError)
    if report.report_type == "Custom Report":
        reference = get_report_doc(report_name)
        if (reference.report_type not in ("Query Report", "Script Report")
                or reference.ref_doctype != report.ref_doctype
                or cint(reference.prepared_report)):
            frappe.throw("Unsupported or prepared reference report", frappe.ValidationError)
    return report


@frappe.whitelist()
def can_download_report_pdf(report_name):
    """Server-side button check; never infer Report role access from client roles."""
    if not isinstance(report_name, str) or not report_name or len(report_name) > 140:
        return False
    try:
        _authorized_report(report_name)
    except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
        return False
    return True


def _parse_filters(filters):
    if filters is None or filters == "":
        return {}
    if isinstance(filters, str):
        if len(filters) > MAX_FILTER_BYTES:
            frappe.throw("Too many report filters", frappe.ValidationError)
        try:
            filters = json.loads(filters)
        except json.JSONDecodeError:
            frappe.throw("Invalid report filters", frappe.ValidationError)

    if not isinstance(filters, dict) or len(filters) > MAX_FILTERS:
        frappe.throw("Invalid report filters", frappe.ValidationError)

    def valid_value(value):
        return (
            value is None
            or isinstance(value, str) and len(value) <= 256
            or isinstance(value, bool)
            or isinstance(value, int) and abs(value) <= 10**15
            or isinstance(value, float) and math.isfinite(value) and abs(value) <= 10**15
        )

    for key, value in filters.items():
        if not isinstance(key, str) or not _FILTER_KEY.fullmatch(key) or key in _BLOCKED_FILTERS:
            frappe.throw("Invalid report filter", frappe.ValidationError)
        if isinstance(value, list):
            if len(value) > MAX_FILTER_VALUES or not all(valid_value(item) for item in value):
                frappe.throw("Invalid report filter value", frappe.ValidationError)
        elif not valid_value(value):
            frappe.throw("Invalid report filter value", frappe.ValidationError)

    if len(json.dumps(filters, ensure_ascii=True)) > MAX_FILTER_BYTES:
        frappe.throw("Too many report filters", frappe.ValidationError)
    return dict(filters)


def _validate_link_filters(filters, report):
    # Unlike Desk, this endpoint does not accept client-authored js_filters.
    # Derive additional Link targets only from the saved Report and DocType metadata.
    links = dict(_LINK_FILTERS)
    for field in report.get("filters") or []:
        if field.get("fieldtype") == "Link" and field.get("fieldname") in filters:
            links[field.fieldname] = field.options
    meta = frappe.get_meta(report.ref_doctype)
    for fieldname in filters:
        field = meta.get_field(fieldname)
        if field and field.fieldtype == "Link" and field.options:
            links[fieldname] = field.options

    for fieldname, doctype in links.items():
        value = filters.get(fieldname)
        for name in value if isinstance(value, list) else [value]:
            if name in (None, ""):
                continue
            if not isinstance(doctype, str) or not doctype or not isinstance(name, str):
                frappe.throw("Invalid report Link filter", frappe.ValidationError)
            if not (
                frappe.has_permission(doctype, "read", doc=name)
                or frappe.has_permission(doctype, "select", doc=name)
            ):
                frappe.throw("No permission for report Link filter", frappe.PermissionError)


def _selected_columns(columns, only_fields):
    if not isinstance(columns, (list, tuple)) or not 0 < len(columns) <= MAX_COLUMNS:
        frappe.throw("Report has too many or no columns", frappe.ValidationError)

    visible = []
    names = set()
    for index, original in enumerate(columns):
        if not isinstance(original, (str, dict)):
            frappe.throw("Invalid report column", frappe.ValidationError)
        column = get_column_as_dict(original)
        name = column.get("fieldname")
        label = column.get("label")
        if not isinstance(name, str) or not name or len(name) > 128 or name in names:
            frappe.throw("Invalid or duplicate report column", frappe.ValidationError)
        if label is not None and (not isinstance(label, str) or len(label) > MAX_COLUMN_LABEL_LENGTH):
            frappe.throw("Invalid report column label", frappe.ValidationError)
        names.add(name)
        if column.get("hidden") in (None, False, 0, "0", ""):
            visible.append((index, column))

    if isinstance(only_fields, str):
        if len(only_fields) > 4096:
            frappe.throw("Invalid column selection", frappe.ValidationError)
        try:
            only_fields = json.loads(only_fields)
        except json.JSONDecodeError:
            frappe.throw("Invalid column selection", frappe.ValidationError)

    if only_fields is not None:
        if (
            not isinstance(only_fields, list)
            or not 0 < len(only_fields) <= MAX_COLUMNS
            or any(not isinstance(name, str) for name in only_fields)
            or len(set(only_fields)) != len(only_fields)
            or not set(only_fields) <= {column["fieldname"] for _, column in visible}
        ):
            frappe.throw("Invalid or hidden column selection", frappe.ValidationError)
        selected = set(only_fields)
        visible = [(index, column) for index, column in visible if column["fieldname"] in selected]

    if not visible:
        frappe.throw("Report has no visible columns", frappe.ValidationError)
    return visible


def _format_cell(value, fieldtype):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        if any(isinstance(item, (list, tuple, dict)) for item in value):
            frappe.throw("Invalid report cell", frappe.ValidationError)
        value = ", ".join(str(item) for item in value)
    elif isinstance(value, Mapping):
        frappe.throw("Invalid report cell", frappe.ValidationError)
    elif fieldtype == "Date" and value != "":
        value = formatdate(value)

    # Preserve numeric precision as returned by the authorized runner; do not
    # round every Currency column to two decimals or interpret formatted HTML.
    text = str(value)
    if len(text) > MAX_CELL_LENGTH:
        frappe.throw("Report cell is too long", frappe.ValidationError)
    return escape(text, quote=True)


def _row_value(row, index, fieldname):
    if isinstance(row, Mapping):
        return row.get(fieldname)
    return row[index] if index < len(row) else None


def _decimal_amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        frappe.throw("Amount in words requires numeric values", frappe.ValidationError)
    text = str(value).strip()
    if isinstance(value, str) and not _NUMBER.fullmatch(text):
        frappe.throw("Amount in words requires numeric values", frappe.ValidationError)
    try:
        amount = Decimal(text.replace(",", ""))
    except InvalidOperation:
        frappe.throw("Invalid amount in words", frappe.ValidationError)
    if not amount.is_finite() or abs(amount) > Decimal("1000000000000"):
        frappe.throw("Amount is too large for words", frappe.ValidationError)
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _amount_words(selected, all_columns, rows, filters, words_column, words_mode, has_total_row):
    if words_column in (None, "__off__"):
        return "", "", None
    if words_mode not in (None, "last", "sum"):
        frappe.throw("Invalid amount-in-words mode", frappe.ValidationError)

    available = [(index, col) for index, col in selected if col.get("fieldtype") == "Currency"]
    if words_column == "__auto__":
        if not available:
            frappe.throw("Select a visible Currency column or turn off amount in words", frappe.ValidationError)
        priorities = (
            "balance", "closing", "outstanding", "grand_total", "net_total",
            "rounded_total", "amount", "total", "debit", "credit",
        )
        chosen = next(
            (matches[-1] for word in priorities
             if (matches := [(i, c) for i, c in available
                             if word in (c["fieldname"] + " " + str(c.get("label") or "")).lower()])),
            available[-1],
        )
    else:
        chosen = next(((i, c) for i, c in selected if c["fieldname"] == words_column), None)
        if not chosen or chosen[1].get("fieldtype") not in ("Currency", "Float"):
            frappe.throw("Amount-in-words column must be visible and numeric", frappe.ValidationError)

    index, column = chosen
    if column.get("precision") not in (None, "", 2, "2"):
        frappe.throw("Selected SAR column has incompatible currency precision", frappe.ValidationError)

    amounts = []
    for row in rows[:-1] if has_total_row else rows:
        if isinstance(row, Mapping) and (row.get("is_total_row") or row.get("bold")):
            continue
        value = _row_value(row, index, column["fieldname"])
        if value in (None, ""):
            continue

        by_name = (row if isinstance(row, Mapping) else {
            col["fieldname"]: row[i] for i, col in enumerate(all_columns) if i < len(row)
        })
        option = column.get("options")
        if option == "Company:default_currency":
            currency = None
        elif isinstance(option, str) and re.fullmatch(r"[A-Z]{3}", option):
            currency = option
        elif option:
            currency = by_name.get(option) or filters.get(option)
            if not currency:
                frappe.throw("Selected column currency is unavailable", frappe.ValidationError)
        else:
            currency = by_name.get("currency") or filters.get("presentation_currency")

        if not currency:
            company = by_name.get("company") or filters.get("company")
            if not company:
                company = frappe.defaults.get_user_default("Company")
            if company:
                if not frappe.has_permission("Company", "read", doc=company):
                    frappe.throw("No permission to resolve report currency", frappe.PermissionError)
                currency = frappe.db.get_value("Company", company, "default_currency")
        if (currency != "SAR" or by_name.get("currency") not in (None, "", "SAR")
                or filters.get("presentation_currency") not in (None, "", "SAR")):
            frappe.throw("Amount in words requires a known, single SAR currency", frappe.ValidationError)
        amounts.append(_decimal_amount(value))

    if not amounts:
        frappe.throw("No visible SAR amount to convert to words", frappe.ValidationError)
    amount = sum(amounts) if words_mode == "sum" else next(
        (value for value in reversed(amounts) if value), amounts[-1]
    )
    if abs(amount) > Decimal("1000000000000"):
        frappe.throw("Amount is too large for words", frappe.ValidationError)

    from num2words import num2words

    try:
        en = num2words(abs(amount), lang="en", to="currency", currency="SAR")
        ar = num2words(abs(amount), lang="ar", to="currency", currency="SAR")
    except (ValueError, OverflowError, InvalidOperation, NotImplementedError):
        frappe.throw("Unable to convert SAR amount to words", frappe.ValidationError)
    if amount == 0:
        ar = "\u0635\u0641\u0631 \u0631\u064a\u0627\u0644 \u0633\u0639\u0648\u062f\u064a"
    elif abs(amount) < 1:
        ar = "\u0635\u0641\u0631 \u0631\u064a\u0627\u0644 \u0633\u0639\u0648\u062f\u064a " + ar.lstrip()
    if amount < 0:
        en = "minus " + en
        ar = "\u0633\u0627\u0644\u0628 " + ar
    return escape(en, quote=True), escape(ar, quote=True), index


def _image_data(file_url):
    if not isinstance(file_url, str):
        frappe.throw("Letter Head image URL is not a local File", frappe.PermissionError)
    try:
        parsed = urlsplit(file_url)
        file_path = unquote(parsed.path, errors="strict")
    except (ValueError, UnicodeError):
        frappe.throw("Letter Head image URL is not a local File", frappe.PermissionError)
    if (parsed.scheme or parsed.netloc or parsed.fragment or not _FILE_IMAGE.fullmatch(file_path)
            or file_path.rsplit("/", 1)[-1] in (".", "..")):
        frappe.throw("Letter Head image URL is not a local File", frappe.PermissionError)

    private = file_path.startswith("/private/files/")
    file_id = _FILE_ID.fullmatch(parsed.query) if parsed.query else None
    if parsed.query and (not private or not file_id):
        frappe.throw("Invalid Letter Head File ID", frappe.PermissionError)
    file_id = file_id.group(1) if file_id else None
    file_filters = {"file_url": file_path}
    if file_id:
        file_filters["name"] = file_id
    for entry in frappe.get_all(
        "File", filters=file_filters, fields=["name"], limit_page_length=20
    ):
        file_doc = frappe.get_doc("File", entry.name)
        if not frappe.has_permission("File", "read", doc=file_doc):
            continue
        if (file_doc.file_url != file_path or bool(cint(file_doc.is_private)) != private
                or file_id and file_doc.name != file_id):
            frappe.throw("Invalid Letter Head File", frappe.PermissionError)
        if cint(file_doc.file_size) > MAX_IMAGE_BYTES:
            frappe.throw("Letter Head image is too large", frappe.ValidationError)

        # File.get_full_path checks normal paths, but also reject symlinks out
        # of the site's public/private files directory before reading bytes.
        base = os.path.realpath(frappe.get_site_path("private" if private else "public", "files"))
        path = os.path.realpath(file_doc.get_full_path())
        if os.path.commonpath((base, path)) != base:
            frappe.throw("Invalid Letter Head File path", frappe.PermissionError)
        content = file_doc.get_content()
        if not isinstance(content, bytes) or len(content) > MAX_IMAGE_BYTES:
            frappe.throw("Invalid Letter Head image", frappe.ValidationError)
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif content.startswith((b"GIF87a", b"GIF89a")):
            mime = "image/gif"
        else:
            frappe.throw("Unsupported Letter Head image", frappe.ValidationError)
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"

    frappe.throw("No permission to read Letter Head image", frappe.PermissionError)


def _safe_letter_head_html(raw_html):
    if not raw_html:
        return ""
    if not isinstance(raw_html, str) or len(raw_html) > MAX_LETTER_HEAD_LENGTH:
        frappe.throw("Letter Head HTML is too long", frappe.ValidationError)

    soup = BeautifulSoup(raw_html, "html.parser")
    images = soup.find_all("img")
    if len(images) > MAX_IMAGES:
        frappe.throw("Too many Letter Head images", frappe.ValidationError)
    for image in images:
        raw_style = image.get("style")
        image.attrs = {"src": _image_data(image.get("src")), "alt": image.get("alt", "")}
        if raw_style:
            image["style"] = raw_style

    for tag in soup.find_all(True):
        original = tag.get("style")
        if not isinstance(original, str):
            tag.attrs.pop("style", None)
            continue
        safe = []
        for declaration in original.split(";"):
            if ":" not in declaration:
                continue
            property_name, value = (part.strip() for part in declaration.split(":", 1))
            if property_name in _SAFE_STYLES and _SAFE_STYLES[property_name].fullmatch(value):
                safe.append(f"{property_name}: {value}")
        if safe:
            tag["style"] = "; ".join(safe)
        else:
            tag.attrs.pop("style", None)

    # Only bounded CSS primitives survive; no url(), imports or remote assets.
    return nh3.clean(
        str(soup),
        tags={"div", "p", "span", "br", "b", "strong", "i", "em", "u", "table", "thead",
              "tbody", "tr", "th", "td", "img"},
        attributes={"*": {"style"}, "img": {"src", "alt", "style"}},
        filter_style_properties=set(_SAFE_STYLES),
        url_schemes={"data"},
        clean_content_tags={"script", "style", "svg", "iframe", "object", "embed", "form", "link"},
        strip_comments=True,
    )


def _letter_head_parts(filters, letter_head):
    if letter_head == "__none__":
        return "", ""
    if letter_head is not None and (not isinstance(letter_head, str) or len(letter_head) > 128):
        frappe.throw("Invalid Letter Head", frappe.ValidationError)

    if not letter_head:
        company = filters.get("company") or frappe.defaults.get_user_default("Company")
        if not company:
            return "", ""
        if not frappe.has_permission("Company", "read", doc=company):
            frappe.throw("No permission to read Company", frappe.PermissionError)
        letter_head = frappe.db.get_value("Company", company, "default_letter_head")
    if not letter_head:
        return "", ""

    doc = frappe.get_doc("Letter Head", letter_head)
    if cint(doc.disabled) or not frappe.has_permission("Letter Head", "read", doc=doc):
        frappe.throw("No permission to read Letter Head", frappe.PermissionError)
    return _safe_letter_head_html(doc.get("content")), _safe_letter_head_html(doc.get("footer"))


def _render_report_pdf(
    title, columns, rows, filters, *, only_fields=None, orientation="Landscape",
    letter_head=None, words_column=None, words_mode="last", has_total_row=False,
    display_filters=None, doctype=None,
):
    if orientation not in ("Landscape", "Portrait"):
        frappe.throw("Invalid PDF orientation", frappe.ValidationError)
    if not isinstance(rows, (list, tuple)) or len(rows) > MAX_ROWS:
        frappe.throw("Report exceeds the PDF row limit", frappe.ValidationError)
    selected = _selected_columns(columns, only_fields)
    normalized = [get_column_as_dict(col) for col in columns]
    for row in rows:
        if not isinstance(row, (Mapping, list, tuple)):
            frappe.throw("Invalid report row", frappe.ValidationError)

    amount_en, amount_ar, words_index = _amount_words(
        selected, normalized, rows, filters, words_column, words_mode, has_total_row
    )
    rendered_columns = [
        {"label": escape(str(col.get("label") or col["fieldname"]), quote=True),
         "numeric": col.get("fieldtype") in ("Currency", "Float", "Int")}
        for _, col in selected
    ]
    formatted_rows = []
    total_length = 0
    for row in rows:
        cells = []
        for index, col in selected:
            value = _row_value(row, index, col["fieldname"])
            if index == words_index and value not in (None, ""):
                value = f"{_decimal_amount(value):,.2f}"
            cells.append(_format_cell(value, col.get("fieldtype")))
        total_length += sum(len(cell) for cell in cells)
        if total_length > MAX_RENDERED_CELLS_LENGTH:
            frappe.throw("Report exceeds the PDF size limit", frappe.ValidationError)
        formatted_rows.append(cells)

    header, footer = _letter_head_parts(filters, letter_head)
    filter_items = [
        (escape(str(label), quote=True),
         escape(", ".join(map(str, value)) if isinstance(value, list) else str(value), quote=True))
        for label, value in (display_filters if display_filters is not None else filters.items())
        if value not in (None, "", [])
    ]
    html = frappe.render_template(
        "awht_custom/pdf_export/report_pdf.html",
        {"title": escape(title, quote=True), "columns": rendered_columns, "rows": formatted_rows,
         "filters": filter_items, "header": header, "footer": footer,
         "amount_words_en": amount_en, "amount_words_ar": amount_ar,
         "today": escape(formatdate(nowdate()), quote=True)},
    )

    from frappe.core.doctype.access_log.access_log import make_access_log
    from frappe.utils.pdf import get_pdf

    try:
        from frappe.utils.print_format import _pdf_bypass_proxy_hosts
    except ImportError:
        # Older v15 builds may not expose the host allow-list helper. Keep the
        # same pinned-proxy isolation and bypass only the current site host.
        def _pdf_bypass_proxy_hosts():
            try:
                host = urlsplit(frappe.utils.get_url()).hostname
            except (AttributeError, TypeError, ValueError):
                host = None
            return [host] if host else []

    pdf = get_pdf(html, {
        "orientation": orientation,
        "proxy": "http://0.0.0.0:0",
        "bypass-proxy-for": _pdf_bypass_proxy_hosts(),
        "load-error-handling": "ignore",
    })
    make_access_log(
        doctype=doctype, report_name=title if not doctype or title != doctype else None,
        filters=filters, file_type="PDF", method="Branded Report PDF",
        columns=json.dumps([col["fieldname"] for _, col in selected]),
    )
    frappe.local.response.filename = (re.sub(r"[^\w.-]+", "-", title).strip("-.") or "Report") + ".pdf"
    frappe.local.response.filecontent = pdf
    frappe.local.response.type = "download"


@frappe.whitelist(methods=["POST"])
def download_report_pdf(
    report_name, filters=None, only_fields=None, orientation="Landscape", letter_head=None,
    words_column=None, words_mode="last",
):
    """Re-run an authorized report in the current session, never use browser rows."""
    report = _authorized_report(report_name)
    if orientation not in ("Landscape", "Portrait"):
        frappe.throw("Invalid PDF orientation", frappe.ValidationError)
    filters = _parse_filters(filters)
    _validate_link_filters(filters, report)

    # This import must fail closed if Cost Visibility has not been deployed.
    from awht_custom.visibility import report_filter

    result = report_filter.run(report_name, filters=filters, are_default_filters=False)
    if not isinstance(result, dict) or result.get("prepared_report"):
        frappe.throw("Prepared or invalid report result", frappe.ValidationError)
    _render_report_pdf(
        report_name, result.get("columns"), result.get("result"), filters,
        only_fields=only_fields, orientation=orientation, letter_head=letter_head,
        words_column=words_column, words_mode=words_mode,
        has_total_row=bool(result.get("add_total_row")), doctype=report.ref_doctype,
        display_filters=[(key.replace("_", " ").title(), value) for key, value in filters.items()],
    )
