import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from awht_custom.credit_control.legacy_reports import (
    UNSOLD_REPORT,
    get_company_currency,
    get_report_warehouses,
    resolve_company,
)

STOCK_OUT = "Stock Out (All Vouchers)"
ALL_SOURCES = [
    STOCK_OUT,
    "Sales Invoice",
    "POS Invoice",
    "Sales Order",
    "Delivery Note",
]


def execute(filters=None):
    filters = frappe._dict(filters or {})
    company = resolve_company(filters, UNSOLD_REPORT)
    validate_filters(filters)

    sources = get_sources(filters)
    warehouses = get_report_warehouses(company, filters.get("warehouse"))
    currency = get_company_currency(company)

    columns = get_columns(currency)
    data = get_data(filters, sources, company, warehouses)
    report_summary = get_report_summary(data, currency)

    return columns, data, None, None, report_summary


def get_sources(filters):
    sources = filters.get("based_on") or [STOCK_OUT]
    if isinstance(sources, str):
        try:
            sources = frappe.parse_json(sources)
        except Exception:
            sources = [sources]
    if isinstance(sources, str):
        sources = [sources]

    sources = [source for source in sources if source in ALL_SOURCES]
    return sources or [STOCK_OUT]


def validate_filters(filters):
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("From Date and To Date are mandatory"))

    if getdate(filters.from_date) > getdate(filters.to_date):
        frappe.throw(_("From Date cannot be greater than To Date"))


def get_columns(currency):
    return [
        {
            "label": _("Item Code"),
            "fieldname": "item_code",
            "fieldtype": "Link",
            "options": "Item",
            "width": 160,
        },
        {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
        {
            "label": _("Item Group"),
            "fieldname": "item_group",
            "fieldtype": "Link",
            "options": "Item Group",
            "width": 140,
        },
        {
            "label": _("Brand"),
            "fieldname": "brand",
            "fieldtype": "Link",
            "options": "Brand",
            "width": 110,
        },
        {
            "label": _("UOM"),
            "fieldname": "stock_uom",
            "fieldtype": "Link",
            "options": "UOM",
            "width": 70,
        },
        {"label": _("Stock Qty"), "fieldname": "stock_qty", "fieldtype": "Float", "width": 100},
        {
            "label": _("Stock Value"),
            "fieldname": "stock_value",
            "fieldtype": "Currency",
            "options": currency,
            "width": 120,
        },
        {
            "label": _("Last Movement Before Period"),
            "fieldname": "last_movement_date",
            "fieldtype": "Date",
            "width": 160,
        },
        {
            "label": _("Days Since Last Movement"),
            "fieldname": "days_since_last",
            "fieldtype": "Int",
            "width": 150,
        },
    ]


def get_source_query(source, filters, company, warehouses):
    warehouse_condition = (
        " AND {alias}.warehouse IN %(warehouses)s" if warehouses else " AND 1 = 0"
    )

    if source == "Sales Invoice":
        query = """
            SELECT sii.item_code AS item_code, si.posting_date AS dt
            FROM `tabSales Invoice Item` sii
            INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
            WHERE si.docstatus = 1
              AND si.is_return = 0
              AND sii.item_code IS NOT NULL
              AND si.company = %(company)s
        """ + warehouse_condition.format(alias="sii")

    elif source == "POS Invoice":
        query = """
            SELECT pii.item_code AS item_code, pi.posting_date AS dt
            FROM `tabPOS Invoice Item` pii
            INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
            WHERE pi.docstatus = 1
              AND pi.is_return = 0
              AND pii.item_code IS NOT NULL
              AND pi.company = %(company)s
        """ + warehouse_condition.format(alias="pii")

    elif source == "Sales Order":
        query = """
            SELECT soi.item_code AS item_code, so.transaction_date AS dt
            FROM `tabSales Order Item` soi
            INNER JOIN `tabSales Order` so ON so.name = soi.parent
            WHERE so.docstatus = 1
              AND soi.item_code IS NOT NULL
              AND so.company = %(company)s
        """ + warehouse_condition.format(alias="soi")

    elif source == "Delivery Note":
        query = """
            SELECT dni.item_code AS item_code, dn.posting_date AS dt
            FROM `tabDelivery Note Item` dni
            INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
            WHERE dn.docstatus = 1
              AND dn.is_return = 0
              AND dni.item_code IS NOT NULL
              AND dn.company = %(company)s
        """ + warehouse_condition.format(alias="dni")

    else:
        query = """
            SELECT sle.item_code AS item_code, sle.posting_date AS dt
            FROM `tabStock Ledger Entry` sle
            WHERE sle.is_cancelled = 0
              AND sle.actual_qty < 0
              AND sle.company = %(company)s
        """ + warehouse_condition.format(alias="sle")
        if cint(filters.get("exclude_adjustments")):
            query += """
                AND sle.voucher_type != 'Stock Reconciliation'
                AND NOT (
                    sle.voucher_type = 'Stock Entry'
                    AND sle.voucher_no IN (
                        SELECT se.name FROM `tabStock Entry` se
                        WHERE se.purpose IN (
                            'Material Transfer',
                            'Material Transfer for Manufacture'
                        )
                    )
                )
            """

    return query


def get_data(filters, sources, company, warehouses):
    source_sql = " UNION ALL ".join(
        get_source_query(source, filters, company, warehouses) for source in sources
    )

    params = {
        "from_date": getdate(filters.from_date),
        "to_date": getdate(filters.to_date),
        "company": company,
        "warehouses": tuple(warehouses),
    }

    item_cond = ""
    if filters.get("item_group"):
        groups = frappe.db.get_descendants("Item Group", filters.item_group) or []
        groups.append(filters.item_group)
        params["item_groups"] = tuple(set(groups))
        item_cond += " AND i.item_group IN %(item_groups)s"
    if filters.get("brand"):
        params["brand"] = filters.brand
        item_cond += " AND i.brand = %(brand)s"
    if filters.get("item_code"):
        params["item_code"] = filters.item_code
        item_cond += " AND i.name = %(item_code)s"
    if cint(filters.get("only_with_stock")):
        item_cond += " AND IFNULL(b.qty, 0) > 0"

    warehouse_condition = "AND b.warehouse IN %(warehouses)s" if warehouses else "AND 1 = 0"

    query = f"""
        SELECT
            i.name AS item_code,
            i.item_name AS item_name,
            i.item_group AS item_group,
            i.brand AS brand,
            i.stock_uom AS stock_uom,
            IFNULL(b.qty, 0) AS stock_qty,
            IFNULL(b.val, 0) AS stock_value,
            l.last_dt AS last_movement_date,
            DATEDIFF(%(to_date)s, l.last_dt) AS days_since_last
        FROM `tabItem` i
        LEFT JOIN (
            SELECT b.item_code, SUM(b.actual_qty) AS qty, SUM(b.stock_value) AS val
            FROM `tabBin` b
            INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
            WHERE w.company = %(company)s
              {warehouse_condition}
            GROUP BY b.item_code
        ) b ON b.item_code = i.name
        LEFT JOIN (
            SELECT item_code, MAX(dt) AS last_dt
            FROM ({source_sql}) s1
            WHERE dt < %(from_date)s
            GROUP BY item_code
        ) l ON l.item_code = i.name
        LEFT JOIN (
            SELECT DISTINCT item_code
            FROM ({source_sql}) s2
            WHERE dt BETWEEN %(from_date)s AND %(to_date)s
        ) m ON m.item_code = i.name
        WHERE i.disabled = 0
            AND i.is_stock_item = 1
            AND i.has_variants = 0
            AND m.item_code IS NULL
            {item_cond}
        ORDER BY stock_value DESC, i.name
    """

    return frappe.db.sql(query, params, as_dict=True)


def get_report_summary(data, currency):
    total_items = len(data)
    items_with_stock = len([row for row in data if flt(row.stock_qty) > 0])
    total_value = sum(flt(row.stock_value) for row in data)

    return [
        {
            "value": total_items,
            "label": _("Unsold Items"),
            "datatype": "Int",
            "indicator": "Red",
        },
        {
            "value": items_with_stock,
            "label": _("Unsold Items With Stock"),
            "datatype": "Int",
            "indicator": "Orange",
        },
        {
            "value": total_value,
            "label": _("Locked Stock Value"),
            "datatype": "Currency",
            "currency": currency,
            "indicator": "Blue",
        },
    ]
