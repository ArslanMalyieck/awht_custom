frappe.ui.form.on("Customer", {
	refresh(frm) {
		if (frm.is_new() || !frm.has_perm("read") || !frm.has_perm("print")) return;
		frm.add_custom_button(__("Print QR"), async () => {
			// Reserve the window during the click so popup blockers do not discard the print request.
			const popup = window.open("", "_blank");
			if (!popup) {
				frappe.msgprint(__("Please allow popups for this site to print the QR."));
				return;
			}
			popup.opener = null;
			popup.document.write("<!doctype html><title>Customer QR</title><p>Loading...</p>");
			popup.document.close();
			try {
				const response = await frappe.call({
					method: "awht_custom.operations.customer_qr.get_customer_qr_print_page",
					args: { customer: frm.doc.name },
				});
				if (popup.closed) return;
				if (!response.message) {
					popup.close();
					return;
				}
				popup.document.open();
				popup.document.write(response.message);
				popup.document.close();
				const image = popup.document.querySelector("img");
				if (!image) {
					popup.close();
					return;
				}
				const print = () => {
					if (popup.closed) return;
					popup.focus();
					popup.print();
				};
				if (image.complete) {
					if (image.naturalWidth) print();
					else popup.close();
				} else {
					image.addEventListener("load", print, { once: true });
					image.addEventListener("error", () => popup.close(), { once: true });
				}
			} catch (error) {
				if (!popup.closed) popup.close();
			}
		}, __("Print"));
	},
});
