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
