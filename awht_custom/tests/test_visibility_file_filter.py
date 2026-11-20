"""Site-free File route, owner/share permission and ZIP bypass tests."""

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from werkzeug.exceptions import Forbidden

with patch.object(frappe, "logger", return_value=logging.getLogger("visibility_file_test")):
    from frappe import handler
    from frappe.core.doctype.file import utils as file_utils
    from frappe.core.doctype.file.file import File
    from frappe.model import base_document
    from frappe.permissions import has_controller_permissions
    from frappe.utils import response

from awht_custom.visibility import file_filter as ff


RESTRICTED = {"restricted": True}
OPEN = {"restricted": False}
URL = "/private/files/report.json.gz"


class ExtendedFile(ff.PreparedReportFileMixin, File):
    pass


def file(doctype="Prepared Report", name="PR-1", url=URL, owner="restricted"):
    doc = ExtendedFile.__new__(ExtendedFile)
    doc.__dict__.update(doctype="File", name="FILE-1", owner=owner, is_private=1,
                        attached_to_doctype=doctype, attached_to_name=name,
                        file_url=url, flags=frappe._dict())
    return doc


class TestFileDownloads(unittest.TestCase):
    def test_frappe_extension_mro_and_hook_target_names(self):
        if hasattr(base_document, "_get_extended_class"):
            with patch.object(frappe, "get_hooks", return_value={
                "File": ["awht_custom.visibility.file_filter.PreparedReportFileMixin"]
            }), patch.object(frappe, "get_attr", return_value=ff.PreparedReportFileMixin):
                controller = base_document._get_extended_class(File, "File")
        else:
            # Frappe v15 has no extend_doctype_class; v15 uses before_request
            # plus the has_permission hook for the same denial.
            controller = type("ExtendedFile", (ff.PreparedReportFileMixin, File), {})
        self.assertEqual(controller.__bases__[0], ff.PreparedReportFileMixin)
        self.assertEqual(controller.__bases__[-1], File)
        self.assertTrue(issubclass(controller, File))
        self.assertEqual(ff.OVERRIDE_WHITELISTED_METHODS, {
            "frappe.core.api.file.zip_files": "awht_custom.visibility.file_filter.zip_files",
            "frappe.core.doctype.file.file.zip_files": "awht_custom.visibility.file_filter.zip_files",
        })
        self.assertIn(ff.zip_files, frappe.whitelisted)

    def test_v15_before_request_blocks_private_prepared_file_route(self):
        with patch.object(frappe, "local", SimpleNamespace(
                    request=SimpleNamespace(path=URL), flags=SimpleNamespace(in_test=True))), \
                patch.object(frappe, "session", SimpleNamespace(user="restricted")), \
                patch.object(frappe, "get_all", return_value=[{"name": "FILE-1"}]), \
                patch.object(frappe, "get_doc", return_value=file()), \
                patch.object(ff, "policy_for_user", return_value=RESTRICTED), \
                patch.object(ff, "_", side_effect=lambda text: text), \
                patch.object(frappe, "throw", side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                ff.before_request()

    def test_private_file_route_and_file_api_cannot_download_restricted_owner(self):
        raw = file()
        data = frappe._dict({key: value for key, value in raw.__dict__.items() if key not in ("doctype", "flags")})
        with patch.object(frappe, "session", SimpleNamespace(user="restricted")), \
                patch.object(frappe, "form_dict", frappe._dict(fid="FILE-1")), \
                patch.object(frappe, "get_all", return_value=[data]), \
                patch.object(frappe, "get_doc", return_value=raw), \
                patch.object(ff, "policy_for_user", return_value=RESTRICTED), \
                patch.object(response, "_", side_effect=lambda text: text), \
                patch.object(response, "send_private_file") as read_disk:
            self.assertIsNone(file_utils.find_file_by_url(URL, name="FILE-1"))
            with self.assertRaises(Forbidden):
                response.download_private_file(URL)
            with self.assertRaises(frappe.PermissionError):
                handler.download_file(URL)
            read_disk.assert_not_called()

    def test_restricted_reader_and_owner_are_both_denied(self):
        for owner in ("restricted", "another@example.com"):
            doc = file(owner=owner)
            with self.subTest(owner=owner), \
                    patch.object(frappe, "session", SimpleNamespace(user="restricted")), \
                    patch.object(ff, "policy_for_user", return_value=RESTRICTED), \
                    patch.object(File, "is_downloadable") as core:
                self.assertFalse(doc.is_downloadable())
                self.assertFalse(ff.has_file_permission(doc, ptype="read", user="restricted"))
                self.assertFalse(ff.has_file_permission(doc, ptype="write", user="restricted"))
                core.assert_not_called()

    def test_unrelated_private_route_preserves_original_download(self):
        ordinary = file(doctype="Sales Invoice", name="SI-1", url="/private/files/ordinary.pdf")
        data = {key: value for key, value in ordinary.__dict__.items() if key not in ("doctype", "flags")}
        def lookup(doctype, *, filters, **kwargs):
            return [] if filters.get("attached_to_doctype") == "Prepared Report" else [data]

        returned = object()
        with patch.object(frappe, "session", SimpleNamespace(user="restricted")), \
                patch.object(frappe, "form_dict", frappe._dict(fid="FILE-1")), \
                patch.object(frappe, "get_all", side_effect=lookup), \
                patch.object(frappe, "get_doc", return_value=ordinary), \
                patch.object(File, "is_downloadable", return_value=True) as core, \
                patch.object(response, "make_access_log"), \
                patch.object(response, "send_private_file", return_value=returned) as send:
            self.assertIs(response.download_private_file(ordinary.file_url), returned)
            core.assert_called_once_with()
            send.assert_called_once_with("/files/ordinary.pdf")

    def test_explicit_permission_user_is_not_replaced_by_session_user(self):
        doc = file()
        def policy(user):
            return RESTRICTED if user == "restricted" else OPEN
        with patch.object(frappe, "session", SimpleNamespace(user="Administrator")), \
                patch.object(ff, "policy_for_user", side_effect=policy) as lookup, \
                patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args: "Ordinary Report")):
            self.assertFalse(ff.has_file_permission(doc, ptype="read", user="restricted"))
            self.assertIsNone(ff.has_file_permission(doc, ptype="read", user="Administrator"))
        self.assertEqual([call.args[0] for call in lookup.call_args_list], ["restricted", "Administrator"])

    def test_unrelated_files_defer_to_original_permission_instead_of_granting(self):
        public = file(doctype="Sales Invoice", name="SI-1", url="/files/normal.pdf")
        private = file(doctype="Sales Invoice", name="SI-1", url="/private/files/normal.pdf")
        with patch.object(frappe, "session", SimpleNamespace(user="restricted")), \
                patch.object(frappe, "get_all", return_value=[]) as lookup, \
                patch.object(ff, "policy_for_user") as policy, \
                patch.object(File, "is_downloadable", side_effect=[True, False]) as core:
            self.assertTrue(public.is_downloadable())
            self.assertFalse(private.is_downloadable())
            self.assertIsNone(ff.has_file_permission(private, ptype="read", user="restricted"))
            self.assertEqual(core.call_count, 2)
            lookup.assert_called()
            policy.assert_not_called()

        def core_permission(**kwargs):
            return False
        with patch.object(frappe, "get_hooks", return_value={"File": [core_permission, ff.has_file_permission]}), \
                patch.object(frappe, "get_all", return_value=[]):
            self.assertFalse(has_controller_permissions(private, "read", user="restricted"))

    def test_file_hook_denies_before_frappe_owner_or_share_grants(self):
        raw = file()
        core = Mock(return_value=True)
        with patch.object(frappe, "get_hooks", return_value={"File": [core, ff.has_file_permission]}), \
                patch.object(ff, "policy_for_user", return_value=RESTRICTED):
            self.assertFalse(has_controller_permissions(raw, "read", user="restricted"))
            core.assert_not_called()

    def test_unrestricted_ordinary_prepared_file_keeps_original_download(self):
        doc = file()
        with patch.object(frappe, "session", SimpleNamespace(user="other")), \
                patch.object(ff, "policy_for_user", return_value=OPEN), \
                patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args: "Ordinary Report")), \
                patch.object(File, "is_downloadable", return_value=True) as core:
            self.assertTrue(doc.is_downloadable())
            self.assertIsNone(ff.has_file_permission(doc, "read", user="other"))
            core.assert_called_once_with()

    def test_branch_prepared_files_denied_even_for_unrestricted_user(self):
        for report_name in ff.BRANCH_REPORTS:
            with self.subTest(report=report_name), \
                    patch.object(frappe, "session", SimpleNamespace(user="Administrator")), \
                    patch.object(ff, "policy_for_user", return_value=OPEN), \
                    patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args: report_name)), \
                    patch.object(File, "is_downloadable") as core:
                doc = file()
                self.assertFalse(doc.is_downloadable())
                self.assertFalse(ff.has_file_permission(doc, "read", user="Administrator"))
                core.assert_not_called()

    def test_same_private_url_cannot_be_laundered_through_unrelated_file(self):
        alias = file(doctype="Sales Invoice", name="SI-1", owner="restricted")
        with patch.object(frappe, "session", SimpleNamespace(user="restricted")), \
                patch.object(frappe, "get_all", return_value=["PR-1"]) as lookup, \
                patch.object(ff, "policy_for_user", return_value=RESTRICTED), \
                patch.object(File, "is_downloadable") as core:
            self.assertFalse(alias.is_downloadable())
            self.assertFalse(ff.has_file_permission(alias, user="restricted"))
            core.assert_not_called()
            lookup.assert_called_with("File", filters={"file_url": URL, "attached_to_doctype": "Prepared Report"},
                                      pluck="attached_to_name")
        with patch.object(frappe, "session", SimpleNamespace(user="other")), \
                patch.object(frappe, "get_all", return_value=["PR-1"]), \
                patch.object(ff, "policy_for_user", return_value=OPEN), \
                patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args: "Unsold Items")):
            self.assertFalse(alias.is_downloadable())

    def test_orphan_prepared_attachment_fails_closed_without_disabling_unrelated_file(self):
        orphan = file(name="PR-DELETED")
        unrelated = file(doctype="Sales Invoice", name="SI-1", url="/files/normal.pdf")
        with patch.object(frappe, "session", SimpleNamespace(user="other")), \
                patch.object(ff, "policy_for_user", return_value=OPEN), \
                patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args: None)), \
                patch.object(File, "is_downloadable", return_value=True):
            self.assertFalse(orphan.is_downloadable())
            self.assertTrue(unrelated.is_downloadable())

    def test_zip_api_rejects_raw_prepared_files_but_delegates_other_files(self):
        raw = file()
        normal = file(doctype="Sales Invoice", name="SI-1", url="/files/invoice.pdf")
        with patch.object(frappe, "session", SimpleNamespace(user="restricted")), \
                patch.object(ff, "policy_for_user", return_value=RESTRICTED), \
                patch.object(ff, "_", side_effect=lambda text: text), \
                patch.object(frappe, "get_doc", side_effect=[raw, normal]), \
                patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                patch("frappe.core.api.file.zip_files", return_value=b"normal zip") as core, \
                patch.object(frappe, "get_all", return_value=[]):
            with self.assertRaises(frappe.PermissionError):
                ff.zip_files('["FILE-RAW"]')
            core.assert_not_called()
            self.assertEqual(ff.zip_files('["FILE-NORMAL"]'), b"normal zip")
            core.assert_called_once_with('["FILE-NORMAL"]')

    def test_zip_api_blocks_old_branch_snapshot_for_unrestricted_user(self):
        with patch.object(frappe, "session", SimpleNamespace(user="other")), \
                patch.object(ff, "policy_for_user", return_value=OPEN), \
                patch.object(frappe, "get_doc", return_value=file()), \
                patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args: "Branch Financial Exposure")), \
                patch.object(ff, "_", side_effect=lambda text: text), \
                patch.object(frappe, "throw", side_effect=frappe.PermissionError), \
                patch("frappe.core.api.file.zip_files") as core:
            with self.assertRaises(frappe.PermissionError):
                ff.zip_files('["PR-FILE"]')
            core.assert_not_called()

    def test_zip_api_preserves_unrestricted_ordinary_prepared_download(self):
        with patch.object(frappe, "session", SimpleNamespace(user="other")), \
                patch.object(ff, "policy_for_user", return_value=OPEN), \
                patch.object(frappe, "get_doc", return_value=file()), \
                patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args: "Ordinary Report")), \
                patch("frappe.core.api.file.zip_files", return_value="original") as core:
            self.assertEqual(ff.zip_files('["PR-FILE"]'), "original")
            core.assert_called_once_with('["PR-FILE"]')


if __name__ == "__main__":
    unittest.main()
