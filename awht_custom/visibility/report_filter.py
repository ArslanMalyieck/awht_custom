"""Supported query-report views, exports and prepared downloads; not an access boundary.

Raw File routes need the separate file_filter hooks. Arbitrary report APIs and
report scripts remain outside this presentation filter.
"""

import datetime
import inspect
import json
from decimal import Decimal

import frappe
from frappe import _
from frappe.core.doctype.prepared_report import prepared_report
from frappe.desk import query_report
from frappe.desk.reportview import clean_params, parse_json
from frappe.utils import cint, sbool

from awht_custom.visibility.api import BRANCH_REPORTS, get_policy
from awht_custom.visibility.matching import matches


OVERRIDE_WHITELISTED_METHODS = {
    "frappe.desk.query_report.run": "awht_custom.visibility.report_filter.run",
    "frappe.desk.query_report.export_query": "awht_custom.visibility.report_filter.export_query",
    "frappe.core.doctype.prepared_report.prepared_report.download_attachment":
        "awht_custom.visibility.report_filter.download_attachment",
    "frappe.core.doctype.prepared_report.prepared_report.enqueue_json_to_csv_conversion":
        "awht_custom.visibility.report_filter.enqueue_json_to_csv_conversion",
}

_COLUMN_KEYS = frozenset({
    "fieldname", "label", "fieldtype", "options", "width", "precision", "hidden",
    "align", "editable", "bold", "is_tree", "convertible", "doctype", "parent",
})
_SCALARS = (str, int, float, bool, Decimal, datetime.date, datetime.time, datetime.timedelta)
_FILTERED_DOWNLOAD = "awht_custom.visibility.report_filter.download_attachment"


def _supports_parameter(func, name):
    """Return True when a core function accepts the named keyword parameter."""
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return True
    return name in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _get_csv_bytes(data, csv_params):
    """Frappe v15's CSV writer rejects v16's decimal_sep parameter."""
    from frappe.desk.utils import get_csv_bytes

    try:
        return get_csv_bytes(data, csv_params)
    except TypeError as exc:
        if "decimal_sep" not in str(exc):
            raise
        params = dict(csv_params)
        params.pop("decimal_sep", None)
        return get_csv_bytes(data, params)


def _deny_prepared_report():
    frappe.throw(_("Prepared report is unavailable"), frappe.PermissionError)


def _requested_prepared_name(filters):
    if isinstance(filters, str):
        try:
            filters = json.loads(filters)
        except ValueError:
            return None  # Core validates invalid filters.
    return filters.get("prepared_report_name") if isinstance(filters, dict) else None


def _check_branch_report(report_name, filters=None, policy=None):
    if report_name not in BRANCH_REPORTS:
        return None
    if report_name in ("Branch Financial Exposure", "AWHT Current Credit Exposure") and (policy or get_policy()).get("restricted"):
        _deny_prepared_report()
    if _requested_prepared_name(filters):
        _deny_prepared_report()
    report = query_report.get_report_doc(report_name)
    if sbool(report.prepared_report):
        _deny_prepared_report()
    return report


def _check_prepared_doc(doc, report_name, requested_name=None):
    name = getattr(doc, "name", None)
    if (not isinstance(name, str) or not name or getattr(doc, "report_name", None) != report_name
            or (requested_name and name != requested_name)
            or not callable(getattr(doc, "has_permission", None)) or not doc.has_permission("read")):
        _deny_prepared_report()
    return name


def _preflight_prepared_report(report_name, filters, ignore_prepared_report=False,
                               custom_columns=None, report=None):
    name = _requested_prepared_name(filters)
    if not name or sbool(ignore_prepared_report) or custom_columns:
        return None
    report = report or query_report.get_report_doc(report_name)
    if not sbool(report.prepared_report):
        return None
    if not isinstance(name, str):
        _deny_prepared_report()
    _check_prepared_doc(frappe.get_doc("Prepared Report", name), report_name, name)
    return name


def _check_prepared_result(data, report_name, requested_name=None):
    if not isinstance(data, dict) or not data.get("prepared_report"):
        return None
    doc = data.get("doc")
    if doc:
        return _check_prepared_doc(doc, report_name, requested_name)
    if data.get("columns") or data.get("result"):
        _deny_prepared_report()
    return None


def _scalar(value):
    return value if value is None or isinstance(value, _SCALARS) else None


def filter_result(data, policy, report_name, ref_doctype, prepared_doc_name=None):
    """Filter a core result at read time, including previously prepared reports."""
    if not policy.get("restricted"):
        return data

    patterns = policy.get("patterns") or []

    def sensitive(field):
        return matches(field, patterns, report_name=report_name, ref_doctype=ref_doctype)

    source_columns = data.get("columns") or []
    columns = [query_report.get_column_as_dict(column) for column in source_columns]
    # A duplicate name/label must not reintroduce a removed column's value.
    blocked = set()
    for col in columns:
        if sensitive(col):
            blocked.update(v for v in (col.get("fieldname"), col.get("label")) if isinstance(v, str))

    kept = []
    for index, col in enumerate(columns):
        name, label = col.get("fieldname"), col.get("label")
        if not isinstance(name, str) or not name:
            continue
        if sensitive(col) or name in blocked or (isinstance(label, str) and label in blocked):
            continue
        clean = {key: _scalar(value) for key, value in col.items() if key in _COLUMN_KEYS}
        if sensitive(clean.get("options")):
            clean.pop("options", None)
        # Custom reports need this metadata to restore previously added columns.
        for key in ("id", "insert_after_index"):
            value = col.get(key)
            if isinstance(value, (str, int)) and not sensitive(value):
                clean[key] = value
        link_field = col.get("link_field")
        if isinstance(link_field, dict):
            link_field = link_field.get("fieldname")
        if isinstance(link_field, str) and link_field and not sensitive(link_field):
            clean["link_field"] = {"fieldname": link_field}
        kept.append((index, clean))

    keys = {value for _, col in kept for value in (col.get("fieldname"), col.get("label"))
            if isinstance(value, str) and value not in blocked}
    rows = []
    for row in (data.get("result") or []) if kept else []:
        if isinstance(row, dict):
            cleaned = {key: _scalar(value) for key, value in row.items() if key in keys}
            if "indent" in row and isinstance(row["indent"], (int, float)) and "indent" not in blocked:
                cleaned["indent"] = row["indent"]
            rows.append(cleaned)
        elif isinstance(row, (list, tuple)):
            rows.append([_scalar(row[index]) if index < len(row) else None for index, _ in kept])
        else:
            rows.append([None] * len(kept))

    result = {
        "columns": [column for _, column in kept],
        "result": rows,
        "message": None,
        "chart": None,
        "report_summary": None,
    }
    for key in ("skip_total_row", "add_total_row", "prepared_report", "execution_time",
                "snapshot_report", "snapshot_at"):
        if key in data:
            result[key] = _scalar(data[key])
    if isinstance(data.get("custom_filters"), dict):
        result["custom_filters"] = {
            key: value if not isinstance(value, list) else list(value)
            for key, value in data["custom_filters"].items()
            if isinstance(key, str) and not sensitive(key)
            and (value is None or isinstance(value, _SCALARS)
                 or (isinstance(value, list) and all(item is None or isinstance(item, _SCALARS) for item in value)))
        }
    if not kept:
        result["add_total_row"] = 0

    # Do not return the Prepared Report document or its raw attachment link.
    if data.get("prepared_report"):
        doc = data.get("doc")
        result["doc"] = ({"filters": "{}", "report_end_time": _scalar(doc.get("report_end_time"))}
                         if doc else None)
        if result["doc"] is not None and prepared_doc_name:
            result["doc"]["name"] = prepared_doc_name
    return result


@frappe.whitelist()
@frappe.read_only()
def run(
    report_name, filters=None, user=None, ignore_prepared_report=False,
    custom_columns=None, is_tree=False, parent_field=None,
    are_default_filters=True, js_filters=None,
):
    """Drop-in query_report.run for Desk and filtered PDF callers."""
    report = _check_branch_report(report_name, filters)
    requested_name = _preflight_prepared_report(
        report_name, filters, ignore_prepared_report, custom_columns, report
    )
    kwargs = dict(
        report_name=report_name, filters=filters, user=user,
        ignore_prepared_report=ignore_prepared_report, custom_columns=custom_columns,
        is_tree=is_tree, parent_field=parent_field, are_default_filters=are_default_filters,
    )
    # js_filters only exists in Frappe v16. v15 validates the same fields via
    # Report filters and DocType metadata, so silently omit the extra keyword.
    if _supports_parameter(query_report.run, "js_filters"):
        kwargs["js_filters"] = js_filters
    data = frappe.call(query_report.run, **kwargs)
    prepared_name = _check_prepared_result(data, report_name, requested_name)
    policy = get_policy()
    if not policy.get("restricted"):
        return data
    report = report or query_report.get_report_doc(report_name)
    if prepared_name and frappe.override_whitelisted_method(
        "frappe.core.doctype.prepared_report.prepared_report.download_attachment"
    ) != _FILTERED_DOWNLOAD:
        _deny_prepared_report()
    return filter_result(data, policy, report_name, report.ref_doctype, prepared_doc_name=prepared_name)


@frappe.whitelist()
def download_attachment(dn):
    """Filter v16 prepared JSON downloads, rejecting unknown shapes for restricted users."""
    policy = get_policy()
    doc = frappe.get_doc("Prepared Report", dn)
    if doc.report_name in BRANCH_REPORTS:
        _deny_prepared_report()
    if not policy.get("restricted"):
        return prepared_report.download_attachment(dn)

    _check_prepared_doc(doc, doc.report_name, dn)
    report = query_report.get_report_doc(doc.report_name)
    raw, filename = doc.get_prepared_data(with_file_name=True)
    try:
        payload = json.loads(raw)
    except (UnicodeError, TypeError, ValueError):
        frappe.throw(_("Unable to safely filter this prepared report"), frappe.PermissionError)
    if (not isinstance(filename, str) or not filename.endswith(".json.gz")
            or not isinstance(payload, dict) or not isinstance(payload.get("columns"), list)
            or not isinstance(payload.get("result"), list)):
        frappe.throw(_("Unable to safely filter this prepared report"), frappe.PermissionError)
    if any(not isinstance(column, (str, dict)) or (isinstance(column, dict)
            and "fieldname" not in column and "label" not in column) for column in payload["columns"]):
        frappe.throw(_("Unable to safely filter this prepared report"), frappe.PermissionError)

    try:
        filtered = filter_result(payload, policy, doc.report_name, report.ref_doctype)
    except (KeyError, TypeError, ValueError):
        frappe.throw(_("Unable to safely filter this prepared report"), frappe.PermissionError)
    frappe.local.response.filename = filename[:-3]
    frappe.local.response.filecontent = frappe.as_json(filtered, indent=None, separators=(",", ":")).encode("utf-8")
    frappe.local.response.type = "binary"


@frappe.whitelist()
def enqueue_json_to_csv_conversion(prepared_report_name):
    """Do not create an unfiltered CSV attachment for restricted users."""
    if get_policy().get("restricted"):
        _deny_prepared_report()
    if frappe.get_doc("Prepared Report", prepared_report_name).report_name in BRANCH_REPORTS:
        _deny_prepared_report()
    return prepared_report.enqueue_json_to_csv_conversion(prepared_report_name)


def _check_export(report_name):
    frappe.permissions.can_export(
        frappe.get_cached_value("Report", report_name, "ref_doctype"), raise_exception=True
    )


def restore_export_user(enqueued_user):
    """Frappe v16's background-job retry drops its user argument."""
    if not isinstance(enqueued_user, str) or not enqueued_user or enqueued_user in ("Administrator", "Guest"):
        raise frappe.PermissionError(_("Invalid report export user"))
    if frappe.session.user != enqueued_user:
        frappe.set_user(enqueued_user)
    if frappe.session.user != enqueued_user:
        raise frappe.PermissionError(_("Unable to restore report export user"))


@frappe.whitelist()
def export_query():
    policy = get_policy()
    request = frappe.local.form_dict
    _check_branch_report(request.get("report_name"), request.get("filters"), policy)
    if not policy.get("restricted"):
        return query_report.export_query()

    from frappe.desk.utils import pop_csv_params

    form_params = frappe._dict(frappe.local.form_dict)
    csv_params = pop_csv_params(form_params)
    clean_params(form_params)
    parse_json(form_params)
    _check_export(form_params.report_name)
    if int(form_params.export_in_background or 0):
        enqueued_user = frappe.session.user
        email = frappe.get_cached_value("User", enqueued_user, "email")
        frappe.enqueue(
            "awht_custom.visibility.report_filter.run_export_query_job",
            enqueued_user=enqueued_user, form_params=form_params, csv_params=csv_params,
            queue="long", now=frappe.flags.in_test,
        )
        frappe.msgprint(_(
            "Your report is being generated in the background. You will receive an email on {0} with a download link once it is ready."
        ).format(email))
        return
    return _export_query(form_params, csv_params)


def run_export_query_job(form_params, csv_params, enqueued_user):
    from frappe.desk.utils import send_report_email

    restore_export_user(enqueued_user)
    _check_export(form_params.report_name)
    email = frappe.get_cached_value("User", enqueued_user, "email")
    exported = _export_query(form_params, csv_params, populate_response=False)
    if exported:
        name, extension, content = exported
        send_report_email(email, name, extension, content, attached_to_name=form_params.report_name)


def _export_query(form_params, csv_params, populate_response=True):
    policy = get_policy()
    report_name = form_params.report_name
    report = _check_branch_report(report_name, form_params.filters, policy)
    custom_columns = None
    if _requested_prepared_name(form_params.filters):
        custom_columns = frappe.parse_json(form_params.custom_columns or "[]")
    requested_name = _preflight_prepared_report(
        report_name, form_params.filters, custom_columns=custom_columns, report=report
    )
    if not policy.get("restricted"):
        return query_report._export_query(form_params, csv_params, populate_response=populate_response)

    from frappe.desk.utils import provide_binary_file
    from frappe.utils.xlsxutils import handle_html, make_xlsx

    file_format = form_params.file_format_type
    if custom_columns is None:
        custom_columns = frappe.parse_json(form_params.custom_columns or "[]")
    data = query_report.run(report_name, form_params.filters, custom_columns=custom_columns,
                            are_default_filters=False)
    _check_prepared_result(data, report_name, requested_name)
    report = report or query_report.get_report_doc(report_name)
    data = frappe._dict(filter_result(data, policy, report_name, report.ref_doctype))
    data.report_name = report_name
    # Client-controlled filters and applied filters can contain cost values.
    data.filters = {}
    data.applied_filters = {}
    if not data.columns:
        frappe.respond_as_web_page(_("No data to export"), _("You can try changing the filters of your report."))
        return

    visible_idx = form_params.visible_idx or []
    if isinstance(visible_idx, str):
        visible_idx = json.loads(visible_idx)
    has_total = cint(data.get("add_total_row"))
    if (visible_idx and not sbool(form_params.get("ignore_visible_idx"))
            and len(visible_idx) < len(data.result) - (1 if has_total else 0)):
        visible_idx = set(visible_idx)
        rows = data.result[:-1] if has_total else data.result
        data.result = [row for index, row in enumerate(rows) if index in visible_idx]
        if has_total and data.result:
            data.result = query_report.add_total_row(data.result, data.columns)

    if _supports_parameter(query_report.format_fields, "file_format_type"):
        query_report.format_fields(data, file_format)
    else:
        # Frappe v15 always stringifies dates and has no Excel-specific branch.
        query_report.format_fields(data)
    supports_styles = _supports_parameter(query_report.build_xlsx_data, "build_styles")
    if supports_styles:
        table, widths, styles = query_report.build_xlsx_data(
            data, include_indentation=form_params.include_indentation, include_filters=False,
            include_hidden_columns=form_params.include_hidden_columns,
            build_styles=file_format == "Excel",
        )
    else:
        table, widths = query_report.build_xlsx_data(
            data, [], form_params.include_indentation, include_filters=False,
            include_hidden_columns=form_params.include_hidden_columns,
        )
        styles = None
    if file_format == "CSV":
        extension = "csv"
        content = _get_csv_bytes([[handle_html(v) if isinstance(v, str) else v for v in row]
                                  for row in table], csv_params)
    elif file_format == "Excel":
        extension = "xlsx"
        xlsx_kwargs = {"column_widths": widths}
        if supports_styles:
            xlsx_kwargs["styles"] = styles
        content = make_xlsx(table, report_name, **xlsx_kwargs).getvalue()
    else:
        frappe.throw(title=_("Unsupported file format: {0}").format(file_format),
                     msg=_("Only CSV and Excel formats are supported for export"))
    if not populate_response:
        return report_name, extension, content
    return provide_binary_file(_(report_name), extension, content)
