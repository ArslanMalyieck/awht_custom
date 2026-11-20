/* Existing AWHT app behaviour for Stock Entry. */
const AWHT_PRICE_ROLE = "Previous Transfer Prices Viewer";
const AWHT_PRICE_BUTTON = "Previous Prices";

function awht_can_view_transfer_prices(frm) {
	return (
		frappe.user.has_role(AWHT_PRICE_ROLE) &&
		!frm.is_new() &&
		frm.doc.docstatus === 0 &&
		frm.doc.purpose === "Material Transfer"
	);
}

async function awht_show_transfer_prices(frm, row) {
	if (!awht_can_view_transfer_prices(frm)) return;
	if (frm.is_dirty()) {
		frappe.msgprint(__("Save the Material Transfer before viewing previous prices."));
		return;
	}
	if (!row || !row.item_code || !row.uom || !row.s_warehouse || !row.t_warehouse) {
		frappe.msgprint(__("Select an item, its UOM and both row warehouses first."));
		return;
	}

	const stock_entry = frm.doc.name;
	const response = await frappe.call({
		method: "awht_custom.operations.transfer_prices.get_previous_transfer_prices",
		args: { stock_entry, row_name: row.name, limit: 20 },
		freeze: true,
	});
	if (frm.doc.name !== stock_entry || frm.is_dirty()) return;
	const prices = Array.isArray(response.message) ? response.message : [];
	const escape = (value) => frappe.utils.escape_html(String(value ?? ""));
	const writable = frm.has_perm("write");
	const heading = `${escape(row.item_code)} (${escape(row.s_warehouse)} / ${escape(row.t_warehouse)})`;
	const rows = prices.map((price, index) => `
		<tr ${writable ? `class="awht-prior-price" data-index="${index}"` : ""}>
			<td>${escape(price.stock_entry)}</td>
			<td>${price.docstatus === 0 ? escape(__("Draft")) : escape(__("Submitted"))}</td>
			<td>${escape(frappe.datetime.str_to_user(price.posting_date))}
				${escape(price.posting_time)}</td>
			<td class="text-right">${escape(format_number(price.qty))} ${escape(price.uom)}</td>
			<td class="text-right">${escape(format_number(price.salesman_price))}</td>
		</tr>`).join("");

	const dialog = new frappe.ui.Dialog({ title: __("Previous Transfer Prices"), size: "large" });
	const html = `<p>${heading}</p>${prices.length ? `
		<table class="table table-bordered table-hover"><thead><tr>
		<th>${escape(__("Stock Entry"))}</th><th>${escape(__("Status"))}</th>
		<th>${escape(__("Posting"))}</th><th>${escape(__("Qty"))}</th>
		<th>${escape(__("Salesman Price"))}</th></tr></thead><tbody>${rows}</tbody></table>`
		: `<p class="text-muted">${escape(__("No earlier transfers found for this row."))}</p>`}`;
	const body = $(dialog.body).html(html);
	body.find(".awht-prior-price").on("click", function () {
		if (frm.doc.name !== stock_entry || frm.doc.docstatus !== 0 || frm.is_dirty() || !frm.has_perm("write")) {
			return;
		}
		const selected = prices[Number($(this).attr("data-index"))];
		if (selected) {
			frappe.model.set_value(row.doctype, row.name, "salesman_price", selected.salesman_price);
			dialog.hide();
		}
	});
	dialog.show();
}

function awht_sync_transfer_price_button(frm) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) return;
	const button = grid.custom_buttons && grid.custom_buttons[AWHT_PRICE_BUTTON];
	if (!awht_can_view_transfer_prices(frm)) {
		if (button) button.addClass("hidden");
		return;
	}
	if (button) {
		button.removeClass("hidden");
		return;
	}
	grid.add_custom_button(AWHT_PRICE_BUTTON, () => {
		const selected = grid.get_selected_children();
		if (selected.length !== 1) {
			frappe.msgprint(__("Select exactly one item row first."));
			return;
		}
		awht_show_transfer_prices(frm, selected[0]);
	});
}

frappe.ui.form.on("Stock Entry", {
	refresh: awht_sync_transfer_price_button,
	onload_post_render: awht_sync_transfer_price_button,
	stock_entry_type: awht_sync_transfer_price_button,
	purpose: awht_sync_transfer_price_button,
});

frappe.ui.form.on("Stock Entry Detail", {
	form_render(frm, cdt, cdn) {
		if (!awht_can_view_transfer_prices(frm)) return;
		const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
		const grid_row = grid && grid.grid_rows_by_docname && grid.grid_rows_by_docname[cdn];
		const field = grid_row && grid_row.grid_form && grid_row.grid_form.fields_dict.salesman_price;
		if (!field || field.$wrapper.find(".awht-prior-price-button").length) return;
		$("<button type='button' class='btn btn-xs btn-default awht-prior-price-button'></button>")
			.text(__("Previous Prices"))
			.appendTo(field.$wrapper)
			.on("click", () => awht_show_transfer_prices(frm, locals[cdt] && locals[cdt][cdn]));
	},
});


/* Ported Client Script: Stock Entry Multiple Item Percentage Search */
(() => {
frappe.ui.form.on("Stock Entry", {
    onload(frm) {
        $(document).on("shown.bs.modal", function () {
            const dialog = $(".modal:visible");

            // Ensure this is the Select Item dialog
            if (!dialog.find(".modal-title").text().includes("Select Item")) {
                return;
            }

            const input = dialog.find("input[type='text']").first();

            // Prevent duplicate button
            if (dialog.find(".insert-percent-btn").length) {
                return;
            }

            // Wrap input for inline button placement
            const wrapper = $('<div class="d-flex align-items-center gap-2"></div>');
            input.wrap(wrapper);

            const percentBtn = $(`
                <button
                    type="button"
                    class="btn btn-sm btn-primary insert-percent-btn"
                >
                    Insert %
                </button>
            `);

            percentBtn.on("click", function () {
                const el = input.get(0);
                const start = el.selectionStart ?? el.value.length;
                const end = el.selectionEnd ?? el.value.length;

                el.value =
                    el.value.substring(0, start) +
                    "%" +
                    el.value.substring(end);

                el.focus();
                el.selectionStart = el.selectionEnd = start + 1;
            });

            input.after(percentBtn);
        });
    }
});

})();


/* Ported Client Script: Salesman Price Stock Entry */
(() => {
frappe.ui.form.on('Stock Entry', {
	refresh(frm) {
		// your code here
	}
})
frappe.ui.form.on('Stock Entry Detail', {
    item_code: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.item_code) {
            frappe.db.get_value('Item', row.item_code, 'custom_system_sale_price', function(r) {
                if (r.custom_system_sale_price) {
                    frappe.model.set_value(cdt, cdn, 'salesman_price', r.custom_system_sale_price);
                }
            });
        }
    }
});

})();
