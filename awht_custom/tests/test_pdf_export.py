"""Site-free tests: no site initialization, DB writes, or wkhtmltopdf process."""

import base64
import json
import logging
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import frappe
from jinja2 import Environment, FileSystemLoader

import awht_custom.visibility as visibility
from awht_custom.pdf_export import report_pdf as pdf


class TestBrandedReportPDF(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(
            frappe.local, "session", frappe._dict(user="pdf.user@example.test"), create=True
        ))
        self.enterContext(patch.object(frappe.local, "response", frappe._dict(), create=True))
        self.enterContext(patch.object(
            frappe.local, "flags", frappe._dict(mute_messages=True), create=True
        ))
        self.enterContext(patch.object(frappe.local, "db", Mock(), create=True))

        self.roles_allowed = True
        self.report = frappe._dict(
            name="General Ledger", is_standard="Yes", module="Accounts",
            report_type="Script Report", ref_doctype="GL Entry", disabled=0,
            prepared_report=0, filters=[], is_permitted=lambda: self.roles_allowed,
        )
        self.letter_head = frappe._dict(content="<b>Test brand</b>", footer="", disabled=0)
        self.file_doc = None
        self.permissions = {}
        self.result = {
            "columns": [{"label": "Entry", "fieldname": "entry", "fieldtype": "Data"}],
            "result": [{"entry": "safe"}],
        }
        self.runner = Mock(return_value=self.result)
        self.visibility_module = types.ModuleType("awht_custom.visibility.report_filter")
        self.visibility_module.run = self.runner
        self.enterContext(patch.dict(sys.modules, {
            "awht_custom.visibility.report_filter": self.visibility_module
        }))
        self.enterContext(patch.object(visibility, "report_filter", self.visibility_module, create=True))

        self.get_doc = self.enterContext(patch.object(frappe, "get_doc", side_effect=self._get_doc))
        self.has_permission = self.enterContext(
            patch.object(frappe, "has_permission", side_effect=self._has_permission)
        )
        self.get_all = self.enterContext(patch.object(frappe, "get_all", return_value=[]))
        self.enterContext(patch.object(
            frappe, "get_meta", return_value=types.SimpleNamespace(get_field=lambda name: None)
        ))
        self.enterContext(patch.object(
            frappe, "get_site_path", side_effect=lambda *parts: "/srv/sites/test/" + "/".join(parts)
        ))
        self.enterContext(patch.object(frappe.defaults, "get_user_default", return_value=None))
        self.enterContext(patch.object(pdf, "formatdate", side_effect=str))
        self.enterContext(patch.object(pdf, "nowdate", return_value="2026-11-18"))
        env = Environment(loader=FileSystemLoader(Path(pdf.__file__).parent), autoescape=False)
        self.enterContext(patch.object(
            frappe, "render_template",
            side_effect=lambda path, context: env.get_template(Path(path).name).render(context),
        ))
        self.enterContext(patch.object(frappe, "logger", return_value=logging.getLogger("pdf-test")))
        self.get_pdf = self.enterContext(
            patch("frappe.utils.pdf.get_pdf", return_value=b"%PDF-1.4\nmock\n")
        )
        self.bypass_hosts = self.enterContext(patch(
            "frappe.utils.print_format._pdf_bypass_proxy_hosts", return_value=["localhost"]
        ))
        self.access_log = self.enterContext(
            patch("frappe.core.doctype.access_log.access_log.make_access_log")
        )

    def _get_doc(self, doctype, name):
        if doctype == "Report":
            return self.report
        if doctype == "Letter Head":
            return self.letter_head
        if doctype == "File":
            return self.file_doc
        raise AssertionError(f"Unexpected document: {doctype} {name}")

    def _has_permission(self, doctype, ptype, doc=None):
        return self.permissions.get((doctype, ptype), True)

    def export(self, **kwargs):
        args = {"filters": {"company": "Acme"}, "letter_head": "__none__"}
        args.update(kwargs)
        pdf.download_report_pdf("General Ledger", **args)
        return self.get_pdf.call_args.args[0] if self.get_pdf.called else None

    def test_report_roles_print_and_report_are_required_for_any_supported_report(self):
        with self.assertRaises(frappe.PermissionError):
            pdf.download_report_pdf("Stock Balance", letter_head="__none__")
        with self.assertRaises(frappe.PermissionError):
            pdf.download_report_pdf(["General Ledger"], letter_head="__none__")
        self.assertFalse(pdf.can_download_report_pdf("Stock Balance"))
        for restriction in ("roles", "print", "report", "disabled", "unsupported_type"):
            with self.subTest(restriction=restriction):
                self.roles_allowed = restriction != "roles"
                self.report.disabled = int(restriction == "disabled")
                self.report.report_type = "Report Builder" if restriction == "unsupported_type" else "Script Report"
                self.permissions[("GL Entry", "print")] = restriction != "print"
                self.permissions[("GL Entry", "report")] = restriction != "report"
                with self.assertRaises(frappe.PermissionError):
                    self.export()
                self.assertFalse(pdf.can_download_report_pdf("General Ledger"))
                self.runner.assert_not_called()
        self.assertFalse(self.get_pdf.called)

    def test_non_accounting_and_custom_reports_are_authorized_by_report_roles(self):
        self.report.name = "Stock Balance"
        self.report.ref_doctype = "Bin"
        self.report.report_type = "Query Report"
        self.assertTrue(pdf.can_download_report_pdf("Stock Balance"))
        self.roles_allowed = False
        self.assertFalse(pdf.can_download_report_pdf("Stock Balance"))
        self.roles_allowed = True
        self.permissions[("Bin", "print")] = False
        self.assertFalse(pdf.can_download_report_pdf("Stock Balance"))
        self.permissions[("Bin", "print")] = True
        self.report.name = "Customer Balance Custom"
        self.report.ref_doctype = "Customer"
        self.report.report_type = "Custom Report"
        reference = frappe._dict(report_type="Script Report", ref_doctype="Customer", prepared_report=0)
        with patch.object(pdf, "get_report_doc", return_value=reference) as core_authorize:
            self.assertTrue(pdf.can_download_report_pdf("Customer Balance Custom"))
            core_authorize.assert_called_once_with("Customer Balance Custom")
        self.roles_allowed = False
        self.assertFalse(pdf.can_download_report_pdf("Customer Balance Custom"))
        self.runner.assert_not_called()

    def test_link_permission_is_checked_before_running_without_client_js_filters(self):
        self.permissions[("Company", "read")] = False
        self.permissions[("Company", "select")] = False
        with self.assertRaises(frappe.PermissionError):
            self.export()
        self.runner.assert_not_called()
        self.permissions[("Company", "select")] = True
        self.export()
        self.runner.assert_called_once_with(
            "General Ledger", filters={"company": "Acme"}, are_default_filters=False
        )

    def test_generic_report_saved_link_filters_are_validated_without_browser_metadata(self):
        self.report.filters = [frappe._dict(
            fieldname="department", fieldtype="Link", options="Department"
        )]
        self.permissions[("Department", "read")] = False
        self.permissions[("Department", "select")] = False
        with self.assertRaises(frappe.PermissionError):
            self.export(filters={"department": "Secret"})
        self.runner.assert_not_called()
        self.permissions[("Department", "select")] = True
        self.export(filters={"department": "Public"})
        self.runner.assert_called_once_with(
            "General Ledger", filters={"department": "Public"}, are_default_filters=False
        )

    def test_permission_denied_by_visibility_runner_does_not_fall_back_to_core(self):
        self.runner.side_effect = frappe.PermissionError("Cost Visibility denied report")
        with patch("frappe.desk.query_report.run") as raw_runner:
            with self.assertRaises(frappe.PermissionError):
                self.export()
            raw_runner.assert_not_called()
        self.get_pdf.assert_not_called()
        self.access_log.assert_not_called()

    def test_filtered_columns_and_rows_are_the_only_values_rendered(self):
        self.runner.return_value = {
            "columns": [{"label": "Document", "fieldname": "document"}],
            "result": [{"document": "Allowed", "purchase_rate": "COST SECRET"}],
        }
        html = self.export(only_fields='["document"]')
        self.assertIn("Allowed", html)
        self.assertNotIn("COST SECRET", html)
        self.assertNotIn("purchase_rate", html)
        self.runner.assert_called_once_with(
            "General Ledger", filters={"company": "Acme"}, are_default_filters=False
        )
        self.get_pdf.reset_mock()
        with self.assertRaises(frappe.ValidationError):
            self.export(only_fields=["purchase_rate"])
        self.get_pdf.assert_not_called()

    def test_report_values_and_letterhead_markup_do_not_inject_html(self):
        self.runner.return_value = {
            "columns": [{"label": '<svg onload="alert(1)">', "fieldname": "entry"}],
            "result": [{"entry": '<img src="http://localhost/a" onerror="alert(2)">'}],
        }
        self.letter_head.content = (
            '<p style="background-image:url(http://127.0.0.1/admin)" onclick="bad()">'
            'Safe brand</p><script>alert(3)</script><a href="https://evil.test/">Link</a>'
        )
        html = self.export(filters={"company": "<script>x</script>"}, letter_head="Brand")
        self.assertIn("&lt;svg onload=&quot;alert(1)&quot;&gt;", html)
        self.assertIn("&lt;img src=&quot;http://localhost/a&quot;", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)
        self.assertIn("Safe brand", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("style=", html)
        self.assertNotIn("onclick=", html)
        self.assertNotIn("https://evil.test/", html)
        self.assertNotIn("http://127.0.0.1/admin", html)

    def test_reject_external_traversal_and_data_letterhead_images(self):
        for src in (
            "http://127.0.0.1/internal", "https://evil.test/logo.png",
            "file:///etc/passwd", "/private/files/../secret.png", "data:image/svg+xml;base64,abc",
            "//localhost/files/logo.png", "/private/files/logo%2Fsecret.png",
            "/private/files/logo.png?fid=logo&debug=1",
        ):
            with self.subTest(src=src):
                self.letter_head.content = f'<img src="{src}">'
                with self.assertRaises(frappe.PermissionError):
                    self.export(letter_head="Brand")
                self.get_all.assert_not_called()
        self.get_pdf.assert_not_called()

    def test_simple_letter_head_alignment_survives_but_css_urls_do_not(self):
        self.letter_head.content = (
            '<div style="text-align: center; color: #123456; '
            'background-image: url(http://127.0.0.1/private)">Brand</div>'
        )
        html = self.export(letter_head="Brand")
        self.assertIn("text-align:center", html)
        self.assertIn("color:#123456", html)
        self.assertNotIn("url(", html)
        self.assertNotIn("http://127.0.0.1/private", html)

    def test_private_file_id_and_encoded_public_filename_require_exact_file(self):
        content = b"GIF89aIMAGE"
        self.letter_head.content = '<img src="/private/files/logo.png?fid=logo">'
        self.get_all.return_value = [frappe._dict(name="logo")]
        self.file_doc = types.SimpleNamespace(
            name="logo", file_url="/private/files/logo.png", is_private=1, file_size=len(content),
            get_full_path=Mock(return_value="/srv/sites/test/private/files/logo.png"),
            get_content=Mock(return_value=content),
        )
        self.assertIn("data:image/gif;base64,", self.export(letter_head="Brand"))
        self.assertEqual(self.get_all.call_args.kwargs["filters"], {
            "file_url": "/private/files/logo.png", "name": "logo",
        })

        self.letter_head.content = '<img src="/files/brand%20logo.png">'
        self.file_doc.file_url = "/files/brand logo.png"
        self.file_doc.is_private = 0
        self.file_doc.get_full_path.return_value = "/srv/sites/test/public/files/brand logo.png"
        self.assertIn("data:image/gif;base64,", self.export(letter_head="Brand"))
        self.assertEqual(self.get_all.call_args.kwargs["filters"], {
            "file_url": "/files/brand logo.png",
        })

    def test_letter_head_and_private_file_require_individual_read_permission(self):
        self.permissions[("Letter Head", "read")] = False
        with self.assertRaises(frappe.PermissionError):
            self.export(letter_head="Brand")
        self.get_all.assert_not_called()
        self.permissions[("Letter Head", "read")] = True
        self.permissions[("File", "read")] = False
        self.letter_head.content = '<img src="/private/files/logo.png">'
        self.get_all.return_value = [frappe._dict(name="logo")]
        self.file_doc = types.SimpleNamespace(
            file_url="/private/files/logo.png", is_private=1, file_size=12,
            get_full_path=Mock(return_value="/srv/sites/test/private/files/logo.png"),
            get_content=Mock(return_value=b"\x89PNG\r\n\x1a\nMORE"),
        )
        with self.assertRaises(frappe.PermissionError):
            self.export(letter_head="Brand")
        self.file_doc.get_content.assert_not_called()
        self.get_pdf.assert_not_called()

    def test_permitted_local_image_is_inlined_and_symlink_escape_is_rejected(self):
        self.letter_head.content = '<img src="/private/files/logo.png" onerror="bad()">'
        self.get_all.return_value = [frappe._dict(name="logo")]
        content = b"\x89PNG\r\n\x1a\nMORE"
        self.file_doc = types.SimpleNamespace(
            file_url="/private/files/logo.png", is_private=1, file_size=len(content),
            get_full_path=Mock(return_value="/srv/sites/test/private/files/logo.png"),
            get_content=Mock(return_value=content),
        )
        html = self.export(letter_head="Brand")
        self.assertIn("data:image/png;base64," + base64.b64encode(content).decode(), html)
        self.assertNotIn("/private/files/logo.png", html)
        self.assertNotIn("onerror=", html)
        self.file_doc.get_content.assert_called_once_with()

        self.get_pdf.reset_mock()
        self.file_doc.get_content.reset_mock()
        self.file_doc.get_full_path.return_value = "/etc/passwd"
        with self.assertRaises(frappe.PermissionError):
            self.export(letter_head="Brand")
        self.file_doc.get_content.assert_not_called()
        self.get_pdf.assert_not_called()

    def test_hidden_columns_keep_row_alignment_and_invalid_selections_fail_closed(self):
        self.runner.return_value = {
            "columns": [
                {"label": "First", "fieldname": "first"},
                {"label": "Secret", "fieldname": "hidden", "hidden": "1"},
                {"label": "Last", "fieldname": "last"},
            ],
            "result": [["FIRST", "TOP SECRET", "LAST"]],
        }
        html = self.export(only_fields=json.dumps(["last"]))
        self.assertIn("LAST", html)
        self.assertNotIn("FIRST", html)
        self.assertNotIn("TOP SECRET", html)
        self.get_pdf.reset_mock()
        for selected in (["hidden"], ["nonexistent"], ["last", "last"], []):
            with self.subTest(selected=selected):
                with self.assertRaises(frappe.ValidationError):
                    self.export(only_fields=selected)
        self.get_pdf.assert_not_called()
        html = self.export()
        self.assertIn("FIRST", html)
        self.assertIn("LAST", html)
        self.assertNotIn("TOP SECRET", html)

    def test_returned_total_row_is_not_recomputed_or_converted_to_words(self):
        self.runner.return_value = {
            "columns": [{"label": "Amount", "fieldname": "amount", "fieldtype": "Currency"}],
            "result": [{"amount": "10.005"}, {"amount": "10.005", "is_total_row": True}],
            "add_total_row": 1,
        }
        html = self.export()
        self.assertEqual(html.count("10.005"), 2)
        self.assertNotIn("Amount in Words", html)

    def test_sar_words_use_only_selected_visible_rows_not_totals_or_hidden_amounts(self):
        from num2words import num2words

        self.runner.return_value = {
            "columns": [
                {"label": "Hidden", "fieldname": "hidden", "hidden": 1, "fieldtype": "Currency",
                 "options": "SAR"},
                {"label": "Amount", "fieldname": "amount", "fieldtype": "Currency",
                 "options": "SAR"},
            ],
            "result": [
                {"hidden": 9999, "amount": "1.005"},
                {"hidden": 9999, "amount": "0.00"},
                {"hidden": 9999, "amount": "2.00", "bold": 1},
                {"hidden": 9999, "amount": "1.00"},
                {"hidden": 9999, "amount": "100.00", "is_total_row": 1},
            ],
            "add_total_row": 1,
        }
        html = self.export(only_fields=["amount"], words_column="__auto__", words_mode="sum")
        self.assertIn(num2words(2.01, lang="en", to="currency", currency="SAR"), html)
        self.assertIn("class=\"arabic\"", html)
        self.assertIn("1.01", html)
        self.assertNotIn("9999", html)
        self.get_pdf.reset_mock()
        with self.assertRaises(frappe.ValidationError):
            self.export(only_fields=["amount"], words_column="hidden")
        self.get_pdf.assert_not_called()

    def test_sar_words_last_nonzero_specific_float_and_mixed_currency_errors(self):
        from num2words import num2words

        self.runner.return_value = {
            "columns": [
                {"label": "Currency", "fieldname": "currency"},
                {"label": "Amount", "fieldname": "amount", "fieldtype": "Currency",
                 "options": "currency"},
                {"label": "Quantity", "fieldname": "qty", "fieldtype": "Float"},
            ],
            "result": [
                {"currency": "SAR", "amount": -12.34, "qty": 5},
                {"currency": "SAR", "amount": 0, "qty": 0},
            ],
        }
        html = self.export(only_fields=["amount"], words_column="amount", words_mode="last")
        self.assertIn("minus " + num2words(12.34, lang="en", to="currency", currency="SAR"), html)
        self.assertNotIn("Quantity", html)
        self.runner.return_value["result"][1]["currency"] = "USD"
        with self.assertRaisesRegex(frappe.ValidationError, "single SAR currency"):
            self.export(only_fields=["amount"], words_column="amount", words_mode="sum")
        self.runner.return_value["result"][1].pop("currency")
        with self.assertRaisesRegex(frappe.ValidationError, "currency is unavailable"):
            self.export(only_fields=["amount"], words_column="amount")
        self.runner.return_value["columns"][1]["options"] = "SAR"
        self.runner.return_value["columns"][1]["precision"] = 3
        with self.assertRaisesRegex(frappe.ValidationError, "precision"):
            self.export(only_fields=["amount"], words_column="amount")

    def test_auto_amount_words_skips_when_currency_metadata_is_unavailable(self):
        self.runner.return_value = {
            "columns": [
                {"label": "Amount", "fieldname": "amount", "fieldtype": "Currency",
                 "options": "currency"},
            ],
            "result": [{"amount": "10.00"}],
        }
        html = self.export(only_fields=["amount"], words_column="__auto__")
        self.assertIsNotNone(html)
        self.get_pdf.assert_called_once()
        self.assertNotIn("Ten Saudi Riyals", html)

    def test_sar_zero_and_fractional_amounts_have_currency_words_in_both_languages(self):
        self.runner.return_value = {
            "columns": [{"label": "Amount", "fieldname": "amount", "fieldtype": "Currency",
                         "options": "SAR"}],
            "result": [{"amount": "0.01"}],
        }
        html = self.export(words_column="__auto__")
        self.assertIn("halala", html.lower())
        self.assertIn("\u0635\u0641\u0631 \u0631\u064a\u0627\u0644", html)
        self.runner.return_value["result"] = [{"amount": "0.00"}]
        html = self.export(words_column="amount")
        self.assertIn("\u0635\u0641\u0631 \u0631\u064a\u0627\u0644", html)

    def test_long_column_labels_and_total_cell_budget_are_rejected(self):
        self.runner.return_value = {
            "columns": [{"label": "A" * (pdf.MAX_COLUMN_LABEL_LENGTH + 1),
                         "fieldname": "entry"}],
            "result": [{"entry": "fine"}],
        }
        with self.assertRaises(frappe.ValidationError):
            self.export()
        self.runner.return_value = {
            "columns": self.result["columns"],
            "result": [{"entry": "X" * 2001}]
            * (pdf.MAX_RENDERED_CELLS_LENGTH // 2001 + 1),
        }
        with self.assertRaises(frappe.ValidationError):
            self.export()
        self.get_pdf.assert_not_called()

    def test_prepared_reports_and_oversized_results_cannot_generate_a_pdf(self):
        self.report.prepared_report = 1
        with self.assertRaises(frappe.ValidationError):
            self.export()
        self.assertFalse(pdf.can_download_report_pdf("General Ledger"))
        self.runner.assert_not_called()
        self.report.prepared_report = 0
        self.runner.return_value = {**self.result, "prepared_report": True}
        with self.assertRaises(frappe.ValidationError):
            self.export()
        self.runner.return_value = {**self.result, "result": [{}] * (pdf.MAX_ROWS + 1)}
        with self.assertRaises(frappe.ValidationError):
            self.export()
        self.runner.return_value = {**self.result, "columns": self.result["columns"] * (pdf.MAX_COLUMNS + 1)}
        with self.assertRaises(frappe.ValidationError):
            self.export()
        self.get_pdf.assert_not_called()
        self.access_log.assert_not_called()

    def test_bad_filters_and_orientation_are_rejected_before_report_run(self):
        for filters in ({"company": "x" * 257}, {"company": {"__bad__": "value"}},
                        {"user": "Administrator"}, "{" + "x" * pdf.MAX_FILTER_BYTES + "}"):
            with self.subTest(filters=type(filters).__name__):
                with self.assertRaises(frappe.ValidationError):
                    self.export(filters=filters)
        with self.assertRaises(frappe.ValidationError):
            self.export(orientation="--enable-local-file-access")
        self.runner.assert_not_called()

    def test_pdf_download_response_access_log_and_dead_proxy(self):
        from frappe.utils.response import build_response

        self.assertEqual(
            frappe.allowed_http_methods_for_whitelisted_func[pdf.download_report_pdf], ["POST"]
        )
        html = self.export(orientation="Portrait")
        self.assertIn("safe", html)
        self.runner.assert_called_once_with(
            "General Ledger", filters={"company": "Acme"}, are_default_filters=False
        )
        self.assertEqual(self.get_pdf.call_args.args[1], {
            "orientation": "Portrait", "proxy": "http://0.0.0.0:0",
            "bypass-proxy-for": ["localhost"], "load-error-handling": "ignore",
        })
        self.bypass_hosts.assert_called_once()
        self.access_log.assert_called_once()
        self.assertEqual(self.access_log.call_args.kwargs["report_name"], "General Ledger")
        response = build_response()
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("General-Ledger.pdf", response.headers["Content-Disposition"])
        self.assertEqual(response.data, b"%PDF-1.4\nmock\n")


if __name__ == "__main__":
    unittest.main()
