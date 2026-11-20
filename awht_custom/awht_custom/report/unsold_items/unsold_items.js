frappe.query_reports["Unsold Items"] = {
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
		{
			fieldname: "based_on",
			label: __("Check Movement In"),
			fieldtype: "MultiSelectList",
			default: ["Stock Out (All Vouchers)"],
			get_data: function (txt) {
				let options = [
					{
						value: "Stock Out (All Vouchers)",
						description: __("Any stock out: invoices, POS, delivery, issue, manufacture"),
					},
					{ value: "Sales Invoice", description: __("Submitted Sales Invoices") },
					{ value: "POS Invoice", description: __("Submitted POS Invoices") },
					{ value: "Sales Order", description: __("Submitted Sales Orders") },
					{ value: "Delivery Note", description: __("Submitted Delivery Notes") },
				];
				return options.filter(
					(option) => !txt || option.value.toLowerCase().includes(txt.toLowerCase())
				);
			},
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: function () {
				let company = frappe.query_report.get_filter_value("company");
				return { filters: { company: company, is_group: 0 } };
			},
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "brand",
			label: __("Brand"),
			fieldtype: "Link",
			options: "Brand",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "only_with_stock",
			label: __("Only Items With Stock"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "exclude_adjustments",
			label: __("Ignore Transfers & Reconciliation"),
			fieldtype: "Check",
			default: 1,
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (column.fieldname === "last_movement_date" && !data.last_movement_date) {
			value = `<span class="text-danger">${__("Never / Before Records")}</span>`;
		}

		if (column.fieldname === "days_since_last" && data.days_since_last > 180) {
			value = `<span class="text-danger"><b>${value}</b></span>`;
		}

		if (column.fieldname === "stock_qty" && data.stock_qty > 0) {
			value = `<span class="text-warning"><b>${value}</b></span>`;
		}

		return value;
	},
};
