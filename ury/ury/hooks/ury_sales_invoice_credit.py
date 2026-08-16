from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate

CREDIT_TYPES = {"Partial Credit", "Full Credit"}
TOLERANCE = 0.01


def before_validate(doc, method=None):
	_apply_settlement_metadata(doc, validate_financials=False)


def before_submit(doc, method=None):
	# Standard Sales Invoice validation may rebuild payment terms. Reapply and
	# verify the agreed date immediately before the accounting submission.
	_apply_settlement_metadata(doc, validate_financials=True)


def on_submit(doc, method=None):
	settlement = doc.get("custom_ury_credit_settlement")
	if settlement:
		_validate_submitted_credit_ledger(doc, settlement)
		_update_closing_sales_invoice(settlement, doc.name)


def on_cancel(doc, method=None):
	settlement = doc.get("custom_ury_credit_settlement")
	if settlement:
		_update_closing_sales_invoice(settlement, None, expected=doc.name)


def _apply_settlement_metadata(doc, *, validate_financials):
	pos_invoice_names = _get_source_pos_invoice_names(doc)
	if not pos_invoice_names:
		return

	records = frappe.get_all(
		"POS Invoice",
		filters={"name": ["in", pos_invoice_names]},
		fields=[
			"name",
			"customer",
			"is_return",
			"custom_ury_settlement",
			"custom_ury_settlement_type",
			"custom_ury_credit_amount",
			"custom_ury_credit_due_date",
		],
	)
	if len(records) != len(pos_invoice_names):
		frappe.throw(_("Unable to resolve every source POS Invoice for consolidation."))

	credit_records = [
		record
		for record in records
		if record.custom_ury_settlement_type in CREDIT_TYPES
		or flt(record.custom_ury_credit_amount) > 0
	]
	if not credit_records:
		_apply_non_credit_type(doc, records)
		return

	settlements = {record.custom_ury_settlement for record in credit_records if record.custom_ury_settlement}
	if len(settlements) != 1 or any(not record.custom_ury_settlement for record in credit_records):
		frappe.throw(_("A consolidated credit Sales Invoice must contain exactly one URY settlement."))
	settlement_name = settlements.pop()

	# Every non-return sale in this Sales Invoice must belong to this agreement.
	foreign_sales = [
		record.name
		for record in records
		if not record.is_return and record.custom_ury_settlement != settlement_name
	]
	if foreign_sales:
		frappe.throw(
			_("Paid and credit POS Invoices cannot be consolidated into the same Sales Invoice.")
		)

	settlement = frappe.db.get_value(
		"URY POS Settlement",
		settlement_name,
		["docstatus", "customer", "settlement_type", "credit_amount", "due_date"],
		as_dict=True,
	)
	if not settlement or settlement.docstatus != 1:
		frappe.throw(_("URY POS Settlement {0} is not submitted.").format(frappe.bold(settlement_name)))
	if settlement.settlement_type not in CREDIT_TYPES:
		frappe.throw(_("URY POS Settlement {0} is not a credit agreement.").format(frappe.bold(settlement_name)))
	if doc.customer != settlement.customer:
		frappe.throw(_("The consolidated Sales Invoice customer differs from the credit agreement."))

	due_dates = {
		getdate(record.custom_ury_credit_due_date)
		for record in credit_records
		if record.custom_ury_credit_due_date
	}
	if settlement.due_date:
		due_dates.add(getdate(settlement.due_date))
	if len(due_dates) != 1:
		frappe.throw(_("Credit agreement due dates are missing or inconsistent."))
	due_date = due_dates.pop()

	allocated_credit = sum(flt(record.custom_ury_credit_amount) for record in credit_records)
	if abs(allocated_credit - flt(settlement.credit_amount)) >= TOLERANCE:
		frappe.throw(_("POS Invoice credit allocations do not match the URY settlement."))

	doc.custom_ury_credit_settlement = settlement_name
	doc.custom_ury_credit_due_date = due_date
	doc.custom_ury_settlement_type = settlement.settlement_type
	doc.due_date = due_date
	for schedule in doc.get("payment_schedule") or []:
		schedule.due_date = due_date

	# ERPNext may automatically write off a small outstanding balance on a
	# consolidated POS invoice (according to POS Profile.write_off_limit).  A
	# credit agreement must preserve even a sub-unit balance in Receivables.
	doc.write_off_outstanding_amount_automatically = 0
	doc.write_off_amount = 0
	doc.base_write_off_amount = 0
	doc.outstanding_amount = flt(settlement.credit_amount)

	if validate_financials:
		final_total = flt(doc.get("rounded_total") or doc.get("grand_total"))
		received = flt(doc.get("paid_amount"))
		expected_outstanding = max(final_total - received, 0)
		if abs(expected_outstanding - flt(settlement.credit_amount)) >= TOLERANCE:
			frappe.throw(
				_("Consolidated Sales Invoice outstanding does not match the agreed credit amount.")
			)


def _get_source_pos_invoice_names(doc):
	return list(
		dict.fromkeys(
			row.pos_invoice
			for row in (doc.get("items") or [])
			if row.get("pos_invoice")
		)
	)


def _validate_submitted_credit_ledger(doc, settlement_name):
	"""Prove that the submitted agreement reached Accounts Receivable."""
	settlement = frappe.db.get_value(
		"URY POS Settlement",
		settlement_name,
		["customer", "credit_amount", "due_date"],
		as_dict=True,
	)
	if not settlement:
		frappe.throw(_("The URY credit settlement no longer exists."))

	invoice_state = frappe.db.get_value(
		"Sales Invoice",
		doc.name,
		[
			"docstatus",
			"customer",
			"outstanding_amount",
			"due_date",
			"status",
			"write_off_outstanding_amount_automatically",
			"write_off_amount",
			"base_write_off_amount",
		],
		as_dict=True,
	)
	if not invoice_state or invoice_state.docstatus != 1:
		frappe.throw(_("The consolidated credit Sales Invoice was not submitted."))
	if invoice_state.customer != settlement.customer:
		frappe.throw(_("Accounts Receivable customer differs from the credit agreement."))
	if getdate(invoice_state.due_date) != getdate(settlement.due_date):
		frappe.throw(_("Accounts Receivable due date differs from the credit agreement."))
	if abs(flt(invoice_state.outstanding_amount) - flt(settlement.credit_amount)) >= TOLERANCE:
		frappe.throw(_("Accounts Receivable balance differs from the credit agreement."))
	if (
		invoice_state.write_off_outstanding_amount_automatically
		or abs(flt(invoice_state.write_off_amount)) >= TOLERANCE
		or abs(flt(invoice_state.base_write_off_amount)) >= TOLERANCE
	):
		frappe.throw(_("A credit agreement cannot be automatically written off."))
	if invoice_state.status not in ("Unpaid", "Partly Paid", "Overdue"):
		frappe.throw(_("The consolidated credit Sales Invoice is not open in Accounts Receivable."))

	ledger = frappe.db.sql(
		"""
		SELECT
			COUNT(*) AS entry_count,
			COALESCE(SUM(amount_in_account_currency), 0) AS outstanding,
			MIN(due_date) AS first_due_date,
			MAX(due_date) AS last_due_date
		FROM `tabPayment Ledger Entry`
		WHERE delinked = 0
		  AND account_type = 'Receivable'
		  AND party_type = 'Customer'
		  AND party = %(customer)s
		  AND account = %(account)s
		  AND against_voucher_type = 'Sales Invoice'
		  AND against_voucher_no = %(invoice)s
		""",
		{
			"customer": settlement.customer,
			"account": doc.debit_to,
			"invoice": doc.name,
		},
		as_dict=True,
	)
	ledger = ledger[0] if ledger else None
	if not ledger or not ledger.entry_count:
		frappe.throw(_("No active Payment Ledger entry was created for the credit sale."))
	if abs(flt(ledger.outstanding) - flt(settlement.credit_amount)) >= TOLERANCE:
		frappe.throw(_("Payment Ledger balance differs from the credit agreement."))
	if (
		getdate(ledger.first_due_date) != getdate(settlement.due_date)
		or getdate(ledger.last_due_date) != getdate(settlement.due_date)
	):
		frappe.throw(_("Payment Ledger due date differs from the credit agreement."))


def _apply_non_credit_type(doc, records):
	types = {
		record.custom_ury_settlement_type
		for record in records
		if record.custom_ury_settlement_type and not record.is_return
	}
	if types == {"House Offer"}:
		doc.custom_ury_settlement_type = "House Offer"
	elif "House Offer" in types:
		doc.custom_ury_settlement_type = "Mixed"
	else:
		doc.custom_ury_settlement_type = None
	doc.custom_ury_credit_settlement = None
	doc.custom_ury_credit_due_date = None


def _update_closing_sales_invoice(settlement, sales_invoice, *, expected=None):
	rows = frappe.get_all(
		"URY POS Closing Credit",
		filters={"settlement": settlement},
		fields=["name", "parent", "sales_invoice"],
	)
	for row in rows:
		if frappe.db.get_value("POS Closing Entry", row.parent, "docstatus") == 2:
			continue
		if expected is not None and row.sales_invoice != expected:
			continue
		if expected is None and row.sales_invoice and row.sales_invoice != sales_invoice:
			frappe.throw(
				_("Credit settlement {0} is already linked to another Sales Invoice.").format(
					frappe.bold(settlement)
				)
			)
		frappe.db.set_value(
			"URY POS Closing Credit",
			row.name,
			"sales_invoice",
			sales_invoice,
			update_modified=False,
		)
