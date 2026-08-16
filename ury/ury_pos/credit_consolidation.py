from __future__ import annotations

from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import flt

CREDIT_TYPES = {"Partial Credit", "Full Credit"}
PAID_GROUP = ("paid", "")


def before_submit(doc, method=None):
	"""Partition one standard merge log without replacing ERPNext's class.

	The current log retains the first partition. Remaining partitions are saved
	and submitted as sibling standard merge logs in the same DB transaction.
	ERPNext therefore remains responsible for sync/background status, retry,
	cancel/unconsolidate and ledger creation.
	"""
	if doc.flags.get("ury_partitioning"):
		return

	partitioned_rows = get_partitioned_merge_rows(doc)
	if len(partitioned_rows) <= 1:
		return

	doc.flags.ury_partitioning = True
	doc.set("pos_invoices", partitioned_rows[0])

	for rows in partitioned_rows[1:]:
		sibling = frappe.new_doc("POS Invoice Merge Log")
		for fieldname in (
			"posting_date",
			"posting_time",
			"company",
			"customer",
			"customer_group",
			"merge_invoices_based_on",
			"pos_closing_entry",
		):
			sibling.set(fieldname, doc.get(fieldname))
		sibling.set("pos_invoices", rows)
		sibling.flags.ury_partitioning = True
		sibling.flags.ignore_permissions = True
		sibling.save(ignore_permissions=True)
		sibling.submit()


def get_partitioned_merge_rows(doc):
	child_rows = list(doc.get("pos_invoices") or [])
	if len(child_rows) < 2:
		return [_serialise_reference_rows(child_rows)] if child_rows else []

	names = [row.pos_invoice for row in child_rows]
	invoice_records = frappe.get_all(
		"POS Invoice",
		filters={"name": ["in", names]},
		fields=[
			"name",
			"custom_ury_settlement",
			"custom_ury_settlement_type",
			"custom_ury_credit_amount",
			"custom_ury_credit_due_date",
			"is_return",
			"return_against",
		],
	)
	record_by_name = {record.name: record for record in invoice_records}
	missing = [name for name in names if name not in record_by_name]
	if missing:
		frappe.throw(
			_("Unable to load POS Invoices while partitioning the closing: {0}").format(
				", ".join(missing)
			)
		)

	ordered_records = [record_by_name[name] for name in names]
	groups = partition_invoice_records(ordered_records)
	rows_by_name = {row.pos_invoice: row for row in child_rows}
	return [
		_serialise_reference_rows([rows_by_name[name] for name in invoice_names])
		for invoice_names in groups.values()
	]


def partition_invoice_records(records):
	"""Return deterministic invoice-name groups, one per credit agreement."""
	records = [frappe._dict(record) for record in records]
	record_by_name = {record.name: record for record in records}
	keys = {record.name: _base_partition_key(record) for record in records}

	# A return submitted in the same log must follow its original invoice. This
	# preserves ERPNext's return-against validation and cancellation ordering.
	for record in records:
		if not record.get("is_return") or not record.get("return_against"):
			continue
		original = record_by_name.get(record.return_against)
		if not original:
			continue
		original_key = keys[original.name]
		own_key = keys[record.name]
		if own_key != PAID_GROUP and own_key != original_key:
			frappe.throw(
				_("Return {0} and its original invoice use different credit agreements.").format(
					frappe.bold(record.name)
				)
			)
		keys[record.name] = original_key

	groups = OrderedDict()
	for record in records:
		key = keys[record.name]
		groups.setdefault(key, []).append(record.name)

	for key, invoice_names in groups.items():
		_validate_credit_group(key, [record_by_name[name] for name in invoice_names])
	return groups


def _base_partition_key(record):
	settlement = record.get("custom_ury_settlement")
	settlement_type = record.get("custom_ury_settlement_type")
	is_credit = settlement_type in CREDIT_TYPES or flt(record.get("custom_ury_credit_amount")) > 0
	if not is_credit:
		return PAID_GROUP
	if not settlement:
		frappe.throw(
			_("Credit POS Invoice {0} has no URY settlement.").format(frappe.bold(record.name))
		)
	return ("credit", settlement)


def _validate_credit_group(key, records):
	if key == PAID_GROUP:
		return

	expected_settlement = key[1]
	settlements = {
		record.get("custom_ury_settlement")
		for record in records
		if not record.get("is_return")
	}
	if settlements != {expected_settlement}:
		frappe.throw(_("A consolidated credit group must contain exactly one URY settlement."))

	due_dates = {
		str(record.get("custom_ury_credit_due_date"))
		for record in records
		if record.get("custom_ury_credit_due_date")
	}
	if len(due_dates) != 1:
		frappe.throw(_("All POS Invoices in one credit agreement must have the same due date."))


def _serialise_reference_rows(rows):
	fields = (
		"pos_invoice",
		"posting_date",
		"customer",
		"grand_total",
		"is_return",
		"return_against",
	)
	return [{fieldname: row.get(fieldname) for fieldname in fields} for row in rows]
