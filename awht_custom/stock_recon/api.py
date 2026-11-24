"""Server helpers for the Stock Reconciliation assistant."""

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate, nowtime


def _item_key(row):
    return (
        row.get("item_code") or "",
        row.get("warehouse") or "",
        row.get("batch_no") or "",
    )


def _remaining_rows(stock_rows, reconciled_keys):
    return [row for row in stock_rows if _item_key(row) not in reconciled_keys]


def _warehouse_names(warehouse):
    if not cint(frappe.db.get_value("Warehouse", warehouse, "is_group")):
        return [warehouse]

    from frappe.utils.nestedset import get_descendants_of

    return [warehouse, *get_descendants_of("Warehouse", warehouse, ignore_permissions=True)]


@frappe.whitelist(methods=["POST"])
def get_remaining_items(
    warehouse,
    company,
    from_date,
    to_date,
    posting_date=None,
    posting_time=None,
    ignore_empty_stock=1,
):
    """Return stock-reconciliation rows not already reconciled in the date range.

    The memory table records every row that was saved, including unchanged rows
    that ERPNext removes while saving. Submitted Stock Reconciliation Item rows
    are also used as a fallback for documents created before this app existed.
    Current-form rows are filtered by the browser because unsaved rows are not in
    the database yet.
    """
    frappe.has_permission("Stock Reconciliation", "write", throw=True)

    if not isinstance(warehouse, str) or not warehouse:
        frappe.throw(_("Select a warehouse first."), frappe.ValidationError)
    if not frappe.db.exists("Warehouse", warehouse):
        frappe.throw(_("Warehouse does not exist."), frappe.DoesNotExistError)
    frappe.has_permission("Warehouse", "read", doc=warehouse, throw=True)

    warehouse_company = frappe.db.get_value("Warehouse", warehouse, "company")
    company = company or warehouse_company
    if not company or (warehouse_company and warehouse_company != company):
        frappe.throw(_("The selected warehouse does not belong to the company."),
                     frappe.ValidationError)
    if not frappe.has_permission("Company", "read", doc=company):
        frappe.throw(_("No permission to read the company."), frappe.PermissionError)

    try:
        from_date = getdate(from_date)
        to_date = getdate(to_date)
    except (TypeError, ValueError):
        frappe.throw(_("Invalid reconciliation date range."), frappe.ValidationError)
    if from_date > to_date:
        frappe.throw(_("The 'from' date cannot be after the 'to' date."),
                     frappe.ValidationError)

    try:
        posting_date = getdate(posting_date or nowdate())
    except (TypeError, ValueError):
        frappe.throw(_("Invalid posting date."), frappe.ValidationError)
    posting_time = posting_time or nowtime()
    posting_date_arg = str(posting_date)

    warehouses = _warehouse_names(warehouse)
    placeholders = ", ".join(["%s"] * len(warehouses))
    reconciled = frappe.db.sql(
        f"""
        select distinct si.item_code, si.warehouse, coalesce(si.batch_no, '') as batch_no
        from `tabStock Reconciliation Item` si
        inner join `tabStock Reconciliation` sr on sr.name = si.parent
        where sr.docstatus = 1
          and sr.company = %s
          and sr.posting_date between %s and %s
          and si.warehouse in ({placeholders})
        """,
        [company, from_date, to_date, *warehouses],
        as_dict=True,
    )
    reconciled_keys = {_item_key(row) for row in reconciled}

    # ERPNext removes unchanged rows before saving a Stock Reconciliation. The
    # memory table captures those rows before that removal, including drafts.
    memory_rows = frappe.get_all(
        "Stock Reconciliation Memory",
        filters={
            "company": company,
            "posting_date": ["between", [from_date, to_date]],
            "warehouse": ["in", warehouses],
            "source_docstatus": ["!=", 2],
        },
        fields=["item_code", "warehouse", "batch_no"],
        limit_page_length=0,
    )
    reconciled_keys.update(_item_key(row) for row in memory_rows)

    from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_items

    stock_rows = get_items(
        warehouse=warehouse,
        posting_date=posting_date_arg,
        posting_time=posting_time,
        company=company,
        ignore_empty_stock=cint(ignore_empty_stock),
    )
    stock_rows = [frappe._dict(row) for row in (stock_rows or [])]

    return {
        "items": _remaining_rows(stock_rows, reconciled_keys),
        "reconciled_count": len(reconciled_keys),
        "total_count": len(stock_rows),
        "warehouse": warehouse,
        "from_date": str(from_date),
        "to_date": str(to_date),
    }
