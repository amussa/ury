import frappe
from frappe import _
from frappe.utils import flt

from ury.ury.permissions.ury_pos_settlement import (
	has_global_access as has_settlement_global_access,
)


TOLERANCE = 0.005

def before_save(doc, method):
    sub_pos_close_check(doc, method)

def validate(doc, method):
	apply_rounded_invoice_totals(doc)
	populate_commercial_summary(doc)
	calculate_closing_amount(doc, method)
	validate_difference_justification(doc)
	validate_cashier(doc, method)


def refresh_commercial_summary_after_submit(doc, method=None):
	"""Overwrite any client-supplied post-submit summary with authoritative data."""
	populate_commercial_summary(doc)


def populate_commercial_summary(doc):
	invoice_names = [row.pos_invoice for row in (doc.get("pos_transactions") or []) if row.pos_invoice]
	summary = get_commercial_summary_data(
		invoice_names,
		pos_opening_entry=doc.get("pos_opening_entry"),
	)
	doc.set("custom_ury_credit_sales", summary["credit_sales"])
	for fieldname in (
		"custom_ury_credit_sales_count",
		"custom_ury_credit_total",
		"custom_ury_discount_total",
		"custom_ury_house_offer_count",
		"custom_ury_house_offer_value",
	):
		doc.set(fieldname, summary[fieldname])


@frappe.whitelist()
def get_commercial_summary(pos_invoices, pos_opening_entry=None):
	"""Preview the read-only summary; the validate hook recalculates it again."""
	if isinstance(pos_invoices, str):
		pos_invoices = frappe.parse_json(pos_invoices)
	invoice_names = [
		row.get("pos_invoice") if isinstance(row, dict) else row
		for row in (pos_invoices or [])
	]
	invoice_names = list(dict.fromkeys(name for name in invoice_names if name))
	_validate_summary_access(invoice_names, pos_opening_entry)
	summary = get_commercial_summary_data(invoice_names, pos_opening_entry=pos_opening_entry)
	rounded_totals = get_rounded_invoice_totals(invoice_names)
	summary["rounded_invoice_totals"] = rounded_totals
	summary["rounded_grand_total"] = flt(sum(rounded_totals.values()), 2)
	return summary


def apply_rounded_invoice_totals(doc):
	"""Keep the closing sales total aligned with the amount actually settled."""
	rows = doc.get("pos_transactions") or []
	invoice_names = [row.pos_invoice for row in rows if row.pos_invoice]
	if not invoice_names:
		doc.grand_total = 0
		return

	rounded_totals = get_rounded_invoice_totals(invoice_names)
	if len(rounded_totals) != len(set(invoice_names)):
		frappe.throw(_("Unable to load every POS Invoice total for the closing entry."))

	for row in rows:
		row.grand_total = rounded_totals[row.pos_invoice]
	doc.grand_total = flt(sum(row.grand_total for row in rows), 2)


def get_rounded_invoice_totals(invoice_names):
	invoice_names = list(dict.fromkeys(invoice_names or []))
	if not invoice_names:
		return {}

	invoices = frappe.get_all(
		"POS Invoice",
		filters={"name": ["in", invoice_names]},
		fields=["name", "grand_total", "rounded_total"],
	)
	return {
		invoice.name: _settled_invoice_total(invoice)
		for invoice in invoices
	}


def _settled_invoice_total(invoice):
	# This is the same fallback used by ERPNext/URY payment validation: a zero
	# rounded_total must not turn a small non-zero sale into a free sale.
	return flt(invoice.get("rounded_total")) or flt(invoice.get("grand_total"))


def get_commercial_summary_data(invoice_names, *, pos_opening_entry=None):
	invoice_names = list(dict.fromkeys(invoice_names or []))
	empty = {
		"credit_sales": [],
		"custom_ury_credit_sales_count": 0,
		"custom_ury_credit_total": 0,
		"custom_ury_discount_total": 0,
		"custom_ury_house_offer_count": 0,
		"custom_ury_house_offer_value": 0,
	}
	if not invoice_names:
		return empty

	invoices = frappe.get_all(
		"POS Invoice",
		filters={"name": ["in", invoice_names]},
		fields=["name", "custom_ury_settlement"],
	)
	if len(invoices) != len(invoice_names):
		frappe.throw(_("Unable to load every POS Invoice in the closing summary."))
	settlement_names = list(
		dict.fromkeys(row.custom_ury_settlement for row in invoices if row.custom_ury_settlement)
	)
	if not settlement_names:
		return empty

	settlements = frappe.get_all(
		"URY POS Settlement",
		filters={"name": ["in", settlement_names], "docstatus": 1},
		fields=[
			"name",
			"pos_opening_entry",
			"customer",
			"settlement_type",
			"total_before_manual_discount",
			"manual_discount_total",
			"grand_total",
			"paid_now",
			"credit_amount",
			"due_date",
		],
	)
	if len(settlements) != len(settlement_names):
		frappe.throw(_("A POS Invoice references a missing or unsubmitted URY settlement."))

	if pos_opening_entry:
		wrong_opening = [
			settlement.name
			for settlement in settlements
			if settlement.pos_opening_entry != pos_opening_entry
		]
		if wrong_opening:
			frappe.throw(_("A URY settlement belongs to a different POS Opening Entry."))

	allocations = frappe.get_all(
		"URY POS Settlement Invoice",
		filters={"parent": ["in", settlement_names], "parenttype": "URY POS Settlement"},
		fields=["parent", "pos_invoice", "idx"],
		order_by="parent, idx",
	)
	sales_invoices = frappe.get_all(
		"Sales Invoice",
		filters={
			"custom_ury_credit_settlement": ["in", settlement_names],
			"docstatus": ["<", 2],
		},
		fields=["name", "custom_ury_credit_settlement"],
	)
	return build_commercial_summary(
		invoice_names,
		settlements,
		allocations,
		sales_invoices,
	)


def build_commercial_summary(invoice_names, settlements, allocations, sales_invoices=None):
	"""Build totals once per settlement, never once per POS Invoice."""
	invoice_names = set(invoice_names)
	allocation_map = {}
	for row in allocations:
		allocation_map.setdefault(row.parent, []).append(row.pos_invoice)

	sales_map = {}
	for row in sales_invoices or []:
		settlement = row.custom_ury_credit_settlement
		if settlement in sales_map and sales_map[settlement] != row.name:
			frappe.throw(_("A credit agreement is linked to more than one active Sales Invoice."))
		sales_map[settlement] = row.name

	credit_sales = []
	credit_total = 0.0
	discount_total = 0.0
	house_offer_count = 0
	house_offer_value = 0.0

	for settlement in settlements:
		allocated_invoices = allocation_map.get(settlement.name, [])
		if not allocated_invoices or any(name not in invoice_names for name in allocated_invoices):
			frappe.throw(
				_("All POS Invoices from a URY settlement must be included in the same closing.")
			)

		if settlement.settlement_type == "House Offer":
			house_offer_count += 1
			house_offer_value += flt(settlement.total_before_manual_discount)
		else:
			discount_total += flt(settlement.manual_discount_total)

		if flt(settlement.credit_amount) < TOLERANCE:
			continue
		credit_total += flt(settlement.credit_amount)
		credit_sales.append(
			{
				"settlement": settlement.name,
				"pos_invoices": ", ".join(allocated_invoices),
				"customer": settlement.customer,
				"total_final": settlement.grand_total,
				"paid_now": settlement.paid_now,
				"credit_amount": settlement.credit_amount,
				"due_date": settlement.due_date,
				"sales_invoice": sales_map.get(settlement.name),
			}
		)

	return {
		"credit_sales": credit_sales,
		"custom_ury_credit_sales_count": len(credit_sales),
		"custom_ury_credit_total": flt(credit_total, 2),
		"custom_ury_discount_total": flt(discount_total, 2),
		"custom_ury_house_offer_count": house_offer_count,
		"custom_ury_house_offer_value": flt(house_offer_value, 2),
	}


def _validate_summary_access(invoice_names, pos_opening_entry):
	if frappe.session.user == "Guest":
		raise frappe.AuthenticationError

	global_access = has_settlement_global_access(frappe.session.user)
	assigned_branches = set()
	if not global_access:
		assigned_branches = set(
			frappe.db.sql(
				"""
				select ury_user.parent
				from `tabURY User` ury_user
				where ury_user.parenttype = 'Branch' and ury_user.user = %s
				""",
				frappe.session.user,
				pluck=True,
			)
		)

	opening = None
	if pos_opening_entry:
		opening = frappe.db.get_value(
			"POS Opening Entry",
			pos_opening_entry,
			["pos_profile", "user", "status"],
			as_dict=True,
		)
		if not opening:
			frappe.throw(_("POS Opening Entry does not exist."), frappe.PermissionError)

	for name in invoice_names:
		invoice = frappe.db.get_value(
			"POS Invoice",
			name,
			["branch", "pos_profile", "owner"],
			as_dict=True,
		)
		if not invoice:
			frappe.throw(_("POS Invoice {0} does not exist.").format(frappe.bold(name)))
		if not global_access and invoice.branch not in assigned_branches:
			frappe.throw(_("You cannot view a settlement from another branch."), frappe.PermissionError)
		if opening and invoice.pos_profile != opening.pos_profile:
			frappe.throw(_("POS Invoice does not belong to the selected POS Opening Entry."), frappe.PermissionError)


def sub_pos_close_check(doc,method):
    cashier = None
    multiple_cashier = frappe.db.get_value("POS Profile",doc.pos_profile,"custom_enable_multiple_cashier")
    if multiple_cashier:
        get_cashier = frappe.get_doc("POS Profile", doc.pos_profile)
        for user_details in get_cashier.applicable_for_users:
            if not user_details.custom_main_cashier:
                cashier = user_details.user
        if frappe.session.user != cashier:
            branch=frappe.db.get_value("POS Profile",doc.pos_profile,"branch")
            pos_opening_list = frappe.get_all(
                "POS Opening Entry",
                fields=["name", "docstatus", "status", "posting_date"],
                filters={"branch": branch,"user":cashier},
            )
            flag = 0
            for pos_opening in pos_opening_list:
                if pos_opening.status == "Open" and pos_opening.docstatus == 1:
                    flag = 1
            if flag == 1:
                frappe.throw(("Sub Cashier POS  must be closed"), title=("Sub Cashier POS Closing Required"))
                
            return flag
    else:
        pass

def calculate_closing_amount(doc, method):
    multiple_cashier = frappe.db.get_value("POS Profile",doc.pos_profile,"custom_enable_multiple_cashier")
    if multiple_cashier:
        sub_pos_closing = frappe.get_all(
            "Sub POS Closing",
            filters=[
                ["posting_date", "<=", doc.posting_date],
                ["period_start_date", ">=", doc.period_start_date],
                ["docstatus", "=", 1]
            ],
            fields=["name"] 
        )
        if sub_pos_closing:
            for closing_details in doc.payment_reconciliation:
                if closing_details.custom_closing_amount is None:
                    frappe.throw(
                        _("Enter the main cashier counted amount for {0}.").format(
                            frappe.bold(closing_details.mode_of_payment)
                        )
                    )
                sub_closing_amount = frappe.db.get_value("Sub POS Closing Payment",{"parent":sub_pos_closing[0].name,"mode_of_payment":closing_details.mode_of_payment},"closing_amount") or 0
                main_closing_amount = flt(closing_details.custom_closing_amount)
                total_closing_amount = flt(sub_closing_amount) + main_closing_amount
                _set_reconciliation_values(closing_details, total_closing_amount)
        else:
            frappe.throw("No Sub POS Closing entries found between the given dates")
            return None
    else:
        for closing_details in doc.payment_reconciliation:
            if closing_details.closing_amount is None:
                frappe.throw(
                    _("Enter the counted amount for {0}.").format(
                        frappe.bold(closing_details.mode_of_payment)
                    )
                )
            _set_reconciliation_values(closing_details, closing_details.closing_amount)


def _set_reconciliation_values(closing_details, counted_amount):
    closing_details.closing_amount = flt(counted_amount, 2)
    closing_details.difference = flt(
        closing_details.closing_amount - flt(closing_details.expected_amount), 2
    )


def validate_difference_justification(doc):
    has_difference = any(
        abs(flt(row.difference, 2)) >= 0.005 for row in doc.payment_reconciliation
    )
    justification = (doc.get("custom_difference_justification") or "").strip()
    if has_difference and not justification:
        frappe.throw(
            _(
                "Explain the payment difference before saving or submitting this closing entry."
            )
        )


def validate_cashier(doc, method):
    cashier = None
    multiple_cashier = frappe.db.get_value("POS Profile",doc.pos_profile,"custom_enable_multiple_cashier")
    if multiple_cashier:
        get_cashier = frappe.get_doc("POS Profile", doc.pos_profile)
        for user_details in get_cashier.applicable_for_users:
            if not user_details.custom_main_cashier:
                cashier = user_details.user
        if frappe.session.user == cashier:
            frappe.throw("Sub Cashiers are not allowed to make POS Closing Entries.")
    else:
        pass
