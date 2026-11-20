/* Ported live Client Scripts for Expense Claim. */


/* Ported Client Script: Expense Claim */
(() => {
frappe.ui.form.on('Expense Claim', {

    onload: function(frm) {
        apply_tax_logic(frm);
    },

    refresh: function(frm) {
        expand_tax_section(frm);
    }

});

// ===============================
// ✅ TAX LOGIC
// ===============================
function apply_tax_logic(frm) {

    if (frm.doc.taxes && frm.doc.taxes.length > 0) return;

    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "User Permission",
            filters: {
                user: frappe.session.user,
                allow: "Branch"
            },
            fields: ["for_value"]
        },
        callback: function(res) {

            let branches = (res.message || []).map(r => r.for_value);

            // ✅ No branch OR multiple → Head Office
            if (branches.length !== 1) {
                add_tax_row(frm, "VAT 15% -  HeadOffice - AWH");
                
                return;
            }

            let branch = branches[0];

            frappe.db.get_list("Account", {
                fields: ["name"],
                filters: {
                    account_type: "Tax"
                },
                limit: 200
            }).then(accounts => {

                let matched = accounts.find(acc =>
                    acc.name.toLowerCase().includes(branch.toLowerCase())
                );

                if (matched) {
                    add_tax_row(frm, matched.name);
                } else {
                    add_tax_row(frm, "VAT 15% -  HeadOffice - AWH");
                }

            });

        }
    });
}

// ===============================
// ✅ ADD TAX ROW
// ===============================
function add_tax_row(frm, account) {

    let row = frm.add_child("taxes");

    row.account_head = account;
    row.rate = 15;

    // ✅ NEW: Description
    row.description = "VAT 15%";

    frm.refresh_field("taxes");
}

// ===============================
// ✅ FORCE EXPAND TAX SECTION
// ===============================
function expand_tax_section(frm) {

    let section = frm.fields_dict["taxes_section"];

    if (section) {
        section.collapse(false);

        if (section.$wrapper) {
            section.$wrapper.find('.section-head')
                .off('click')
                .on('click', function(e) {
                    e.stopPropagation();
                });
        }
    }
}

})();
