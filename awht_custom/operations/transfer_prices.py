"""Previous salesman prices from authorized, earlier Material Transfer rows."""

from datetime import datetime

import frappe
from frappe import _
from frappe.utils import flt, get_time, getdate, now_datetime


PRICES_ROLE = "Previous Transfer Prices Viewer"
MAX_RESULTS = 50
MAX_SCAN = 250


@frappe.whitelist(methods=["POST"])
def get_previous_transfer_prices(stock_entry, row_name, limit=20):
	"""Return a small, permission-checked history for a saved Stock Entry item row."""
	if PRICES_ROLE not in frappe.get_roles():
		frappe.throw(_("You are not permitted to view previous transfer prices."), frappe.PermissionError)
	if not stock_entry or not row_name:
		frappe.throw(_("Save the Stock Entry and select an item row first."), frappe.ValidationError)

	current = frappe.get_doc("Stock Entry", stock_entry)
	frappe.has_permission("Stock Entry", "read", doc=current, throw=True)
	if current.docstatus != 0 or current.purpose != "Material Transfer":
		frappe.throw(_("Previous prices are available only on draft Material Transfers."), frappe.ValidationError)

	item = next((row for row in current.get("items") or [] if row.name == row_name), None)
	if not item or (item.parent, item.parenttype, item.parentfield) != (
		current.name, "Stock Entry", "items"
	):
		frappe.throw(_("The item row does not belong to this Stock Entry."), frappe.PermissionError)
	frappe.has_permission("Stock Entry Detail", "read", doc=item, throw=True)

	item_meta = frappe.get_meta("Stock Entry Detail")
	if not item_meta.has_field("salesman_price") or not frappe.db.has_column(
		"Stock Entry Detail", "salesman_price"
	):
		frappe.throw(
			_("Stock Entry Detail.salesman_price is required; add and migrate the custom field first."),
			frappe.ValidationError,
		)
	item_fields = {"item_code", "s_warehouse", "t_warehouse", "uom", "qty", "salesman_price"}
	if any(item_meta.get_field(field).get("mask") for field in item_fields) or not item_fields <= set(
		item_meta.get_permitted_fieldnames(parenttype="Stock Entry")
	):
		frappe.throw(_("You cannot read the transfer price or item details."), frappe.PermissionError)
	entry_meta = frappe.get_meta("Stock Entry")
	entry_fields = {"posting_date", "posting_time", "company", "purpose"}
	if any(entry_meta.get_field(field).get("mask") for field in entry_fields) or not entry_fields <= set(
		entry_meta.get_permitted_fieldnames()
	):
		frappe.throw(_("You cannot read the transfer posting details."), frappe.PermissionError)

	if not all((item.item_code, item.s_warehouse, item.t_warehouse, item.uom)):
		frappe.throw(_("Select the item, its UOM and both row warehouses first."), frappe.ValidationError)
	if not current.posting_date or current.posting_time is None:
		frappe.throw(_("Set the Stock Entry posting date and time first."), frappe.ValidationError)
	try:
		cutoff = datetime.combine(getdate(current.posting_date), get_time(current.posting_time))
	except (TypeError, ValueError):
		frappe.throw(_("The Stock Entry posting date or time is invalid."), frappe.ValidationError)
	# A future-dated draft must not reveal transfers that have not happened yet.
	cutoff = min(cutoff, now_datetime())
	try:
		requested = max(1, min(int(limit) if limit not in (None, "") else 20, MAX_RESULTS))
	except (TypeError, ValueError):
		frappe.throw(_("Invalid previous-prices limit."), frappe.ValidationError)

	rows = frappe.db.sql(
		"""
		select se.name as stock_entry, d.name as detail_name
		from `tabStock Entry Detail` d
		inner join `tabStock Entry` se on se.name = d.parent
		where d.parenttype = 'Stock Entry' and d.parentfield = 'items'
		  and se.purpose = 'Material Transfer' and se.company = %(company)s
		  and se.docstatus in (0, 1) and se.name != %(current)s
		  and d.item_code = %(item_code)s and d.uom = %(uom)s
		  and d.s_warehouse = %(source)s and d.t_warehouse = %(target)s
		  and d.salesman_price > 0
		  and (se.posting_date < %(date)s
		       or (se.posting_date = %(date)s and se.posting_time < %(time)s))
		order by se.posting_date desc, se.posting_time desc, se.creation desc, d.idx desc
		limit %(scan_limit)s
		""",
		{
			"company": current.company,
			"current": current.name,
			"item_code": item.item_code,
			"uom": item.uom,
			"source": item.s_warehouse,
			"target": item.t_warehouse,
			"date": cutoff.date(),
			"time": cutoff.time(),
			"scan_limit": min(MAX_SCAN, requested * 5),
		},
		as_dict=True,
	)

	results, checked = [], {}
	for match in rows:
		name = match.stock_entry
		if name == current.name:
			continue
		if name not in checked:
			try:
				previous = frappe.get_doc("Stock Entry", name)
			except frappe.DoesNotExistError:
				previous = None
			checked[name] = previous if previous and frappe.has_permission(
				"Stock Entry", "read", doc=previous
			) else None
		previous = checked[name]
		if not previous or previous.purpose != "Material Transfer" or previous.company != current.company:
			continue
		if previous.docstatus not in (0, 1) or not previous.posting_date or previous.posting_time is None:
			continue
		try:
			posting = datetime.combine(getdate(previous.posting_date), get_time(previous.posting_time))
		except (TypeError, ValueError):
			continue
		if posting >= cutoff:
			continue

		prior_item = next((r for r in previous.get("items") or [] if r.name == match.detail_name), None)
		if not prior_item or not frappe.has_permission("Stock Entry Detail", "read", doc=prior_item):
			continue
		if (
			prior_item.parent != name
			or prior_item.parenttype != "Stock Entry"
			or prior_item.parentfield != "items"
			or prior_item.item_code != item.item_code
			or prior_item.uom != item.uom
			or prior_item.s_warehouse != item.s_warehouse
			or prior_item.t_warehouse != item.t_warehouse
			or flt(prior_item.get("salesman_price")) <= 0
		):
			continue
		results.append(
			{
				"stock_entry": name,
				"docstatus": previous.docstatus,
				"posting_date": previous.posting_date,
				"posting_time": previous.posting_time,
				"qty": prior_item.qty,
				"uom": prior_item.uom,
				"salesman_price": prior_item.salesman_price,
			}
		)
		if len(results) == requested:
			break
	return results
