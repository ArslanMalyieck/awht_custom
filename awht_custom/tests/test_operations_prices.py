"""No-site tests for the previous-transfer-prices API."""

import unittest
from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

import frappe

from awht_custom.operations import transfer_prices as prices


def _row(name, parent, price=12, source="A", target="B", uom="Nos"):
	return frappe._dict(
		name=name, parent=parent, parenttype="Stock Entry", parentfield="items",
		item_code="ITEM", s_warehouse=source, t_warehouse=target, uom=uom,
		qty=2, salesman_price=price, basic_rate=9999, valuation_rate=8888,
	)


def _entry(name, row, posting_date="2026-11-15", posting_time="08:00:00", status=1):
	return frappe._dict(
		name=name, docstatus=status, purpose="Material Transfer", stock_entry_type="Other Transfer",
		company="Company", posting_date=posting_date, posting_time=posting_time, items=[row],
	)


def _throw(message, exc=frappe.ValidationError):
	raise exc(message)


class TestPreviousTransferPrices(unittest.TestCase):
	def setUp(self):
		self.current = _entry("CURRENT", _row("ROW", "CURRENT"), "2026-11-16", "09:00:00", 0)
		self.docs = {"CURRENT": self.current}
		self.db = MagicMock()
		self.db.has_column.return_value = True
		self.db.sql.return_value = []
		self.item_meta = MagicMock()
		self.item_meta.has_field.return_value = True
		self.item_meta.get_field.return_value = {"mask": 0}
		self.item_meta.get_permitted_fieldnames.return_value = {
			"item_code", "s_warehouse", "t_warehouse", "uom", "qty", "salesman_price",
		}
		self.entry_meta = MagicMock()
		self.entry_meta.get_field.return_value = {"mask": 0}
		self.entry_meta.get_permitted_fieldnames.return_value = {
			"posting_date", "posting_time", "company", "purpose",
		}
		self.denied = set()

		def permitted(doctype, ptype, doc=None, throw=False):
			allowed = (doctype, doc.name if doc else None) not in self.denied
			if not allowed and throw:
				raise frappe.PermissionError("Not permitted")
			return allowed

		for p in (
			patch.object(prices.frappe, "db", self.db),
			patch.object(prices.frappe, "get_doc", side_effect=lambda dt, name: self.docs[name]),
			patch.object(prices.frappe, "get_meta", side_effect=lambda dt: self.item_meta
				if dt == "Stock Entry Detail" else self.entry_meta),
			patch.object(prices.frappe, "has_permission", side_effect=permitted),
			patch.object(prices.frappe, "get_roles", return_value=[prices.PRICES_ROLE]),
			patch.object(prices.frappe, "throw", side_effect=_throw),
			patch.object(prices, "_", side_effect=lambda text: text),
			patch.object(prices, "now_datetime", return_value=datetime(2026, 11, 18, 12)),
		):
			p.start()
			self.addCleanup(p.stop)

	def _candidate(self, name="PRIOR", **kwargs):
		doc = _entry(name, _row(f"{name}-ROW", name, **kwargs))
		self.docs[name] = doc
		return frappe._dict(stock_entry=name, detail_name=doc.get("items")[0].name)

	def test_query_uses_actual_row_fields_purpose_and_strict_timestamp(self):
		self.current.from_warehouse = "WRONG HEADER"
		self.current.to_warehouse = "WRONG HEADER"
		self.db.sql.return_value = [self._candidate()]
		rows = prices.get_previous_transfer_prices("CURRENT", "ROW", limit="50000")
		self.assertEqual(rows[0]["salesman_price"], 12)
		self.assertEqual(set(rows[0]), {
			"stock_entry", "docstatus", "posting_date", "posting_time", "qty", "uom", "salesman_price"
		})
		query, params = self.db.sql.call_args.args
		query = " ".join(query.lower().split())
		for predicate in (
			"se.purpose = 'material transfer'", "se.docstatus in (0, 1)",
			"se.name != %(current)s", "d.s_warehouse = %(source)s",
			"d.t_warehouse = %(target)s", "d.uom = %(uom)s",
			"se.posting_time < %(time)s", "limit %(scan_limit)s",
		):
			self.assertIn(predicate, query)
		self.assertNotIn("basic_rate", query)
		self.assertNotIn("valuation_rate", query)
		self.assertNotIn("stock_entry_type", query)
		self.assertEqual(params["source"], "A")
		self.assertEqual(params["target"], "B")
		self.assertEqual(params["date"], date(2026, 11, 16))
		self.assertEqual(params["time"], time(9))
		self.assertEqual(params["scan_limit"], prices.MAX_SCAN)

	def test_rechecks_candidate_permissions_state_and_row_warehouses(self):
		current = frappe._dict(stock_entry="CURRENT", detail_name="ROW")
		future = self._candidate("FUTURE")
		self.docs["FUTURE"].posting_date = "2026-11-17"
		cancelled = self._candidate("CANCELLED")
		self.docs["CANCELLED"].docstatus = 2
		wrong_warehouse = self._candidate("OTHER_WAREHOUSE", source="C")
		wrong_uom = self._candidate("OTHER_UOM", uom="Box")
		wrong_company = self._candidate("OTHER_COMPANY")
		self.docs["OTHER_COMPANY"].company = "Another Company"
		wrong_purpose = self._candidate("OTHER_PURPOSE")
		self.docs["OTHER_PURPOSE"].purpose = "Material Receipt"
		zero_price = self._candidate("ZERO_PRICE", price=0)
		denied_parent = self._candidate("DENIED_PARENT")
		denied_child = self._candidate("DENIED_CHILD")
		good = self._candidate("GOOD")
		self.denied = {("Stock Entry", "DENIED_PARENT"), ("Stock Entry Detail", "DENIED_CHILD-ROW")}
		self.db.sql.return_value = [
			current, future, cancelled, wrong_warehouse, wrong_uom, wrong_company,
			wrong_purpose, zero_price, denied_parent, denied_child, good
		]
		self.assertEqual(
			[r["stock_entry"] for r in prices.get_previous_transfer_prices("CURRENT", "ROW")],
			["GOOD"],
		)

	def test_same_timestamp_and_missing_posting_time_are_not_prior(self):
		same = self._candidate("SAME")
		self.docs["SAME"].posting_date = self.current.posting_date
		self.docs["SAME"].posting_time = self.current.posting_time
		missing = self._candidate("MISSING_TIME")
		self.docs["MISSING_TIME"].posting_time = None
		self.db.sql.return_value = [same, missing]
		self.assertEqual(prices.get_previous_transfer_prices("CURRENT", "ROW"), [])

	def test_authorized_draft_and_submitted_prior_rows_are_both_visible(self):
		draft = self._candidate("PRIOR-DRAFT")
		self.docs["PRIOR-DRAFT"].docstatus = 0
		submitted = self._candidate("PRIOR-SUBMITTED")
		self.db.sql.return_value = [draft, submitted]
		self.assertEqual(
			[(row["stock_entry"], row["docstatus"]) for row in prices.get_previous_transfer_prices("CURRENT", "ROW")],
			[("PRIOR-DRAFT", 0), ("PRIOR-SUBMITTED", 1)],
		)

	def test_future_dated_current_entry_is_clipped_to_now(self):
		self.current.posting_date = "2026-11-30"
		prices.get_previous_transfer_prices("CURRENT", "ROW")
		params = self.db.sql.call_args.args[1]
		self.assertEqual(params["date"], date(2026, 11, 18))
		self.assertEqual(params["time"], time(12))

	def test_result_and_scan_limits_are_bounded(self):
		self.db.sql.return_value = [self._candidate(f"ENTRY{i}") for i in range(3)]
		self.assertEqual(len(prices.get_previous_transfer_prices("CURRENT", "ROW", limit=1)), 1)
		self.assertEqual(self.db.sql.call_args.args[1]["scan_limit"], 5)
		for invalid in ("not-a-number", [], {}):
			with self.subTest(invalid=invalid), self.assertRaises(frappe.ValidationError):
				prices.get_previous_transfer_prices("CURRENT", "ROW", limit=invalid)

	def test_missing_role_blocks_all_db_access(self):
		with patch.object(prices.frappe, "get_roles", return_value=[]):
			with self.assertRaises(frappe.PermissionError):
				prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.db.sql.assert_not_called()

	def test_sensitive_json_endpoint_is_post_only_and_not_public(self):
		self.assertIn(prices.get_previous_transfer_prices, frappe.whitelisted)
		self.assertNotIn(prices.get_previous_transfer_prices, frappe.guest_methods)
		self.assertEqual(
			frappe.allowed_http_methods_for_whitelisted_func[prices.get_previous_transfer_prices],
			["POST"],
		)

	def test_current_parent_and_child_permissions_are_required(self):
		for doctype, name in (("Stock Entry", "CURRENT"), ("Stock Entry Detail", "ROW")):
			with self.subTest(doctype=doctype):
				self.denied = {(doctype, name)}
				with self.assertRaises(frappe.PermissionError):
					prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.db.sql.assert_not_called()

	def test_row_must_belong_to_saved_draft_transfer(self):
		with self.assertRaises(frappe.PermissionError):
			prices.get_previous_transfer_prices("CURRENT", "ANOTHER-ROW")
		self.current.purpose = "Material Receipt"
		with self.assertRaises(frappe.ValidationError):
			prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.current.purpose = "Material Transfer"
		self.current.docstatus = 1
		with self.assertRaises(frappe.ValidationError):
			prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.db.sql.assert_not_called()

	def test_row_warehouses_and_posting_timestamp_are_required(self):
		self.current.get("items")[0].s_warehouse = None
		with self.assertRaises(frappe.ValidationError):
			prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.current.get("items")[0].s_warehouse = "A"
		self.current.posting_time = None
		with self.assertRaises(frappe.ValidationError):
			prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.db.sql.assert_not_called()

	def test_missing_custom_field_fails_clearly(self):
		for meta_exists, column_exists in ((False, True), (True, False)):
			with self.subTest(meta_exists=meta_exists, column_exists=column_exists):
				self.item_meta.has_field.return_value = meta_exists
				self.db.has_column.return_value = column_exists
				with self.assertRaisesRegex(frappe.ValidationError, "salesman_price"):
					prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.db.sql.assert_not_called()

	def test_field_level_restrictions_block_query(self):
		self.item_meta.get_permitted_fieldnames.return_value = {"item_code", "uom"}
		with self.assertRaises(frappe.PermissionError):
			prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.item_meta.get_permitted_fieldnames.return_value = {
			"item_code", "s_warehouse", "t_warehouse", "uom", "qty", "salesman_price"
		}
		self.entry_meta.get_permitted_fieldnames.return_value = {"company", "purpose"}
		with self.assertRaises(frappe.PermissionError):
			prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.db.sql.assert_not_called()

	def test_masked_price_or_wrong_child_parenttype_is_not_disclosed(self):
		self.item_meta.get_field.return_value = {"mask": 1}
		with self.assertRaises(frappe.PermissionError):
			prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.item_meta.get_field.return_value = {"mask": 0}
		self.current.get("items")[0].parenttype = "Another Doctype"
		with self.assertRaises(frappe.PermissionError):
			prices.get_previous_transfer_prices("CURRENT", "ROW")
		self.db.sql.assert_not_called()
