const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function load(script, context) {
	const file = path.join(__dirname, "..", "public", "js", script);
	vm.runInNewContext(fs.readFileSync(file, "utf8"), context, { filename: script });
}

test("authorized customer QR popup prints automatically after its inline image loads", async () => {
	const handlers = {};
	const calls = [];
	const written = [];
	const messages = [];
	let printed = 0;
	let opened = true;
	let ready = false;
	const imageHandlers = {};
	const image = {
		get complete() { return ready; }, naturalWidth: 120,
		addEventListener: (event, callback) => { imageHandlers[event] = callback; },
	};
	const popup = {
		opener: {}, closed: false,
		document: {
			open() {}, close() {}, write: (html) => written.push(html),
			querySelector: () => image,
		},
		focus() {}, print: () => { printed++; },
		close() { this.closed = true; },
	};
	load("customer.js", {
		frappe: {
			ui: { form: { on: (doctype, events) => { handlers[doctype] = events; } } },
			msgprint: (message) => messages.push(message),
			call: async (request) => {
				calls.push(request);
				return { message: '<html><img src="data:image/svg+xml;base64,AA"></html>' };
			},
		},
		__: (text) => text,
		window: { open: () => opened ? popup : null },
	});
	let button;
	let permitted = true;
	const name = "CUST/1?value=<script>&other=two";
	const frm = {
		doc: { name },
		is_new: () => false,
		has_perm: () => permitted,
		add_custom_button: (_label, callback) => { button = callback; },
	};
	handlers.Customer.refresh(frm);
	await button();
	assert.equal(popup.opener, null);
	assert.equal(calls[0].method, "awht_custom.operations.customer_qr.get_customer_qr_print_page");
	assert.equal(calls[0].args.customer, name);
	assert.match(written[1], /data:image\/svg\+xml;base64/);
	assert.equal(printed, 0);
	ready = true;
	imageHandlers.load();
	assert.equal(printed, 1);
	await button();
	assert.equal(printed, 2);
	opened = false;
	await button();
	assert.equal(calls.length, 2);
	assert.match(messages[0], /allow popups/);
	permitted = false;
	button = null;
	handlers.Customer.refresh(frm);
	assert.equal(button, null);
});

test("transfer lookup sends only the saved entry and row IDs, never warehouse or cutoff input", async () => {
	const handlers = {};
	const requests = [];
	const messages = [];
	let click;
	let html;
	let hidden = false;
	const row = {
		doctype: "Stock Entry Detail", name: "ROW-1", item_code: "<img src=x>",
		uom: "Nos", s_warehouse: "A", t_warehouse: "B",
	};
	const grid = {
		custom_buttons: {},
		add_custom_button(label, callback) {
			click = callback;
			this.custom_buttons[label] = {
				addClass: () => { hidden = true; },
				removeClass: () => { hidden = false; },
			};
		},
		get_selected_children: () => [row],
	};
	load("stock_entry.js", {
		frappe: {
			user: { has_role: () => true },
			msgprint: (message) => messages.push(message),
			ui: {
				form: { on: (doctype, events) => { handlers[doctype] = events; } },
				Dialog: class {
					constructor() { this.body = {}; }
					show() {}
				},
			},
			utils: { escape_html: (value) => value.replace(/&/g, "&amp;")
				.replace(/</g, "&lt;").replace(/>/g, "&gt;") },
			datetime: { str_to_user: (value) => value },
			call: async (request) => {
				requests.push(request);
				return { message: [{
					stock_entry: "<script>bad</script>", docstatus: 1,
					posting_date: "2026-11-14", posting_time: "07:00:00",
					qty: 2, uom: "Nos", salesman_price: 15,
				}] };
			},
		},
		__: (text) => text,
		format_number: String,
		$: () => ({ html: (value) => {
			html = value;
			return { find: () => ({ on: () => {} }) };
		} }),
	});
	const frm = {
		doc: { name: "STE-1", purpose: "Material Transfer", docstatus: 0 },
		fields_dict: { items: { grid } },
		is_new: () => false,
		is_dirty: () => false,
		has_perm: () => true,
	};
	handlers["Stock Entry"].refresh(frm);
	click();
	await new Promise((resolve) => setImmediate(resolve));
	assert.equal(requests.length, 1);
	assert.equal(requests[0].method,
		"awht_custom.operations.transfer_prices.get_previous_transfer_prices");
	assert.deepEqual(Object.fromEntries(Object.entries(requests[0].args)),
		{ stock_entry: "STE-1", row_name: "ROW-1", limit: 20 });
	assert.match(html, /&lt;img src=x&gt;/);
	assert.match(html, /&lt;script&gt;bad&lt;\/script&gt;/);
	assert.doesNotMatch(html, /<script>|<img src=x>/);
	frm.doc.purpose = "Material Receipt";
	handlers["Stock Entry"].purpose(frm);
	assert.equal(hidden, true);
	click();
	assert.equal(requests.length, 1);
	frm.doc.purpose = "Material Transfer";
	frm.is_dirty = () => true;
	click();
	assert.equal(requests.length, 1);
	assert.match(messages[0], /Save the Material Transfer/);
});

test("out-of-order native tax previews cannot overwrite newer item values", async () => {
	const handlers = {};
	const timers = [];
	const requests = [];
	const pending = [];
	load("sales_invoice.js", {
		frappe: {
			boot: { awht_native_item_wise_tax_details: true },
			ui: { form: { on: (doctype, events) => { handlers[doctype] = events; } } },
			meta: { get_docfield: () => true },
			call: (request) => {
				requests.push(request);
				return new Promise((resolve) => pending.push(resolve));
			},
		},
		setTimeout: (callback) => {
			const timer = { callback, cancelled: false };
			timers.push(timer);
			return timer;
		},
		clearTimeout: (timer) => { if (timer) timer.cancelled = true; },
	});
	const row = { name: "ROW", item_code: "ITEM", rate: 100,
		item_tax_amount: 10, amount_after_tax: 110, net_amount: 100 };
	const frm = {
		doc: { doctype: "Sales Invoice", name: "SI-1", docstatus: 0,
			company: "COMPANY", customer: "CUSTOMER", currency: "SAR",
			items: [row], taxes: [], total_taxes_and_charges: 10 },
		is_new: () => false,
		is_dirty: () => true,
		refresh_field: () => {},
	};
	handlers["Sales Invoice Item"].rate(frm);
	assert.equal(row.item_tax_amount, null);
	assert.equal(row.amount_after_tax, null);
	assert.equal(frm.doc.total_taxes_and_charges, 10);
	const first = timers.shift().callback();
	assert.equal(requests[0].method, "awht_custom.operations.invoice_tax.preview_item_taxes");
	row.rate = 200;
	handlers["Sales Invoice Item"].rate(frm);
	const second = timers.shift().callback();
	pending[1]({ message: [{ name: "ROW", item_tax_amount: 20, amount_after_tax: 220 }] });
	await second;
	assert.equal(row.item_tax_amount, 20);
	pending[0]({ message: [{ name: "ROW", item_tax_amount: 10, amount_after_tax: 110 }] });
	await first;
	assert.equal(row.item_tax_amount, 20);
	assert.equal(frm.doc.total_taxes_and_charges, 10);
	frm.doc.currency = "USD";
	handlers["Sales Invoice"].currency(frm);
	assert.equal(row.item_tax_amount, null);
	const third = timers.shift().callback();
	frm.doc.discount_amount = 5;
	handlers["Sales Invoice"].discount_amount(frm);
	pending[2]({ message: [{ name: "ROW", item_tax_amount: 20, amount_after_tax: 220 }] });
	await third;
	assert.equal(row.item_tax_amount, null);
	const fourth = timers.shift().callback();
	pending[3]({ message: [{ name: "ROW", item_tax_amount: null, amount_after_tax: null }] });
	await fourth;
	assert.equal(row.item_tax_amount, null);
	for (const event of ["taxes_and_charges", "currency", "discount_amount", "additional_discount_percentage", "apply_tds"]) {
		assert.equal(typeof handlers["Sales Invoice"][event], "function");
	}
	assert.equal(typeof handlers["Sales Taxes and Charges"].rate, "function");
	assert.equal(typeof handlers["Sales Taxes and Charges"].taxes_remove, "function");
	const count = requests.length;
	frm.is_dirty = () => false;
	frm.doc.items = [];
	handlers["Sales Invoice Item"].items_remove(frm);
	frm.is_dirty = () => true;
	timers.shift().callback();
	assert.equal(requests.length, count);
});

test("explicit branch mapping fills blanks but never replaces conflicting user warehouses", async () => {
	const handlers = {};
	const timers = [];
	const requests = [];
	const alerts = [];
	let writes = 0;
	load("sales_invoice.js", {
		frappe: {
			ui: { form: { on: (doctype, events) => { handlers[doctype] = events; } } },
			meta: { get_docfield: () => false },
			call: async (request) => { requests.push(request); return { message: "WH-1" }; },
			show_alert: (alert) => alerts.push(alert),
			model: { set_value: async () => { writes++; } },
		},
		__: (text) => text,
		setTimeout: (callback) => { timers.push(callback); return callback; },
		clearTimeout: () => {},
	});
	const row = { doctype: "Sales Invoice Item", name: "ROW", warehouse: "USER" };
	const frm = {
		doc: { name: "SI-1", company: "C1", custom_branch: "B1",
			set_warehouse: "USER", docstatus: 0, is_return: 0, items: [row] },
		is_new: () => false,
		set_value: async (_field, warehouse) => {
			writes++;
			frm.doc.set_warehouse = warehouse;
			// ERPNext's standard header handler fills every item warehouse.
			row.warehouse = warehouse;
		},
	};
	handlers["Sales Invoice"].custom_branch(frm);
	await timers.shift()();
	assert.equal(requests[0].method, "awht_custom.operations.invoice_branch.get_branch_warehouse");
	assert.equal(row.warehouse, "USER");
	assert.equal(frm.doc.set_warehouse, "USER");
	assert.equal(writes, 0);
	assert.equal(alerts[0].indicator, "orange");
	const blankRow = { doctype: "Sales Invoice Item", name: "ROW-2", warehouse: null };
	frm.doc.items.push(blankRow);
	frm.doc.set_warehouse = "WH-1";
	handlers["Sales Invoice"].custom_branch(frm);
	await timers.shift()();
	assert.equal(row.warehouse, "USER");
	assert.equal(blankRow.warehouse, null);
	assert.equal(writes, 0);
	frm.doc.items.pop();
	frm.doc.set_warehouse = null;
	row.warehouse = null;
	handlers["Sales Invoice"].custom_branch(frm);
	await timers.shift()();
	assert.equal(frm.doc.set_warehouse, "WH-1");
	assert.equal(row.warehouse, "WH-1");
	assert.equal(writes, 1);
	frm.doc.is_return = 1;
	handlers["Sales Invoice"].custom_branch(frm);
	assert.equal(timers.length, 0);
	frm.doc.is_return = 0;
	frm.doc.set_warehouse = null;
	row.warehouse = null;
	frm.is_new = () => true;
	handlers["Sales Invoice"].custom_branch(frm);
	await timers.shift()();
	assert.equal(Object.hasOwn(requests.at(-1).args, "invoice"), false);
	frm.is_new = () => false;
	frm.doc.company = "C2";
	frm.doc.set_warehouse = null;
	row.warehouse = null;
	handlers["Sales Invoice"].company(frm);
	await timers.shift()();
	assert.equal(requests.at(-1).args.company, "C2");
	assert.equal(frm.doc.set_warehouse, "WH-1");
});

test("stale branch response cannot fill a branch selected afterwards", async () => {
	const handlers = {};
	const timers = [];
	const requests = [];
	const pending = [];
	load("sales_invoice.js", {
		frappe: {
			ui: { form: { on: (doctype, events) => { handlers[doctype] = events; } } },
			show_alert: () => {},
			call: (request) => {
				requests.push(request);
				return new Promise((resolve) => pending.push(resolve));
			},
		},
		__: (text) => text,
		setTimeout: (callback) => { timers.push(callback); return callback; },
		clearTimeout: () => {},
	});
	const frm = {
		doc: { name: "SI-1", company: "C1", custom_branch: "B1",
			set_warehouse: null, docstatus: 0, is_return: 0, items: [] },
		is_new: () => false,
		set_value: async (_field, warehouse) => { frm.doc.set_warehouse = warehouse; },
	};
	handlers["Sales Invoice"].custom_branch(frm);
	const first = timers.shift()();
	frm.doc.custom_branch = "B2";
	handlers["Sales Invoice"].custom_branch(frm);
	const second = timers.shift()();
	pending[1]({ message: "WH-2" });
	await second;
	pending[0]({ message: "WH-1" });
	await first;
	assert.equal(frm.doc.set_warehouse, "WH-2");
	assert.deepEqual(requests.map((call) => call.args.branch), ["B1", "B2"]);
});
