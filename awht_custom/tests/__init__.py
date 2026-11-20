import unittest

import frappe


def _ensure_test_local():
    if not dict(frappe.local):
        frappe.init(site=_SITE, sites_path=_SITES_PATH)
        frappe.connect()
    if "session" not in dict(frappe.local):
        frappe.local.session = frappe._dict(
            user="Administrator", sid="Administrator", data=frappe._dict()
        )
    try:
        frappe.local.flags.in_test = True
    except AttributeError:
        frappe.local.flags = frappe._dict(in_test=True)
    except RuntimeError:
        pass


_SITE = frappe.local.site
_SITES_PATH = frappe.local.sites_path
_original_start_test = unittest.TestResult.startTest
_original_stop_test = unittest.TestResult.stopTest


def _start_test(self, test):
    _ensure_test_local()
    return _original_start_test(self, test)


def _stop_test(self, test):
    _ensure_test_local()
    return _original_stop_test(self, test)


unittest.TestResult.startTest = _start_test
unittest.TestResult.stopTest = _stop_test
