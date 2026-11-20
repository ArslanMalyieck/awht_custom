/* Ported live Client Scripts for Sales Order. */


/* Ported Client Script: Sales Order Scripts */
(() => {
// ============================================================
// Sales Order - Master Client Script (Branch-Driven Automation)
// ============================================================

const SO_STATE = {
    branch_perm_cache: null,        // cached user branch permissions
    account_cache: {},              // company -> accounts[]
    warehouse_cache: {},            // company -> warehouses[]
    tax_template_applied_for: null, // cache key: company::branch
    accounts_applied_for: null,     // cache key: company::branch
    warehouse_applied_for: null     // cache key: company::branch
};

frappe.ui.form.on('Sales Order', {
    onload(frm) {
        setup_branch_permission_filters(frm);
        bind_insert_percent_button();
    },

    refresh(frm) {
        toggle_customer_lock(frm);
        toggle_tables(frm);
        unlock_fields(frm);
        setup_branch_permission_filters(frm); // cheap, uses cache
    },

    custom_branch(frm) {
    toggle_customer_lock(frm);
    toggle_tables(frm);
    bind_customer_branch_filter(frm); // re-bind with new branch immediately

    if (!frm.doc.custom_branch) return;

    apply_tax_template(frm);
    apply_item_accounts(frm);
    apply_target_warehouse(frm);
    unlock_fields(frm);
},

    company(frm) {
        // company changed -> cached account/warehouse lookups are stale, reset per-doc cache keys
        SO_STATE.tax_template_applied_for = null;
        SO_STATE.accounts_applied_for = null;
        SO_STATE.warehouse_applied_for = null;

        if (frm.doc.custom_branch) {
            apply_tax_template(frm);
            apply_item_accounts(frm);
            apply_target_warehouse(frm);
        }
    }
});

// Event-driven price fetch (no MutationObserver — zero polling, zero idle load)
frappe.ui.form.on('Sales Order Item', {
    item_code(frm, cdt, cdn) {
        fetch_salesman_price(cdt, cdn);
        apply_item_accounts(frm, cdn); // new row also needs branch accounts
    },
    items_add(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (row.item_code) {
            fetch_salesman_price(cdt, cdn);
            apply_item_accounts(frm, cdn);
        }
    }
});

function fetch_salesman_price(cdt, cdn) {
    const row = locals[cdt][cdn];
    if (!row.item_code) return;
    frappe.db.get_value('Item', row.item_code, 'custom_system_sale_price')
        .then(r => {
            const price = r.message?.custom_system_sale_price;
            if (price) frappe.model.set_value(cdt, cdn, 'salesman_price', price);
        })
        .catch(err => console.error('fetch_salesman_price failed:', err));
}

/* ---------------- Branch Mandatory -> Customer Lock ---------------- */
function toggle_customer_lock(frm) {
    const has_branch = !!frm.doc.custom_branch;
    frm.set_df_property('customer', 'read_only', has_branch ? 0 : 1);

    if (!has_branch && frm.doc.customer) {
        // safety: don't let a customer sit selected without a branch
        frm.set_value('customer', '');
    }
}

/* ---------------- Show / Hide Tables ---------------- */
function toggle_tables(frm) {
    if (frm.doc.docstatus !== 0) return; // never touch submitted/cancelled docs

    const show = !!frm.doc.custom_branch;
    frm.toggle_display('items', show);
    frm.toggle_display('taxes', show);

    if (!show) {
        frm.clear_table('items');
        frm.clear_table('taxes');
        frm.refresh_fields(['items', 'taxes']);
    }
}

/* ---------------- Tax Template (cached, one call per branch/company) ---------------- */
function apply_tax_template(frm) {
    if (frm.doc.docstatus !== 0) return;
    if (!frm.doc.custom_branch || !frm.doc.company) return;

    const branch_norm = normalize(frm.doc.custom_branch);
    const cache_key = `${frm.doc.company}::${branch_norm}`;
    if (SO_STATE.tax_template_applied_for === cache_key && frm.doc.taxes_and_charges) return;

    frappe.db.get_list('Sales Taxes and Charges Template', {
        filters: [['company', '=', frm.doc.company]],
        fields: ['name'],
        limit: 500
    }).then(templates => {
        const template = templates.find(t =>
            normalize(t.name).includes(branch_norm) && normalize(t.name).includes('awh')
        );

        if (!template) {
            frappe.msgprint({
                title: __('Configuration Missing'),
                message: __(`Tax Template not found for ${frm.doc.custom_branch}`),
                indicator: 'red'
            });
            return;
        }

        SO_STATE.tax_template_applied_for = cache_key;
        if (frm.doc.taxes_and_charges === template.name) return;

        frm.clear_table('taxes');
        frm.set_value('taxes_and_charges', template.name);
        frm.refresh_fields(['taxes', 'taxes_and_charges']);
    }).catch(err => console.error('apply_tax_template failed:', err));
}

/* ---------------- Income / Expense Accounts (cached per company) ---------------- */
function apply_item_accounts(frm, single_cdn) {
    if (frm.doc.docstatus !== 0) return;
    if (!frm.doc.custom_branch || !frm.doc.company) return;
    if (!frm.doc.items || !frm.doc.items.length) return;

    const branch_norm = normalize(frm.doc.custom_branch);

    get_company_accounts(frm.doc.company).then(accounts => {
        const income_acc = accounts.find(a => a.root_type === 'Income' && normalize(a.account_name).includes(branch_norm));
        const expense_acc = accounts.find(a => a.root_type === 'Expense' && normalize(a.account_name).includes(branch_norm));

        const rows = single_cdn ? frm.doc.items.filter(r => r.name === single_cdn) : frm.doc.items;

        rows.forEach(row => {
            if ('income_account' in row && income_acc) frappe.model.set_value(row.doctype, row.name, 'income_account', income_acc.name);
            if ('expense_account' in row && expense_acc) frappe.model.set_value(row.doctype, row.name, 'expense_account', expense_acc.name);
        });
        frm.refresh_field('items');
    }).catch(err => console.error('apply_item_accounts failed:', err));
}

function get_company_accounts(company) {
    if (SO_STATE.account_cache[company]) return Promise.resolve(SO_STATE.account_cache[company]);
    return frappe.db.get_list('Account', {
        filters: [['company', '=', company], ['is_group', '=', 0]],
        fields: ['name', 'account_name', 'root_type'],
        limit: 500
    }).then(accounts => {
        SO_STATE.account_cache[company] = accounts;
        return accounts;
    });
}

/* ---------------- Target Warehouse (branch-based, header field -> auto-fills all rows) ---------------- */
function apply_target_warehouse(frm) {
    if (frm.doc.docstatus !== 0) return;
    if (!frm.doc.custom_branch || !frm.doc.company) return;

    const branch_norm = normalize(frm.doc.custom_branch);
    const cache_key = `${frm.doc.company}::${branch_norm}`;
    if (SO_STATE.warehouse_applied_for === cache_key && frm.doc.set_warehouse) return;

    get_company_warehouses(frm.doc.company).then(warehouses => {
        const match = warehouses.find(w => normalize(w.warehouse_name || w.name).includes(branch_norm));

        if (!match) {
            frappe.msgprint({
                title: __('Configuration Missing'),
                message: __(`Warehouse not found for branch ${frm.doc.custom_branch}`),
                indicator: 'orange'
            });
            return;
        }

        SO_STATE.warehouse_applied_for = cache_key;
        if (frm.doc.set_warehouse === match.name) return;

        frm.set_value('set_warehouse', match.name);
        // propagate to existing item rows too
        (frm.doc.items || []).forEach(row => {
            if ('warehouse' in row) frappe.model.set_value(row.doctype, row.name, 'warehouse', match.name);
        });
        frm.refresh_field('items');
    }).catch(err => console.error('apply_target_warehouse failed:', err));
}

function get_company_warehouses(company) {
    if (SO_STATE.warehouse_cache[company]) return Promise.resolve(SO_STATE.warehouse_cache[company]);
    return frappe.db.get_list('Warehouse', {
        filters: [['company', '=', company], ['is_group', '=', 0], ['disabled', '=', 0]],
        fields: ['name', 'warehouse_name'],
        limit: 500
    }).then(warehouses => {
        SO_STATE.warehouse_cache[company] = warehouses;
        return warehouses;
    });
}

/* ---------------- Unlock Fields ---------------- */
function unlock_fields(frm) {
    if (frm.doc.docstatus !== 0) return;

    if (frm.fields_dict.taxes?.grid) {
        frm.fields_dict.taxes.grid.toggle_enable('account_head', false);
        frm.fields_dict.taxes.grid.toggle_enable('rate', false);
    }
    const items_grid = frm.fields_dict.items?.grid;
    if (items_grid) {
        items_grid.docfields.forEach(df => {
            if (['income_account', 'expense_account', 'warehouse'].includes(df.fieldname)) {
                items_grid.toggle_enable(df.fieldname, true);
            }
        });
    }
}

/* ---------------- Branch Permission Filters (Item visibility + Customer) ---------------- */
function setup_branch_permission_filters(frm) {
    get_user_branch_permissions().then(userPermissions => {
        set_default_head_office_branch(frm, userPermissions.branches);
        apply_item_filter(frm);
        bind_customer_branch_filter(frm); // <-- no branches param now
    }).catch(err => console.error('setup_branch_permission_filters failed:', err));
}

function get_user_branch_permissions() {
    if (SO_STATE.branch_perm_cache) return Promise.resolve(SO_STATE.branch_perm_cache);
    return frappe.db.get_list('User Permission', {
        filters: { user: frappe.session.user, allow: 'Branch' },
        fields: ['for_value'],
        limit: 100
    }).then(rows => {
        const branches = rows.map(r => r.for_value);
        SO_STATE.branch_perm_cache = {
            branches,
            is_head_office: branches.some(b => normalize(b).includes('headoffice')),
            has_khamis_branch: branches.some(b => normalize(b).includes('khamismashit'))
        };
        return SO_STATE.branch_perm_cache;
    });
}

function set_default_head_office_branch(frm, branches) {
    if (frm.doc.custom_branch || frm.doc.docstatus !== 0) return;
    const ho_branch = branches.find(b => normalize(b).includes('headoffice'));
    if (ho_branch) frm.set_value('custom_branch', ho_branch);
}

/* ---------------- Item Visibility Filter (based on document's selected branch) ---------------- */
function apply_item_filter(frm) {
    frm.set_query('item_code', 'items', () => {
        const branch = frm.doc.custom_branch;

        // Branch mandatory -> no branch selected yet, block items entirely
        if (!branch) {
            return { filters: { name: ['=', '__none__'] } };
        }

        const branch_norm = normalize(branch);
        const filters = {};

        // This SO's branch is NOT Head Office -> hide head-office-only items
        if (!branch_norm.includes('headoffice')) {
            filters.custom_is_head_office_only = 0;
        }

        // This SO's branch is NOT Khamis Mashit -> hide khamis-only items
        if (!branch_norm.includes('khamismashit')) {
            filters.custom_is_khamis_only = 0;
        }

        return { filters };
    });
}

/* ---------------- Customer Branch Filter (fixed) ---------------- */
function bind_customer_branch_filter(frm) {
    frm.set_query('customer', () => {
        // Branch mandatory — no branch selected yet -> show nothing
        if (!frm.doc.custom_branch) {
            return { filters: { name: ['=', '__none__'] } };
        }
        // Always filter by the branch actually selected on this document,
        // not by the user's permitted branch list.
        return { filters: { custom_branch: frm.doc.custom_branch } };
    });
}

/* ---------------- Insert % Button (bound once, whole session) ---------------- */
function bind_insert_percent_button() {
    if (window.__so_percent_btn_bound) return;
    window.__so_percent_btn_bound = true;

    $(document).on('shown.bs.modal', function () {
        const dialog = $('.modal:visible');
        if (!dialog.find('.modal-title').text().includes('Select Item')) return;

        const input = dialog.find("input[type='text']").first();
        if (!input.length || dialog.find('.insert-percent-btn').length) return;

        const wrapper = $('<div class="d-flex align-items-center gap-2"></div>');
        input.wrap(wrapper);

        const percentBtn = $(`<button type="button" class="btn btn-sm btn-primary insert-percent-btn">Insert %</button>`);
        percentBtn.on('click', function () {
            const el = input.get(0);
            const start = el.selectionStart ?? el.value.length;
            const end = el.selectionEnd ?? el.value.length;
            el.value = el.value.substring(0, start) + '%' + el.value.substring(end);
            el.focus();
            el.selectionStart = el.selectionEnd = start + 1;
        });
        input.after(percentBtn);
    });
}

/* ---------------- Normalize Helper ---------------- */
function normalize(value) {
    return (value || '').toLowerCase().replace(/\s+/g, '').replace(/-/g, '');
}

})();
