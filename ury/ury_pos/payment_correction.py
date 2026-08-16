import json

import frappe
from frappe import _
from frappe.utils import escape_html, flt


PAYMENT_CORRECTION_SUPERVISOR_ROLES = frozenset({"URY Manager", "System Manager"})


def can_correct_payment(invoice, user=None, roles=None):
	"""Return whether a user is the sale author or an authorised supervisor."""
	user = user or frappe.session.user
	roles = set(roles if roles is not None else frappe.get_roles(user))

	return bool(
		user == "Administrator"
		or user == invoice.waiter
		or roles.intersection(PAYMENT_CORRECTION_SUPERVISOR_ROLES)
	)


def normalise_payment_correction(payments, allowed_modes, expected_total, precision=2):
	if isinstance(payments, str):
		try:
			payments = json.loads(payments)
		except (TypeError, ValueError):
			frappe.throw(_("Payments must be a valid JSON array."))

	if not isinstance(payments, (list, tuple)):
		frappe.throw(_("Payments must be a list."))

	allowed_modes = set(allowed_modes)
	normalised = []
	seen_modes = set()

	for row in payments:
		if not isinstance(row, dict):
			frappe.throw(_("Each payment must contain a payment method and amount."))

		mode_of_payment = str(row.get("mode_of_payment") or "").strip()
		amount = flt(row.get("amount"), precision)

		if not mode_of_payment or mode_of_payment not in allowed_modes:
			frappe.throw(
				_("Payment method {0} is not available in this POS Profile.").format(
					frappe.bold(mode_of_payment or _("Not set"))
				)
			)
		if mode_of_payment in seen_modes:
			frappe.throw(
				_("Payment method {0} was entered more than once.").format(
					frappe.bold(mode_of_payment)
				)
			)
		if amount <= 0:
			frappe.throw(_("Payment amounts must be greater than zero."))

		seen_modes.add(mode_of_payment)
		normalised.append({"mode_of_payment": mode_of_payment, "amount": amount})

	if not normalised:
		frappe.throw(_("At least one payment method is required."))

	new_total = flt(sum(row["amount"] for row in normalised), precision)
	if new_total != flt(expected_total, precision):
		frappe.throw(
			_("The corrected payment total must remain {0}.").format(
				frappe.bold(flt(expected_total, precision))
			)
		)

	return normalised


def distribute_payment_correction(payments, invoice_totals, precision=2):
	"""Distribute combined payments without changing any invoice's paid total."""
	allocations = [[] for _ in invoice_totals]
	remaining_by_invoice = [flt(total, precision) for total in invoice_totals]

	for payment in payments:
		remaining_payment = flt(payment["amount"], precision)
		for index, invoice_balance in enumerate(remaining_by_invoice):
			if remaining_payment <= 0 or invoice_balance <= 0:
				continue

			allocated = flt(min(remaining_payment, invoice_balance), precision)
			if allocated <= 0:
				continue

			allocations[index].append(
				{
					"mode_of_payment": payment["mode_of_payment"],
					"amount": allocated,
				}
			)
			remaining_payment = flt(remaining_payment - allocated, precision)
			remaining_by_invoice[index] = flt(invoice_balance - allocated, precision)

		if remaining_payment:
			frappe.throw(_("The corrected payments could not be allocated to the invoices."))

	if any(remaining_by_invoice):
		frappe.throw(_("The corrected payments do not cover the original paid total."))

	return allocations


def _payment_map(invoices, precision):
	payment_map = {}
	for invoice in invoices:
		for payment in invoice.payments:
			mode = payment.mode_of_payment
			payment_map[mode] = flt(payment_map.get(mode, 0) + payment.amount, precision)
	return payment_map


def _payment_rows(payment_map):
	return [
		{"mode_of_payment": mode, "amount": amount}
		for mode, amount in payment_map.items()
		if amount
	]


def _get_correction_invoice_names(invoice_name):
	selected = frappe.db.get_value(
		"POS Invoice",
		invoice_name,
		["name", "custom_merged_pos_invoice"],
		as_dict=True,
	)
	if not selected:
		frappe.throw(_("POS Invoice {0} was not found.").format(frappe.bold(invoice_name)))

	if selected.custom_merged_pos_invoice:
		return [selected.name, selected.custom_merged_pos_invoice]

	primary = frappe.db.get_value(
		"POS Invoice",
		{"custom_merged_pos_invoice": selected.name, "docstatus": 1},
		"name",
	)
	return [primary, selected.name] if primary else [selected.name]


def _load_correction_invoices(invoice_name, for_update=False):
	names = list(dict.fromkeys(_get_correction_invoice_names(invoice_name)))
	if for_update:
		placeholders = ", ".join(["%s"] * len(names))
		frappe.db.sql(
			f"""
			SELECT name
			FROM `tabPOS Invoice`
			WHERE name IN ({placeholders})
			ORDER BY name
			FOR UPDATE
			""",
			tuple(names),
		)

	invoices_by_name = {name: frappe.get_doc("POS Invoice", name) for name in names}
	return [invoices_by_name[name] for name in names]


def _get_open_closing_entry(invoice_names):
	placeholders = ", ".join(["%s"] * len(invoice_names))
	rows = frappe.db.sql(
		f"""
		SELECT closing.name, closing.docstatus
		FROM `tabPOS Invoice Reference` invoice_ref
		INNER JOIN `tabPOS Closing Entry` closing ON closing.name = invoice_ref.parent
		WHERE invoice_ref.parenttype = 'POS Closing Entry'
			AND invoice_ref.pos_invoice IN ({placeholders})
			AND closing.docstatus < 2
		ORDER BY closing.creation DESC
		LIMIT 1
		""",
		tuple(invoice_names),
		as_dict=True,
	)
	return rows[0] if rows else None


def _validate_correction_state(invoices):
	for invoice in invoices:
		if invoice.docstatus != 1 or invoice.status != "Paid":
			frappe.throw(
				_("Only submitted POS Invoices with status Paid can have their payment methods corrected.")
			)
		if invoice.consolidated_invoice:
			frappe.throw(
				_("POS Invoice {0} is already consolidated and cannot be corrected here.").format(
					frappe.bold(invoice.name)
				)
			)
		if invoice.get("custom_ury_settlement_type") == "House Offer":
			frappe.throw(_("A House Offer has no payment method to correct."))
		if (
			abs(flt(invoice.get("change_amount"))) >= 0.01
			or abs(flt(invoice.get("base_change_amount"))) >= 0.01
		):
			frappe.throw(
				_(
					"A commercial checkout payment with change cannot be reclassified. "
					"Use an approved accounting correction procedure."
				)
			)

	closing_entry = _get_open_closing_entry([invoice.name for invoice in invoices])
	if closing_entry:
		state = _("submitted") if closing_entry.docstatus == 1 else _("draft")
		frappe.throw(
			_("POS Closing Entry {0} is {1}. Remove or cancel that closing before correcting this payment.").format(
				frappe.bold(closing_entry.name), state
			)
		)


def _validate_correction_actor(selected_invoice):
	user = frappe.session.user
	if user == "Guest":
		raise frappe.AuthenticationError

	roles = set(frappe.get_roles(user))
	if not can_correct_payment(selected_invoice, user=user, roles=roles):
		frappe.throw(
			_("Only the person who made the sale or a supervisor can correct its payment methods."),
			frappe.PermissionError,
		)

	if user == "Administrator" or "System Manager" in roles or user == selected_invoice.waiter:
		return

	user_branches = set(
		frappe.db.sql(
			"""
			SELECT branch.branch
			FROM `tabURY User` ury_user
			INNER JOIN `tabBranch` branch ON branch.name = ury_user.parent
			WHERE ury_user.user = %s
			""",
			user,
			pluck=True,
		)
	)
	if selected_invoice.branch not in user_branches:
		frappe.throw(
			_("Supervisors can only correct payments for their assigned branch."),
			frappe.PermissionError,
		)


def _get_allowed_payment_modes(invoices):
	profiles = {invoice.pos_profile for invoice in invoices}
	if len(profiles) != 1:
		frappe.throw(_("Merged invoices must use the same POS Profile."))

	profile = frappe.get_doc("POS Profile", profiles.pop())
	modes = list(dict.fromkeys(row.mode_of_payment for row in profile.payments if row.mode_of_payment))
	if not modes:
		frappe.throw(_("No payment methods are configured in POS Profile {0}.").format(frappe.bold(profile.name)))
	return modes


def _format_payment_summary(payments):
	return ", ".join(f"{row['mode_of_payment']}: {row['amount']}" for row in payments)


@frappe.whitelist()
def get_payment_correction_details(invoice):
	invoices = _load_correction_invoices(invoice)
	selected_invoice = next(doc for doc in invoices if doc.name == invoice)
	_validate_correction_actor(selected_invoice)
	_validate_correction_state(invoices)

	precision = selected_invoice.precision("paid_amount") or 2
	current_payments = _payment_rows(_payment_map(invoices, precision))
	return {
		"invoice": invoice,
		"affected_invoices": [doc.name for doc in invoices],
		"payment_modes": _get_allowed_payment_modes(invoices),
		"payments": current_payments,
		"total_paid": flt(sum(row["amount"] for row in current_payments), precision),
		"currency": selected_invoice.currency,
		"precision": precision,
	}


@frappe.whitelist()
def correct_payment_methods(invoice, payments, reason):
	reason = str(reason or "").strip()
	if not reason:
		frappe.throw(_("A reason is required to correct the payment methods."))

	invoices = _load_correction_invoices(invoice, for_update=True)
	selected_invoice = next(doc for doc in invoices if doc.name == invoice)
	_validate_correction_actor(selected_invoice)
	_validate_correction_state(invoices)

	precision = selected_invoice.precision("paid_amount") or 2
	previous_map = _payment_map(invoices, precision)
	previous_payments = _payment_rows(previous_map)
	expected_total = flt(sum(previous_map.values()), precision)
	corrected_payments = normalise_payment_correction(
		payments,
		_get_allowed_payment_modes(invoices),
		expected_total,
		precision,
	)
	corrected_map = {
		row["mode_of_payment"]: flt(row["amount"], precision)
		for row in corrected_payments
	}
	if corrected_map == previous_map:
		frappe.throw(_("Change at least one payment method or amount before saving."))

	invoice_totals = [
		flt(sum(payment.amount for payment in doc.payments), precision)
		for doc in invoices
	]
	allocations = distribute_payment_correction(corrected_payments, invoice_totals, precision)

	for doc, allocation in zip(invoices, allocations, strict=True):
		doc.set("payments", [])
		for payment in allocation:
			doc.append("payments", payment)
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)

	audit_message = _(
		"Payment methods corrected by {0}. Previous: {1}. Corrected: {2}. Reason: {3}"
	).format(
		frappe.bold(frappe.session.user),
		escape_html(_format_payment_summary(previous_payments)),
		escape_html(_format_payment_summary(corrected_payments)),
		escape_html(reason),
	)
	for doc in invoices:
		doc.add_comment("Edit", audit_message)

	return {
		"invoice": invoice,
		"affected_invoices": [doc.name for doc in invoices],
		"payments": corrected_payments,
		"total_paid": expected_total,
	}
