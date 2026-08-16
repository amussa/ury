from unittest import TestCase
from unittest.mock import patch

import frappe

from ury.ury.hooks.ury_sales_invoice_credit import (
	_validate_submitted_credit_ledger,
	before_submit,
	before_validate,
)


class TestURYSalesInvoiceCredit(TestCase):
	@patch("ury.ury.hooks.ury_sales_invoice_credit.frappe.db.get_value")
	@patch("ury.ury.hooks.ury_sales_invoice_credit.frappe.get_all")
	def test_credit_due_date_is_applied_before_validation(self, get_all, get_value):
		get_all.return_value = [_credit_pos_invoice()]
		get_value.return_value = frappe._dict(
			docstatus=1,
			customer="Customer A",
			settlement_type="Full Credit",
			credit_amount=100,
			due_date="2026-09-15",
		)
		doc = _sales_invoice()

		before_validate(doc)

		self.assertEqual(str(doc.due_date), "2026-09-15")
		self.assertEqual(doc.custom_ury_credit_settlement, "SET-1")
		self.assertEqual(str(doc.payment_schedule[0].due_date), "2026-09-15")

	@patch("ury.ury.hooks.ury_sales_invoice_credit.frappe.db.get_value")
	@patch("ury.ury.hooks.ury_sales_invoice_credit.frappe.get_all")
	def test_mismatched_outstanding_is_rejected_before_submit(self, get_all, get_value):
		get_all.return_value = [_credit_pos_invoice()]
		get_value.return_value = frappe._dict(
			docstatus=1,
			customer="Customer A",
			settlement_type="Full Credit",
			credit_amount=100,
			due_date="2026-09-15",
		)
		doc = _sales_invoice()
		doc.paid_amount = 20

		with self.assertRaises(frappe.ValidationError):
			before_submit(doc)

	@patch("ury.ury.hooks.ury_sales_invoice_credit.frappe.db.get_value")
	@patch("ury.ury.hooks.ury_sales_invoice_credit.frappe.get_all")
	def test_small_credit_disables_standard_auto_write_off(self, get_all, get_value):
		get_all.return_value = [_credit_pos_invoice(credit_amount=0.5)]
		get_value.return_value = frappe._dict(
			docstatus=1,
			customer="Customer A",
			settlement_type="Partial Credit",
			credit_amount=0.5,
			due_date="2026-09-15",
		)
		doc = _sales_invoice(grand_total=100, paid_amount=99.5)
		doc.write_off_outstanding_amount_automatically = 1
		doc.write_off_amount = 0.5
		doc.base_write_off_amount = 0.5
		doc.outstanding_amount = 0

		before_submit(doc)

		self.assertEqual(doc.write_off_outstanding_amount_automatically, 0)
		self.assertEqual(doc.write_off_amount, 0)
		self.assertEqual(doc.base_write_off_amount, 0)
		self.assertEqual(doc.outstanding_amount, 0.5)

	@patch("ury.ury.hooks.ury_sales_invoice_credit.frappe.db.sql")
	@patch("ury.ury.hooks.ury_sales_invoice_credit.frappe.db.get_value")
	def test_submitted_credit_is_proved_in_accounts_receivable(self, get_value, sql):
		get_value.side_effect = [
			frappe._dict(customer="Customer A", credit_amount=100, due_date="2026-09-15"),
			frappe._dict(
				docstatus=1,
				customer="Customer A",
				outstanding_amount=100,
				due_date="2026-09-15",
				status="Unpaid",
				write_off_outstanding_amount_automatically=0,
				write_off_amount=0,
				base_write_off_amount=0,
			),
		]
		sql.return_value = [
			frappe._dict(
				entry_count=1,
				outstanding=100,
				first_due_date="2026-09-15",
				last_due_date="2026-09-15",
			)
		]
		doc = _sales_invoice()
		doc.name = "SINV-1"
		doc.debit_to = "Debtors - G"

		_validate_submitted_credit_ledger(doc, "SET-1")

	@patch("ury.ury.hooks.ury_sales_invoice_credit.frappe.db.sql")
	@patch("ury.ury.hooks.ury_sales_invoice_credit.frappe.db.get_value")
	def test_payment_ledger_due_date_mismatch_is_rejected(self, get_value, sql):
		get_value.side_effect = [
			frappe._dict(customer="Customer A", credit_amount=100, due_date="2026-09-15"),
			frappe._dict(
				docstatus=1,
				customer="Customer A",
				outstanding_amount=100,
				due_date="2026-09-15",
				status="Unpaid",
				write_off_outstanding_amount_automatically=0,
				write_off_amount=0,
				base_write_off_amount=0,
			),
		]
		sql.return_value = [
			frappe._dict(
				entry_count=1,
				outstanding=100,
				first_due_date="2026-09-16",
				last_due_date="2026-09-16",
			)
		]
		doc = _sales_invoice()
		doc.name = "SINV-1"
		doc.debit_to = "Debtors - G"

		with self.assertRaises(frappe.ValidationError):
			_validate_submitted_credit_ledger(doc, "SET-1")


def _credit_pos_invoice(*, credit_amount=100):
	return frappe._dict(
		name="PI-1",
		customer="Customer A",
		is_return=0,
		custom_ury_settlement="SET-1",
		custom_ury_settlement_type="Full Credit",
		custom_ury_credit_amount=credit_amount,
		custom_ury_credit_due_date="2026-09-15",
	)


def _sales_invoice(*, grand_total=100, paid_amount=0):
	return frappe._dict(
		customer="Customer A",
		items=[frappe._dict(pos_invoice="PI-1")],
		payment_schedule=[frappe._dict(due_date="2026-08-16")],
		grand_total=grand_total,
		rounded_total=grand_total,
		paid_amount=paid_amount,
	)
