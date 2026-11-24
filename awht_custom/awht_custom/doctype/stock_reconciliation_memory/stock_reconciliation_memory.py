import frappe
from frappe import _
from frappe.model.document import Document


SYNC_FLAG = "awht_stock_recon_memory_sync"


class StockReconciliationMemory(Document):
    def _assert_internal_sync(self):
        if not frappe.flags.get(SYNC_FLAG):
            frappe.throw(
                _("Stock Reconciliation Memory records are generated automatically and cannot be created, changed, or deleted manually."),
                frappe.PermissionError,
            )

    def before_insert(self):
        self._assert_internal_sync()

    def before_save(self):
        self._assert_internal_sync()

    def on_trash(self):
        self._assert_internal_sync()
