frappe.ui.form.on("Material Request", {
	refresh(frm) {
		const finance_role =
			frappe.session.user === "Administrator" ||
			frappe.user.has_role("Accounts Manager") ||
			frappe.user.has_role("System Manager");
		if (frm.doc.docstatus !== 0 || !frm.has_perm("write") || !finance_role) return;

		frm.add_custom_button(__("Preview Branch Credit"), async () => {
			if (frm.is_new() || frm.is_dirty()) {
				frappe.msgprint(__("Save the Material Request draft before previewing its current rows."));
				return;
			}
			const name = frm.doc.name;
			try {
				const response = await frappe.call({
					method: "awht_custom.credit_control.material_request.preview_limit",
					type: "POST",
					args: { material_request: name },
					freeze: true,
				});
				if (frm.doc.name !== name || frm.doc.docstatus !== 0 || frm.is_dirty()) return;
				const feedback = {
					inactive: [__("Credit control is disabled for this company."), "blue"],
					not_applicable: [__("No head-office branch transfer is in this request."), "blue"],
					within_limit: [__("Current estimate is within the branch limit. This is not a reservation; submission and the eventual Stock Entry are checked again."), "green"],
					blocked: [__("Current estimate is blocked by the branch limit. Submission will be checked again on the server."), "red"],
				}[response.message && response.message.status];
				if (!feedback) throw new Error("Unexpected branch credit preview result");
				frappe.msgprint({ title: __("Branch Credit Preview"), message: feedback[0], indicator: feedback[1] });
			} catch (error) {
				frappe.msgprint(__("Branch credit preview is unavailable. Check access or request data; submission is validated on the server."));
			}
		});
	},
});
