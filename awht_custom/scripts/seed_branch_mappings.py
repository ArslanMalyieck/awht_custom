"""Idempotently seed AWHT Branch Warehouse mappings from explicit live naming."""
import frappe


SOURCE_BRANCH = "Head Office"
SOURCE_WAREHOUSE = "Head Office - AWH"


def run():
    created = []
    skipped = []
    branches = frappe.get_all("Branch", fields=["name", "custom_company"], order_by="name")
    warehouses = frappe.get_all(
        "Warehouse", filters={"is_group": 0, "disabled": 0},
        fields=["name", "company"], order_by="name",
    )
    by_name = {row["name"]: row["company"] for row in warehouses}

    for branch in branches:
        name = branch["name"]
        company = branch.get("custom_company")
        if name == "WAREHOUSE" or not company:
            skipped.append(name)
            continue
        matches = [w for w, c in by_name.items() if c == company and w.startswith(name + " - ")]
        if len(matches) != 1:
            skipped.append(name)
            continue
        warehouse = matches[0]
        if frappe.db.exists("AWHT Branch Warehouse", {"company": company, "branch": name, "warehouse": warehouse}):
            continue
        frappe.get_doc({
            "doctype": "AWHT Branch Warehouse",
            "company": company,
            "branch": name,
            "warehouse": warehouse,
        }).insert(ignore_permissions=True)
        created.append((name, warehouse))

    companies = {row["custom_company"] for row in branches if row.get("custom_company")}
    for company in sorted(companies):
        if not frappe.db.exists("AWHT Credit Policy", {"company": company}):
            frappe.get_doc({
                "doctype": "AWHT Credit Policy",
                "company": company,
                "enabled": 0,
                "source_warehouse": SOURCE_WAREHOUSE,
            }).insert(ignore_permissions=True)

    frappe.db.commit()
    return {"created": created, "skipped": skipped}
