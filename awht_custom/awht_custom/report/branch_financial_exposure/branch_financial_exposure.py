import frappe
from frappe import _

from awht_custom.credit_control.legacy_reports import (
    BRANCH_REPORT,
    get_branch_warehouses,
    get_company_currency,
    get_report_branches,
    resolve_company,
    validate_period,
)


def execute(filters=None):
    filters = frappe._dict(filters or {})
    company = resolve_company(filters, BRANCH_REPORT)
    from_date, to_date = validate_period(filters, BRANCH_REPORT)
    branches = get_report_branches(company, filters.get("branch"))
    currency = get_company_currency(company)
    columns = get_columns(currency)
    data = get_data(company, branches, from_date, to_date)
    return columns, data


def get_columns(currency):
    return [
        {"label": _("Branch"), "fieldname": "branch", "fieldtype": "Data", "width": 200},
        {
            "label": _("Stock Value (Current)"),
            "fieldname": "stock_value",
            "fieldtype": "Currency",
            "options": currency,
            "width": 180,
        },
        {
            "label": _("Total Sales"),
            "fieldname": "total_sales",
            "fieldtype": "Currency",
            "options": currency,
            "width": 180,
        },
        {
            "label": _("Total Payments"),
            "fieldname": "total_payments",
            "fieldtype": "Currency",
            "options": currency,
            "width": 180,
        },
        {
            "label": _("Gross Profit"),
            "fieldname": "gross_profit",
            "fieldtype": "Currency",
            "options": currency,
            "width": 180,
        },
        {
            "label": _("Gross Profit (%)"),
            "fieldname": "gross_profit_percent",
            "fieldtype": "Percent",
            "width": 120,
        },
    ]


def get_data(company, branches, from_date, to_date):
    data = []
    for branch in branches:
        stock_value = get_stock_value(company, get_branch_warehouses(company, branch))
        total_sales = get_total_sales(company, branch, from_date, to_date)
        net_total_sales = get_net_total_sales(company, branch, from_date, to_date)
        total_payments = get_total_payments(company, branch, from_date, to_date)
        total_cogs = get_total_cogs(company, branch, from_date, to_date)
        gross_profit = net_total_sales - total_cogs
        gross_profit_percent = (gross_profit / net_total_sales * 100) if net_total_sales else 0

        data.append(
            {
                "branch": branch,
                "stock_value": stock_value,
                "total_sales": total_sales,
                "total_payments": total_payments,
                "gross_profit": gross_profit,
                "gross_profit_percent": gross_profit_percent,
            }
        )
    return data


def get_stock_value(company, warehouses):
    if not warehouses:
        return 0
    result = frappe.db.sql(
        """
        SELECT SUM(b.actual_qty * b.valuation_rate) AS stock_value
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
        WHERE b.warehouse IN %(warehouses)s
          AND w.company = %(company)s
        """,
        {"company": company, "warehouses": tuple(warehouses)},
        as_dict=1,
    )
    return result[0]["stock_value"] or 0


def get_total_sales(company, branch, from_date, to_date):
    result = frappe.db.sql(
        """
        SELECT SUM(base_grand_total) AS total_sales
        FROM `tabSales Invoice`
        WHERE company = %(company)s
          AND custom_branch = %(branch)s
          AND posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND docstatus = 1
        """,
        {
            "company": company,
            "branch": branch,
            "from_date": from_date,
            "to_date": to_date,
        },
        as_dict=1,
    )
    return result[0]["total_sales"] or 0


def get_net_total_sales(company, branch, from_date, to_date):
    result = frappe.db.sql(
        """
        SELECT SUM(base_net_total) AS total_sales
        FROM `tabSales Invoice`
        WHERE company = %(company)s
          AND custom_branch = %(branch)s
          AND posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND docstatus = 1
        """,
        {
            "company": company,
            "branch": branch,
            "from_date": from_date,
            "to_date": to_date,
        },
        as_dict=1,
    )
    return result[0]["total_sales"] or 0


def get_total_payments(company, branch, from_date, to_date):
    result = frappe.db.sql(
        """
        SELECT SUM(base_paid_amount) AS total_payments
        FROM `tabPayment Entry`
        WHERE company = %(company)s
          AND custom_branch = %(branch)s
          AND posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND docstatus = 1
        """,
        {
            "company": company,
            "branch": branch,
            "from_date": from_date,
            "to_date": to_date,
        },
        as_dict=1,
    )
    return result[0]["total_payments"] or 0


def get_total_cogs(company, branch, from_date, to_date):
    result = frappe.db.sql(
        """
        SELECT SUM(ABS(sle.actual_qty * sle.valuation_rate)) AS total_cogs
        FROM `tabStock Ledger Entry` sle
        INNER JOIN `tabSales Invoice` si
            ON sle.voucher_no = si.name
            AND sle.voucher_type = 'Sales Invoice'
            AND si.docstatus = 1
            AND si.company = %(company)s
            AND si.custom_branch = %(branch)s
        WHERE sle.company = %(company)s
          AND sle.posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND sle.is_cancelled = 0
        """,
        {
            "company": company,
            "branch": branch,
            "from_date": from_date,
            "to_date": to_date,
        },
        as_dict=1,
    )
    return result[0]["total_cogs"] or 0
