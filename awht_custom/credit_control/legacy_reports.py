"""Company, branch and warehouse scope for the ported Branch Credit Control reports.

The original reports inferred scope from names and unrestricted SQL. These
helpers keep their branch/warehouse convention while making company, report
role, branch and warehouse boundaries explicit and server-enforced.
"""

import frappe
from frappe import _
from frappe.permissions import get_allowed_docs_for_doctype, get_user_permissions
from frappe.utils import getdate

from awht_custom.credit_control.reporting import assert_credit_report_access


BRANCH_REPORT = "Branch Financial Exposure"
UNSOLD_REPORT = "Unsold Items"


def resolve_company(filters, report_name):
    filters = filters or {}
    company = filters.get("company")
    if not company:
        company = frappe.defaults.get_user_default("Company")
    if not company:
        company = frappe.db.get_single_value("Global Defaults", "default_company")
    if not company:
        companies = frappe.get_all("Company", pluck="name", limit=2)
        if len(companies) == 1:
            company = companies[0]
    if not company:
        frappe.throw(_("Company is required."))

    require_report_access(report_name)
    if not frappe.db.exists("Company", company):
        frappe.throw(_("Company {0} does not exist.").format(company), frappe.DoesNotExistError)
    if not frappe.has_permission("Company", "read", company, ignore_share_permissions=True):
        frappe.throw(_("Not permitted for this company."), frappe.PermissionError)
    return company


def require_report_access(report_name):
    """Enforce the Report record roles and the referenced doctype report right.

    ``query_report.run`` normally performs these checks, but report modules can
    also be executed directly, so repeat them before reading any amounts.
    """
    assert_credit_report_access(report_name)
    if not frappe.db.exists("Report", report_name):
        frappe.throw(_("Report {0} is not installed.").format(report_name), frappe.DoesNotExistError)
    report = frappe.get_doc("Report", report_name)
    allowed_roles = {row.role for row in report.roles}
    if allowed_roles and not allowed_roles.intersection(frappe.get_roles()):
        frappe.throw(_("Not permitted to run this report."), frappe.PermissionError)
    if not frappe.has_permission(report.ref_doctype, "report"):
        frappe.throw(
            _("Not permitted to report on {0}.").format(report.ref_doctype),
            frappe.PermissionError,
        )


def validate_period(filters, report_name):
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("From Date and To Date are required."))
    from_date, to_date = getdate(filters["from_date"]), getdate(filters["to_date"])
    if from_date > to_date:
        frappe.throw(_("From Date cannot be after To Date."))
    return from_date, to_date


def get_company_currency(company):
    currency = frappe.db.get_value("Company", company, "default_currency")
    if not currency:
        frappe.throw(_("Company has no default currency."))
    return currency


def _user_permission_docs(doctype):
    if frappe.session.user == "Administrator":
        return None
    entries = get_user_permissions(frappe.session.user).get(doctype, [])
    if not entries:
        return None
    return set(get_allowed_docs_for_doctype(entries, doctype))


def _company_branches(company):
    if frappe.get_meta("Branch").has_field("custom_company"):
        return frappe.get_all(
            "Branch",
            filters={"custom_company": company},
            pluck="name",
            limit_page_length=0,
        )
    return frappe.get_all("Branch", pluck="name", limit_page_length=0)


def get_report_branches(company, branch=None):
    """Return only branches attributed to the company and permitted to the user."""
    branches = _company_branches(company)
    permitted = _user_permission_docs("Branch")
    if permitted is not None:
        branches = [name for name in branches if name in permitted]
    if branch:
        if branch not in branches:
            frappe.throw(_("Not permitted for this branch."), frappe.PermissionError)
        return [branch]
    return branches


def get_report_warehouses(company, warehouse=None):
    """Company warehouses, narrowed by Warehouse User Permissions when present."""
    warehouses = frappe.get_all(
        "Warehouse",
        filters={"company": company, "is_group": 0},
        pluck="name",
        limit_page_length=0,
    )
    permitted = _user_permission_docs("Warehouse")
    if permitted is not None:
        warehouses = [name for name in warehouses if name in permitted]
        if not warehouses:
            frappe.throw(_("No permitted warehouses for this company."), frappe.PermissionError)
    if warehouse:
        if warehouse not in warehouses:
            frappe.throw(_("Not permitted for this warehouse."), frappe.PermissionError)
        return (warehouse,)
    return tuple(warehouses)


def get_branch_warehouses(company, branch):
    """Preserve the original branch-name warehouse prefix, scoped to company."""
    warehouses = frappe.get_all(
        "Warehouse",
        filters={"company": company, "is_group": 0},
        pluck="name",
        limit_page_length=0,
    )
    warehouses = [name for name in warehouses if name.startswith(branch)]
    permitted = _user_permission_docs("Warehouse")
    if permitted is not None:
        warehouses = [name for name in warehouses if name in permitted]
    return tuple(warehouses)
