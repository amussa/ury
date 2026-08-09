// Copyright (c) 2026, Gelatiamo and contributors
// For license information, please see license.txt

frappe.ui.form.on("URY KOT Print Job", {
	refresh(frm) {
		if (!frm.is_new() && ["Failed", "Queued"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Requeue"), () => {
				frappe.call({
					method: "ury.ury.printing.kot.retry_print_job",
					args: { print_job: frm.doc.name },
					freeze: true,
					freeze_message: __("Queueing KOT print job..."),
				}).then(() => frm.reload_doc());
			});
		}
	},
});
