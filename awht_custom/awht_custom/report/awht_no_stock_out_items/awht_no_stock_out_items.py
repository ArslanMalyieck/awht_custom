"""Current stock with no outbound Stock Ledger Entry in the selected period.

This is deliberately narrower than 'not invoiced': transfers, issues and
manufacture count as stock out. Sales Orders and unfulfilled invoices do not.
Unconsolidated POS is rejected because its stock movements may not be posted.
"""

import frappe
from frappe import _
from frappe.utils import getdate

from awht_custom.credit_control.exposure import amount, ensure_no_pending_pos
from awht_custom.credit_control.reporting import get_report_scope


def execute(filters=None):
    filters = filters or {}
    company, visible, _mappings = get_report_scope(filters, report_name="AWHT No Stock Out Items")
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("From Date and To Date are required."))
    from_date, to_date = getdate(filters["from_date"]), getdate(filters["to_date"])
    if from_date > to_date:
        frappe.throw(_("From Date cannot be after To Date."))
    currency = frappe.db.get_value("Company", company, "default_currency")
    if not currency:
        frappe.throw(_("Company has no default currency."))
    ensure_no_pending_pos(company)

    warehouses = tuple(warehouse for names in visible.values() for warehouse in names)
    params = {"company": company, "warehouses": warehouses, "from_date": from_date, "to_date": to_date}
    item_conditions = ""
    if filters.get("item_group"):
        groups = frappe.db.get_descendants("Item Group", filters["item_group"]) or []
        params["item_groups"] = tuple(set(groups) | {filters["item_group"]})
        item_conditions += " AND i.item_group IN %(item_groups)s"
    if filters.get("brand"):
        params["brand"] = filters["brand"]
        item_conditions += " AND i.brand = %(brand)s"
    if filters.get("item_code"):
        params["item_code"] = filters["item_code"]
        item_conditions += " AND i.name = %(item_code)s"

    rows = frappe.db.sql(
        """
        SELECT i.name AS item_code, i.item_name, i.item_group, i.brand, i.stock_uom,
               SUM(b.actual_qty) AS stock_qty, SUM(b.stock_value) AS stock_value,
               MIN(b.actual_qty) AS min_qty, MIN(b.stock_value) AS min_stock_value,
               (
                   SELECT MAX(prior.posting_date) FROM `tabStock Ledger Entry` prior
                   WHERE prior.company = %(company)s AND prior.warehouse IN %(warehouses)s
                     AND prior.item_code = i.name AND prior.is_cancelled = 0
                     AND prior.actual_qty < 0 AND prior.posting_date < %(from_date)s
               ) AS last_stock_out_before_period
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse AND w.company = %(company)s
        INNER JOIN `tabItem` i ON i.name = b.item_code
        WHERE b.warehouse IN %(warehouses)s AND w.is_group = 0 AND w.disabled = 0
          AND i.disabled = 0 AND i.is_stock_item = 1 AND i.has_variants = 0
          AND NOT EXISTS (
              SELECT 1 FROM `tabStock Ledger Entry` outgoing
              WHERE outgoing.company = %(company)s AND outgoing.warehouse IN %(warehouses)s
                AND outgoing.item_code = b.item_code AND outgoing.is_cancelled = 0
                AND outgoing.actual_qty < 0
                AND outgoing.posting_date BETWEEN %(from_date)s AND %(to_date)s
          )
        """ + item_conditions + """
        GROUP BY i.name, i.item_name, i.item_group, i.brand, i.stock_uom
        HAVING SUM(b.actual_qty) > 0
        ORDER BY stock_value DESC, i.name
        """,
        params,
        as_dict=True,
    )

    data = []
    for row in rows:
        if amount(row.min_qty) < 0 or amount(row.min_stock_value) < 0:
            frappe.throw(_("Negative Bin balances in the selected warehouses; report unavailable."))
        last = row.last_stock_out_before_period
        data.append(
            {
                "item_code": row.item_code,
                "item_name": row.item_name,
                "item_group": row.item_group,
                "brand": row.brand,
                "stock_uom": row.stock_uom,
                "stock_qty": row.stock_qty,
                "stock_value": row.stock_value,
                "currency": currency,
                "last_stock_out_before_period": last,
                "days_since_last": (to_date - getdate(last)).days if last else None,
            }
        )

    columns = [
        {"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Data", "width": 150},
        {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
        {"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Data", "width": 140},
        {"label": _("Brand"), "fieldname": "brand", "fieldtype": "Data", "width": 110},
        {"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Data", "width": 75},
        {"label": _("Current Stock Qty"), "fieldname": "stock_qty", "fieldtype": "Float", "width": 135},
        {"label": _("Current Stock Value"), "fieldname": "stock_value", "fieldtype": "Currency", "options": "currency", "width": 150},
        {"label": _("Last Stock Out Before Period"), "fieldname": "last_stock_out_before_period", "fieldtype": "Date", "width": 190},
        {"label": _("Days to Period End"), "fieldname": "days_since_last", "fieldtype": "Int", "width": 135},
    ]
    summary = [
        {"value": len(data), "label": _("Items With No Stock Out"), "datatype": "Int"},
        {
            "value": float(sum(amount(row["stock_value"]) for row in data)),
            "label": _("Current Stock Value"),
            "datatype": "Currency",
            "currency": currency,
        },
    ]
    return columns, data, None, None, summary
