"""Persist every Stock Reconciliation row, including rows ERPNext later drops."""

from contextlib import contextmanager

import frappe
from frappe.utils import flt, now_datetime


MEMORY_DOCTYPE = "Stock Reconciliation Memory"
SYNC_FLAG = "awht_stock_recon_memory_sync"


@contextmanager
def _memory_sync():
    frappe.flags[SYNC_FLAG] = True
    try:
        yield
    finally:
        frappe.flags[SYNC_FLAG] = False


def _row_snapshot(doc, row):
    return {
        "stock_reconciliation": doc.name,
        "company": doc.company,
        "posting_date": doc.posting_date,
        "item_code": row.item_code,
        "warehouse": row.warehouse,
        "batch_no": row.batch_no or None,
        "qty": flt(row.qty),
        "valuation_rate": flt(row.valuation_rate),
        "source_docstatus": int(doc.docstatus or 0),
        "reconciled_on": now_datetime(),
    }


def _snapshot_rows(doc):
    return [
        _row_snapshot(doc, row)
        for row in (doc.get("items") or [])
        if row.get("item_code") and row.get("warehouse")
    ]


def _key(row):
    return (row.get("item_code") or "", row.get("warehouse") or "", row.get("batch_no") or "")


def capture_items(doc, method=None):
    """Capture rows before ERPNext removes unchanged items from the document."""
    doc.flags.awht_recon_memory_rows = _snapshot_rows(doc)


def sync_memory(doc, method=None):
    if not doc.name or not doc.get("items"):
        return

    rows = getattr(doc.flags, "awht_recon_memory_rows", None)
    if rows is None:
        rows = _snapshot_rows(doc)

    existing = frappe.get_all(
        MEMORY_DOCTYPE,
        filters={"stock_reconciliation": doc.name},
        fields=["name", "item_code", "warehouse", "batch_no"],
        limit_page_length=0,
    )
    existing_by_key = {_key(row): row.name for row in existing}

    with _memory_sync():
        for row in rows:
            key = _key(row)
            memory_name = existing_by_key.get(key)
            values = {
                "company": row["company"],
                "posting_date": row["posting_date"],
                "qty": row["qty"],
                "valuation_rate": row["valuation_rate"],
                "source_docstatus": row["source_docstatus"],
                "reconciled_on": row["reconciled_on"],
            }
            if memory_name:
                frappe.db.set_value(MEMORY_DOCTYPE, memory_name, values, update_modified=False)
            else:
                frappe.get_doc({"doctype": MEMORY_DOCTYPE, **row}).insert(ignore_permissions=True)

    # Keep rows that ERPNext removed from a draft/update. They were already
    # reconciled and must not appear in the remaining-items list again. Memory is
    # removed only when the document is cancelled or deleted.


def delete_memory(doc, method=None):
    if not doc.name:
        return
    names = frappe.get_all(
        MEMORY_DOCTYPE,
        filters={"stock_reconciliation": doc.name},
        pluck="name",
        limit_page_length=0,
    )
    with _memory_sync():
        for memory_name in names:
            frappe.delete_doc(MEMORY_DOCTYPE, memory_name, force=True, ignore_permissions=True)
