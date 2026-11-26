const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const js_dir = path.join(__dirname, "..", "public", "js");

function load_item_branch_filters() {
	const context = {};
	vm.runInNewContext(
		fs.readFileSync(path.join(js_dir, "branch_item_visibility.js"), "utf8"),
		context,
		{ filename: "branch_item_visibility.js" }
	);
	return context.awht_custom_item_branch_filters;
}

function allows(item, branch, filters) {
	return Object.entries(filters(branch)).every(([field, value]) => {
		return Number(item[field] || 0) === value;
	});
}

test("Head Office branch only hides Khamis-only items", () => {
	const filters = load_item_branch_filters();
	assert.deepEqual({ ...filters("Head Office") }, { custom_is_khamis_only: 0 });
	assert.deepEqual({ ...filters("HEAD-OFFICE") }, { custom_is_khamis_only: 0 });
});

test("Khamis Mashit branches only hide Head Office-only items", () => {
	const filters = load_item_branch_filters();
	assert.deepEqual({ ...filters("KHAMIS MASHIT") }, { custom_is_head_office_only: 0 });
	assert.deepEqual({ ...filters("KHAMIS MASHIT VAN") }, { custom_is_head_office_only: 0 });
	assert.deepEqual({ ...filters("Khamis-Mashit") }, { custom_is_head_office_only: 0 });
});

test("other and unselected branches hide both restricted item types", () => {
	const filters = load_item_branch_filters();
	const expected = { custom_is_head_office_only: 0, custom_is_khamis_only: 0 };
	for (const branch of ["DAMMAM VAN", "MMK SHOP", "WAREHOUSE", ""]) {
		assert.deepEqual({ ...filters(branch) }, expected);
	}
});

test("restricted items are visible only in their own branch", () => {
	const filters = load_item_branch_filters();
	const head_office_item = { custom_is_head_office_only: 1, custom_is_khamis_only: 0 };
	const khamis_item = { custom_is_head_office_only: 0, custom_is_khamis_only: 1 };
	const both = { custom_is_head_office_only: 1, custom_is_khamis_only: 1 };

	assert.equal(allows(head_office_item, "Head Office", filters), true);
	assert.equal(allows(head_office_item, "KHAMIS MASHIT", filters), false);
	assert.equal(allows(head_office_item, "DAMMAM VAN", filters), false);

	assert.equal(allows(khamis_item, "Head Office", filters), false);
	assert.equal(allows(khamis_item, "KHAMIS MASHIT VAN", filters), true);
	assert.equal(allows(khamis_item, "DAMMAM VAN", filters), false);

	assert.equal(allows(both, "Head Office", filters), false);
	assert.equal(allows(both, "KHAMIS MASHIT", filters), false);
});

test("sales documents filter the selected document branch, not user permissions", () => {
	for (const file of ["sales_invoice_combined.js", "legacy_pos_invoice.js", "legacy_quotation.js"]) {
		const source = fs.readFileSync(path.join(js_dir, file), "utf8");
		assert.match(source, /awht_custom_item_branch_filters\(frm\.doc\.custom_branch\)/,
			`${file} must filter items by the document branch`);
		assert.doesNotMatch(source, /User has no branch restrictions = show all items/,
			`${file} must not derive item visibility from user permissions`);
	}
});
