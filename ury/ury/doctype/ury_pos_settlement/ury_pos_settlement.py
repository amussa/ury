from __future__ import annotations

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

CREDIT_TYPES = {"Partial Credit", "Full Credit"}
SPECIAL_TYPES = CREDIT_TYPES | {"House Offer"}
TOLERANCE = 0.005


class URYPOSSettlement(Document):
	"""Immutable, server-authored audit record for one commercial checkout."""

	def validate(self):
		self._validate_numbers()
		self._validate_invoice_allocations()
		self._validate_business_invariants()

	def before_cancel(self):
		frappe.throw(
			_("A URY POS Settlement is immutable and cannot be cancelled."),
			frappe.PermissionError,
		)

	def on_trash(self):
		frappe.throw(
			_("A URY POS Settlement is an audit record and cannot be deleted."),
			frappe.PermissionError,
		)

	def _validate_numbers(self):
		fields = (
			"total_catalogue",
			"price_option_reduction",
			"total_before_manual_discount",
			"item_discount_total",
			"invoice_discount_total",
			"manual_discount_total",
			"grand_total",
			"paid_now",
			"credit_amount",
		)
		for fieldname in fields:
			value = flt(self.get(fieldname))
			if not math.isfinite(value) or value < -TOLERANCE:
				frappe.throw(_("{0} must be a finite, non-negative amount.").format(self.meta.get_label(fieldname)))

	def _validate_business_invariants(self):
		settlement_type = self.settlement_type
		paid_now = flt(self.paid_now)
		credit_amount = flt(self.credit_amount)
		grand_total = flt(self.grand_total)

		if abs(grand_total - paid_now - credit_amount) >= TOLERANCE:
			frappe.throw(_("Final total must equal paid now plus credit."))

		if settlement_type in CREDIT_TYPES:
			if credit_amount < TOLERANCE:
				frappe.throw(_("A credit settlement must contain a positive credit amount."))
			if not self.due_date:
				frappe.throw(_("Due date is mandatory for a credit settlement."))
			posting_date = frappe.db.get_value("POS Invoice", self.invoices[0].pos_invoice, "posting_date")
			if posting_date and getdate(self.due_date) < getdate(posting_date):
				frappe.throw(_("Credit due date cannot be before the sale date."))
		elif credit_amount >= TOLERANCE:
			frappe.throw(_("Only a credit settlement can contain a credit amount."))

		if settlement_type == "Full Credit" and paid_now >= TOLERANCE:
			frappe.throw(_("A full-credit settlement cannot contain a payment received now."))
		if settlement_type == "Partial Credit" and paid_now < TOLERANCE:
			frappe.throw(_("A partial-credit settlement must contain a payment received now."))
		if settlement_type == "House Offer" and any(
			abs(value) >= TOLERANCE for value in (grand_total, paid_now, credit_amount)
		):
			frappe.throw(_("A house offer must finish at zero, without payment or credit."))

		needs_reason = settlement_type in SPECIAL_TYPES or flt(self.manual_discount_total) >= TOLERANCE
		if needs_reason and not (self.reason or "").strip():
			frappe.throw(_("Reason is mandatory for a discount, house offer or credit settlement."))

	def _validate_invoice_allocations(self):
		if not self.invoices:
			frappe.throw(_("At least one POS Invoice is required."))

		seen = set()
		for row in self.invoices:
			if row.pos_invoice in seen:
				frappe.throw(_("POS Invoice {0} is repeated in the settlement.").format(frappe.bold(row.pos_invoice)))
			seen.add(row.pos_invoice)

		checks = (
			("total_final", "grand_total"),
			("paid_now", "paid_now"),
			("credit_amount", "credit_amount"),
		)
		for child_field, parent_field in checks:
			allocated = sum(flt(row.get(child_field)) for row in self.invoices)
			if abs(allocated - flt(self.get(parent_field))) >= TOLERANCE:
				frappe.throw(
					_("POS Invoice allocations for {0} do not match the settlement total.").format(
						self.meta.get_label(parent_field)
					)
				)
