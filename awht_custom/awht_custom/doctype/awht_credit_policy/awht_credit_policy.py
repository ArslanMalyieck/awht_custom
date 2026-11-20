import frappe
from frappe import _
from frappe.model.document import Document

from awht_custom.credit_control.exposure import require_credit_fields, require_sar_company
from awht_custom.credit_control.mapping import get_company_mappings, validate_warehouse


class AWHTCreditPolicy(Document):
    def validate(self):
        if not self.enabled:
            return
        if not self.source_warehouse:
            frappe.throw(_("Select a head-office source warehouse before enabling credit control."))
        require_credit_fields()
        require_sar_company(self.company)
        validate_warehouse(self.company, self.source_warehouse)
        mappings = get_company_mappings(self.company)
        if not mappings:
            frappe.throw(_("Map at least one branch warehouse before enabling credit control."))
        if self.source_warehouse in (warehouse for names in mappings.values() for warehouse in names):
            frappe.throw(_("The policy source warehouse cannot be a branch warehouse."))
