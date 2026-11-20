"""Display-only tax extraction: native v16 details and the guarded v15 fallback."""

import json
import unittest
from unittest.mock import MagicMock, patch

import frappe

from awht_custom.operations import invoice_tax


def _item(name, net, **kwargs):
	row = frappe._dict({
		"name": name, "item_code": "SAME-ITEM", "net_amount": net,
		"item_tax_amount": 999, "amount_after_tax": 999, **kwargs,
	})
	row.precision = lambda field: 2
	return row


def _tax(name, base, **kwargs):
	tax = frappe._dict({"name": name, "base_tax_amount_after_discount_amount": base, **kwargs})
	tax.precision = lambda field: 2
	return tax


def _detail(item, tax, amount):
	return frappe._dict(item=item, tax=tax, amount=amount)


class _Doc:
	def __init__(self, **values):
		self.__dict__.update(values)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


class TestInvoiceItemTaxDisplay(unittest.TestCase):
	def setUp(self):
		self.items = [_item("ROW-1", 100), _item("ROW-2", 200)]
		self.taxes = [_tax("VAT", 30, included_in_print_rate=1),
			_tax("CASCADE", 16.5, charge_type="On Previous Row Total")]
		self.doc = frappe._dict(
			doctype="Sales Invoice", name="SI-1", company="Company", currency="SAR", conversion_rate=1,
			items=self.items, taxes=self.taxes, grand_total=346.5, total_taxes_and_charges=46.5,
		)
		self.doc.meta = MagicMock()
		self.doc.meta.has_field.return_value = True
		self.doc.precision = lambda field: 2
		self.doc._item_wise_tax_details = [
			_detail(self.items[0], self.taxes[0], 10),
			_detail(self.items[1], self.taxes[0], 20),
			_detail(self.items[0], self.taxes[1], 5.5),
			_detail(self.items[1], self.taxes[1], 11),
		]
		self.meta = MagicMock()
		self.meta.has_field.return_value = True
		self.db = MagicMock()
		for p in (
			patch.object(invoice_tax.frappe, "get_meta", return_value=self.meta),
			patch.object(invoice_tax.frappe, "db", self.db),
			patch.object(invoice_tax.erpnext, "get_company_currency", return_value="SAR"),
			patch.object(invoice_tax.frappe, "get_system_settings", return_value="Banker's Rounding (legacy)"),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_duplicate_item_codes_inclusive_and_cascaded_native_details(self):
		before = (self.doc.grand_total, self.doc.total_taxes_and_charges,
			[(t.name, t.base_tax_amount_after_discount_amount) for t in self.taxes])
		invoice_tax.set_item_tax_display(self.doc, "validate")
		self.assertEqual([(r.item_tax_amount, r.amount_after_tax) for r in self.items],
			[(15.5, 115.5), (31.0, 231.0)])
		self.assertEqual(before, (self.doc.grand_total, self.doc.total_taxes_and_charges,
			[(t.name, t.base_tax_amount_after_discount_amount) for t in self.taxes]))
		self.assertEqual(len(self.doc._item_wise_tax_details), 4)

	def test_missing_custom_field_does_not_create_or_modify_the_other(self):
		self.meta.has_field.side_effect = lambda name: name != "amount_after_tax"
		invoice_tax.set_item_tax_display(self.doc)
		self.assertEqual([(r.item_tax_amount, r.amount_after_tax) for r in self.items],
			[(999, 999), (999, 999)])

	def test_onload_uses_persisted_native_item_wise_tax_detail_rows(self):
		self.doc.item_wise_tax_details = [
			frappe._dict(item_row=d.item.name, tax_row=d.tax.name, amount=d.amount)
			for d in self.doc._item_wise_tax_details
		]
		self.doc._item_wise_tax_details = []
		invoice_tax.set_item_tax_display(self.doc, "onload")
		self.assertEqual([(r.item_tax_amount, r.amount_after_tax) for r in self.items],
			[(15.5, 115.5), (31.0, 231.0)])
		self.db.sql.assert_not_called()

	def test_onload_hides_legacy_values_when_native_table_is_missing(self):
		self.doc.item_wise_tax_details = []
		invoice_tax.set_item_tax_display(self.doc, "onload")
		self.assertEqual([(r.item_tax_amount, r.amount_after_tax) for r in self.items],
			[(None, None), (None, None)])
		self.db.sql.assert_not_called()

	def test_missing_native_details_clear_stale_display(self):
		for details in ([], self.doc._item_wise_tax_details[:1]):
			with self.subTest(details=len(details)):
				self.doc._item_wise_tax_details = details
				invoice_tax.set_item_tax_display(self.doc)
				self.assertEqual([(r.item_tax_amount, r.amount_after_tax) for r in self.items],
					[(None, None), (None, None)])

	def test_rounding_mismatch_and_duplicate_details_fail_closed(self):
		self.doc._item_wise_tax_details[0].amount = 9
		invoice_tax.set_item_tax_display(self.doc)
		self.assertIsNone(self.items[0].item_tax_amount)
		self.doc._item_wise_tax_details[0].amount = 10
		self.doc._item_wise_tax_details[1].item = self.items[0]
		invoice_tax.set_item_tax_display(self.doc)
		self.assertIsNone(self.items[0].amount_after_tax)

	def test_row_rounding_that_does_not_sum_to_native_total_is_blank(self):
		self.doc.items = [self.items[0], self.items[1]]
		self.doc.taxes = [self.taxes[0]]
		self.taxes[0].base_tax_amount_after_discount_amount = 0.01
		self.doc._item_wise_tax_details = [
			_detail(self.items[0], self.taxes[0], 0.005),
			_detail(self.items[1], self.taxes[0], 0.005),
		]
		invoice_tax.set_item_tax_display(self.doc)
		self.assertIsNone(self.items[0].item_tax_amount)

	def test_multicurrency_never_guesses_row_conversion(self):
		for currency, rate in (("USD", 1300), ("USD", 1), ("SAR", 0)):
			with self.subTest(currency=currency, rate=rate):
				self.doc.currency = currency
				self.doc.conversion_rate = rate
				invoice_tax.set_item_tax_display(self.doc)
				self.assertIsNone(self.items[0].item_tax_amount)
				self.assertIsNone(self.items[1].amount_after_tax)

	def test_no_tax_rows_can_show_zero_and_net_in_transaction_currency(self):
		self.doc.taxes = []
		self.doc.currency = "USD"
		self.doc.conversion_rate = 1300
		self.doc._item_wise_tax_details = []
		invoice_tax.set_item_tax_display(self.doc)
		self.assertEqual([(r.item_tax_amount, r.amount_after_tax) for r in self.items],
			[(0, 100), (0, 200)])

	def test_v15_missing_native_table_leaves_original_values_untouched(self):
		self.doc.meta.has_field.return_value = False
		invoice_tax.set_item_tax_display(self.doc)
		self.assertEqual([(r.item_tax_amount, r.amount_after_tax) for r in self.items],
			[(999, 999), (999, 999)])
		self.doc.meta.has_field.return_value = True
		self.doc.is_cash_or_non_trade_discount = 1
		self.doc.apply_discount_on = "Grand Total"
		self.doc.discount_amount = 10
		invoice_tax.set_item_tax_display(self.doc)
		self.assertIsNone(self.items[0].amount_after_tax)

	def test_v15_missing_native_doctype_never_clears_live_values(self):
		self.db.exists.return_value = False
		invoice_tax.set_item_tax_display(self.doc)
		self.assertEqual([(r.item_tax_amount, r.amount_after_tax) for r in self.items],
			[(999, 999), (999, 999)])
		self.doc.item_wise_tax_details = []
		invoice_tax.set_item_tax_display(self.doc, "onload")
		self.assertEqual([(r.item_tax_amount, r.amount_after_tax) for r in self.items],
			[(999, 999), (999, 999)])
		self.db.sql.assert_not_called()

	def test_non_finite_and_non_recomputed_native_details_are_not_displayed(self):
		self.doc._item_wise_tax_details[0].amount = float("nan")
		invoice_tax.set_item_tax_display(self.doc)
		self.assertIsNone(self.items[0].item_tax_amount)
		self.doc._item_wise_tax_details[0].amount = 10
		self.taxes[0].dont_recompute_tax = 1
		invoice_tax.set_item_tax_display(self.doc)
		self.assertIsNone(self.items[0].item_tax_amount)

	def test_display_only_never_writes_to_the_invoice(self):
		invoice_tax.set_item_tax_display(self.doc, "validate")
		self.db.sql.assert_not_called()


class TestV15ItemTaxFallback(unittest.TestCase):
	def setUp(self):
		self.items = [
			_item("ROW-1", 100, qty=2, item_tax_rate=json.dumps({"VAT": 0})),
			_item("ROW-2", 200, qty=3, item_tax_rate=json.dumps({"VAT": 5})),
		]
		self.taxes = [
			_tax("VAT", 0, account_head="VAT", charge_type="On Net Total", rate=5),
			_tax("CASCADE", 0, charge_type="On Previous Row Total", rate=10, row_id=1),
			_tax("DEDUCT", 0, charge_type="On Net Total", rate=2, add_deduct_tax="Deduct"),
			_tax("VAL", 0, charge_type="On Net Total", rate=15, category="Valuation"),
		]
		self.doc = _Doc(
			doctype="Sales Invoice", name="SI-1", net_total=300,
			items=self.items, taxes=self.taxes, grand_total=335,
			total_taxes_and_charges=35,
		)
		self.doc.meta = MagicMock()
		self.doc.meta.has_field.return_value = False
		self.doc.precision = lambda field: 2
		self.item_meta = MagicMock()
		self.item_meta.has_field.return_value = True
		self.db = MagicMock()
		self.db.exists.return_value = False
		for p in (
			patch.object(invoice_tax.frappe, "get_meta", return_value=self.item_meta),
			patch.object(invoice_tax.frappe, "db", self.db),
			patch.object(invoice_tax.erpnext, "get_company_currency", return_value="SAR"),
			patch.object(invoice_tax.frappe, "get_system_settings", return_value="Banker's Rounding (legacy)"),
		):
			p.start()
			self.addCleanup(p.stop)

	def _values(self):
		return [(row.item_tax_amount, row.amount_after_tax) for row in self.items]

	def test_v15_fallback_covers_legacy_charge_types_and_item_rates(self):
		before = (self.doc.grand_total, self.doc.total_taxes_and_charges)
		invoice_tax.set_item_tax_display(self.doc, "validate")
		self.assertEqual(self._values(), [(8.0, 108.0), (27.0, 227.0)])
		self.assertEqual((self.doc.grand_total, self.doc.total_taxes_and_charges), before)
		self.db.sql.assert_not_called()
		self.db.set_value.assert_not_called()

	def test_v15_fallback_distributes_actual_and_previous_row_amounts(self):
		self.taxes[:] = [
			_tax("ACTUAL", 0, charge_type="Actual", tax_amount=30),
			_tax("PREV-AMOUNT", 0, charge_type="On Previous Row Amount", rate=10, row_id=1),
		]
		invoice_tax.set_item_tax_display(self.doc, "validate")
		self.assertEqual(self._values(), [(11.0, 111.0), (22.0, 222.0)])

	def test_v15_fallback_uses_quantity_charge(self):
		self.taxes[:] = [_tax("QTY", 0, charge_type="On Item Quantity", rate=7)]
		invoice_tax.set_item_tax_display(self.doc, "validate")
		self.assertEqual(self._values(), [(14.0, 114.0), (21.0, 221.0)])

	def test_v15_fallback_invalid_item_tax_rate_uses_tax_row_rate(self):
		self.items[0].item_tax_rate = "not-json"
		self.taxes[:] = [
			_tax("VAT", 0, account_head="VAT", charge_type="On Net Total", rate=5),
		]
		invoice_tax.set_item_tax_display(self.doc, "validate")
		self.assertEqual(self._values(), [(5.0, 105.0), (10.0, 210.0)])

	def test_v15_fallback_is_idempotent_on_repeated_validate(self):
		invoice_tax.set_item_tax_display(self.doc, "validate")
		expected = self._values()
		invoice_tax.set_item_tax_display(self.doc, "validate")
		self.assertEqual(self._values(), expected)

	def test_v15_onload_keeps_persisted_historical_values(self):
		invoice_tax.set_item_tax_display(self.doc, "onload")
		self.assertEqual(self._values(), [(999, 999), (999, 999)])

	def test_v15_fallback_matches_installed_legacy_formula_in_any_order(self):
		try:
			from awht_customizations.sales_invoice import set_item_wise_tax as legacy
		except ImportError:
			self.skipTest("awht_customizations is not installed")
		legacy(self.doc)
		expected = self._values()
		invoice_tax.set_item_tax_display(self.doc, "validate")
		self.assertEqual(self._values(), expected)
		invoice_tax.set_item_tax_display(self.doc, "validate")
		self.assertEqual(self._values(), expected)
		legacy(self.doc)
		self.assertEqual(self._values(), expected)
