from unittest import TestCase

import frappe

from ury.ury.printing.pos_closing_format import HTML


class TestPOSClosingPrintFormat(TestCase):
	def test_template_compiles_and_contains_reconciliation_controls(self):
		frappe.get_jenv().from_string(HTML)
		self.assertIn("row.closing_amount", HTML)
		self.assertIn("row.difference", HTML)
		self.assertIn("custom_difference_justification", HTML)
		self.assertIn("Falta", HTML)
		self.assertIn("Excesso", HTML)
