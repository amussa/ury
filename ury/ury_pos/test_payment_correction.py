from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from ury.ury_pos.payment_correction import (
	_validate_correction_state,
	can_correct_payment,
	distribute_payment_correction,
	normalise_payment_correction,
)


class TestPaymentCorrection(FrappeTestCase):
	@patch("ury.ury_pos.payment_correction._get_open_closing_entry", return_value=None)
	def test_payment_with_change_cannot_be_reclassified(self, _closing):
		invoice = frappe._dict(
			name="PI-1",
			docstatus=1,
			status="Paid",
			consolidated_invoice=None,
			custom_ury_settlement=None,
			custom_ury_settlement_type="Paid",
			change_amount=100,
			base_change_amount=100,
		)

		with self.assertRaises(frappe.ValidationError):
			_validate_correction_state([invoice])

	@patch("ury.ury_pos.payment_correction._get_open_closing_entry", return_value=None)
	def test_house_offer_has_no_payment_to_correct(self, _closing):
		invoice = frappe._dict(
			name="PI-1",
			docstatus=1,
			status="Paid",
			consolidated_invoice=None,
			custom_ury_settlement="SET-1",
			custom_ury_settlement_type="House Offer",
			change_amount=0,
			base_change_amount=0,
		)

		with self.assertRaises(frappe.ValidationError):
			_validate_correction_state([invoice])

	def test_sale_author_and_supervisors_are_authorised(self):
		invoice = frappe._dict(waiter="seller@example.com")

		self.assertTrue(
			can_correct_payment(invoice, user="seller@example.com", roles=[])
		)
		self.assertTrue(
			can_correct_payment(invoice, user="manager@example.com", roles=["URY Manager"])
		)
		self.assertTrue(
			can_correct_payment(invoice, user="system@example.com", roles=["System Manager"])
		)
		self.assertFalse(
			can_correct_payment(invoice, user="other@example.com", roles=["URY Cashier"])
		)

	def test_normalise_payment_correction_supports_mixed_payments(self):
		payments = normalise_payment_correction(
			[
				{"mode_of_payment": "Numerário", "amount": 250},
				{"mode_of_payment": "M-Pesa", "amount": 750},
			],
			["Numerário", "M-Pesa", "e-Mola"],
			1000,
		)

		self.assertEqual(
			payments,
			[
				{"mode_of_payment": "Numerário", "amount": 250.0},
				{"mode_of_payment": "M-Pesa", "amount": 750.0},
			],
		)

	def test_normalise_payment_correction_preserves_total(self):
		with self.assertRaises(frappe.ValidationError):
			normalise_payment_correction(
				[{"mode_of_payment": "M-Pesa", "amount": 900}],
				["Numerário", "M-Pesa"],
				1000,
			)

	def test_normalise_payment_correction_rejects_duplicate_modes(self):
		with self.assertRaises(frappe.ValidationError):
			normalise_payment_correction(
				[
					{"mode_of_payment": "M-Pesa", "amount": 400},
					{"mode_of_payment": "M-Pesa", "amount": 600},
				],
				["M-Pesa"],
				1000,
			)

	def test_distribute_payment_correction_preserves_merged_invoice_totals(self):
		allocations = distribute_payment_correction(
			[
				{"mode_of_payment": "M-Pesa", "amount": 700},
				{"mode_of_payment": "Numerário", "amount": 300},
			],
			[600, 400],
		)

		self.assertEqual(
			allocations,
			[
				[{"mode_of_payment": "M-Pesa", "amount": 600.0}],
				[
					{"mode_of_payment": "M-Pesa", "amount": 100.0},
					{"mode_of_payment": "Numerário", "amount": 300.0},
				],
			],
		)
