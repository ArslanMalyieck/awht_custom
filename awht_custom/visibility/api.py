import frappe


DEFAULT_PATTERNS = (
    "valuation rate",
    "valuation_rate",
    "purchase rate",
    "purchase_rate",
    "purchase price",
    "purchase_price",
    "buying price",
    "buying_price",
    "last purchase",
    "last_purchase",
    "landed cost",
    "landed_cost",
    "cost price",
    "cost_price",
    "stock value",
    "stock_value",
    "valuation",
)


BRANCH_REPORTS = frozenset({
    "Branch Financial Exposure", "Unsold Items",
    "AWHT Current Credit Exposure", "AWHT No Stock Out Items",
})


def policy_for_user(user):
    """Evaluate the same policy for an explicit File permission-check user."""
    if user in ("Administrator", "Guest"):
        return {"restricted": False, "patterns": []}

    settings = frappe.get_doc("Cost Visibility Settings")
    if not settings.enabled or not any(row.user == user for row in settings.restricted_users):
        return {"restricted": False, "patterns": []}

    patterns = list(DEFAULT_PATTERNS)
    for row in settings.extra_patterns:
        pattern = (row.pattern or "").strip().lower()
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    return {"restricted": True, "patterns": patterns}


@frappe.whitelist()
def get_policy():
    return policy_for_user(frappe.session.user)


def has_native_item_wise_tax_details():
    try:
        return bool(
            frappe.get_meta("Sales Invoice").has_field("item_wise_tax_details")
            and frappe.db.exists("DocType", "Item Wise Tax Detail")
        )
    except (AttributeError, RuntimeError, frappe.DoesNotExistError):
        return False


def extend_bootinfo(bootinfo):
    bootinfo.cost_visibility = get_policy()
    bootinfo.awht_native_item_wise_tax_details = has_native_item_wise_tax_details()
