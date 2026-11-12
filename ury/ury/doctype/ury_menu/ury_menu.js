//  Copyright (c) 2023, Tridz Technologies Pvt. Ltd. and contributors
//  For license information, please see license.txt

frappe.ui.form.on('URY Menu', {
	setup: function (frm) {
		frm.add_fetch('item', 'standard_rate', 'rate');
	},
	refresh: function (frm) {
		// Reload clean forms so orders made at another till reduce the visible
		// balance. Never overwrite an operator's unsaved changes.
		clearTimeout(frm._promotion_balance_timer);
		const refresh_balance = () => {
			const route = frappe.get_route();
			if (cur_frm !== frm || frm.is_new() || route[0] !== 'Form'
				|| route[1] !== frm.doctype || route[2] !== frm.doc.name) return;
			if (!frm.is_dirty() && !frm._promotion_balance_dialog_open) {
				frm.reload_doc();
			} else {
				frm._promotion_balance_timer = setTimeout(refresh_balance, 15000);
			}
		};
		if (!frm.is_new()) {
			frm._promotion_balance_timer = setTimeout(refresh_balance, 15000);
		}
	},
});

frappe.ui.form.on('URY Menu Price Option', {
	set_remaining_qty: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const expected = { ...(frm.doc.__onload?.promotion_balances?.[cdn] || {}) };
		frm._promotion_balance_dialog_open = true;
		const dialog = frappe.prompt({
			fieldname: 'quantity', fieldtype: 'Float',
			label: __('Remaining Promotional Quantity'),
			default: row.remaining_qty || 0, reqd: 1,
			description: __('Enter the number of currently available units to put on promotion. No historical sales calculation is needed.'),
		}, (values) => {
			row.remaining_qty = values.quantity;
			row._remaining_request = {
				quantity: values.quantity,
				expected,
			};
			frm.dirty();
			frm.refresh_field('price_options');
		}, __('Set Promotional Quantity'), __('Apply'));
		dialog.$wrapper.on('hidden.bs.modal', () => {
			frm._promotion_balance_dialog_open = false;
		});
	},
	price_options_add: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.label) {
			frappe.model.set_value(cdt, cdn, 'label', __('Promotion'));
		}
	},
});
