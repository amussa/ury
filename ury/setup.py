import frappe
import os
import click
from frappe import _

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def after_install():
	add_custom_fields()
	# Patches are marked as executed before ``after_install`` on a fresh site,
	# so programmatic print formats must also be installed here.
	install_commercial_checkout_runtime_artifacts()


def after_migrate():
	"""Reapply runtime settings after fixtures have been synchronised."""
	install_commercial_checkout_runtime_artifacts()


def install_commercial_checkout_runtime_artifacts():
	"""Install canonical print formats and settings that fixtures may overwrite."""
	from ury.ury.printing.pos_closing_format import install_pos_closing_print_format
	from ury.ury.printing.pos_receipt_format import install_pos_receipt_formats

	# The historical fixture still contains ``no_copy = 0``.  Migrations import
	# fixtures after patches, so enforce the safe mapper setting in after_migrate
	# rather than modifying generated fixture JSON by hand.
	if frappe.db.exists("Custom Field", "POS Invoice-custom_split_from"):
		frappe.db.set_value(
			"Custom Field",
			"POS Invoice-custom_split_from",
			"no_copy",
			1,
			update_modified=False,
		)

	install_pos_closing_print_format()
	install_pos_receipt_formats()
	for doctype in (
		"POS Profile",
		"POS Invoice",
		"POS Invoice Item",
		"Sales Invoice",
		"POS Closing Entry",
	):
		frappe.clear_cache(doctype=doctype)


def add_custom_fields():
	"""Create or update all URY-owned custom fields idempotently."""
	create_custom_fields(get_custom_fields(), update=True)
    
def before_uninstall():
	delete_custom_fields(get_custom_fields())
 
def get_custom_fields():
	"""URY specific custom fields that need to be added to the masters in ERPNext"""
	return {
     	"POS Invoice": [
				{
					"fieldname": "mobile_number",
					"fieldtype": "Data",
					"fetch_from": "customer.mobile_number",
					"label": "Mobile Number",
					"insert_after": "customer_name",
					"translatable": 0,
				},
				{
					"fieldname": "order_info",
					"fieldtype": "Section Break",
					"label": "Order Info",
					"insert_after": "return_against",
				},
				{
					"fieldname": "order_type",
					"fieldtype": "Select",
					"default": "Dine In",
					"label": "Order Type",
					"options": "\nDine In\nTake Away\nDelivery\nPhone In\nAggregators",
					"insert_after": "order_info",
					"translatable": 0
				},
				{
					"fieldname": "waiter",
					"fieldtype": "Data",
					"label": "Waiter",
					"read_only": 0,
					"insert_after": "order_type",
					"translatable": 0
				},
				{
					"fieldname": "column_break_rwbwf",
					"fieldtype": "Column Break",
					"insert_after": "waiter"
				},
				{
					"fieldname": "no_of_pax",
					"fieldtype": "Data",
					"label": "Pax",
					"insert_after": "column_break_rwbwf",
					"read_only": 0,
					"translatable": 0
				},
				{
					"fieldname": "cashier",
					"fieldtype": "Data",
					"label": "Cashier",
					"insert_after": "no_of_pax",
					"read_only": 0,
					"translatable": 0
				},
				{
					"fieldname": "invoice_printed",
					"fieldtype": "Check",
					"label": "Invoice Printed",
					"insert_after": "cashier",
					"read_only": 1,
				},
				{
					"fieldname": "invoice_created",
					"fieldtype": "Check",
					"label": "Invoice Created",
					"insert_after": "invoice_printed",
					"read_only": 0,
					"hidden": 1,
				},
				{
					"fieldname": "restaurant_info",
					"fieldtype": "Section Break",
					"label": "Restaurant Info",
					"insert_after": "invoice_created",
				},
				{
					"fieldname": "restaurant",
					"fieldtype": "Link",
					"insert_after": "restaurant_info",
					"label": "Restaurant",
					"options": "URY Restaurant",
					"read_only": 0,
				},
				{
					"fieldname": "branch",
					"fieldtype": "Link",
					"insert_after": "restaurant",
					"label": "Branch",
					"options": "Branch",
					"read_only": 0,
				},
				{
					"fieldname": "restaurant_table",
					"fieldtype": "Link",
					"insert_after": "branch",
					"label": "Restaurant Table",
					"options": "URY Table",
					"read_only": 0,
				},
				{
					"fieldname": "custom_merged_tables",
					"fieldtype": "Data",
					"insert_after": "restaurant_table",
					"label": "Merged Tables",
					"read_only": 1,
				},
				{
					"fieldname": "custom_split_from",
					"fieldtype": "Link",
					"insert_after": "custom_merged_tables",
					"label": "Split From",
					"options": "POS Invoice",
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "custom_split_group",
					"fieldtype": "Data",
					"insert_after": "custom_split_from",
					"label": "Split Group",
					"read_only": 1,
				},
				{
					"fieldname": "column_break_gd1mq",
					"fieldtype": "Column Break",
					"insert_after": "restaurant_table",
				},
				{
					"fieldname": "arrived_time",
					"fieldtype": "Data",
					"insert_after": "column_break_gd1mq",
					"label": "Arrived Time"
				},
				{
					"fieldname": "total_spend_time",
					"fieldtype": "Data",
					"insert_after": "arrived_time",
					"label": "Total Spend Time"
				},
				{
					"fieldname": "custom_ury_settlement_section",
					"fieldtype": "Section Break",
					"label": "URY Settlement",
					"insert_after": "total_spend_time",
					"collapsible": 1,
					"module": "URY",
				},
				{
					"fieldname": "custom_ury_settlement",
					"fieldtype": "Link",
					"label": "URY POS Settlement",
					"options": "URY POS Settlement",
					"insert_after": "custom_ury_settlement_section",
					"read_only": 1,
					"no_copy": 1,
					"search_index": 1,
					"module": "URY",
				},
				{
					"fieldname": "custom_ury_settlement_type",
					"fieldtype": "Select",
					"label": "Settlement Type",
					"options": "\nPaid\nPartial Credit\nFull Credit\nHouse Offer",
					"insert_after": "custom_ury_settlement",
					"read_only": 1,
					"no_copy": 1,
					"module": "URY",
				},
				{
					"fieldname": "custom_ury_credit_amount",
					"fieldtype": "Currency",
					"label": "Credit Amount",
					"insert_after": "custom_ury_settlement_type",
					"read_only": 1,
					"no_copy": 1,
					"module": "URY",
				},
				{
					"fieldname": "custom_ury_credit_due_date",
					"fieldtype": "Date",
					"label": "Credit Due Date",
					"insert_after": "custom_ury_credit_amount",
					"read_only": 1,
					"no_copy": 1,
					"module": "URY",
				},
				{
					"fieldname": "custom_ury_manual_discount_total",
					"fieldtype": "Currency",
					"label": "Manual Discount Total",
					"insert_after": "custom_ury_credit_due_date",
					"read_only": 1,
					"no_copy": 1,
					"module": "URY",
				}
				],
      
		"Sales Invoice": [
					{
					"fieldname": "mobile_number",
					"fieldtype": "Data",
					"fetch_from": "customer.mobile_number",
					"label": "Mobile Number",
					"insert_after": "customer_name",
					"translatable": 0,
				},
				{
					"fieldname": "order_info",
					"fieldtype": "Section Break",
					"label": "Order Info",
					"insert_after": "return_against",
				},
				{
					"fieldname": "order_type",
					"fieldtype": "Select",
					"default": "Dine In",
					"options": "URY Restaurant",
					"fetch_from": "customer.mobile_number",
					"label": "Order Type",
					"options": "\nDine In\nTake Away\nDelivery\nPhone In\nAggregators",
					"insert_after": "order_info",
					"translatable": 0
				},
				{
					"fieldname": "waiter",
					"fieldtype": "Data",
					"label": "Waiter",
					"read_only": 0,
					"insert_after": "order_type",
					"translatable": 0
				},
				{
					"fieldname": "column_break_rwbwf",
					"fieldtype": "Column Break",
					"insert_after": "waiter"
				},
				{
					"fieldname": "no_of_pax",
					"fieldtype": "Int",
					"label": "Pax",
					"insert_after": "column_break_rwbwf",
					"read_only": 0,
					"translatable": 0
				},
				{
					"fieldname": "cashier",
					"fieldtype": "Data",
					"label": "Cashier",
					"insert_after": "no_of_pax",
					"read_only": 0,
					"translatable": 0
				},
				{
					"fieldname": "restaurant_info",
					"fieldtype": "Section Break",
					"label": "Restaurant Info",
					"insert_after": "invoice_created",
				},
				{
					"fieldname": "restaurant",
					"fieldtype": "Link",
					"insert_after": "restaurant_info",
					"label": "Restaurant",
					"options": "URY Restaurant",
					"read_only": 0,
				},
				{
					"fieldname": "branch",
					"fieldtype": "Link",
					"insert_after": "restaurant",
					"label": "Branch",
					"options": "Branch",
					"read_only": 0,
				},
				{
					"fieldname": "restaurant_table",
					"fieldtype": "Link",
					"insert_after": "branch",
					"label": "Restaurant Table",
					"options": "URY Table",
					"read_only": 0,
				},
				{
					"fieldname": "column_break_gd1mq",
					"fieldtype": "Column Break",
					"insert_after": "restaurant_table",
				},
				{
					"fieldname": "arrived_time",
					"fieldtype": "Data",
					"insert_after": "column_break_gd1mq",
					"label": "Arrived Time"
				},
				{
					"fieldname": "total_spend_time",
					"fieldtype": "Data",
					"insert_after": "arrived_time",
					"label": "Total Spend Time"
				},
				{
					"fieldname": "custom_ury_credit_section",
					"fieldtype": "Section Break",
					"label": "URY Credit",
					"insert_after": "total_spend_time",
					"collapsible": 1,
					"module": "URY",
				},
				{
					"fieldname": "custom_ury_credit_settlement",
					"fieldtype": "Link",
					"label": "URY POS Settlement",
					"options": "URY POS Settlement",
					"insert_after": "custom_ury_credit_section",
					"read_only": 1,
					"no_copy": 1,
					"search_index": 1,
					"module": "URY",
				},
				{
					"fieldname": "custom_ury_credit_due_date",
					"fieldtype": "Date",
					"label": "Agreed Credit Due Date",
					"insert_after": "custom_ury_credit_settlement",
					"read_only": 1,
					"no_copy": 1,
					"module": "URY",
				},
				{
					"fieldname": "custom_ury_settlement_type",
					"fieldtype": "Select",
					"label": "Settlement Type",
					"options": "\nPartial Credit\nFull Credit\nHouse Offer\nMixed",
					"insert_after": "custom_ury_credit_due_date",
					"read_only": 1,
					"no_copy": 1,
					"module": "URY",
				}
				],

		"POS Profile": [
			{
				"fieldname": "restaurant_info",
				"fieldtype": "Section Break",
				"label": "Restaurant Info",
				"insert_after": "company_address",
			},
			{
				"fieldname": "restaurant",
				"fieldtype": "Link",
				"insert_after": "restaurant_info",
				"label": "Restaurant",
				"options": "URY Restaurant",
			},
			{
				"fieldname": "column_break_c10ag",
				"fieldtype": "Column Break",
				"insert_after": "restaurant",
			},
			{
				"fetch_from": "restaurant.branch" ,
				"fieldname": "branch",
				"fieldtype": "Link",
				"insert_after": "column_break_c10ag",
				"label": "Branch",
				"options": "Branch"
			},
			{
				"fieldname": "printer_info",
				"fieldtype": "Section Break",
				"label": "Printer Info",
				"insert_after": "branch",
			},
			{
				"depends_on": "eval:doc.qz_print != 1" , 
				"fieldname": "printer_settings",
				"fieldtype": "Table",
				"insert_after": "printer_info",
				"label": "Printer Settings",
				"options": "URY Printer Settings"
			},
			{
				"fieldname": "qz_print",
				"fieldtype": "Check",
				"label": "QZ Print",
				"insert_after": "printer_settings"
			},
			{
				"depends_on": "qz_print",
				"fieldname": "qz_host",
				"fieldtype": "Data",
				"insert_after": "qz_print",
				"label": "QZ Host",
				"translatable": 0,
			},
			{
				"fieldname": "custom_ury_commercial_checkout_section",
				"fieldtype": "Section Break",
				"label": "Commercial Checkout",
				"insert_after": "qz_host",
				"collapsible": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_enable_commercial_checkout",
				"fieldtype": "Check",
				"label": "Enable URY Commercial Checkout",
				"insert_after": "custom_ury_commercial_checkout_section",
				"default": "0",
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_enable_credit_sales",
				"fieldtype": "Check",
				"label": "Enable Credit Sales",
				"insert_after": "custom_ury_enable_commercial_checkout",
				"default": "0",
				"depends_on": "custom_ury_enable_commercial_checkout",
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_default_credit_days",
				"fieldtype": "Int",
				"label": "Default Credit Days",
				"insert_after": "custom_ury_enable_credit_sales",
				"default": "30",
				"depends_on": "custom_ury_enable_credit_sales",
				"non_negative": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_max_discount_percentage",
				"fieldtype": "Percent",
				"label": "Maximum Manual Discount",
				"insert_after": "custom_ury_default_credit_days",
				"default": "100",
				"non_negative": 1,
				"module": "URY",
			}
		],
  
		"POS Opening Entry": [
			{
				"fieldname": "restaurant_info",
				"fieldtype": "Section Break",
				"label": "Restaurant Info",
				"insert_after": "user",
			},
			{
				"fieldname": "restaurant",
				"fieldtype": "Link",
				"insert_after": "restaurant_info",
				"label": "Restaurant",
				"options": "URY Restaurant",
				"reqd": 1
			},
			{
				"fieldname": "column_break_e3dky",
				"fieldtype": "Column Break",
				"insert_after": "restaurant",
			},
			{	
				"fieldname": "branch",
				"fieldtype": "Link",
				"insert_after": "column_break_e3dky",
				"label": "Branch",
				"options": "Branch",
				"reqd": 1
			}
		],

		"Price List": [
			{
				"fieldname": "restaurant_menu",
				"fieldtype": "Link",
				"options": "URY Menu",
				"label": "Restaurant Menu",
				"insert_after": "currency",
			}
		],
  
		"Branch": [
			{
				"fieldname": "user",
				"fieldtype": "Table",
				"options": "URY User",
				"label": "User",
				"insert_after": "branch",
				"reqd": 1
			}
		],

		"Customer": [
			{
				"fieldname": "mobile_number",
				"fieldtype": "Data",
				"label": "Mobile Number",
				"insert_after": "customer_name",
				"translatable": 0,
				"reqd": 1
			},
		],

		"POS Invoice Item": [
			{
				"fieldname": "comment",
				"fieldtype": "Data",
				"label": "Comment",
				"insert_after": "description",
				"translatable": 0
			},
			{
				"fieldname": "custom_ury_price_option",
				"fieldtype": "Data",
				"label": "URY Price Option",
				"insert_after": "custom_course",
				"hidden": 1,
				"read_only": 1,
				"search_index": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_price_option_label",
				"fieldtype": "Data",
				"label": "Price Option",
				"insert_after": "custom_ury_price_option",
				"read_only": 1,
				"print_hide_if_no_value": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_rate_before_manual_discount",
				"fieldtype": "Currency",
				"label": "Rate Before Manual Discount",
				"insert_after": "custom_ury_price_option_label",
				"hidden": 1,
				"read_only": 1,
				"no_copy": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_price_option_reduction",
				"fieldtype": "Currency",
				"label": "Price Option Reduction",
				"insert_after": "custom_ury_rate_before_manual_discount",
				"hidden": 1,
				"read_only": 1,
				"no_copy": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_manual_discount_type",
				"fieldtype": "Select",
				"label": "Manual Discount Type",
				"options": "\nPercent\nAmount",
				"insert_after": "custom_ury_price_option_reduction",
				"hidden": 1,
				"read_only": 1,
				"no_copy": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_manual_discount_input",
				"fieldtype": "Float",
				"label": "Manual Discount Input",
				"insert_after": "custom_ury_manual_discount_type",
				"hidden": 1,
				"read_only": 1,
				"no_copy": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_manual_discount_amount",
				"fieldtype": "Currency",
				"label": "Manual Discount Amount",
				"insert_after": "custom_ury_manual_discount_input",
				"hidden": 1,
				"read_only": 1,
				"no_copy": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_manual_discount_reason",
				"fieldtype": "Small Text",
				"label": "Manual Discount Reason",
				"insert_after": "custom_ury_manual_discount_amount",
				"hidden": 1,
				"read_only": 1,
				"no_copy": 1,
				"module": "URY",
			},
		],

		"Sales Invoice Item": [
			{
				"fieldname": "custom_ury_price_option",
				"fieldtype": "Data",
				"label": "URY Price Option",
				"insert_after": "custom_course",
				"hidden": 1,
				"read_only": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_price_option_label",
				"fieldtype": "Data",
				"label": "Price Option",
				"insert_after": "custom_ury_price_option",
				"read_only": 1,
				"print_hide_if_no_value": 1,
				"module": "URY",
			},
		],

		"URY KOT": [
			{
				"fieldname": "custom_merged_tables",
				"fieldtype": "Data",
				"insert_after": "restaurant_table",
				"label": "Merged Tables",
				"read_only": 1,
			},
		],

		"POS Closing Entry": [
			{
				"fieldname": "custom_ury_commercial_summary_section",
				"fieldtype": "Section Break",
				"label": "Resumo comercial URY",
				"insert_after": "pos_transactions",
				"collapsible": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_credit_sales",
				"fieldtype": "Table",
				"label": "Vendas a crédito",
				"options": "URY POS Closing Credit",
				"insert_after": "custom_ury_commercial_summary_section",
				"read_only": 1,
				"allow_on_submit": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_credit_sales_count",
				"fieldtype": "Int",
				"label": "Número de vendas a crédito",
				"insert_after": "custom_ury_credit_sales",
				"read_only": 1,
				"allow_on_submit": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_credit_total",
				"fieldtype": "Currency",
				"label": "Total concedido a crédito",
				"insert_after": "custom_ury_credit_sales_count",
				"read_only": 1,
				"allow_on_submit": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_commercial_summary_column",
				"fieldtype": "Column Break",
				"insert_after": "custom_ury_credit_total",
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_discount_total",
				"fieldtype": "Currency",
				"label": "Total de descontos manuais",
				"insert_after": "custom_ury_commercial_summary_column",
				"read_only": 1,
				"allow_on_submit": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_house_offer_count",
				"fieldtype": "Int",
				"label": "Número de ofertas da casa",
				"insert_after": "custom_ury_discount_total",
				"read_only": 1,
				"allow_on_submit": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_house_offer_value",
				"fieldtype": "Currency",
				"label": "Valor das ofertas da casa",
				"insert_after": "custom_ury_house_offer_count",
				"read_only": 1,
				"allow_on_submit": 1,
				"module": "URY",
			},
			{
				"fieldname": "custom_ury_discount_sales",
				"fieldtype": "Table",
				"label": "Vendas com desconto ou oferta",
				"options": "URY POS Closing Discount Item",
				"insert_after": "custom_ury_house_offer_value",
				"read_only": 1,
				"allow_on_submit": 1,
				"module": "URY",
			},
		],
     
    }
 
def delete_custom_fields(custom_fields):
    for doctype, fields in custom_fields.items():
        frappe.db.delete(
			"Custom Field",
			{
				"fieldname": ("in", [field["fieldname"] for field in fields]),
				"dt": doctype,
			},
		)
        
        frappe.clear_cache(doctype=doctype)
 
 
    
