"""Site-free prepared report identity, branch-cache and retry regressions."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from awht_custom.visibility import report_filter as rf


OPEN = {"restricted": False, "patterns": []}
RESTRICTED = {"restricted": True, "patterns": ["valuation"]}


def report(prepared_report=0):
    return SimpleNamespace(ref_doctype="Item", prepared_report=prepared_report)


def prepared(name="PR-1", report_name="Ordinary Report", allowed=True):
    doc = frappe._dict(name=name, report_name=report_name, filters='{"secret":1234}',
                       report_end_time="2026-01-01")
    doc.has_permission = Mock(return_value=allowed)
    return doc


def result(doc):
    return {"columns": [{"fieldname": "item", "label": "Item"},
                        {"fieldname": "valuation_rate", "label": "Valuation Rate"}],
            "result": [{"item": "A", "valuation_rate": 1234}],
            "prepared_report": True, "doc": doc}


class TestPreparedIdentity(unittest.TestCase):
    def setUp(self):
        config = patch.object(frappe, "conf", SimpleNamespace(read_from_replica=False))
        config.start()
        self.addCleanup(config.stop)
        translation = patch.object(rf, "_", side_effect=lambda text: text)
        translation.start()
        self.addCleanup(translation.stop)

    def test_explicit_id_must_match_requested_report_and_id_before_core_read(self):
        for doc in (prepared(report_name="Other Report"), prepared(name="PR-OTHER")):
            with self.subTest(doc=doc.name), \
                    patch.object(rf.query_report, "get_report_doc", return_value=report(1)), \
                    patch.object(frappe, "get_doc", return_value=doc), \
                    patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                    patch.object(rf.query_report, "run") as core, \
                    patch.object(rf, "filter_result") as filtered:
                with self.assertRaises(frappe.PermissionError):
                    rf.run("Ordinary Report", filters='{"prepared_report_name":"PR-1"}')
                core.assert_not_called()
                filtered.assert_not_called()
                if doc.report_name != "Ordinary Report":
                    doc.has_permission.assert_not_called()

    def test_explicit_id_checks_current_read_permission_before_core_read(self):
        doc = prepared(allowed=False)
        filters = {"prepared_report_name": "PR-1", "company": "Example"}
        with patch.object(rf.query_report, "get_report_doc", return_value=report(1)), \
                patch.object(frappe, "get_doc", return_value=doc), \
                patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                patch.object(rf.query_report, "run") as core:
            with self.assertRaises(frappe.PermissionError):
                rf.run("Ordinary Report", filters=filters)
        doc.has_permission.assert_called_once_with("read")
        self.assertEqual(filters["prepared_report_name"], "PR-1")
        core.assert_not_called()

    def test_implicit_cached_result_checks_report_name_and_permission_before_filter(self):
        for doc in (prepared(report_name="Other Report"), prepared(allowed=False)):
            with self.subTest(doc=doc.report_name, allowed=doc.has_permission.return_value), \
                    patch.object(rf.query_report, "run", return_value=result(doc)), \
                    patch.object(rf.query_report, "get_report_doc") as report_lookup, \
                    patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                    patch.object(rf, "filter_result") as filtered, \
                    patch.object(rf, "get_policy") as policy:
                with self.assertRaises(frappe.PermissionError):
                    rf.run("Ordinary Report")
                filtered.assert_not_called()
                report_lookup.assert_not_called()
                policy.assert_not_called()

    def test_result_with_values_but_no_prepared_document_is_denied(self):
        with patch.object(rf.query_report, "run", return_value=result(None)), \
                patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                patch.object(rf, "filter_result") as filtered:
            with self.assertRaises(frappe.PermissionError):
                rf.run("Ordinary Report")
            filtered.assert_not_called()

    def test_filtered_download_hook_must_be_active_before_prepared_name_is_exposed(self):
        doc = prepared()
        with patch.object(rf.query_report, "run", return_value=result(doc)), \
                patch.object(rf.query_report, "get_report_doc", return_value=report()), \
                patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(frappe, "override_whitelisted_method", return_value="frappe.core.doctype.prepared_report.prepared_report.download_attachment"), \
                patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                patch.object(rf, "filter_result") as filtered:
            with self.assertRaises(frappe.PermissionError):
                rf.run("Ordinary Report")
            filtered.assert_not_called()

    def test_authorized_prepared_result_has_only_safe_name_and_filtered_values(self):
        doc = prepared()
        with patch.object(rf.query_report, "run", return_value=result(doc)), \
                patch.object(rf.query_report, "get_report_doc", return_value=report()), \
                patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(frappe, "override_whitelisted_method", return_value=rf._FILTERED_DOWNLOAD):
            data = rf.run("Ordinary Report")
        doc.has_permission.assert_called_once_with("read")
        self.assertEqual(data["doc"], {"filters": "{}", "report_end_time": "2026-01-01", "name": "PR-1"})
        self.assertEqual(data["result"], [{"item": "A"}])
        self.assertEqual(result(doc)["result"][0]["valuation_rate"], 1234)

    def test_unrestricted_prepared_result_also_checks_permission(self):
        doc = prepared(allowed=False)
        with patch.object(rf.query_report, "run", return_value=result(doc)), \
                patch.object(rf, "get_policy", return_value=OPEN), \
                patch.object(frappe, "throw", side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                rf.run("Ordinary Report")

    def test_ignored_prepared_id_preserves_fresh_ordinary_run(self):
        payload = {"columns": [{"fieldname": "item", "label": "Item"}], "result": [{"item": "A"}]}
        with patch.object(rf.query_report, "run", return_value=payload) as core, \
                patch.object(rf.query_report, "get_report_doc") as lookup, \
                patch.object(frappe, "get_doc") as doc_lookup, \
                patch.object(rf, "get_policy", return_value=OPEN):
            self.assertIs(rf.run("Ordinary Report", {"prepared_report_name": "PR-1"},
                                 ignore_prepared_report=True), payload)
            core.assert_called_once()
            lookup.assert_not_called()
            doc_lookup.assert_not_called()

    def test_restricted_export_checks_cached_report_identity_before_formatting(self):
        args = frappe._dict(report_name="Ordinary Report", filters={}, file_format_type="CSV", custom_columns=[])
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(rf.query_report, "run", return_value=result(prepared(report_name="Other Report"))), \
                patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                patch.object(rf.query_report, "build_xlsx_data") as builder:
            with self.assertRaises(frappe.PermissionError):
                rf._export_query(args, {}, False)
            builder.assert_not_called()


class TestBranchReports(unittest.TestCase):
    def setUp(self):
        config = patch.object(frappe, "conf", SimpleNamespace(read_from_replica=False))
        config.start()
        self.addCleanup(config.stop)
        translation = patch.object(rf, "_", side_effect=lambda text: text)
        translation.start()
        self.addCleanup(translation.stop)

    def test_branch_report_guards_cover_source_and_awht_names(self):
        self.assertEqual(rf.BRANCH_REPORTS, {
            "Branch Financial Exposure", "AWHT Current Credit Exposure",
            "Unsold Items", "AWHT No Stock Out Items",
        })

    def test_restricted_user_never_runs_credit_exposure_reports(self):
        with patch.object(rf, "get_policy", return_value=RESTRICTED), \
                patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                patch.object(rf.query_report, "get_report_doc") as lookup, \
                patch.object(rf.query_report, "run") as core:
            for report_name in ("Branch Financial Exposure", "AWHT Current Credit Exposure"):
                with self.subTest(report=report_name):
                    with self.assertRaises(frappe.PermissionError):
                        rf.run(report_name, filters={"company": "Example"})
                    with self.assertRaises(frappe.PermissionError):
                        rf.run(report_name, filters='{"prepared_report_name":"PR-1"}')
            lookup.assert_not_called()
            core.assert_not_called()

    def test_prepared_flag_blocks_both_branch_reports_for_all_users(self):
        for report_name in rf.BRANCH_REPORTS:
            for policy in (OPEN, RESTRICTED):
                with self.subTest(report=report_name, policy=policy), \
                        patch.object(rf, "get_policy", return_value=policy), \
                        patch.object(rf.query_report, "get_report_doc", return_value=report(1)), \
                        patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                        patch.object(rf.query_report, "run") as core:
                    with self.assertRaises(frappe.PermissionError):
                        rf.run(report_name, filters={"company": "Example"}, ignore_prepared_report=True)
                    core.assert_not_called()

    def test_explicit_id_blocked_even_when_branch_report_flag_is_off(self):
        for report_name in rf.BRANCH_REPORTS:
            with self.subTest(report=report_name), \
                    patch.object(rf, "get_policy", return_value=OPEN), \
                    patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                    patch.object(rf.query_report, "run") as core:
                with self.assertRaises(frappe.PermissionError):
                    rf.run(report_name, filters={"prepared_report_name": "PR-1"})
                core.assert_not_called()

    def test_unprepared_branch_reports_still_run_fresh_for_allowed_users(self):
        payload = {"columns": [], "result": [{"item": "A"}]}
        for report_name, policy in (
            ("Branch Financial Exposure", OPEN), ("AWHT Current Credit Exposure", OPEN),
            ("Unsold Items", RESTRICTED), ("AWHT No Stock Out Items", RESTRICTED),
        ):
            with self.subTest(report=report_name), \
                    patch.object(rf, "get_policy", return_value=policy), \
                    patch.object(rf.query_report, "get_report_doc", return_value=report(0)) as lookup, \
                    patch.object(rf.query_report, "run", return_value=payload) as core:
                result_data = rf.run(report_name, {"company": "Example"})
                if policy["restricted"]:
                    self.assertEqual(result_data["result"], [])
                else:
                    self.assertIs(result_data, payload)
                core.assert_called_once()
                lookup.assert_called_once_with(report_name)

    def test_cached_branch_attachment_denied_even_for_unrestricted_user(self):
        for policy in (OPEN, RESTRICTED):
            for report_name in rf.BRANCH_REPORTS:
                doc = prepared(report_name=report_name)
                doc.get_prepared_data = Mock()
                with self.subTest(report=report_name, policy=policy), \
                        patch.object(rf, "get_policy", return_value=policy), \
                        patch.object(frappe, "get_doc", return_value=doc), \
                        patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                        patch.object(rf.prepared_report, "download_attachment") as core:
                    with self.assertRaises(frappe.PermissionError):
                        rf.download_attachment("PR-1")
                    doc.get_prepared_data.assert_not_called()
                    core.assert_not_called()

    def test_branch_csv_conversion_denied_unrestricted_but_ordinary_delegates(self):
        with patch.object(rf, "get_policy", return_value=OPEN), \
                patch.object(frappe, "get_doc", side_effect=[
                    prepared(report_name=name) for name in rf.BRANCH_REPORTS
                ] + [prepared()]), \
                patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                patch.object(rf.prepared_report, "enqueue_json_to_csv_conversion", return_value="job") as core:
            for report_name in rf.BRANCH_REPORTS:
                with self.subTest(report=report_name), self.assertRaises(frappe.PermissionError):
                    rf.enqueue_json_to_csv_conversion("PR-1")
            self.assertEqual(rf.enqueue_json_to_csv_conversion("PR-1"), "job")
            core.assert_called_once_with("PR-1")

    def test_export_endpoint_rejects_prepared_branch_before_core(self):
        for report_name in rf.BRANCH_REPORTS:
            request = frappe._dict(report_name=report_name, filters={"company": "Example"})
            with self.subTest(report=report_name), \
                    patch.object(rf, "get_policy", return_value=OPEN), \
                    patch.object(frappe, "local", SimpleNamespace(
                        form_dict=request, flags=SimpleNamespace(in_test=True))), \
                    patch.object(rf.query_report, "get_report_doc", return_value=report(1)), \
                    patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                    patch.object(rf.query_report, "export_query") as core:
                with self.assertRaises(frappe.PermissionError):
                    rf.export_query()
                core.assert_not_called()

    def test_retry_user_cannot_export_credit_exposure_reports(self):
        for report_name in ("Branch Financial Exposure", "AWHT Current Credit Exposure"):
            session = SimpleNamespace(user="Administrator")
            seen = []
            def set_user(user):
                session.user = user
            def policy():
                seen.append(session.user)
                return RESTRICTED if session.user == "alice" else OPEN

            args = frappe._dict(report_name=report_name, filters={}, file_format_type="CSV")
            with self.subTest(report=report_name), \
                    patch.object(frappe, "session", session), \
                    patch.object(frappe, "set_user", side_effect=set_user) as restore, \
                    patch.object(rf, "get_policy", side_effect=policy), \
                    patch.object(rf, "_check_export"), \
                    patch.object(frappe, "get_cached_value", return_value="alice@example.com"), \
                    patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                    patch.object(rf.query_report, "run") as core, \
                    patch("frappe.desk.utils.send_report_email") as mail:
                with self.assertRaises(frappe.PermissionError):
                    rf.run_export_query_job(args, {}, "alice")
                core.assert_not_called()
                mail.assert_not_called()
            restore.assert_called_once_with("alice")
            self.assertEqual(seen, ["alice"])


if __name__ == "__main__":
    unittest.main()
