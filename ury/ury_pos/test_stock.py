from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from ury.ury_pos.api import (
    _attach_price_options,
    _get_bin_qty_map,
    _get_stock_details,
    _normalise_item_codes,
    get_pos_profile_for_current_branch,
    getStockAvailability,
)


class TestPOSStockAvailability(FrappeTestCase):
    @patch("ury.ury_pos.api.frappe.db.sql")
    def test_locking_bin_read_uses_deterministic_current_read(self, db_sql):
        db_sql.return_value = [
            frappe._dict(item_code="ITEM-A", actual_qty=5)
        ]

        quantities = _get_bin_qty_map(
            ["ITEM-B", "ITEM-A"], "Main Warehouse", for_update=True
        )

        self.assertEqual(quantities, {"ITEM-A": 5})
        query, values = db_sql.call_args.args
        self.assertIn("ORDER BY item_code, name", query)
        self.assertIn("FOR UPDATE", query)
        self.assertEqual(values["item_codes"], ("ITEM-A", "ITEM-B"))

    def test_item_codes_accept_json_and_deduplicate(self):
        self.assertEqual(
            _normalise_item_codes("ITEM-A", '["ITEM-B", "ITEM-A"]'),
            ["ITEM-A", "ITEM-B"],
        )

    @patch("ury.ury_pos.api.frappe.get_doc")
    @patch("ury.ury_pos.api.getBranch")
    def test_pos_profile_must_belong_to_user_branch(self, get_branch, get_doc):
        get_branch.return_value = "Branch A"
        get_doc.return_value = frappe._dict(
            name="POS Branch B",
            branch="Branch B",
            warehouse="Warehouse B",
        )

        with self.assertRaises(frappe.PermissionError):
            get_pos_profile_for_current_branch("POS Branch B")

    @patch("ury.ury_pos.api.frappe.db.get_single_value")
    @patch("ury.ury_pos.api.get_draft_reserved_qty_map")
    @patch("ury.ury_pos.api.get_submitted_reserved_qty_map")
    @patch("ury.ury_pos.api._get_bin_qty_map")
    @patch("ury.ury_pos.api._get_active_product_bundle_codes")
    def test_direct_stock_deducts_submitted_and_draft_reservations(
        self,
        get_bundle_codes,
        get_bin_qty,
        get_submitted_reserved,
        get_draft_reserved,
        get_global_negative_stock,
    ):
        get_bundle_codes.return_value = set()
        get_bin_qty.return_value = {"ITEM-A": 5.5}
        get_submitted_reserved.return_value = {"ITEM-A": 1.25}
        get_draft_reserved.return_value = {"ITEM-A": 0.75}
        get_global_negative_stock.return_value = 0
        metadata = {
            "ITEM-A": frappe._dict(
                name="ITEM-A",
                is_stock_item=1,
                stock_uom="Kg",
                allow_negative_stock=0,
            )
        }

        details = _get_stock_details(
            ["ITEM-A"],
            "Main Warehouse",
            exclude_invoice="POS-INV-1",
            item_metadata=metadata,
        )

        self.assertEqual(details["ITEM-A"]["available_qty"], 3.5)
        self.assertTrue(details["ITEM-A"]["is_stock_item"])
        self.assertEqual(details["ITEM-A"]["stock_uom"], "Kg")
        self.assertEqual(get_draft_reserved.call_args.kwargs["exclude_invoice"], "POS-INV-1")

    @patch("ury.ury_pos.api.frappe.db.get_single_value")
    @patch("ury.ury_pos.api.get_draft_reserved_qty_map", return_value={})
    @patch("ury.ury_pos.api.get_submitted_reserved_qty_map", return_value={})
    @patch("ury.ury_pos.api._get_bin_qty_map", return_value={"ITEM-A": 5})
    @patch("ury.ury_pos.api._get_active_product_bundle_codes", return_value=set())
    def test_locking_stock_read_propagates_to_bin_and_reservations(
        self,
        _get_bundle_codes,
        get_bin_qty,
        get_submitted_reserved,
        get_draft_reserved,
        get_global_negative_stock,
    ):
        get_global_negative_stock.return_value = 0
        metadata = {
            "ITEM-A": frappe._dict(
                name="ITEM-A",
                is_stock_item=1,
                stock_uom="Unit",
                allow_negative_stock=0,
            )
        }

        _get_stock_details(
            ["ITEM-A"],
            "Main Warehouse",
            exclude_invoice="POS-INV-1",
            item_metadata=metadata,
            for_update=True,
        )

        get_bin_qty.assert_called_once_with(
            {"ITEM-A"}, "Main Warehouse", for_update=True
        )
        get_submitted_reserved.assert_called_once_with(
            {"ITEM-A"}, "Main Warehouse", for_update=True
        )
        get_draft_reserved.assert_called_once_with(
            {"ITEM-A"},
            "Main Warehouse",
            exclude_invoice="POS-INV-1",
            for_update=True,
        )

    @patch("ury.ury_pos.api.frappe.db.get_single_value")
    @patch("ury.ury_pos.api.get_draft_reserved_qty_map")
    @patch("ury.ury_pos.api.get_submitted_reserved_qty_map")
    @patch("ury.ury_pos.api._get_bin_qty_map")
    @patch("ury.ury_pos.api.get_product_bundle_stock_availability")
    @patch("ury.ury_pos.api._get_active_product_bundle_codes")
    def test_bundle_availability_uses_component_draft_reservations(
        self,
        get_bundle_codes,
        get_bundle_availability,
        get_bin_qty,
        get_submitted_reserved,
        get_draft_reserved,
        get_global_negative_stock,
    ):
        get_bundle_codes.return_value = {"COMBO"}
        get_bundle_availability.return_value = (
            [
                {"item_code": "COMP-A", "required": 2, "available": 8},
                {"item_code": "COMP-B", "required": 1, "available": 5},
            ],
            True,
            False,
        )
        get_bin_qty.return_value = {}
        get_submitted_reserved.return_value = {}
        get_draft_reserved.return_value = {"COMP-A": 2, "COMP-B": 1}
        get_global_negative_stock.return_value = 0
        metadata = {
            "COMBO": frappe._dict(
                name="COMBO",
                is_stock_item=0,
                stock_uom="Unit",
                allow_negative_stock=0,
            )
        }

        details = _get_stock_details(
            ["COMBO"], "Main Warehouse", item_metadata=metadata
        )

        # COMP-A permits 3 combos, COMP-B permits 4; the limiting component wins.
        self.assertEqual(details["COMBO"]["available_qty"], 3)
        self.assertTrue(details["COMBO"]["is_stock_item"])

    @patch("ury.ury_pos.api._get_stock_details")
    @patch("ury.ury_pos.api._get_menu_for_context", return_value="Menu A")
    @patch("ury.ury_pos.api.frappe.get_all", return_value=[])
    @patch("ury.ury_pos.api._validate_excluded_invoice")
    @patch("ury.ury_pos.api.get_pos_profile_for_current_branch")
    def test_endpoint_always_returns_stocks_map(
        self,
        get_profile,
        validate_exclusion,
        _get_all,
        _get_menu,
        get_stock_details,
    ):
        profile = frappe._dict(
            name="POS A", branch="Branch A", warehouse="Warehouse A"
        )
        get_profile.return_value = (profile, "Branch A")
        get_stock_details.return_value = {
            "ITEM-A": {
                "item_code": "ITEM-A",
                "available_qty": 2,
                "is_stock_item": True,
                "stock_uom": "Unit",
                "negative_stock_allowed": False,
            }
        }

        result = getStockAvailability(
            "POS A",
            item_codes='["ITEM-A"]',
            exclude_invoice="POS-INV-1",
        )

        self.assertEqual(set(result), {"stocks"})
        self.assertEqual(result["stocks"]["ITEM-A"]["available_qty"], 2)
        validate_exclusion.assert_called_once_with("POS-INV-1", profile)

    @patch("ury.ury_pos.api._validate_excluded_invoice")
    @patch("ury.ury_pos.api.get_pos_profile_for_current_branch")
    def test_endpoint_rejects_an_empty_item_request(
        self,
        get_profile,
        validate_exclusion,
    ):
        profile = frappe._dict(
            name="POS A", branch="Branch A", warehouse="Warehouse A"
        )
        get_profile.return_value = (profile, "Branch A")

        with self.assertRaises(frappe.ValidationError):
            getStockAvailability("POS A")

        validate_exclusion.assert_called_once_with(None, profile)

    @patch("ury.ury_pos.api.get_item_price_options")
    def test_promoted_item_exposes_total_and_partitioned_availability(
        self, get_options
    ):
        menu_items = [frappe._dict(item="ITEM-A", rate=100)]
        stock = {
            "ITEM-A": {
                "item_code": "ITEM-A",
                "available_qty": 10,
                "is_stock_item": True,
                "stock_uom": "Unit",
                "negative_stock_allowed": False,
            }
        }
        get_options.return_value = {
            "ITEM-A": [
                {
                    "id": "standard",
                    "label": "Normal",
                    "rate": 100,
                    "available_qty": 6,
                    "is_default": True,
                },
                {
                    "id": "PROMO-1",
                    "label": "Promotion",
                    "rate": 75,
                    "available_qty": 4,
                    "is_default": False,
                },
            ]
        }

        _attach_price_options("Menu A", menu_items, stock)

        self.assertEqual(stock["ITEM-A"]["total_available_qty"], 10)
        self.assertEqual(stock["ITEM-A"]["available_qty"], 6)
        self.assertEqual(stock["ITEM-A"]["price_options"][1]["available_qty"], 4)

    @patch("ury.ury_pos.api._attach_price_options")
    @patch("ury.ury_pos.api._get_stock_details")
    @patch("ury.ury_pos.api._validate_excluded_invoice")
    @patch("ury.ury_pos.api.get_pos_profile_for_current_branch")
    def test_aggregator_stock_refresh_does_not_apply_menu_promotions(
        self,
        get_profile,
        _validate_exclusion,
        get_stock_details,
        attach_price_options,
    ):
        profile = frappe._dict(
            name="POS A", branch="Branch A", warehouse="Warehouse A"
        )
        get_profile.return_value = (profile, "Branch A")
        get_stock_details.return_value = {
            "ITEM-A": {
                "item_code": "ITEM-A",
                "available_qty": 10,
                "is_stock_item": True,
                "stock_uom": "Unit",
                "negative_stock_allowed": False,
            }
        }

        result = getStockAvailability(
            "POS A",
            item_codes='["ITEM-A"]',
            order_type="Aggregators",
        )

        self.assertEqual(result["stocks"]["ITEM-A"]["available_qty"], 10)
        attach_price_options.assert_not_called()
