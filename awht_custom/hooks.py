app_name = "awht_custom"
app_title = "AWHT Custom"
app_publisher = "AWHT"
app_description = "Consolidated AWHT operations for ERPNext"
app_email = "admin@example.invalid"
app_license = "MIT"

required_apps = ["erpnext"]

app_include_js = [
    "/assets/awht_custom/js/cost_visibility.js",
    "/assets/awht_custom/js/branded_report.js",
    "/assets/awht_custom/js/legacy_listview.js",
]

doctype_js = {
    "Stock Entry": "public/js/stock_entry_combined.js",
    "Material Request": "public/js/material_request_combined.js",
    "Customer": "public/js/customer.js",
    "Sales Invoice": "public/js/sales_invoice_combined.js",
    "BOM": "public/js/legacy_bom.js",
    "Delivery Note": "public/js/legacy_delivery_note.js",
    "Expense Claim": "public/js/legacy_expense_claim.js",
    "POS Invoice": "public/js/legacy_pos_invoice.js",
    "Payment Entry": "public/js/legacy_payment_entry.js",
    "Purchase Invoice": "public/js/legacy_purchase_invoice.js",
    "Purchase Order": "public/js/legacy_purchase_order.js",
    "Purchase Receipt": "public/js/legacy_purchase_receipt.js",
    "Quotation": "public/js/legacy_quotation.js",
    "Request for Quotation": "public/js/legacy_request_for_quotation.js",
    "Sales Order": "public/js/legacy_sales_order.js",
    "Stock Reconciliation": "public/js/legacy_stock_reconciliation.js",
    "Supplier Quotation": "public/js/legacy_supplier_quotation.js",
}

doc_events = {
    "Stock Entry": {
        "before_submit": "awht_custom.credit_control.stock_entry.validate_stock_entry",
    },
    "Material Request": {
        "before_submit": "awht_custom.credit_control.material_request.validate_material_request",
    },
    "Sales Invoice": {
        "before_validate": "awht_custom.credit_control.invoice.map_invoice_branch_warehouse",
        "validate": "awht_custom.operations.invoice_tax.set_item_tax_display",
        "onload": "awht_custom.operations.invoice_tax.set_item_tax_display",
    },
}

extend_bootinfo = "awht_custom.visibility.api.extend_bootinfo"
before_request = ["awht_custom.visibility.file_filter.before_request"]
pdf_body_html = "awht_custom.visibility.print_filter.pdf_body_html"

extend_doctype_class = {
    "File": ["awht_custom.visibility.file_filter.PreparedReportFileMixin"],
}
has_permission = {
    "File": "awht_custom.visibility.file_filter.has_file_permission",
}

override_whitelisted_methods = {
    "frappe.desk.query_report.run": "awht_custom.visibility.report_filter.run",
    "frappe.desk.query_report.export_query": "awht_custom.visibility.report_filter.export_query",
    "frappe.desk.reportview.get": "awht_custom.visibility.report_builder.get",
    "frappe.desk.reportview.export_query": "awht_custom.visibility.report_builder.export_query",
    "frappe.core.doctype.prepared_report.prepared_report.download_attachment":
        "awht_custom.visibility.report_filter.download_attachment",
    "frappe.core.doctype.prepared_report.prepared_report.enqueue_json_to_csv_conversion":
        "awht_custom.visibility.report_filter.enqueue_json_to_csv_conversion",
    "frappe.core.api.file.zip_files": "awht_custom.visibility.file_filter.zip_files",
    "frappe.core.doctype.file.file.zip_files": "awht_custom.visibility.file_filter.zip_files",
}

after_install = [
    "awht_custom.install.setup",
    "awht_custom.install.sync_ported_reports",
]
after_migrate = [
    "awht_custom.install.setup",
    "awht_custom.install.sync_ported_reports",
]
