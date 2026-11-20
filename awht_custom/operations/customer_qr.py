"""Customer QR PDF, limited to readable, customer-linked information."""

import base64
import io

import frappe
from frappe import _


MAX_LINKED = 8
MAX_QR_BYTES = 2400
MAX_VALUE_CHARS = 160


def _value(doc, fieldname, permitted):
	field = doc.meta.get_field(fieldname)
	if fieldname not in permitted or not field or field.get("mask"):
		return ""
	return " ".join(str(doc.get(fieldname) or "").split())[:MAX_VALUE_CHARS]


def _unique(values):
	return list(dict.fromkeys(value for value in values if value))


def _linked_docs(doctype, customer, primary):
	filters = {
		"parenttype": doctype,
		"parentfield": "links",
		"link_doctype": "Customer",
		"link_name": customer,
	}
	names = frappe.get_all(
		"Dynamic Link", filters=filters, pluck="parent", distinct=True, limit_page_length=MAX_LINKED
	)
	for name in _unique([primary, *names])[:MAX_LINKED]:
		# Primary links are hints, not proof. Recheck every included record's actual link.
		if not frappe.db.exists("Dynamic Link", {**filters, "parent": name}):
			continue
		try:
			doc = frappe.get_doc(doctype, name)
		except frappe.DoesNotExistError:
			continue
		if not frappe.has_permission(doctype, "read", doc=doc) or doc.get("disabled"):
			continue
		yield doc


def _qr_text(customer):
	customer_fields = set(customer.meta.get_permitted_fieldnames())
	contact_fields = set(frappe.get_meta("Contact").get_permitted_fieldnames())
	address_fields = set(frappe.get_meta("Address").get_permitted_fieldnames())
	phone_fields = set(frappe.get_meta("Contact Phone").get_permitted_fieldnames(parenttype="Contact"))
	email_fields = set(frappe.get_meta("Contact Email").get_permitted_fieldnames(parenttype="Contact"))

	lines = []

	def add(label, text):
		if text:
			line = f"{label}: {text}"
			if len("\n".join([*lines, line]).encode("utf-8")) <= MAX_QR_BYTES:
				lines.append(line)

	add("Customer ID", " ".join(customer.name.split())[:MAX_VALUE_CHARS])
	for label, field in (
		("Customer Name", "customer_name"),
		("Customer Group", "customer_group"),
		("Territory", "territory"),
		("Tax ID", "tax_id"),
	):
		add(label, _value(customer, field, customer_fields))

	primary = _value(customer, "customer_primary_contact", customer_fields)
	for index, contact in enumerate(_linked_docs("Contact", customer.name, primary), 1):
		name = " ".join(filter(None, (
			_value(contact, field, contact_fields) for field in ("first_name", "middle_name", "last_name")
		)))
		phones = [_value(contact, field, contact_fields) for field in ("mobile_no", "phone")]
		if "phone_nos" in contact_fields:
			phones.extend(
				_value(row, "phone", phone_fields)
				for row in (contact.get("phone_nos") or [])[:4]
				if not row.get("disabled") and frappe.has_permission("Contact Phone", "read", doc=row)
			)
		emails = [_value(contact, "email_id", contact_fields)]
		if "email_ids" in contact_fields:
			emails.extend(
				_value(row, "email_id", email_fields)
				for row in (contact.get("email_ids") or [])[:4]
				if not row.get("disabled") and frappe.has_permission("Contact Email", "read", doc=row)
			)
		add(
			f"Contact {index}",
			" | ".join(filter(None, (
				name, _value(contact, "designation", contact_fields),
				" / ".join(_unique(phones)[:4]), " / ".join(_unique(emails)[:4]),
			))),
		)

	primary = _value(customer, "customer_primary_address", customer_fields)
	for index, address in enumerate(_linked_docs("Address", customer.name, primary), 1):
		parts = [
			_value(address, field, address_fields)
			for field in ("address_line1", "address_line2", "city", "county", "state", "pincode", "country")
		]
		add(
			f"Address {index}",
			" | ".join(filter(None, (
				_value(address, "address_title", address_fields),
				_value(address, "address_type", address_fields),
				", ".join(filter(None, parts)),
				" / ".join(filter(None, (
					_value(address, "phone", address_fields), _value(address, "fax", address_fields)
				))),
				_value(address, "email_id", address_fields),
			))),
		)
	return "\n".join(lines)


def _customer_qr_image(customer):
	if not isinstance(customer, str) or not customer or len(customer) > 140:
		frappe.throw(_("Select a saved Customer first."), frappe.ValidationError)
	cust = frappe.get_doc("Customer", customer)
	frappe.has_permission("Customer", "read", doc=cust, throw=True)
	frappe.has_permission("Customer", "print", doc=cust, throw=True)
	frappe.has_permission("Contact", "read", throw=True)
	frappe.has_permission("Address", "read", throw=True)

	from pyqrcode import create as create_qr

	qr = create_qr(_qr_text(cust), error="L", encoding="utf-8")
	image = io.BytesIO()
	qr.svg(image, scale=5, quiet_zone=4, module_color="#000", background="#fff")
	return base64.b64encode(image.getvalue()).decode("ascii")


def _print_page(encoded):
	# Only a library-produced QR image is interpolated; no record value becomes HTML or an asset URL.
	return f"""<!doctype html><html><head><meta charset="utf-8"><title>Customer QR</title>
<style>@page {{ size: A4; margin: 0; }}
body {{ margin: 0; text-align: center; }}
img {{ width: 120mm; height: 120mm; margin-top: 88mm; }}</style></head>
<body><img alt="Customer QR" src="data:image/svg+xml;base64,{encoded}"></body></html>"""


@frappe.whitelist(methods=["POST"])
def get_customer_qr_print_page(customer):
	"""Return a static, same-origin page for an authorized browser print dialog."""
	return _print_page(_customer_qr_image(customer))


@frappe.whitelist(methods=["GET"])
def download_customer_qr(customer):
	"""Serve an inline PDF as a manual print/download fallback."""
	html = _print_page(_customer_qr_image(customer))
	from frappe.utils.pdf import get_pdf

	frappe.local.response["filename"] = "Customer-QR.pdf"
	frappe.local.response["filecontent"] = get_pdf(html, {"page-size": "A4", "margin-top": "0mm",
		"margin-bottom": "0mm", "margin-left": "0mm", "margin-right": "0mm"})
	frappe.local.response["type"] = "pdf"
