"""Deny raw Prepared Report File downloads without restricting unrelated Files.

Frappe 16's private-file route and download_file APIs use find_file_by_url,
which calls File.is_downloadable directly. Document-level File reads use the
has_permission hook; both hooks are needed. The ZIP API bypasses both by using
the core module-level File.has_permission and needs its own targeted override.
"""

import frappe
from frappe import _

from awht_custom.visibility.api import BRANCH_REPORTS, policy_for_user


OVERRIDE_WHITELISTED_METHODS = {
    "frappe.core.api.file.zip_files": "awht_custom.visibility.file_filter.zip_files",
    "frappe.core.doctype.file.file.zip_files": "awht_custom.visibility.file_filter.zip_files",
}


def _blocks_raw_file(doc, user):
    if getattr(doc, "attached_to_doctype", None) == "Prepared Report":
        names = [getattr(doc, "attached_to_name", None)]
    else:
        # Frappe can reuse the same file_url for multiple File records. An
        # unrelated record must not make a prepared attachment downloadable.
        url = getattr(doc, "file_url", None)
        if not isinstance(url, str) or not url.startswith("/private/files/"):
            return False
        names = frappe.get_all(
            "File", filters={"file_url": url, "attached_to_doctype": "Prepared Report"},
            pluck="attached_to_name",
        )
    if not names:
        return False
    if policy_for_user(user).get("restricted"):
        return True
    # These reports contain branch-scoped snapshots. No user may download an
    # old copy after branch grants have changed, even if the flag is now off.
    for name in names:
        report_name = frappe.db.get_value("Prepared Report", name, "report_name") if name else None
        if not report_name or report_name in BRANCH_REPORTS:
            return True
    return False


class PreparedReportFileMixin:
    def is_downloadable(self):
        if _blocks_raw_file(self, frappe.session.user):
            return False
        return super().is_downloadable()


def before_request():
    """Deny the v15 private-file route before core calls File.is_downloadable.

    v15 has no extend_doctype_class hook, so the mixin alone cannot protect
    ``/private/files/...``. This read-only preflight applies the same block to
    any File record sharing the requested URL.
    """
    request = getattr(frappe.local, "request", None)
    path = getattr(request, "path", None)
    if not isinstance(path, str) or not path.startswith("/private/files/"):
        return
    user = frappe.session.user
    if user == "Guest":
        return
    for entry in frappe.get_all("File", filters={"file_url": path}, fields=["name"]):
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        if not name:
            continue
        doc = frappe.get_doc("File", name)
        if _blocks_raw_file(doc, user):
            frappe.throw(_("Unfiltered prepared report files are unavailable"), frappe.PermissionError)


def has_file_permission(doc, ptype=None, user=None, debug=False):
    """Only deny: return None so Frappe's existing File hooks still decide.

    Frappe v15 stops at the first hook returning a non-None value. Returning
    True for unrelated files would therefore bypass a later core denial, so the
    hook must stay silent unless it is blocking a prepared-report attachment.
    """
    if _blocks_raw_file(doc, user or frappe.session.user):
        return False
    return None


@frappe.whitelist()
def zip_files(files: str):
    from frappe.core.api.file import zip_files as core_zip_files
    from frappe.core.doctype.file.file import File

    selected = frappe.parse_json(files)
    for entry in selected if isinstance(selected, (list, tuple, str, dict)) else ():
        if isinstance(entry, str):
            doc = frappe.get_doc("File", entry)
        elif isinstance(entry, File):
            doc = entry
        else:
            continue
        if _blocks_raw_file(doc, frappe.session.user):
            frappe.throw(_("Unfiltered prepared report files are unavailable"), frappe.PermissionError)
    return core_zip_files(files)
