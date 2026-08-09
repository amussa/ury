"""Gelatiamo raw ESC/POS format for 80 mm kitchen tickets."""

import hashlib

import frappe

PRINT_FORMAT_NAME = "Gelatiamo KOT ESC/POS 80mm"
DOCTYPE = "URY KOT"

ESC = "\x1b"
GS = "\x1d"

INIT = ESC + "@"
CODEPAGE_PC860 = ESC + "t" + "\x03"
ALIGN_LEFT = ESC + "a" + "\x00"
ALIGN_CENTER = ESC + "a" + "\x01"
SIZE_NORMAL = ESC + "!" + "\x00"
SIZE_TALL = ESC + "!" + "\x10"
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
	+ """ -%}
{%- set sep = "=" * W -%}
{%- set thin = "-" * W -%}
{%- set cancellation = doc.type in ["Cancelled", "Partially cancelled"] -%}
{%- set type_labels = {
	"New Order": "NOVO PEDIDO",
	"Order Modified": "PEDIDO ALTERADO",
	"Cancelled": "CANCELADO",
	"Partially cancelled": "CANCELAMENTO",
	"Duplicate": "SEGUNDA VIA"
} -%}
{%- set type_label = type_labels.get(doc.type, doc.type or "KOT") -%}
{%- macro qty(value) -%}
{%- set number = frappe.utils.flt(value or 0) -%}
{{ (number|round(3)|string).rstrip("0").rstrip(".") }}
{%- endmacro -%}
"""
	+ ALIGN_CENTER
	+ BOLD_ON
	+ """GELATIAMO
"""
	+ SIZE_DOUBLE
	+ """{{ type_label[:24] }}
"""
	+ SIZE_NORMAL
	+ """{{ (doc.production or "COZINHA")[:W] }}
"""
	+ ALIGN_LEFT
	+ BOLD_OFF
	+ """{{ sep }}
"""
	+ ALIGN_CENTER
	+ SIZE_DOUBLE
	+ BOLD_ON
	+ """PEDIDO {{ doc.order_no or "-" }}
"""
	+ SIZE_NORMAL
	+ ALIGN_LEFT
	+ BOLD_OFF
	+ """KOT: {{ doc.name }}
{%- if doc.restaurant_table %}
MESA: {{ doc.restaurant_table }}{% if doc.table_takeaway %} / TAKEAWAY{% endif %}
{%- else %}
TIPO: TAKEAWAY / BALCAO
{%- endif %}
DATA: {{ frappe.utils.formatdate(doc.date, "dd-MM-yyyy") }} {{ frappe.utils.format_time(doc.time, "HH:mm") }}
{%- if doc.user %}
ATENDENTE: {{ doc.user[:W - 10] }}
{%- endif %}
{%- if doc.is_aggregator %}
AGREGADOR: {{ (doc.aggregator_id or "-")[:W - 12] }}
{%- endif %}
{%- if cancellation and doc.original_kot %}
KOT ORIGINAL: {{ doc.original_kot[:W - 14] }}
{%- endif %}
{{ thin }}
"""
	+ """{%- for item in doc.kot_items %}
{%- set printed_qty = item.cancelled_qty if cancellation and frappe.utils.flt(item.cancelled_qty) else item.quantity -%}
{%- set item_line = qty(printed_qty) ~ " x " ~ (item.item_name or item.item or "") -%}
"""
	+ SIZE_TALL
	+ BOLD_ON
	+ """{{ item_line[:W] }}
{%- if item_line|length > W %}
{{ item_line[W:96] }}
{%- endif %}
{%- if item_line|length > 96 %}
{{ item_line[96:144] }}
{%- endif %}
"""
	+ SIZE_NORMAL
	+ BOLD_OFF
	+ """{%- if item.comments %}
{%- set item_note = ">> " ~ item.comments -%}
{{ item_note[:W] }}
{%- if item_note|length > W %}
{{ item_note[W:96] }}
{%- endif %}
{%- if item_note|length > 96 %}
{{ item_note[96:144] }}
{%- endif %}
{%- endif %}
{%- if not loop.last %}
{{ thin }}
{%- endif %}
{%- endfor %}
{{ sep }}
{%- if doc.comments %}
"""
	+ BOLD_ON
	+ """OBSERVACOES
"""
	+ BOLD_OFF
	+ """{{ doc.comments[:W] }}
{%- if doc.comments|length > W %}
{{ doc.comments[W:96] }}
{%- endif %}
{{ sep }}
{%- endif %}
"""
	+ ALIGN_CENTER
	+ BOLD_ON
	+ """{{ type_label }} - PEDIDO {{ doc.order_no or "-" }}
"""
	+ BOLD_OFF
	+ ALIGN_LEFT
	+ FEED_AND_CUT
)


def install() -> str:
	"""Create or update the application-owned raw KOT Print Format."""
	values = {
		"doc_type": DOCTYPE,
		"module": "URY",
		"standard": "No",
		"custom_format": 1,
		"print_format_type": "Jinja",
		"raw_printing": 1,
		"raw_commands": RAW_COMMANDS,
		"disabled": 0,
		"margin_top": 0,
		"margin_bottom": 0,
		"margin_left": 0,
		"margin_right": 0,
		"page_number": "Hide",
		"line_breaks": 0,
	}

	if frappe.db.exists("Print Format", PRINT_FORMAT_NAME):
		doc = frappe.get_doc("Print Format", PRINT_FORMAT_NAME)
		for fieldname, value in values.items():
			doc.set(fieldname, value)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Print Format",
				"name": PRINT_FORMAT_NAME,
				**values,
			}
		).insert(ignore_permissions=True)

	return doc.name


def validate_rendering(kot: str) -> dict:
	"""Render an existing KOT without opening a socket or sending printer data."""
	from ury.ury.printing.kot import render_escpos_payload

	payload = render_escpos_payload(kot, PRINT_FORMAT_NAME)
	assert payload.startswith(INIT.encode("cp860")), "ESC/POS initialize command is missing"
	assert payload.endswith(FEED_AND_CUT.encode("cp860")), "ESC/POS cut command is missing"
	return {
		"kot": kot,
		"bytes": len(payload),
		"sha256": hashlib.sha256(payload).hexdigest(),
		"status": "rendered_without_sending",
	}
