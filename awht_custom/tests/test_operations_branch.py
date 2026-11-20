"""Explicit invoice branch lookup: permission gates and canonical service delegation."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from awht_custom.operations import invoice_branch


class Record:
	def __init__(self, **values):
		self.__dict__.update(values)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def _throw(message, exc=frappe.ValidationError, **kwargs):
	raise exc(message)


class TestInvoiceBranchLookup(unittest.TestCase):
	def setUp(self):
		self.saved = Record(name="SI-1", docstatus=0, company="C1", is_return=0)
		self.denied = set()
		self.mapped = "WH-1"
		self.probe = None
		self.mapping = MagicMock(side_effect=self._map)

		def get_doc(*args):
			if len(args) == 2:
				self.assertEqual(args, ("Sales Invoice", "SI-1"))
				return self.saved
			self.probe = Record(**args[0])
			return self.probe

		def permitted(doctype, ptype, doc=None, throw=False):
			if (doctype, ptype, doc if isinstance(doc, str) else getattr(doc, "name", None)) in self.denied:
				if throw:
					raise frappe.PermissionError("Not permitted")
				return False
			return True

		for p in (
			patch.object(invoice_branch.frappe, "get_doc", side_effect=get_doc),
			patch.object(invoice_branch.frappe, "has_permission", side_effect=permitted),
			patch.object(invoice_branch.frappe, "throw", side_effect=_throw),
			patch.object(invoice_branch.credit_invoice, "map_invoice_branch_warehouse", self.mapping),
			patch.object(invoice_branch, "_", side_effect=lambda text: text),
		):
			p.start()
			self.addCleanup(p.stop)

	def _map(self, doc):
		doc.set_warehouse = self.mapped

	def test_saved_draft_uses_only_canonical_mapping(self):
		result = invoice_branch.get_branch_warehouse("C1", "B1", invoice="SI-1")
		self.assertEqual(result, "WH-1")
		self.mapping.assert_called_once_with(self.probe)
		self.assertEqual(self.probe.custom_branch, "B1")

	def test_saved_read_write_denial_and_unpermitted_company_block_mapping(self):
		for permission in ("read", "write"):
			self.denied = {("Sales Invoice", permission, "SI-1")}
			with self.assertRaises(frappe.PermissionError):
				invoice_branch.get_branch_warehouse("C1", "B1", invoice="SI-1")
		self.denied = {("Company", "read", "C2")}
		with self.assertRaises(frappe.PermissionError):
			invoice_branch.get_branch_warehouse("C2", "B1", invoice="SI-1")
		self.mapping.assert_not_called()

	def test_saved_company_change_is_checked_against_explicit_new_company_mapping(self):
		self.assertEqual(invoice_branch.get_branch_warehouse("C2", "B1", invoice="SI-1"), "WH-1")
		self.assertEqual(self.probe.company, "C2")
		self.mapping.assert_called_once_with(self.probe)

	def test_new_invoice_requires_create_company_branch_and_warehouse_read(self):
		for doctype, ptype, name in (("Sales Invoice", "create", None), ("Sales Invoice", "read", None),
			("Company", "read", "C1"),
			("Branch", "read", "B1"), ("Warehouse", "read", "WH-1")):
			with self.subTest(doctype=doctype):
				self.denied = {(doctype, ptype, name)}
				with self.assertRaises(frappe.PermissionError):
					invoice_branch.get_branch_warehouse("C1", "B1")
				self.mapping.reset_mock()

	def test_returns_and_submitted_invoices_do_not_get_mapping(self):
		self.assertIsNone(invoice_branch.get_branch_warehouse("C1", "B1", is_return=1))
		self.saved.is_return = 1
		with self.assertRaises(frappe.PermissionError):
			invoice_branch.get_branch_warehouse("C1", "B1", invoice="SI-1")
		self.saved.is_return = 0
		self.saved.docstatus = 1
		with self.assertRaises(frappe.PermissionError):
			invoice_branch.get_branch_warehouse("C1", "B1", invoice="SI-1")
		self.mapping.assert_not_called()

	def test_invalid_mapping_errors_are_not_replaced_with_fuzzy_match(self):
		self.mapping.side_effect = frappe.ValidationError("Multiple warehouses")
		with self.assertRaises(frappe.ValidationError):
			invoice_branch.get_branch_warehouse("C1", "B1", invoice="SI-1")

	def test_lookup_is_post_only(self):
		self.assertIn(invoice_branch.get_branch_warehouse, frappe.whitelisted)
		self.assertNotIn(invoice_branch.get_branch_warehouse, frappe.guest_methods)
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[invoice_branch.get_branch_warehouse],
			["POST"])
