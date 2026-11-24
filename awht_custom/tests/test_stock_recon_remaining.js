const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const file = path.join(__dirname, "..", "public", "js", "stock_reconciliation_combined.js");
const handlers = {};
let button;
let dialog;
let call;
let alert;

const frm = {
    doc: {
        docstatus: 0,
        company: "AWH",
        posting_date: "2026-11-24",
        posting_time: "12:00:00",
        set_warehouse: "WH-1",
        items: [
            { item_code: "EXISTING", warehouse: "WH-1", batch_no: "" },
            { name: "ROW-BLANK", item_code: "", warehouse: "", batch_no: "" },
        ],
    },
    add_custom_button: (label, callback) => { button = callback; },
    get_field: () => ({ grid: { grid_rows_by_docname: {} } }),
    add_child: () => {
        const row = { name: `ROW-${frm.doc.items.length + 1}` };
        frm.doc.items.push(row);
        return row;
    },
    refresh_field: () => {},
    dirty: () => {},
};

const context = {
    console,
    setTimeout,
    clearTimeout,
    locals: {},
    flt: (value) => Number(value) || 0,
    cint: (value) => Number(value) || 0,
    __: (text) => text,
    frappe: {
        ui: {
            form: { on(doctype, eventHandlers) { handlers[doctype] = eventHandlers; } },
            Dialog: class {
                constructor(options) { this.options = options; dialog = this; }
                set_secondary_action_label() {}
                set_secondary_action() {}
                show() { this.shown = true; }
                hide() { this.hidden = true; }
            },
        },
        datetime: {
            get_today: () => "2026-11-24",
            str_to_obj: (value) => new Date(value),
        },
        call(request) {
            call = request;
            request.callback({
                message: {
                    items: [
                        { item_code: "NEW", warehouse: "WH-1", batch_no: "", qty: 7 },
                        { item_code: "EXISTING", warehouse: "WH-1", batch_no: "", qty: 3 },
                    ],
                    reconciled_count: 2,
                    total_count: 2,
                },
            });
        },
        show_alert: (payload) => { alert = payload; },
        user_defaults: {},
        utils: { escape_html: (value) => String(value) },
    },
};
context.window = context;

vm.runInNewContext(fs.readFileSync(file, "utf8"), context, { filename: "stock_reconciliation_combined.js" });

handlers["Stock Reconciliation"].refresh(frm);
assert.equal(typeof button, "function");
button();
assert.ok(dialog && dialog.shown);
dialog.options.primary_action({
    warehouse: "WH-1",
    from_date: "2026-11-01",
    to_date: "2026-11-24",
    ignore_empty_stock: 1,
});
assert.equal(call.method, "awht_custom.stock_recon.api.get_remaining_items");
assert.equal(call.type, "POST");
assert.equal(call.args.warehouse, "WH-1");
assert.equal(call.args.company, "AWH");
assert.equal(call.args.from_date, "2026-11-01");
assert.equal(call.args.ignore_empty_stock, 1);
assert.equal(frm.doc.items.length, 2, "blank row is removed, existing row is kept and NEW is added");
assert.ok(frm.doc.items.every((row) => row.item_code));
assert.equal(frm.doc.items[1].item_code, "NEW");
assert.equal(frm.doc.items[1].qty, 7);
assert.equal(frm.doc.items[1].amount, 0);
assert.match(alert.message, /Added/);
console.log("stock reconciliation remaining-items test: PASS");
