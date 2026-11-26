const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const MODES = [
	"Cash",
	"Cash AWH",
	"Cash Damam",
	"Cash DCK Showroom",
	"Cash DCK Sinaya",
	"Cash Hashim Van",
	"Cash Head Office",
	"Cash Khamis Mashit",
	"Cash khamis Van",
	"Cash Kifayat Van",
	"Cash MMK Shop",
	"Cash Tanveer Van",
	"Cash Umar Van",
	"Bank Transfer (Damam Van)",
	"Bank Transfer (DCK Sanaya)",
	"Bank Transfer (Dck Showroom)",
	"Bank Transfer (Hashim Van)",
	"Bank Transfer (Head Office)",
	"Bank Transfer (Khamis Mashit)",
	"Bank Transfer (Khamis Van)",
	"Bank Transfer (Kifayat Van)",
	"Bank Transfer (MMK Shop)",
	"Bank Transfer (Tanveer Van)",
	"Bank Transfer (Umar van)",
	"POS Mada DAMMAM VAN",
	"POS Mada DCK SANAYA",
	"POS Mada DCK SHOWROOM",
	"POS Mada HASHAM VAN",
	"POS Mada Head Office",
	"POS Mada KHAMIS MASHIT",
	"POS Mada KHAMIS MASHIT VAN",
	"POS Mada KIFIYAT VAN",
	"POS Mada MMK Shop",
	"POS Mada TANVEER VAN",
	"POS Mada UMAR VAN",
	"TAMARA FINANCE",
];

function load_payment_entry() {
	const handlers = {};
	const context = {
		frappe: {
			ui: {
				form: {
					on: (doctype, events) => {
						handlers[doctype] = { ...(handlers[doctype] || {}), ...events };
					},
				},
			},
			get_meta: () => ({ has_field: () => false }),
			db: {
				get_value: () => {},
				get_list: () => Promise.resolve(MODES.map(name => ({ name }))),
			},
		},
	};
	const file = path.join(__dirname, "..", "public", "js", "legacy_payment_entry.js");
	vm.runInNewContext(fs.readFileSync(file, "utf8"), context, { filename: "legacy_payment_entry.js" });
	return handlers["Payment Entry"];
}

function plain(value) {
	return JSON.parse(JSON.stringify(value));
}

function flush() {
	return new Promise(resolve => setImmediate(resolve));
}

function make_form(doc) {
	const properties = [];
	const values = [];
	const queries = {};
	return {
		properties,
		values,
		queries,
		frm: {
			doc,
			set_df_property: (...args) => properties.push(args),
			set_value: (field, value) => values.push([field, value]),
			set_query: (field, callback) => { queries[field] = { field, result: callback() }; },
		},
	};
}

test("Payment Entry party and mode fields stay disabled until a branch is selected", () => {
	const handlers = load_payment_entry();
	const form = make_form({ docstatus: 0, custom_branch: "", party_type: "Customer", party: "" });
	handlers.refresh(form.frm);

	assert.deepEqual(form.properties, [
		["party_type", "read_only", 1],
		["party", "read_only", 1],
		["mode_of_payment", "read_only", 1],
	]);
	assert.deepEqual(plain(form.queries.party), {
		field: "party",
		result: { filters: { name: ["=", "__none__"] } },
	});
	assert.deepEqual(plain(form.queries.mode_of_payment), {
		field: "mode_of_payment",
		result: { filters: { name: ["=", "__none__"] } },
	});
});

test("Customer and Employee parties are filtered by their document branch field", () => {
	const handlers = load_payment_entry();

	const customer = make_form({ docstatus: 0, custom_branch: "KHAMIS MASHIT", party_type: "Customer", party: "" });
	handlers.refresh(customer.frm);
	assert.deepEqual(plain(customer.queries.party.result), { filters: { custom_branch: "KHAMIS MASHIT" } });
	assert.deepEqual(customer.properties, [
		["party_type", "read_only", 0],
		["party", "read_only", 0],
		["mode_of_payment", "read_only", 0],
	]);

	customer.frm.doc.party_type = "Employee";
	handlers.party_type(customer.frm);
	assert.deepEqual(plain(customer.queries.party.result), {
		query: "erpnext.controllers.queries.employee_query",
		filters: { branch: "KHAMIS MASHIT" },
	});
});

test("changing branch or party type clears selected party and branch-specific mode", () => {
	const handlers = load_payment_entry();
	const form = make_form({
		docstatus: 0, custom_branch: "DAMMAM VAN", party_type: "Customer",
		party: "CUST-1", party_name: "Customer One", mode_of_payment: "Cash Damam",
	});

	handlers.custom_branch(form.frm);
	assert.deepEqual(form.values, [["party", ""], ["party_name", ""], ["mode_of_payment", ""]]);
	assert.deepEqual(plain(form.queries.party.result), { filters: { custom_branch: "DAMMAM VAN" } });

	form.values.length = 0;
	form.frm.doc.party = "CUST-2";
	form.frm.doc.party_type = "Employee";
	handlers.party_type(form.frm);
	assert.deepEqual(form.values, [["party", ""]]);
	assert.deepEqual(plain(form.queries.party.result), {
		query: "erpnext.controllers.queries.employee_query",
		filters: { branch: "DAMMAM VAN" },
	});
});

test("party types without a branch field are not filtered", () => {
	const handlers = load_payment_entry();
	const form = make_form({ docstatus: 0, custom_branch: "DAMMAM VAN", party_type: "Supplier", party: "" });
	handlers.refresh(form.frm);
	assert.deepEqual(plain(form.queries.party.result), {});
});

test("Shareholder keeps the standard company filter", () => {
	const handlers = load_payment_entry();
	const form = make_form({
		docstatus: 0, custom_branch: "DAMMAM VAN", party_type: "Shareholder",
		party: "", company: "AWHT",
	});
	handlers.refresh(form.frm);
	assert.deepEqual(plain(form.queries.party.result), { filters: { company: "AWHT" } });
});

test("branch users see only their own modes of payment", async () => {
	const handlers = load_payment_entry();
	const form = make_form({ docstatus: 0, custom_branch: "DAMMAM VAN", party_type: "Customer", party: "" });
	handlers.refresh(form.frm);
	await flush();

	assert.deepEqual(plain(form.queries.mode_of_payment.result), {
		filters: {
			name: ["in", ["Cash Damam", "Bank Transfer (Damam Van)", "POS Mada DAMMAM VAN"]],
		},
	});
});

test("Khamis van does not receive the parent Khamis modes", async () => {
	const handlers = load_payment_entry();
	const form = make_form({ docstatus: 0, custom_branch: "KHAMIS MASHIT VAN", party_type: "Customer", party: "" });
	handlers.refresh(form.frm);
	await flush();

	const allowed = form.queries.mode_of_payment.result.filters.name[1];
	assert.ok(allowed.includes("Bank Transfer (Khamis Van)"));
	assert.ok(allowed.includes("POS Mada KHAMIS MASHIT VAN"));
	assert.ok(!allowed.includes("Bank Transfer (Khamis Mashit)"));
	assert.ok(!allowed.includes("POS Mada KHAMIS MASHIT"));
});

test("Head Office sees every enabled mode and all parties", async () => {
	const handlers = load_payment_entry();
	const form = make_form({ docstatus: 0, custom_branch: "Head Office", party_type: "Customer", party: "" });
	handlers.refresh(form.frm);
	await flush();

	assert.deepEqual(plain(form.queries.party.result), {});
	assert.deepEqual(plain(form.queries.mode_of_payment.result), {});
	assert.deepEqual(form.properties, [
		["party_type", "read_only", 0],
		["party", "read_only", 0],
		["mode_of_payment", "read_only", 0],
	]);
});

test("submitted Payment Entry fields are never unlocked", () => {
	const handlers = load_payment_entry();
	const form = make_form({ docstatus: 1, custom_branch: "DAMMAM VAN", party_type: "Customer", party: "CUST-1" });
	handlers.refresh(form.frm);
	assert.deepEqual(form.properties, []);
	assert.deepEqual(form.queries, {});
});
