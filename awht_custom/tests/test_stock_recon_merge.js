const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const file = path.join(__dirname, "..", "public", "js", "stock_reconciliation_combined.js");
const handlers = {};
let dialog;
let merged;
let removed;
let alert;

const previous = {
    doctype: "Stock Reconciliation Item",
    name: "ROW-OLD",
    idx: 1,
    item_code: "ITEM-1",
    warehouse: "WH-1",
    batch_no: "",
    qty: 30,
    current_qty: 30,
};
const current = {
    doctype: "Stock Reconciliation Item",
    name: "ROW-NEW",
    idx: 2,
    item_code: "ITEM-1",
    warehouse: "WH-1",
    batch_no: "",
    qty: 10,
    current_qty: 0,
};

const frm = {
    doc: { docstatus: 0, items: [previous, current] },
    get_field: () => ({
        grid: {
            grid_rows_by_docname: {
                "ROW-NEW": { remove: () => { removed = true; } },
            },
        },
    }),
    refresh_field: () => {},
    dirty: () => {},
};

const context = {
    console,
    setTimeout,
    clearTimeout,
    locals: { "Stock Reconciliation Item": { "ROW-OLD": previous, "ROW-NEW": current } },
    flt: (value) => Number(value) || 0,
    cint: (value) => Number(value) || 0,
    __: (text) => text,
    frappe: {
        ui: {
            form: {
                on(doctype, eventHandlers) {
                    handlers[doctype] = eventHandlers;
                },
            },
            Dialog: class {
                constructor(options) {
                    this.options = options;
                    this.primary_action = options.primary_action;
                    dialog = this;
                }
                set_secondary_action_label(label) { this.secondary_label = label; }
                set_secondary_action(callback) { this.secondary_action = callback; }
                show() { this.shown = true; }
                hide() { this.hidden = true; if (this.onhide) this.onhide(); }
            },
        },
        datetime: {
            get_today: () => "2026-11-24",
            str_to_obj: (value) => new Date(value),
        },
        model: {
            set_value(doctype, name, fieldname, value) {
                merged = { doctype, name, fieldname, value };
                const row = context.locals[doctype][name];
                row[fieldname] = value;
                return Promise.resolve();
            },
        },
        show_alert: (payload) => { alert = payload; },
        call: () => {},
        user_defaults: {},
        utils: { escape_html: (value) => String(value) },
    },
};
context.window = context;

vm.runInNewContext(fs.readFileSync(file, "utf8"), context, { filename: "stock_reconciliation_combined.js" });

(async () => {
    assert.ok(handlers["Stock Reconciliation"]);
    assert.ok(handlers["Stock Reconciliation Item"]);
    handlers["Stock Reconciliation Item"].qty(frm, "Stock Reconciliation Item", "ROW-NEW");
    await new Promise((resolve) => setTimeout(resolve, 450));
    assert.ok(dialog && dialog.shown, "duplicate dialog should open");
    assert.match(dialog.options.fields[0].options, /row <strong>#1/);
    assert.match(dialog.options.fields[0].options, /already listed/);
    dialog.primary_action();
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(previous.qty, 40);
    assert.equal(merged.name, "ROW-OLD");
    assert.equal(removed, true, "new duplicate row should be removed");
    assert.match(alert.message, /Merged into row/);
    console.log("stock reconciliation merge test: PASS");
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
