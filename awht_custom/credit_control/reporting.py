"""Server-side report scope; raw SQL must never determine a user's scope."""

import frappe
from frappe import _
from frappe.permissions import get_allowed_docs_for_doctype, get_user_permissions

from awht_custom.credit_control.mapping import get_company_mappings


def assert_credit_report_access(report_name):
    """Call before core report.run too: a prepared result bypasses execute()."""
    if report_name not in (
        "Branch Financial Exposure", "AWHT Current Credit Exposure",
        "Unsold Items", "AWHT No Stock Out Items",
    ):
        return
    if report_name in ("Branch Financial Exposure", "AWHT Current Credit Exposure"):
        from awht_custom.visibility.api import get_policy

        if get_policy().get("restricted"):
            frappe.throw(_("This report is unavailable."), exc=frappe.PermissionError)
    if frappe.db.get_value("Report", report_name, "prepared_report") or frappe.db.exists(
        "Prepared Report",
        {"report_name": report_name, "status": ("in", ("Queued", "Started", "Completed"))},
    ):
        frappe.throw(_("Prepared data is unavailable for this report."), exc=frappe.PermissionError)


def get_credit_scope(company, branch=None, *, report_name=None):
    """Authorize company and full branch mappings for reports or MR preview."""
    if not company:
        frappe.throw(_("Company is required."))

    user = frappe.session.user
    if user != "Administrator" and not {"Accounts Manager", "System Manager"}.intersection(
        frappe.get_roles(user)
    ):
        frappe.throw(_("Not permitted to run this report."), exc=frappe.PermissionError)
    if not frappe.has_permission("Company", "read", company, ignore_share_permissions=True):
        frappe.throw(_("Not permitted for this company."), exc=frappe.PermissionError)

    if report_name:
        assert_credit_report_access(report_name)
    mappings = get_company_mappings(company)
    if user == "Administrator":
        allowed_branches = set(mappings)
        allowed_warehouses = None
    else:
        permissions = get_user_permissions(user)
        # An explicit, globally applicable Branch User Permission is mandatory.
        allowed_branches = set(get_allowed_docs_for_doctype(permissions.get("Branch", []), "Branch"))
        allowed_warehouses = (
            {entry.get("doc") for entry in permissions["Warehouse"]}
            if "Warehouse" in permissions
            else None
        )

    visible = {
        branch: warehouses
        for branch, warehouses in mappings.items()
        if branch in allowed_branches
        and (allowed_warehouses is None or set(warehouses).issubset(allowed_warehouses))
    }
    if branch:
        if branch not in visible:
            frappe.throw(_("Not permitted for this branch."), exc=frappe.PermissionError)
        return company, {branch: visible[branch]}, mappings
    if not visible:
        frappe.throw(_("No permitted, fully mapped branch warehouses."), exc=frappe.PermissionError)
    return company, visible, mappings


def get_report_scope(filters, *, report_name):
    return get_credit_scope(filters.get("company"), filters.get("branch"), report_name=report_name)
