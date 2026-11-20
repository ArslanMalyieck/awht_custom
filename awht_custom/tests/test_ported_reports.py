"""Tests for the reports ported from Branch Credit Control."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from awht_custom.credit_control import legacy_reports
from awht_custom.awht_custom.report.branch_financial_exposure import (
    branch_financial_exposure as exposure,
)
from awht_custom.awht_custom.report.unsold_items import unsold_items


class TestLegacyReportScope(unittest.TestCase):
    def test_company_branches_use_company_attribution(self):
        calls = []

        def get_all(doctype, **kwargs):
            calls.append((doctype, kwargs))
            if doctype == "Branch":
                return ["B1", "B2"]
            raise AssertionError(doctype)

        with patch.object(legacy_reports.frappe, "get_meta",
                          return_value=SimpleNamespace(has_field=lambda name: name == "custom_company")), \
                patch.object(legacy_reports.frappe, "get_all", side_effect=get_all), \
                patch.object(legacy_reports, "_user_permission_docs", return_value=None):
            self.assertEqual(legacy_reports.get_report_branches("C1"), ["B1", "B2"])
        self.assertEqual(calls[0][1]["filters"], {"custom_company": "C1"})

    def test_branch_and_warehouse_permissions_intersect_and_deny(self):
        with patch.object(legacy_reports, "_company_branches", return_value=["B1", "B2"]), \
                patch.object(legacy_reports, "_user_permission_docs",
                             side_effect=lambda doctype: {"B1"} if doctype == "Branch" else None):
            self.assertEqual(legacy_reports.get_report_branches("C1"), ["B1"])
            self.assertEqual(legacy_reports.get_report_branches("C1", "B1"), ["B1"])
            with patch.object(legacy_reports.frappe, "throw",
                              side_effect=frappe.PermissionError("branch")):
                with self.assertRaises(frappe.PermissionError):
                    legacy_reports.get_report_branches("C1", "B2")

        with patch.object(legacy_reports.frappe, "get_all",
                          return_value=["WH1", "WH2", "FOREIGN"]), \
                patch.object(legacy_reports, "_user_permission_docs",
                             side_effect=lambda doctype: {"WH1"} if doctype == "Warehouse" else None):
            self.assertEqual(legacy_reports.get_report_warehouses("C1"), ("WH1",))
            self.assertEqual(legacy_reports.get_report_warehouses("C1", "WH1"), ("WH1",))
            with patch.object(legacy_reports.frappe, "throw",
                              side_effect=frappe.PermissionError("warehouse")):
                with self.assertRaises(frappe.PermissionError):
                    legacy_reports.get_report_warehouses("C1", "FOREIGN")

    def test_administrator_and_user_without_permissions_are_not_narrowed(self):
        with patch.object(legacy_reports.frappe, "session", SimpleNamespace(user="Administrator")):
            self.assertIsNone(legacy_reports._user_permission_docs("Branch"))
        with patch.object(legacy_reports.frappe, "session", SimpleNamespace(user="alice")), \
                patch.object(legacy_reports, "get_user_permissions", return_value={}):
            self.assertIsNone(legacy_reports._user_permission_docs("Branch"))


class TestPortedReports(unittest.TestCase):
    def test_branch_financial_exposure_is_company_scoped_and_has_no_hidden_fields(self):
        queries = []

        def sql(query, params, as_dict=False):
            queries.append((query, params))
            if "SUM(b.actual_qty * b.valuation_rate)" in query:
                return [{"stock_value": 100}]
            if "SUM(base_grand_total)" in query:
                return [{"total_sales": 300}]
            if "SUM(base_net_total)" in query:
                return [{"total_sales": 180}]
            if "SUM(base_paid_amount)" in query:
                return [{"total_payments": 90}]
            if "SUM(ABS(" in query:
                return [{"total_cogs": 80}]
            raise AssertionError(query)

        with patch.object(exposure, "resolve_company", return_value="C1"), \
                patch.object(exposure, "validate_period", return_value=("2026-01-01", "2026-01-31")), \
                patch.object(exposure, "get_report_branches", return_value=["B1"]), \
                patch.object(exposure, "get_company_currency", return_value="SAR"), \
                patch.object(exposure, "get_branch_warehouses", return_value=("WH1",)), \
                patch.object(exposure.frappe, "db", SimpleNamespace(sql=sql)):
            columns, data = exposure.execute({"from_date": "2026-01-01", "to_date": "2026-01-31"})

        self.assertEqual([column["fieldname"] for column in columns], [
            "branch", "stock_value", "total_sales", "total_payments",
            "gross_profit", "gross_profit_percent",
        ])
        self.assertEqual({column["options"] for column in columns if column.get("options")}, {"SAR"})
        self.assertEqual(set(data[0]), {column["fieldname"] for column in columns})
        self.assertEqual(data[0]["gross_profit"], 100)
        self.assertAlmostEqual(data[0]["gross_profit_percent"], 55.55555555555556)
        self.assertTrue(all(params["company"] == "C1" for _, params in queries))
        self.assertEqual(queries[0][1]["warehouses"], ("WH1",))
        self.assertIn("w.company = %(company)s", queries[0][0])
        self.assertTrue(all("base_" in query for query, _ in queries[1:4]))
        self.assertTrue(all("company = %(company)s" in query for query, _ in queries[1:]))

    def test_unsold_items_is_company_and_warehouse_scoped(self):
        queries = []

        def sql(query, params, as_dict=False):
            queries.append((query, params))
            return [frappe._dict({
                "item_code": "SKU", "item_name": "Item", "item_group": "Group",
                "brand": None, "stock_uom": "Nos", "stock_qty": 2,
                "stock_value": 50, "last_movement_date": None, "days_since_last": None,
            })]

        filters = frappe._dict({
            "company": "C1", "from_date": "2026-01-01", "to_date": "2026-01-31",
            "based_on": ["Stock Out (All Vouchers)"], "exclude_adjustments": 1,
        })
        with patch.object(unsold_items, "resolve_company", return_value="C1"), \
                patch.object(unsold_items, "get_report_warehouses", return_value=("WH1", "WH2")), \
                patch.object(unsold_items, "get_company_currency", return_value="SAR"), \
                patch.object(unsold_items.frappe, "db", SimpleNamespace(sql=sql)):
            columns, data, _, _, summary = unsold_items.execute(filters)

        self.assertEqual(data[0]["item_code"], "SKU")
        self.assertEqual(summary[-1]["currency"], "SAR")
        self.assertEqual({column["fieldname"] for column in columns}, {
            "item_code", "item_name", "item_group", "brand", "stock_uom",
            "stock_qty", "stock_value", "last_movement_date", "days_since_last",
        })
        query, params = queries[0]
        self.assertEqual(params["company"], "C1")
        self.assertEqual(params["warehouses"], ("WH1", "WH2"))
        for fragment in (
            "w.company = %(company)s",
            "b.warehouse IN %(warehouses)s",
            "sle.company = %(company)s",
            "sle.warehouse IN %(warehouses)s",
        ):
            self.assertIn(fragment, query)
        self.assertNotIn("currency", data[0])

    def test_javascript_keeps_original_filters_and_defaults(self):
        exposure_js = Path(exposure.__file__).with_suffix(".js").read_text()
        self.assertIn('frappe.query_reports["Branch Financial Exposure"]', exposure_js)
        self.assertIn('fieldname: "company"', exposure_js)
        self.assertIn('default: frappe.defaults.get_user_default("Company")', exposure_js)
        self.assertIn('fieldname: "from_date"', exposure_js)
        self.assertIn('fieldname: "to_date"', exposure_js)
        self.assertIn('fieldname: "branch"', exposure_js)
        self.assertIn("custom_company: company", exposure_js)
        self.assertGreaterEqual(exposure_js.count("reqd: 1"), 3)

        unsold_js = Path(unsold_items.__file__).with_suffix(".js").read_text()
        self.assertIn('frappe.query_reports["Unsold Items"]', unsold_js)
        self.assertIn('default: ["Stock Out (All Vouchers)"]', unsold_js)
        self.assertIn("frappe.datetime.add_months(frappe.datetime.get_today(), -3)", unsold_js)
        self.assertIn('fieldname: "only_with_stock"', unsold_js)
        self.assertIn('fieldname: "exclude_adjustments"', unsold_js)
        self.assertIn("default: 1", unsold_js)
        self.assertIn('fieldname: "warehouse"', unsold_js)

    def test_unsold_source_selection_and_date_validation_match_source_report(self):
        self.assertEqual(
            unsold_items.get_sources({"based_on": '["Sales Invoice", "Invalid"]'}),
            ["Sales Invoice"],
        )
        self.assertEqual(unsold_items.get_sources({"based_on": "Invalid"}),
                         ["Stock Out (All Vouchers)"])
        with self.assertRaises(frappe.ValidationError):
            unsold_items.validate_filters(
                frappe._dict({"from_date": "2026-02-01", "to_date": "2026-01-01"})
            )


if __name__ == "__main__":
    unittest.main()
