"""Site-free tests for Frappe 16 query-report presentation overrides."""

import inspect
import io
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from openpyxl import load_workbook

from awht_custom.visibility import report_filter as rf


RESTRICTED = {"restricted": True, "patterns": ["valuation", "supplier cost"]}
OPEN = {"restricted": False, "patterns": []}
CSV = {"delimiter": ",", "quoting": 0, "decimal_sep": "."}


def local(**kwargs):
    return SimpleNamespace(flags=SimpleNamespace(in_test=True), **kwargs)


def sample():
    return {
        "columns": [
            {"fieldname": "item", "label": "Item", "fieldtype": "Data", "width": 90},
            {"fieldname": "valuation_rate", "label": "Valuation Rate", "fieldtype": "Currency"},
            {"fieldname": "qty", "label": "Qty", "fieldtype": "Float", "width": 70},
        ],
        "result": [
            {"item": "A", "valuation_rate": 1234, "qty": 2, "secret": 5678, "indent": 1},
            {"item": "B", "valuation_rate": 2345, "qty": 3},
            ["Total", 3579, 5],
        ],
        "add_total_row": 1,
        "skip_total_row": 0,
        "chart": {"data": [1234]},
        "report_summary": [{"value": 1234}],
        "message": "cost 1234",
        "extra": {"cost": 1234},
    }


def params(**kwargs):
    values = dict(report_name="Stock Report", file_format_type="CSV", filters={},
                  custom_columns=[], visible_idx=[], ignore_visible_idx=False,
                  include_indentation=False, include_filters=True, include_hidden_columns=True,
                  applied_filters={"anything": "SECRET FILTER"}, export_in_background=0)
    values.update(kwargs)
    return frappe._dict(values)


class FilterTests(unittest.TestCase):
    def test_unrestricted_returns_same_object(self):
        data = sample()
        self.assertIs(rf.filter_result(data, OPEN, "Report", "Item"), data)

    def test_dict_list_tuple_totals_and_unknown_keys(self):
        data = sample()
        data["result"].insert(2, ("C", 7777, 4))
        original = deepcopy(data)
        result = rf.filter_result(data, RESTRICTED, "Stock Report", "Item")
        self.assertEqual([c["fieldname"] for c in result["columns"]], ["item", "qty"])
        self.assertEqual(result["result"], [
            {"item": "A", "qty": 2, "indent": 1}, {"item": "B", "qty": 3},
            ["C", 4], ["Total", 5],
        ])
        self.assertEqual(data, original)
        self.assertIsNone(result["chart"])
        self.assertIsNone(result["report_summary"])
        self.assertIsNone(result["message"])
        self.assertNotIn("extra", result)

    def test_purchase_context_and_legacy_columns(self):
        data = {"columns": ["Item:Data:100", "Rate:Currency:90", "Qty:Float:80"],
                "result": [["A", 100, 4]]}
        result = rf.filter_result(data, RESTRICTED, "Purchase Register", "Purchase Invoice")
        self.assertEqual([c["fieldname"] for c in result["columns"]], ["item", "qty"])
        self.assertEqual(result["result"], [["A", 4]])
        sales = rf.filter_result(data, RESTRICTED, "Sales Register", "Sales Invoice")
        self.assertEqual(sales["result"], [["A", 100, 4]])

    def test_nested_metadata_duplicate_alias_and_short_rows(self):
        data = {"columns": [
            {"fieldname": "alias", "label": "Price", "df": {"fieldname": "valuation_rate"}},
            {"fieldname": "alias", "label": "Other"},
            {"fieldname": "qty", "label": "Qty", "options": "valuation_rate", "extra": "SECRET"},
        ], "result": [{"alias": 1234, "qty": {"secret": 1234}}, [1]]}
        result = rf.filter_result(data, RESTRICTED, "Report", "Item")
        self.assertEqual(result["columns"], [{"fieldname": "qty", "label": "Qty"}])
        self.assertEqual(result["result"], [{"qty": None}, [None]])

    def test_prepared_data_is_filtered_and_document_is_not_exposed(self):
        data = sample() | {"prepared_report": True,
                           "doc": {"name": "PR-1", "filters": '{"cost":1234}',
                                   "report_end_time": "2026-01-01", "secret": 1234},
                           "custom_filters": {"secret": 1234}}
        result = rf.filter_result(data, RESTRICTED, "Report", "Item")
        self.assertEqual(result["doc"], {"filters": "{}", "report_end_time": "2026-01-01"})
        self.assertEqual(result["custom_filters"], {"secret": 1234})
        self.assertNotIn("valuation_rate", result["result"][0])
        empty = rf.filter_result({"prepared_report": True, "doc": None}, RESTRICTED, "Report", "Item")
        self.assertEqual(empty["result"], [])
        self.assertIsNone(empty["doc"])

    def test_custom_report_columns_and_safe_filters_survive(self):
        data = {
            "columns": [
                {"fieldname": "item", "label": "Item"},
                {"fieldname": "description-item", "id": "description-item", "label": "Description",
                 "doctype": "Item", "link_field": {"fieldname": "item", "names": ["private"]},
                 "insert_after_index": 0},
                {"fieldname": "valuation_rate", "label": "Valuation Rate"},
            ],
            "result": [{"item": "A", "description-item": "Safe", "valuation_rate": 99,
                        "removed_field": "not returned"}],
            "custom_filters": {"company": "Example", "warehouses": ["A", "B"],
                               "valuation_rate": 99, "nested": {"secret": 99}},
        }
        result = rf.filter_result(data, RESTRICTED, "Saved Custom Report", "Item")
        self.assertEqual([col["fieldname"] for col in result["columns"]], ["item", "description-item"])
        self.assertEqual(result["columns"][1]["link_field"], {"fieldname": "item"})
        self.assertEqual(result["columns"][1]["id"], "description-item")
        self.assertEqual(result["columns"][1]["insert_after_index"], 0)
        self.assertEqual(result["result"], [{"item": "A", "description-item": "Safe"}])
        self.assertEqual(result["custom_filters"], {"company": "Example", "warehouses": ["A", "B"]})

    def test_label_only_data_and_blocked_label_collision(self):
        data = {"columns": [{"fieldname": "item", "label": "Item"},
                            {"fieldname": "secret", "label": "Supplier Cost"},
                            {"fieldname": "safe", "label": "Supplier Cost"}],
                "result": [{"Item": "A", "Supplier Cost": 99, "safe": 99}]}
        self.assertEqual(rf.filter_result(data, RESTRICTED, "Report", "Item")["result"], [{"Item": "A"}])

    def test_all_columns_removed_produces_empty_report(self):
        data = {"columns": [{"fieldname": "valuation_rate", "label": "Valuation"}],
                "result": [[100], [100]], "add_total_row": 1}
        result = rf.filter_result(data, RESTRICTED, "Report", "Item")
        self.assertEqual(result["columns"], [])
        self.assertEqual(result["result"], [])
        self.assertEqual(result["add_total_row"], 0)

    def test_stock_balance_and_gross_profit_derived_cost_columns(self):
        for report, fields in (
            ("Stock Balance", ["bal_qty", "bal_val", "opening_val", "in_val", "out_val", "val_rate"]),
            ("Gross Profit", ["sales_invoice", "selling_amount", "buying_rate", "buying_amount",
                              "gross_profit", "gross_profit_%"]),
        ):
            data = {"columns": [{"fieldname": name, "label": name} for name in fields],
                    "result": [list(range(len(fields)))]}
            with self.subTest(report=report):
                result = rf.filter_result(data, RESTRICTED, report, "Item")
                expected = ["bal_qty"] if report == "Stock Balance" else ["sales_invoice", "selling_amount"]
                self.assertEqual([col["fieldname"] for col in result["columns"]], expected)
                self.assertEqual(result["result"], [list(range(len(expected)))])

    def test_item_price_mixed_report_drops_all_price_list_rates(self):
        data = {"columns": [{"fieldname": "item_code", "label": "Item"},
                            {"fieldname": "price_list_rate", "label": "Price List Rate"}],
                "result": [["A", 100], ["B", 200]]}
        result = rf.filter_result(data, RESTRICTED, "Item Prices", "Item Price")
        self.assertEqual(result["result"], [["A"], ["B"]])


class EndpointTests(unittest.TestCase):
    def setUp(self):
        config = patch.object(frappe, "conf", SimpleNamespace(read_from_replica=False))
        config.start()
        self.addCleanup(config.stop)
        for target in (rf, rf.query_report):
            translation = patch.object(target, "_", side_effect=lambda text, **kw: text)
            translation.start()
            self.addCleanup(translation.stop)

    def test_signatures_and_mapping(self):
        core_parameters = list(inspect.signature(rf.query_report.run).parameters)
        self.assertEqual(
            list(inspect.signature(rf.run).parameters)[:len(core_parameters)], core_parameters
        )
        self.assertEqual(inspect.signature(rf.export_query), inspect.signature(rf.query_report.export_query))
        for endpoint in (rf.run, rf.export_query, rf.download_attachment, rf.enqueue_json_to_csv_conversion):
            self.assertIn(endpoint, frappe.whitelisted)
        self.assertEqual(rf.OVERRIDE_WHITELISTED_METHODS, {
            "frappe.desk.query_report.run": "awht_custom.visibility.report_filter.run",
            "frappe.desk.query_report.export_query": "awht_custom.visibility.report_filter.export_query",
            "frappe.core.doctype.prepared_report.prepared_report.download_attachment":
                "awht_custom.visibility.report_filter.download_attachment",
            "frappe.core.doctype.prepared_report.prepared_report.enqueue_json_to_csv_conversion":
                "awht_custom.visibility.report_filter.enqueue_json_to_csv_conversion",
        })

    def test_run_delegates_all_arguments_and_filters_for_pdf_or_prepared_views(self):
        doc = frappe._dict(name="PR-1", report_name="Report", filters='{"secret":1234}',
                           report_end_time="2026-01-01")
        doc.has_permission = lambda ptype: ptype == "read"
        payload = sample() | {"prepared_report": True, "doc": doc}
        with patch.object(rf.query_report, "run", return_value=payload) as core, \
                patch.object(rf.query_report, "get_report_doc", return_value=SimpleNamespace(ref_doctype="Item")), \
                patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(frappe, "override_whitelisted_method", return_value=rf._FILTERED_DOWNLOAD):
            result = rf.run("Report", {"x": 1}, "spoofed", True, ["custom"], True, "parent", False, ["js"])
        core.assert_called_once_with(report_name="Report", filters={"x": 1}, user="spoofed",
                                     ignore_prepared_report=True, custom_columns=["custom"], is_tree=True,
                                     parent_field="parent", are_default_filters=False, js_filters=["js"])
        self.assertNotIn("valuation_rate", result["result"][0])
        self.assertEqual(result["doc"], {"filters": "{}", "report_end_time": "2026-01-01", "name": "PR-1"})

    def test_run_permission_exception_is_not_caught(self):
        with patch.object(rf.query_report, "run", side_effect=frappe.PermissionError), \
                patch.object(rf, "get_policy") as policy:
            with self.assertRaises(frappe.PermissionError):
                rf.run("Report")
            policy.assert_not_called()

    def test_run_unrestricted_is_identical_and_no_second_report_lookup(self):
        payload = sample()
        with patch.object(rf.query_report, "run", return_value=payload), \
                patch.object(rf, "get_policy", return_value=OPEN), \
                patch.object(rf.query_report, "get_report_doc") as lookup:
            self.assertIs(rf.run("Report", ignore_prepared_report=True), payload)
            lookup.assert_not_called()

    def test_real_core_run_ignores_custom_user(self):
        with patch.object(rf.query_report, "_run", return_value={}) as internal:
            rf.query_report.run("Report", user="spoofed@example.com", ignore_prepared_report=True)
        self.assertNotIn("user", internal.call_args.kwargs)
        self.assertTrue(internal.call_args.kwargs["ignore_prepared_report"])

    def test_unrestricted_export_uses_core_unchanged(self):
        p = params()
        with patch.object(rf, "get_policy", return_value=OPEN), \
                patch.object(rf.query_report, "_export_query", return_value="original") as core:
            self.assertEqual(rf._export_query(p, {}, False), "original")
            core.assert_called_once_with(p, {}, populate_response=False)

    def test_unrestricted_export_endpoint_delegates_for_ordinary_reports(self):
        result = object()
        with patch.object(rf, "get_policy", return_value=OPEN), \
                patch.object(rf.frappe, "local", local(form_dict=frappe._dict(report_name="Stock Report"))), \
                patch.object(rf.query_report, "export_query", return_value=result) as core, \
                patch.object(rf.query_report, "get_report_doc") as report_lookup, \
                patch.object(rf, "_check_export") as check, \
                patch.object(rf, "_export_query") as export, \
                patch.object(rf.frappe, "enqueue") as enqueue:
            self.assertIs(rf.export_query(), result)
        core.assert_called_once_with()
        report_lookup.assert_not_called()
        check.assert_not_called()
        export.assert_not_called()
        enqueue.assert_not_called()

    def test_no_safe_export_columns_returns_no_file(self):
        data = {"columns": [{"fieldname": "valuation_rate", "label": "Valuation"}], "result": [[100]]}
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(rf.query_report, "run", return_value=data), \
                patch.object(rf.query_report, "get_report_doc", return_value=SimpleNamespace(ref_doctype="Item")), \
                patch.object(rf.frappe, "respond_as_web_page") as no_data, \
                patch.object(rf, "_", side_effect=lambda s: s):
            self.assertIsNone(rf._export_query(params(), {}, False))
        no_data.assert_called_once()

    def test_export_rerun_permission_exception_is_not_caught(self):
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(rf.query_report, "run", side_effect=frappe.PermissionError), \
                patch.object(rf.query_report, "get_report_doc") as lookup:
            with self.assertRaises(frappe.PermissionError):
                rf._export_query(params(), {}, False)
            lookup.assert_not_called()

    def test_foreground_and_background_permission_check(self):
        with patch.object(rf.frappe, "get_cached_value", return_value="Item"), \
                patch.object(rf.frappe.permissions, "can_export", side_effect=frappe.PermissionError), \
                patch.object(rf.frappe, "session", SimpleNamespace(user="actual@example.com")):
            with self.assertRaises(frappe.PermissionError):
                rf._check_export("Report")
            with self.assertRaises(frappe.PermissionError):
                rf.run_export_query_job(params(), {}, "actual@example.com")

    def test_background_uses_enqueued_user_and_not_client_email(self):
        p = params(user_email="attacker@example.com")
        with patch.object(rf, "_check_export") as check, \
                patch.object(rf.frappe, "session", SimpleNamespace(user="actual@example.com")), \
                patch.object(rf.frappe, "get_cached_value", return_value="actual@example.com") as lookup, \
                patch.object(rf, "_export_query", return_value=("Report", "csv", b"data")) as export, \
                patch("frappe.desk.utils.send_report_email") as send:
            rf.run_export_query_job(p, {}, "actual@example.com")
        check.assert_called_once_with("Stock Report")
        lookup.assert_called_once_with("User", "actual@example.com", "email")
        export.assert_called_once_with(p, {}, populate_response=False)
        send.assert_called_once_with("actual@example.com", "Report", "csv", b"data", attached_to_name="Stock Report")

    def test_retry_restores_enqueued_user_before_policy_and_export(self):
        session = SimpleNamespace(user="Administrator")
        seen = []
        def set_user(user):
            session.user = user
        def policy():
            seen.append(session.user)
            return RESTRICTED if session.user == "actual@example.com" else OPEN

        with patch.object(rf.frappe, "session", session), patch.object(rf.frappe, "set_user", side_effect=set_user) as restore, \
                patch.object(rf, "get_policy", side_effect=policy), patch.object(rf, "_check_export"), \
                patch.object(rf.frappe, "get_cached_value", return_value="actual@example.com"), \
                patch.object(rf.query_report, "run", return_value=sample()), \
                patch.object(rf.query_report, "get_report_doc", return_value=SimpleNamespace(ref_doctype="Item")), \
                patch("frappe.desk.utils.send_report_email") as send:
            rf.run_export_query_job(params(), CSV.copy(), "actual@example.com")
        restore.assert_called_once_with("actual@example.com")
        self.assertEqual(seen, ["actual@example.com"])
        self.assertNotIn(b"1234", send.call_args.args[3])

    def test_invalid_or_unrestored_background_identity_fails_closed(self):
        with patch.object(rf.frappe, "session", SimpleNamespace(user="Administrator")), \
                patch.object(rf.frappe, "set_user") as restore, patch.object(rf, "_check_export") as check, \
                patch.object(rf, "_export_query") as export:
            for user in (None, "", "Administrator", "Guest", "actual@example.com"):
                with self.subTest(user=user), self.assertRaises(frappe.PermissionError):
                    rf.run_export_query_job(params(), {}, user)
            restore.assert_called_once_with("actual@example.com")
            check.assert_not_called()
            export.assert_not_called()

    def test_export_endpoint_checks_permission_and_enqueues_own_job(self):
        p = params(export_in_background=1, user="attacker", ignore_permissions=1)
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(rf.frappe, "local", local(form_dict=p)), \
                patch.object(rf.frappe, "session", SimpleNamespace(user="actual@example.com")), \
                patch.object(rf.frappe, "flags", SimpleNamespace(in_test=False)), \
                patch.object(rf.frappe, "get_cached_value", return_value="actual@example.com"), \
                patch.object(rf, "_check_export") as check, \
                patch.object(rf.frappe, "enqueue") as enqueue, \
                patch.object(rf.frappe, "msgprint"), patch.object(rf, "_", side_effect=lambda s: s):
            rf.export_query()
        check.assert_called_once_with("Stock Report")
        args, kwargs = enqueue.call_args
        self.assertEqual(args[0], "awht_custom.visibility.report_filter.run_export_query_job")
        self.assertEqual(kwargs["enqueued_user"], "actual@example.com")
        self.assertNotIn("user", kwargs["form_params"])
        self.assertNotIn("ignore_permissions", kwargs["form_params"])
        self.assertNotIn("user_email", kwargs)

    def test_foreground_export_does_not_run_after_permission_denied(self):
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(rf.frappe, "local", local(form_dict=params())), \
                patch.object(rf, "_check_export", side_effect=frappe.PermissionError), \
                patch.object(rf, "_export_query") as export:
            with self.assertRaises(frappe.PermissionError):
                rf.export_query()
            export.assert_not_called()

    def test_job_uses_policy_at_execution_not_enqueue(self):
        with patch.object(rf, "_check_export"), \
                patch.object(rf.frappe, "session", SimpleNamespace(user="actual@example.com")), \
                patch.object(rf.frappe, "get_cached_value", return_value="actual@example.com"), \
                patch.object(rf, "get_policy", return_value=RESTRICTED) as policy, \
                patch.object(rf.query_report, "run", return_value=sample()), \
                patch.object(rf.query_report, "get_report_doc", return_value=SimpleNamespace(ref_doctype="Item")), \
                patch("frappe.desk.utils.send_report_email") as send:
            rf.run_export_query_job(params(), CSV.copy(), "actual@example.com")
        policy.assert_called_once()
        self.assertNotIn(b"1234", send.call_args.args[3])

    def test_visible_rows_recalculate_sanitized_total(self):
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(rf.query_report, "run", return_value=sample()), \
                patch.object(rf.query_report, "get_report_doc", return_value=SimpleNamespace(ref_doctype="Item")):
            _, _, content = rf._export_query(params(visible_idx=[0]), CSV.copy(), False)
        text = content.decode()
        self.assertIn("A,2", text)
        self.assertIn("Total,2", text)
        self.assertNotIn("B,3", text)
        self.assertNotIn("1234", text)

    def test_visible_indices_do_not_count_original_total_as_data(self):
        data = sample()
        data["result"].insert(2, {"item": "C", "qty": 4, "valuation_rate": 42})
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(rf.query_report, "run", return_value=data), \
                patch.object(rf.query_report, "get_report_doc", return_value=SimpleNamespace(ref_doctype="Item")):
            _, _, content = rf._export_query(params(visible_idx=[0, 3]), CSV.copy(), False)
        self.assertIn("Total,2", content.decode())
        self.assertNotIn("Total,7", content.decode())

    def test_csv_and_excel_use_sanitized_data_including_total(self):
        for format_type in ("CSV", "Excel"):
            with self.subTest(format_type=format_type), \
                    patch.object(rf, "get_policy", return_value=RESTRICTED), \
                    patch.object(rf.query_report, "run", return_value=sample()) as core, \
                    patch.object(rf.query_report, "get_report_doc", return_value=SimpleNamespace(ref_doctype="Item")), \
                    patch.object(rf.frappe, "get_system_settings", side_effect=lambda key: {
                        "date_format": "yyyy-mm-dd", "time_format": "HH:mm:ss"
                    }.get(key)):
                name, ext, content = rf._export_query(params(file_format_type=format_type), CSV.copy(), False)
            self.assertEqual(name, "Stock Report")
            core.assert_called_once()
            text = (content.decode() if ext == "csv" else str(list(load_workbook(io.BytesIO(content), data_only=True).active.values)))
            self.assertIn("Item", text)
            self.assertIn("Qty", text)
            self.assertNotIn("Valuation", text)
            self.assertNotIn("1234", text)
            self.assertNotIn("3579", text)
            self.assertNotIn("SECRET FILTER", text)


class PreparedDownloadTests(unittest.TestCase):
    def setUp(self):
        translation = patch.object(rf, "_", side_effect=lambda text, **kw: text)
        translation.start()
        self.addCleanup(translation.stop)

    def test_unrestricted_download_and_csv_conversion_delegate_unchanged(self):
        doc = SimpleNamespace(report_name="Stock Report")
        with patch.object(rf, "get_policy", return_value=OPEN), \
                patch.object(rf.prepared_report, "download_attachment", return_value="raw") as core, \
                patch.object(rf.prepared_report, "enqueue_json_to_csv_conversion", return_value="job") as csv, \
                patch.object(rf.frappe, "get_doc", return_value=doc) as lookup:
            self.assertEqual(rf.download_attachment("PR-1"), "raw")
            self.assertEqual(rf.enqueue_json_to_csv_conversion("PR-1"), "job")
        core.assert_called_once_with("PR-1")
        csv.assert_called_once_with("PR-1")
        self.assertEqual(lookup.call_count, 2)

    def test_restricted_download_checks_permission_and_sends_only_filtered_json(self):
        raw = sample() | {"prepared_report": True, "status": "secret", "filters": {"cost": 1234}}
        doc = SimpleNamespace(name="PR-1", report_name="Stock Report", has_permission=lambda ptype: ptype == "read",
                              get_prepared_data=lambda **kw: (json.dumps(raw).encode(), "report.json.gz"))
        response = frappe._dict()
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(rf.frappe, "get_doc", return_value=doc) as lookup, \
                patch.object(rf.query_report, "get_report_doc", return_value=SimpleNamespace(ref_doctype="Item")), \
                        patch.object(rf.frappe, "local", local(response=response)), \
                patch.object(rf.prepared_report, "download_attachment") as core:
            self.assertIsNone(rf.download_attachment("PR-1"))
        lookup.assert_called_once_with("Prepared Report", "PR-1")
        core.assert_not_called()
        self.assertEqual(response.filename, "report.json")
        self.assertEqual(response.type, "binary")
        payload = json.loads(response.filecontent)
        self.assertEqual([c["fieldname"] for c in payload["columns"]], ["item", "qty"])
        self.assertEqual(payload["result"][0], {"item": "A", "qty": 2, "indent": 1})
        self.assertNotIn("1234", response.filecontent.decode())
        self.assertNotIn("secret", response.filecontent.decode())

    def test_restricted_legacy_or_invalid_raw_data_fails_closed(self):
        for raw, name in ((b"not json", "report.json.gz"), (b"[]", "report.json.gz"),
                          (b'{"result":[]}', "report.json.gz"),
                          (b'{"columns":[],"result":{}}', "report.json.gz"),
                          (b'{"columns":[17],"result":[[1234]]}', "report.json.gz"),
                          (b'{"columns":[{}],"result":[[1234]]}', "report.json.gz"),
                          (b'{"columns":[],"result":[]}', "report.csv.gz")):
            with self.subTest(raw=raw, name=name):
                response = frappe._dict()
                doc = SimpleNamespace(name="PR-1", report_name="Report", has_permission=lambda ptype: True,
                                      get_prepared_data=lambda **kw: (raw, name))
                with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                        patch.object(rf.frappe, "get_doc", return_value=doc), \
                        patch.object(rf.query_report, "get_report_doc", return_value=SimpleNamespace(ref_doctype="Item")), \
                patch.object(rf.frappe, "local", local(response=response)), \
                        patch.object(rf.frappe, "throw", side_effect=frappe.PermissionError), \
                        patch.object(rf.prepared_report, "download_attachment") as core:
                    with self.assertRaises(frappe.PermissionError):
                        rf.download_attachment("PR-1")
                    core.assert_not_called()
                self.assertNotIn("filecontent", response)

    def test_denied_prepared_report_is_not_read_or_exported(self):
        doc = SimpleNamespace(name="PR-1", report_name="Report", has_permission=lambda ptype: False,
                              get_prepared_data=lambda **kw: self.fail("read without permission"))
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(rf.frappe, "get_doc", return_value=doc), \
                patch.object(rf.frappe, "throw", side_effect=frappe.PermissionError), \
                patch.object(rf.query_report, "get_report_doc") as lookup:
            with self.assertRaises(frappe.PermissionError):
                rf.download_attachment("PR-1")
            lookup.assert_not_called()

    def test_restricted_csv_conversion_cannot_enqueue_raw_attachment(self):
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(rf.frappe, "throw", side_effect=frappe.PermissionError), \
                patch.object(rf.prepared_report, "enqueue_json_to_csv_conversion") as core:
            with self.assertRaises(frappe.PermissionError):
                rf.enqueue_json_to_csv_conversion("PR-1")
            core.assert_not_called()


if __name__ == "__main__":
    unittest.main()
