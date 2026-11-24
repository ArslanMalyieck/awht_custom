/* Ported live Client Scripts for Stock Reconciliation. */


/* Ported Client Script: Stock Reconciliation Multiple Item Percentage Search */
(() => {
frappe.ui.form.on("Stock Reconciliation", {
    onload(frm) {
        frm.get_field('items').grid.set_multiple_add('item_code', 'qty');
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
(function () {
    "use strict";

    const ITEM_DOCTYPE = "Stock Reconciliation Item";
    const DIALOG_FLAG = "__awht_stock_recon_merge_dialog";
    const SKIP_FLAG = "__awht_stock_recon_skip_add";
    const MERGING_FLAG = "__awht_stock_recon_merging";

    function escape_html(value) {
        if (frappe.utils && frappe.utils.escape_html) {
            return frappe.utils.escape_html(String(value ?? ""));
        }
        return String(value ?? "").replace(/[&<>"']/g, (char) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        })[char]);
    }

    function item_key(row) {
        return [row.item_code || "", row.warehouse || "", row.batch_no || ""].join("::");
    }

    function row_qty(row) {
        return flt(row && row.qty);
    }

    function find_previous_row(frm, row) {
        return (frm.doc.items || []).find((other) =>
            other.name !== row.name &&
            item_key(other) === item_key(row)
        );
    }

    function remove_child_row(frm, cdn) {
        const grid = frm.get_field("items") && frm.get_field("items").grid;
        const grid_row = grid && grid.grid_rows_by_docname && grid.grid_rows_by_docname[cdn];
        if (grid_row) {
            grid_row.remove();
            return;
        }
        const index = (frm.doc.items || []).findIndex((row) => row.name === cdn);
        if (index >= 0) {
            frm.doc.items.splice(index, 1);
            frm.refresh_field("items");
        }
    }

    function merge_row(frm, cdt, cdn, previous) {
        const row = locals[cdt] && locals[cdt][cdn];
        if (!row || !previous) return;
        frm[MERGING_FLAG] = true;
        const merged_qty = row_qty(previous) + row_qty(row);
        frappe.model.set_value(previous.doctype || ITEM_DOCTYPE, previous.name, "qty", merged_qty)
            .then(() => {
                remove_child_row(frm, cdn);
                frm.refresh_field("items");
                frappe.show_alert({
                    message: __("Merged into row #{0}; new quantity is {1}.", [
                        previous.idx, merged_qty,
                    ]),
                    indicator: "green",
                }, 6);
            })
            .finally(() => {
                frm[MERGING_FLAG] = false;
            });
    }

    function prompt_merge(frm, cdt, cdn) {
        if (frm[SKIP_FLAG] || frm[DIALOG_FLAG] || frm[MERGING_FLAG]) return;
        const row = locals[cdt] && locals[cdt][cdn];
        if (!row || !row.item_code || !row_qty(row) || row.__awht_keep_separate) return;

        // ERPNext fills qty from the current stock when an item is selected.
        // Do not open the merge dialog for that automatic value; wait for an
        // actual user-entered quantity (or a row whose current qty is unknown).
        if (row.current_qty !== undefined && row.current_qty !== null &&
            Math.abs(row_qty(row) - flt(row.current_qty)) < 0.000001) {
            return;
        }

        const previous = find_previous_row(frm, row);
        if (!previous) return;

        frm[DIALOG_FLAG] = true;
        const total = row_qty(previous) + row_qty(row);
        const dialog = new frappe.ui.Dialog({
            title: __("Duplicate Item"),
            fields: [{
                fieldtype: "HTML",
                fieldname: "duplicate_message",
                options: `<p><strong>${escape_html(row.item_code)}</strong> is already listed in row <strong>#${previous.idx}</strong> for warehouse <strong>${escape_html(previous.warehouse || "")}</strong> with a quantity of <strong>${escape_html(row_qty(previous))}</strong>.</p>
                    <p>Merge both quantities into a single line with a total quantity of <strong>${escape_html(total)}</strong>?</p>`,
            }],
            primary_action_label: __("Merge Quantities"),
            primary_action() {
                frm[DIALOG_FLAG] = false;
                dialog.hide();
                merge_row(frm, cdt, cdn, previous);
            },
        });
        dialog.set_secondary_action_label(__("Keep Separate"));
        dialog.set_secondary_action(() => {
            row.__awht_keep_separate = true;
            frm[DIALOG_FLAG] = false;
            dialog.hide();
        });
        dialog.onhide = () => {
            frm[DIALOG_FLAG] = false;
        };
        dialog.show();
    }

    function schedule_merge_prompt(frm, cdt, cdn) {
        const row = locals[cdt] && locals[cdt][cdn];
        if (!row) return;
        clearTimeout(row.__awht_dup_timer);
        row.__awht_dup_timer = setTimeout(() => prompt_merge(frm, cdt, cdn), 350);
    }

    function refresh_stock_preserving_qty(frm, cdt, cdn) {
        const row = frappe.model.get_doc(cdt, cdn);
        if (!row || !row.item_code || !row.warehouse) return;
        frappe.call({
            method: "erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_stock_balance_for",
            args: {
                item_code: row.item_code,
                warehouse: row.warehouse,
                posting_date: frm.doc.posting_date,
                posting_time: frm.doc.posting_time,
                batch_no: row.batch_no,
                row: row,
                company: frm.doc.company,
            },
            callback(response) {
                const message = response.message || {};
                const current = frappe.model.get_doc(cdt, cdn);
                frappe.model.set_value(cdt, cdn, "valuation_rate", message.rate);
                frappe.model.set_value(cdt, cdn, "current_qty", message.qty);
                frappe.model.set_value(cdt, cdn, "current_valuation_rate", message.rate);
                frappe.model.set_value(cdt, cdn, "current_amount", flt(message.rate) * flt(message.qty));
                frappe.model.set_value(cdt, cdn, "amount", flt(current.qty) * flt(current.valuation_rate));
                frappe.model.set_value(cdt, cdn, "current_serial_no", message.serial_nos);
                frappe.model.set_value(cdt, cdn, "use_serial_batch_fields", message.use_serial_batch_fields);
                if (frm.doc.purpose === "Stock Reconciliation" && !frm.doc.scan_mode) {
                    frappe.model.set_value(cdt, cdn, "serial_no", message.serial_nos);
                }
            },
        });
    }

    function install_qty_preserving_refresh(frm) {
        if (frm.__awht_qty_preserve_patch || !frm.events) return;
        const refresh_all = frm.events.set_valuation_rate_and_qty_for_all_items;
        const refresh_one = frm.events.set_valuation_rate_and_qty;
        if (typeof refresh_all !== "function" || typeof refresh_one !== "function") return;

        frm.events.set_valuation_rate_and_qty_for_all_items = function (form) {
            form.__awht_preserve_qty = true;
            try {
                return refresh_all.call(this, form);
            } finally {
                form.__awht_preserve_qty = false;
            }
        };
        frm.events.set_valuation_rate_and_qty = function (form, cdt, cdn) {
            if (!form.__awht_preserve_qty) {
                return refresh_one.call(this, form, cdt, cdn);
            }
            return refresh_stock_preserving_qty(form, cdt, cdn);
        };
        frm.__awht_qty_preserve_patch = true;
    }

    function remove_blank_item_rows(frm) {
        const blank_rows = (frm.doc.items || []).filter((row) => !row.item_code);
        blank_rows.forEach((row) => {
            if (row.name) {
                remove_child_row(frm, row.name);
            }
        });
    }

    function default_warehouse(frm) {
        if (frm.doc.set_warehouse) return frm.doc.set_warehouse;
        const item = (frm.doc.items || []).find((row) => row.warehouse);
        return item ? item.warehouse : "";
    }

    function open_remaining_dialog(frm) {
        if (frm.doc.docstatus !== 0) return;
        if (!frm.doc.company) {
            frappe.msgprint(__("Select a company first."));
            return;
        }
        const today = frappe.datetime.get_today();
        const default_date = frm.doc.posting_date || today;
        const dialog = new frappe.ui.Dialog({
            title: __("Get Remaining Items"),
            fields: [
                {
                    fieldtype: "Select",
                    fieldname: "purpose",
                    label: __("Purpose"),
                    options: [
                        { label: __("Stock Reconciliation"), value: "Stock Reconciliation" },
                        { label: __("Opening Stock"), value: "Opening Stock" },
                    ],
                    default: frm.doc.purpose || "Stock Reconciliation",
                    reqd: 1,
                },
                {
                    fieldtype: "Link",
                    fieldname: "warehouse",
                    label: __("Default Warehouse"),
                    options: "Warehouse",
                    reqd: 1,
                    default: default_warehouse(frm),
                    get_query: () => ({
                        filters: { company: frm.doc.company, is_group: 0 },
                    }),
                },
                {
                    fieldtype: "Date",
                    fieldname: "from_date",
                    label: __("Reconciled From"),
                    reqd: 1,
                    default: default_date,
                },
                {
                    fieldtype: "Date",
                    fieldname: "to_date",
                    label: __("Reconciled To"),
                    reqd: 1,
                    default: default_date,
                },
                {
                    fieldtype: "Check",
                    fieldname: "ignore_empty_stock",
                    label: __("Ignore Empty Stock"),
                    default: 1,
                },
                {
                    fieldtype: "HTML",
                    fieldname: "help",
                    options: __("Items already saved or submitted in Stock Reconciliations for this warehouse and date range will be skipped. The selected range is only for remembering reconciled items; stock balances still use this document's posting date and time."),
                },
            ],
            primary_action_label: __("Get Items"),
            primary_action(values) {
                if (frappe.datetime.str_to_obj(values.from_date) > frappe.datetime.str_to_obj(values.to_date)) {
                    frappe.msgprint(__("The 'from' date cannot be after the 'to' date."));
                    return;
                }
                if (values.purpose && frm.doc.purpose !== values.purpose) {
                    frm.set_value("purpose", values.purpose);
                }
                if (!frm.doc.set_warehouse) {
                    frm.set_value("set_warehouse", values.warehouse);
                }
                frappe.call({
                    method: "awht_custom.stock_recon.api.get_remaining_items",
                    type: "POST",
                    args: {
                        warehouse: values.warehouse,
                        company: frm.doc.company,
                        from_date: values.from_date,
                        to_date: values.to_date,
                        posting_date: frm.doc.posting_date,
                        posting_time: frm.doc.posting_time,
                        ignore_empty_stock: values.ignore_empty_stock ? 1 : 0,
                    },
                    freeze: true,
                    callback(response) {
                        const result = response.message || {};
                        const rows = Array.isArray(result.items) ? result.items : [];
                        const current = new Set((frm.doc.items || []).map(item_key));
                        let added = 0;
                        let already_in_form = 0;

                        frm[SKIP_FLAG] = true;
                        try {
                            remove_blank_item_rows(frm);
                            rows.forEach((source) => {
                                const key = item_key(source);
                                if (current.has(key)) {
                                    already_in_form += 1;
                                    return;
                                }
                                const child = frm.add_child("items");
                                Object.assign(child, source);
                                child.qty = flt(source.qty);
                                child.valuation_rate = flt(source.valuation_rate);
                                child.current_qty = flt(source.current_qty);
                                child.current_valuation_rate = flt(source.current_valuation_rate);
                                child.amount = flt(child.qty) * flt(child.valuation_rate);
                                child.current_amount = flt(child.current_qty) * flt(child.current_valuation_rate);
                                child.use_serial_batch_fields = cint(
                                    frappe.user_defaults && frappe.user_defaults.use_serial_batch_fields
                                );
                                current.add(key);
                                added += 1;
                            });
                        } finally {
                            frm[SKIP_FLAG] = false;
                        }

                        if (added) {
                            frm.refresh_field("items");
                            frm.dirty();
                        }
                        frappe.show_alert({
                            message: __("Added {0} remaining items. {1} already reconciled and {2} already in this form were skipped.", [
                                added, cint(result.reconciled_count), already_in_form,
                            ]),
                            indicator: added ? "green" : "orange",
                        }, 8);
                        dialog.hide();
                    },
                });
            },
        });
        dialog.show();
    }

    frappe.ui.form.on("Stock Reconciliation", {
        onload(frm) {
            install_qty_preserving_refresh(frm);
        },
        refresh(frm) {
            install_qty_preserving_refresh(frm);
            if (frm.doc.docstatus === 0) {
                frm.add_custom_button(__("Get Remaining Items"), () => open_remaining_dialog(frm));
            }
        },
    });

    frappe.ui.form.on(ITEM_DOCTYPE, {
        item_code: schedule_merge_prompt,
        qty: schedule_merge_prompt,
        warehouse: schedule_merge_prompt,
    });
})();
