import io
from unittest import TestCase, TextTestRunner, defaultTestLoader
from unittest.mock import patch

import frappe

from ury.ury.hooks import ury_pos_closing_entry


class TestURYPosClosingEntryHooks(TestCase):
	@patch("ury.ury.hooks.ury_pos_closing_entry.frappe.db.get_value", return_value=0)
	def test_single_cashier_uses_manually_counted_native_amount(self, _get_value):
		doc = _closing(
			_rows(_row("Cartão", expected=14060, closing=11330)),
			justification="Terminal de pagamento conferido.",
		)

		ury_pos_closing_entry.calculate_closing_amount(doc, "validate")
		ury_pos_closing_entry.validate_difference_justification(doc)

		self.assertEqual(doc.payment_reconciliation[0].closing_amount, 11330)
		self.assertEqual(doc.payment_reconciliation[0].difference, -2730)

	@patch("ury.ury.hooks.ury_pos_closing_entry.frappe.db.get_value", return_value=0)
	def test_single_cashier_accepts_explicit_zero(self, _get_value):
		doc = _closing(
			_rows(_row("Numerário", expected=125, closing=0)),
			justification="Nenhum numerário encontrado.",
		)

		ury_pos_closing_entry.calculate_closing_amount(doc, "validate")

		self.assertEqual(doc.payment_reconciliation[0].closing_amount, 0)
		self.assertEqual(doc.payment_reconciliation[0].difference, -125)

	@patch("ury.ury.hooks.ury_pos_closing_entry.frappe.db.get_value", return_value=0)
	def test_single_cashier_rejects_missing_count(self, _get_value):
		doc = _closing(_rows(_row("M-Pesa", expected=100, closing=None)))

		with self.assertRaises(frappe.ValidationError):
			ury_pos_closing_entry.calculate_closing_amount(doc, "validate")

	def test_difference_requires_justification(self):
		doc = _closing(_rows(_row("Cartão", expected=100, closing=90, difference=-10)))

		with self.assertRaises(frappe.ValidationError):
			ury_pos_closing_entry.validate_difference_justification(doc)

	def test_matching_count_does_not_require_justification(self):
		doc = _closing(_rows(_row("Cartão", expected=100, closing=100, difference=0)))

		ury_pos_closing_entry.validate_difference_justification(doc)

	@patch("ury.ury.hooks.ury_pos_closing_entry.frappe.get_all")
	@patch("ury.ury.hooks.ury_pos_closing_entry.frappe.db.get_value")
	def test_multiple_cashier_keeps_aggregate_in_native_amount(self, get_value, get_all):
		def values(doctype, *args, **kwargs):
			if doctype == "POS Profile":
				return 1
			if doctype == "Sub POS Closing Payment":
				return 30
			raise AssertionError(doctype)

		get_value.side_effect = values
		get_all.return_value = [frappe._dict(name="SUB-CLO-1")]
		doc = _closing(
			_rows(
				_row(
					"Cartão",
					expected=120,
					closing=120,
					custom_closing_amount=100,
				)
			)
		)

		ury_pos_closing_entry.calculate_closing_amount(doc, "validate")

		self.assertEqual(doc.payment_reconciliation[0].closing_amount, 130)
		self.assertEqual(doc.payment_reconciliation[0].difference, 10)

	def test_commercial_summary_counts_each_settlement_once(self):
		settlements = [
			frappe._dict(
				name="SET-1",
				customer="Customer A",
				settlement_type="Partial Credit",
				total_before_manual_discount=120,
				manual_discount_total=20,
				grand_total=100,
				paid_now=40,
				credit_amount=60,
				due_date="2026-09-15",
			),
			frappe._dict(
				name="SET-2",
				customer="Customer B",
				settlement_type="House Offer",
				total_before_manual_discount=50,
				manual_discount_total=50,
				grand_total=0,
				paid_now=0,
				credit_amount=0,
			),
		]
		allocations = [
			frappe._dict(parent="SET-1", pos_invoice="PI-1"),
			frappe._dict(parent="SET-1", pos_invoice="PI-2"),
			frappe._dict(parent="SET-2", pos_invoice="PI-3"),
		]

		summary = ury_pos_closing_entry.build_commercial_summary(
			["PI-1", "PI-2", "PI-3"], settlements, allocations
		)

		self.assertEqual(summary["custom_ury_credit_sales_count"], 1)
		self.assertEqual(summary["custom_ury_credit_total"], 60)
		self.assertEqual(summary["custom_ury_discount_total"], 20)
		self.assertEqual(summary["custom_ury_house_offer_count"], 1)
		self.assertEqual(summary["custom_ury_house_offer_value"], 50)
		self.assertEqual(summary["credit_sales"][0]["pos_invoices"], "PI-1, PI-2")
		self.assertEqual(summary["discount_sales"], [])

	def test_discount_sale_rows_include_every_item_and_final_rounding(self):
		settlements = [
			frappe._dict(
				name="SET-1",
				customer="Customer A",
				settlement_type="Paid",
				reason="Autorizado pela gerência",
				total_before_manual_discount=1200,
				manual_discount_total=196,
				grand_total=1004,
				paid_now=1004,
				credit_amount=0,
			),
			frappe._dict(
				name="SET-2",
				customer="Customer B",
				settlement_type="House Offer",
				reason="Oferta autorizada",
				total_before_manual_discount=100,
				manual_discount_total=100,
				grand_total=0,
				paid_now=0,
				credit_amount=0,
			),
		]
		allocations = [
			frappe._dict(parent="SET-1", pos_invoice="PI-1"),
			frappe._dict(parent="SET-2", pos_invoice="PI-2"),
		]
		pos_invoices = [
			frappe._dict(name="PI-1", grand_total=1003.5, rounded_total=1004),
			frappe._dict(name="PI-2", grand_total=0, rounded_total=0),
		]
		invoice_items = [
			frappe._dict(
				parent="PI-1",
				item_code="CREPE",
				item_name="Crepes",
				qty=1,
				price_list_rate=350,
				rate=350,
				amount=350,
			),
			frappe._dict(
				parent="PI-1",
				item_code="ARROZ",
				item_name="Arroz de mariscos",
				qty=1,
				price_list_rate=850,
				rate=765,
				amount=765,
			),
			frappe._dict(
				parent="PI-2",
				item_code="AGUA",
				item_name="Água 500ml",
				qty=2,
				price_list_rate=50,
				rate=50,
				amount=100,
			),
		]

		summary = ury_pos_closing_entry.build_commercial_summary(
			["PI-1", "PI-2"],
			settlements,
			allocations,
			pos_invoices=pos_invoices,
			invoice_items=invoice_items,
		)

		rows = summary["discount_sales"]
		self.assertEqual(len(rows), 3)
		self.assertEqual([row["item_name"] for row in rows], ["Crepes", "Arroz de mariscos", "Água 500ml"])
		self.assertEqual([row["normal_amount"] for row in rows], [350, 850, 100])
		self.assertEqual(sum(row["charged_amount"] for row in rows[:2]), 1004)
		self.assertEqual(rows[2]["charged_amount"], 0)
		self.assertEqual(rows[2]["sale_type"], "House Offer")
		self.assertEqual(rows[0]["reason"], "Autorizado pela gerência")

	@patch("ury.ury.hooks.ury_pos_closing_entry.get_commercial_summary_data")
	def test_print_fallback_derives_rows_for_historical_closing(self, get_summary):
		get_summary.return_value = {"discount_sales": [{"pos_invoice": "PI-1"}]}
		doc = frappe._dict(
			custom_ury_discount_sales=[],
			pos_opening_entry="OPEN-1",
			pos_transactions=[frappe._dict(pos_invoice="PI-1")],
		)

		rows = ury_pos_closing_entry.get_pos_closing_discount_sales_for_print(doc)

		self.assertEqual(rows, [{"pos_invoice": "PI-1"}])
		get_summary.assert_called_once_with(["PI-1"], pos_opening_entry="OPEN-1")

	@patch("ury.ury.hooks.ury_pos_closing_entry.get_commercial_summary_data")
	def test_print_fallback_prefers_persisted_rows(self, get_summary):
		persisted = [frappe._dict(pos_invoice="PI-2")]
		doc = frappe._dict(custom_ury_discount_sales=persisted)

		self.assertIs(
			ury_pos_closing_entry.get_pos_closing_discount_sales_for_print(doc),
			persisted,
		)
		get_summary.assert_not_called()

	@patch("ury.ury.hooks.ury_pos_closing_entry.frappe.get_all")
	def test_closing_uses_the_final_rounded_invoice_totals(self, get_all):
		get_all.return_value = [
			frappe._dict(name="POL-1", grand_total=4.5, rounded_total=4),
			frappe._dict(name="POL-2", grand_total=382.5, rounded_total=382),
		]
		doc = frappe._dict(
			grand_total=0,
			pos_transactions=[
				frappe._dict(pos_invoice="POL-1", grand_total=4.5),
				frappe._dict(pos_invoice="POL-2", grand_total=382.5),
			],
		)

		ury_pos_closing_entry.apply_rounded_invoice_totals(doc)

		self.assertEqual(doc.grand_total, 386)
		self.assertEqual([row.grand_total for row in doc.pos_transactions], [4, 382])

	def test_zero_rounded_total_does_not_make_a_nonzero_sale_free(self):
		invoice = frappe._dict(grand_total=0.4, rounded_total=0)

		self.assertEqual(ury_pos_closing_entry._settled_invoice_total(invoice), 0.4)

	@patch("ury.ury.hooks.ury_pos_closing_entry.frappe.db.sql")
	@patch("ury.ury.hooks.ury_pos_closing_entry.frappe.db.get_value")
	@patch(
		"ury.ury.hooks.ury_pos_closing_entry.has_settlement_global_access",
		return_value=True,
	)
	def test_ury_manager_can_preview_commercial_summary_without_branch_row(
		self, _global_access, get_value, db_sql
	):
		get_value.return_value = frappe._dict(
			branch="Polana",
			pos_profile="POS Polana",
			owner="cashier@example.com",
		)

		ury_pos_closing_entry._validate_summary_access(["PI-1"], None)

		db_sql.assert_not_called()


def _closing(payment_reconciliation, justification=""):
	return frappe._dict(
		pos_profile="POS Test",
		posting_date="2026-08-13",
		period_start_date="2026-08-13 07:00:00",
		payment_reconciliation=payment_reconciliation,
		custom_difference_justification=justification,
	)


def _rows(*rows):
	return list(rows)


def _row(
	mode_of_payment,
	*,
	expected,
	closing,
	difference=0,
	custom_closing_amount=None,
):
	return frappe._dict(
		mode_of_payment=mode_of_payment,
		expected_amount=expected,
		closing_amount=closing,
		difference=difference,
		custom_closing_amount=custom_closing_amount,
	)


def run_unit_tests():
	"""Run the mock-only POS Closing Entry hook suite through bench execute."""
	if getattr(frappe.local, "flags", None) is None:
		frappe.local.flags = frappe._dict()
	stream = io.StringIO()
	suite = defaultTestLoader.loadTestsFromNames(
		[
			__name__,
			"ury.ury.test_pos_closing_reconciliation",
			"ury.ury.printing.test_pos_closing_format",
		]
	)
	result = TextTestRunner(stream=stream, verbosity=2).run(suite)
	if not result.wasSuccessful():
		raise AssertionError(stream.getvalue())
	return {"tests_run": result.testsRun, "successful": True}
