(function (root) {
    "use strict";

    function normalize(value) {
        return (value || "")
            .toLowerCase()
            .replace(/\s+/g, "")
            .replace(/-/g, "");
    }

    function item_branch_filters(branch) {
        const branch_norm = normalize(branch);
        const is_head_office = branch_norm.includes("headoffice");
        const is_khamis = branch_norm.includes("khamismashit");
        const filters = {};

        if (!is_head_office) {
            filters.custom_is_head_office_only = 0;
        }
        if (!is_khamis) {
            filters.custom_is_khamis_only = 0;
        }
        return filters;
    }

    root.awht_custom_item_branch_filters = item_branch_filters;
})(typeof window === "undefined" ? globalThis : window);
