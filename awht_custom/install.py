"""Idempotent prerequisites for AWHT features, without importing site data."""

import os

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


PORTED_REPORTS = {
    "branch_financial_exposure": "Branch Financial Exposure",
    "unsold_items": "Unsold Items",
}


CUSTOM_FIELDS = {
    "Stock Entry Detail": [
        {
            "fieldname": "salesman_price", "label": "Salesman Price",
            "fieldtype": "Currency", "options": "Company:company:default_currency",
            "insert_after": "basic_rate", "non_negative": 1, "in_list_view": 1,
            "no_copy": 1, "print_hide": 1,
        },
        {
            "fieldname": "old_price", "label": "Old Price",
            "fieldtype": "Currency", "options": "Company:company:default_currency",
            "insert_after": "salesman_price", "non_negative": 1,
            "in_list_view": 1, "columns": 1, "no_copy": 1,
        },
    ],
    "Material Request Item": [
        {
            "fieldname": "salesman_price", "label": "Salesman Price",
            "fieldtype": "Currency", "options": "Company:company:default_currency",
            "insert_after": "rate", "non_negative": 1, "in_list_view": 1,
            "columns": 1, "no_copy": 1,
        },
        {
            "fieldname": "old_price", "label": "Old Price",
            "fieldtype": "Currency", "options": "Company:company:default_currency",
            "insert_after": "salesman_price", "non_negative": 1,
            "in_list_view": 1, "columns": 1, "no_copy": 1,
        },
    ],
    "Sales Invoice Item": [
        {
            "fieldname": "salesman_price", "label": "Salesman Price",
            "fieldtype": "Currency", "options": "Company:company:default_currency",
            "insert_after": "rate", "read_only": 1, "non_negative": 1,
            "in_list_view": 1, "columns": 1, "no_copy": 1,
        },
        {
            "fieldname": "item_tax_amount", "label": "Item Tax Amount",
            "fieldtype": "Currency", "options": "currency",
            "insert_after": "net_amount", "read_only": 1, "no_copy": 1,
            "in_list_view": 1, "columns": 1, "print_hide": 1,
        },
        {
            "fieldname": "amount_after_tax", "label": "Amount After Tax",
            "fieldtype": "Currency", "options": "currency",
            "insert_after": "item_tax_amount", "read_only": 1, "no_copy": 1,
            "in_list_view": 1, "columns": 1, "print_hide": 1,
        },
    ],
    "Quotation Item": [
        {
            "fieldname": "salesman_price", "label": "Salesman Price",
            "fieldtype": "Currency", "options": "Company:company:default_currency",
            "insert_after": "rate", "read_only": 1, "non_negative": 1,
            "in_list_view": 1, "columns": 1, "no_copy": 1,
        },
    ],
    "Sales Order Item": [
        {
            "fieldname": "salesman_price", "label": "Salesman Price",
            "fieldtype": "Currency", "options": "Company:company:default_currency",
            "insert_after": "rate", "read_only": 1, "non_negative": 1,
            "in_list_view": 1, "columns": 1, "no_copy": 1,
        },
    ],
    "Branch": [
        {
            "fieldname": "custom_credit_limit", "label": "Credit Limit (SAR)",
            "fieldtype": "Currency", "options": "SAR", "default": "0",
            "non_negative": 1, "insert_after": "branch",
        },
    ],
    "Sales Invoice": [
        {
            "fieldname": "custom_branch", "label": "Branch",
            "fieldtype": "Link", "options": "Branch", "insert_after": "company",
        },
    ],
}


SALESMAN_PRICE_DOCTYPES = ("Sales Invoice Item", "Quotation Item", "Sales Order Item")
OLD_PRICE_DOCTYPES = ("Material Request Item", "Stock Entry Detail")
PRICE_COLUMNS = (
    *((doctype, "salesman_price") for doctype in SALESMAN_PRICE_DOCTYPES),
    *((doctype, "old_price") for doctype in OLD_PRICE_DOCTYPES),
)


def _normalize_price_columns():
    """Allow existing varchar/blank price columns to be adopted as Currency fields."""
    has_column = getattr(frappe.db, "has_column", None)
    if not has_column:
        return
    for doctype, fieldname in PRICE_COLUMNS:
        if not has_column(doctype, fieldname):
            continue
        if frappe.get_meta(doctype).get_field(fieldname):
            continue
        column = frappe.db.sql(
            f"show columns from `tab{doctype}` like '{fieldname}'", as_dict=True
        )
        if not column:
            continue
        column_type = (column[0].get("Type") or "").lower()
        if "char" in column_type or "text" in column_type:
            condition = (
                f"{fieldname} is null or trim({fieldname}) = '' "
                f"or {fieldname} not regexp '^-?[0-9]+([.][0-9]+)?$'"
            )
        else:
            condition = f"{fieldname} is null"
        frappe.db.sql(f"update `tab{doctype}` set {fieldname} = 0 where {condition}")


def setup():
    _normalize_price_columns()
    missing = {}
    for doctype, fields in CUSTOM_FIELDS.items():
        meta = frappe.get_meta(doctype)
        for field in fields:
            existing = meta.get_field(field["fieldname"])
            if existing:
                if existing.fieldtype != field["fieldtype"] or (
                    field["fieldtype"] == "Link" and existing.options != field["options"]
                ):
                    frappe.throw(
                        f"Existing {doctype}.{field['fieldname']} is incompatible with AWHT Custom. "
                        "Review the original field and its data before migration."
                    )
                continue
            missing.setdefault(doctype, []).append({**field, "module": "AWHT Custom"})
    if missing:
        create_custom_fields(missing, update=False)

    if not frappe.db.exists("Role", "Previous Transfer Prices Viewer"):
        frappe.get_doc({
            "doctype": "Role", "role_name": "Previous Transfer Prices Viewer",
            "desk_access": 1, "is_custom": 1,
        }).insert(ignore_permissions=True)


def sync_ported_reports():
    """Keep the original report names owned by AWHT Custom after app sync."""
    from frappe.modules.import_file import import_file_by_path

    changed = False
    for folder, report_name in PORTED_REPORTS.items():
        path = frappe.get_app_path(
            "awht_custom", "awht_custom", "report", folder, f"{folder}.json"
        )
        if not os.path.exists(path):
            continue
        if not frappe.db.exists("Report", report_name) or frappe.db.get_value(
            "Report", report_name, "module"
        ) != "AWHT Custom":
            import_file_by_path(path, force=True, ignore_version=True)
            changed = True
    if changed:
        frappe.clear_cache(doctype="Report")
