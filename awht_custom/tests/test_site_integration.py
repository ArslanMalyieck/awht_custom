"""Read-only, staged-site checks; never run against a working production site."""

import json
import unittest

import frappe

try:
    from frappe.tests import IntegrationTestCase
except ImportError:
    from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from awht_custom.install import CUSTOM_FIELDS


class TestAWHTStagedSite(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        if not (getattr(frappe.local, "site", "") or "").startswith("awhtstage"):
            raise unittest.SkipTest("Staged-site tests require a private awhtstage site")
        super().setUpClass()

    def test_field_and_module_ownership(self):
        self.assertIn("awht_custom", frappe.get_installed_apps())
        self.assertEqual(frappe.get_meta("Cost Visibility Settings").module, "AWHT Custom")
        for doctype, fields in CUSTOM_FIELDS.items():
            meta = frappe.get_meta(doctype)
            for field in fields:
                with self.subTest(doctype=doctype, field=field["fieldname"]):
                    self.assertEqual(meta.get_field(field["fieldname"]).fieldtype, field["fieldtype"])
        self.assertTrue(frappe.db.exists("Role", "Previous Transfer Prices Viewer"))

    def test_effective_hooks_and_file_mixin(self):
        overrides = frappe.get_hooks("override_whitelisted_methods")
        self.assertEqual(overrides["frappe.desk.query_report.run"][-1],
                         "awht_custom.visibility.report_filter.run")
        self.assertEqual(overrides["frappe.desk.reportview.get"][-1],
                         "awht_custom.visibility.report_builder.get")
        self.assertIn("PreparedReportFileMixin", [cls.__name__ for cls in frappe.new_doc("File").__class__.__mro__])

    def test_restricted_user_cannot_download_raw_prepared_file(self):
        from awht_custom.visibility.api import get_policy

        users = frappe.get_doc("Cost Visibility Settings").restricted_users
        if not users:
            self.skipTest("No restricted user configured on this staged site")
        original = frappe.session.user
        try:
            frappe.set_user(users[0].user)
            self.assertTrue(get_policy()["restricted"])
            attachment = frappe.get_doc({
                "doctype": "File", "file_url": "/private/files/awht-staging-raw.json.gz",
                "is_private": 1, "attached_to_doctype": "Prepared Report",
                "attached_to_name": "AWHT-STAGING-PREPARED",
            })
            self.assertFalse(attachment.is_downloadable())
            self.assertFalse(frappe.has_permission("File", "read", doc=attachment))
        finally:
            frappe.set_user(original)

    def test_branded_query_report_pdf_is_a_real_pdf(self):
        from awht_custom.pdf_export.report_pdf import download_report_pdf

        original = frappe.session.user
        try:
            frappe.set_user("Administrator")
            download_report_pdf(
                report_name="General Ledger",
                filters={"company": "BOT Solutions", "from_date": "2030-01-01", "to_date": "2030-01-02"},
                letter_head="__none__", words_column="__off__",
            )
            self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))
        finally:
            frappe.set_user(original)

    def test_branded_report_view_pdf_refetches_data(self):
        from awht_custom.pdf_export.report_view import download_report_view_pdf

        original = frappe.session.user
        try:
            frappe.set_user("Administrator")
            download_report_view_pdf(
                doctype="Company", fields=json.dumps([["name", "Company"]]),
                filters="[]", row_count="2", letter_head="__none__", words_column="__off__",
            )
            self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))
        finally:
            frappe.set_user(original)
