from __future__ import annotations

import hashlib
import socket
import traceback
from contextlib import closing

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime
from frappe.utils.print_format import print_by_server
from frappe.www.printview import get_rendered_template

TRANSPORT_CUPS = "CUPS/PDF"
TRANSPORT_ESCPOS = "ESC/POS TCP"
ESCPOS_ENCODING = "cp860"
DEFAULT_ESCPOS_PORT = 9100
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 30.0
MAX_PAYLOAD_BYTES = 128 * 1024

PRINT_JOB_DOCTYPE = "URY KOT Print Job"


def queue_kot_prints(kot) -> list[str]:
	"""Create one durable background print job per selected KOT printer."""
	jobs = []
	for printer_setting in get_kot_printer_settings(kot):
		try:
			jobs.append(create_print_job(kot.name, printer_setting))
		except Exception:
			frappe.log_error(
				message=traceback.format_exc(),
				title=f"Unable to queue KOT print {kot.name}",
			)
	return jobs


def get_kot_printer_settings(kot) -> list[frappe._dict]:
	"""Preserve URY's production/room/POS printer routing rules."""
	fields = [
		"name",
		"parent",
		"parenttype",
		"printer",
		"custom_kot_print_format",
		"custom_block_takeaway_kot",
		"transport",
		"escpos_host",
		"escpos_port",
		"escpos_timeout",
	]
	pos_printers = frappe.get_all(
		"URY Printer Settings",
		fields=fields,
		filters={
			"parent": kot.pos_profile,
			"custom_kot_print": 1,
			"parenttype": "POS Profile",
		},
		order_by="idx",
	)

	if not kot.production:
		return []

	production_printers = frappe.get_all(
		"URY Printer Settings",
		fields=fields,
		filters={
			"parent": kot.production,
			"custom_kot_print": 1,
			"parenttype": "URY Production Unit",
		},
		order_by="idx",
	)
	if not production_printers:
		return []

	selected = []
	for printer in production_printers:
		if cint(printer.custom_block_takeaway_kot) and (
			not kot.restaurant_table or cint(kot.table_takeaway)
		):
			continue
		selected.append(printer)

	if kot.restaurant_table and not cint(kot.table_takeaway):
		room = frappe.db.get_value("URY Table", kot.restaurant_table, "restaurant_room")
		if room:
			selected.extend(
				frappe.get_all(
					"URY Printer Settings",
					fields=fields,
					filters={
						"parent": room,
						"custom_kot_print": 1,
						"parenttype": "URY Room",
					},
					order_by="idx",
				)
			)
	elif pos_printers:
		selected.extend(pos_printers)

	return selected


def create_print_job(kot_name: str, printer_setting: frappe._dict) -> str:
	print_format = printer_setting.custom_kot_print_format or ""
	job_key = build_job_key(kot_name, printer_setting.name, print_format)
	transport = printer_setting.transport or TRANSPORT_CUPS

	if not frappe.db.exists(PRINT_JOB_DOCTYPE, job_key):
		frappe.get_doc(
			{
				"doctype": PRINT_JOB_DOCTYPE,
				"job_key": job_key,
				"kot": kot_name,
				"status": "Queued",
				"configuration_parent": printer_setting.parent,
				"printer_setting": printer_setting.name,
				"transport": transport,
				"network_printer": printer_setting.printer,
				"escpos_host": printer_setting.escpos_host,
				"escpos_port": printer_setting.escpos_port or DEFAULT_ESCPOS_PORT,
				"print_format": print_format,
			}
		).insert(ignore_permissions=True)

	queue_print_job(job_key, enqueue_after_commit=True)
	return job_key


def build_job_key(kot_name: str, printer_setting: str | int, print_format: str) -> str:
	# MariaDB may return numeric child-row names as ``int`` values.  Normalise
	# every component before joining so live printer settings such as name ``1``
	# produce the same deterministic key as their string representation.
	identity = "\x1f".join(
		str(component or "") for component in (kot_name, printer_setting, print_format)
	)
	return hashlib.sha256(identity.encode()).hexdigest()


def queue_print_job(print_job: str, *, enqueue_after_commit: bool) -> None:
	frappe.enqueue(
		"ury.ury.printing.kot.run_print_job",
		queue="short",
		timeout=60,
		enqueue_after_commit=enqueue_after_commit,
		job_id=f"ury-kot-print::{print_job}",
		deduplicate=True,
		print_job=print_job,
	)


def run_print_job(print_job: str) -> dict:
	job = _start_job(print_job)
	if not job:
		return {"status": "Skipped", "print_job": print_job}

	stage = "render"
	try:
		if job.transport == TRANSPORT_ESCPOS:
			payload = render_escpos_payload(job.kot, job.print_format)
			payload_hash = hashlib.sha256(payload).hexdigest()
			frappe.db.set_value(
				PRINT_JOB_DOCTYPE,
				job.name,
				{"payload_sha256": payload_hash, "payload_bytes": len(payload)},
			)
			frappe.db.commit()

			host, port, timeout = validate_escpos_target(
				job.escpos_host,
				job.escpos_port,
				_get_printer_timeout(job.printer_setting),
			)
			stage = "connect"
			with closing(socket.create_connection((host, port), timeout=timeout)) as connection:
				connection.settimeout(timeout)
				stage = "send"
				connection.sendall(payload)
		else:
			if not job.network_printer:
				frappe.throw(_("A CUPS network printer is required."))
			if not job.print_format:
				frappe.throw(_("A KOT print format is required."))
			stage = "send"
			print_by_server("URY KOT", job.kot, job.network_printer, job.print_format)

		stage = "sent"
		_finish_job(job.name, "Sent", sent_at=now_datetime())
		return {"status": "Sent", "print_job": job.name}
	except Exception as exc:
		status = "Ambiguous" if stage in {"send", "sent"} else "Failed"
		error = _safe_error(exc)
		_finish_job(job.name, status, last_error=error)
		frappe.log_error(
			message=traceback.format_exc(),
			title=f"KOT print {status}: {job.kot}",
		)
		return {"status": status, "print_job": job.name, "error": error}


def render_escpos_payload(kot_name: str, print_format_name: str) -> bytes:
	if not print_format_name:
		frappe.throw(_("A raw ESC/POS KOT print format is required."))

	print_format = frappe.get_doc("Print Format", print_format_name)
	if print_format.doc_type != "URY KOT" or not cint(print_format.raw_printing):
		frappe.throw(_("Print Format {0} is not a raw URY KOT format.").format(print_format_name))
	if cint(print_format.disabled):
		frappe.throw(_("Print Format {0} is disabled.").format(print_format_name))

	doc = frappe.get_doc("URY KOT", kot_name)
	_sanitize_kot_text(doc)
	rendered = get_rendered_template(doc=doc, print_format=print_format, meta=doc.meta)
	payload = rendered.encode(ESCPOS_ENCODING, errors="replace")
	if not payload:
		frappe.throw(_("The rendered ESC/POS payload is empty."))
	if len(payload) > MAX_PAYLOAD_BYTES:
		frappe.throw(_("The rendered ESC/POS payload is too large."))
	return payload


def sanitize_escpos_text(value) -> str:
	"""Remove bytes that a raw printer could interpret as control commands."""
	if value is None:
		return ""
	return "".join(" " if ord(character) < 32 or ord(character) == 127 else character for character in str(value))


def _sanitize_kot_text(doc) -> None:
	for fieldname in (
		"production",
		"order_no",
		"restaurant_table",
		"user",
		"aggregator_id",
		"original_kot",
		"comments",
	):
		doc.set(fieldname, sanitize_escpos_text(doc.get(fieldname)))
	for item in doc.kot_items:
		for fieldname in ("item", "item_name", "comments"):
			item.set(fieldname, sanitize_escpos_text(item.get(fieldname)))


def validate_escpos_target(host, port, timeout) -> tuple[str, int, float]:
	host = (host or "").strip()
	if not host or any(character.isspace() for character in host):
		frappe.throw(_("A valid ESC/POS host is required."))

	port = cint(port or DEFAULT_ESCPOS_PORT)
	if not 1 <= port <= 65535:
		frappe.throw(_("ESC/POS port must be between 1 and 65535."))

	timeout = flt(timeout or DEFAULT_TIMEOUT_SECONDS)
	if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
		frappe.throw(_("ESC/POS timeout must be greater than 0 and at most 30 seconds."))
	return host, port, timeout


def _get_printer_timeout(printer_setting: str) -> float:
	if not printer_setting:
		return DEFAULT_TIMEOUT_SECONDS
	return (
		frappe.db.get_value("URY Printer Settings", printer_setting, "escpos_timeout")
		or DEFAULT_TIMEOUT_SECONDS
	)


def _start_job(print_job: str):
	rows = frappe.db.sql(
		"""
		select status, attempts
		from `tabURY KOT Print Job`
		where name = %s
		for update
		""",
		print_job,
		as_dict=True,
	)
	if not rows or rows[0].status != "Queued":
		frappe.db.rollback()
		return None

	frappe.db.set_value(
		PRINT_JOB_DOCTYPE,
		print_job,
		{
			"status": "Printing",
			"attempts": cint(rows[0].attempts) + 1,
			"last_error": None,
		},
	)
	frappe.db.commit()
	return frappe.get_doc(PRINT_JOB_DOCTYPE, print_job)


def _finish_job(print_job: str, status: str, **values) -> None:
	values["status"] = status
	frappe.db.set_value(PRINT_JOB_DOCTYPE, print_job, values)
	frappe.db.commit()


def _safe_error(exc: Exception) -> str:
	return str(exc).replace("\x00", "")[:2000]


@frappe.whitelist()
def retry_print_job(print_job: str) -> dict:
	job = frappe.get_doc(PRINT_JOB_DOCTYPE, print_job)
	if not frappe.has_permission(PRINT_JOB_DOCTYPE, "write", doc=job):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if job.status not in {"Failed", "Queued"}:
		frappe.throw(_("Only failed or queued print jobs can be requeued safely."))

	job.db_set("status", "Queued")
	job.db_set("last_error", None)
	queue_print_job(job.name, enqueue_after_commit=True)
	return {"status": "Queued", "print_job": job.name}
