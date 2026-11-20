const AWHT_ITEM_TAX_PREVIEW = "awht_custom.operations.invoice_tax.preview_item_taxes";
const AWHT_BRANCH_WAREHOUSE = "awht_custom.operations.invoice_branch.get_branch_warehouse";

function awht_has_native_item_tax_details() {
	return Boolean(frappe.boot && frappe.boot.awht_native_item_wise_tax_details);
}

function awht_has_invoice_tax_fields(frm) {
	return frappe.meta.get_docfield("Sales Invoice Item", "item_tax_amount", frm.doc.name) &&
		frappe.meta.get_docfield("Sales Invoice Item", "amount_after_tax", frm.doc.name);
}

function awht_clear_invoice_item_taxes(frm) {
	for (const row of frm.doc.items || []) {
		row.item_tax_amount = null;
		row.amount_after_tax = null;
	}
	frm.refresh_field("items");
}

function awht_invoice_tax_snapshot(frm) {
	const { item_wise_tax_details, __onload, ...doc } = frm.doc;
	return JSON.stringify({
		...doc,
		items: (doc.items || []).map(({ item_tax_amount, amount_after_tax, ...row }) => row),
	});
}

function awht_schedule_item_tax_preview(frm) {
	const version = frm._awht_tax_version = (frm._awht_tax_version || 0) + 1;
	clearTimeout(frm._awht_tax_timer);
	if (!awht_has_native_item_tax_details() || !awht_has_invoice_tax_fields(frm) ||
		frm.doc.docstatus !== 0 || (!frm.is_new() && !frm.is_dirty())) return;
	awht_clear_invoice_item_taxes(frm);
	frm._awht_tax_timer = setTimeout(async () => {
		if (version !== frm._awht_tax_version || frm.doc.docstatus !== 0) return;
		// ERPNext's synchronous calculator also sets item_tax_amount to zero during the event.
		awht_clear_invoice_item_taxes(frm);
		if (!(frm.doc.items || []).length || frm.doc.items.some((row) => !row.item_code)) return;
		const snapshot = awht_invoice_tax_snapshot(frm);
		try {
			const response = await frappe.call({ method: AWHT_ITEM_TAX_PREVIEW, args: { doc: snapshot } });
			if (version !== frm._awht_tax_version || frm.doc.docstatus !== 0 ||
				awht_invoice_tax_snapshot(frm) !== snapshot) return;
			const values = response.message;
			if (!Array.isArray(values) || values.length !== frm.doc.items.length) return;
			const byName = new Map(values.map((row) => [row.name, row]));
			if (byName.size !== values.length || frm.doc.items.some((row) => !byName.has(row.name))) return;
			for (const row of frm.doc.items) {
				const { item_tax_amount, amount_after_tax } = byName.get(row.name);
				row.item_tax_amount = Number.isFinite(item_tax_amount) ? item_tax_amount : null;
				row.amount_after_tax = Number.isFinite(amount_after_tax) ? amount_after_tax : null;
			}
			frm.refresh_field("items");
		} catch (error) {
			// Permission/calculation failures leave both display-only values blank.
		}
	}, 650);
}

function awht_schedule_branch_warehouse(frm, notify = false) {
	if (frm._awht_filling_branch) return;
	const version = frm._awht_branch_version = (frm._awht_branch_version || 0) + 1;
	clearTimeout(frm._awht_branch_timer);
	if (frm.doc.docstatus !== 0 || frm.doc.is_return || !frm.doc.company || !frm.doc.custom_branch) return;
	frm._awht_branch_timer = setTimeout(async () => {
		const company = frm.doc.company;
		const branch = frm.doc.custom_branch;
		const args = { company, branch };
		if (!frm.is_new()) args.invoice = frm.doc.name;
		try {
			const response = await frappe.call({
				method: AWHT_BRANCH_WAREHOUSE,
				args,
			});
			if (version !== frm._awht_branch_version || frm.doc.docstatus !== 0 || frm.doc.is_return ||
				frm.doc.company !== company || frm.doc.custom_branch !== branch) return;
			const warehouse = response.message;
			if (!warehouse) return;
			const conflicts = () => (frm.doc.set_warehouse && frm.doc.set_warehouse !== warehouse) ||
				(frm.doc.items || []).some((row) => row.warehouse && row.warehouse !== warehouse);
			if (conflicts()) {
				frappe.show_alert({
					message: __("The selected branch conflicts with an existing warehouse. Clear conflicting warehouses before applying its mapping."),
					indicator: "orange",
				}, 7);
				return;
			}
			frm._awht_filling_branch = true;
			try {
				let changed = false;
				// ERPNext's header event fills every row, so conflicts must be ruled out first.
				if (!frm.doc.set_warehouse) {
					await frm.set_value("set_warehouse", warehouse);
					changed = true;
				}
				for (const row of frm.doc.items || []) {
					if (version !== frm._awht_branch_version || frm.doc.company !== company ||
						frm.doc.custom_branch !== branch || conflicts()) return;
					if (!row.warehouse) {
						await frappe.model.set_value(row.doctype, row.name, "warehouse", warehouse);
						changed = true;
					}
				}
				if (notify && changed) frappe.show_alert({
					message: __("Blank warehouses filled from the selected branch mapping."),
					indicator: "green",
				}, 4);
			} finally {
				frm._awht_filling_branch = false;
			}
		} catch (error) {
			// The owning mapping service rejects missing/ambiguous mappings and unauthorized access.
		}
	}, 250);
}

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus === 0) awht_schedule_branch_warehouse(frm);
		else {
			frm._awht_tax_version = (frm._awht_tax_version || 0) + 1;
			clearTimeout(frm._awht_tax_timer);
		}
	},
	onload_post_render(frm) {
		if (frm.is_new()) awht_schedule_branch_warehouse(frm);
	},
	after_save(frm) {
		frm._awht_tax_version = (frm._awht_tax_version || 0) + 1;
		clearTimeout(frm._awht_tax_timer);
		frm.refresh_field("items");
	},
	calculate_taxes_and_totals: awht_schedule_item_tax_preview,
	custom_branch: (frm) => awht_schedule_branch_warehouse(frm, true),
	company(frm) {
		awht_schedule_branch_warehouse(frm, true);
		awht_schedule_item_tax_preview(frm);
	},
	set_warehouse: (frm) => awht_schedule_branch_warehouse(frm, true),
	items_add(frm) {
		awht_schedule_branch_warehouse(frm);
		awht_schedule_item_tax_preview(frm);
	},
	items_remove: awht_schedule_item_tax_preview,
	taxes_add: awht_schedule_item_tax_preview,
	taxes_remove: awht_schedule_item_tax_preview,
	customer: awht_schedule_item_tax_preview,
	currency: awht_schedule_item_tax_preview,
	conversion_rate: awht_schedule_item_tax_preview,
	taxes_and_charges: awht_schedule_item_tax_preview,
	tax_category: awht_schedule_item_tax_preview,
	apply_tds: awht_schedule_item_tax_preview,
	tax_withholding_category: awht_schedule_item_tax_preview,
	tax_withholding_group: awht_schedule_item_tax_preview,
	apply_discount_on: awht_schedule_item_tax_preview,
	discount_amount: awht_schedule_item_tax_preview,
	additional_discount_percentage: awht_schedule_item_tax_preview,
	is_cash_or_non_trade_discount: awht_schedule_item_tax_preview,
	shipping_rule: awht_schedule_item_tax_preview,
	is_return(frm) {
		awht_schedule_branch_warehouse(frm);
		awht_schedule_item_tax_preview(frm);
	},
});

frappe.ui.form.on("Sales Invoice Item", {
	items_add(frm) {
		awht_schedule_branch_warehouse(frm);
		awht_schedule_item_tax_preview(frm);
	},
	items_remove(frm) {
		// Grid removal marks the form dirty after firing the child event.
		setTimeout(() => awht_schedule_item_tax_preview(frm), 0);
	},
	rate: awht_schedule_item_tax_preview,
	qty: awht_schedule_item_tax_preview,
	item_code(frm) {
		awht_schedule_item_tax_preview(frm);
		setTimeout(() => awht_schedule_branch_warehouse(frm), 500);
	},
	warehouse: (frm) => awht_schedule_branch_warehouse(frm, true),
	discount_percentage: awht_schedule_item_tax_preview,
	discount_amount: awht_schedule_item_tax_preview,
	price_list_rate: awht_schedule_item_tax_preview,
	item_tax_template: awht_schedule_item_tax_preview,
	apply_tds: awht_schedule_item_tax_preview,
	tax_withholding_category: awht_schedule_item_tax_preview,
	uom: awht_schedule_item_tax_preview,
	margin_type: awht_schedule_item_tax_preview,
	margin_rate_or_amount: awht_schedule_item_tax_preview,
});

frappe.ui.form.on("Sales Taxes and Charges", {
	taxes_add: awht_schedule_item_tax_preview,
	taxes_remove(frm) {
		setTimeout(() => awht_schedule_item_tax_preview(frm), 0);
	},
	rate: awht_schedule_item_tax_preview,
	tax_amount: awht_schedule_item_tax_preview,
	account_head: awht_schedule_item_tax_preview,
	charge_type: awht_schedule_item_tax_preview,
	row_id: awht_schedule_item_tax_preview,
	included_in_print_rate: awht_schedule_item_tax_preview,
	is_tax_withholding_account: awht_schedule_item_tax_preview,
});
