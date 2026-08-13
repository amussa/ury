from __future__ import annotations

import hashlib
import json

import frappe
from frappe.utils import flt


CONFIRMATION = "REPAIR_LEGACY_POS_CLOSING"


def preview(closing_entry: str) -> dict:
	doc = frappe.get_doc("POS Closing Entry", closing_entry)
	rows = [_row_snapshot(row) for row in doc.payment_reconciliation]
	payload = {
		"name": doc.name,
		"docstatus": doc.docstatus,
		"pos_profile": doc.pos_profile,
		"posting_date": str(doc.posting_date),
		"rows": rows,
	}
	payload["fingerprint"] = _fingerprint(payload)
	payload["repairable"] = [row for row in rows if _classification(row) == "repairable"]
	payload["ambiguous"] = [row for row in rows if _classification(row) == "ambiguous"]
	return payload


def audit_submitted() -> dict:
	entries = frappe.get_all(
		"POS Closing Entry",
		filters={"docstatus": 1},
		pluck="name",
		order_by="posting_date asc, creation asc",
		limit_page_length=0,
	)
	results = [preview(name) for name in entries]
	return {
		"closing_entries": len(results),
		"repairable": [
			{"name": result["name"], "rows": result["repairable"]}
			for result in results
			if result["repairable"]
		],
		"ambiguous": [
			{"name": result["name"], "rows": result["ambiguous"]}
			for result in results
			if result["ambiguous"]
		],
	}


def repair(
	closing_entry: str,
	expected_fingerprint: str,
	justification: str,
	confirmation: str,
) -> dict:
	if confirmation != CONFIRMATION:
		frappe.throw("Invalid legacy POS Closing repair confirmation.")
	if not (justification or "").strip():
		frappe.throw("A transparent historical repair note is required.")

	before = preview(closing_entry)
	if before["fingerprint"] != expected_fingerprint:
		frappe.throw("POS Closing Entry changed after preview; refusing repair.")
	if before["docstatus"] != 1:
		frappe.throw("Only submitted POS Closing Entries can be repaired by this command.")
	if frappe.db.get_value(
		"POS Profile", before["pos_profile"], "custom_enable_multiple_cashier"
	):
		frappe.throw("Legacy repair is restricted to single-cashier POS Profiles.")
	if not before["repairable"]:
		frappe.throw("No unambiguous counted amounts require repair.")

	try:
		for row in before["repairable"]:
			counted = flt(row["custom_closing_amount"], 2)
			difference = flt(counted - flt(row["expected_amount"]), 2)
			frappe.db.set_value(
				"POS Closing Entry Detail",
				row["name"],
				{"closing_amount": counted, "difference": difference},
				update_modified=True,
			)
		frappe.db.set_value(
			"POS Closing Entry",
			closing_entry,
			"custom_difference_justification",
			justification.strip(),
			update_modified=True,
		)
		after = preview(closing_entry)
		if after["repairable"]:
			frappe.throw("Legacy POS Closing repair did not converge.")
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		raise

	return {"status": "repaired", "before": before, "after": after}


def _row_snapshot(row) -> dict:
	return {
		"name": row.name,
		"idx": row.idx,
		"mode_of_payment": row.mode_of_payment,
		"expected_amount": flt(row.expected_amount, 2),
		"custom_closing_amount": (
			None if row.custom_closing_amount is None else flt(row.custom_closing_amount, 2)
		),
		"closing_amount": flt(row.closing_amount, 2),
		"difference": flt(row.difference, 2),
	}


def _classification(row: dict) -> str:
	custom = row["custom_closing_amount"]
	closing = flt(row["closing_amount"], 2)
	expected = flt(row["expected_amount"], 2)
	stored_difference = flt(row["difference"], 2)
	if custom is None or (
		abs(flt(custom, 2)) < 0.005
		and abs(expected) >= 0.005
		and abs(closing - expected) < 0.005
		and abs(stored_difference) < 0.005
	):
		return "ambiguous" if abs(closing - flt(custom)) >= 0.005 else "consistent"
	if abs(flt(custom) - closing) >= 0.005:
		return "repairable"
	return "consistent"


def _fingerprint(payload: dict) -> str:
	canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
	return hashlib.sha256(canonical.encode()).hexdigest()
