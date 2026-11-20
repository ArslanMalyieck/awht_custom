"""ReportView PDF data is re-fetched through the cost-visibility reportview runner.

The browser supplies only identifiers, filters and row-selection requests. It
cannot supply rendered rows, labels, HTML, permissions, or a query to execute.
"""

import json
import math
import re

import frappe
from frappe.model import get_permitted_fields
from frappe.model.meta import get_default_df
from frappe.model.utils import is_virtual_doctype
from frappe.utils import cint

from awht_custom.pdf_export.report_pdf import MAX_COLUMNS, MAX_FILTER_BYTES, MAX_FILTERS, MAX_ROWS
from awht_custom.pdf_export.report_pdf import _render_report_pdf


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_DOCTYPE = re.compile(r"[A-Za-z0-9 _-]{1,140}\Z")
_ORDER_PART = re.compile(
    r"(?:`tab([A-Za-z0-9 _-]+)`\.)?`?([A-Za-z_][A-Za-z0-9_]*)`?\s+(asc|desc)\Z", re.I
)
_OPERATORS = frozenset({"=", "!=", ">", "<", ">=", "<=", "in", "not in", "like", "not like", "between", "is"})


def _authorize(doctype, report_name):
    if (not isinstance(doctype, str) or not _DOCTYPE.fullmatch(doctype)
            or report_name is not None and not isinstance(report_name, str)
            or frappe.session.user == "Guest"):
        frappe.throw("Branded Report View is not available", frappe.PermissionError)
    meta = frappe.get_meta(doctype)
    if (meta.istable or meta.issingle or is_virtual_doctype(doctype)
            or not all(frappe.has_permission(doctype, permission)
                       for permission in ("read", "report", "print"))):
        frappe.throw("No permission to print this Report View", frappe.PermissionError)

    if report_name:
        if not isinstance(report_name, str) or len(report_name) > 140:
            frappe.throw("Invalid Report View", frappe.PermissionError)
        report = frappe.get_doc("Report", report_name)
        if (report.name != report_name or report.report_type != "Report Builder"
                or report.ref_doctype != doctype or cint(report.disabled) or not report.is_permitted()):
            frappe.throw("No permission to print this Report View", frappe.PermissionError)
    return meta


@frappe.whitelist()
def can_download_report_view_pdf(doctype, report_name=None):
    try:
        _authorize(doctype, report_name)
    except (frappe.PermissionError, frappe.DoesNotExistError):
        return False
    return True


def _json(value, name, max_length=MAX_FILTER_BYTES):
    if isinstance(value, str):
        if len(value) > max_length:
            frappe.throw(f"{name} exceeds the PDF limit", frappe.ValidationError)
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            frappe.throw(f"Invalid {name}", frappe.ValidationError)
    return value


def _field(meta, doctype, fieldname, owner):
    if (not isinstance(fieldname, str) or not _IDENTIFIER.fullmatch(fieldname)
            or not isinstance(owner, str) or not _DOCTYPE.fullmatch(owner)):
        frappe.throw("Invalid Report View field", frappe.ValidationError)
    if owner != doctype:
        if not any(df.fieldtype in ("Table", "Table MultiSelect") and df.options == owner
                   for df in meta.fields):
            frappe.throw("Unrelated Report View DocType", frappe.PermissionError)
        if not frappe.get_meta(owner).istable:
            frappe.throw("Invalid Report View child table", frappe.PermissionError)
    if fieldname not in get_permitted_fields(owner, parenttype=doctype if owner != doctype else None,
                                             ignore_virtual=True):
        frappe.throw("Report View field is not permitted", frappe.PermissionError)

    df = frappe.get_meta(owner).get_field(fieldname) or get_default_df(fieldname)
    if not df or df.get("report_hide") or df.get("is_virtual") or df.get("hidden"):
        frappe.throw("Report View field is hidden or unavailable", frappe.PermissionError)
    key = fieldname if owner == doctype else f"{owner}:{fieldname}"
    label = frappe._(df.label or fieldname)
    if owner != doctype:
        label += f" ({frappe._(owner)})"
    if len(label) > 256:
        frappe.throw("Report View field label is too long", frappe.ValidationError)
    return {"fieldname": key, "label": label, "fieldtype": df.fieldtype,
            "options": df.options, "precision": df.get("precision"),
            "source": fieldname, "owner": owner}


def _field_sql(doctype, column):
    owner, fieldname = column["owner"], column["source"]
    sql = f"`tab{owner}`.`{fieldname}`"
    return sql if owner == doctype else f"{sql} as '{owner}:{fieldname}'"


def _fields(meta, doctype, value):
    fields = _json(value, "fields")
    if not isinstance(fields, list) or not 0 < len(fields) <= MAX_COLUMNS:
        frappe.throw("Invalid Report View columns", frappe.ValidationError)
    columns = []
    seen = set()
    for pair in fields:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            frappe.throw("Invalid Report View column", frappe.ValidationError)
        col = _field(meta, doctype, pair[0], pair[1])
        if col["fieldname"] in seen:
            frappe.throw("Duplicate Report View column", frappe.ValidationError)
        seen.add(col["fieldname"])
        columns.append(col)
    return columns


def _filter_value(value):
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, float):
        return math.isfinite(value) and abs(value) <= 10**15
    if isinstance(value, (str, int)):
        return len(str(value)) <= 256
    if isinstance(value, list):
        return len(value) <= 50 and all(_filter_value(item) and not isinstance(item, list) for item in value)
    return False


def _filters(meta, doctype, value):
    filters = _json(value, "filters")
    if filters is None:
        return [], [], None
    if not isinstance(filters, list) or len(filters) > MAX_FILTERS:
        frappe.throw("Invalid Report View filters", frappe.ValidationError)
    normalized, displayed, company = [], [], None
    for item in filters:
        if not isinstance(item, (list, tuple)) or len(item) not in (3, 4):
            frappe.throw("Invalid Report View filter", frappe.ValidationError)
        owner, fieldname, op, val = (doctype, *item) if len(item) == 3 else item
        col = _field(meta, doctype, fieldname, owner)
        if not isinstance(op, str) or op not in _OPERATORS or not _filter_value(val):
            frappe.throw("Invalid Report View filter value or operator", frappe.ValidationError)
        if op in ("in", "not in", "between") and not isinstance(val, list):
            frappe.throw("Report View filter needs a list", frappe.ValidationError)
        if op == "between" and len(val) != 2:
            frappe.throw("Report View range needs two values", frappe.ValidationError)
        if col["fieldtype"] == "Link" and col["options"] and op in ("=", "in"):
            for name in val if isinstance(val, list) else [val]:
                if name and not (frappe.has_permission(col["options"], "read", doc=name)
                                 or frappe.has_permission(col["options"], "select", doc=name)):
                    frappe.throw("No permission for Report View Link filter", frappe.PermissionError)
        if owner == doctype and fieldname == "company" and op == "=" and isinstance(val, str):
            company = val
        normalized.append([owner, fieldname, op, val])
        displayed.append((f"{col['label']} {op}", val))
    if len(json.dumps(normalized, ensure_ascii=True)) > MAX_FILTER_BYTES:
        frappe.throw("Report View filters exceed the PDF limit", frappe.ValidationError)
    return normalized, displayed, company


def _order(meta, doctype, order_by, grouped):
    if not order_by:
        return "creation desc" if not grouped else "_aggregate_column desc"
    if not isinstance(order_by, str) or len(order_by) > 256:
        frappe.throw("Invalid Report View order", frappe.ValidationError)
    if grouped and order_by == "_aggregate_column desc":
        return order_by
    parts = order_by.split(",")
    if len(parts) > 3 or grouped:
        frappe.throw("Invalid Report View order", frappe.ValidationError)
    safe = []
    for part in parts:
        match = _ORDER_PART.fullmatch(part.strip())
        if not match:
            frappe.throw("Invalid Report View order", frappe.ValidationError)
        owner, field, direction = match.groups()
        column = _field(meta, doctype, field, owner or doctype)
        safe.append(f"`tab{column['owner']}`.`{column['source']}` {direction.lower()}")
    return ", ".join(safe)


def _names(value, label):
    names = _json(value, label, MAX_ROWS * 145)
    if names is None:
        return None
    if (not isinstance(names, list) or len(names) > MAX_ROWS
            or any(not isinstance(name, str) or not name or len(name) > 140 for name in names)
            or len(set(names)) != len(names)):
        frappe.throw(f"Invalid {label}", frappe.ValidationError)
    return names


def _grouping(meta, doctype, value):
    group = _json(value, "grouping", 1024)
    if group is None:
        return None
    if not isinstance(group, dict) or set(group) != {"field", "owner", "function", "on_field", "on_owner"}:
        frappe.throw("Invalid Report View grouping", frappe.ValidationError)
    first = _field(meta, doctype, group["field"], group["owner"])
    if group["function"] not in ("count", "sum", "avg"):
        frappe.throw("Invalid Report View aggregate", frappe.ValidationError)
    aggregate = _field(meta, doctype, group["on_field"], group["on_owner"])
    if group["function"] != "count" and aggregate["fieldtype"] not in ("Currency", "Float", "Int"):
        frappe.throw("Report View aggregate must be numeric", frappe.ValidationError)
    label = "Count" if group["function"] == "count" else f"{group['function'].title()} of {aggregate['label']}"
    second = {"fieldname": "_aggregate_column", "label": label,
              "fieldtype": "Int" if group["function"] == "count" else aggregate["fieldtype"],
              "options": aggregate.get("options"), "precision": aggregate.get("precision")}
    return first, second, aggregate, group["function"]


@frappe.whitelist(methods=["POST"])
def download_report_view_pdf(
    doctype, report_name=None, fields=None, filters=None, order_by=None, row_count=None,
    visible_names=None, selected_items=None, only_fields=None, grouping=None, add_totals_row=0,
    orientation="Landscape", letter_head=None, words_column=None, words_mode="last",
):
    meta = _authorize(doctype, report_name)
    from awht_custom.visibility import report_builder
    from awht_custom.visibility.api import get_policy

    group = _grouping(meta, doctype, grouping)
    columns = [group[0], group[1]] if group else _fields(meta, doctype, fields)
    if group and (visible_names is not None or selected_items is not None):
        frappe.throw("Grouped Report View row selection is unavailable", frappe.ValidationError)
    if group and fields is not None:
        requested = _json(fields, "fields")
        if requested != [[group[0]["source"], group[0]["owner"]],
                         ["_aggregate_column", group[2]["owner"]]]:
            frappe.throw("Invalid grouped Report View columns", frappe.ValidationError)

    filter_values, display_filters, company = _filters(meta, doctype, filters)
    order_by = _order(meta, doctype, order_by, bool(group))
    selected = _names(selected_items, "selected rows")
    visible = _names(visible_names, "visible rows")
    if visible is not None and selected and not set(selected) <= set(visible):
        frappe.throw("Selected rows are not visible", frappe.ValidationError)
    if (not isinstance(row_count, (int, str)) or len(str(row_count)) > 5
            or not str(row_count).isdigit()
            or not 0 < int(row_count) <= MAX_ROWS):
        frappe.throw("Report View exceeds the PDF row limit", frappe.ValidationError)

    policy = get_policy()
    if policy.get("restricted"):
        patterns = policy.get("patterns") or []
        if group and (report_builder._sensitive(_field_sql(doctype, group[0]), group[0], doctype,
                                                report_name, patterns)
                      or report_builder._sensitive(_field_sql(doctype, group[2]), group[2], doctype,
                                                   report_name, patterns)):
            frappe.throw("Restricted Report View grouping is unavailable", frappe.PermissionError)
        blocked = {col["fieldname"] for col in columns
                   if report_builder._sensitive(_field_sql(doctype, col), col, doctype,
                                                report_name, patterns)} if not group else set()
    else:
        blocked = set()

    fields_for_query = [group[0]] if group else [col for col in columns if col["fieldname"] not in blocked]
    if not group and "name" not in {col["fieldname"] for col in fields_for_query}:
        fields_for_query.insert(0, _field(meta, doctype, "name", doctype))
    if not fields_for_query or (not group and not any(col["fieldname"] not in blocked for col in columns)):
        frappe.throw("No visible Report View columns remain", frappe.PermissionError)

    params = frappe._dict(
        doctype=doctype, fields=json.dumps([_field_sql(doctype, col) for col in fields_for_query]),
        filters=json.dumps(filter_values), order_by=order_by, start="0", page_length=str(row_count),
        save_user_settings="false",
    )
    if group:
        params.update(group_by=_field_sql(doctype, group[0]).split(" as ")[0],
                      aggregate_function=group[3], aggregate_on_field=group[2]["source"],
                      aggregate_on_doctype=group[2]["owner"])

    original = frappe.local.form_dict
    try:
        frappe.local.form_dict = params
        result = report_builder.get()
    finally:
        frappe.local.form_dict = original

    if result == []:
        frappe.throw("No accessible Report View rows to export", frappe.ValidationError)
    if (not isinstance(result, dict) or not isinstance(result.get("keys"), list)
            or not isinstance(result.get("values"), list) or len(result["values"]) > MAX_ROWS
            or len(result["keys"]) > MAX_COLUMNS + 2
            or any(not isinstance(key, str) for key in result["keys"])
            or len(result["keys"]) != len(set(result["keys"]))):
        frappe.throw("Invalid Report View response", frappe.ValidationError)
    keys = result["keys"]
    required = {col["fieldname"] for col in fields_for_query}
    if group:
        required.add("_aggregate_column")
    if not required <= set(keys):
        frappe.throw("Report View field was not authorized by the server", frappe.PermissionError)
    data = []
    for values in result["values"]:
        if not isinstance(values, (list, tuple)) or len(values) != len(keys):
            frappe.throw("Report View returned misaligned rows", frappe.ValidationError)
        data.append(dict(zip(keys, values)))

    if not group:
        by_name = {}
        for row in data:
            name = row.get("name")
            if not isinstance(name, str) or not name:
                frappe.throw("Report View row identity is unavailable", frappe.PermissionError)
            by_name.setdefault(name, row)
        if visible is not None and not set(visible) <= set(by_name):
            frappe.throw("Visible rows were not returned by the authorized report", frappe.PermissionError)
        if selected and not set(selected) <= set(by_name):
            frappe.throw("Selected rows were not returned by the authorized report", frappe.PermissionError)
        names = visible if visible is not None else list(by_name)
        data = [by_name[name] for name in names if not selected or name in selected]

    allowed = [col for col in columns if col["fieldname"] not in blocked]
    if only_fields is not None:
        # Sensitive fields may still be visible (blank) in the Desk grid, but
        # may never be selected as the source of amount-in-words or PDF cells.
        picked = _json(only_fields, "selected columns", 4096)
        if (not isinstance(picked, list) or not picked
                or any(not isinstance(name, str) for name in picked)
                or len(set(picked)) != len(picked)
                or not set(picked) <= {col["fieldname"] for col in columns}):
            frappe.throw("Invalid Report View column selection", frappe.ValidationError)
        allowed = [col for col in allowed if col["fieldname"] in picked]
    if not allowed:
        frappe.throw("No permitted Report View columns selected", frappe.PermissionError)

    if cint(add_totals_row) not in (0, 1):
        frappe.throw("Invalid Report View total-row option", frappe.ValidationError)
    context = {"company": company} if company else {}
    if cint(add_totals_row) and data:
        from decimal import Decimal, InvalidOperation

        total = {"is_total_row": True}
        if not group:
            total["name"] = "Total"
        elif allowed[0]["fieldtype"] not in ("Currency", "Float", "Int"):
            total[allowed[0]["fieldname"]] = "Total"
        for col in allowed:
            if col["fieldtype"] not in ("Currency", "Float", "Int"):
                continue
            try:
                total[col["fieldname"]] = sum(
                    Decimal(str(row.get(col["fieldname"]) or 0)) for row in data
                )
            except InvalidOperation:
                frappe.throw("Invalid numeric Report View total", frappe.ValidationError)
        data.append(total)

    _render_report_pdf(
        report_name or doctype, allowed, data, context, orientation=orientation,
        letter_head=letter_head, words_column=words_column, words_mode=words_mode,
        has_total_row=bool(cint(add_totals_row) and data),
        display_filters=display_filters, doctype=doctype,
    )
