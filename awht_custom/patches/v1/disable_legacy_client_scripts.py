"""Disable live Client Scripts now owned by awht_custom app JS."""
import frappe

LEGACY_CLIENT_SCRIPTS = (
    "Toggle Items",
    "Item Filter For Headoffice",
    "POS Invoice HeadOffice Items",
    "Pos Invoice Accounts toggle",
    "Fetch Saleman Price",
    "Custom Zatca Status",
    "new Item branch permission",
    "Stock Entry Multiple Item Percentage Search",
    "Sales Order Multiple Item Percentage Search",
    "Purchase Order Multiple Item Percentage Search",
    "Purchase Invoice Multiple Item Percentage Search",
    "Quotation Multiple Item Percentage Search",
    "Delivey Note Multiple Item Percentage Search",
    "Sales Invoice Multiple Item Percentage Search",
    "Purchase Receipt Multiple Item Percentage Search",
    "Stock Reconciliation Multiple Item Percentage Search",
    "Supplier Quotation Multiple Item Percentage Search",
    "BOM Multiple Item Percentage Search",
    "RFQ Multiple Item Percentage Search",
    "POS Multiple Item Percentage Search",
    "Toggle Items For Proforma",
    "Proforma Branch Permission",
    "Toggle Items For Quotation",
    "Submit To Zatca",
    "Zero Item Qty or Rate",
    "Salesman Price Stock Entry",
    "Fetch Saleman Price Quotation",
    "Mode Of Payment Cash",
    "Material Request Branch",
    "Expense Claim",
    "WildCard Sales MAn price",
    "Sales Order Items",
    "One Script Sales Invoice",
    "Quotation One Script",
    "Branch Warehouse",
    "custom letter head",
    "Sales Order Scripts",
    "Custom Letter head branch wise",
)


def execute():
    for name in LEGACY_CLIENT_SCRIPTS:
        if frappe.db.exists("Client Script", name):
            frappe.db.set_value("Client Script", name, "enabled", 0, update_modified=False)
