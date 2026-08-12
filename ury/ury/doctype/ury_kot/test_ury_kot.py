# Copyright (c) 2023, Tridz Technologies Pvt. Ltd. and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from ury.ury.api.ury_kot_generate import (
    compare_two_array,
    create_cancel_kot_doc,
    create_order_items,
    get_removed_items,
)


class TestURYKOT(FrappeTestCase):
    def test_price_options_are_one_kitchen_line(self):
        items = [
            frappe._dict(
                item_code="CAKE-SLICE",
                item_name="Cake Slice",
                qty=6,
                comments="",
                custom_ury_price_option="standard",
                rate=100,
            ),
            frappe._dict(
                item_code="CAKE-SLICE",
                item_name="Cake Slice",
                qty=4,
                comments="",
                custom_ury_price_option="PROMO-1",
                rate=80,
            ),
        ]

        self.assertEqual(
            create_order_items(items),
            [
                {
                    "item_code": "CAKE-SLICE",
                    "item_name": "Cake Slice",
                    "qty": 10.0,
                    "comments": "",
                }
            ],
        )

    def test_kitchen_lines_preserve_distinct_comments(self):
        items = [
            {
                "item_code": "CAKE-SLICE",
                "item_name": "Cake Slice",
                "qty": 1,
                "comments": "no cream",
            },
            {
                "item_code": "CAKE-SLICE",
                "item_name": "Cake Slice",
                "qty": 2,
                "comments": "extra cream",
            },
        ]

        self.assertEqual(len(create_order_items(items)), 2)

    def test_price_partition_change_generates_only_net_kitchen_delta(self):
        previous = [
            {
                "item_code": "CAKE-SLICE",
                "item_name": "Cake Slice",
                "qty": 6,
                "comments": "",
                "custom_ury_price_option": "standard",
            },
            {
                "item_code": "CAKE-SLICE",
                "item_name": "Cake Slice",
                "qty": 4,
                "comments": "",
                "custom_ury_price_option": "PROMO-1",
            },
        ]
        current = [
            {
                "item_code": "CAKE-SLICE",
                "item_name": "Cake Slice",
                "qty": 6,
                "comments": "",
                "custom_ury_price_option": "standard",
            },
            {
                "item_code": "CAKE-SLICE",
                "item_name": "Cake Slice",
                "qty": 3,
                "comments": "",
                "custom_ury_price_option": "PROMO-1",
            },
        ]

        self.assertEqual(
            compare_two_array(current, previous),
            [
                {
                    "item_code": "CAKE-SLICE",
                    "item_name": "Cake Slice",
                    "qty": -1.0,
                    "comments": "",
                }
            ],
        )
        self.assertEqual(get_removed_items(previous, current), [])

    @patch("ury.ury.api.ury_kot_generate.getBranch", return_value="Branch A")
    @patch("ury.ury.api.ury_kot_generate.frappe.db.get_value")
    @patch("ury.ury.api.ury_kot_generate.frappe.db.get_list", return_value=[])
    @patch("ury.ury.api.ury_kot_generate.frappe.get_doc")
    def test_cancel_kot_does_not_duplicate_same_item_price_lines(
        self,
        get_doc,
        _get_list,
        get_value,
        _get_branch,
    ):
        pos_invoice = frappe._dict(
            custom_ury_order_number="42",
            order_type="Dine In",
        )
        cancel_kot = MagicMock()

        def get_document(doctype, name=None):
            if doctype == "POS Invoice":
                return pos_invoice
            if isinstance(doctype, dict):
                return cancel_kot
            raise AssertionError((doctype, name))

        get_doc.side_effect = get_document
        get_value.return_value = "Menu A"

        create_cancel_kot_doc(
            "POS-INV-1",
            None,
            [
                {
                    "item_code": "CAKE-SLICE",
                    "item_name": "Cake Slice",
                    "qty": -1,
                    "comments": "",
                }
            ],
            "Partially cancelled",
            "Walk In",
            None,
            "POS A",
            "CNCL-.KOT-.####",
            [
                {
                    "item_code": "CAKE-SLICE",
                    "item_name": "Cake Slice",
                    "qty": 6,
                    "comments": "",
                    "custom_ury_price_option": "standard",
                },
                {
                    "item_code": "CAKE-SLICE",
                    "item_name": "Cake Slice",
                    "qty": 4,
                    "comments": "",
                    "custom_ury_price_option": "PROMO-1",
                },
            ],
            "Kitchen",
        )

        cancel_kot.append.assert_called_once()
        _fieldname, values = cancel_kot.append.call_args.args
        self.assertEqual(values["cancelled_qty"], 1.0)
        self.assertEqual(values["quantity"], 10.0)
