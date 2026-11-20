"""No-site authorization and native-engine tests for the invoice tax preview."""

import copy
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from awht_custom.operations import invoice_tax


class Record:
	def __init__(self, **values):
		self.__dict__.update(values)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def precision(self, field):
		return 2


class Invoice(Record):
	def __init__(self, **values):
		super().__init__(**values)
		self.meta = MagicMock()
		self.meta.has_field.return_value = True


def _throw(message, exc=frappe.ValidationError, **kwargs):
	raise exc(message)


class TestInvoiceTaxPreview(unittest.TestCase):
	def setUp(self):
		self.payload = {
			"doctype": "Sales Invoice", "name": "SI-1", "docstatus": 0,
			"company": "COMPANY", "customer": "CUSTOMER", "currency": "SAR", "conversion_rate": 1,
			"owner": "FORGED-OWNER", "_action": "submit", "flags": {"ignore_permissions": True},
			"items": [
				{"doctype": "Sales Invoice Item", "name": f"ROW-{i}", "parent": "SI-1",
				 "parenttype": "Sales Invoice", "parentfield": "items", "item_code": f"ITEM-{i}",
				 "qty": 1, "rate": i * 100, "net_amount": i * 100, "item_tax_amount": 999}
				for i in (1, 2)
			],
			"taxes": [
				{"doctype": "Sales Taxes and Charges", "name": name, "parent": "SI-1",
				 "parenttype": "Sales Invoice", "parentfield": "taxes", "rate": 10}
				for name in ("VAT", "CASCADE")
			],
		}
		self.saved = Invoice(name="SI-1", docstatus=0, company="COMPANY", customer="CUSTOMER", currency="SAR",
			items=[Record(name="ROW-1"), Record(name="ROW-2")],
			taxes=[Record(name="VAT"), Record(name="CASCADE")])
		self.scratch_data = None
		self.scratch = None
		self.invoice_exists = True
		self.denied = set()
		self.db = MagicMock()
		self.db.exists.side_effect = self._exists
		self.flags = frappe._dict(round_off_applicable_accounts=["saved"], round_row_wise_tax=9)
		self.meta = {doctype: MagicMock() for doctype in
			("Sales Invoice", "Sales Invoice Item", "Sales Taxes and Charges")}
		self.meta["Sales Invoice"].get_valid_fields.return_value = set(self.payload) | {
			"is_consolidated", "party_account_currency", "is_pos"
		}
		self.meta["Sales Invoice"].get_field.side_effect = lambda name: frappe._dict(
			fieldtype="Check" if name == "is_pos" else "Data"
		)
		self.masked = set()
		self.meta["Sales Invoice Item"].get_valid_fields.return_value = set(self.payload["items"][0]) | {
			"price_list_rate"
		}
		self.meta["Sales Invoice Item"].get_field.side_effect = lambda name: frappe._dict(
			fieldtype="Currency" if name == "price_list_rate" else "Data", mask=name in self.masked
		)
		self.meta["Sales Invoice Item"].get_permitted_fieldnames.return_value = {
			"item_tax_amount", "amount_after_tax", "net_amount", "item_code"
		}
		self.meta["Sales Invoice Item"].has_field.return_value = True
		self.meta["Sales Invoice Item"].get_field.return_value = {"mask": 0}
		self.meta["Sales Taxes and Charges"].get_valid_fields.return_value = set(self.payload["taxes"][0]) | {
			"dont_recompute_tax"
		}
		self.engine = MagicMock(side_effect=self._calculate)

		def permitted(doctype, ptype, doc=None, throw=False, **kwargs):
			key = (doctype, ptype, doc.name if hasattr(doc, "name") else doc)
			if key in self.denied:
				if throw:
					raise frappe.PermissionError("Not permitted")
				return False
			return True

		for p in (
			patch.object(invoice_tax.frappe, "db", self.db),
			patch.object(invoice_tax.frappe, "flags", self.flags),
			patch.object(invoice_tax.frappe, "parse_json", side_effect=json.loads),
			patch.object(invoice_tax.frappe, "get_doc", side_effect=self._get_doc),
			patch.object(invoice_tax.frappe, "get_meta", side_effect=lambda doctype: self.meta[doctype]),
			patch.object(invoice_tax.frappe, "has_permission", side_effect=permitted),
			patch.object(invoice_tax.frappe, "throw", side_effect=_throw),
			patch.object(invoice_tax, "_", side_effect=lambda text: text),
			patch.object(invoice_tax.erpnext, "get_company_currency", return_value="SAR"),
			patch.object(invoice_tax.frappe, "get_system_settings", return_value="Banker's Rounding (legacy)"),
			patch.dict(sys.modules, {"erpnext.controllers.taxes_and_totals": SimpleNamespace(
				calculate_taxes_and_totals=self.engine
			)}),
		):
			p.start()
			self.addCleanup(p.stop)

	def _exists(self, doctype, name):
		if doctype == "DocType":
			return name if name == "Item Wise Tax Detail" else None
		if doctype == "Sales Invoice":
			return name if self.invoice_exists and name == "SI-1" else None
		if doctype in ("Sales Invoice Item", "Sales Taxes and Charges"):
			return name if name == "OTHER-ROW" else None
		return None

	def _get_doc(self, *args):
		if len(args) == 2:
			self.assertEqual(args, ("Sales Invoice", "SI-1"))
			return self.saved
		self.scratch_data = copy.deepcopy(args[0])
		self.scratch = Invoice(**args[0])
		self.scratch.items = [Record(**row) for row in args[0]["items"]]
		self.scratch.taxes = [Record(**row) for row in args[0]["taxes"]]
		return self.scratch

	def _calculate(self, scratch):
		self.flags.round_off_applicable_accounts = ["changed"]
		self.flags.round_row_wise_tax = 1
		if not scratch.taxes:
			scratch._item_wise_tax_details = []
			return
		sign = -1 if scratch.get("is_return") else 1
		if sign < 0:
			for item in scratch.items:
				item.net_amount = -abs(item.net_amount)
		for tax, total in zip(scratch.taxes, (30, 16.5)):
			tax.base_tax_amount_after_discount_amount = sign * total
		scratch._item_wise_tax_details = [
			frappe._dict(item=scratch.items[item], tax=scratch.taxes[tax], amount=sign * value)
			for item, tax, value in ((0, 0, 10), (1, 0, 20), (0, 1, 5.5), (1, 1, 11))
		]

	def _preview(self):
		return invoice_tax.preview_item_taxes(json.dumps(self.payload))

	def test_saved_draft_uses_native_breakdown_without_writes_or_private_input(self):
		result = self._preview()
		self.assertEqual(result, [
			{"name": "ROW-1", "item_tax_amount": 15.5, "amount_after_tax": 115.5},
			{"name": "ROW-2", "item_tax_amount": 31.0, "amount_after_tax": 231.0},
		])
		self.assertNotIn("owner", self.scratch_data)
		self.assertNotIn("flags", self.scratch_data)
		self.assertNotIn("_action", self.scratch_data)
		self.assertNotIn("parent", self.scratch_data["items"][0])
		self.assertNotIn("item_tax_amount", self.scratch_data["items"][0])
		self.assertIsNone(self.scratch_data["party_account_currency"])
		self.assertEqual(self.scratch_data["is_pos"], 0)
		self.assertEqual(self.scratch_data["items"][0]["price_list_rate"], 0)
		self.assertEqual(self.saved.docstatus, 0)
		self.assertEqual(self.flags.round_off_applicable_accounts, ["saved"])
		self.assertEqual(self.flags.round_row_wise_tax, 9)
		self.db.sql.assert_not_called()
		self.db.set_value.assert_not_called()
		self.engine.assert_called_once_with(self.scratch)

	def test_new_invoice_requires_create_read_company_customer_and_item(self):
		self.invoice_exists = False
		self.payload["name"] = "new-sales-invoice"
		self.payload["__islocal"] = 1
		for row in [*self.payload["items"], *self.payload["taxes"]]:
			row["parent"] = "new-sales-invoice"
			row["__islocal"] = 1
		self._preview()
		self.engine.assert_called_once()
		self.assertTrue(self.scratch_data["__islocal"])
		for dt, ptype, name in (("Sales Invoice", "create", None), ("Company", "read", "COMPANY"),
			("Customer", "read", "CUSTOMER"), ("Item", "read", "ITEM-1")):
			with self.subTest(dt=dt, ptype=ptype):
				self.engine.reset_mock()
				self.denied = {(dt, ptype, name)}
				with self.assertRaises(frappe.PermissionError):
					self._preview()
				self.engine.assert_not_called()

	def test_returns_use_native_signed_breakdown_and_no_tax_is_zero(self):
		self.payload["is_return"] = 1
		self.meta["Sales Invoice"].get_valid_fields.return_value.add("is_return")
		self.assertEqual(self._preview(), [
			{"name": "ROW-1", "item_tax_amount": -15.5, "amount_after_tax": -115.5},
			{"name": "ROW-2", "item_tax_amount": -31.0, "amount_after_tax": -231.0},
		])
		self.payload["is_return"] = 0
		self.payload["taxes"] = []
		self.engine.reset_mock()
		self.assertEqual(self._preview(), [
			{"name": "ROW-1", "item_tax_amount": 0, "amount_after_tax": 100},
			{"name": "ROW-2", "item_tax_amount": 0, "amount_after_tax": 200},
		])

	def test_pending_tax_template_or_withholding_never_previews_false_zero(self):
		self.payload["taxes"] = []
		self.payload["taxes_and_charges"] = "PENDING-TEMPLATE"
		expected = [
			{"name": f"ROW-{i}", "item_tax_amount": None, "amount_after_tax": None} for i in (1, 2)
		]
		self.assertEqual(self._preview(), expected)
		self.payload.pop("taxes_and_charges")
		self.payload["items"][0]["item_tax_template"] = "PENDING-ITEM-TEMPLATE"
		self.assertEqual(self._preview(), expected)
		self.payload["items"][0].pop("item_tax_template")
		self.payload["taxes"] = [
			{"doctype": "Sales Taxes and Charges", "name": "VAT", "rate": 10},
			{"doctype": "Sales Taxes and Charges", "name": "CASCADE", "rate": 10},
		]
		self.saved.apply_tds = 1
		self.assertEqual(self._preview(), expected)
		self.engine.assert_not_called()

	def test_unapplied_withholding_group_does_not_disable_native_preview(self):
		self.payload["tax_withholding_group"] = "GROUP"
		self.saved.tax_withholding_group = "GROUP"
		self.assertEqual(self._preview()[0]["item_tax_amount"], 15.5)
		self.engine.assert_called_once()

	def test_currency_changed_on_saved_draft_stays_blank_until_saved(self):
		self.saved.currency = "USD"
		self.assertEqual(self._preview(), [
			{"name": f"ROW-{i}", "item_tax_amount": None, "amount_after_tax": None} for i in (1, 2)
		])
		self.engine.assert_not_called()

	def test_saved_requires_current_session_read_and_write_even_with_islocal_spoof(self):
		self.payload["__islocal"] = 1
		for permission in ("read", "write"):
			self.denied = {("Sales Invoice", permission, "SI-1")}
			with self.subTest(permission=permission), self.assertRaises(frappe.PermissionError):
				self._preview()
		self.engine.assert_not_called()

	def test_submitted_and_forged_identity_are_denied_before_calculation(self):
		for key, value in (("docstatus", 1), ("company", "OTHER"), ("customer", "OTHER")):
			with self.subTest(key=key):
				self.payload[key] = value
				with self.assertRaises(frappe.PermissionError):
					self._preview()
				self.engine.assert_not_called()
				self.payload[key] = 0 if key == "docstatus" else key.upper()
		self.saved.docstatus = 1
		with self.assertRaises(frappe.PermissionError):
			self._preview()
		self.engine.assert_not_called()

	def test_child_or_referenced_item_read_denial_prevents_preview(self):
		for doctype, name in (("Sales Invoice Item", "ROW-1"), ("Sales Taxes and Charges", "VAT"),
			("Item", "ITEM-1")):
			with self.subTest(doctype=doctype):
				self.denied = {(doctype, "read", name)}
				with self.assertRaises(frappe.PermissionError):
					self._preview()
				self.engine.assert_not_called()

	def test_masked_display_and_non_recomputed_tax_fail_closed(self):
		self.masked.add("item_tax_amount")
		with self.assertRaises(frappe.PermissionError):
			self._preview()
		self.masked.clear()
		self.saved.taxes[0].dont_recompute_tax = 1
		with self.assertRaises(frappe.ValidationError):
			self._preview()
		self.engine.assert_not_called()

	def test_absent_or_unreadable_display_fields_do_not_invoke_native_calculator(self):
		self.meta["Sales Invoice Item"].has_field.side_effect = lambda field: field != "amount_after_tax"
		with self.assertRaises(frappe.ValidationError):
			self._preview()
		self.meta["Sales Invoice Item"].has_field.side_effect = None
		self.meta["Sales Invoice Item"].get_permitted_fieldnames.return_value = {"item_code", "net_amount"}
		with self.assertRaises(frappe.PermissionError):
			self._preview()
		self.engine.assert_not_called()

	def test_rejects_foreign_duplicate_and_unsaved_row_spoofs(self):
		for value in ("OTHER-ROW", "ROW-1"):
			self.payload["items"][1]["name"] = value
			with self.subTest(value=value), self.assertRaises(frappe.PermissionError):
				self._preview()
			self.engine.assert_not_called()
		self.payload["items"][1]["name"] = "ROW-2"
		self.payload["items"][0]["parent"] = "OTHER-INVOICE"
		with self.assertRaises(frappe.PermissionError):
			self._preview()

	def test_invalid_and_oversized_payload_fails_before_native_calculation(self):
		for value in ("not-json", "{}", "X" * (invoice_tax.MAX_PREVIEW_BYTES + 1)):
			with self.subTest(value=value[:20]), self.assertRaises(frappe.ValidationError):
				invoice_tax.preview_item_taxes(value)
		with self.assertRaises(frappe.ValidationError):
			invoice_tax.preview_item_taxes({"doctype": "Sales Invoice", "payload": "X" * invoice_tax.MAX_PREVIEW_BYTES})
		self.engine.assert_not_called()

	def test_preview_is_post_only_and_not_guest(self):
		self.assertIn(invoice_tax.preview_item_taxes, frappe.whitelisted)
		self.assertNotIn(invoice_tax.preview_item_taxes, frappe.guest_methods)
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[invoice_tax.preview_item_taxes],
			["POST"])

	def test_native_calculation_failure_restores_request_flags_without_writing(self):
		def fail(scratch):
			self.flags.round_row_wise_tax = 1
			raise frappe.ValidationError("Native tax calculation failed")

		self.engine.side_effect = fail
		with self.assertRaises(frappe.ValidationError):
			self._preview()
		self.assertEqual(self.flags.round_row_wise_tax, 9)
		self.db.sql.assert_not_called()
		self.db.set_value.assert_not_called()

	def test_v15_site_without_native_schema_never_runs_preview_calculator(self):
		self.meta["Sales Invoice"].has_field.return_value = False
		self.assertEqual(self._preview(), [
			{"name": row["name"], "item_tax_amount": None, "amount_after_tax": None}
			for row in self.payload["items"]
		])
		self.engine.assert_not_called()
