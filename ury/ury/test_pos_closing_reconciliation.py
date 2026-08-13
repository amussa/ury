from unittest import TestCase

from ury.ury.pos_closing_reconciliation import _classification


class TestPOSClosingReconciliation(TestCase):
	def test_nonzero_custom_count_is_repairable(self):
		self.assertEqual(
			_classification(
				{
					"expected_amount": 14060,
					"custom_closing_amount": 11330,
					"closing_amount": 14060,
					"difference": 0,
				}
			),
			"repairable",
		)

	def test_zero_custom_value_that_was_not_required_is_ambiguous(self):
		self.assertEqual(
			_classification(
				{
					"expected_amount": 7620,
					"custom_closing_amount": 0,
					"closing_amount": 7620,
					"difference": 0,
				}
			),
			"ambiguous",
		)

	def test_matching_values_are_consistent(self):
		self.assertEqual(
			_classification(
				{
					"expected_amount": 100,
					"custom_closing_amount": 100,
					"closing_amount": 100,
					"difference": 0,
				}
			),
			"consistent",
		)
