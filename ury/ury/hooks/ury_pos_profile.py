import frappe
from frappe import _, msgprint
from frappe.utils import cint, flt


def validate(doc, method):
    validate_bill_check(doc, method)
    validate_cost_center(doc, method)
    validate_commercial_checkout(doc)


def validate_bill_check(doc, method):
    for row in doc.printer_settings:
        if not row.bill or not row.printer:
            msgprint(
                _(
                    "Either Bill is not enabled / Printer is not selected in Printer Settings."
                )
            )
            
def validate_cost_center(doc, method):
    if not doc.cost_center:
       frappe.throw(
                _(
                    "Cost center is mandatory."
                )
            )


def validate_commercial_checkout(doc):
    """Keep partial payments gated by an explicit URY credit configuration."""
    credit_enabled = cint(doc.get("custom_ury_enable_credit_sales"))
    checkout_enabled = cint(doc.get("custom_ury_enable_commercial_checkout"))

    if credit_enabled and not checkout_enabled:
        frappe.throw(
            _("Enable URY Commercial Checkout before enabling credit sales.")
        )

    if credit_enabled and not cint(doc.get("allow_partial_payment")):
        frappe.throw(
            _("Allow Partial Payment must be enabled when URY credit sales are enabled.")
        )

    if credit_enabled and cint(doc.get("custom_ury_default_credit_days")) < 1:
        frappe.throw(_("Default Credit Days must be at least one day."))

    max_discount = flt(doc.get("custom_ury_max_discount_percentage"))
    if max_discount < 0 or max_discount > 100:
        frappe.throw(_("Maximum Manual Discount must be between 0 and 100 percent."))
