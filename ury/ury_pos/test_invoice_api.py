from unittest import TestCase
from unittest.mock import patch

import frappe

from ury.ury_pos.api import getPosInvoiceItems


class TestInvoiceAPI(TestCase):
    @patch("ury.ury_pos.api.frappe.get_doc")
    def test_reload_items_include_price_option_identity(self, get_doc):
        get_doc.return_value = frappe._dict(
            items=[
                frappe._dict(
                    name="ROW-1",
                    item_code="CAKE-SLICE",
                    item_name="Cake Slice",
                    qty=4,
                    rate=80,
                    amount=320,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                )
            ],
            taxes=[],
        )

        item_details, tax_details = getPosInvoiceItems("POS-INV-1")

        self.assertEqual(tax_details, [])
        self.assertEqual(item_details[0]["item_code"], "CAKE-SLICE")
        self.assertEqual(
            item_details[0]["custom_ury_price_option"], "PROMO-1"
        )
        self.assertEqual(
            item_details[0]["custom_ury_price_option_label"], "Promotion"
        )
