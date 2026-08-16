import re
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ury.ury.printing import pos_receipt_format
from ury.ury.printing.pos_receipt_format import (
	HTML,
	HTML_FORMAT,
	RAW_COMMANDS,
	RAW_FORMAT,
	install_pos_receipt_formats,
)


class _Receipt(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)

	def as_dict(self):
		def convert(value):
			if isinstance(value, SimpleNamespace):
				return {key: convert(item) for key, item in vars(value).items()}
			if isinstance(value, list):
				return [convert(item) for item in value]
			return value

		return convert(self)


class TestPOSReceiptFormat(TestCase):
	def test_templates_compile_and_remain_ury_owned(self):
		frappe.get_jenv().from_string(HTML)
		frappe.get_jenv().from_string(RAW_COMMANDS)

		self.assertEqual(HTML_FORMAT, "Gelatiamo Recibo 80mm")
		self.assertEqual(RAW_FORMAT, "Gelatiamo Recibo ESC/POS")
		for template in (HTML, RAW_COMMANDS):
			self.assertIn("OFERTA DA CASA", template)
			self.assertIn("Desconto manual", template)
			self.assertIn("Pago agora", template)
			self.assertIn("Saldo concedido", template)
			self.assertIn("Vencimento", template)

	def test_house_offer_discount_and_credit_render_in_both_formats(self):
		cases = (
			(
				self._document(
					custom_ury_settlement_type="House Offer",
					custom_ury_manual_discount_total=500,
					grand_total=0,
					rounded_total=0,
					net_total=0,
					payments=[],
				),
				("OFERTA DA CASA", "Sem pagamento - oferta da casa"),
			),
			(
				self._document(custom_ury_manual_discount_total=50),
				("Desconto manual", "PAGAMENTO"),
			),
			(
				self._document(
					custom_ury_settlement_type="Partial Credit",
					custom_ury_credit_amount=300,
					custom_ury_credit_due_date="2026-09-18",
					payments=[SimpleNamespace(mode_of_payment="Numerário", amount=150)],
				),
				("Cliente Teste", "Pago agora", "Saldo concedido", "18-09-2026"),
			),
			(
				self._document(
					custom_ury_settlement_type="Full Credit",
					custom_ury_credit_amount=450,
					custom_ury_credit_due_date="2026-09-18",
					payments=[],
				),
				("Pago agora", "Saldo concedido", "Sem pagamento recebido agora"),
			),
			(
				self._document(docstatus=0),
				("Pagamento: pendente",),
			),
			(
				self._document(
					docstatus=0,
					items=[SimpleNamespace(
						item_name="Fatia de bolo",
						qty=2,
						rate=135,
						amount=270,
						custom_ury_rate_before_manual_discount=150,
						custom_ury_manual_discount_amount=30,
					)],
				),
				("Desconto artigo", "Pagamento: pendente"),
			),
			(
				self._document(
					name="POS-MIXED",
					grand_total=175,
					rounded_total=175,
					payments=[
						SimpleNamespace(mode_of_payment="Numerário", amount=100),
						SimpleNamespace(mode_of_payment="M-Pesa", amount=50),
						SimpleNamespace(mode_of_payment="Numerário", amount=25),
					],
				),
				("Numerário", "M-Pesa"),
			),
		)

		for template in (HTML, RAW_COMMANDS):
			for document, expected_fragments in cases:
				output = frappe.render_template(template, {"doc": document})
				plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", output))
				for fragment in expected_fragments:
					self.assertIn(fragment, plain)
				if document.custom_ury_settlement_type == "House Offer":
					self.assertRegex(
						plain,
						rf"Valor oferecido\s*:?[ ]*{re.escape(frappe.utils.fmt_money(500, currency=None))}",
					)
				if document.name == "POS-MIXED":
					self.assertRegex(
						plain,
						rf"Numerário\s+{re.escape(frappe.utils.fmt_money(125, currency=None))}",
					)
				if document.docstatus == 0:
					self.assertNotIn("PAGAMENTO", plain)

	def test_commercial_merge_uses_live_partner_totals(self):
		merged = self._document(
			name="POS-TEST-0002",
			custom_merged_pos_invoice="POS-TEST-0001",
			custom_ury_settlement="URY-SET-TEST",
			custom_ury_settlement_type="Partial Credit",
			custom_ury_manual_discount_total=20,
			custom_ury_credit_amount=200,
			custom_ury_credit_due_date="2026-09-18",
			grand_total=250,
			rounded_total=250,
			net_total=225,
			taxes=[SimpleNamespace(description="IVA parceiro", tax_amount=25)],
			payments=[SimpleNamespace(mode_of_payment="Numerário", amount=55)],
			change_amount=5,
			items=[SimpleNamespace(item_name="Parceiro actualizado", qty=1, rate=225, amount=225)],
		)
		merged_view = frappe._dict(merged.as_dict())
		template_frappe = SimpleNamespace(
			utils=frappe.utils,
			get_doc=lambda *_args, **_kwargs: merged_view,
		)
		main = self._document(
			custom_merged_pos_invoice=merged.name,
			custom_merged_total=999,
			custom_merged_pos_invoice_details=[
				SimpleNamespace(item_name="Snapshot antigo", qty=1, rate=999, amount=999)
			],
			custom_ury_settlement="URY-SET-TEST",
			custom_ury_settlement_type="Partial Credit",
			custom_ury_manual_discount_total=30,
			custom_ury_credit_amount=100,
			custom_ury_credit_due_date="2026-09-18",
			grand_total=250,
			rounded_total=250,
			net_total=250,
			payments=[SimpleNamespace(mode_of_payment="Numerário", amount=160)],
			change_amount=10,
		)

		for template in (HTML, RAW_COMMANDS):
			output = frappe.render_template(
				template,
				{"doc": main, "frappe": template_frappe},
			)
			plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", output))
			self.assertIn("Parceiro actualizado", plain)
			self.assertIn("IVA parceiro", plain)
			self.assertNotIn("Snapshot antigo", plain)
			self.assertNotIn(frappe.utils.fmt_money(999, currency=None), plain)
			self.assertRegex(
				plain,
				rf"Desconto manual\s+-{re.escape(frappe.utils.fmt_money(50, currency=None))}",
			)
			self.assertRegex(
				plain,
				rf"TOTAL MZN\s+{re.escape(frappe.utils.fmt_money(500, currency=None))}",
			)
			self.assertRegex(
				plain,
				rf"Pago agora\s+{re.escape(frappe.utils.fmt_money(200, currency=None))}",
			)
			self.assertRegex(
				plain,
				rf"Saldo concedido\s+{re.escape(frappe.utils.fmt_money(300, currency=None))}",
			)
			self.assertRegex(
				plain,
				rf"Troco\s+{re.escape(frappe.utils.fmt_money(15, currency=None))}",
			)
			self.assertIn("18-09-2026", plain)

	def test_legacy_merge_keeps_the_stored_receipt_snapshot(self):
		merged = self._document(
			name="POS-LEGACY-0002",
			items=[SimpleNamespace(item_name="Parceiro vivo", qty=1, rate=777, amount=777)],
			grand_total=777,
			rounded_total=777,
		)
		template_frappe = SimpleNamespace(
			utils=frappe.utils,
			get_doc=lambda *_args, **_kwargs: frappe._dict(merged.as_dict()),
		)
		main = self._document(
			grand_total=100,
			rounded_total=100,
			custom_merged_pos_invoice=merged.name,
			custom_merged_total=80,
			custom_merged_pos_invoice_details=[
				SimpleNamespace(item_name="Snapshot legado", qty=1, rate=80, amount=80)
			],
		)

		for template in (HTML, RAW_COMMANDS):
			output = frappe.render_template(
				template,
				{"doc": main, "frappe": template_frappe},
			)
			plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", output))
			self.assertIn("Snapshot legado", plain)
			self.assertNotIn("Parceiro vivo", plain)
			self.assertRegex(
				plain,
				rf"Total conta junta\s+{re.escape(frappe.utils.fmt_money(80, currency=None))}",
			)
			self.assertRegex(
				plain,
				rf"TOTAL MZN\s+{re.escape(frappe.utils.fmt_money(180, currency=None))}",
			)

	def test_installer_updates_both_formats_idempotently(self):
		documents = {}
		html_doc = SimpleNamespace(
			name=HTML_FORMAT,
			update=Mock(),
			save=Mock(),
			insert=Mock(side_effect=lambda **_kwargs: documents.setdefault(HTML_FORMAT, html_doc)),
		)
		raw_doc = SimpleNamespace(
			name=RAW_FORMAT,
			update=Mock(),
			save=Mock(),
			insert=Mock(side_effect=lambda **_kwargs: documents.setdefault(RAW_FORMAT, raw_doc)),
		)
		new_documents = {HTML_FORMAT: html_doc, RAW_FORMAT: raw_doc}
		created_values = {}

		def get_doc(*args):
			if len(args) == 1:
				values = args[0]
				created_values[values["name"]] = values
				return new_documents[values["name"]]
			return documents[args[1]]

		fake_db = SimpleNamespace(
			exists=Mock(side_effect=lambda _doctype, name: name in documents),
		)
		with (
			patch.object(pos_receipt_format.frappe, "db", fake_db),
			patch.object(pos_receipt_format.frappe, "get_doc", side_effect=get_doc),
		):
			self.assertEqual(
				install_pos_receipt_formats(),
				(HTML_FORMAT, RAW_FORMAT),
			)
			self.assertEqual(
				install_pos_receipt_formats(),
				(HTML_FORMAT, RAW_FORMAT),
			)

		self.assertEqual(html_doc.insert.call_count, 1)
		self.assertEqual(raw_doc.insert.call_count, 1)
		self.assertEqual(html_doc.save.call_count, 1)
		self.assertEqual(raw_doc.save.call_count, 1)
		self.assertEqual(created_values[HTML_FORMAT]["module"], "URY")
		self.assertEqual(created_values[RAW_FORMAT]["module"], "URY")
		self.assertEqual(html_doc.update.call_args.args[0]["module"], "URY")
		self.assertEqual(html_doc.update.call_args.args[0]["html"], HTML)
		self.assertEqual(raw_doc.update.call_args.args[0]["raw_commands"], RAW_COMMANDS)

	@staticmethod
	def _document(**overrides):
		values = {
			"name": "POS-TEST-0001",
			"branch": "Polana",
			"restaurant_table": None,
			"order_type": "Take Away",
			"posting_date": "2026-08-16",
			"posting_time": "12:34:00",
			"customer": "CUST-TEST",
			"customer_name": "Cliente Teste",
			"cashier": "Caixa Teste",
			"items": [SimpleNamespace(item_name="Gelado", qty=1, rate=500, amount=500)],
			"taxes": [],
			"net_total": 500,
			"discount_amount": 0,
			"rounded_total": 450,
			"grand_total": 450,
			"currency": "MZN",
			"docstatus": 1,
			"payments": [SimpleNamespace(mode_of_payment="Numerário", amount=450)],
			"change_amount": 0,
			"custom_merged_pos_invoice": None,
			"custom_merged_pos_invoice_details": [],
			"custom_merged_total": 0,
			"custom_ury_settlement": None,
			"custom_ury_settlement_type": "Paid",
			"custom_ury_manual_discount_total": 0,
			"custom_ury_credit_amount": 0,
			"custom_ury_credit_due_date": None,
		}
		values.update(overrides)
		for fieldname in ("items", "custom_merged_pos_invoice_details"):
			values[fieldname] = [
				item if isinstance(item, _Receipt) else _Receipt(**vars(item))
				for item in values.get(fieldname, [])
			]
		return _Receipt(**values)
