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
	suite = defaultTestLoader.loadTestsFromModule(__import__(__name__, fromlist=["*"]))
	result = TextTestRunner(stream=stream, verbosity=2).run(suite)
	if not result.wasSuccessful():
		raise AssertionError(stream.getvalue())
	return {"tests_run": result.testsRun, "successful": True}
