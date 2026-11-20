frappe.query_reports["AWHT No Stock Out Items"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            reqd: 1,
        },
        // Exact-name Data filters avoid extra HR/stock DocType roles; the server scopes all rows.
        {
            fieldname: "branch",
            label: __("Branch (exact name)"),
            fieldtype: "Data",
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
        {fieldname: "item_group", label: __("Item Group (exact name)"), fieldtype: "Data"},
        {fieldname: "brand", label: __("Brand (exact name)"), fieldtype: "Data"},
        {fieldname: "item_code", label: __("Item (exact code)"), fieldtype: "Data"},
    ],
};
