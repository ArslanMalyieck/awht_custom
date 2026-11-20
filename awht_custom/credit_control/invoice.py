"""Optional, explicit Sales Invoice branch warehouse mapping service.

This is not registered as a hook; the integrating component must opt in.
"""

import frappe
from frappe import _

from awht_custom.credit_control.mapping import get_company_mappings


def map_invoice_branch_warehouse(doc, method=None):
    if doc.get("is_return"):
        return
    branch = doc.get("custom_branch")
    if not branch:
        return
    if not doc.company:
        frappe.throw(_("Select a company before mapping an invoice branch."))

    warehouses = get_company_mappings(doc.company).get(branch, [])
    if len(warehouses) != 1:
        frappe.throw(_("Invoice branch {0} must have exactly one mapped warehouse in its company.").format(branch))
    warehouse = warehouses[0]
    if doc.get("set_warehouse") and doc.set_warehouse != warehouse:
        frappe.throw(_("Invoice Set Warehouse conflicts with the selected branch warehouse."))
    if any(row.get("warehouse") and row.warehouse != warehouse for row in doc.items):
        frappe.throw(_("An invoice item warehouse conflicts with the selected branch warehouse."))

    # Check every row before assigning anything, so conflicts never leave a partial mapping.
    if not doc.get("set_warehouse"):
        doc.set_warehouse = warehouse
    for row in doc.items:
        if not row.get("warehouse"):
            row.warehouse = warehouse
