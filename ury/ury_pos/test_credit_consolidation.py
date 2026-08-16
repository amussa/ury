from unittest import TestCase

import frappe

from ury.ury_pos.credit_consolidation import PAID_GROUP, partition_invoice_records


class TestCreditConsolidationPartition(TestCase):
	def test_same_customer_with_two_agreements_is_partitioned(self):
		groups = partition_invoice_records(
			[
				_credit("PI-1", "SET-1", "2026-09-01"),
				_credit("PI-2", "SET-2", "2026-09-15"),
			]
		)
		self.assertEqual(list(groups.values()), [["PI-1"], ["PI-2"]])

	def test_joined_invoices_in_same_agreement_stay_together(self):
		groups = partition_invoice_records(
			[
				_credit("PI-1", "SET-1", "2026-09-01"),
				_credit("PI-2", "SET-1", "2026-09-01", amount=0),
			]
		)
		self.assertEqual(list(groups.values()), [["PI-1", "PI-2"]])

	def test_paid_and_house_offer_share_standard_partition(self):
		groups = partition_invoice_records(
			[
				frappe._dict(name="PI-PAID", custom_ury_settlement_type="Paid"),
				frappe._dict(name="PI-OFFER", custom_ury_settlement_type="House Offer"),
			]
		)
		self.assertEqual(groups, {PAID_GROUP: ["PI-PAID", "PI-OFFER"]})

	def test_return_follows_original_credit_partition(self):
		groups = partition_invoice_records(
			[
				_credit("PI-1", "SET-1", "2026-09-01"),
				frappe._dict(
					name="PI-RET",
					is_return=1,
					return_against="PI-1",
				),
			]
		)
		self.assertEqual(list(groups.values()), [["PI-1", "PI-RET"]])

	def test_credit_without_settlement_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			partition_invoice_records(
				[
					frappe._dict(
						name="PI-1",
						custom_ury_settlement_type="Full Credit",
						custom_ury_credit_amount=100,
					)
				]
			)


def _credit(name, settlement, due_date, amount=100):
	return frappe._dict(
		name=name,
		custom_ury_settlement=settlement,
		custom_ury_settlement_type="Full Credit",
		custom_ury_credit_amount=amount,
		custom_ury_credit_due_date=due_date,
		is_return=0,
		return_against=None,
	)
