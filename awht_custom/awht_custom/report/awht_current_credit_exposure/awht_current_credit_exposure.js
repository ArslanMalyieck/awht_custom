frappe.query_reports["AWHT Current Credit Exposure"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            reqd: 1,
        },
        // Branch is exact-name Data: ERPNext grants Branch Link access through HR roles.
        // The report checks Branch User Permissions itself on the server.
        {
            fieldname: "branch",
            label: __("Branch (exact name)"),
            fieldtype: "Data",
        },
    ],
};
