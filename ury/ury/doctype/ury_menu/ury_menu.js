//  Copyright (c) 2023, Tridz Technologies Pvt. Ltd. and contributors
//  For license information, please see license.txt

frappe.ui.form.on('URY Menu', {
	setup: function (frm) {
		frm.add_fetch('item', 'standard_rate', 'rate');
	},
});

frappe.ui.form.on('URY Menu Price Option', {
	price_options_add: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.label) {
			frappe.model.set_value(cdt, cdn, 'label', __('Promotion'));
		}
	},
});
