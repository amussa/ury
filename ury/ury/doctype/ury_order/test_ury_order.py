# Copyright (c) 2023, Tridz Technologies Pvt. Ltd. and contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from ury.ury.doctype.ury_order.ury_order import (
    _aggregate_order_stock_qty,
    _get_stock_lock_item_codes,
    _lock_stock_bins,
    _validate_order_stock,
)


class TestURYOrder(FrappeTestCase):
    def test_aggregate_order_stock_qty_handles_duplicates_and_fractional_qty(self):
        quantities = _aggregate_order_stock_qty(
            [
                {"item_code": "ITEM-A", "qty": 0.75, "conversion_factor": 1},
                {"item_code": "ITEM-A", "qty": 0.25, "conversion_factor": 2},
                {"item_code": "SERVICE", "qty": 0.5, "conversion_factor": 1},
            ]
        )

        self.assertEqual(quantities, {"ITEM-A": 1.25, "SERVICE": 0.5})

    @patch("ury.ury.doctype.ury_order.ury_order.get_draft_reserved_qty_map")
    @patch("ury.ury.doctype.ury_order.ury_order.get_stock_availability")
    @patch("ury.ury.doctype.ury_order.ury_order._get_active_product_bundle_codes")
    def test_stock_validation_aggregates_duplicates_and_draft_reservations(
        self,
        get_bundle_codes,
        get_availability,
        get_draft_reserved,
    ):
        get_bundle_codes.return_value = set()
        get_availability.return_value = (2, True, False)
        get_draft_reserved.return_value = {"ITEM-A": 0.8}

        with self.assertRaises(frappe.ValidationError):
            _validate_order_stock(
                {"ITEM-A": 1.25},
                "Main Warehouse",
                exclude_invoice="POS-INV-1",
            )

        get_availability.assert_called_once_with("ITEM-A", "Main Warehouse")
        get_draft_reserved.assert_called_once()
        self.assertEqual(get_draft_reserved.call_args.kwargs["exclude_invoice"], "POS-INV-1")

    @patch("ury.ury.doctype.ury_order.ury_order.get_draft_reserved_qty_map")
    @patch("ury.ury.doctype.ury_order.ury_order.get_stock_availability")
    @patch("ury.ury.doctype.ury_order.ury_order._get_active_product_bundle_codes")
    def test_non_stock_item_does_not_block_order(
        self,
        get_bundle_codes,
        get_availability,
        get_draft_reserved,
    ):
        get_bundle_codes.return_value = set()
        get_availability.return_value = (0, False, False)
        get_draft_reserved.return_value = {}

        _validate_order_stock({"SERVICE": 3.5}, "Main Warehouse")

        get_draft_reserved.assert_called_once()
        self.assertEqual(list(get_draft_reserved.call_args.args[0]), [])

    @patch("ury.ury.doctype.ury_order.ury_order.get_draft_reserved_qty_map")
    @patch("ury.ury.doctype.ury_order.ury_order.get_stock_availability")
    @patch("ury.ury.doctype.ury_order.ury_order._get_active_product_bundle_codes")
    def test_negative_stock_setting_does_not_bypass_gelatiamo_rule(
        self,
        get_bundle_codes,
        get_availability,
        get_draft_reserved,
    ):
        get_bundle_codes.return_value = set()
        get_availability.return_value = (0, True, True)
        get_draft_reserved.return_value = {}

        with self.assertRaises(frappe.ValidationError):
            _validate_order_stock({"ITEM-A": 0.25}, "Main Warehouse")

    @patch("ury.ury.doctype.ury_order.ury_order.get_draft_reserved_qty_map")
    @patch(
        "ury.ury.doctype.ury_order.ury_order.get_product_bundle_stock_availability"
    )
    @patch("ury.ury.doctype.ury_order.ury_order._get_active_product_bundle_codes")
    def test_product_bundle_uses_aggregated_qty_and_component_draft_reservation(
        self,
        get_bundle_codes,
        get_bundle_availability,
        get_draft_reserved,
    ):
        get_bundle_codes.return_value = {"COMBO"}
        get_bundle_availability.return_value = (
            [
                {
                    "item_code": "COMPONENT-A",
                    "required": 2.5,
                    "available": 3,
                }
            ],
            True,
            False,
        )
        get_draft_reserved.return_value = {"COMPONENT-A": 1}

        with self.assertRaises(frappe.ValidationError):
            _validate_order_stock({"COMBO": 1.25}, "Main Warehouse")

        get_bundle_availability.assert_called_once_with(
            "COMBO", "Main Warehouse", 1.25
        )

    @patch("ury.ury.doctype.ury_order.ury_order.frappe.get_all")
    def test_product_bundle_components_are_included_in_stock_locks(self, get_all):
        get_all.return_value = ["COMPONENT-B", "COMPONENT-A"]

        lock_items = _get_stock_lock_item_codes({"COMBO", "ITEM-A"})

        self.assertEqual(lock_items, ["COMBO", "COMPONENT-A", "COMPONENT-B", "ITEM-A"])

    @patch("ury.ury.doctype.ury_order.ury_order.frappe.db.sql")
    def test_stock_bins_are_locked_in_deterministic_order(self, db_sql):
        _lock_stock_bins("Main Warehouse", ["ITEM-B", "ITEM-A"])

        query, values = db_sql.call_args.args
        self.assertIn("FOR UPDATE", query)
        self.assertEqual(values["item_codes"], ("ITEM-A", "ITEM-B"))
