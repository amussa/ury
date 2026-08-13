import frappe
from frappe import _
from frappe.utils import flt

def before_save(doc, method):
    sub_pos_close_check(doc, method)

def validate(doc, method):
    calculate_closing_amount(doc, method)
    validate_difference_justification(doc)
    validate_cashier(doc, method)


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
