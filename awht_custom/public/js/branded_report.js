// Loaded from app_include_js after the app owner wires the asset into hooks.py.
// Only identifiers and selections leave the browser; both endpoints fetch their own rows.
(function () {
	"use strict";

	const permission_method = "awht_custom.pdf_export.report_pdf.can_download_report_pdf";
	const download_url = "/api/method/awht_custom.pdf_export.report_pdf.download_report_pdf";
	const view_permission_method = "awht_custom.pdf_export.report_view.can_download_report_view_pdf";
	const view_download_url = "/api/method/awht_custom.pdf_export.report_view.download_report_view_pdf";
	const report_types = new Set(["Query Report", "Script Report", "Custom Report"]);

	function escape_html(value) {
		return String(value).replace(/[&<>"']/g, (char) =>
			({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]
		);
	}

	function eligible(report) {
		const doc = report.report_doc;
		return (
			doc &&
			doc.name === report.report_name &&
			report_types.has(doc.report_type) &&
			doc.ref_doctype &&
			!doc.disabled &&
			frappe.model.can_get_report(doc.ref_doctype) &&
			frappe.model.can_print(doc.ref_doctype)
		);
	}

	function open_dialog(report, report_name, is_view) {
		const columns = (report.columns || [])
			.filter((column) => !column.hidden && !column.docfield?.hidden &&
				(is_view ? column.id : column.fieldname))
			.map((column) => ({
				fieldname: is_view ? column.id : column.fieldname,
				label: (is_view ? column.content : column.label) || column.fieldname || column.id,
				fieldtype: (is_view ? column.docfield?.fieldtype : column.fieldtype) || "Data",
			}));
		if (!columns.length) {
			frappe.msgprint(__("Refresh the report before exporting columns."));
			return;
		}
		const words_options = [
			{ label: __("Auto (SAR currency column)"), value: "__auto__" },
			{ label: __("Off"), value: "__off__" },
			...columns
				.filter((col) => col.fieldtype === "Currency" || col.fieldtype === "Float")
				.map((col) => ({ label: escape_html(col.label), value: col.fieldname })),
		];

		const inputs = [];
		const dialog = new frappe.ui.Dialog({
			title: __("Select Columns - {0}", [report_name]),
			fields: [
				{
					fieldtype: "Link",
					fieldname: "letter_head",
					label: __("Letter Head (blank = Company default)"),
					options: "Letter Head",
				},
				{ fieldtype: "Check", fieldname: "no_letter_head", label: __("No letter head") },
				{
					fieldtype: "Select",
					fieldname: "orientation",
					label: __("Orientation"),
					options: ["Landscape", "Portrait"],
					default: "Landscape",
				},
				{
					fieldtype: "Select",
					fieldname: "words_column",
					label: __("Amount in Words (SAR)"),
					options: words_options,
					default: columns.some((col) => col.fieldtype === "Currency") ? "__auto__" : "__off__",
				},
				{
					fieldtype: "Select",
					fieldname: "words_mode",
					label: __("Amount in Words basis"),
					options: [
						{ label: __("Last non-zero row"), value: "last" },
						{ label: __("Sum visible rows"), value: "sum" },
					],
					default: "last",
				},
				{ fieldtype: "HTML", fieldname: "columns" },
			],
			primary_action_label: __("Download PDF"),
			primary_action(values) {
				const selected = inputs.filter((input) => input.checked).map((input) => input.value);
				if (!selected.length) {
					frappe.msgprint(__("Select at least one column."));
					return;
				}
				let url = download_url;
				let params;
				if (is_view) {
					// get_args mirrors the current server-side ReportView query; no row values are sent.
					const args = report.get_args();
					const grouped = args.group_by && report.group_by_control;
					const group = grouped ? {
						field: report.group_by_control.group_by_field,
						owner: report.group_by_control.group_by_doctype,
						function: report.group_by_control.aggregate_function,
						on_field: report.group_by_control.aggregate_on_field || "name",
						on_owner: report.group_by_control.aggregate_on_doctype || report.doctype,
					} : null;
					const visible_names = group ? null : report.datatable.datamanager.rowViewOrder
						.filter((index) => report.datatable.bodyRenderer.visibleRowIndices.includes(index))
						.map((index) => report.data[index]?.name)
						.filter(Boolean);
					url = view_download_url;
					params = {
						doctype: report.doctype,
						report_name: report.report_name || "",
						fields: JSON.stringify(report.fields),
						filters: JSON.stringify(args.filters || []),
						order_by: args.order_by || "",
						row_count: report.data.length,
						add_totals_row: report.add_totals_row ? 1 : 0,
					};
					if (group) {
						params.grouping = JSON.stringify(group);
					} else {
						params.visible_names = JSON.stringify(visible_names);
						params.selected_items = JSON.stringify(report.get_checked_items(true));
					}
				} else {
					params = {
						report_name: report_name,
						filters: JSON.stringify(report.get_filter_values(true)),
					};
				}
				open_url_post(
					url,
					{
						...params,
						only_fields: JSON.stringify(selected),
						orientation: values.orientation || "Landscape",
						letter_head: values.no_letter_head ? "__none__" : values.letter_head || "",
						words_column: values.words_column || "__off__",
						words_mode: values.words_mode || "last",
					},
					true
				);
				dialog.hide();
			},
		});

		const wrapper = document.createElement("div");
		wrapper.style.maxHeight = "300px";
		wrapper.style.overflowY = "auto";
		const all = document.createElement("button");
		all.type = "button";
		all.textContent = __("Select All");
		all.addEventListener("click", () => inputs.forEach((input) => (input.checked = true)));
		const clear = document.createElement("button");
		clear.type = "button";
		clear.textContent = __("Clear");
		clear.addEventListener("click", () => inputs.forEach((input) => (input.checked = false)));
		wrapper.appendChild(all);
		wrapper.appendChild(clear);
		columns.forEach((column) => {
			const label = document.createElement("label");
			label.style.display = "block";
			const input = document.createElement("input");
			input.type = "checkbox";
			input.value = column.fieldname;
			input.checked = true;
			inputs.push(input);
			label.appendChild(input);
			label.appendChild(document.createTextNode(" " + column.label));
			wrapper.appendChild(label);
		});
		dialog.fields_dict.columns.$wrapper.empty().append(wrapper);
		dialog.show();
	}

	function check_and_add_button(report) {
		report.__awht_pdf_check = (report.__awht_pdf_check || 0) + 1;
		if (!eligible(report)) return;
		const check = report.__awht_pdf_check;
		const name = report.report_name;
		frappe.xcall(permission_method, { report_name: name })
			.then((allowed) => {
				if (
					allowed &&
					check === report.__awht_pdf_check &&
					report.report_name === name &&
					frappe.get_route()[1] === name &&
					eligible(report)
				) {
					report.page.add_inner_button(__("Branded PDF"), () => open_dialog(report, name, false));
				}
			})
			.catch(() => {}); // Failed authorization means no button, never a client-side fallback.
	}

	function patch_query_report() {
		const klass = frappe.views && frappe.views.QueryReport;
		if (!klass || !klass.prototype || typeof klass.prototype.refresh_report !== "function") {
			return false;
		}
		if (klass.prototype.__awht_pdf_patched) return true;
		const original = klass.prototype.refresh_report;
		klass.prototype.refresh_report = function (...args) {
			if (this.report_settings && this.report_name) {
				const settings = this.report_settings;
				if (!settings.__awht_pdf_onload) {
					settings.__awht_pdf_onload = true;
					const onload = settings.onload;
					settings.onload = function (report) {
						const result = onload && onload.call(this, report);
						check_and_add_button(report);
						return result;
					};
				}
			}
			return original.apply(this, args);
		};
		klass.prototype.__awht_pdf_patched = true;
		return true;
	}

	function check_and_add_view_button(report) {
		if (
			!report.doctype || !frappe.model.can_read(report.doctype) ||
			!frappe.model.can_get_report(report.doctype) || !frappe.model.can_print(report.doctype) ||
			(report.report_doc &&
				(report.report_doc.ref_doctype !== report.doctype ||
					report.report_doc.report_type !== "Report Builder" || report.report_doc.disabled))
		) return;
		if (report.__awht_pdf_view_button) return;
		if (report.__awht_pdf_view_pending) return;
		report.__awht_pdf_view_pending = true;
		frappe.xcall(view_permission_method, {
			doctype: report.doctype, report_name: report.report_name || null,
		}).then((allowed) => {
			report.__awht_pdf_view_pending = false;
			const route = frappe.get_route();
			if (allowed && route[1] === report.doctype &&
				(!report.report_name || route[3] === report.report_name)) {
				const button = report.page.add_inner_button(__("Branded PDF"), () =>
					open_dialog(report, report.report_name || report.doctype, true));
				button?.addClass?.("awht-branded-pdf-btn");
				report.__awht_pdf_view_button = true;
			}
		}).catch(() => { report.__awht_pdf_view_pending = false; });
	}

	function patch_report_view() {
		const klass = frappe.views && frappe.views.ReportView;
		if (!klass || !klass.prototype || typeof klass.prototype.setup_result_area !== "function") {
			return false;
		}
		if (klass.prototype.__awht_pdf_patched) return true;
		const original = klass.prototype.setup_result_area;
		klass.prototype.setup_result_area = function (...args) {
			const result = original.apply(this, args);
			check_and_add_view_button(this);
			return result;
		};
		klass.prototype.__awht_pdf_patched = true;
		return true;
	}

	let attempts = 0;
	(function init() {
		const query_ready = patch_query_report();
		const view_ready = patch_report_view();
		if ((!query_ready || !view_ready) && ++attempts < 60) setTimeout(init, 300);
	})();
})();
