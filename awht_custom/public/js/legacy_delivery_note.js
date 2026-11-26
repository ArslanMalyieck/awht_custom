/* Ported live Client Scripts for Delivery Note. */


/* Ported Client Script: Delivey Note Multiple Item Percentage Search */
(() => {
frappe.ui.form.on("Delivery Note", {
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


/* Ported Client Script: branch item visibility */
(() => {
let branch_cache = null;

frappe.ui.form.on("Delivery Note", {
    onload(frm) {
        setup_item_filter(frm);
    },

    refresh(frm) {
        setup_item_filter(frm);
    },

    onload_post_render(frm) {
        apply_item_filter(frm);
    },
});

function setup_item_filter(frm) {
    apply_item_filter(frm);
    setTimeout(() => apply_item_filter(frm), 300);

    if (branch_cache) return;

    frappe.db.get_list("User Permission", {
        filters: { user: frappe.session.user, allow: "Branch" },
        fields: ["for_value"],
        limit: 100,
    }).then(rows => {
        branch_cache = rows.map(row => row.for_value);
        apply_item_filter(frm);
    });
}

function apply_item_filter(frm) {
    const branch_filters = user_branch_filters(branch_cache);
    frm.set_query("item_code", "items", () => ({
        query: "erpnext.controllers.queries.item_query",
        filters: {
            is_sales_item: 1,
            customer: frm.doc.customer,
            has_variants: 0,
            ...branch_filters,
        },
    }));
}

function user_branch_filters(branches) {
    if (!branches || !branches.length) return {};

    const has_head_office = branches.some(branch =>
        normalize(branch).includes("headoffice")
    );
    const has_khamis = branches.some(branch =>
        normalize(branch).includes("khamismashit")
    );
    const filters = {};

    if (!has_head_office) {
        filters.custom_is_head_office_only = 0;
    }
    if (!has_khamis) {
        filters.custom_is_khamis_only = 0;
    }
    return filters;
}

function normalize(value) {
    return (value || "").toLowerCase().replace(/\s+/g, "").replace(/-/g, "");
}

})();
