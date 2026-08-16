from __future__ import annotations

import frappe


PRINT_FORMAT_NAME = "Gelatiamo Fecho de Caixa 80mm"

HTML = r"""
<style>
  @page { size: 80mm auto; margin: 0; }
  html, body { margin: 0 !important; padding: 0 !important; }
  .print-format {
    box-sizing: border-box;
    width: 72mm;
    max-width: 72mm;
    margin: 0 auto;
    padding: 2mm;
    color: #000;
    background: #fff;
    font-family: "DejaVu Sans Mono", "Liberation Mono", Consolas, monospace;
    font-size: 10.5px;
    font-weight: 700;
    line-height: 1.32;
  }
  .print-format * {
    box-sizing: border-box;
    color: #000 !important;
    background: transparent !important;
  }
  .t-center { text-align: center; }
  .t-right { text-align: right; }
  .t-brand { font-family: Arial, sans-serif; font-size: 20px; font-weight: 900; letter-spacing: 1px; line-height: 1.05; }
  .t-title { margin-top: 1mm; font-family: Arial, sans-serif; font-size: 14px; font-weight: 900; letter-spacing: .7px; }
  .t-status { margin-top: .5mm; font-size: 9px; text-transform: uppercase; }
  .t-rule { border-top: 2px solid #000; margin: 1.8mm 0; }
  .t-rule-thin { border-top: 1px solid #000; margin: 1.2mm 0; }
  .t-section { margin: 1.7mm 0 .7mm; font-family: Arial, sans-serif; font-size: 10px; font-weight: 900; letter-spacing: .3px; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  td, th { padding: .35mm 0; vertical-align: top; }
  .meta td:first-child { width: 31%; }
  .meta td:last-child { width: 69%; overflow-wrap: anywhere; }
  .summary td:first-child { width: 58%; }
  .summary td:last-child { width: 42%; text-align: right; white-space: nowrap; }
  .total { font-family: Arial, sans-serif; font-size: 15px; font-weight: 900; }
  .payments { font-size: 8.7px; line-height: 1.18; }
  .payments th { border-bottom: 1px solid #000; font-size: 7.8px; text-align: right; white-space: nowrap; }
  .payments th:first-child { width: 25%; text-align: left; }
  .payments th:nth-child(2) { width: 27%; }
  .payments th:nth-child(3) { width: 27%; }
  .payments th:nth-child(4) { width: 21%; }
  .payments td { border-bottom: 1px solid #000; text-align: right; white-space: nowrap; }
  .payments td:first-child { text-align: left; white-space: normal; overflow-wrap: anywhere; }
  .payments .mismatch { font-weight: 900; }
  .difference-label { display: block; font-size: 7px; text-transform: uppercase; }
  .justification { border: 1px solid #000; margin-top: 1.5mm; padding: 1.2mm; font-size: 8.5px; overflow-wrap: anywhere; }
  .credit-entry { border: 1px solid #000; margin-top: 1mm; padding: 1mm; font-size: 8.2px; overflow-wrap: anywhere; }
  .credit-entry .credit-title { font-family: Arial, sans-serif; font-size: 9px; font-weight: 900; }
  .credit-entry table td:first-child { width: 45%; }
  .credit-entry table td:last-child { width: 55%; text-align: right; }
  .note { margin-top: .8mm; font-size: 8.2px; line-height: 1.25; }
  .sign { margin-top: 5mm; }
  .sign-line { border-top: 1px solid #000; margin-top: 5mm; padding-top: .8mm; text-align: center; font-size: 9px; }
  .footer { margin-top: 2mm; font-size: 8px; text-align: center; }
</style>

{% set branch = frappe.db.get_value("POS Profile", doc.pos_profile, "branch") or doc.pos_profile %}
{% set cashier = frappe.db.get_value("User", doc.user, "full_name") or doc.user %}
{% set currency = frappe.db.get_value("Company", doc.company, "default_currency") or "MZN" %}
{% set sale_count = doc.pos_transactions|length %}
{% set opening_total = doc.payment_reconciliation|sum(attribute="opening_amount") %}
{% set expected_total = doc.payment_reconciliation|sum(attribute="expected_amount") %}
{% set counted_total = doc.payment_reconciliation|sum(attribute="closing_amount") %}
{% set difference_total = doc.payment_reconciliation|sum(attribute="difference") %}
{% set credit_rows = doc.custom_ury_credit_sales or [] %}
{% set reconciliation = namespace(has_difference=false) %}
{% for row in doc.payment_reconciliation %}
  {% if row.difference %}{% set reconciliation.has_difference = true %}{% endif %}
{% endfor %}

<div class="t-center t-brand">GELATIAMO</div>
<div class="t-center t-title">FECHO DE CAIXA</div>
<div class="t-center t-status">
  {% if doc.docstatus == 1 %}Submetido{% elif doc.docstatus == 2 %}Cancelado{% else %}Rascunho{% endif %}
  &nbsp;-&nbsp; {{ doc.name }}
</div>

<div class="t-rule"></div>

<table class="meta">
  <tr><td>Loja</td><td>{{ branch }}</td></tr>
  <tr><td>Perfil POS</td><td>{{ doc.pos_profile }}</td></tr>
  <tr><td>Operador</td><td>{{ cashier }}</td></tr>
  <tr><td>Abertura</td><td>{{ frappe.utils.format_datetime(doc.period_start_date, "dd-MM-yyyy HH:mm") }}</td></tr>
  <tr><td>Fecho</td><td>{{ frappe.utils.format_datetime(doc.period_end_date, "dd-MM-yyyy HH:mm") }}</td></tr>
</table>

<div class="t-rule"></div>
<div class="t-section">MOVIMENTO DO TURNO</div>
<table class="summary">
  <tr><td>Número de vendas</td><td>{{ sale_count }}</td></tr>
  <tr><td>Artigos vendidos</td><td>{{ doc.total_quantity or 0 }}</td></tr>
  <tr><td>Venda média</td><td>{% if sale_count %}{{ frappe.utils.fmt_money((doc.grand_total or 0) / sale_count, currency=None) }}{% else %}0.00{% endif %}</td></tr>
</table>

<div class="t-rule-thin"></div>
<div class="t-section">RESUMO FINANCEIRO - {{ currency }}</div>
<table class="summary">
  <tr><td>Vendas líquidas</td><td>{{ frappe.utils.fmt_money(doc.net_total or 0, currency=None) }}</td></tr>
  {% for tax in doc.taxes %}
  <tr><td>{{ tax.account_head }} ({{ tax.rate }}%)</td><td>{{ frappe.utils.fmt_money(tax.amount or 0, currency=None) }}</td></tr>
  {% endfor %}
</table>

<div class="t-rule"></div>
<table class="summary total">
  <tr><td>TOTAL VENDAS</td><td>{{ frappe.utils.fmt_money(doc.grand_total or 0, currency=None) }}</td></tr>
</table>

{% if doc.custom_ury_discount_total or doc.custom_ury_house_offer_count or doc.custom_ury_credit_sales_count %}
<div class="t-rule-thin"></div>
<div class="t-section">DESCONTOS, OFERTAS E CRÉDITO</div>
<table class="summary">
  <tr><td>Descontos manuais</td><td>{{ frappe.utils.fmt_money(doc.custom_ury_discount_total or 0, currency=None) }}</td></tr>
  <tr><td>Ofertas da casa</td><td>{{ doc.custom_ury_house_offer_count or 0 }}</td></tr>
  <tr><td>Valor oferecido</td><td>{{ frappe.utils.fmt_money(doc.custom_ury_house_offer_value or 0, currency=None) }}</td></tr>
  <tr><td>Vendas a crédito</td><td>{{ doc.custom_ury_credit_sales_count or 0 }}</td></tr>
  <tr><td>Saldo a crédito</td><td>{{ frappe.utils.fmt_money(doc.custom_ury_credit_total or 0, currency=None) }}</td></tr>
</table>
{% endif %}

{% if credit_rows %}
<div class="t-section">VENDAS A CRÉDITO</div>
{% for credit in credit_rows %}
<div class="credit-entry">
  <div class="credit-title">{{ credit.customer }}</div>
  <div>POS: {{ credit.pos_invoices }}</div>
  <table>
    <tr><td>Total</td><td>{{ frappe.utils.fmt_money(credit.total_final or 0, currency=None) }}</td></tr>
    <tr><td>Pago agora</td><td>{{ frappe.utils.fmt_money(credit.paid_now or 0, currency=None) }}</td></tr>
    <tr><td>Saldo</td><td>{{ frappe.utils.fmt_money(credit.credit_amount or 0, currency=None) }}</td></tr>
    <tr><td>Vencimento</td><td>{{ frappe.utils.formatdate(credit.due_date, "dd-MM-yyyy") }}</td></tr>
    {% if credit.sales_invoice %}<tr><td>Factura</td><td>{{ credit.sales_invoice }}</td></tr>{% endif %}
  </table>
</div>
{% endfor %}
<div class="note">Crédito não é valor recebido em caixa.</div>
{% endif %}

<div class="t-rule"></div>
<div class="t-section">RECONCILIAÇÃO DE PAGAMENTOS</div>
<table class="payments">
  <thead>
    <tr><th>Modo</th><th>Sistema</th><th>Contado</th><th>Dif.</th></tr>
  </thead>
  <tbody>
    {% for row in doc.payment_reconciliation %}
    <tr>
      <td>{{ row.mode_of_payment }}</td>
      <td>{{ frappe.utils.fmt_money(row.expected_amount or 0, currency=None) }}</td>
      <td>{{ frappe.utils.fmt_money(row.closing_amount or 0, currency=None) }}</td>
      <td{% if row.difference %} class="mismatch"{% endif %}>
        {% if row.difference and row.difference > 0 %}+{% endif %}{{ frappe.utils.fmt_money(row.difference or 0, currency=None) }}
        {% if row.difference and row.difference < 0 %}<span class="difference-label">Falta</span>{% elif row.difference and row.difference > 0 %}<span class="difference-label">Excesso</span>{% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>

<div class="t-rule-thin"></div>
<table class="summary">
  <tr><td>Fundo inicial</td><td>{{ frappe.utils.fmt_money(opening_total or 0, currency=None) }}</td></tr>
  <tr><td>Total no sistema</td><td>{{ frappe.utils.fmt_money(expected_total or 0, currency=None) }}</td></tr>
  <tr><td>Total contado</td><td>{{ frappe.utils.fmt_money(counted_total or 0, currency=None) }}</td></tr>
  <tr class="{% if difference_total %}mismatch{% endif %}"><td>Diferença total</td><td>{% if difference_total and difference_total > 0 %}+{% endif %}{{ frappe.utils.fmt_money(difference_total or 0, currency=None) }}</td></tr>
</table>

{% if reconciliation.has_difference %}
<div class="t-section">JUSTIFICAÇÃO DA DIFERENÇA</div>
<div class="justification">{{ doc.custom_difference_justification or "Não registada no fecho original." }}</div>
{% endif %}

<div class="note">Sistema = fundo inicial + pagamentos efectivamente registados no turno.</div>

<div class="sign">
  <div class="sign-line">Conferido por</div>
  <div class="sign-line">Assinatura</div>
</div>

<div class="t-rule-thin"></div>
<div class="footer">
  {{ doc.company }} - {{ doc.pos_opening_entry }}<br>
  Impresso em {{ frappe.utils.format_datetime(frappe.utils.now_datetime(), "dd-MM-yyyy HH:mm") }}
</div>
"""


def install_pos_closing_print_format() -> str:
	values = {
		"print_format_for": "DocType",
		"doc_type": "POS Closing Entry",
		"module": "URY",
		"standard": "No",
		"custom_format": 1,
		"disabled": 0,
		"pdf_generator": "wkhtmltopdf",
		"print_format_type": "Jinja",
		"raw_printing": 0,
		"html": HTML,
		"margin_top": 0,
		"margin_bottom": 0,
		"margin_left": 0,
		"margin_right": 0,
		"page_number": "Hide",
	}
	if frappe.db.exists("Print Format", PRINT_FORMAT_NAME):
		doc = frappe.get_doc("Print Format", PRINT_FORMAT_NAME)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{"doctype": "Print Format", "name": PRINT_FORMAT_NAME, **values}
		)
		doc.insert(ignore_permissions=True)
	return doc.name
