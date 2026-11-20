// Run with node awht_custom/tests/pdf_export_branded_report.js (no browser or site).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const asset = fs.readFileSync(path.join(__dirname, "../public/js/branded_report.js"), "utf8");
const reports = {
	"General Ledger": "GL Entry",
	"Accounts Receivable": "Sales Invoice",
	"Accounts Payable": "Purchase Invoice",
	"Accounts Receivable Summary": "Sales Invoice",
	"Accounts Payable Summary": "Purchase Invoice",
	"Sales Register": "Sales Invoice",
};
let route;
let canReport = true;
let canPrint = true;
let serverAllows = true;
let dialog;
const calls = [];
const posts = [];

class Element {
	constructor(tag) {
		this.tag = tag;
		this.style = {};
		this.children = [];
	}
	appendChild(child) {
		this.children.push(child);
	}
	addEventListener(event, callback) {
		this[event] = callback;
	}
}

class QueryReport {
	constructor(name, doctype, type = "Script Report") {
		this.report_name = name;
		this.report_doc = {
			name,
			ref_doctype: doctype,
			module: "Accounts",
			is_standard: "Yes",
			report_type: type,
			prepared_report: 0,
			disabled: 0,
		};
		this.report_settings = { onload: () => this.onloadCount++ };
		this.onloadCount = 0;
		this.buttons = [];
		this.page = { add_inner_button: (name, action) => this.buttons.push({ name, action }) };
		this.columns = [
			{ fieldname: "shown", fieldtype: "Currency", label: '<img src="x" onerror="bad()">' },
			{ fieldname: "secret", label: "Secret", hidden: 1 },
		];
	}
	refresh_report() {
		this.report_settings.onload(this);
	}
	get_filter_values() {
		return { company: "Acme" };
	}
}

class ReportView {
	constructor(doctype) {
		this.doctype = doctype;
		this.report_doc = null;
		this.report_name = null;
		this.fields = [["name", doctype], ["amount", doctype]];
		this.columns = [
			{ id: "name", content: "ID", docfield: { fieldtype: "Data" } },
			{ id: "amount", content: '<img src="x" onerror="bad()">',
				docfield: { fieldtype: "Currency" } },
		];
		this.data = [
			{ name: "INV-1", amount: "BROWSER ROW VALUE" },
			{ name: "INV-2", amount: "DO NOT TRUST" },
		];
		this.add_totals_row = 0;
		this.buttons = [];
		this.page = { add_inner_button: (name, action) => {
			this.buttons.push({ name, action });
			return { addClass() {} };
		} };
		this.datatable = {
			datamanager: { rowViewOrder: [1, 0] },
			bodyRenderer: { visibleRowIndices: [0, 1] },
		};
	}
	setup_result_area() { this.setupCount = (this.setupCount || 0) + 1; }
	get_args() {
		return { filters: [[this.doctype, "company", "=", "Acme"]], order_by: "creation desc" };
	}
	get_checked_items() { return ["INV-2"]; }
}
const reportViewSetup = ReportView.prototype.setup_result_area;
const context = {
	frappe: {
		views: { QueryReport, ReportView },
		model: {
			can_read: () => true,
			can_get_report: () => canReport,
			can_print: () => canPrint,
		},
		get_route: () => ["query-report", route],
		xcall: (method, args) => {
			calls.push({ method, args });
			return Promise.resolve(serverAllows);
		},
		ui: {
			Dialog: class {
				constructor(config) {
					this.config = config;
					this.fields_dict = { columns: { $wrapper: {
							empty() { return this; },
							append(node) { this.node = node; },
						} } };
					dialog = this;
				}
				show() { this.shown = true; }
				hide() { this.hidden = true; }
			},
		},
		msgprint: (message) => { throw new Error(message); },
	},
	document: {
		createElement: (tag) => new Element(tag),
		createTextNode: (text) => ({ tag: "#text", textContent: text }),
	},
	open_url_post: (url, args, newWindow) => posts.push({ url, args, newWindow }),
	__: (value) => value,
	setTimeout: () => { throw new Error("QueryReport patch must be installed at load"); },
	console,
};
vm.runInNewContext(asset, context);
assert.notEqual(ReportView.prototype.setup_result_area, reportViewSetup,
	"ReportView must use server-side re-fetch for its branded PDF button");

async function run() {
	for (const [name, doctype] of Object.entries(reports)) {
		route = name;
		const report = new QueryReport(name, doctype);
		report.refresh_report();
		await new Promise(setImmediate);
		assert.equal(report.onloadCount, 1, "existing onload must be preserved");
		assert.equal(report.buttons.length, 1, name);
		assert.equal(calls.at(-1).method,
			"awht_custom.pdf_export.report_pdf.can_download_report_pdf");
	}

	let report = new QueryReport("Stock Balance", "Bin", "Query Report");
	route = report.report_name;
	const previousCalls = calls.length;
	report.refresh_report();
	await new Promise(setImmediate);
	assert.equal(report.buttons.length, 1, "non-accounting Query Reports are supported");
	assert.equal(calls.length, previousCalls + 1);
	report = new QueryReport("Custom Balance", "Sales Invoice", "Custom Report");
	route = report.report_name;
	report.refresh_report();
	await new Promise(setImmediate);
	assert.equal(report.buttons.length, 1, "Custom Reports are supported");
	report = new QueryReport("Report Builder", "Sales Invoice", "Report Builder");
	route = report.report_name;
	report.refresh_report();
	await new Promise(setImmediate);
	assert.equal(report.buttons.length, 0, "Report Builder uses the separate ReportView endpoint");

	route = "General Ledger";
	canPrint = false;
	report = new QueryReport(route, "GL Entry");
	report.refresh_report();
	await new Promise(setImmediate);
	assert.equal(report.buttons.length, 0);
	canPrint = true;
	canReport = false;
	report = new QueryReport(route, "GL Entry");
	report.refresh_report();
	await new Promise(setImmediate);
	assert.equal(report.buttons.length, 0);
	canReport = true;
	serverAllows = false;
	report = new QueryReport(route, "GL Entry");
	report.refresh_report();
	await new Promise(setImmediate);
	assert.equal(report.buttons.length, 0, "Report role denial from server hides button");
	serverAllows = true;

	report = new QueryReport(route, "GL Entry");
	report.refresh_report();
	await new Promise(setImmediate);
	assert.equal(report.buttons.length, 1);
	report.buttons[0].action();
	assert.ok(dialog.shown);
	const wrapper = dialog.fields_dict.columns.$wrapper.node;
	const labels = wrapper.children.filter((child) => child.tag === "label");
	assert.equal(labels.length, 1, "hidden columns are not offered");
	assert.equal(labels[0].children[1].textContent, ' <img src="x" onerror="bad()">');
	assert.equal(labels[0].children[1].tag, "#text", "column label must be a text node");
	const words = dialog.config.fields.find((field) => field.fieldname === "words_column");
	assert.equal(words.default, "__auto__");
	assert.ok(words.options[2].label.includes("&lt;img"), "Select options must escape labels");
	dialog.config.primary_action({ orientation: "Landscape", no_letter_head: true });
	assert.equal(posts.length, 1);
	assert.equal(posts[0].url,
		"/api/method/awht_custom.pdf_export.report_pdf.download_report_pdf");
	assert.equal(posts[0].newWindow, true);
	assert.equal(posts[0].args.only_fields, '["shown"]');
	assert.equal(posts[0].args.letter_head, "__none__");
	assert.deepEqual(Object.keys(posts[0].args).sort(),
		["filters", "letter_head", "only_fields", "orientation", "report_name",
			"words_column", "words_mode"]);
	assert.ok(dialog.hidden);

	route = "Sales Invoice";
	const view = new ReportView(route);
	view.setup_result_area();
	await new Promise(setImmediate);
	assert.equal(view.setupCount, 1, "original ReportView method must still run");
	assert.equal(view.buttons.length, 1);
	assert.equal(calls.at(-1).method,
		"awht_custom.pdf_export.report_view.can_download_report_view_pdf");
	view.buttons[0].action();
	const viewLabels = dialog.fields_dict.columns.$wrapper.node.children
		.filter((child) => child.tag === "label");
	assert.equal(viewLabels.length, 2);
	assert.equal(viewLabels[1].children[1].tag, "#text");
	dialog.config.primary_action({ orientation: "Landscape", no_letter_head: true });
	assert.equal(posts[1].url,
		"/api/method/awht_custom.pdf_export.report_view.download_report_view_pdf");
	assert.equal(posts[1].args.visible_names, '["INV-2","INV-1"]');
	assert.equal(posts[1].args.selected_items, '["INV-2"]');
	assert.equal(posts[1].args.only_fields, '["name","amount"]');
	assert.ok(!JSON.stringify(posts[1].args).includes("BROWSER ROW VALUE"));
	assert.ok(!Object.keys(posts[1].args).some((key) => ["rows", "html", "columns", "data"].includes(key)));
	console.log("branded PDF UI tests passed");
}

run().catch((error) => {
	console.error(error);
	process.exitCode = 1;
});
