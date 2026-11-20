"""Isolated credit-control tests: mocked Frappe, no site or database writes."""

import importlib
import json
import sys
import threading
import types
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


class ValidationError(Exception):
    pass


class PermissionError(Exception):
    pass


class Record(dict):
    def __getattr__(self, key):
        return self.get(key)

    __setattr__ = dict.__setitem__


class Doc(types.SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)


class FakeDB:
    def __init__(self):
        self.policies = {"C1": Record(company="C1", enabled=1, source_warehouse="HEAD")}
        self.companies = {"C1": "SAR", "C2": "SAR"}
        self.branches = {"B1": 100, "B2": 100}
        self.branch_companies = {"B1": "C1", "B2": "C1"}
        self.prepared_flags = {}
        self.cached_reports = set()
        self.warehouses = {
            "HEAD": Record(company="C1", is_group=0, disabled=0),
            "WH1": Record(company="C1", is_group=0, disabled=0),
            "WH2": Record(company="C1", is_group=0, disabled=0),
            "FOREIGN": Record(company="C2", is_group=0, disabled=0),
        }
        self.mappings = [Record(name="WH1", company="C1", branch="B1", warehouse="WH1")]
        self.bins = [Record(warehouse="WH1", stock_value=70)]
        self.items = {"SKU": Record(is_stock_item=1, has_serial_no=0, has_batch_no=0)}
        self.source_bins = {("HEAD", "SKU"): Record(actual_qty=100, valuation_rate=1)}
        self.requests = {}
        self.invoices = []
        self.pending_pos = set()
        self.unsold_rows = []
        self.calls = []
        self.policy_lock = None
        self.second_waiting = threading.Event()

    def get_value(self, doctype, name, fieldname="name", *, as_dict=False, for_update=False):
        self.calls.append(("get_value", doctype, name, fieldname, for_update))
        if doctype == "AWHT Credit Policy":
            if not isinstance(name, dict) or "company" not in name:
                raise AssertionError("Policy must be selected by its unique company field")
            if for_update and self.policy_lock:
                if threading.current_thread().name == "credit-second":
                    self.second_waiting.set()
                if not self.policy_lock.acquire(timeout=5):
                    raise RuntimeError("Policy lock timed out")
            values = next((row for row in self.policies.values() if row.company == name["company"]), None)
        elif doctype == "Company":
            values = Record(default_currency=self.companies.get(name)) if name in self.companies else None
        elif doctype == "Branch":
            values = (
                Record(custom_credit_limit=self.branches[name], custom_company=self.branch_companies[name])
                if name in self.branches else None
            )
        elif doctype == "Report":
            values = Record(prepared_report=self.prepared_flags.get(name, 0))
        elif doctype == "Warehouse":
            values = self.warehouses.get(name)
        elif doctype == "Item":
            values = self.items.get(name)
        elif doctype == "Bin":
            values = self.source_bins.get((name["warehouse"], name["item_code"]))
        else:
            raise AssertionError("Unexpected get_value: " + doctype)
        if not values:
            return None
        if isinstance(fieldname, list):
            selected = Record({key: values.get(key) for key in fieldname})
            return selected if as_dict else tuple(selected.values())
        return values.get(fieldname)

    def exists(self, doctype, name):
        if doctype == "Branch":
            return name in self.branches
        if doctype == "Prepared Report":
            self.calls.append(("exists", doctype, name))
            return "PREP-1" if name["report_name"] in self.cached_reports else None
        raise AssertionError("Unexpected exists: " + doctype)

    def get_descendants(self, doctype, name):
        if doctype != "Item Group":
            raise AssertionError(doctype)
        return ["Child of " + name]

    def sql(self, query, params, *, as_dict=False):
        self.calls.append(("sql", query, params, as_dict))
        company = params["company"]
        if "FROM `tabAWHT Branch Warehouse` m" in query and "LEFT JOIN `tabWarehouse`" in query:
            return [
                Record(
                    branch=m.branch,
                    warehouse=m.warehouse,
                    warehouse_company=self.warehouses.get(m.warehouse, {}).get("company"),
                    is_group=self.warehouses.get(m.warehouse, {}).get("is_group"),
                    disabled=self.warehouses.get(m.warehouse, {}).get("disabled"),
                )
                for m in self.mappings
                if m.company == company
            ]
        if "FROM `tabPOS Invoice`" in query:
            return [(1,)] if company in self.pending_pos else []
        if "FROM `tabBin` b" in query:
            if "SUM(b.actual_qty)" in query:
                return [Record(row) for row in self.unsold_rows]
            return [
                Record(warehouse=row.warehouse, stock_value=row.stock_value)
                for row in self.bins
                if row.warehouse in params["warehouses"]
                and self.warehouses[row.warehouse].company == company
            ]
        if "FROM `tabSales Invoice` si" in query:
            matching = [
                row
                for row in self.invoices
                if row.company == company and row.docstatus == 1 and Decimal(str(row.outstanding_amount)) > 0
            ]
            if "NOT EXISTS (" in query:
                return [
                    (1,)
                    for row in matching
                    if not any(m.company == company and m.branch == row.custom_branch for m in self.mappings)
                ][:1]
            return [Record(row) for row in matching if row.custom_branch in params["branches"]]
        raise AssertionError("Unexpected SQL: " + query)


class TestCreditControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.feature_prefixes = (
            "awht_custom.credit_control.",
            "awht_custom.awht_custom.report.awht_current_credit_exposure.",
            "awht_custom.awht_custom.report.awht_no_stock_out_items.",
            "awht_custom.awht_custom.doctype.awht_credit_policy.",
            "awht_custom.awht_custom.doctype.awht_branch_warehouse.",
        )
        cls.old_feature_modules = {
            name: module for name, module in sys.modules.items()
            if name.startswith(cls.feature_prefixes)
        }
        for name in cls.old_feature_modules:
            sys.modules.pop(name)
        cls.state = types.SimpleNamespace(
            permissions={}, roles=[], companies={"C1"}, fields=True, shared_only=False,
            branch_company_field=False, visibility_policy={"restricted": False},
            request_read=True, request_rights={"read", "write"}, request_names={"MR-1"},
            permission_calls=[], warehouse_names={"HEAD", "WH1", "WH2"},
        )
        cls.frappe = types.ModuleType("frappe")
        cls.frappe.__path__ = []
        cls.frappe._ = lambda message: message
        cls.frappe.PermissionError = PermissionError
        cls.frappe.session = Doc(user="manager@example.test")
        cls.frappe.whitelist = lambda **kwargs: lambda func: setattr(func, "allowed_methods", kwargs["methods"]) or func
        cls.frappe.read_only = lambda: lambda func: setattr(func, "read_only", True) or func
        cls.frappe.get_doc = lambda doctype, name: cls.frappe.db.requests[name] if doctype == "Material Request" else None
        cls.frappe.get_roles = lambda user: cls.state.roles
        cls.frappe.get_meta = lambda doctype: Doc(
            has_field=lambda field: cls.state.branch_company_field if field == "custom_company" else cls.state.fields
        )

        def has_permission(doctype, ptype="read", doc=None, *, ignore_share_permissions=False):
            cls.state.permission_calls.append((doctype, ptype, doc, ignore_share_permissions))
            if doctype == "Material Request":
                if doc is None:
                    return ptype == "read" and cls.state.request_read
                return doc.name in cls.state.request_names and ptype in cls.state.request_rights
            if doctype == "Warehouse":
                return ptype == "read" and doc in cls.state.warehouse_names and ignore_share_permissions
            return (
                doctype == "Company"
                and ptype == "read"
                and doc in cls.state.companies
                and not (cls.state.shared_only and ignore_share_permissions)
            )

        cls.frappe.has_permission = has_permission

        def throw(message, *, exc=None, **kwargs):
            raise (exc or ValidationError)(message)

        cls.frappe.throw = throw
        permissions = types.ModuleType("frappe.permissions")
        permissions.get_user_permissions = lambda user: cls.state.permissions
        permissions.get_allowed_docs_for_doctype = lambda entries, doctype: [
            entry["doc"] for entry in entries if entry.get("applicable_for") in (None, "", doctype)
        ]
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        model = types.ModuleType("frappe.model")
        model.__path__ = []
        document = types.ModuleType("frappe.model.document")
        class Document:
            def is_new(self):
                return getattr(self, "_is_new", True)

            def get_doc_before_save(self):
                return getattr(self, "_previous", None)

        document.Document = Document
        visibility = types.ModuleType("awht_custom.visibility.api")
        visibility.get_policy = lambda: cls.state.visibility_policy
        cls.stubs = {
            "frappe": cls.frappe,
            "frappe.permissions": permissions,
            "frappe.utils": utils,
            "frappe.model": model,
            "frappe.model.document": document,
            "awht_custom.visibility.api": visibility,
        }
        cls.old_modules = {name: sys.modules.get(name) for name in cls.stubs}
        sys.modules.update(cls.stubs)

        cls.gate = importlib.import_module("awht_custom.credit_control.stock_entry")
        cls.mapping = importlib.import_module("awht_custom.credit_control.mapping")
        cls.invoice = importlib.import_module("awht_custom.credit_control.invoice")
        cls.material_request = importlib.import_module("awht_custom.credit_control.material_request")
        cls.reporting = importlib.import_module("awht_custom.credit_control.reporting")
        cls.financial = importlib.import_module(
            "awht_custom.awht_custom.report.awht_current_credit_exposure.awht_current_credit_exposure"
        )
        cls.unsold = importlib.import_module("awht_custom.awht_custom.report.awht_no_stock_out_items.awht_no_stock_out_items")
        cls.policy_doctype = importlib.import_module(
            "awht_custom.awht_custom.doctype.awht_credit_policy.awht_credit_policy"
        )
        cls.mapping_doctype = importlib.import_module(
            "awht_custom.awht_custom.doctype.awht_branch_warehouse.awht_branch_warehouse"
        )

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name.startswith(cls.feature_prefixes):
                sys.modules.pop(name, None)
        sys.modules.update(cls.old_feature_modules)
        for name, previous in cls.old_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    def setUp(self):
        self.db = FakeDB()
        self.frappe.db = self.db
        self.frappe.session.user = "manager@example.test"
        self.state.permissions = {"Branch": [Record(doc="B1", applicable_for=None)]}
        self.state.roles = ["Accounts Manager"]
        self.state.companies = {"C1"}
        self.state.fields = True
        self.state.shared_only = False
        self.state.branch_company_field = False
        self.state.visibility_policy = {"restricted": False}
        self.state.request_read = True
        self.state.request_rights = {"read", "write"}
        self.state.request_names = {"MR-1"}
        self.state.permission_calls = []
        self.state.warehouse_names = {"HEAD", "WH1", "WH2"}

    def transfer(self, *values, purpose="Material Transfer", target="WH1"):
        return Doc(
            company="C1",
            purpose=purpose,
            stock_entry_type="Custom Branch Stock Move",
            items=[Doc(s_warehouse="HEAD", t_warehouse=target, amount=value) for value in values],
        )

    def invoice_row(self, branch="B1", outstanding=10, currency="SAR", company="C1", is_return=0):
        return Record(
            company=company,
            docstatus=1,
            custom_branch=branch,
            outstanding_amount=outstanding,
            party_account_currency=currency,
            is_return=is_return,
        )

    def request_row(self, qty=15, source="HEAD", target="WH1", item_code="SKU", factor=1):
        return Doc(
            item_code=item_code, from_warehouse=source, warehouse=target,
            qty=qty, stock_qty=qty * factor, conversion_factor=factor,
            amount=100000, idx=1,
        )

    def request(self, *rows, request_type="Material Transfer", company="C1", name="MR-1"):
        return Doc(
            doctype="Material Request", name=name, company=company, docstatus=0,
            material_request_type=request_type, items=list(rows) or [self.request_row()],
            set_from_warehouse="HEAD", set_warehouse="WH1",
        )

    def test_disabled_or_absent_policy_never_enforces(self):
        self.state.fields = False
        self.db.mappings = []
        for policy in (None, Record(company="C1", enabled=0, source_warehouse=None)):
            with self.subTest(policy=policy):
                self.db.policies = {"C1": policy} if policy else {}
                self.db.calls.clear()
                self.gate.validate_stock_entry(self.transfer(10000))
                self.assertFalse([call for call in self.db.calls if call[0] == "sql"])
                self.assertTrue([call for call in self.db.calls if call[1] == "AWHT Credit Policy" and call[-1]])

    def test_renamed_company_policy_still_enforces(self):
        self.db.policies = {"old-record-name": Record(company="C1", enabled=1, source_warehouse="HEAD")}
        with self.assertRaisesRegex(ValidationError, "Branch credit limit prevents"):
            self.gate.validate_stock_entry(self.transfer(31))

    def test_two_rows_are_summed_before_limit_check(self):
        self.db.invoices = [self.invoice_row()]
        with self.assertRaises(ValidationError) as denied:
            self.gate.validate_stock_entry(self.transfer(15, 15))
        self.assertEqual(str(denied.exception), "Branch credit limit prevents this Stock Entry from being submitted.")
        for secret in ("B1", "70", "10", "15", "30", "100", "stock", "unpaid", "transfer"):
            self.assertNotIn(secret, str(denied.exception))
        self.gate.validate_stock_entry(self.transfer(10, 10))
        sql = [call[1] for call in self.db.calls if call[0] == "sql"]
        self.assertTrue(all("FOR UPDATE" in query for query in sql))
        self.assertTrue(any(call[1] == "AWHT Credit Policy" and call[-1] for call in self.db.calls))

    def test_zero_missing_and_negative_limits_never_fail_open(self):
        for limit in (None, 0, -1):
            with self.subTest(limit=limit):
                self.db.branches["B1"] = limit
                with self.assertRaises(ValidationError) as denied:
                    self.gate.validate_stock_entry(self.transfer(0))
                self.assertEqual(str(denied.exception), "Branch credit limit prevents this Stock Entry from being submitted.")

    def test_custom_type_uses_purpose_not_stock_entry_type(self):
        self.gate.validate_stock_entry(self.transfer(20))
        self.gate.validate_stock_entry(self.transfer(20, purpose="Material Transfer for Manufacture"))
        outside_source = Doc(company="C1", purpose="Material Transfer", items=[Doc(s_warehouse="OTHER", t_warehouse="WH1", amount=200)])
        self.gate.validate_stock_entry(outside_source)
        with self.assertRaisesRegex(ValidationError, "supported transfer purpose"):
            self.gate.validate_stock_entry(self.transfer(20, purpose="Repack"))
        issue = Doc(company="C1", purpose="Material Issue", items=[Doc(s_warehouse="HEAD", t_warehouse=None)])
        self.gate.validate_stock_entry(issue)

    def test_cross_company_target_or_corrupt_mapping_is_rejected(self):
        self.db.mappings = [Record(name="FOREIGN", company="C2", branch="B1", warehouse="FOREIGN")]
        with self.assertRaisesRegex(ValidationError, "target needs a mapping"):
            self.gate.validate_stock_entry(self.transfer(10, target="FOREIGN"))
        self.db.mappings = [Record(name="FOREIGN", company="C1", branch="B1", warehouse="FOREIGN")]
        with self.assertRaisesRegex(ValidationError, "Invalid AWHT Branch Warehouse mapping"):
            self.gate.validate_stock_entry(self.transfer(10, target="FOREIGN"))

    def test_multiple_branches_and_all_mapped_warehouses_count(self):
        self.db.mappings.append(Record(name="WH2", company="C1", branch="B1", warehouse="WH2"))
        self.db.bins.append(Record(warehouse="WH2", stock_value=20))
        with self.assertRaisesRegex(ValidationError, "Branch credit limit prevents"):
            self.gate.validate_stock_entry(self.transfer(15))
        self.db.mappings[1].branch = "B2"
        self.gate.validate_stock_entry(self.transfer(15))

    def test_other_source_rows_targeting_branch_are_rejected_not_summed(self):
        doc = self.transfer(15)
        doc.items.append(Doc(s_warehouse="OTHER", t_warehouse="WH1", amount=15))
        with self.assertRaisesRegex(ValidationError, "Separate other-source branch targets"):
            self.gate.validate_stock_entry(doc)
        doc.items[-1].t_warehouse = "OTHER_TARGET"
        doc.items[-1].amount = 10000
        self.gate.validate_stock_entry(doc)

    def test_unattributed_invoice_foreign_currency_positive_return_and_pending_pos_block(self):
        for invoice, error in (
            (self.invoice_row(branch=None), "mapped custom_branch"),
            (self.invoice_row(currency="USD"), "unsupported branch, currency or return"),
            (self.invoice_row(is_return=1), "unsupported branch, currency or return"),
        ):
            with self.subTest(error=error):
                self.db.invoices = [invoice]
                with self.assertRaisesRegex(ValidationError, error):
                    self.gate.validate_stock_entry(self.transfer(5))
        self.db.invoices = []
        self.db.pending_pos.add("C1")
        with self.assertRaisesRegex(ValidationError, "Consolidate pending POS"):
            self.gate.validate_stock_entry(self.transfer(5))

    def test_negative_invoice_returns_do_not_reduce_exposure(self):
        self.db.bins[0].stock_value = 90
        self.db.invoices = [self.invoice_row(outstanding=-50, is_return=1)]
        with self.assertRaisesRegex(ValidationError, "Branch credit limit prevents"):
            self.gate.validate_stock_entry(self.transfer(15))

    def test_missing_fields_or_non_sar_company_blocks_enabled_gate(self):
        self.state.fields = False
        with self.assertRaisesRegex(ValidationError, "Install Branch"):
            self.gate.validate_stock_entry(self.transfer(5))
        self.state.fields = True
        self.db.companies["C1"] = "USD"
        with self.assertRaisesRegex(ValidationError, "use SAR"):
            self.gate.validate_stock_entry(self.transfer(5))

    def test_policy_lock_serializes_and_rechecks_after_commit(self):
        self.db.bins[0].stock_value = 85
        self.db.policy_lock = threading.Lock()
        first_done = threading.Event()
        allow_commit = threading.Event()
        second_done = threading.Event()
        results = []

        def first():
            try:
                self.gate.validate_stock_entry(self.transfer(15))
                first_done.set()
                if not allow_commit.wait(5):
                    raise RuntimeError("Commit was not released")
                self.db.bins[0].stock_value = 100
                results.append("first committed")
            except Exception as error:
                results.append(error)
                first_done.set()
            finally:
                self.db.policy_lock.release()

        def second():
            try:
                self.gate.validate_stock_entry(self.transfer(15))
                results.append("second passed")
            except Exception as error:
                results.append(error)
            finally:
                self.db.policy_lock.release()
                second_done.set()

        thread_one = threading.Thread(target=first, name="credit-first")
        thread_two = threading.Thread(target=second, name="credit-second")
        thread_one.start()
        try:
            self.assertTrue(first_done.wait(5))
            thread_two.start()
            self.assertTrue(self.db.second_waiting.wait(5))
            self.assertFalse(second_done.is_set())
        finally:
            allow_commit.set()
            thread_one.join(5)
            if thread_two.ident:
                thread_two.join(5)
        self.assertFalse(thread_one.is_alive())
        self.assertFalse(thread_two.is_alive())
        self.assertIn("first committed", results)
        self.assertTrue(any(isinstance(value, ValidationError) and "Branch credit limit prevents" in str(value) for value in results))
        bin_reads = [call for call in self.db.calls if call[0] == "sql" and "FROM `tabBin` b" in call[1]]
        self.assertEqual(len(bin_reads), 2)
        self.assertTrue(all("FOR UPDATE" in read[1] for read in bin_reads))

    def test_invoice_mapping_is_opt_in_and_never_overwrites_conflicts(self):
        rows = [Doc(warehouse=None), Doc(warehouse="WH1")]
        invoice = Doc(company="C1", custom_branch=None, set_warehouse=None, items=rows)
        self.invoice.map_invoice_branch_warehouse(invoice)
        self.assertIsNone(rows[0].warehouse)
        invoice.custom_branch = "B1"
        self.invoice.map_invoice_branch_warehouse(invoice)
        self.assertEqual(invoice.set_warehouse, "WH1")
        self.assertEqual([row.warehouse for row in rows], ["WH1", "WH1"])
        invoice.set_warehouse = None
        rows[0].warehouse = None
        rows[1].warehouse = "FOREIGN"
        with self.assertRaisesRegex(ValidationError, "item warehouse conflicts"):
            self.invoice.map_invoice_branch_warehouse(invoice)
        self.assertEqual(rows[1].warehouse, "FOREIGN")
        self.assertIsNone(rows[0].warehouse)
        self.assertIsNone(invoice.set_warehouse)
        invoice.set_warehouse = "FOREIGN"
        with self.assertRaisesRegex(ValidationError, "Set Warehouse conflicts"):
            self.invoice.map_invoice_branch_warehouse(invoice)

    def test_return_keeps_original_warehouse_without_branch_remapping(self):
        row = Doc(warehouse="FOREIGN")
        invoice = Doc(company="C1", custom_branch="B1", is_return=1,
                      set_warehouse="FOREIGN", items=[row])
        self.invoice.map_invoice_branch_warehouse(invoice)
        self.assertEqual(invoice.set_warehouse, "FOREIGN")
        self.assertEqual(row.warehouse, "FOREIGN")
        self.assertFalse(any(call[0] == "sql" and "tabAWHT Branch Warehouse" in call[1]
                             for call in self.db.calls))

    def test_invoice_mapping_rejects_ambiguous_and_foreign_company(self):
        doc = Doc(company="C1", custom_branch="B1", set_warehouse=None, items=[Doc(warehouse=None)])
        self.db.mappings.append(Record(name="WH2", company="C1", branch="B1", warehouse="WH2"))
        with self.assertRaisesRegex(ValidationError, "exactly one mapped warehouse"):
            self.invoice.map_invoice_branch_warehouse(doc)
        self.db.mappings = [Record(name="FOREIGN", company="C2", branch="B1", warehouse="FOREIGN")]
        with self.assertRaisesRegex(ValidationError, "exactly one mapped warehouse"):
            self.invoice.map_invoice_branch_warehouse(doc)
        self.assertIsNone(doc.items[0].warehouse)

    def test_policy_and_mapping_doctype_validation(self):
        policy = self.policy_doctype.AWHTCreditPolicy()
        policy.company, policy.enabled, policy.source_warehouse = "C1", 0, None
        policy.validate()
        policy.enabled = 1
        with self.assertRaisesRegex(ValidationError, "Select a head-office"):
            policy.validate()
        policy.source_warehouse = "HEAD"
        policy.validate()
        self.db.mappings = []
        with self.assertRaisesRegex(ValidationError, "Map at least one"):
            policy.validate()
        self.db.mappings = [Record(name="HEAD", company="C1", branch="B1", warehouse="HEAD")]
        with self.assertRaisesRegex(ValidationError, "source warehouse cannot"):
            policy.validate()

        mapping = self.mapping_doctype.AWHTBranchWarehouse()
        mapping.company, mapping.branch, mapping.warehouse = "C1", "B1", "FOREIGN"
        with self.assertRaisesRegex(ValidationError, "company C1"):
            mapping.validate()
        mapping.warehouse = "HEAD"
        with self.assertRaisesRegex(ValidationError, "policy source warehouse"):
            mapping.before_validate()
        self.db.calls.clear()
        with self.assertRaisesRegex(ValidationError, "Disable the credit policy"):
            mapping.on_trash()
        self.assertEqual(self.db.calls, [
            ("get_value", "AWHT Credit Policy", {"company": "C1"}, "enabled", False)
        ])
        self.db.policies["C1"].enabled = 0
        mapping.on_trash()

    def test_mapping_insert_locks_policy_but_existing_save_never_inverts_lock_order(self):
        mapping = self.mapping_doctype.AWHTBranchWarehouse()
        mapping.company, mapping.branch, mapping.warehouse = "C1", "B1", "WH1"
        mapping.before_validate()
        self.assertTrue(any(c[1] == "AWHT Credit Policy" and c[-1] for c in self.db.calls if c[0] == "get_value"))

        self.db.calls.clear()
        mapping._is_new = False
        mapping._previous = Doc(company="C1", branch="B1", warehouse="WH1")
        mapping.before_validate()
        mapping.validate()
        self.assertFalse(any(c[1] == "AWHT Credit Policy" for c in self.db.calls if c[0] == "get_value"))
        mapping.branch = "B2"
        with self.assertRaisesRegex(ValidationError, "Delete and recreate"):
            mapping.validate()
        self.assertFalse(any(c[1] == "AWHT Credit Policy" for c in self.db.calls if c[0] == "get_value"))

    def test_mapping_insert_waits_for_inflight_stock_entry_policy_lock(self):
        self.db.policy_lock = threading.Lock()
        submitted = threading.Event()
        allow_commit = threading.Event()
        inserted = threading.Event()
        errors = []

        def submit():
            try:
                self.gate.validate_stock_entry(self.transfer(10))
                submitted.set()
                if not allow_commit.wait(5):
                    raise RuntimeError("Commit not released")
            except Exception as error:
                errors.append(error)
                submitted.set()
            finally:
                self.db.policy_lock.release()

        def insert():
            mapping = self.mapping_doctype.AWHTBranchWarehouse()
            mapping.company, mapping.branch, mapping.warehouse = "C1", "B2", "WH2"
            try:
                mapping.before_validate()
                inserted.set()
            except Exception as error:
                errors.append(error)
            finally:
                self.db.policy_lock.release()

        first = threading.Thread(target=submit)
        second = threading.Thread(target=insert)
        first.start()
        try:
            self.assertTrue(submitted.wait(5))
            second.start()
            self.assertFalse(inserted.wait(0.05))
        finally:
            allow_commit.set()
            first.join(5)
            if second.ident:
                second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertFalse(errors)
        self.assertTrue(inserted.is_set())

    def test_optional_branch_company_must_match_mapping_company(self):
        self.state.branch_company_field = True
        self.db.branch_companies["B1"] = "C2"
        with self.assertRaisesRegex(ValidationError, "Branch company does not match"):
            self.gate.validate_stock_entry(self.transfer(5))
        with self.assertRaisesRegex(ValidationError, "Branch company does not match"):
            self.financial.execute({"company": "C1"})
        with self.assertRaisesRegex(ValidationError, "Branch company does not match"):
            self.invoice.map_invoice_branch_warehouse(
                Doc(company="C1", custom_branch="B1", set_warehouse=None, items=[Doc(warehouse=None)])
            )
        mapping = self.mapping_doctype.AWHTBranchWarehouse()
        mapping.company, mapping.branch, mapping.warehouse = "C1", "B1", "WH1"
        with self.assertRaisesRegex(ValidationError, "Branch company does not match"):
            mapping.validate()
        self.db.branch_companies["B1"] = None
        with self.assertRaisesRegex(ValidationError, "Branch company does not match"):
            mapping.validate()
        self.db.branch_companies["B1"] = "C1"
        mapping.validate()
        self.gate.validate_stock_entry(self.transfer(5))

    def test_material_request_sums_rows_using_source_valuation_not_request_amount(self):
        mr = self.request(self.request_row(), self.request_row())
        self.db.invoices = [self.invoice_row(outstanding=10)]
        self.assertEqual(self.material_request.assess_material_request(mr), "blocked")
        with self.assertRaisesRegex(ValidationError, "Branch credit limit prevents") as denied:
            self.material_request.validate_material_request(mr)
        self.assertNotIn("100000", str(denied.exception))
        self.assertTrue(any(call[1] == "Bin" and call[-1] for call in self.db.calls if call[0] == "get_value"))
        self.assertTrue(any(call[1] == "AWHT Credit Policy" and call[-1] for call in self.db.calls if call[0] == "get_value"))
        mr.items[1].qty = mr.items[1].stock_qty = 5
        self.assertEqual(self.material_request.assess_material_request(mr), "within_limit")
        self.material_request.validate_material_request(mr)
        # Submitting the MR does not change Bin or reserve the measured headroom.
        self.material_request.validate_material_request(mr)

    def test_material_request_handles_multiple_branches_and_stock_uom_conversion(self):
        self.db.mappings.append(Record(name="WH2", company="C1", branch="B2", warehouse="WH2"))
        self.db.bins.append(Record(warehouse="WH2", stock_value=80))
        second = self.request_row(qty=5, target="WH2", factor=2)
        mr = self.request(self.request_row(qty=10), second)
        self.assertEqual(self.material_request.assess_material_request(mr), "within_limit")
        second.stock_qty = 12  # An inflated value must not reduce the projected transfer.
        self.assertEqual(self.material_request.assess_material_request(mr), "within_limit")
        second.qty = 6
        second.stock_qty = 14
        self.assertEqual(self.material_request.assess_material_request(mr), "within_limit")
        second.stock_qty = 21
        self.assertEqual(self.material_request.assess_material_request(mr), "blocked")

    def test_material_request_uses_row_warehouses_and_rejects_ambiguous_branch_targets(self):
        mr = self.request(self.request_row(source="OTHER", target="WH1"))
        self.assertEqual(self.material_request.assess_material_request(mr), "not_applicable")
        mr.items[0].from_warehouse = None
        with self.assertRaisesRegex(ValidationError, "both warehouses"):
            self.material_request.validate_material_request(mr)
        mr.items[0].from_warehouse = "HEAD"
        mr.items[0].warehouse = "FOREIGN"
        with self.assertRaisesRegex(ValidationError, "target needs a mapping"):
            self.material_request.validate_material_request(mr)
        mr.items[0].warehouse = "WH1"
        mr.items.append(self.request_row(source="OTHER", target="WH1"))
        with self.assertRaisesRegex(ValidationError, "Separate other-source"):
            self.material_request.validate_material_request(mr)
        self.db.mappings[0].company = "C2"
        with self.assertRaisesRegex(ValidationError, "target needs a mapping"):
            self.material_request.validate_material_request(self.request(self.request_row()))

    def test_material_request_is_inert_without_enabled_policy_and_avoids_unrelated_types(self):
        mr = self.request(self.request_row(qty=0, source=None, target=None))
        self.db.policies["C1"].enabled = 0
        self.assertEqual(self.material_request.assess_material_request(mr), "inactive")
        self.material_request.validate_material_request(mr)
        self.assertFalse(any(call[0] == "sql" for call in self.db.calls))
        self.db.mappings = []
        self.db.source_bins = {}
        self.material_request.validate_material_request(mr)
        self.db.policies = {}
        self.assertEqual(self.material_request.assess_material_request(mr), "inactive")
        self.db.mappings = [Record(name="WH1", company="C1", branch="B1", warehouse="WH1")]
        self.db.source_bins = {("HEAD", "SKU"): Record(actual_qty=100, valuation_rate=1)}
        self.db.policies = {"C1": Record(company="C1", enabled=1, source_warehouse="HEAD")}
        self.assertEqual(self.material_request.assess_material_request(
            self.request(self.request_row(source="OTHER"))
        ), "not_applicable")
        self.assertEqual(self.material_request.assess_material_request(
            self.request(self.request_row(source="HEAD", target=None), request_type="Material Issue")
        ), "not_applicable")
        with self.assertRaisesRegex(ValidationError, "Material Transfer purpose"):
            self.material_request.validate_material_request(
                self.request(self.request_row(), request_type="Manufacture")
            )

    def test_material_request_fails_on_pending_pos_currency_and_missing_valuation(self):
        mr = self.request(self.request_row())
        self.db.pending_pos.add("C1")
        with self.assertRaisesRegex(ValidationError, "Consolidate pending POS"):
            self.material_request.validate_material_request(mr)
        self.db.pending_pos.clear()
        self.db.companies["C1"] = "USD"
        with self.assertRaisesRegex(ValidationError, "use SAR"):
            self.material_request.validate_material_request(mr)
        self.db.companies["C1"] = "SAR"
        for source_bin in (None, Record(actual_qty=0, valuation_rate=1),
                           Record(actual_qty=10, valuation_rate=1),
                           Record(actual_qty=100, valuation_rate=0)):
            with self.subTest(source_bin=source_bin), self.assertRaisesRegex(
                ValidationError, "Source stock or a positive valuation rate"
            ):
                self.db.source_bins = {("HEAD", "SKU"): source_bin} if source_bin else {}
                self.material_request.validate_material_request(mr)

    def test_material_request_rejects_missing_rows_and_unsupported_items(self):
        mr = self.request(self.request_row())
        mr.items = []
        with self.assertRaisesRegex(ValidationError, "both warehouses"):
            self.material_request.validate_material_request(mr)
        mr.items = [self.request_row(qty=0)]
        with self.assertRaisesRegex(ValidationError, "Positive quantity"):
            self.material_request.validate_material_request(mr)
        mr.items[0].qty = mr.items[0].stock_qty = 5
        for item in (None, Record(is_stock_item=0), Record(is_stock_item=1, has_serial_no=1),
                     Record(is_stock_item=1, has_batch_no=1)):
            with self.subTest(item=item), self.assertRaisesRegex(ValidationError, "Only non-serialized"):
                self.db.items = {"SKU": item} if item else {}
                self.material_request.validate_material_request(mr)

    def test_material_request_zero_and_missing_limits_fail_closed_even_for_administrator(self):
        mr = self.request(self.request_row(qty=0.5))
        for limit in (None, 0, -1):
            with self.subTest(limit=limit):
                self.db.branches["B1"] = limit
                self.assertEqual(self.material_request.assess_material_request(mr), "blocked")
                with self.assertRaisesRegex(ValidationError, "Branch credit limit prevents"):
                    self.material_request.validate_material_request(mr)
        self.frappe.session.user = "Administrator"
        self.state.request_names.add("MR-1")
        self.assertEqual(self.material_request.assess_material_request(mr), "blocked")

    def test_material_request_preview_post_checks_saved_draft_and_permissions(self):
        preview = self.material_request.preview_limit
        self.assertEqual(preview.allowed_methods, ["POST"])
        self.assertTrue(preview.read_only)
        doc = self.request(self.request_row(qty=5))
        self.db.requests["MR-1"] = doc
        self.assertEqual(preview("MR-1"), {"status": "within_limit"})
        self.assertTrue(any(call[0] == "Material Request" and call[1] == "write" and call[3]
                            for call in self.state.permission_calls))
        self.assertTrue(any(call[0] == "Material Request" and call[1] == "read" and call[3]
                            for call in self.state.permission_calls))
        self.assertFalse(any(call[1] == "Bin" and call[-1] for call in self.db.calls if call[0] == "get_value"))
        doc.docstatus = 1
        with self.assertRaisesRegex(ValidationError, "saved draft"):
            preview("MR-1")
        self.state.request_rights = {"read"}
        with self.assertRaises(PermissionError):
            preview("MR-1")

    def test_material_request_preview_denies_untrusted_or_restricted_readers_before_amount_queries(self):
        self.db.requests["MR-1"] = self.request(self.request_row())
        for change in (
            lambda: setattr(self.state, "request_read", False),
            lambda: setattr(self.state, "request_rights", {"read"}),
            lambda: setattr(self.state, "visibility_policy", {"restricted": True}),
            lambda: setattr(self.state, "roles", ["Stock User"]),
            lambda: setattr(self.state, "permissions", {"Branch": [Record(doc="B2")]}),
            lambda: setattr(self.state, "companies", set()),
            lambda: setattr(self.state, "warehouse_names", {"WH1"}),
        ):
            with self.subTest(change=change):
                self.setUp()
                self.db.requests["MR-1"] = self.request(self.request_row())
                change()
                with self.assertRaises(PermissionError):
                    self.material_request.preview_limit("MR-1")
                self.assertFalse(any(call[1] in ("Item", "Bin") for call in self.db.calls if call[0] == "get_value"))

    def test_material_request_preview_never_echoes_requested_or_financial_amounts(self):
        mr = self.request(self.request_row(qty=31))
        self.db.requests["MR-1"] = mr
        self.assertEqual(self.material_request.preview_limit("MR-1"), {"status": "blocked"})
        self.db.policies["C1"].enabled = 0
        self.assertEqual(self.material_request.preview_limit("MR-1"), {"status": "inactive"})

    def test_material_request_branch_company_and_missing_stock_qty_fail_closed(self):
        mr = self.request(self.request_row())
        self.state.branch_company_field = True
        self.db.branch_companies["B1"] = "C2"
        with self.assertRaisesRegex(ValidationError, "Branch company does not match"):
            self.material_request.validate_material_request(mr)
        self.db.branch_companies["B1"] = "C1"
        mr.items[0].stock_qty = None
        with self.assertRaisesRegex(ValidationError, "amount is missing"):
            self.material_request.validate_material_request(mr)

    def test_material_request_preview_pending_pos_never_returns_values(self):
        self.db.requests["MR-1"] = self.request(self.request_row())
        self.db.pending_pos.add("C1")
        with self.assertRaisesRegex(ValidationError, "Consolidate pending POS") as denied:
            self.material_request.preview_limit("MR-1")
        self.assertNotIn("70", str(denied.exception))

    def test_report_rejects_unprivileged_roles_stale_shares_and_wrong_company(self):
        self.state.roles = ["Accounts User"]
        with self.assertRaises(PermissionError):
            self.financial.execute({"company": "C1"})
        self.state.roles = ["Accounts Manager"]
        self.state.shared_only = True
        with self.assertRaises(PermissionError):
            self.financial.execute({"company": "C1"})
        self.state.shared_only = False
        with self.assertRaises(PermissionError):
            self.unsold.execute({"company": "C2", "from_date": "2026-01-01", "to_date": "2026-02-01"})
        self.state.permissions = {}
        with self.assertRaises(PermissionError):
            self.financial.execute({"company": "C1"})

    def test_restricted_visibility_denies_financial_report_before_any_amount_reads(self):
        self.state.visibility_policy = {"restricted": True, "patterns": ["stock value"]}
        with self.assertRaises(PermissionError) as denied:
            self.financial.execute({"company": "C1"})
        self.assertEqual(str(denied.exception), "This report is unavailable.")
        self.assertFalse(any(call[0] == "sql" for call in self.db.calls))
        self.assertFalse(any(call[1] == "Branch" for call in self.db.calls if call[0] == "get_value"))
        for name in ("Branch Financial Exposure", "AWHT Current Credit Exposure"):
            with self.subTest(report=name), self.assertRaises(PermissionError):
                self.reporting.assert_credit_report_access(name)

    def test_direct_execute_denies_prepared_flags_and_cached_reports(self):
        cases = (
            ("AWHT Current Credit Exposure", self.financial.execute, {"company": "C1"}),
            ("AWHT No Stock Out Items", self.unsold.execute,
             {"company": "C1", "from_date": "2026-01-01", "to_date": "2026-01-31"}),
        )
        for name, execute, filters in cases:
            with self.subTest(report=name, mode="enabled"):
                self.db.prepared_flags[name] = 1
                with self.assertRaisesRegex(PermissionError, "Prepared data is unavailable"):
                    execute(filters)
                self.assertFalse(any(call[0] == "sql" for call in self.db.calls))
                self.db.prepared_flags.clear()
                self.db.calls.clear()
            with self.subTest(report=name, mode="cached"):
                self.db.cached_reports.add(name)
                with self.assertRaisesRegex(PermissionError, "Prepared data is unavailable"):
                    execute(filters)
                self.assertFalse(any(call[0] == "sql" for call in self.db.calls))
                self.assertTrue(any(call[0] == "exists" and call[1] == "Prepared Report" for call in self.db.calls))
                self.db.cached_reports.clear()
                self.db.calls.clear()
        self.assertIsNone(self.reporting.assert_credit_report_access("Unrelated Report"))

    def test_legacy_report_names_retain_prepared_data_guards(self):
        for name in ("Branch Financial Exposure", "Unsold Items"):
            with self.subTest(report=name, mode="enabled"):
                self.db.prepared_flags[name] = 1
                with self.assertRaisesRegex(PermissionError, "Prepared data is unavailable"):
                    self.reporting.assert_credit_report_access(name)
                self.db.prepared_flags.clear()
            with self.subTest(report=name, mode="cached"):
                self.db.cached_reports.add(name)
                with self.assertRaisesRegex(PermissionError, "Prepared data is unavailable"):
                    self.reporting.assert_credit_report_access(name)
                self.db.cached_reports.clear()

    def test_branch_grant_and_warehouse_grant_prevent_partial_or_cross_company_reads(self):
        self.db.mappings.append(Record(name="WH2", company="C1", branch="B2", warehouse="WH2"))
        self.db.mappings.append(Record(name="FOREIGN", company="C2", branch="B1", warehouse="FOREIGN"))
        self.db.bins.append(Record(warehouse="FOREIGN", stock_value=9999))
        self.db.invoices = [self.invoice_row(branch="B2", outstanding=1000)]
        _, data = self.financial.execute({"company": "C1"})
        self.assertEqual([row["branch"] for row in data], ["B1"])
        self.assertEqual(data[0]["stock_value"], 70)
        with self.assertRaises(PermissionError):
            self.financial.execute({"company": "C1", "branch": "B2"})
        scoped = [call for call in self.db.calls if call[0] == "sql" and "FROM `tabBin` b" in call[1]]
        self.assertEqual(scoped[0][2]["warehouses"], ("WH1",))
        self.assertIn("w.company = %(company)s", scoped[0][1])
        self.state.permissions["Warehouse"] = [Record(doc="FOREIGN")]
        with self.assertRaises(PermissionError):
            self.financial.execute({"company": "C1"})

    def test_financial_exposure_omits_unsound_sales_payments_and_profit(self):
        self.db.invoices = [
            self.invoice_row(outstanding=15),
            self.invoice_row(outstanding=-50, is_return=1),
        ]
        columns, data = self.financial.execute({"company": "C1"})
        self.assertEqual(data[0]["unpaid_invoices"], 15)
        self.assertEqual(data[0]["total_exposure"], 85)
        self.assertEqual(data[0]["available_credit"], 15)
        self.assertEqual(columns[0]["fieldtype"], "Data")
        self.assertFalse({"total_payments", "gross_profit", "total_sales"} & {c["fieldname"] for c in columns})
        self.db.branches["B1"] = None
        _, data = self.financial.execute({"company": "C1"})
        self.assertIsNone(data[0]["available_credit"])
        self.db.invoices = [self.invoice_row(branch=None)]
        with self.assertRaisesRegex(ValidationError, "mapped custom_branch"):
            self.financial.execute({"company": "C1"})
        self.db.invoices = [self.invoice_row(currency="USD")]
        with self.assertRaisesRegex(ValidationError, "unsupported branch, currency or return"):
            self.financial.execute({"company": "C1"})

    def test_reports_fail_on_pending_pos_or_non_sar_financials(self):
        self.db.pending_pos.add("C1")
        with self.assertRaisesRegex(ValidationError, "Consolidate pending POS"):
            self.financial.execute({"company": "C1"})
        with self.assertRaisesRegex(ValidationError, "Consolidate pending POS"):
            self.unsold.execute({"company": "C1", "from_date": "2026-01-01", "to_date": "2026-02-01"})
        self.db.pending_pos.clear()
        self.db.companies["C1"] = "USD"
        with self.assertRaisesRegex(ValidationError, "use SAR"):
            self.financial.execute({"company": "C1"})

    def test_unsold_stock_is_company_and_warehouse_scoped(self):
        self.db.unsold_rows = [
            Record(
                item_code="ITM",
                item_name="Item",
                item_group="Group",
                brand="Brand",
                stock_uom="Nos",
                stock_qty=5,
                stock_value=50,
                min_qty=5,
                min_stock_value=50,
                last_stock_out_before_period=date(2025, 12, 31),
            )
        ]
        columns, data, _, _, summary = self.unsold.execute(
            {"company": "C1", "branch": "B1", "from_date": "2026-01-01", "to_date": "2026-01-31"}
        )
        self.assertEqual(data[0]["days_since_last"], 31)
        self.assertEqual(summary[1]["currency"], "SAR")
        self.assertIn("stock_value", {column["fieldname"] for column in columns})
        self.assertFalse(any(column["fieldtype"] == "Link" for column in columns))
        query = [call for call in self.db.calls if call[0] == "sql" and "SUM(b.actual_qty)" in call[1]][0]
        self.assertEqual(query[2]["warehouses"], ("WH1",))
        for fragment in (
            "w.company = %(company)s",
            "b.warehouse IN %(warehouses)s",
            "outgoing.company = %(company)s",
            "outgoing.warehouse IN %(warehouses)s",
            "outgoing.actual_qty < 0",
            "outgoing.is_cancelled = 0",
        ):
            self.assertIn(fragment, query[1])
        self.db.companies["C1"] = "USD"
        _, usd_data, _, _, usd_summary = self.unsold.execute(
            {"company": "C1", "from_date": "2026-01-01", "to_date": "2026-01-31"}
        )
        self.assertEqual(usd_data[0]["currency"], "USD")
        self.assertEqual(usd_summary[1]["currency"], "USD")

    def test_unsold_invalid_period_and_negative_bin_balance_fail_closed(self):
        with self.assertRaisesRegex(ValidationError, "From Date cannot be after"):
            self.unsold.execute({"company": "C1", "from_date": "2026-02-01", "to_date": "2026-01-01"})
        self.db.unsold_rows = [
            Record(min_qty=-1, min_stock_value=10, last_stock_out_before_period=None)
        ]
        with self.assertRaisesRegex(ValidationError, "Negative Bin balances"):
            self.unsold.execute({"company": "C1", "from_date": "2026-01-01", "to_date": "2026-01-31"})

    def test_doctype_and_report_metadata(self):
        root = Path(__file__).resolve().parents[1] / "awht_custom"
        policy = json.loads((root / "doctype/awht_credit_policy/awht_credit_policy.json").read_text())
        mapping = json.loads((root / "doctype/awht_branch_warehouse/awht_branch_warehouse.json").read_text())
        self.assertEqual(next(f for f in policy["fields"] if f["fieldname"] == "enabled")["default"], "0")
        self.assertTrue(next(f for f in mapping["fields"] if f["fieldname"] == "warehouse")["unique"])
        self.assertTrue(all(field["set_only_once"] for field in mapping["fields"]))
        reports = {
            "awht_current_credit_exposure": {
                "name": "AWHT Current Credit Exposure",
                "ref_doctype": "Company",
                "roles": {"Accounts Manager", "System Manager"},
            },
            "awht_no_stock_out_items": {
                "name": "AWHT No Stock Out Items",
                "ref_doctype": "Company",
                "roles": {"Accounts Manager", "System Manager"},
            },
            "branch_financial_exposure": {
                "name": "Branch Financial Exposure",
                "ref_doctype": "Sales Invoice",
                "roles": {
                    "Accounts User", "Accounts Manager",
                    "Employee Self Service", "Sales Master Manager",
                },
            },
            "unsold_items": {
                "name": "Unsold Items",
                "ref_doctype": "Stock Ledger Entry",
                "roles": {"Stock User", "Accounts Manager", "Report User awht"},
            },
        }
        for slug, expected in reports.items():
            name = expected["name"]
            meta = json.loads((root / f"report/{slug}/{slug}.json").read_text())
            self.assertEqual(meta["name"], name)
            self.assertEqual(meta["report_name"], name)
            self.assertEqual(meta["module"], "AWHT Custom")
            self.assertEqual(meta["ref_doctype"], expected["ref_doctype"])
            self.assertEqual(meta["prepared_report"], 0)
            self.assertEqual({entry["role"] for entry in meta["roles"]}, expected["roles"])
            self.assertIn(f'frappe.query_reports["{name}"]', (root / f"report/{slug}/{slug}.js").read_text())
            for extension in ("py", "js", "json"):
                self.assertTrue((root / f"report/{slug}/{slug}.{extension}").exists())
        owned_names = {
            json.loads(path.read_text())["name"] for path in (root / "report").glob("*/*.json")
        }
        self.assertEqual(owned_names, {expected["name"] for expected in reports.values()})


if __name__ == "__main__":
    unittest.main()
