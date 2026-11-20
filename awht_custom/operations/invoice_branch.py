"""Authorized preview of the canonical, explicit invoice branch warehouse mapping."""

import frappe
from frappe import _
from frappe.utils import cint

from awht_custom.credit_control import invoice as credit_invoice


@frappe.whitelist(methods=["POST"])
def get_branch_warehouse(company, branch, invoice=None, is_return=0):
	if not all(isinstance(value, str) and 0 < len(value) <= 140 for value in (company, branch)):
		frappe.throw(_("Select a company and branch first."), frappe.ValidationError)
	if invoice is not None and (not isinstance(invoice, str) or not invoice or len(invoice) > 140):
		frappe.throw(_("Invalid Sales Invoice name."), frappe.ValidationError)
	if invoice:
		saved = frappe.get_doc("Sales Invoice", invoice)
		frappe.has_permission("Sales Invoice", "read", doc=saved, throw=True)
		frappe.has_permission("Sales Invoice", "write", doc=saved, throw=True)
		if saved.docstatus != 0 or saved.get("is_return"):
			frappe.throw(_("A non-return draft invoice is required."), frappe.PermissionError)
	else:
		frappe.has_permission("Sales Invoice", "create", throw=True)
		frappe.has_permission("Sales Invoice", "read", throw=True)
	if cint(is_return):
		return None
	frappe.has_permission("Company", "read", doc=company, throw=True)
	frappe.has_permission("Branch", "read", doc=branch, throw=True)

	# Keep the mapping rules in the owning component; this document never gets saved.
	probe = frappe.get_doc({"doctype": "Sales Invoice", "company": company, "custom_branch": branch, "items": []})
	credit_invoice.map_invoice_branch_warehouse(probe)
	frappe.has_permission("Warehouse", "read", doc=probe.set_warehouse, throw=True)
	return probe.set_warehouse
