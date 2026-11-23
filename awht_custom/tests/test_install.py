"""Smoke the shared integration contract without touching a Frappe site."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from awht_custom import hooks, install


class TestAppSetup(unittest.TestCase):
    def test_hooks_are_registered_once(self):
        self.assertEqual(hooks.required_apps, ["erpnext"])
        self.assertEqual(hooks.doc_events["Stock Entry"]["before_submit"],
                         "awht_custom.credit_control.stock_entry.validate_stock_entry")
        self.assertEqual(hooks.doc_events["Sales Invoice"]["before_validate"],
                         "awht_custom.credit_control.invoice.map_invoice_branch_warehouse")
        self.assertEqual(hooks.override_whitelisted_methods["frappe.desk.query_report.run"],
                         "awht_custom.visibility.report_filter.run")
        self.assertEqual(hooks.override_whitelisted_methods[
            "frappe.core.api.file.zip_files"], "awht_custom.visibility.file_filter.zip_files")
        self.assertEqual(hooks.extend_doctype_class["File"],
                         ["awht_custom.visibility.file_filter.PreparedReportFileMixin"])
        self.assertEqual(len(hooks.app_include_js), len(set(hooks.app_include_js)))
        self.assertIn("awht_custom.install.sync_ported_reports", hooks.after_install)
        self.assertIn("awht_custom.install.sync_ported_reports", hooks.after_migrate)

    def test_setup_creates_missing_fields_and_role_without_overwriting_existing(self):
        def get_meta(doctype):
            return SimpleNamespace(get_field=lambda field: SimpleNamespace(
                fieldtype="Link", options="Branch") if doctype == "Sales Invoice" and field == "custom_branch" else None)

        db = SimpleNamespace(exists=MagicMock(return_value=False))
        with patch.object(install.frappe, "get_meta", side_effect=get_meta), \
             patch.object(install.frappe, "db", db), \
             patch.object(install.frappe, "get_doc") as get_doc, \
             patch.object(install, "create_custom_fields") as create_fields:
            install.setup()
        fields = create_fields.call_args.args[0]
        self.assertEqual(create_fields.call_args.kwargs, {"update": False})
        self.assertEqual({field["fieldname"] for field in fields["Address"]}, {"additional_number", "street_name"})
        self.assertEqual({field["fieldname"] for field in fields["Customer"]}, {"phone_number"})
        self.assertEqual({field["fieldname"] for field in fields["POS Invoice"]}, {"custom_branch", "update_stock"})
        self.assertEqual({field["fieldname"] for field in fields["Sales Invoice"]}, {"company_cr", "custom_customer_cr"})
        self.assertEqual({field["fieldname"] for field in fields["Stock Entry Detail"]}, {"salesman_price", "old_price"})
        self.assertEqual({field["fieldname"] for field in fields["Material Request Item"]}, {"salesman_price", "old_price"})
        self.assertEqual({field["fieldname"] for field in fields["Sales Invoice Item"]},
                         {"salesman_price", "item_tax_amount", "amount_after_tax"})
        self.assertEqual({field["fieldname"] for field in fields["Quotation Item"]}, {"salesman_price"})
        self.assertEqual({field["fieldname"] for field in fields["Sales Order Item"]}, {"salesman_price"})
        self.assertEqual(get_doc.call_args.args[0]["role_name"], "Previous Transfer Prices Viewer")
        get_doc.return_value.insert.assert_called_once_with(ignore_permissions=True)

    def test_ported_report_sync_repairs_missing_or_foreign_ownership(self):
        cases = (
            (False, None, 2),
            (True, "AWHT Custom", 0),
            (True, "Branch Credit Control", 2),
        )
        for exists, module, expected in cases:
            with self.subTest(exists=exists, module=module), \
                    patch.object(install.frappe, "get_app_path", return_value="report.json"), \
                    patch.object(install.os.path, "exists", return_value=True), \
                    patch.object(install.frappe, "db", SimpleNamespace(
                        exists=MagicMock(return_value=exists),
                        get_value=MagicMock(return_value=module),
                    )), \
                    patch.object(install.frappe, "clear_cache") as clear_cache, \
                    patch("frappe.modules.import_file.import_file_by_path") as importer:
                install.sync_ported_reports()
            self.assertEqual(importer.call_count, expected)
            if expected:
                importer.assert_called_with("report.json", force=True, ignore_version=True)
                clear_cache.assert_called_once_with(doctype="Report")
            else:
                clear_cache.assert_not_called()

    def test_incompatible_existing_field_fails_closed(self):
        def get_meta(doctype):
            return SimpleNamespace(get_field=lambda field: SimpleNamespace(fieldtype="Data", options=None)
                                   if doctype == "Stock Entry Detail" and field == "salesman_price" else None)

        with patch.object(install.frappe, "get_meta", side_effect=get_meta), \
             patch.object(install.frappe, "throw", side_effect=frappe.ValidationError), \
             patch.object(install, "create_custom_fields") as create_fields:
            with self.assertRaises(frappe.ValidationError):
                install.setup()
        create_fields.assert_not_called()
