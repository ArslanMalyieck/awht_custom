const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "../public/js/material_request.js"), "utf8");

function form() {
	const messages = [];
	const buttons = [];
	const calls = [];
	let handlers;
	const frappe = {
		session: { user: "manager@example.test" },
		user: { has_role: (role) => role === "Accounts Manager" },
		ui: { form: { on: (doctype, events) => {
			assert.equal(doctype, "Material Request");
			handlers = events;
		} } },
		msgprint: (message) => messages.push(message),
		call: (options) => {
			calls.push(options);
			return Promise.resolve({ message: { status: "within_limit" } });
		},
	};
	vm.runInNewContext(script, { frappe, __: (text) => text });
	const frm = {
		doc: { name: "MR-1", docstatus: 0 },
		has_perm: (permission) => permission === "write",
		is_new: () => false,
		is_dirty: () => false,
		add_custom_button: (label, action) => buttons.push({ label, action }),
	};
	handlers.refresh(frm);
	return { frappe, frm, buttons, calls, messages, handlers };
}

test("preview awaits the saved draft POST before showing advisory feedback", async () => {
	const ui = form();
	assert.equal(ui.buttons.length, 1);
	let resolve;
	ui.frappe.call = (options) => {
		ui.calls.push(options);
		return new Promise((done) => { resolve = done; });
	};
	let finished = false;
	const pending = ui.buttons[0].action().then(() => { finished = true; });
	await Promise.resolve();
	assert.equal(finished, false);
	assert.equal(ui.messages.length, 0);
	assert.equal(ui.calls[0].method, "awht_custom.credit_control.material_request.preview_limit");
	assert.equal(ui.calls[0].type, "POST");
	assert.equal(ui.calls[0].args.material_request, "MR-1");
	assert.deepEqual(Object.keys(ui.calls[0].args), ["material_request"]);
	resolve({ message: { status: "within_limit" } });
	await pending;
	assert.equal(finished, true);
	assert.match(ui.messages[0].message, /not a reservation/i);
	assert.doesNotMatch(ui.messages[0].message, /stock value|unpaid amount|100000/i);
});

test("new or dirty drafts must be saved, never previewed with stale rows", async () => {
	for (const method of ["is_new", "is_dirty"]) {
		const ui = form();
		ui.frm[method] = () => true;
		await ui.buttons[0].action();
		assert.equal(ui.calls.length, 0);
		assert.match(ui.messages[0], /Save the Material Request draft/);
	}
});

test("unavailable or blocked results never reveal amounts and do not change validation", async () => {
	for (const status of ["inactive", "not_applicable", "blocked"]) {
		const ui = form();
		ui.frappe.call = async () => ({ message: { status } });
		await ui.buttons[0].action();
		assert.equal(ui.messages[0].title, "Branch Credit Preview");
		assert.doesNotMatch(ui.messages[0].message, /100000|stock value|unpaid amount/i);
		assert.equal(ui.frappe.validated, undefined);
	}
	const denied = form();
	denied.frappe.call = async () => { throw new Error("secret amount 100000"); };
	await denied.buttons[0].action();
	assert.match(denied.messages[0], /preview is unavailable/i);
	assert.doesNotMatch(denied.messages[0], /100000/);
});

test("a changed form suppresses an in-flight response", async () => {
	const ui = form();
	let resolve;
	ui.frappe.call = () => new Promise((done) => { resolve = done; });
	const pending = ui.buttons[0].action();
	ui.frm.is_dirty = () => true;
	resolve({ message: { status: "blocked" } });
	await pending;
	assert.equal(ui.messages.length, 0);
});

test("no button for readonly, submitted or non-finance users", () => {
	for (const restriction of ["readonly", "submitted", "role"]) {
		const ui = form();
		ui.buttons.length = 0;
		if (restriction === "readonly") ui.frm.has_perm = () => false;
		if (restriction === "submitted") ui.frm.doc.docstatus = 1;
		if (restriction === "role") ui.frappe.user.has_role = () => false;
		ui.handlers.refresh(ui.frm);
		assert.equal(ui.buttons.length, 0);
	}
});
