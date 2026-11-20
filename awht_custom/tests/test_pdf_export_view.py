"""ReportView PDF checks without a site, DB writes or real PDF generation."""

import json
import logging
import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import frappe
from jinja2 import Environment, FileSystemLoader

import awht_custom.visibility as visibility
from awht_custom.pdf_export import report_pdf, report_view


class FakeMeta:
    def __init__(self, fields, *, istable=False):
        self.fields = fields
        self.istable = istable
        self.issingle = False

    def get_field(self, name):
        return next((field for field in self.fields if field.fieldname == name), None)


class TestReportViewPDF(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(
            frappe.local, "session", frappe._dict(user="staff@example.test"), create=True
        ))
        self.enterContext(patch.object(frappe.local, "response", frappe._dict(), create=True))
        self.enterContext(patch.object(
            frappe.local, "flags", frappe._dict(mute_messages=True), create=True
        ))
        self.enterContext(patch.object(frappe.local, "db", Mock(), create=True))
        self.original_form = frappe._dict(rows="INJECTED ROW", ignore_permissions=1, cmd="raw.execute")
        self.enterContext(patch.object(frappe.local, "form_dict", self.original_form, create=True))
        self.enterContext(patch.object(frappe, "_", side_effect=lambda value, *args, **kwargs: value))

        def df(name, fieldtype="Data", options=None, hidden=0):
            return frappe._dict(fieldname=name, label=name.title(), fieldtype=fieldtype,
                                options=options, hidden=hidden)

        self.parent = FakeMeta([
            df("amount", "Currency", "SAR"), df("purchase_rate", "Currency", "SAR"),
            df("currency", "Link", "Currency"), df("company", "Link", "Company"),
            df("customer", "Link", "Customer"), df("hidden_amount", "Currency", "SAR", 1),
            df("items", "Table", "Sales Invoice Item"),
        ])
        self.child = FakeMeta([df("price", "Currency", "SAR")], istable=True)
        self.enterContext(patch.object(
            frappe, "get_meta", side_effect=lambda name: {
                "Sales Invoice": self.parent, "Sales Invoice Item": self.child
            }[name]
        ))
        self.enterContext(patch.object(report_view, "is_virtual_doctype", return_value=False))
        self.enterContext(patch.object(
            report_view, "get_permitted_fields",
            side_effect=lambda name, **kwargs: (
                ["name", "creation", "amount", "purchase_rate", "currency", "company", "customer",
                 "hidden_amount", "items"] if name == "Sales Invoice" else ["name", "price"]
            ),
        ))
        self.permissions = {}
        self.enterContext(patch.object(frappe, "has_permission", side_effect=self._has_permission))
        self.report = frappe._dict(
            name="Saved Invoices", report_type="Report Builder", ref_doctype="Sales Invoice",
            disabled=0, is_permitted=lambda: self.roles_allowed,
        )
        self.roles_allowed = True
        self.get_doc = self.enterContext(patch.object(frappe, "get_doc", return_value=self.report))
        self.policy = {"restricted": False}
        self.enterContext(patch("awht_custom.visibility.api.get_policy", side_effect=lambda: self.policy))
        self.builder = types.ModuleType("awht_custom.visibility.report_builder")
        self.builder.get = Mock(side_effect=self._fetch)
        self.builder._sensitive = Mock(side_effect=lambda sql, col, *args: col["source"] == "purchase_rate")
        self.enterContext(patch.dict(sys.modules, {
            "awht_custom.visibility.report_builder": self.builder
        }))
        self.enterContext(patch.object(visibility, "report_builder", self.builder, create=True))
        self.result = {
            "keys": ["name", "amount", "currency"],
            "values": [["INV-1", 10, "SAR"], ["INV-2", 20, "SAR"]],
        }
        env = Environment(loader=FileSystemLoader(Path(report_pdf.__file__).parent), autoescape=False)
        self.enterContext(patch.object(
            frappe, "render_template",
            side_effect=lambda path, context: env.get_template(Path(path).name).render(context),
        ))
        self.enterContext(patch.object(report_pdf, "formatdate", side_effect=str))
        self.enterContext(patch.object(report_pdf, "nowdate", return_value="2026-11-18"))
        self.enterContext(patch.object(frappe, "logger", return_value=logging.getLogger("pdf-view-test")))
        self.get_pdf = self.enterContext(
            patch("frappe.utils.pdf.get_pdf", return_value=b"%PDF-1.4\nmock\n")
        )
        self.enterContext(patch(
            "frappe.utils.print_format._pdf_bypass_proxy_hosts", return_value=["localhost"]
        ))
        self.access_log = self.enterContext(patch(
            "frappe.core.doctype.access_log.access_log.make_access_log"
        ))

    def _has_permission(self, doctype, ptype, doc=None):
        return self.permissions.get((doctype, ptype), True)

    def _fetch(self):
        params = frappe.local.form_dict
        self.last_params = frappe._dict(params)
        self.assertNotIn("rows", params)
        self.assertNotIn("columns", params)
        self.assertNotIn("ignore_permissions", params)
        self.assertNotIn("cmd", params)
        self.assertEqual(params.save_user_settings, "false")
        return self.result

    def export(self, **changes):
        args = {
            "doctype": "Sales Invoice", "fields": json.dumps([
                ["name", "Sales Invoice"], ["amount", "Sales Invoice"],
                ["currency", "Sales Invoice"],
            ]),
            "filters": json.dumps([["Sales Invoice", "company", "=", "Acme"]]),
            "order_by": "creation desc", "row_count": 2,
            "visible_names": json.dumps(["INV-1", "INV-2"]),
            "selected_items": json.dumps([]), "only_fields": json.dumps(["name", "amount"]),
            "letter_head": "__none__",
        }
        args.update(changes)
        report_view.download_report_view_pdf(**args)
        return self.get_pdf.call_args.args[0] if self.get_pdf.called else None

    def test_restricted_reportview_rows_are_refetched_without_browser_data(self):
        self.assertEqual(
            frappe.allowed_http_methods_for_whitelisted_func[report_view.download_report_view_pdf],
            ["POST"],
        )
        html = self.export(
            selected_items=json.dumps(["INV-2"]),
            visible_names=json.dumps(["INV-2", "INV-1"]),
        )
        self.assertIn("INV-2", html)
        self.assertNotIn("INV-1", html)
        self.assertNotIn("INJECTED ROW", html)
        self.assertEqual(frappe.local.form_dict, self.original_form)
        self.builder.get.assert_called_once()
        self.access_log.assert_called_once()
        self.assertEqual(self.access_log.call_args.kwargs["doctype"], "Sales Invoice")

    def test_row_injection_and_foreign_selected_names_fail_after_fetch(self):
        for key, names in (("selected_items", ["FAKE"]), ("visible_names", ["FAKE"])):
            with self.subTest(key=key):
                with self.assertRaises(frappe.PermissionError):
                    kwargs = {key: json.dumps(names)}
                    if key == "selected_items":
                        kwargs["visible_names"] = json.dumps(names)
                    self.export(**kwargs)
                self.assertIs(frappe.local.form_dict, self.original_form)
        self.get_pdf.assert_not_called()
        self.access_log.assert_not_called()

    def test_visibility_denial_does_not_fall_back_to_unfiltered_reportview(self):
        self.builder.get.side_effect = frappe.PermissionError("cost visibility denied")
        with patch("frappe.desk.reportview.get") as raw_get:
            with self.assertRaises(frappe.PermissionError):
                self.export()
            raw_get.assert_not_called()
        self.get_pdf.assert_not_called()
        self.assertIs(frappe.local.form_dict, self.original_form)

    def test_saved_report_roles_doctype_permissions_and_prepared_virtual_fail_closed(self):
        self.roles_allowed = False
        self.assertFalse(report_view.can_download_report_view_pdf("Sales Invoice", "Saved Invoices"))
        with self.assertRaises(frappe.PermissionError):
            self.export(report_name="Saved Invoices")
        self.roles_allowed = True
        self.report.ref_doctype = "Customer"
        with self.assertRaises(frappe.PermissionError):
            self.export(report_name="Saved Invoices")
        self.permissions[("Sales Invoice", "print")] = False
        with self.assertRaises(frappe.PermissionError):
            self.export()
        self.permissions[("Sales Invoice", "print")] = True
        with patch.object(report_view, "is_virtual_doctype", return_value=True):
            with self.assertRaises(frappe.PermissionError):
                self.export()
        with self.assertRaises(frappe.PermissionError):
            self.export(report_name=["Saved Invoices"])
        self.builder.get.assert_not_called()

    def test_qualified_order_is_validated_by_server_metadata(self):
        html = self.export(order_by="`tabSales Invoice`.`creation` desc")
        self.assertIn("INV-1", html)
        self.assertEqual(self.last_params.order_by, "`tabSales Invoice`.`creation` desc")

    def test_sensitive_cost_fields_cannot_be_printed_or_used_for_words(self):
        self.policy = {"restricted": True, "patterns": ["purchase rate"]}
        self.result = {"keys": ["name", "amount", "currency", "purchase_rate"],
                       "values": [["INV-1", 10, "SAR", "COST SECRET"]]}
        html = self.export(
            fields=json.dumps([["name", "Sales Invoice"], ["amount", "Sales Invoice"],
                               ["currency", "Sales Invoice"], ["purchase_rate", "Sales Invoice"]]),
            only_fields=json.dumps(["name", "amount", "purchase_rate"]),
            visible_names=json.dumps(["INV-1"]), row_count=1,
        )
        self.assertNotIn("COST SECRET", html)
        self.assertNotIn("Purchase_Rate", html)
        self.assertNotIn("purchase_rate", self.last_params.fields)
        self.get_pdf.reset_mock()
        with self.assertRaises(frappe.ValidationError):
            self.export(
                fields=json.dumps([["name", "Sales Invoice"], ["amount", "Sales Invoice"],
                                   ["currency", "Sales Invoice"], ["purchase_rate", "Sales Invoice"]]),
                only_fields=json.dumps(["amount", "purchase_rate"]),
                words_column="purchase_rate", visible_names=json.dumps(["INV-1"]), row_count=1,
            )
        self.get_pdf.assert_not_called()

    def test_unrelated_hidden_columns_invalid_filters_order_and_row_limits(self):
        for fields in (
            [["name", "Sales Invoice"], ["hidden_amount", "Sales Invoice"]],
            [["name", "Sales Invoice"], ["password", "User"]],
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(frappe.PermissionError):
                    self.export(fields=json.dumps(fields))
        with self.assertRaises(frappe.ValidationError):
            self.export(order_by="creation desc; drop table File")
        with self.assertRaises(frappe.ValidationError):
            self.export(row_count=report_pdf.MAX_ROWS + 1)
        with self.assertRaises(frappe.ValidationError):
            self.export(filters=json.dumps([["Sales Invoice", "company", "between", ["A"]]]))
        self.builder.get.assert_not_called()
        with self.assertRaises(frappe.ValidationError):
            self.export(only_fields=json.dumps(["not_selected"]))
        self.get_pdf.assert_not_called()

    def test_grouped_count_is_refetched_but_unstable_group_row_selection_is_refused(self):
        self.result = {"keys": ["customer", "_aggregate_column"],
                       "values": [["Customer A", 2], ["Customer B", 3]]}
        args = {
            "fields": json.dumps([["customer", "Sales Invoice"],
                                  ["_aggregate_column", "Sales Invoice"]]),
            "only_fields": json.dumps(["customer", "_aggregate_column"]),
            "grouping": json.dumps({"field": "customer", "owner": "Sales Invoice",
                                    "function": "count", "on_field": "name",
                                    "on_owner": "Sales Invoice"}),
            "visible_names": None, "selected_items": None,
            "order_by": "_aggregate_column desc",
        }
        html = self.export(**args)
        self.assertIn("Customer A", html)
        self.assertIn(">Count</th>", html)
        self.assertNotIn("INJECTED ROW", html)
        with self.assertRaises(frappe.ValidationError):
            self.export(**{**args, "selected_items": json.dumps(["Customer A"])})

    def test_grouped_sar_totals_are_not_included_twice_in_amount_words(self):
        from num2words import num2words

        self.result = {"keys": ["customer", "_aggregate_column"],
                       "values": [["Customer A", 2], ["Customer B", 3]]}
        html = self.export(
            fields=json.dumps([["customer", "Sales Invoice"],
                               ["_aggregate_column", "Sales Invoice"]]),
            only_fields=json.dumps(["customer", "_aggregate_column"]),
            grouping=json.dumps({"field": "customer", "owner": "Sales Invoice",
                                 "function": "sum", "on_field": "amount",
                                 "on_owner": "Sales Invoice"}),
            visible_names=None, selected_items=None, order_by="_aggregate_column desc",
            words_column="_aggregate_column", words_mode="sum", add_totals_row=1,
        )
        self.assertEqual(html.count("5.00"), 1)
        self.assertIn(num2words(Decimal("5.00"), lang="en", to="currency", currency="SAR"), html)
        self.assertNotIn(num2words(Decimal("10.00"), lang="en", to="currency", currency="SAR"), html)

    def test_reportview_sar_column_precision_must_match_words_precision(self):
        self.parent.get_field("amount").precision = 3
        with self.assertRaisesRegex(frappe.ValidationError, "precision"):
            self.export(words_column="amount")
        self.get_pdf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
