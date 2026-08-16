from __future__ import annotations

import frappe

DOCTYPE = "POS Invoice"

# Keep the operational names installed by the historical one-off script so
# existing POS Profiles continue to pick up the URY-managed templates.
HTML_FORMAT = "Gelatiamo Recibo 80mm"
RAW_FORMAT = "Gelatiamo Recibo ESC/POS"

ESC = "\x1b"
GS = "\x1d"

INIT = ESC + "@"
CODEPAGE_PC860 = ESC + "t" + "\x03"
ALIGN_LEFT = ESC + "a" + "\x00"
ALIGN_CENTER = ESC + "a" + "\x01"
SIZE_NORMAL = ESC + "!" + "\x00"
SIZE_DOUBLE = ESC + "!" + "\x30"
BOLD_ON = ESC + "E" + "\x01"
BOLD_OFF = ESC + "E" + "\x00"
FEED_AND_CUT = ESC + "d" + "\x04" + GS + "V" + "\x42" + "\x00"

COLUMNS = 48

RAW_COMMANDS = (
	INIT
	+ CODEPAGE_PC860
	+ """{%- set W = """
	+ str(COLUMNS)
	+ """ %}
{%- set AMOUNT_COL = 14 %}
{%- set sep = "=" * W %}
{%- set thin = "-" * W %}
{%- set merged_invoice = frappe.get_doc("POS Invoice", doc.custom_merged_pos_invoice) if doc.custom_merged_pos_invoice else none %}
{%- set is_commercial_merge = merged_invoice and doc.get("custom_ury_settlement") and merged_invoice.get("custom_ury_settlement") == doc.get("custom_ury_settlement") %}
{%- set merged_items = merged_invoice.get("items") if is_commercial_merge else doc.custom_merged_pos_invoice_details %}
{%- set merged_total = (merged_invoice.rounded_total or merged_invoice.grand_total or 0) if is_commercial_merge else (doc.custom_merged_total or 0) %}
{%- set merged_subtotal = merged_invoice.net_total if is_commercial_merge else merged_total %}
{%- set merged_taxes = merged_invoice.get("taxes") if is_commercial_merge else [] %}
{%- set settlement_type = doc.get("custom_ury_settlement_type") or "" %}
{%- set manual_discount = (doc.get("custom_ury_manual_discount_total") or 0) + ((merged_invoice.get("custom_ury_manual_discount_total") or 0) if is_commercial_merge else 0) %}
{%- set credit_amount = (doc.get("custom_ury_credit_amount") or 0) + ((merged_invoice.get("custom_ury_credit_amount") or 0) if is_commercial_merge else 0) %}
{%- set credit_due_date = doc.get("custom_ury_credit_due_date") or (merged_invoice.get("custom_ury_credit_due_date") if is_commercial_merge else none) %}
{%- set receipt_total = (doc.rounded_total or doc.grand_total or 0) + merged_total %}
{%- set is_house_offer = settlement_type == "House Offer" %}
{%- set is_credit = credit_amount > 0 or settlement_type in ("Partial Credit", "Full Credit") %}
{%- macro money(value) -%}
{{ frappe.utils.fmt_money(value or 0, currency=None) }}
{%- endmacro -%}
{%- macro qty(value) -%}
{{ (value|round(3)|string).rstrip("0").rstrip(".") }}
{%- endmacro -%}
{%- macro row(label, value) -%}
{{ label.ljust(W - AMOUNT_COL) }}{{ value.rjust(AMOUNT_COL) }}
{%- endmacro -%}
"""
	+ ALIGN_CENTER
	+ SIZE_DOUBLE
	+ """GELATIAMO
"""
	+ SIZE_NORMAL
	+ """{{ doc.branch or "" }}
{% if doc.restaurant_table %}Mesa: {{ doc.restaurant_table }}{% else %}{{ doc.order_type or "" }}{% endif %}
"""
	+ ALIGN_LEFT
	+ """{{ sep }}
Factura: {{ doc.name }}
Data:    {{ frappe.utils.formatdate(doc.posting_date, "dd-MM-yyyy") }} {{ frappe.utils.format_time(doc.posting_time, "HH:mm") }}
{%- if doc.customer_name or doc.customer %}
Cliente: {{ (doc.customer_name or doc.customer)[:W - 9] }}
{%- endif %}
{%- if doc.cashier %}
Caixa:   {{ doc.cashier[:W - 9] }}
{%- endif %}
{{ thin }}
{%- for item in doc.items %}
{{ (item.item_name or "")[:W] }}
{{ row("  " ~ qty(item.qty) ~ " x " ~ money(item.rate), money(item.amount)) }}
{%- endfor %}
{{ thin }}
{{ row("Subtotal", money(doc.net_total)) }}
{%- for tax in doc.taxes %}
{%- if tax.tax_amount %}
{{ row((tax.description or "")[:W - AMOUNT_COL - 1], money(tax.tax_amount)) }}
{%- endif %}
{%- endfor %}
{%- if doc.custom_merged_pos_invoice %}
{{ thin }}
Conta junta: {{ doc.custom_merged_pos_invoice }}
{%- for item in merged_items %}
{{ (item.item_name or "")[:W] }}
{{ row("  " ~ qty(item.qty) ~ " x " ~ money(item.rate), money(item.amount)) }}
{%- endfor %}
{{ row("Subtotal conta junta" if is_commercial_merge else "Total conta junta", money(merged_subtotal)) }}
{%- for tax in merged_taxes %}
{%- if tax.tax_amount %}
{{ row((tax.description or "")[:W - AMOUNT_COL - 1], money(tax.tax_amount)) }}
{%- endif %}
{%- endfor %}
{%- endif %}
{%- if manual_discount and not is_house_offer %}
{{ row("Antes desconto", money(receipt_total + manual_discount)) }}
{{ row("Desconto manual", "-" ~ money(manual_discount)) }}
{%- elif doc.discount_amount and not is_house_offer %}
{{ row("Desconto", "-" ~ money(doc.discount_amount)) }}
{%- endif %}
{{ sep }}
"""
	+ BOLD_ON
	+ """{%- if is_house_offer %}
"""
	+ ALIGN_CENTER
	+ SIZE_DOUBLE
	+ """OFERTA DA CASA
"""
	+ SIZE_NORMAL
	+ ALIGN_LEFT
	+ """{{ row("Valor oferecido", money(manual_discount)) }}
{%- endif %}
{{ row("TOTAL " ~ (doc.currency or ""), money(receipt_total)) }}
"""
	+ BOLD_OFF
	+ """{%- set payment_rows = doc.payments if doc.docstatus == 1 else [] %}
{%- if doc.docstatus == 1 and merged_invoice %}
{%- set payment_rows = payment_rows + merged_invoice.payments %}
{%- endif %}
{%- set change_total = (doc.change_amount or 0) + ((merged_invoice.change_amount or 0) if merged_invoice else 0) %}
{%- set paid_now = (payment_rows|sum(attribute="amount")) - change_total %}
{{ thin }}
{%- if is_credit and doc.docstatus == 1 %}
"""
	+ BOLD_ON
	+ """CREDITO
"""
	+ BOLD_OFF
	+ """Cliente: {{ (doc.customer_name or doc.customer or "")[:W - 9] }}
{{ row("Pago agora", money(paid_now)) }}
{{ row("Saldo concedido", money(credit_amount)) }}
Vencimento: {{ frappe.utils.formatdate(credit_due_date, "dd-MM-yyyy") if credit_due_date else "-" }}
{{ thin }}
{%- endif %}
{%- if is_house_offer and doc.docstatus == 1 %}
Sem pagamento - oferta da casa
{%- elif doc.docstatus == 1 and (not is_credit or paid_now) %}
PAGAMENTO
{%- for payment_group in payment_rows|groupby("mode_of_payment") %}
{%- set payment_total = payment_group.list|sum(attribute="amount") %}
{%- if payment_total %}
{{ row((payment_group.grouper or "")[:W - AMOUNT_COL - 1], money(payment_total)) }}
{%- endif %}
{%- endfor %}
{%- if change_total %}
{{ row("Troco", money(change_total)) }}
{%- endif %}
{%- elif is_credit and doc.docstatus == 1 %}
Sem pagamento recebido agora
{%- else %}
Pagamento: pendente
{%- endif %}
{{ sep }}
"""
	+ ALIGN_CENTER
	+ """Obrigado pela preferencia!
"""
	+ ALIGN_LEFT
	+ FEED_AND_CUT
)

HTML = r"""
<style>
  @page { margin: 0; size: 72mm auto; }
  .print-format {
    width: 72mm;
    margin: 0;
    padding: 2mm;
    font-family: "DejaVu Sans Mono", "Liberation Mono", Consolas, monospace;
    font-size: 12px;
    font-weight: 700;
    line-height: 1.35;
    color: #000;
  }
  .print-format * { color: #000 !important; background: transparent !important; }
  .t-center { text-align: center; }
  .t-right { text-align: right; }
  .t-name { font-size: 20px; font-weight: 700; }
  .t-rule { border-top: 2px solid #000; margin: 1.5mm 0; }
  .t-total { font-size: 16px; font-weight: 700; }
  .t-callout { border: 3px solid #000; margin: 2mm 0; padding: 1.5mm; }
  .t-callout-title { font-family: Arial, sans-serif; font-size: 18px; font-weight: 900; letter-spacing: .5px; }
  .t-credit { border: 2px solid #000; margin: 1.5mm 0; padding: 1.2mm; }
  .t-credit-title { font-family: Arial, sans-serif; font-size: 13px; font-weight: 900; }
  table { width: 100%; border-collapse: collapse; }
  td { padding: 0; vertical-align: top; color: #000; }
</style>

{% set merged_invoice = frappe.get_doc("POS Invoice", doc.custom_merged_pos_invoice) if doc.custom_merged_pos_invoice else none %}
{% set is_commercial_merge = merged_invoice and doc.get("custom_ury_settlement") and merged_invoice.get("custom_ury_settlement") == doc.get("custom_ury_settlement") %}
{% set merged_items = merged_invoice.get("items") if is_commercial_merge else doc.custom_merged_pos_invoice_details %}
{% set merged_total = (merged_invoice.rounded_total or merged_invoice.grand_total or 0) if is_commercial_merge else (doc.custom_merged_total or 0) %}
{% set merged_subtotal = merged_invoice.net_total if is_commercial_merge else merged_total %}
{% set merged_taxes = merged_invoice.get("taxes") if is_commercial_merge else [] %}
{% set settlement_type = doc.get("custom_ury_settlement_type") or "" %}
{% set manual_discount = (doc.get("custom_ury_manual_discount_total") or 0) + ((merged_invoice.get("custom_ury_manual_discount_total") or 0) if is_commercial_merge else 0) %}
{% set credit_amount = (doc.get("custom_ury_credit_amount") or 0) + ((merged_invoice.get("custom_ury_credit_amount") or 0) if is_commercial_merge else 0) %}
{% set credit_due_date = doc.get("custom_ury_credit_due_date") or (merged_invoice.get("custom_ury_credit_due_date") if is_commercial_merge else none) %}
{% set receipt_total = (doc.rounded_total or doc.grand_total or 0) + merged_total %}
{% set is_house_offer = settlement_type == "House Offer" %}
{% set is_credit = credit_amount > 0 or settlement_type in ("Partial Credit", "Full Credit") %}

<div class="t-center t-name">GELATIAMO</div>
<div class="t-center">{{ doc.branch or "" }}</div>
<div class="t-center">
  {%- if doc.restaurant_table %}Mesa: {{ doc.restaurant_table }}
  {%- else %}{{ doc.order_type or "" }}{% endif -%}
</div>

<div class="t-rule"></div>

<div>Factura: {{ doc.name }}</div>
<div>Data: {{ frappe.utils.formatdate(doc.posting_date, "dd-MM-yyyy") }} {{ frappe.utils.format_time(doc.posting_time, "HH:mm") }}</div>
{% if doc.customer_name or doc.customer %}<div>Cliente: {{ doc.customer_name or doc.customer }}</div>{% endif %}
{% if doc.cashier %}<div>Caixa: {{ doc.cashier }}</div>{% endif %}

<div class="t-rule"></div>

<table>
  {% for item in doc.items %}
  <tr><td colspan="2">{{ item.item_name }}</td></tr>
  <tr>
    <td>&nbsp;&nbsp;{{ item.qty }} x {{ frappe.utils.fmt_money(item.rate, currency=None) }}</td>
    <td class="t-right">{{ frappe.utils.fmt_money(item.amount, currency=None) }}</td>
  </tr>
  {% endfor %}
</table>

<div class="t-rule"></div>

<table>
  <tr>
    <td>Subtotal</td>
    <td class="t-right">{{ frappe.utils.fmt_money(doc.net_total, currency=None) }}</td>
  </tr>
  {% for tax in doc.taxes %}{% if tax.tax_amount %}
  <tr>
    <td>{{ tax.description }}</td>
    <td class="t-right">{{ frappe.utils.fmt_money(tax.tax_amount, currency=None) }}</td>
  </tr>
  {% endif %}{% endfor %}
</table>

{% if doc.custom_merged_pos_invoice %}
<div class="t-rule"></div>
<div>Conta junta: {{ doc.custom_merged_pos_invoice }}</div>
<table>
  {% for item in merged_items %}
  <tr><td colspan="2">{{ item.item_name }}</td></tr>
  <tr>
    <td>&nbsp;&nbsp;{{ item.qty }} x {{ frappe.utils.fmt_money(item.rate, currency=None) }}</td>
    <td class="t-right">{{ frappe.utils.fmt_money(item.amount, currency=None) }}</td>
  </tr>
  {% endfor %}
  <tr>
    <td>{% if is_commercial_merge %}Subtotal{% else %}Total{% endif %} conta junta</td>
    <td class="t-right">{{ frappe.utils.fmt_money(merged_subtotal, currency=None) }}</td>
  </tr>
  {% for tax in merged_taxes %}{% if tax.tax_amount %}
  <tr>
    <td>{{ tax.description }}</td>
    <td class="t-right">{{ frappe.utils.fmt_money(tax.tax_amount, currency=None) }}</td>
  </tr>
  {% endif %}{% endfor %}
</table>
{% endif %}

{% if manual_discount and not is_house_offer %}
<table>
  <tr>
    <td>Antes do desconto</td>
    <td class="t-right">{{ frappe.utils.fmt_money(receipt_total + manual_discount, currency=None) }}</td>
  </tr>
  <tr>
    <td>Desconto manual</td>
    <td class="t-right">-{{ frappe.utils.fmt_money(manual_discount, currency=None) }}</td>
  </tr>
</table>
{% elif doc.discount_amount and not is_house_offer %}
<table>
  <tr>
    <td>Desconto</td>
    <td class="t-right">-{{ frappe.utils.fmt_money(doc.discount_amount, currency=None) }}</td>
  </tr>
</table>
{% endif %}

<div class="t-rule"></div>

{% if is_house_offer %}
<div class="t-callout t-center">
  <div class="t-callout-title">OFERTA DA CASA</div>
  <div>Valor oferecido: {{ frappe.utils.fmt_money(manual_discount, currency=None) }}</div>
</div>
{% endif %}

<table class="t-total">
  <tr>
    <td>TOTAL {{ doc.currency or "" }}</td>
    <td class="t-right">{{ frappe.utils.fmt_money(receipt_total, currency=None) }}</td>
  </tr>
</table>

{% set payment_rows = doc.payments if doc.docstatus == 1 else [] %}
{% if doc.docstatus == 1 and merged_invoice %}
  {% set payment_rows = payment_rows + merged_invoice.payments %}
{% endif %}
{% set change_total = (doc.change_amount or 0) + ((merged_invoice.change_amount or 0) if merged_invoice else 0) %}
{% set paid_now = (payment_rows|sum(attribute="amount")) - change_total %}

{% if is_credit and doc.docstatus == 1 %}
<div class="t-credit">
  <div class="t-credit-title">CRÉDITO</div>
  <table>
    <tr><td>Cliente</td><td class="t-right">{{ doc.customer_name or doc.customer or "" }}</td></tr>
    <tr><td>Pago agora</td><td class="t-right">{{ frappe.utils.fmt_money(paid_now, currency=None) }}</td></tr>
    <tr><td>Saldo concedido</td><td class="t-right">{{ frappe.utils.fmt_money(credit_amount, currency=None) }}</td></tr>
    <tr><td>Vencimento</td><td class="t-right">{{ frappe.utils.formatdate(credit_due_date, "dd-MM-yyyy") if credit_due_date else "-" }}</td></tr>
  </table>
</div>
{% endif %}

<div class="t-rule"></div>
{% if is_house_offer and doc.docstatus == 1 %}
<div>Sem pagamento - oferta da casa</div>
{% elif doc.docstatus == 1 and (not is_credit or paid_now) %}
<div>PAGAMENTO</div>
<table>
  {% for payment_group in payment_rows|groupby("mode_of_payment") %}
  {% set payment_total = payment_group.list|sum(attribute="amount") %}
  {% if payment_total %}
  <tr>
    <td>{{ payment_group.grouper }}</td>
    <td class="t-right">{{ frappe.utils.fmt_money(payment_total, currency=None) }}</td>
  </tr>
  {% endif %}
  {% endfor %}
  {% if change_total %}
  <tr>
    <td>Troco</td>
    <td class="t-right">{{ frappe.utils.fmt_money(change_total, currency=None) }}</td>
  </tr>
  {% endif %}
</table>
{% elif is_credit and doc.docstatus == 1 %}
<div>Sem pagamento recebido agora</div>
{% else %}
<div>Pagamento: pendente</div>
{% endif %}

<div class="t-rule"></div>
<div class="t-center">Obrigado pela preferência!</div>
"""


def _upsert(name: str, fields: dict) -> str:
	values = {
		"print_format_for": "DocType",
		"doc_type": DOCTYPE,
		"module": "URY",
		"standard": "No",
		"custom_format": 1,
		"print_format_type": "Jinja",
		"disabled": 0,
		"margin_top": 0,
		"margin_bottom": 0,
		"margin_left": 0,
		"margin_right": 0,
		"page_number": "Hide",
		"line_breaks": 0,
		**fields,
	}
	if frappe.db.exists("Print Format", name):
		doc = frappe.get_doc("Print Format", name)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc({"doctype": "Print Format", "name": name, **values})
		doc.insert(ignore_permissions=True)
	return doc.name


def install_pos_receipt_formats() -> tuple[str, str]:
	"""Create or update both URY-owned POS receipt formats without committing."""
	html_name = _upsert(
		HTML_FORMAT,
		{
			"raw_printing": 0,
			"html": HTML,
			"font_size": 12,
			"pdf_generator": "wkhtmltopdf",
		},
	)
	raw_name = _upsert(
		RAW_FORMAT,
		{
			"raw_printing": 1,
			"raw_commands": RAW_COMMANDS,
		},
	)
	return html_name, raw_name
