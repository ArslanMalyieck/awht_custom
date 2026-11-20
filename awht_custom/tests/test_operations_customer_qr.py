"""Permission, link-proof, and PDF response tests without a Frappe site."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from awht_custom.operations import customer_qr as qr


class Meta:
	def __init__(self, fields, permitted=None, masked=()):
		self.fields = {field: {"mask": field in masked} for field in fields}
		self.permitted = set(fields if permitted is None else permitted)

	def get_field(self, field):
		return self.fields.get(field)

	def get_permitted_fieldnames(self, parenttype=None):
		return self.permitted

	def has_field(self, field):
		return field in self.fields


def _doc(doctype, name, fields, **values):
	doc = frappe._dict(doctype=doctype, name=name, **values)
	doc.meta = Meta(fields)
	return doc


def _throw(message, exc=frappe.ValidationError, **kwargs):
	raise exc(message)


class TestCustomerQR(unittest.TestCase):
	def setUp(self):
		self.customer = _doc(
			"Customer", "CUST-1",
			("customer_name", "customer_group", "territory", "tax_id", "customer_primary_contact",
			 "customer_primary_address"),
			customer_name="Customer Name", customer_group="Retail", territory="Region",
			tax_id="VAT-1", customer_primary_contact="PRIMARY-NOT-LINKED",
			customer_primary_address="ADDRESS-OK",
		)
		self.docs = {("Customer", "CUST-1"): self.customer}
		self.metas = {
			"Contact": Meta(("first_name", "middle_name", "last_name", "designation", "mobile_no",
				"phone", "email_id", "phone_nos", "email_ids")),
			"Address": Meta(("address_title", "address_type", "address_line1", "address_line2",
				"city", "county", "state", "pincode", "country", "phone", "fax", "email_id")),
			"Contact Phone": Meta(("phone",)),
			"Contact Email": Meta(("email_id",)),
		}
		self.linked = {"Contact": set(), "Address": set()}
		self.names = {"Contact": [], "Address": []}
		self.denied_doctypes = set()
		self.denied_docs = set()
		self.db = MagicMock()
		self.db.exists.side_effect = lambda dt, filters: (
			dt == "Dynamic Link" and filters.get("parentfield") == "links"
			and filters.get("link_doctype") == "Customer"
			and filters.get("link_name") == self.customer.name
			and filters.get("parent") in self.linked.get(filters.get("parenttype"), set())
		)

		def get_all(doctype, filters=None, **kwargs):
			self.assertEqual(doctype, "Dynamic Link")
			self.assertEqual(kwargs["limit_page_length"], qr.MAX_LINKED)
			self.assertEqual(filters["link_name"], self.customer.name)
			return self.names[filters["parenttype"]]

		def permitted(doctype, ptype, doc=None, throw=False):
			allowed = (doctype, ptype) not in self.denied_doctypes and (
				(doctype, doc.name) not in self.denied_docs if doc else True
			)
			if not allowed and throw:
				raise frappe.PermissionError("Not permitted")
			return allowed

		self.pdf_html = None
		self.qr_payload = None
		self.response = frappe._dict()
		for p in (
			patch.object(qr.frappe, "db", self.db),
			patch.object(qr.frappe, "get_doc", side_effect=lambda dt, name: self.docs[(dt, name)]),
			patch.object(qr.frappe, "get_meta", side_effect=lambda dt: self.metas[dt]),
			patch.object(qr.frappe, "get_all", side_effect=get_all),
			patch.object(qr.frappe, "has_permission", side_effect=permitted),
			patch.object(qr.frappe, "throw", side_effect=_throw),
			patch.object(qr.frappe.local, "response", self.response, create=True),
			patch.object(qr, "_", side_effect=lambda text: text),
			patch.dict(sys.modules, {"frappe.utils.pdf": SimpleNamespace(get_pdf=self._pdf)}),
			patch("pyqrcode.create", side_effect=self._create_qr),
		):
			p.start()
			self.addCleanup(p.stop)

	def _create_qr(self, text, **kwargs):
		self.qr_payload = text
		self.assertEqual(kwargs, {"error": "L", "encoding": "utf-8"})
		return SimpleNamespace(svg=lambda stream, **kw: stream.write(b"<svg><path d='M0 0'/></svg>"))

	def _pdf(self, html, options):
		self.pdf_html = html
		return b"%PDF-1.4 test"

	def _add_contact(self, name, **kwargs):
		doc = _doc("Contact", name, self.metas["Contact"].fields, **kwargs)
		self.docs[("Contact", name)] = doc
		self.names["Contact"].append(name)
		self.linked["Contact"].add(name)
		return doc

	def _add_address(self, name, **kwargs):
		doc = _doc("Address", name, self.metas["Address"].fields, **kwargs)
		self.docs[("Address", name)] = doc
		self.names["Address"].append(name)
		self.linked["Address"].add(name)
		return doc

	def test_customer_read_and_print_are_both_required_before_pdf(self):
		for ptype in ("read", "print"):
			with self.subTest(ptype=ptype):
				self.denied_doctypes = {("Customer", ptype)}
				with self.assertRaises(frappe.PermissionError):
					qr.download_customer_qr("CUST-1")
				self.assertIsNone(self.pdf_html)
				self.assertIsNone(self.qr_payload)
		self.db.exists.assert_not_called()

	def test_contact_and_address_doctype_read_are_both_required(self):
		for doctype in ("Contact", "Address"):
			with self.subTest(doctype=doctype):
				self.denied_doctypes = {(doctype, "read")}
				with self.assertRaises(frappe.PermissionError):
					qr.download_customer_qr("CUST-1")
				self.assertIsNone(self.qr_payload)

	def test_every_contact_and_address_needs_link_and_record_read(self):
		unlinked = _doc("Contact", "PRIMARY-NOT-LINKED", self.metas["Contact"].fields,
			first_name="UNLINKED SECRET")
		self.docs[("Contact", unlinked.name)] = unlinked
		self._add_contact("CONTACT-OK", first_name="Allowed", phone="123")
		self._add_contact("CONTACT-PRIVATE", first_name="PRIVATE SECRET")
		self.customer.customer_primary_contact = "CONTACT-PRIVATE"
		self._add_contact("CONTACT-DISABLED", first_name="DISABLED SECRET", disabled=1)
		self.denied_docs.add(("Contact", "CONTACT-PRIVATE"))
		self._add_address("ADDRESS-OK", address_title="Head Office", address_line1="Main Street",
			phone="444", email_id="address@invalid.example")
		self._add_address("ADDRESS-DISABLED", address_line1="HIDDEN STREET", disabled=1)
		qr.download_customer_qr("CUST-1")
		for allowed in ("Allowed", "123", "Head Office", "Main Street", "444", "address@invalid.example"):
			self.assertIn(allowed, self.qr_payload)
		for forbidden in ("UNLINKED SECRET", "PRIVATE SECRET", "DISABLED SECRET", "HIDDEN STREET"):
			self.assertNotIn(forbidden, self.qr_payload)
		self.assertEqual(self.response["type"], "pdf")
		self.assertEqual(self.response["filename"], "Customer-QR.pdf")
		self.assertEqual(self.response["filecontent"], b"%PDF-1.4 test")

	def test_field_and_child_permissions_exclude_restricted_values(self):
		self.customer.meta = Meta(self.customer.meta.fields, masked=("tax_id",))
		self.customer.mobile_no = "CACHED SECRET"
		self.metas["Contact"].permitted.discard("email_id")
		contact = self._add_contact(
			"CONTACT-OK", first_name="Allowed", email_id="PRIVATE@invalid.example",
		)
		phone = _doc("Contact Phone", "PHONE", ("phone",), phone="555")
		blocked_phone = _doc("Contact Phone", "PHONE-SECRET", ("phone",), phone="SECRET PHONE")
		disabled_phone = _doc("Contact Phone", "PHONE-DISABLED", ("phone",),
			phone="DISABLED PHONE", disabled=1)
		contact.phone_nos = [phone, blocked_phone, disabled_phone]
		contact.email_ids = [_doc("Contact Email", "EMAIL", ("email_id",), email_id="OTHER@invalid.example")]
		self.denied_docs.add(("Contact Phone", "PHONE-SECRET"))
		qr.download_customer_qr("CUST-1")
		self.assertIn("555", self.qr_payload)
		for forbidden in ("VAT-1", "PRIVATE@invalid.example", "SECRET PHONE",
			"DISABLED PHONE", "CACHED SECRET"):
			self.assertNotIn(forbidden, self.qr_payload)
		self.assertIn("OTHER@invalid.example", self.qr_payload)

	def test_pdf_html_has_no_untrusted_markup_urls_or_filename(self):
		self.customer.customer_name = '<img src="https://bad.invalid/steal">'
		self.customer.tax_id = "\r\nContent-Disposition: attachment; filename=bad.pdf"
		self._add_contact("CONTACT-OK", first_name="<script>alert(1)</script>")
		qr.download_customer_qr("CUST-1")
		self.assertIn("bad.invalid", self.qr_payload)
		self.assertIn("<script>", self.qr_payload)
		for forbidden in ("bad.invalid", "Content-Disposition", "<script>", "<img src="):
			self.assertNotIn(forbidden, self.pdf_html)
		self.assertEqual(self.response["filename"], "Customer-QR.pdf")
		self.assertIn('src="data:image/svg+xml;base64,', self.pdf_html)

	def test_automatic_print_page_uses_same_permission_checked_inline_qr(self):
		self.customer.customer_name = '<img src="https://bad.invalid/steal">'
		page = qr.get_customer_qr_print_page("CUST-1")
		self.assertIn("bad.invalid", self.qr_payload)
		self.assertNotIn("bad.invalid", page)
		self.assertIn('src="data:image/svg+xml;base64,', page)
		self.assertNotIn("<script", page)
		self.assertIsNone(self.pdf_html)
		self.assertNotIn("filecontent", self.response)

	def test_print_page_denies_missing_record_and_customer_print_permissions(self):
		self.denied_doctypes.add(("Customer", "print"))
		with self.assertRaises(frappe.PermissionError):
			qr.get_customer_qr_print_page("CUST-1")
		self.denied_doctypes.clear()
		self.denied_doctypes.add(("Address", "read"))
		with self.assertRaises(frappe.PermissionError):
			qr.get_customer_qr_print_page("CUST-1")
		self.assertIsNone(self.qr_payload)

	def test_link_count_and_qr_payload_are_bounded(self):
		for i in range(30):
			self._add_contact(f"CONTACT-{i}", first_name="X" * 1000, phone=str(i))
			self._add_address(f"ADDRESS-{i}", address_line1="Y" * 1000, city="Z" * 1000)
		qr.download_customer_qr("CUST-1")
		self.assertLessEqual(len(self.qr_payload.encode("utf-8")), qr.MAX_QR_BYTES)
		self.assertNotIn("Contact 9:", self.qr_payload)
		self.assertNotIn("Address 9:", self.qr_payload)
		self.assertNotIn("CONTACT-9", self.qr_payload)

	def test_pdf_url_is_get_only_and_not_available_to_guests(self):
		self.assertIn(qr.download_customer_qr, frappe.whitelisted)
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[qr.download_customer_qr], ["GET"])
		self.assertIn(qr.get_customer_qr_print_page, frappe.whitelisted)
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[qr.get_customer_qr_print_page], ["POST"])
		self.assertNotIn(qr.download_customer_qr, frappe.guest_methods)
		self.assertNotIn(qr.get_customer_qr_print_page, frappe.guest_methods)
		with patch.object(frappe.local, "session", frappe._dict(user="Guest"), create=True), patch.object(
			frappe, "_", side_effect=lambda text: text
		):
			with self.assertRaises(frappe.PermissionError):
				frappe.is_whitelisted(qr.download_customer_qr)

	def test_invalid_customer_name_is_rejected_before_lookup(self):
		with self.assertRaises(frappe.ValidationError):
			qr.download_customer_qr("x" * 141)
		self.assertIsNone(self.pdf_html)
