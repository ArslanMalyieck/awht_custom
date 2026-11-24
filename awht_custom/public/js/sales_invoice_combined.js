/* Existing AWHT app behaviour for Sales Invoice. */
const AWHT_ITEM_TAX_PREVIEW = "awht_custom.operations.invoice_tax.preview_item_taxes";
const AWHT_BRANCH_WAREHOUSE = "awht_custom.operations.invoice_branch.get_branch_warehouse";

if (typeof erpnext !== "undefined" && erpnext.utils && erpnext.utils.set_taxes && !erpnext.utils._awht_missing_party_guard) {
    const awht_set_taxes = erpnext.utils.set_taxes;
    erpnext.utils.set_taxes = function (frm, ...args) {
        const party = frm.doc.customer || frm.doc.supplier || frm.doc.lead || frm.doc.party_name;
        if (!party) return Promise.resolve();
        return awht_set_taxes.call(this, frm, ...args);
    };
    erpnext.utils._awht_missing_party_guard = true;
}

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


/* Ported Client Script: Toggle Items */
(() => {
frappe.ui.form.on('Sales Invoice', {
    refresh(frm) {
        toggle_tables(frm);
        apply_tax_template(frm);
        apply_debit_to(frm);
        apply_item_accounts(frm);
        unlock_fields(frm);
    },
   

    custom_branch(frm) {
        toggle_tables(frm);
        apply_tax_template(frm);
        apply_debit_to(frm);

        // Wait a tiny bit to ensure items table is ready
        setTimeout(() => apply_item_accounts(frm), 200);

        unlock_fields(frm);
    }
});

frappe.ui.form.on('Sales Invoice', {
    customer(frm) {
        apply_tax_template(frm);
        apply_debit_to(frm);
        apply_item_accounts(frm);
    },
});

/* -------------------------------------------------------
   Show / Hide Tables
--------------------------------------------------------*/
function toggle_tables(frm) {
    const show = !!frm.doc.custom_branch;
    frm.toggle_display('items', show);
    frm.toggle_display('taxes', show);

    if (!show) {
        frm.clear_table('items');
        frm.clear_table('taxes');
        frm.refresh_fields(['items', 'taxes']);
    }
}

/* -------------------------------------------------------
   Auto VAT Template
--------------------------------------------------------*/
function apply_tax_template(frm) {
    if (!frm.doc.custom_branch || !frm.doc.company) return;

    const branch_norm = normalize(frm.doc.custom_branch);

    frappe.db.get_list('Sales Taxes and Charges Template', {
        filters: [['company', '=', frm.doc.company]], // optional filter by company
        fields: ['name'],
        limit: 200
    }).then(templates => {
        // Prefer the shortest branch-specific company template. This avoids
        // selecting "Khamis Mashit Van" for the "Khamis Mashit" branch when
        // both names match the branch text.
        const template = templates
            .filter(t => normalize(t.name).includes(branch_norm) && normalize(t.name).includes('awh'))
            .sort((a, b) => normalize(a.name).length - normalize(b.name).length)[0];

        if (!template) {
            frappe.msgprint({
                title: __('Configuration Missing'),
                message: __(`Tax Template not found for ${frm.doc.custom_branch}`),
                indicator: 'red'
            });
            console.warn('Available templates:', templates.map(t => t.name));
            return;
        }

        if (frm.doc.taxes_and_charges === template.name) return;

        // Clear old taxes
        frm.clear_table('taxes');
        frm.set_value('taxes_and_charges', null);
        frm.doc._apply_taxes = 0;
        frm.refresh_fields(['taxes', 'taxes_and_charges']);

        // Apply new template after reset
        frappe.after_ajax(() => {
            frm.set_value('taxes_and_charges', template.name);
            setTimeout(() => {
                // frm.trigger('taxes_and_charges');
                frm.refresh_field('taxes');
                console.log('Tax template applied:', template.name);
            }, 300);
        });
    });
}

/* -------------------------------------------------------
   Auto Debtors Account
--------------------------------------------------------*/
function apply_debit_to(frm) {
    if (!frm.doc.custom_branch || !frm.doc.company) return;

    const branch_norm = normalize(frm.doc.custom_branch);

    frappe.db.get_list('Account', {
        filters: [
            ['company', '=', frm.doc.company],
            ['account_type', '=', 'Receivable'],
            ['is_group', '=', 0]
        ],
        fields: ['name', 'account_name'],
        limit: 200
    }).then(accounts => {
        const debtor = accounts.find(acc =>
            acc.account_name && normalize(acc.account_name).includes(branch_norm)
        );

        if (!debtor) return;

        frappe.after_ajax(() => {
            frm.set_df_property('debit_to', 'read_only', 0);
            frm.doc.debit_to = debtor.name;
            frm.refresh_field('debit_to');
            console.log('Debtors account set to:', debtor.name);
        });
    });
}

/* -------------------------------------------------------
   Auto Income & Expense Accounts for Items
--------------------------------------------------------*/
function apply_item_accounts(frm) {
    if (!frm.doc.custom_branch || !frm.doc.company) return;
    if (!frm.doc.items || !frm.doc.items.length) return;

    const branch_norm = normalize(frm.doc.custom_branch);

    frappe.db.get_list('Account', {
        filters: [
            ['company', '=', frm.doc.company],
            ['is_group', '=', 0]
        ],
        fields: ['name', 'account_name', 'root_type', 'account_type'],
        limit: 500
    }).then(accounts => {
        const pick = (root_type, preferred_types) => {
            const matches = accounts.filter(a =>
                a.root_type === root_type &&
                a.account_name &&
                normalize(a.account_name).includes(branch_norm)
            );
            matches.sort((a, b) => {
                const a_rank = preferred_types.indexOf(a.account_type);
                const b_rank = preferred_types.indexOf(b.account_type);
                if ((a_rank < 0 ? 99 : a_rank) !== (b_rank < 0 ? 99 : b_rank)) {
                    return (a_rank < 0 ? 99 : a_rank) - (b_rank < 0 ? 99 : b_rank);
                }
                return normalize(a.account_name).length - normalize(b.account_name).length;
            });
            return matches[0] || null;
        };

        const income_acc = pick('Income', ['Income Account']);
        const expense_acc = pick('Expense', ['Cost of Goods Sold']);

        if (!income_acc && !expense_acc) {
            console.warn('No branch income/expense accounts found for:', frm.doc.custom_branch);
            return;
        }

        // ERPNext sets item defaults asynchronously after item selection. Delay
        // this override just like the original client script so the branch
        // accounts win over the item's default accounts.
        setTimeout(() => {
            (frm.doc.items || []).forEach(row => {
                if (income_acc) {
                    frappe.model.set_value(row.doctype, row.name, 'income_account', income_acc.name);
                }
                if (expense_acc) {
                    frappe.model.set_value(row.doctype, row.name, 'expense_account', expense_acc.name);
                }
            });
            frm.refresh_field('items');
        }, 250);
    });
}

/* -------------------------------------------------------
   Handle NEW Item Insertion
--------------------------------------------------------*/
frappe.ui.form.on('Sales Invoice Item', {
    item_code(frm, cdt, cdn) {
        // wait a bit to ensure row exists
        setTimeout(() => apply_item_accounts(frm), 100);
    }
});

/* -------------------------------------------------------
   Unlock Fields (ERPNext-safe)
--------------------------------------------------------*/
function unlock_fields(frm) {
    frm.set_df_property('debit_to', 'read_only', 0);

    if (frm.fields_dict.taxes) {
        frm.fields_dict.taxes.grid.toggle_enable('account_head', false);
        frm.fields_dict.taxes.grid.toggle_enable('rate', false);
    }

    if (frm.fields_dict.items) {
        frm.fields_dict.items.grid.toggle_enable('income_account', true);
        frm.fields_dict.items.grid.toggle_enable('expense_account', true);
    }
}

/* -------------------------------------------------------
   Normalize Helper
--------------------------------------------------------*/
function normalize(value) {
    return value.toLowerCase().replace(/\s+/g, '').replace(/-/g, '');
}

})();


/* Ported Client Script: Fetch Saleman Price */
(() => {
frappe.ui.form.on("Sales Invoice Item", {
    item_code(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.item_code) return;

        frappe.db.get_value(
            "Item",
            row.item_code,
            "custom_system_sale_price",
            (r) => {
                if (r && r.custom_system_sale_price !== undefined) {
                    frappe.model.set_value(
                        cdt,
                        cdn,
                        "salesman_price",
                        r.custom_system_sale_price
                    );
                }
            }
        );
    }
});

})();


/* Ported Client Script: new Item branch permission */
(() => {
frappe.ui.form.on('Sales Invoice', {
    onload(frm) {
        init_sales_invoice_filters(frm);
    },

    refresh(frm) {
        init_sales_invoice_filters(frm);
    },

    custom_branch(frm) {
        bind_customer_branch_filter(frm);
    }
});

/* -------------------------------------------------------
   Init (Refresh Safe)
--------------------------------------------------------*/
function init_sales_invoice_filters(frm) {
    frappe.after_ajax(() => {
        setup_sales_invoice_filters(frm);
    });
}

/* -------------------------------------------------------
   Main Setup
--------------------------------------------------------*/
function setup_sales_invoice_filters(frm) {
    get_user_branch_permissions().then(userPermissions => {
        set_default_head_office_branch(frm, userPermissions.branches);
        apply_item_filter_sales(frm, userPermissions);
        bind_customer_branch_filter(frm, userPermissions.branches);
    });
}

/* -------------------------------------------------------
   Fetch & Cache Branch Permissions
--------------------------------------------------------*/
let _branch_cache = null;

function get_user_branch_permissions() {
    if (_branch_cache) {
        return Promise.resolve(_branch_cache);
    }

    return frappe.db.get_list('User Permission', {
        filters: {
            user: frappe.session.user,
            allow: 'Branch'
        },
        fields: ['for_value'],
        limit: 100
    }).then(rows => {
        const branches = rows.map(r => r.for_value);

        const result = {
            branches,
            is_head_office: branches.some(b =>
                normalize(b).includes('headoffice')
            ),
            has_khamis_branch: branches.some(b =>
                normalize(b).includes('khamismashit')
            )
        };

        _branch_cache = result;
        return result;
    });
}

/* -------------------------------------------------------
   Default Head Office Branch
--------------------------------------------------------*/
function set_default_head_office_branch(frm, branches) {
    if (frm.doc.custom_branch) return;

    const ho_branch = branches.find(b =>
        normalize(b).includes('headoffice')
    );

    if (ho_branch) {
        frm.set_value('custom_branch', ho_branch);
    }
}

/* -------------------------------------------------------
   Item Visibility Filter
--------------------------------------------------------*/
function apply_item_filter_sales(frm, userPermissions) {
    const { branches } = userPermissions;

    frm.set_query('item_code', 'items', () => {
        // 1️⃣ User has no branch restrictions = show all items
        if (!branches || branches.length === 0) {
            return {};
        }

        // 2️⃣ User has some branch restrictions, hide items not allowed
        const filters = {};

        // Head Office
        if (!branches.some(b => normalize(b).includes('headoffice'))) {
            filters.custom_is_head_office_only = 0;
        }

        // KHAMIS MASHIT / KHAMIS MASHIT VAN
        if (!branches.some(b => normalize(b).includes('khamismashit'))) {
            filters.custom_is_khamis_only = 0;
        }

        return { filters };
    });
}

/* -------------------------------------------------------
   Customer Branch Filter
--------------------------------------------------------*/
function bind_customer_branch_filter(frm, branches) {
    get_user_branch_permissions().then(({ branches }) => {
        // retry if field not ready
        if (!frm.fields_dict.customer) {
            setTimeout(() => bind_customer_branch_filter(frm, branches), 100);
            return;
        }

        // For users without Branch User Permission, filter by the branch
        // selected on this invoice instead of showing all customers.
        const allowed = (branches && branches.length)
            ? branches
            : (frm.doc.custom_branch ? [frm.doc.custom_branch] : []);
        if (!allowed.length) return;

        frm.set_query("customer", () => ({
            filters: { custom_branch: ["in", allowed] }
        }));
    });
}

/* -------------------------------------------------------
   Normalize Helper
--------------------------------------------------------*/
function normalize(val) {
    return (val || '')
        .toLowerCase()
        .replace(/\s+/g, '')
        .replace(/-/g, '');
}

})();


/* Ported Client Script: Sales Invoice Multiple Item Percentage Search */
(() => {
frappe.ui.form.on("Sales Invoice", {
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


/* Ported Client Script: Zero Item Qty or Rate */
(() => {
frappe.ui.form.on('Sales Invoice Item', {
    item_code: function(frm, cdt, cdn) {
        check_previous_items(frm, cdn);
    },
    qty: function(frm, cdt, cdn) {
        check_previous_items(frm, cdn);
    },
    rate: function(frm, cdt, cdn) {
        check_previous_items(frm, cdn);
    }
});

// Also run on final save/submit
frappe.ui.form.on('Sales Invoice', {
    validate: function(frm) {
        check_previous_items(frm, null, true); // check all items on submit
    }
});

function check_previous_items(frm, current_cdn, is_submit=false) {
    let error_items = [];
    let items_to_check = frm.doc.items;

    // If not submit, skip the current row being entered
    if(!is_submit && current_cdn) {
        items_to_check = items_to_check.filter(item => item.name !== current_cdn);
    }

    items_to_check.forEach(function(item) {
        if (!item.item_code) return; // skip blank rows

        if (!item.qty || item.qty === 0) {
            error_items.push(`Row ${item.idx} (${item.item_code}): Quantity is zero`);
        }
        if (!item.rate || item.rate === 0) {
            error_items.push(`Row ${item.idx} (${item.item_code}): Rate is zero`);
        }
    });

    if(error_items.length) {
        frappe.msgprint({
            title: __('Invalid Items'),
            message: error_items.join('<br>'),
            indicator: 'red'
        });
        frappe.validated = false;
    }
}

})();


/* Ported Client Script: Custom Letter head branch wise */
(() => {
frappe.ui.form.on('Sales Invoice', {

    onload(frm) {
        apply_letterhead(frm);
    },

    refresh(frm) {
        apply_letterhead(frm);
    },

    custom_branch(frm) {
        apply_letterhead(frm);
    },

    // Company select hote hi Frappe apna default letter_head set kar deta hai -
    // isliye company change ke baad bhi humara branch-based letterhead override chalna chahiye
    company(frm) {
        apply_letterhead(frm);
    }
});

function apply_letterhead(frm) {

    // Submitted/cancelled invoice pr kabhi mat chalao
    if (frm.doc.docstatus !== 0) return;

    if (!frm.doc.custom_branch) return;

    frappe.db.get_list('Letter Head', {
        fields: ['name'],
        filters: { disabled: 0 },
        limit_page_length: 500
    }).then(letterheads => {

        const branch_norm = normalize(frm.doc.custom_branch);

        // Sirf EXACT match - branch ke naam se letterhead ka naam match hona chahiye
        const match = letterheads.find(lh => normalize(lh.name) === branch_norm);

        if (match) {
            if (frm.doc.letter_head !== match.name) {
                frm.set_value('letter_head', match.name);
            }
        } else {
            console.warn('⚠️ No letterhead found matching branch:', frm.doc.custom_branch);
        }
    });
}

function normalize(value) {
    if (!value) return '';
    return value.toString().toLowerCase()
        .replace(/\s+/g, '')
        .replace(/-/g, '')
        .replace(/_/g, '')
        .replace(/[()]/g, '')
        .replace(/[&]/g, 'and')
        .trim();
}

})();
