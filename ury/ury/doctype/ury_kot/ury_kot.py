# Copyright (c) 2023, Tridz Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.model.document import Document

from ury.ury.printing.kot import queue_kot_prints


class URYKOT(Document):
    def on_submit(self):
        self.multi_print_kot()
        self.kotDisplayRealtime()

    def before_submit(self):
        self.userSetting()

    # Function for printing multiple KOTs.
    def multi_print_kot(self):
        queue_kot_prints(self)


    # Function for displaying KOT-related information in real-time On KDS(Kitchen Display System)
    def kotDisplayRealtime(self):
        currentBranch = self.branch
        production = self.production

        if production:
            production_doc = frappe.get_doc("URY Production Unit", production)
            if production_doc.enable_order_type_wise_display_on_mosaic:
                invoice_order_type = frappe.db.get_value("POS Invoice", self.invoice, "order_type")
                allowed_order_types = [row.order_type for row in production_doc.get("order_type", [])]
                if invoice_order_type not in allowed_order_types:
                    return

        kotjson = json.loads(frappe.as_json(self))
        audio_file = frappe.db.get_value(
            "POS Profile", self.pos_profile, "custom_kot_alert_sound"
        )
        cache_key = "{}_{}_last_kot_time".format(currentBranch, production)
        time = frappe.cache().get_value(cache_key)
        kot_channel = "{}_{}_{}".format("kot_update", currentBranch, production)
        frappe.publish_realtime(
            kot_channel,
            {"kot": kotjson, "audio_file": audio_file, "last_kot_time": time},
            after_commit=True,
        )
        kot_time = self.time
        frappe.db.after_commit.add(
            lambda: frappe.cache().set_value(cache_key, kot_time)
        )

    def userSetting(self):
        userDoc = frappe.get_doc("User", self.owner)
        self.user = userDoc.full_name
