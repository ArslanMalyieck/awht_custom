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
