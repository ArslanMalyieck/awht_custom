frappe.query_reports["Branch Financial Exposure"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Branch",
			get_query: function () {
				let company = frappe.query_report.get_filter_value("company");
				if (company && frappe.meta.has_field("Branch", "custom_company")) {
					return { filters: { custom_company: company } };
				}
				return {};
			},
		},
	],
};
