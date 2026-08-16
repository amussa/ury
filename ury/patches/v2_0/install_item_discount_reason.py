import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from ury.setup import get_custom_fields


FIELDNAME = "custom_ury_manual_discount_reason"


def execute():
	"""Install the item-discount reason added after the checkout base patch."""
	fields = [
		field
		for field in get_custom_fields()["POS Invoice Item"]
		if field["fieldname"] == FIELDNAME
	]
	create_custom_fields({"POS Invoice Item": fields}, update=True)
	frappe.clear_cache(doctype="POS Invoice Item")
