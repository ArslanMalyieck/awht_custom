"""Opt-in Material Request estimate; only the Stock Entry moves stock.

Material Request amount is a commercial rate, not stock valuation. Use current
source Bin valuation for a provisional stock-UOM cost, never a reservation.
Two submitted requests can both pass for the same headroom; the Stock Entry
before_submit gate remains authoritative at the actual movement. Serial/batch
valuation, missing rates and insufficient source stock cannot be projected.
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
from awht_custom.credit_control.reporting import get_credit_scope
from awht_custom.visibility.api import get_policy


def assess_material_request(doc, *, for_update=False, permitted_branches=None):
    """Return inactive/not_applicable/within_limit/blocked, never financial details."""
    policy = frappe.db.get_value(
        "AWHT Credit Policy", {"company": doc.company}, ["enabled", "source_warehouse"],
        as_dict=True, for_update=for_update,
    )
    if not policy or not policy.enabled:
        return "inactive"
    if not policy.source_warehouse:
        frappe.throw(_("Enabled AWHT Credit Policy has no head-office source warehouse."))

    rows = doc.get("items") or []
    if doc.material_request_type != "Material Transfer":
        if any(row.from_warehouse == policy.source_warehouse and row.warehouse for row in rows):
            frappe.throw(_("Head-office branch requests require the Material Transfer purpose."))
        return "not_applicable"
    if not rows or any(not row.from_warehouse or not row.warehouse for row in rows):
        frappe.throw(_("Set both warehouses on every Material Transfer item row."))

    source_rows = [row for row in rows if row.from_warehouse == policy.source_warehouse]
    if not source_rows:
        return "not_applicable"

    require_credit_fields()
    require_sar_company(doc.company)
    validate_warehouse(doc.company, policy.source_warehouse, for_update=for_update)
    if permitted_branches is not None and not frappe.has_permission(
        "Warehouse", "read", doc=policy.source_warehouse, ignore_share_permissions=True
    ):
        frappe.throw(_("Not permitted to preview the source warehouse."), exc=frappe.PermissionError)
    mappings = get_company_mappings(doc.company, for_update=for_update)
    targets = {warehouse: branch for branch, names in mappings.items() for warehouse in names}
    if policy.source_warehouse in targets:
        frappe.throw(_("The head-office source warehouse cannot also be a branch warehouse."))
    if any(row.warehouse not in targets for row in source_rows):
        frappe.throw(_("Every head-office request target needs a mapping in this company."))
    if any(row.warehouse in targets and row.from_warehouse != policy.source_warehouse for row in rows):
        frappe.throw(_("Separate other-source branch targets into another Material Request."))

    requested = defaultdict(lambda: Decimal(0))
    by_branch = defaultdict(list)
    for row in source_rows:
        branch = targets[row.warehouse]
        if permitted_branches is not None and branch not in permitted_branches:
            frappe.throw(_("Not permitted to preview this branch."), exc=frappe.PermissionError)
        if not row.item_code:
            frappe.throw(_("Set an item code on every head-office request row."))
        qty, factor, stock_qty = amount(row.qty), amount(row.conversion_factor), amount(row.stock_qty)
        if min(qty, factor, stock_qty) <= 0:
            frappe.throw(_("Positive quantity and stock-UOM conversion are required."))
        # If stock_qty has been rounded, use the larger figure, never an understated projection.
        projected_qty = max(qty * factor, stock_qty)
        requested[row.item_code] += projected_qty
        by_branch[branch].append((row.item_code, projected_qty))

    rates = {}
    for item_code, qty in requested.items():
        item = frappe.db.get_value(
            "Item", item_code, ["is_stock_item", "has_serial_no", "has_batch_no"], as_dict=True
        )
        if not item or not item.is_stock_item or item.has_serial_no or item.has_batch_no:
            frappe.throw(_("Only non-serialized, non-batched stock items can be valued for this request."))
        source_bin = frappe.db.get_value(
            "Bin", {"item_code": item_code, "warehouse": policy.source_warehouse},
            ["actual_qty", "valuation_rate"], as_dict=True, for_update=for_update,
        )
        if not source_bin or amount(source_bin.actual_qty) < qty or amount(source_bin.valuation_rate) <= 0:
            frappe.throw(_("Source stock or a positive valuation rate is unavailable for this request."))
        rates[item_code] = amount(source_bin.valuation_rate)

    transfers = {
        branch: sum((qty * rates[item_code] for item_code, qty in items), Decimal(0))
        for branch, items in by_branch.items()
    }
    ensure_no_pending_pos(doc.company, for_update=for_update)
    scoped = {branch: mappings[branch] for branch in transfers}
    stock = get_stock_values(doc.company, scoped, for_update=for_update)
    unpaid = get_unpaid_invoices(doc.company, mappings, transfers, for_update=for_update)
    for branch in sorted(transfers):
        limit = frappe.db.get_value("Branch", branch, "custom_credit_limit", for_update=for_update)
        if limit is None or amount(limit) <= 0 or stock[branch] + unpaid[branch] + transfers[branch] > amount(limit):
            return "blocked"
    return "within_limit"


def validate_material_request(doc, method=None):
    """Register only on Material Request.before_submit; never blocks draft saves."""
    if assess_material_request(doc, for_update=True) == "blocked":
        frappe.throw(_("Branch credit limit prevents this Material Request from being submitted."))


@frappe.whitelist(methods=["POST"])
@frappe.read_only()
def preview_limit(material_request):
    """Preview a saved draft only; client-provided rows and company are never trusted."""
    if not isinstance(material_request, str) or not material_request:
        frappe.throw(_("Save a Material Request draft before previewing it."))
    if not frappe.has_permission("Material Request", "read", ignore_share_permissions=True):
        frappe.throw(_("Not permitted to preview this Material Request."), exc=frappe.PermissionError)
    doc = frappe.get_doc("Material Request", material_request)
    if not all(
        frappe.has_permission("Material Request", ptype, doc=doc, ignore_share_permissions=True)
        for ptype in ("read", "write")
    ):
        frappe.throw(_("Not permitted to preview this Material Request."), exc=frappe.PermissionError)
    if doc.docstatus != 0:
        frappe.throw(_("Only saved draft Material Requests can be previewed."))
    if get_policy().get("restricted"):
        frappe.throw(_("Branch credit preview is unavailable."), exc=frappe.PermissionError)

    _company, visible, _mappings = get_credit_scope(doc.company)
    status = assess_material_request(doc, permitted_branches=visible)
    return {"status": status}
