/* Ported live Client Scripts for POS Invoice. */


/* Ported Client Script: POS Invoice HeadOffice Items */
(() => {
frappe.ui.form.on('POS Invoice', {
    onload(frm) {
        setup_pos_filters(frm);
    },

    refresh(frm) {
        setup_pos_filters(frm);
    }
});

/* -------------------------------------------------------
   Main Setup (Single Entry Point)
--------------------------------------------------------*/
function setup_pos_filters(frm) {
    get_user_branch_permissions().then(({ branches, is_head_office, is_khamis }) => {
        apply_item_filter(frm, is_head_office, is_khamis);
        apply_customer_filter_pos_safe(frm, branches);
    });
}

/* -------------------------------------------------------
   Fetch & Cache User Branch Permissions
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

            // 🏢 Head Office detection
            is_head_office: branches.some(b =>
                normalize(b).includes('headoffice')
            ),

            // 🟡 KHAMIS branches detection
            is_khamis: branches.some(b =>
                normalize(b).includes('khamismashit')
            )
        };

        _branch_cache = result;
        return result;
    });
}

/* -------------------------------------------------------
   Item Visibility Filter (POS SAFE)
--------------------------------------------------------*/
function apply_item_filter(frm, is_head_office, is_khamis) {
    frm.set_query('item_code', 'items', () => {

        // 🔓 Head Office → see everything
        if (is_head_office) {
            return {};
        }

        // 🟡 KHAMIS branches
        if (is_khamis) {
            return {
                filters: {
                    custom_is_head_office_only: 0
                }
            };
        }

        // 🔒 All other branches
        return {
            filters: {
                custom_is_head_office_only: 0,
                custom_is_khamis_only: 0
            }
        };
    });
}

/* -------------------------------------------------------
   Customer Branch Filter (POS-SAFE FIX)
--------------------------------------------------------*/
function apply_customer_filter_pos_safe(frm, branches) {
    if (!branches || !branches.length) return;
    if (!frm.fields_dict.customer) return;

    // 🔒 prevent multiple bindings
    if (frm.__customer_filter_bound) return;
    frm.__customer_filter_bound = true;

    // 🔥 POS-safe: bind query on focus
    frm.fields_dict.customer.$input.on('focus', function () {
        frm.set_query('customer', () => {
            return {
                filters: {
                    custom_branch: ['in', branches]
                }
            };
        });
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


/* Ported Client Script: Pos Invoice Accounts toggle */
(() => {
frappe.ui.form.on('POS Invoice', {
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

        // wait to ensure items grid is ready
        setTimeout(() => apply_item_accounts(frm), 200);

        unlock_fields(frm);
    }
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
        filters: [['company', '=', frm.doc.company]],
        fields: ['name'],
        limit: 200
    }).then(templates => {
        const template = templates.find(t =>
            normalize(t.name).includes(branch_norm) &&
            normalize(t.name).includes('awh')
        );

        if (!template) {
            frappe.msgprint({
                title: __('Configuration Missing'),
                message: __(`Tax Template not found for ${frm.doc.custom_branch}`),
                indicator: 'red'
            });
            return;
        }

        if (frm.doc.taxes_and_charges === template.name) return;

        frm.clear_table('taxes');
        frm.set_value('taxes_and_charges', null);
        frm.refresh_fields(['taxes', 'taxes_and_charges']);

        frappe.after_ajax(() => {
            frm.set_value('taxes_and_charges', template.name);
            setTimeout(() => {
                frm.trigger('taxes_and_charges');
                frm.refresh_field('taxes');
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
            acc.account_name &&
            normalize(acc.account_name).includes(branch_norm)
        );

        if (!debtor) return;

        frappe.after_ajax(() => {
            frm.set_df_property('debit_to', 'read_only', 0);
            frm.set_value('debit_to', debtor.name);
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
        fields: ['name', 'account_name', 'root_type'],
        limit: 500
    }).then(accounts => {
        const income_acc = accounts.find(a =>
            a.root_type === 'Income' &&
            normalize(a.account_name).includes(branch_norm)
        );

        const expense_acc = accounts.find(a =>
            a.root_type === 'Expense' &&
            normalize(a.account_name).includes(branch_norm)
        );

        if (!income_acc && !expense_acc) return;

        frm.doc.items.forEach(row => {
            if (income_acc) {
                frappe.model.set_value(row.doctype, row.name, 'income_account', income_acc.name);
            }
            if (expense_acc) {
                frappe.model.set_value(row.doctype, row.name, 'expense_account', expense_acc.name);
            }
        });

        frm.refresh_field('items');
    });
}

/* -------------------------------------------------------
   Handle NEW Item Insertion
--------------------------------------------------------*/
frappe.ui.form.on('POS Invoice Item', {
    item_code(frm, cdt, cdn) {
        setTimeout(() => apply_item_accounts(frm), 100);
    }
});

/* -------------------------------------------------------
   Unlock Fields
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
    return value
        .toLowerCase()
        .replace(/\s+/g, '')
        .replace(/-/g, '');
}

})();
