"""Display-only invoice item amounts: native v16 details with a v15 fallback.

On ERPNext v15 there is no native Item Wise Tax Detail DocType or
`item_wise_tax_details` field. On such a site the original AWHT app computed
`item_tax_amount` / `amount_after_tax` from each invoice's tax rows. This module
reproduces that deterministic legacy formula on validate.

Precedence: when awht_customizations is still installed, its validate hook runs
first (it is installed before this app) and this fallback runs last. Both use the
same formula, so the result is idempotent and remains correct whichever app is
removed first. A partially restored native schema is deliberately unsupported:
if only the field or only the DocType exists, the hook leaves existing values
untouched rather than choosing between two disagreeing sources. Onload never
recomputes v15 values, so persisted historical invoices keep their values.
"""

import json
from math import isfinite

import erpnext
import frappe
from frappe import _
from frappe.utils import cint, flt


MAX_PREVIEW_BYTES = 250_000
MAX_PREVIEW_ITEMS = 100
MAX_PREVIEW_TAXES = 40


def _has_native_item_wise_tax_details(doc):
	"""Item Wise Tax Detail is an ERPNext v16 DocType, not a v15 feature."""
	if not doc.meta.has_field("item_wise_tax_details"):
		return False
	# A restored site can carry a stale custom field with the same name. Never
	# let that turn this display-only hook into a writer over the original
	# AWHT v15 item_tax_amount / amount_after_tax calculation.
	return bool(frappe.db.exists("DocType", "Item Wise Tax Detail"))


def _has_native_item_wise_tax_support():
	return bool(
		frappe.get_meta("Sales Invoice").has_field("item_wise_tax_details")
		and frappe.db.exists("DocType", "Item Wise Tax Detail")
	)


def _set_v15_item_wise_tax(doc):
	"""Port of awht_customizations.sales_invoice.set_item_wise_tax for v15."""
	taxes = list(doc.get("taxes") or [])
	net_total = flt(doc.get("net_total"))

	for row in doc.get("items") or []:
		net = flt(row.get("net_amount")) if row.get("net_amount") not in (None, "") else flt(row.get("amount"))

		item_tax_rates = {}
		if row.get("item_tax_rate"):
			try:
				item_tax_rates = json.loads(row.item_tax_rate)
			except Exception:
				item_tax_rates = {}
			if not isinstance(item_tax_rates, dict):
				item_tax_rates = {}

		amounts = []
		cumulative = []

		for tax in taxes:
			amount = 0

			if tax.get("category") != "Valuation":
				rate = flt(item_tax_rates.get(tax.get("account_head"), tax.get("rate")))
				charge_type = tax.get("charge_type")

				if charge_type == "On Net Total":
					amount = net * rate / 100
				elif charge_type == "On Previous Row Amount":
					reference = cint(tax.get("row_id")) - 1
					amount = (
						amounts[reference] if 0 <= reference < len(amounts) else 0
					) * rate / 100
				elif charge_type == "On Previous Row Total":
					reference = cint(tax.get("row_id")) - 1
					amount = (
						cumulative[reference] if 0 <= reference < len(cumulative) else net
					) * rate / 100
				elif charge_type == "On Item Quantity":
					amount = flt(row.get("qty")) * rate
				elif charge_type == "Actual":
					amount = flt(tax.get("tax_amount")) * net / net_total if net_total else 0

				if tax.get("add_deduct_tax") == "Deduct":
					amount = -amount

			amounts.append(amount)
			cumulative.append((cumulative[-1] if cumulative else net) + amount)

		total_tax = sum(amounts)
		row.item_tax_amount = flt(total_tax, row.precision("item_tax_amount"))
		row.amount_after_tax = flt(net + total_tax, row.precision("amount_after_tax"))


def set_item_tax_display(doc, method=None):
	"""Read native details after validate, or the persisted native table on form load."""
	meta = frappe.get_meta("Sales Invoice Item")
	if not (meta.has_field("item_tax_amount") and meta.has_field("amount_after_tax")):
		return

	has_native_field = doc.meta.has_field("item_wise_tax_details")
	has_native_doctype = bool(frappe.db.exists("DocType", "Item Wise Tax Detail"))
	if not has_native_field:
		# Only a complete absence of the native schema identifies a v15 site.
		# A stale field-like DocType (or a missing field on v16) is unsupported.
		if not has_native_doctype and method != "onload":
			_set_v15_item_wise_tax(doc)
		return
	if not has_native_doctype:
		return

	items = doc.get("items") or []
	for item in items:
		item.item_tax_amount = None
		item.amount_after_tax = None
	if not items:
		return
	if (
		doc.get("is_cash_or_non_trade_discount")
		and doc.get("apply_discount_on") == "Grand Total"
		and doc.get("discount_amount")
	):
		return  # The header-only discount has no native per-item allocation.

	values = {}
	for item in items:
		try:
			net = float(item.get("net_amount"))
		except (TypeError, ValueError, OverflowError):
			return
		if not item.name or not isfinite(net) or item.name in values:
			return
		values[item.name] = net

	taxes = doc.get("taxes") or []
	if not taxes:
		for item in items:
			item.item_tax_amount = 0
			item.amount_after_tax = flt(values[item.name], item.precision("amount_after_tax"))
		return

	# Native Item Wise Tax Detail.amount is in company currency. Converting it back
	# to invoice currency would invent a per-row rounding allocation.
	if (
		not doc.get("company") or not doc.get("currency") or flt(doc.get("conversion_rate")) != 1
		or any(tax.get("category") == "Valuation" or tax.get("dont_recompute_tax") for tax in taxes)
	):
		return
	if doc.currency != erpnext.get_company_currency(doc.company):
		return

	details = doc.get("item_wise_tax_details" if method == "onload" else "_item_wise_tax_details") or []
	if len(details) != len(items) * len(taxes):
		return
	tax_rows = {tax.name: tax for tax in taxes if tax.name}
	if len(tax_rows) != len(taxes):
		return

	per_item = dict.fromkeys(values, 0.0)
	per_tax = dict.fromkeys(tax_rows, 0.0)
	seen = set()
	for detail in details:
		item = detail.get("item_row") if method == "onload" else detail.get("item")
		tax = detail.get("tax_row") if method == "onload" else detail.get("tax")
		item_name = item if isinstance(item, str) else getattr(item, "name", None)
		tax_name = tax if isinstance(tax, str) else getattr(tax, "name", None)
		if item_name not in values or tax_name not in tax_rows:
			return
		key = (item_name, tax_name)
		if key in seen:
			return
		seen.add(key)
		try:
			amount = float(detail.get("amount"))
		except (TypeError, ValueError, OverflowError):
			return
		if not isfinite(amount):
			return
		per_item[item_name] += amount
		per_tax[tax_name] += amount

	for tax in taxes:
		try:
			expected = float(tax.get("base_tax_amount_after_discount_amount"))
		except (TypeError, ValueError, OverflowError):
			return
		if not isfinite(expected):
			return
		if tax.get("add_deduct_tax") == "Deduct":
			expected = -expected
		precision = tax.precision("base_tax_amount_after_discount_amount")
		if flt(per_tax[tax.name], precision) != flt(expected, precision):
			return

	amounts = {
		item.name: flt(per_item[item.name], item.precision("item_tax_amount")) for item in items
	}
	precision = doc.precision("total_taxes_and_charges")
	if flt(sum(amounts.values()), precision) != flt(sum(per_tax.values()), precision):
		return
	for item in items:
		item.item_tax_amount = amounts[item.name]
		item.amount_after_tax = flt(
			values[item.name] + amounts[item.name], item.precision("amount_after_tax")
		)


@frappe.whitelist(methods=["POST"])
def preview_item_taxes(doc):
	"""Calculate a draft's native item tax detail in memory; never save the supplied document."""
	if isinstance(doc, dict) and len(json.dumps(doc, default=str).encode("utf-8")) > MAX_PREVIEW_BYTES:
		frappe.throw(_("The invoice is too large for a tax preview."), frappe.ValidationError)
	if isinstance(doc, str):
		if len(doc.encode("utf-8")) > MAX_PREVIEW_BYTES:
			frappe.throw(_("The invoice is too large for a tax preview."), frappe.ValidationError)
		try:
			doc = frappe.parse_json(doc)
		except (TypeError, ValueError):
			frappe.throw(_("Invalid invoice tax preview data."), frappe.ValidationError)
	if not isinstance(doc, dict) or doc.get("doctype") != "Sales Invoice":
		frappe.throw(_("A Sales Invoice is required for tax preview."), frappe.ValidationError)
	if doc.get("docstatus") not in (None, 0, "0"):
		frappe.throw(_("Tax preview is available only for draft invoices."), frappe.PermissionError)
	name, company, customer = doc.get("name"), doc.get("company"), doc.get("customer")
	if not all(isinstance(value, str) and 0 < len(value) <= 140 for value in (company, customer)):
		frappe.throw(_("Select a company and customer first."), frappe.ValidationError)
	if name is not None and (not isinstance(name, str) or len(name) > 140):
		frappe.throw(_("Invalid Sales Invoice name."), frappe.ValidationError)
	items, taxes = doc.get("items"), doc.get("taxes") or []
	if (
		not isinstance(items, list) or not isinstance(taxes, list)
		or len(items) > MAX_PREVIEW_ITEMS or len(taxes) > MAX_PREVIEW_TAXES
		or any(not isinstance(row, dict) for row in [*items, *taxes])
	):
		frappe.throw(_("Invalid or oversized invoice item/tax rows."), frappe.ValidationError)

	saved = frappe.get_doc("Sales Invoice", name) if name and frappe.db.exists("Sales Invoice", name) else None
	if saved:
		frappe.has_permission("Sales Invoice", "read", doc=saved, throw=True)
		frappe.has_permission("Sales Invoice", "write", doc=saved, throw=True)
		if saved.docstatus != 0 or saved.company != company or saved.customer != customer:
			frappe.throw(_("Save the draft with its current company and customer first."), frappe.PermissionError)
	elif not doc.get("__islocal"):
		frappe.throw(_("A saved draft or a new invoice is required for tax preview."), frappe.PermissionError)
	else:
		frappe.has_permission("Sales Invoice", "create", throw=True)
		frappe.has_permission("Sales Invoice", "read", throw=True)

	frappe.has_permission("Company", "read", doc=company, throw=True)
	frappe.has_permission("Customer", "read", doc=customer, throw=True)
	frappe.has_permission("Sales Invoice Item", "read", parent_doctype="Sales Invoice", throw=True)
	frappe.has_permission("Sales Taxes and Charges", "read", parent_doctype="Sales Invoice", throw=True)
	item_meta = frappe.get_meta("Sales Invoice Item")
	if not (item_meta.has_field("item_tax_amount") and item_meta.has_field("amount_after_tax")):
		frappe.throw(_("Install both Sales Invoice Item tax display fields first."), frappe.ValidationError)
	if any(item_meta.get_field(field).get("mask") for field in ("item_tax_amount", "amount_after_tax")):
		frappe.throw(_("The invoice item tax fields are masked."), frappe.PermissionError)
	if not {"item_tax_amount", "amount_after_tax", "net_amount", "item_code"} <= set(
		item_meta.get_permitted_fieldnames(parenttype="Sales Invoice")
	):
		frappe.throw(_("You cannot read the invoice item tax fields."), frappe.PermissionError)

	saved_items = {row.name: row for row in saved.get("items")} if saved else {}
	saved_taxes = {row.name: row for row in saved.get("taxes")} if saved else {}
	seen_items, seen_taxes, item_codes = set(), set(), set()
	if any(row.get("dont_recompute_tax") for row in taxes) or any(
		row.get("dont_recompute_tax") for row in saved_taxes.values()
	):
		frappe.throw(_("Tax preview is unavailable for non-recomputed tax rows."), frappe.ValidationError)
	for rows, seen, existing, doctype in (
		(items, seen_items, saved_items, "Sales Invoice Item"),
		(taxes, seen_taxes, saved_taxes, "Sales Taxes and Charges"),
	):
		for row in rows:
			row_name = row.get("name")
			if (
				row.get("doctype") not in (None, doctype)
				or not isinstance(row_name, str) or not row_name or len(row_name) > 140
				or row_name in seen
				or row.get("parent") not in (None, name)
				or row.get("parenttype") not in (None, "Sales Invoice")
				or row.get("parentfield") not in (None, "items" if doctype == "Sales Invoice Item" else "taxes")
			):
				frappe.throw(_("Invalid invoice child row."), frappe.PermissionError)
			seen.add(row_name)
			if row_name in existing:
				frappe.has_permission(doctype, "read", doc=existing[row_name], throw=True)
			elif not row.get("__islocal") or frappe.db.exists(doctype, row_name):
				frappe.throw(_("Invoice child row does not belong to this draft."), frappe.PermissionError)
			if doctype == "Sales Invoice Item":
				code = row.get("item_code")
				if not isinstance(code, str) or not code:
					frappe.throw(_("Select an item before previewing its taxes."), frappe.ValidationError)
				item_codes.add(code)
	for code in item_codes:
		frappe.has_permission("Item", "read", doc=code, throw=True)
	if (
		not _has_native_item_wise_tax_support()
		or (not taxes and (doc.get("taxes_and_charges") or any(row.get("item_tax_template") for row in items)))
		or (saved and saved.get("currency") and saved.get("currency") != doc.get("currency"))
		or doc.get("apply_tds")
		or any(row.get("apply_tds") for row in items)
		or any(row.get("is_tax_withholding_account") for row in taxes)
		or (saved and (saved.get("apply_tds")
			or any(row.get("is_tax_withholding_account") for row in saved_taxes.values())
			or any(row.get("apply_tds") for row in saved_items.values())))
	):
		return [{"name": row["name"], "item_tax_amount": None, "amount_after_tax": None} for row in items]

	meta = frappe.get_meta("Sales Invoice")
	ignored = {"owner", "modified_by", "creation", "modified", "docstatus", "item_wise_tax_details",
		"payments", "advances", "payment_schedule", "tax_withholding_entries"}
	allowed = set(meta.get_valid_fields()) - ignored
	# Draft JSON can omit nullable fields that ERPNext's calculator reads as attributes.
	numeric = {"Currency", "Float", "Int", "Long Int", "Percent", "Check"}
	data = {
		key: 0 if (field := meta.get_field(key)) and field.fieldtype in numeric else None
		for key in allowed if not key.startswith("_") and key != "flags"
	}
	data.update({key: value for key, value in doc.items() if key in data})
	for fieldname, doctype in (("items", "Sales Invoice Item"), ("taxes", "Sales Taxes and Charges")):
		child_meta = frappe.get_meta(doctype)
		fields = set(child_meta.get_valid_fields()) - {
			"owner", "modified_by", "creation", "modified", "docstatus",
			"parent", "parenttype", "parentfield", "dont_recompute_tax",
			"item_tax_amount", "amount_after_tax",
		}
		data[fieldname] = [
			{
				**{key: 0 if (field := child_meta.get_field(key)) and field.fieldtype in numeric else None
					for key in fields if not key.startswith("_") and key != "flags"},
				**{key: value for key, value in row.items() if key in fields and not key.startswith("_") and key != "flags"},
			}
			for row in doc.get(fieldname) or []
		]
	data.update(doctype="Sales Invoice", docstatus=0, is_consolidated=0)
	if not saved:
		data["__islocal"] = 1
	scratch = frappe.get_doc(data)
	from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals

	previous_flags = {
		key: frappe.flags.get(key) for key in ("round_off_applicable_accounts", "round_row_wise_tax")
	}
	try:
		calculate_taxes_and_totals(scratch)
	finally:
		for key, value in previous_flags.items():
			if value is None:
				frappe.flags.pop(key, None)
			else:
				frappe.flags[key] = value
	if scratch.company != company or scratch.customer != customer or scratch.docstatus != 0:
		frappe.throw(_("Tax preview changed the invoice identity."), frappe.PermissionError)
	if {row.name for row in scratch.get("items")} != seen_items:
		frappe.throw(_("Tax preview changed the invoice items."), frappe.ValidationError)
	set_item_tax_display(scratch, "validate")
	return [
		{"name": row.name, "item_tax_amount": row.get("item_tax_amount"),
		 "amount_after_tax": row.get("amount_after_tax")}
		for row in scratch.get("items")
	]
