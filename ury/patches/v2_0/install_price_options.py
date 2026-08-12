import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    pos_invoice_item_fields = [
        {
            "fieldname": "custom_ury_price_option",
            "label": "URY Price Option",
            "fieldtype": "Data",
            "insert_after": "custom_course",
            "hidden": 1,
            "read_only": 1,
            "search_index": 1,
            "module": "URY",
        },
        {
            "fieldname": "custom_ury_price_option_label",
            "label": "Price Option",
            "fieldtype": "Data",
            "insert_after": "custom_ury_price_option",
            "read_only": 1,
            "print_hide_if_no_value": 1,
            "module": "URY",
        },
    ]
    sales_invoice_item_fields = [
        {key: value for key, value in field.items() if key != "search_index"}
        for field in pos_invoice_item_fields
    ]
    create_custom_fields(
        {
            "POS Invoice Item": pos_invoice_item_fields,
            "Sales Invoice Item": sales_invoice_item_fields,
        },
        update=True,
    )
    frappe.clear_cache(doctype="POS Invoice Item")
    frappe.clear_cache(doctype="Sales Invoice Item")
