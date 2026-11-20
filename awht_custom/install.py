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
    ],
    "Sales Invoice Item": [
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


def setup():
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
