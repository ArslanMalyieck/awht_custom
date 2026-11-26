/* Ported live Client Scripts for Quotation. */


/* Ported Client Script: Quotation One Script */
(() => {
/* =========================================================
   WILDCARD SAFE • BRANCH SAFE • CUSTOMER FILTER FIXED
=========================================================*/


let _branch_cache = null;
let _branch_warehouse = null;
let _account_timer = null;


/* =========================================================
   QUOTATION EVENTS
=========================================================*/

frappe.ui.form.on('Quotation', {

    onload(frm) {
        setup_select_item_dialog();
        init(frm);
    },

    refresh(frm) {
        init(frm);
    },

    onload_post_render(frm) {
        apply_item_filter(frm);
    },

    custom_branch(frm) {
        handle_branch_change(frm);
    },

    quotation_to(frm) {
        apply_customer_filter(frm);
    },

    items_add(frm, cdt, cdn) {
        setTimeout(() => {
            enforce_item_warehouse(frm, cdt, cdn);
        }, 500);
    }
});


/* =========================================================
   ITEM EVENTS
=========================================================*/

frappe.ui.form.on('Quotation Item', {

    item_code(frm, cdt, cdn) {

        const row = locals[cdt][cdn];
        if (!row.item_code) return;

        frappe.db.get_value('Item', row.item_code, 'custom_system_sale_price')
            .then(r => {
                const price = r.message?.custom_system_sale_price;
                if (price != null) {
                    frappe.model.set_value(cdt, cdn, 'salesman_price', price);
                }
            });

        enforce_item_warehouse(frm, cdt, cdn);
        debounce_accounts(frm);
    },

    qty(frm, cdt, cdn) {
        debounce_accounts(frm);
    },

    rate(frm, cdt, cdn) {
        debounce_accounts(frm);
    }
});


/* =========================================================
   INIT
=========================================================*/

function init(frm) {

    toggle_tables(frm);
    apply_item_filter(frm);
    setTimeout(() => apply_item_filter(frm), 300);

    frappe.after_ajax(() => {

        get_user_branch_permissions().then(res => {

            set_default_branch(frm, res.branches);

            apply_item_filter(frm);
            apply_customer_filter(frm);

            setTimeout(() => {
                apply_all(frm);
            }, 300);
        });

    });
}


/* =========================================================
   MASTER APPLY
=========================================================*/

function apply_all(frm) {

    resolve_warehouse(frm);
    apply_item_warehouse(frm);
    apply_tax_template(frm);
    debounce_accounts(frm);

    setTimeout(() => {
        apply_item_warehouse(frm);
    }, 700);
}


/* =========================================================
   BRANCH CHANGE
=========================================================*/

function handle_branch_change(frm) {

    toggle_tables(frm);

    frm.clear_table('taxes');
    frm.refresh_field('taxes');

    resolve_warehouse(frm);

    apply_item_filter(frm);
    apply_customer_filter(frm);

    setTimeout(() => {
        apply_all(frm);
    }, 300);
}


/* =========================================================
   TABLE CONTROL
=========================================================*/

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


/* =========================================================
   USER PERMISSIONS
=========================================================*/

function get_user_branch_permissions() {

    if (_branch_cache) return Promise.resolve(_branch_cache);

    return frappe.db.get_list('User Permission', {
        filters: {
            user: frappe.session.user,
            allow: 'Branch'
        },
        fields: ['for_value']
    }).then(rows => {

        const branches = rows.map(r => r.for_value);
        _branch_cache = { branches };

        return _branch_cache;
    });
}


/* =========================================================
   DEFAULT BRANCH
=========================================================*/

function set_default_branch(frm, branches) {

    if (frm.doc.custom_branch) return;

    if (!branches || branches.length === 0) {
        frappe.db.get_list('Branch', { fields: ['name'] })
            .then(list => {
                const ho = list.find(b =>
                    normalize(b.name).includes('headoffice')
                );
                if (ho) frm.set_value('custom_branch', ho.name);
            });
        return;
    }

    if (branches.length === 1) {
        frm.set_value('custom_branch', branches[0]);
        return;
    }

    const ho = branches.find(b =>
        normalize(b).includes('headoffice')
    );

    if (ho) frm.set_value('custom_branch', ho);
}


/* =========================================================
   ITEM VISIBILITY FILTER
=========================================================*/

function apply_item_filter(frm) {

    frm.set_query('item_code', 'items', () => ({
        query: 'erpnext.controllers.queries.item_query',
        filters: {
            is_sales_item: 1,
            has_variants: 0,
            ...(frm.doc.customer ? { customer: frm.doc.customer } : {}),
            ...awht_custom_item_branch_filters(frm.doc.custom_branch),
        }
    }));
}


/* =========================================================
   🔥 CUSTOMER FILTER FIX (MAIN PART)
=========================================================*/

function apply_customer_filter(frm) {

    if (frm.doc.quotation_to !== "Customer") return;

    frm.set_query("party_name", () => {

        if (!frm.doc.custom_branch) return {};

        return {
            filters: {
                custom_branch: frm.doc.custom_branch
            }
        };
    });
}


/* =========================================================
   WAREHOUSE
=========================================================*/

function resolve_warehouse(frm) {
    if (!frm.doc.custom_branch) return;
    _branch_warehouse = `${frm.doc.custom_branch} - AWH`;
}


/* =========================================================
   ITEM WAREHOUSE
=========================================================*/

function apply_item_warehouse(frm) {

    if (!_branch_warehouse) return;

    (frm.doc.items || []).forEach(row => {
        frappe.model.set_value(row.doctype, row.name, 'warehouse', _branch_warehouse);
    });

    frm.refresh_field('items');
}


/* =========================================================
   SINGLE ROW WAREHOUSE
=========================================================*/

function enforce_item_warehouse(frm, cdt, cdn) {

    if (!_branch_warehouse) return;

    frappe.model.set_value(cdt, cdn, 'warehouse', _branch_warehouse);
}


/* =========================================================
   TAX TEMPLATE
=========================================================*/

function apply_tax_template(frm) {

    if (!frm.doc.custom_branch || !frm.doc.company) return;

    const branch_norm = normalize(frm.doc.custom_branch);

    frappe.db.get_list('Sales Taxes and Charges Template', {
        filters: { company: frm.doc.company },
        fields: ['name']
    }).then(list => {

        const template = list.find(t =>
            normalize(t.name).includes(branch_norm)
        );

        if (template) {
            frm.set_value('taxes_and_charges', template.name);
        }
    });
}


/* =========================================================
   ACCOUNTS
=========================================================*/

function debounce_accounts(frm) {

    clearTimeout(_account_timer);

    _account_timer = setTimeout(() => {
        apply_item_accounts(frm);
    }, 400);
}


function apply_item_accounts(frm) {

    if (!frm.doc.custom_branch || !frm.doc.company) return;

    const branch_norm = normalize(frm.doc.custom_branch);

    frappe.db.get_list('Account', {
        filters: [
            ['company', '=', frm.doc.company],
            ['is_group', '=', 0]
        ],
        fields: ['name', 'account_name', 'root_type']
    }).then(accounts => {

        const income = accounts.find(a =>
            a.root_type === 'Income' &&
            normalize(a.account_name).includes(branch_norm)
        );

        const expense = accounts.find(a =>
            a.root_type === 'Expense' &&
            normalize(a.account_name).includes(branch_norm)
        );

        (frm.doc.items || []).forEach(row => {

            if (income)
                frappe.model.set_value(row.doctype, row.name, 'income_account', income.name);

            if (expense)
                frappe.model.set_value(row.doctype, row.name, 'expense_account', expense.name);
        });

        frm.refresh_field('items');
    });
}


/* =========================================================
   SELECT ITEM TOOL
=========================================================*/

let dialog_bound = false;

function setup_select_item_dialog() {

    if (dialog_bound) return;
    dialog_bound = true;

    $(document).on('shown.bs.modal', function () {

        const dialog = $('.modal:visible');

        if (!dialog.find('.modal-title').text().includes('Select Item')) return;
        if (dialog.find('.insert-percent-btn').length) return;

        const input = dialog.find("input[type='text']").first();

        const btn = $(`<button type="button" class="btn btn-sm btn-primary insert-percent-btn">%</button>`);

        btn.on('click', function () {
            const el = input.get(0);
            const start = el.selectionStart ?? el.value.length;
            const end = el.selectionEnd ?? el.value.length;

            el.value = el.value.substring(0, start) + '%' + el.value.substring(end);
        });

        input.after(btn);
    });
}


/* =========================================================
   NORMALIZE
=========================================================*/

function normalize(v) {
    return (v || '').toLowerCase().replace(/\s+/g, '').replace(/-/g, '');
}

})();
