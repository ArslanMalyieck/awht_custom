"""Current SAR exposure, not historical sales, cash collection or gross profit."""

import frappe
from frappe import _

from awht_custom.credit_control.exposure import (
    amount,
    ensure_no_pending_pos,
    get_stock_values,
    get_unpaid_invoices,
    require_credit_fields,
    require_sar_company,
)
from awht_custom.credit_control.reporting import get_report_scope


def execute(filters=None):
    company, visible, mappings = get_report_scope(filters or {}, report_name="AWHT Current Credit Exposure")
    require_credit_fields()
    require_sar_company(company)
    ensure_no_pending_pos(company)
    stock = get_stock_values(company, visible)
    unpaid = get_unpaid_invoices(company, mappings, visible)

    data = []
    for branch in sorted(visible):
        raw_limit = frappe.db.get_value("Branch", branch, "custom_credit_limit")
        limit = amount(raw_limit) if raw_limit is not None else None
        exposure = stock[branch] + unpaid[branch]
        data.append(
            {
                "branch": branch,
                "currency": "SAR",
                "stock_value": float(stock[branch]),
                "unpaid_invoices": float(unpaid[branch]),
                "total_exposure": float(exposure),
                "credit_limit": float(limit) if limit is not None else None,
                "available_credit": float(limit - exposure) if limit is not None and limit > 0 else None,
            }
        )

    return [
        {"label": _("Branch"), "fieldname": "branch", "fieldtype": "Data", "width": 180},
        {"label": _("Stock Value (Current)"), "fieldname": "stock_value", "fieldtype": "Currency", "options": "currency", "width": 170},
        {"label": _("Unpaid Invoices (SAR)"), "fieldname": "unpaid_invoices", "fieldtype": "Currency", "options": "currency", "width": 180},
        {"label": _("Total Exposure (Current)"), "fieldname": "total_exposure", "fieldtype": "Currency", "options": "currency", "width": 185},
        {"label": _("Credit Limit (SAR)"), "fieldname": "credit_limit", "fieldtype": "Currency", "options": "currency", "width": 165},
        {"label": _("Available Credit (SAR)"), "fieldname": "available_credit", "fieldtype": "Currency", "options": "currency", "width": 185},
    ], data
