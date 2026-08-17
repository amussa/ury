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
		self.assertIn("custom_ury_credit_sales", HTML)
		self.assertIn("custom_ury_discount_total", HTML)
		self.assertIn("custom_ury_discount_sales", HTML)
		self.assertIn("get_pos_closing_discount_sales_for_print", HTML)
		self.assertIn("OFERTAS DA CASA", HTML.upper())
		self.assertIn("VENDAS COM DESCONTO/OFERTA", HTML)
		self.assertIn("item.item_name", HTML)
		self.assertIn("item.normal_amount", HTML)
		self.assertIn("item.charged_amount", HTML)
		self.assertIn("Crédito não é valor recebido em caixa", HTML)
		self.assertIn("precision=0", HTML)
		self.assertIn("Arredondamento", HTML)
