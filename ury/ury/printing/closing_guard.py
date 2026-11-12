"""Require a persisted, completed closing before rendering any print format."""

import frappe
from frappe import _


def before_print(doc, method=None, print_settings=None):
	# Read the database: printview also accepts a client-supplied document, whose
	# docstatus/status cannot establish that submission actually completed.
	fields = ["docstatus"]
	if doc.doctype == "POS Closing Entry":
		fields += ["status", "error_message"]
	state = (
		frappe.db.get_value(doc.doctype, doc.name, fields, as_dict=True)
		if doc.name
		else None
	)
	ready = state and state.docstatus == 1 and doc.docstatus == 1
	if ready and doc.doctype == "POS Closing Entry":
		ready = state.status == "Submitted" and not state.error_message
	if not ready:
		frappe.throw(
			_("Submeta o fecho e aguarde a conclusão do processamento antes de imprimir."),
			frappe.ValidationError,
		)
