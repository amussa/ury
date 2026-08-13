const URY_COUNTING_FIELDS = {
	single: "closing_amount",
	multiple: "custom_closing_amount",
};

frappe.ui.form.on("POS Closing Entry", {
	refresh(frm) {
		configure_counting_fields(frm).then(() => refresh_difference_justification(frm));
	},

	pos_profile(frm) {
		return configure_counting_fields(frm);
	},

	// ERPNext's standard handler runs first and fills closing_amount with the
	// expected amount. This URY handler runs immediately afterwards and clears
	// the operator input field so every amount, including zero, is deliberate.
	async get_pos_invoices(frm) {
		const multiple_cashier = await configure_counting_fields(frm);
		const counted_field = multiple_cashier
			? URY_COUNTING_FIELDS.multiple
			: URY_COUNTING_FIELDS.single;

		for (const row of frm.doc.payment_reconciliation || []) {
			row[counted_field] = null;
			row.difference = null;
		}
		frm.refresh_field("payment_reconciliation");
		refresh_difference_justification(frm);
	},

	validate(frm) {
		const multiple_cashier = Boolean(frm.__ury_multiple_cashier);
		const counted_field = multiple_cashier
			? URY_COUNTING_FIELDS.multiple
			: URY_COUNTING_FIELDS.single;
		const missing = (frm.doc.payment_reconciliation || []).filter((row) =>
			is_missing_count(row[counted_field])
		);

		if (missing.length) {
			frappe.throw(
				__("Enter the counted amount for: {0}.", [
					missing.map((row) => row.mode_of_payment).join(", "),
				])
			);
		}

		refresh_difference_justification(frm);
		if (
			has_payment_difference(frm) &&
			!(frm.doc.custom_difference_justification || "").trim()
		) {
			frappe.throw(
				__("Explain the payment difference before saving or submitting this closing entry.")
			);
		}
	},
});

frappe.ui.form.on("POS Closing Entry Detail", {
	closing_amount(frm) {
		refresh_difference_justification(frm);
	},

	difference(frm) {
		refresh_difference_justification(frm);
	},
});

async function configure_counting_fields(frm) {
	let multiple_cashier = false;
	if (frm.doc.pos_profile) {
		const result = await frappe.db.get_value(
			"POS Profile",
			frm.doc.pos_profile,
			"custom_enable_multiple_cashier"
		);
		multiple_cashier = Boolean(
			Number(result?.message?.custom_enable_multiple_cashier || 0)
		);
	}
	frm.__ury_multiple_cashier = multiple_cashier;

	const grid = frm.fields_dict.payment_reconciliation?.grid;
	if (!grid) return multiple_cashier;

	grid.update_docfield_property("custom_closing_amount", "hidden", !multiple_cashier);
	grid.update_docfield_property("custom_closing_amount", "reqd", multiple_cashier);
	grid.update_docfield_property(
		"custom_closing_amount",
		"label",
		__("Main Cashier Counted Amount")
	);
	grid.update_docfield_property("closing_amount", "hidden", multiple_cashier);
	grid.update_docfield_property("closing_amount", "read_only", multiple_cashier);
	grid.update_docfield_property("closing_amount", "reqd", !multiple_cashier);
	grid.update_docfield_property(
		"closing_amount",
		"label",
		multiple_cashier ? __("Total Closing Amount") : __("Closing Amount")
	);
	frm.refresh_field("payment_reconciliation");
	return multiple_cashier;
}

function refresh_difference_justification(frm) {
	const has_difference = has_payment_difference(frm);
	frm.set_df_property("custom_difference_justification", "hidden", !has_difference);
	frm.set_df_property("custom_difference_justification", "reqd", has_difference);
}

function has_payment_difference(frm) {
	return (frm.doc.payment_reconciliation || []).some((row) => {
		if (is_missing_count(row.closing_amount)) return false;
		return Math.abs(flt(row.difference, 2)) >= 0.005;
	});
}

function is_missing_count(value) {
	return value === null || value === undefined || value === "";
}
