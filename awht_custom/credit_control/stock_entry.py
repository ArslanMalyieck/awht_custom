"""Submission-only credit gate for head-office transfers.

The policy row lock lasts until the Stock Entry transaction commits and
serializes Stock Entries using this hook. Locking Bin and invoice reads avoids
stale repeatable-read snapshots. Other stock/invoice writers, valuation reposts,
retention/advances outside invoice outstanding and POS consolidation do not
acquire this policy lock. Unconsolidated POS is rejected when observed; new POS
or non-Stock-Entry stock changes can still race the check.
"""

from collections import defaultdict
from decimal import Decimal

import frappe
from frappe import _

from awht_custom.credit_control.exposure import (
    amount,
    ensure_no_pending_pos,
    get_stock_values,
    get_unpaid_invoices,
    require_credit_fields,
    require_sar_company,
)
from awht_custom.credit_control.mapping import get_company_mappings, validate_warehouse


TRANSFER_PURPOSES = {"Material Transfer", "Material Transfer for Manufacture"}


def validate_stock_entry(doc, method=None):
    """Hook for Stock Entry.before_submit; never run as a draft validation."""
    # This persisted row is the common lock for every branch of this company.
    policy = frappe.db.get_value(
        "AWHT Credit Policy",
        {"company": doc.company},
        ["enabled", "source_warehouse"],
        as_dict=True,
        for_update=True,
    )
    if not policy or not policy.enabled:
        return
    if not policy.source_warehouse:
        frappe.throw(_("Enabled AWHT Credit Policy has no head-office source warehouse."))

    source_rows = [row for row in doc.items if row.s_warehouse == policy.source_warehouse]
    if not source_rows:
        return
    if doc.purpose not in TRANSFER_PURPOSES:
        if any(row.t_warehouse for row in doc.items):
            frappe.throw(_("Head-office stock entering a branch requires a supported transfer purpose."))
        return

    require_credit_fields()
    require_sar_company(doc.company)
    validate_warehouse(doc.company, policy.source_warehouse, for_update=True)
    mappings = get_company_mappings(doc.company, for_update=True)
    target_branches = {warehouse: branch for branch, names in mappings.items() for warehouse in names}
    if policy.source_warehouse in target_branches:
        frappe.throw(_("The head-office source warehouse cannot also be a branch warehouse."))

    transfers = defaultdict(lambda: Decimal(0))
    for row in source_rows:
        if not row.t_warehouse or row.t_warehouse not in target_branches:
            frappe.throw(_("Every head-office transfer target needs a mapping in this company."))
    for row in doc.items:
        if row.t_warehouse in target_branches and row.s_warehouse != policy.source_warehouse:
            frappe.throw(_("Separate other-source branch targets into another Stock Entry."))
    for row in source_rows:
        value = amount(row.amount)
        if value < 0:
            frappe.throw(_("Transfer amount cannot be negative."))
        transfers[target_branches[row.t_warehouse]] += value

    ensure_no_pending_pos(doc.company, for_update=True)
    stock = get_stock_values(doc.company, mappings, for_update=True)
    unpaid = get_unpaid_invoices(doc.company, mappings, transfers, for_update=True)
    for branch in sorted(transfers):
        limit = frappe.db.get_value("Branch", branch, "custom_credit_limit", for_update=True)
        if limit is None or amount(limit) <= 0:
            frappe.throw(_("Branch credit limit prevents this Stock Entry from being submitted."))
        projected = stock[branch] + unpaid[branch] + transfers[branch]
        if projected > amount(limit):
            frappe.throw(_("Branch credit limit prevents this Stock Entry from being submitted."))
