/* Ported live list-view Client Scripts. */


/* Ported Client Script: Custom Zatca Status */
(() => {
frappe.listview_settings['Sales Invoice'] = {
    refresh: function(listview) {
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Sales Invoice Additional Fields",
                fields: ["sales_invoice", "integration_status"],
                limit_page_length: 100000
            },
            callback: function(response) {
                let status_data = {};

                // Store the integration_status for each sales_invoice
                response.message.forEach(function(record) {
                    status_data[record.sales_invoice] = record.integration_status;
                });

                // Check if the Integration Status column exists, if not, add it
                if (listview.$result.find('.list-row-head .list-row-col:contains("Zatca Status")').length === 0) {
                    listview.$result.find('.list-row-head .list-row-col:last').each(function() {
                        $('<div class="list-row-col ellipsis hidden-xs"><span>Zatca Status</span></div>').insertAfter($(this));
                    });
                }

                // Iterate through each row in the Sales Invoice list
                listview.$result.find('.list-row-container .list-row').each(function() {
                    let $row = $(this);

                    let invoice_id = $row.find('.list-row-col:last').text().trim();

                    let integration_status = status_data[invoice_id] || 'N/A';

                    // Define badge classes based on the integration status
                    let badge_class = 'gray'; // Default class for unknown status
                    let badge_text = integration_status; // Default text
                    

                    // Assign specific badge classes for each status value
                    switch (integration_status) {
                        case 'Accepted':
                            badge_class = 'green'; 
                            break;
                        case 'Ready For Batch':
                            badge_class = 'gray';
                            break;
                        case 'Resend':
                            badge_class = 'yellow';
                            break;
                        case 'Corrected':
                            badge_class = 'gray';
                            break;
                        case 'Accepted with warnings':
                            badge_class = 'blue';
                            break;
                        case 'Rejected':
                            badge_class = 'red';
                            break;
                        case 'Clearance switched off':
                            badge_class = 'orange';
                            break;
                        default:
                            badge_class = 'gray';
                            break;
                    }

                    // Check if the Integration Status column exists, if not add it
                    if ($row.find('.list-row-col:contains("Zatca Status")').length === 0) {
                        let $status_col = $(`<div class="list-row-col hidden-xs ellipsis">
					        <span class="indicator-pill ${badge_class} filterable ellipsis"  title="Zatca Status: ${integration_status}">
				                <span class="ellipsis"> ${integration_status}</span>
			                </span>
				        </div>`);
                        
                        $status_col.insertAfter($row.find('.list-row-col:last'));
                    }
                });
            }
        });
    }
};

})();
