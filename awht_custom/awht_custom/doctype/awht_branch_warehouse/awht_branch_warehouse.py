"""Mappings are immutable: Frappe locks existing rows before before_validate.

New mappings lock the policy before insertion. Existing mapping saves and deletes
must not take that lock after Frappe has already locked the mapping row, because
Stock Entry takes the policy lock before locking mapping rows. Disable the policy
before deleting a mapping; create a replacement in a separate transaction.
Frappe also locks the row before on_trash: a stale disabled-policy snapshot
followed by delete and insert in one transaction can still deadlock against a
simultaneous Stock Entry. Direct DB writes bypass these protections.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from awht_custom.credit_control.mapping import validate_branch_company, validate_warehouse


class AWHTBranchWarehouse(Document):
    def before_validate(self):
        if self.is_new():
            policy = frappe.db.get_value(
                "AWHT Credit Policy",
                {"company": self.company},
                ["enabled", "source_warehouse"],
                as_dict=True,
                for_update=True,
            )
            if policy and policy.enabled and policy.source_warehouse == self.warehouse:
                frappe.throw(_("The policy source warehouse cannot be a branch warehouse."))

    def validate(self):
        previous = self.get_doc_before_save()
        if previous and (previous.company, previous.branch, previous.warehouse) != (
            self.company, self.branch, self.warehouse
        ):
            frappe.throw(_("Delete and recreate a branch warehouse mapping instead of changing it."))
        if not frappe.db.exists("Branch", self.branch):
            frappe.throw(_("Select an existing Branch for this warehouse."))
        validate_branch_company(self.company, self.branch)
        validate_warehouse(self.company, self.warehouse)

    def on_trash(self):
        if frappe.db.get_value("AWHT Credit Policy", {"company": self.company}, "enabled"):
            frappe.throw(_("Disable the credit policy before deleting a branch warehouse mapping."))
