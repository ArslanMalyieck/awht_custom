"""Site-free policy, settings and ownership-migration contract tests."""

import importlib
import json
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from awht_custom.awht_custom.doctype.cost_visibility_settings import cost_visibility_settings
from awht_custom.visibility import api


def settings(enabled=1, users=(), patterns=()):
    return SimpleNamespace(
        enabled=enabled,
        restricted_users=[SimpleNamespace(user=user) for user in users],
        extra_patterns=[SimpleNamespace(pattern=pattern) for pattern in patterns],
    )


class TestPolicy(unittest.TestCase):
    def policy(self, user, config):
        with patch.object(api.frappe, "session", SimpleNamespace(user=user)), \
                patch.object(api.frappe, "get_doc", return_value=config):
            return api.get_policy()

    def test_no_users_or_disabled_has_no_effect(self):
        self.assertEqual(self.policy("user@example.com", settings()), {"restricted": False, "patterns": []})
        self.assertEqual(
            self.policy("user@example.com", settings(enabled=0, users=["user@example.com"])),
            {"restricted": False, "patterns": []},
        )
        self.assertEqual(
            self.policy("other@example.com", settings(users=["user@example.com"])),
            {"restricted": False, "patterns": []},
        )

    def test_selected_user_gets_lowercase_literal_patterns(self):
        result = self.policy(
            "user@example.com", settings(users=["user@example.com"], patterns=["  Supplier Cost  "])
        )
        self.assertEqual(result, {"restricted": True, "patterns": [*api.DEFAULT_PATTERNS, "supplier cost"]})
        self.assertNotIn("rate", result["patterns"])
        self.assertNotIn("price", result["patterns"])

    def test_administrator_and_guest_are_never_restricted(self):
        for user in ("Administrator", "Guest"):
            with self.subTest(user=user):
                self.assertEqual(
                    self.policy(user, settings(users=[user])), {"restricted": False, "patterns": []}
                )

    def test_bootinfo_uses_current_session_policy(self):
        config = settings(users=["user@example.com"], patterns=[" Supplier Cost "])
        for user in ("user@example.com", "other@example.com"):
            with self.subTest(user=user):
                bootinfo = SimpleNamespace()
                with patch.object(api.frappe, "session", SimpleNamespace(user=user)), \
                        patch.object(api.frappe, "get_doc", return_value=config):
                    api.extend_bootinfo(bootinfo)
                    self.assertEqual(bootinfo.cost_visibility, api.get_policy())

    def test_explicit_file_permission_user_does_not_switch_session(self):
        config = settings(users=["restricted@example.com"])
        with patch.object(api.frappe, "session", SimpleNamespace(user="Administrator")) as session, \
                patch.object(api.frappe, "get_doc", return_value=config):
            self.assertTrue(api.policy_for_user("restricted@example.com")["restricted"])
            self.assertFalse(api.policy_for_user("other@example.com")["restricted"])
            self.assertFalse(api.policy_for_user("Administrator")["restricted"])
            self.assertEqual(session.user, "Administrator")


class TestSettingsValidation(unittest.TestCase):
    def validate(self, config):
        with patch.object(cost_visibility_settings, "_", side_effect=lambda text: text), \
                patch.object(cost_visibility_settings.frappe, "throw", side_effect=ValueError):
            cost_visibility_settings.CostVisibilitySettings.validate(config)

    def test_invalid_users(self):
        for users in (["Administrator"], ["user@example.com", "user@example.com"]):
            with self.subTest(users=users), self.assertRaises(ValueError):
                self.validate(settings(users=users))

    def test_patterns_are_normalized_and_duplicates_rejected(self):
        config = settings(patterns=["  Supplier Cost  "])
        self.validate(config)
        self.assertEqual(config.extra_patterns[0].pattern, "supplier cost")
        for patterns in ([" "], ["Supplier Cost", " supplier cost "], ["VALUATION RATE"]):
            with self.subTest(patterns=patterns), self.assertRaises(ValueError):
                self.validate(settings(patterns=patterns))


class TestSchema(unittest.TestCase):
    def test_single_and_children_keep_exact_persisted_names_and_permissions(self):
        root = Path(__file__).resolve().parents[1] / "awht_custom" / "doctype"
        for slug, name, field, kind in (
            ("cost_visibility_settings", "Cost Visibility Settings", "enabled", "Check"),
            ("restricted_cost_visibility_user", "Restricted Cost Visibility User", "user", "Link"),
            ("cost_visibility_pattern", "Cost Visibility Pattern", "pattern", "Data"),
        ):
            with self.subTest(doctype=name):
                schema = json.loads((root / slug / f"{slug}.json").read_text(encoding="utf-8"))
                self.assertEqual((schema["name"], schema["module"]), (name, "AWHT Custom"))
                self.assertIn({"fieldname": field, "fieldtype": kind},
                              [{k: df[k] for k in ("fieldname", "fieldtype")} for df in schema["fields"]])
                if slug == "cost_visibility_settings":
                    self.assertEqual(schema["issingle"], 1)
                    self.assertEqual(schema["field_order"], ["enabled", "restricted_users", "extra_patterns"])
                    self.assertEqual([df["options"] for df in schema["fields"] if df["fieldtype"] == "Table"],
                                     ["Restricted Cost Visibility User", "Cost Visibility Pattern"])
                    self.assertEqual(schema["permissions"],
                                     [{"role": "System Manager", "read": 1, "write": 1, "create": 1}])
                else:
                    self.assertEqual(schema["istable"], 1)
                    self.assertEqual(schema["permissions"], [])

    def test_hook_and_pdf_runner_targets_resolve_under_new_package(self):
        from awht_custom.visibility import file_filter, report_builder, report_filter

        paths = [
            "awht_custom.visibility.api.extend_bootinfo",
            "awht_custom.visibility.print_filter.pdf_body_html",
            *report_builder.OVERRIDE_WHITELISTED_METHODS.values(),
            *report_filter.OVERRIDE_WHITELISTED_METHODS.values(),
            *file_filter.OVERRIDE_WHITELISTED_METHODS.values(),
            "awht_custom.visibility.file_filter.PreparedReportFileMixin",
            "awht_custom.visibility.file_filter.has_file_permission",
            "awht_custom.visibility.report_filter.run_export_query_job",
            "awht_custom.visibility.report_builder.run_report_view_export_job",
        ]
        with patch.object(frappe, "logger", return_value=logging.getLogger("awht_visibility_test")):
            for path in paths:
                with self.subTest(path=path):
                    module, name = path.rsplit(".", 1)
                    self.assertTrue(callable(getattr(importlib.import_module(module), name)))
        self.assertIs(report_filter.run, importlib.import_module("awht_custom.visibility.report_filter").run)
