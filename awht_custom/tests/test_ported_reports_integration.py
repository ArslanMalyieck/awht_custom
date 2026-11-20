"""Read-only execution of the ported reports on the configured test site."""

import unittest

import frappe

try:
    from frappe.tests import IntegrationTestCase
except ImportError:
    from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from awht_custom.awht_custom.report.branch_financial_exposure import (
    branch_financial_exposure,
)
from awht_custom.awht_custom.report.unsold_items import unsold_items


class TestPortedReportsOnSite(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for name in ("Branch Financial Exposure", "Unsold Items"):
            if not frappe.db.exists("Report", name):
                raise unittest.SkipTest("Ported reports are not installed")

    def company(self):
        company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
            "Global Defaults", "default_company"
        )
        if not company:
            companies = frappe.get_all("Company", pluck="name", limit=1)
            company = companies[0] if companies else None
        if not company:
            self.skipTest("No Company on this site")
        return company

    def test_report_records_keep_original_names_roles_and_owner_module(self):
        expected = {
            "Branch Financial Exposure": {
                "ref_doctype": "Sales Invoice",
                "roles": {
                    "Accounts User", "Accounts Manager",
                    "Employee Self Service", "Sales Master Manager",
                },
            },
            "Unsold Items": {
                "ref_doctype": "Stock Ledger Entry",
                "roles": {"Stock User", "Accounts Manager", "Report User awht"},
            },
        }
        for name, details in expected.items():
            with self.subTest(report=name):
                report = frappe.get_doc("Report", name)
                self.assertEqual(report.module, "AWHT Custom")
                self.assertEqual(report.report_type, "Script Report")
                self.assertEqual(report.ref_doctype, details["ref_doctype"])
                self.assertEqual(report.is_standard, "Yes")
                self.assertFalse(report.disabled)
                self.assertFalse(report.prepared_report)
                self.assertEqual({row.role for row in report.roles}, details["roles"])

    def test_desk_loads_ported_javascript_after_migration(self):
        for name in ("Branch Financial Exposure", "Unsold Items"):
            with self.subTest(report=name):
                script = frappe.desk.query_report.get_script(name)["script"]
                self.assertIn(f'frappe.query_reports["{name}"]', script)

    def test_branch_financial_exposure_runs_with_company_and_branch_filters(self):
        company = self.company()
        bounds = frappe.db.sql(
            """
            SELECT MIN(posting_date) AS from_date, MAX(posting_date) AS to_date
            FROM `tabSales Invoice`
            WHERE company = %(company)s AND docstatus = 1
            """,
            {"company": company},
            as_dict=True,
        )[0]
        if not bounds.from_date:
            self.skipTest("No submitted Sales Invoice on this site")
        branches = frappe.get_all(
            "Branch", filters={"custom_company": company}, pluck="name", limit=1
        )
        if not branches:
            self.skipTest("No company branch on this site")
        branch = branches[0]

        data = frappe.desk.query_report.run(
            "Branch Financial Exposure",
            filters={
                "company": company,
                "from_date": bounds.from_date,
                "to_date": bounds.to_date,
                "branch": branch,
            },
        )

        self.assertEqual([column["fieldname"] for column in data["columns"]], [
            "branch", "stock_value", "total_sales", "total_payments",
            "gross_profit", "gross_profit_percent",
        ])
        self.assertEqual(len(data["result"]), 1)
        self.assertEqual(data["result"][0]["branch"], branch)
        self.assertEqual(
            set(data["result"][0]),
            {column["fieldname"] for column in data["columns"]},
        )

    def test_unsold_items_runs_with_original_filters(self):
        company = self.company()
        latest = frappe.db.sql(
            """
            SELECT MAX(posting_date) AS posting_date
            FROM `tabStock Ledger Entry`
            WHERE company = %(company)s
            """,
            {"company": company},
            as_dict=True,
        )[0].posting_date
        if not latest:
            self.skipTest("No Stock Ledger Entry on this site")
        from_date = frappe.utils.add_days(latest, -90)
        item = frappe.db.sql(
            """
            SELECT b.item_code
            FROM `tabBin` b
            INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
            WHERE w.company = %(company)s AND b.actual_qty > 0
            LIMIT 1
            """,
            {"company": company},
            as_dict=True,
        )
        filters = {
            "company": company,
            "from_date": from_date,
            "to_date": latest,
            "based_on": ["Stock Out (All Vouchers)"],
            "exclude_adjustments": 1,
        }
        if item:
            filters["item_code"] = item[0].item_code

        data = frappe.desk.query_report.run("Unsold Items", filters=filters)

        self.assertEqual([column["fieldname"] for column in data["columns"]], [
            "item_code", "item_name", "item_group", "brand", "stock_uom",
            "stock_qty", "stock_value", "last_movement_date", "days_since_last",
        ])
        self.assertIsNotNone(data.get("report_summary"))
        currency = frappe.db.get_value("Company", company, "default_currency")
        self.assertEqual(data["report_summary"][-1]["currency"], currency)

    def test_execute_without_company_uses_default_company(self):
        company = self.company()
        bounds = frappe.db.sql(
            """
            SELECT MAX(posting_date) AS to_date
            FROM `tabStock Ledger Entry`
            WHERE company = %(company)s
            """,
            {"company": company},
            as_dict=True,
        )[0].to_date
        if not bounds:
            self.skipTest("No Stock Ledger Entry on this site")
        filters = {"from_date": frappe.utils.add_days(bounds, -30), "to_date": bounds}
        columns, rows, _, _, summary = unsold_items.execute(filters)
        self.assertTrue(columns)
        self.assertIsInstance(rows, list)
        self.assertEqual(summary[-1]["currency"],
                         frappe.db.get_value("Company", company, "default_currency"))
        exposure_columns, exposure_rows = branch_financial_exposure.execute({
            "from_date": frappe.utils.add_days(bounds, -30), "to_date": bounds,
        })
        self.assertTrue(exposure_columns)
        self.assertIsInstance(exposure_rows, list)
