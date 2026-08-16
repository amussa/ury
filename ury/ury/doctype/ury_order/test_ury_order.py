# Copyright (c) 2023, Tridz Technologies Pvt. Ltd. and contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from ury.ury.doctype.ury_order.ury_order import (
    _aggregate_order_stock_qty,
    _copy_invoice_item_fields,
    _get_stock_lock_item_codes,
    _lock_stock_bins,
    _validate_order_stock,
    cancel_order,
    get_authoritative_item_prices,
    make_invoice,
    merge_tables_batch,
    release_merge_cluster_tables,
    split_bill,
    table_transfer,
)


class TestURYOrder(FrappeTestCase):
    @patch("ury.ury.doctype.ury_order.ury_order.frappe.db.get_value", return_value=1)
    @patch("ury.ury.doctype.ury_order.ury_order.get_order_invoice")
    @patch("ury.ury.doctype.ury_order.ury_order.frappe.get_value", return_value="Dine In")
    def test_legacy_payment_is_blocked_before_mutation_for_commercial_profile(
        self, _get_value, get_order_invoice, get_profile_flag
    ):
        invoice = frappe._dict(
            name="POS-INV-1",
            pos_profile="POS Polana",
            customer="Cliente Balc\u00e3o",
            payments=[],
        )
        get_order_invoice.return_value = invoice

        with self.assertRaises(frappe.ValidationError):
            make_invoice(
                customer="OTHER-CUSTOMER",
                payments=[{"mode_of_payment": "Numer\u00e1rio", "amount": 10}],
                cashier="cashier@example.com",
                pos_profile="OTHER-PROFILE",
                owner="cashier@example.com",
                additionalDiscount=100,
                invoice="POS-INV-1",
            )

        self.assertEqual(invoice.customer, "Cliente Balc\u00e3o")
        self.assertEqual(invoice.payments, [])
        get_profile_flag.assert_called_once_with(
            "POS Profile",
            "POS Polana",
            "custom_ury_enable_commercial_checkout",
        )

    def test_merge_requires_at_least_one_target_table(self):
        with self.assertRaises(frappe.ValidationError):
            merge_tables_batch("Table 1", [])

    @patch(
        "ury.ury.doctype.ury_order.ury_order._get_merge_cluster",
        return_value=(["Table 1", "Table 2"], {}),
    )
    @patch("ury.ury.doctype.ury_order.ury_order.frappe.get_doc")
    def test_table_transfer_rejects_a_merged_source(
        self,
        get_doc,
        get_merge_cluster,
    ):
        get_doc.side_effect = [
            frappe._dict(name="Table 1", branch="Branch A"),
            frappe._dict(name="POS-INV-1"),
            frappe._dict(name="Table 3", branch="Branch A", occupied=0),
        ]

        with self.assertRaises(frappe.ValidationError):
            table_transfer("Table 1", "Table 3", "POS-INV-1")

        get_merge_cluster.assert_called_once_with("Table 1")

    @patch("ury.ury.doctype.ury_order.ury_order.frappe.db.sql")
    def test_authoritative_price_lock_is_current_and_deterministic(self, db_sql):
        db_sql.return_value = [
            frappe._dict(
                name="PRICE-A",
                item_code="ITEM-A",
                price_list_rate=100,
            ),
            frappe._dict(
                name="PRICE-B",
                item_code="ITEM-B",
                price_list_rate=200,
            ),
        ]

        prices = get_authoritative_item_prices(
            ["ITEM-B", "ITEM-A"], "Menu Price List", for_update=True
        )

        self.assertEqual(prices, {"ITEM-B": 200, "ITEM-A": 100})
        query, values = db_sql.call_args.args
        self.assertIn("FROM `tabItem Price`", query)
        self.assertIn("ORDER BY item_code, name", query)
        self.assertIn("FOR UPDATE", query)
        self.assertEqual(values["item_codes"], ("ITEM-A", "ITEM-B"))

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
    @patch("ury.ury.doctype.ury_order.ury_order.get_submitted_reserved_qty_map")
    @patch("ury.ury.doctype.ury_order.ury_order.get_stock_availability")
    @patch("ury.ury.doctype.ury_order.ury_order._get_active_product_bundle_codes")
    def test_stock_lock_order_is_submitted_before_draft(
        self,
        get_bundle_codes,
        get_availability,
        get_submitted_reserved,
        get_draft_reserved,
    ):
        events = []
        get_bundle_codes.return_value = set()
        get_availability.return_value = (5, True, False)
        get_submitted_reserved.side_effect = lambda *_args, **_kwargs: (
            events.append("submitted") or {}
        )
        get_draft_reserved.side_effect = lambda *_args, **_kwargs: (
            events.append("draft") or {}
        )

        _validate_order_stock(
            {"ITEM-A": 1},
            "Main Warehouse",
            locked_bin_qty={"ITEM-A": 5},
        )

        self.assertEqual(events, ["submitted", "draft"])

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

    def test_split_line_preserves_price_option_snapshot(self):
        item = frappe._dict(
            item_code="CAKE-SLICE",
            item_name="Cake Slice",
            rate=80,
            price_list_rate=100,
            base_price_list_rate=100,
            comment="no cream",
            custom_course="Dessert",
            custom_ury_price_option="PROMO-1",
            custom_ury_price_option_label="Promotion",
            cost_center="Main - G",
            uom="Nos",
            conversion_factor=1,
            warehouse="Main - G",
        )

        copied = _copy_invoice_item_fields(item, 2)

        self.assertEqual(copied["custom_ury_price_option"], "PROMO-1")
        self.assertEqual(copied["custom_ury_price_option_label"], "Promotion")
        self.assertEqual(copied["rate"], 80)
        self.assertEqual(copied["price_list_rate"], 100)

    @patch("ury.ury.doctype.ury_order.ury_order.lock_invoice_price_options")
    @patch("ury.ury.doctype.ury_order.ury_order.frappe.get_doc")
    def test_split_locks_price_options_before_validating_selection(
        self,
        get_doc,
        lock_price_options,
    ):
        source = frappe._dict(
            docstatus=0,
            items=[],
        )
        get_doc.return_value = source

        with self.assertRaises(frappe.ValidationError):
            split_bill("POS-INV-1", [])

        lock_price_options.assert_called_once_with(source)

    @patch("ury.ury.doctype.ury_order.ury_order.frappe.db.set_value")
    @patch("ury.ury.doctype.ury_order.ury_order.frappe.db.sql")
    @patch("ury.ury.doctype.ury_order.ury_order.cancel_kot")
    @patch("ury.ury.doctype.ury_order.ury_order.release_merge_cluster_tables")
    @patch("ury.ury.doctype.ury_order.ury_order.lock_invoice_price_options")
    @patch("ury.ury.doctype.ury_order.ury_order.frappe.get_doc")
    def test_custom_cancel_locks_price_options_before_releasing_invoice(
        self,
        get_doc,
        lock_price_options,
        release_tables,
        cancel_kot,
        _db_sql,
        _set_value,
    ):
        events = []
        invoice = frappe._dict(
            restaurant_table="Table 1",
            docstatus=0,
            custom_ury_settlement=None,
        )
        invoice.check_permission = MagicMock()
        get_doc.return_value = invoice
        lock_price_options.side_effect = lambda _invoice: events.append("lock")
        release_tables.side_effect = (
            lambda _table, **_kwargs: events.append("release")
        )
        cancel_kot.side_effect = lambda _invoice: events.append("kot")

        cancel_order("POS-INV-1", "Customer request")

        self.assertEqual(events, ["lock", "release", "kot"])
        lock_price_options.assert_called_once_with(invoice)
        release_tables.assert_called_once_with("Table 1", commit=False)

    @patch("ury.ury.doctype.ury_order.ury_order.frappe.db.commit")
    @patch("ury.ury.doctype.ury_order.ury_order.frappe.db.set_value")
    def test_table_release_can_remain_in_callers_transaction(
        self,
        _set_value,
        commit,
    ):
        release_merge_cluster_tables(["Table 1"], commit=False)

        commit.assert_not_called()
