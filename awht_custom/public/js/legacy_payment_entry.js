/* Ported live Client Scripts for Payment Entry. */


/* Ported Client Script: Mode Of Payment Cash */
(() => {
frappe.ui.form.on('Payment Entry', {
    mode_of_payment: function(frm) {

        if (!frm.doc.mode_of_payment) return;

        frappe.db.get_value(
            "Mode of Payment",
            frm.doc.mode_of_payment,
            "type",
            function(r) {
                if (r && r.type === "Cash") {
                    frm.set_df_property("reference_no", "hidden", 1);
                    frm.set_df_property("reference_date", "hidden", 1);

                    frm.set_value("reference_no", "");
                    frm.set_value("reference_date", "");

                } else {
                    frm.set_df_property("reference_no", "hidden", 0);
                    frm.set_df_property("reference_date", "hidden", 0);
                }
            }
        );
    },

    // refresh: function(frm){
    //     // frm.trigger("mode_of_payment");
    // }
});

})();


(() => {
const PARTY_BRANCH_FIELDS = {
    Customer: "custom_branch",
    Employee: "branch",
};

const BRANCH_MODE_ALIASES = [
    { branch: 'dammamvan', aliases: ['dammamvan', 'damamvan', 'damam'] },
    { branch: 'dcksaniya', aliases: ['dcksaniya', 'dcksanaya', 'dcksinaya'] },
    { branch: 'dckshowroom', aliases: ['dckshowroom'] },
    { branch: 'hashamvan', aliases: ['hashamvan', 'hashimvan'] },
    { branch: 'headoffice', aliases: ['headoffice'] },
    { branch: 'khamismashit', aliases: ['khamismashit'] },
    { branch: 'khamismashitvan', aliases: ['khamismashitvan', 'khamisvan'] },
    { branch: 'kifiyatvan', aliases: ['kifiyatvan', 'kifayatvan'] },
    { branch: 'mmkshop', aliases: ['mmkshop'] },
    { branch: 'tanveervan', aliases: ['tanveervan'] },
    { branch: 'umarvan', aliases: ['umarvan'] },
];

let mode_names_cache = null;
let mode_names_promise = null;

frappe.ui.form.on('Payment Entry', {
    onload(frm) {
        setup_branch_filters(frm);
    },

    refresh(frm) {
        setup_branch_filters(frm);
    },

    custom_branch(frm) {
        if (frm.doc.party || frm.doc.party_name) {
            frm.set_value('party', '');
            frm.set_value('party_name', '');
        }
        if (frm.doc.mode_of_payment) {
            frm.set_value('mode_of_payment', '');
        }
        setup_branch_filters(frm);
    },

    party_type(frm) {
        if (frm.doc.party) {
            frm.set_value('party', '');
        }
        setup_branch_filters(frm);
    }
});

function setup_branch_filters(frm) {
    setup_party_branch_filter(frm);
    setup_mode_of_payment_filter(frm);
}

function setup_party_branch_filter(frm) {
    if ((frm.doc.docstatus || 0) !== 0) return;

    const branch = frm.doc.custom_branch;
    const is_head_office = normalize(branch).includes('headoffice');
    const has_party_type = !!frm.doc.party_type;

    frm.set_df_property('party_type', 'read_only', branch ? 0 : 1);
    frm.set_df_property('party', 'read_only', branch && has_party_type ? 0 : 1);

    frm.set_query('party', () => {
        if (!branch || !has_party_type) {
            return { filters: { name: ['=', '__none__'] } };
        }

        const party_type = frm.doc.party_type;
        if (is_head_office) {
            if (party_type === 'Employee') {
                return { query: 'erpnext.controllers.queries.employee_query' };
            }
            if (party_type === 'Shareholder') {
                return { filters: { company: frm.doc.company } };
            }
            return {};
        }

        const branch_field = get_party_branch_field(party_type);
        if (!branch_field) {
            if (party_type === 'Shareholder') {
                return { filters: { company: frm.doc.company } };
            }
            return {};
        }

        const filters = { [branch_field]: branch };
        if (party_type === 'Employee') {
            return {
                query: 'erpnext.controllers.queries.employee_query',
                filters,
            };
        }
        return { filters };
    });
}

function setup_mode_of_payment_filter(frm) {
    if ((frm.doc.docstatus || 0) !== 0) return;

    const branch = frm.doc.custom_branch;
    const is_head_office = normalize(branch).includes('headoffice');

    frm.set_df_property('mode_of_payment', 'read_only', branch ? 0 : 1);
    apply_mode_of_payment_query(frm, branch, is_head_office);

    if (!branch || is_head_office || mode_names_cache) return;

    load_mode_names().then(() => {
        if (frm.doc.custom_branch === branch) {
            apply_mode_of_payment_query(frm, branch, is_head_office);
        }
    });
}

function apply_mode_of_payment_query(frm, branch, is_head_office) {
    frm.set_query('mode_of_payment', () => {
        if (!branch) {
            return { filters: { name: ['=', '__none__'] } };
        }
        if (is_head_office) {
            return {};
        }
        if (!mode_names_cache) {
            return { filters: { name: ['=', '__none__'] } };
        }

        const allowed = mode_names_cache.filter(name => mode_belongs_to_branch(name, branch));
        return {
            filters: {
                name: ['in', allowed.length ? allowed : ['__none__']]
            }
        };
    });
}

function load_mode_names() {
    if (mode_names_cache) return Promise.resolve(mode_names_cache);
    if (!mode_names_promise) {
        mode_names_promise = frappe.db.get_list('Mode of Payment', {
            filters: { enabled: 1 },
            fields: ['name'],
            limit: 0,
        }).then(rows => {
            mode_names_cache = rows.map(row => row.name);
            return mode_names_cache;
        });
    }
    return mode_names_promise;
}

function mode_belongs_to_branch(mode_name, branch) {
    const mode = normalize(mode_name);
    const branch_key = normalize(branch);
    let owner = null;

    for (const entry of BRANCH_MODE_ALIASES) {
        for (const alias of entry.aliases) {
            if (mode.includes(alias) && (!owner || alias.length > owner.alias_length)) {
                owner = { branch: entry.branch, alias_length: alias.length };
            }
        }
    }

    if (owner) return owner.branch === branch_key;
    return mode.includes(branch_key);
}

function get_party_branch_field(party_type) {
    if (!party_type) return null;
    if (PARTY_BRANCH_FIELDS[party_type]) return PARTY_BRANCH_FIELDS[party_type];

    const meta = frappe.get_meta(party_type);
    if (!meta || !meta.has_field) return null;
    if (meta.has_field('custom_branch')) return 'custom_branch';
    if (meta.has_field('branch')) return 'branch';
    return null;
}

function normalize(value) {
    return (value || '').toLowerCase().replace(/\s+/g, '').replace(/-/g, '');
}

})();
