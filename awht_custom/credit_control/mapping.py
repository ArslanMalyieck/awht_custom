"""Explicit, company-scoped Branch -> Warehouse assignments."""

import frappe
from frappe import _


def validate_warehouse(company, warehouse, *, for_update=False):
    details = frappe.db.get_value(
        "Warehouse",
        warehouse,
        ["company", "is_group", "disabled"],
        as_dict=True,
        for_update=for_update,
    )
    if not details or details.company != company or details.is_group or details.disabled:
        frappe.throw(_("Warehouse must be an active, non-group warehouse of company {0}.").format(company))


def validate_branch_company(company, branch, *, for_update=False):
    if frappe.get_meta("Branch").has_field("custom_company") and frappe.db.get_value(
        "Branch", branch, "custom_company", for_update=for_update
    ) != company:
        frappe.throw(_("Branch company does not match its warehouse mapping."))


def get_company_mappings(company, *, for_update=False):
    """Return {branch: [warehouses]}; never infer a branch from a warehouse name."""
    rows = frappe.db.sql(
        """
        SELECT m.branch, m.warehouse, w.company AS warehouse_company, w.is_group, w.disabled
        FROM `tabAWHT Branch Warehouse` m
        LEFT JOIN `tabWarehouse` w ON w.name = m.warehouse
        WHERE m.company = %(company)s
        ORDER BY m.name
        """ + (" FOR UPDATE" if for_update else ""),
        {"company": company},
        as_dict=True,
    )
    mappings = {}
    seen = set()
    for row in rows:
        if (
            not row.branch
            or not row.warehouse
            or row.warehouse in seen
            or row.warehouse_company != company
            or row.is_group
            or row.disabled
        ):
            frappe.throw(_("Invalid AWHT Branch Warehouse mapping for company {0}.").format(company))
        validate_branch_company(company, row.branch, for_update=for_update)
        seen.add(row.warehouse)
        mappings.setdefault(row.branch, []).append(row.warehouse)
    return mappings
