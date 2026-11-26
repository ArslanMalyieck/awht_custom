"""Current company-currency stock and positive SAR invoice receivables.

Only submitted, positively outstanding Sales Invoices are counted. Negative
returns/credits cannot cancel a different branch's debt. Retention or advances
not in Sales Invoice.outstanding_amount are outside this measure. Pending POS
invoices have no reliable branch receivable yet and block a credit check.
"""

from decimal import Decimal, InvalidOperation

import frappe
from frappe import _


def amount(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw(_("A credit-control amount is missing or invalid."))
    if not result.is_finite():
        frappe.throw(_("A credit-control amount is missing or invalid."))
    return result


def require_credit_fields():
    if not frappe.get_meta("Branch").has_field("custom_credit_limit") or not frappe.get_meta(
        "Sales Invoice"
    ).has_field("custom_branch"):
        frappe.throw(_("Install Branch.custom_credit_limit and Sales Invoice.custom_branch first."))


def require_sar_company(company):
    if frappe.db.get_value("Company", company, "default_currency") != "SAR":
        frappe.throw(_("Branch credit exposure requires company {0} to use SAR.").format(company))


def ensure_no_pending_pos(company, *, for_update=False):
    pending = frappe.db.sql(
        """
        SELECT 1 FROM `tabPOS Invoice`
        WHERE company = %(company)s AND docstatus = 1
          AND COALESCE(consolidated_invoice, '') = ''
        LIMIT 1
        """ + (" FOR UPDATE" if for_update else ""),
        {"company": company},
    )
    if pending:
        frappe.throw(_("Consolidate pending POS Invoices before calculating branch exposure."))


def get_stock_values(company, mappings, *, for_update=False, strict=True):
    warehouses = {warehouse: branch for branch, names in mappings.items() for warehouse in names}
    totals = {branch: Decimal(0) for branch in mappings}
    if not warehouses:
        return totals

    rows = frappe.db.sql(
        """
        SELECT b.warehouse, b.stock_value
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
        WHERE w.company = %(company)s AND b.warehouse IN %(warehouses)s
        """ + (" FOR UPDATE" if for_update else ""),
        {"company": company, "warehouses": tuple(warehouses)},
        as_dict=True,
    )
    warehouse_totals = {warehouse: Decimal(0) for warehouse in warehouses}
    for row in rows:
        warehouse_totals[row.warehouse] += amount(row.stock_value)

    for warehouse, value in warehouse_totals.items():
        if value < 0:
            if strict:
                frappe.throw(_("Negative stock value in a mapped warehouse; exposure is unavailable."))
            value = Decimal(0)
        totals[warehouses[warehouse]] += value
    return totals


def get_unpaid_invoices(company, mappings, branches, *, for_update=False):
    """Require attribution globally, but read amounts only for requested branches."""
    if frappe.db.sql(
        """
        SELECT 1 FROM `tabSales Invoice` si
        WHERE si.company = %(company)s AND si.docstatus = 1 AND si.outstanding_amount > 0
          AND NOT EXISTS (
              SELECT 1 FROM `tabAWHT Branch Warehouse` m
              WHERE m.company = si.company AND m.branch = si.custom_branch
          )
        LIMIT 1
        """ + (" FOR UPDATE" if for_update else ""),
        {"company": company},
    ):
        frappe.throw(_("Unpaid Sales Invoices must have a mapped custom_branch in their company."))

    totals = {branch: Decimal(0) for branch in branches}
    if not totals:
        return totals
    rows = frappe.db.sql(
        """
        SELECT si.custom_branch, si.outstanding_amount, si.party_account_currency, si.is_return
        FROM `tabSales Invoice` si
        WHERE si.company = %(company)s AND si.docstatus = 1 AND si.outstanding_amount > 0
          AND si.custom_branch IN %(branches)s
        """ + (" FOR UPDATE" if for_update else ""),
        {"company": company, "branches": tuple(sorted(totals))},
        as_dict=True,
    )
    for row in rows:
        if row.custom_branch not in mappings or row.party_account_currency != "SAR" or row.is_return:
            frappe.throw(_("Unpaid Sales Invoice has an unsupported branch, currency or return."))
        totals[row.custom_branch] += amount(row.outstanding_amount)
    return totals
