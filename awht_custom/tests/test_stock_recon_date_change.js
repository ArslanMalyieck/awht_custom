const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const file = path.join(__dirname, "..", "public", "js", "stock_reconciliation_combined.js");
const handlers = {};
const row = {
    doctype: "Stock Reconciliation Item",
    name: "ROW-1",
    item_code: "ITEM-1",
    warehouse: "WH-1",
    qty: 30,
    current_qty: 10,
    valuation_rate: 2,
};
const calls = [];
const set_values = [];
const frm = {
    doc: { docstatus: 0, purpose: "Stock Reconciliation", items: [row] },
    events: {
        set_valuation_rate_and_qty_for_all_items(form) {
            form.doc.items.forEach((item) => form.events.set_valuation_rate_and_qty(form, item.doctype, item.name));
        },
        set_valuation_rate_and_qty() {
            // Simulates ERPNext's original behaviour that overwrites qty.
            frappe.model.set_value("Stock Reconciliation Item", "ROW-1", "qty", 10);
        },
    },
    add_custom_button: () => {},
};
const context = {
    console,
    setTimeout,
    clearTimeout,
    locals: { "Stock Reconciliation Item": { "ROW-1": row } },
    flt: (value) => Number(value) || 0,
    cint: (value) => Number(value) || 0,
    __: (text) => text,
    frappe: {
        ui: {
            form: { on(doctype, eventHandlers) { handlers[doctype] = eventHandlers; } },
        },
        model: {
            get_doc: (cdt, cdn) => context.locals[cdt][cdn],
            set_value(cdt, cdn, fieldname, value) {
                set_values.push([cdn, fieldname, value]);
                context.locals[cdt][cdn][fieldname] = value;
                return Promise.resolve();
            },
        },
        call(request) {
            calls.push(request);
            request.callback({ message: { rate: 5, qty: 100, serial_nos: "" } });
        },
        show_alert: () => {},
        utils: { escape_html: (value) => String(value) },
        datetime: {},
    },
};
context.window = context;
vm.runInNewContext(fs.readFileSync(file, "utf8"), context, { filename: "stock_reconciliation_combined.js" });

handlers["Stock Reconciliation"].onload(frm);
assert.ok(frm.__awht_qty_preserve_patch);
assert.notEqual(frm.events.set_valuation_rate_and_qty_for_all_items.toString(), "function () {}");
frm.events.set_valuation_rate_and_qty_for_all_items(frm);
assert.equal(row.qty, 30, "date/time refresh must preserve the entered qty");
assert.equal(row.current_qty, 100, "new current stock is still refreshed");
assert.equal(calls.length, 1);
assert.equal(calls[0].method, "erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_stock_balance_for");
assert.ok(set_values.some(([, field, value]) => field === "current_qty" && value === 100));
assert.ok(!set_values.some(([, field, value]) => field === "qty" && value === 10));
console.log("stock reconciliation date-change quantity preservation test: PASS");
