import io
from unittest import TestCase, TextTestRunner, defaultTestLoader
from unittest.mock import patch

import frappe

from ury.ury_pos.price_options import (
    MANUAL_DISCOUNT_AMOUNT_FIELD,
    MANUAL_DISCOUNT_INPUT_FIELD,
    MANUAL_DISCOUNT_TYPE_FIELD,
    OPTION_FIELD,
    OPTION_LABEL_FIELD,
    RATE_BEFORE_MANUAL_DISCOUNT_FIELD,
    STANDARD_OPTION_ID,
    _get_used_promotion_qty,
    apply_price_option_to_row,
    get_expected_rate_after_manual_discount,
    get_item_price_options,
    resolve_price_option,
    validate_pos_invoice_price_options,
    validate_price_option_quantities,
    validate_price_option_row_prices,
)


def _raise_frappe(message, exc=frappe.ValidationError, **_kwargs):
    if isinstance(exc, type):
        raise exc(message)
    raise frappe.ValidationError(message)


def _promotion(**overrides):
    values = {
        "name": "PROMO-1",
        "item": "CAKE-SLICE",
        "label": "Promotion",
        "rate": 75,
        "allocated_qty": 4,
        "enabled": 1,
    }
    values.update(overrides)
    return frappe._dict(values)


class TestPriceOptions(TestCase):
    @patch("ury.ury_pos.price_options.frappe.db.sql")
    def test_returns_do_not_release_promotion_quota(self, db_sql):
        # The database result represents qualifying submitted/current sale rows;
        # the SQL predicate must exclude negative draft returns until submit.
        db_sql.return_value = [
            frappe._dict(
                invoice_name="POS-SALE-1",
                item_name="ROW-1",
                price_option="PROMO-1",
                used_qty=4,
            ),
            frappe._dict(
                invoice_name="POS-RETURN-SUBMITTED",
                item_name="ROW-2",
                price_option="PROMO-1",
                used_qty=-1,
            ),
        ]

        used = _get_used_promotion_qty(["PROMO-1"], for_update=True)

        self.assertEqual(used, {"PROMO-1": 4})
        query = db_sql.call_args.args[0]
        self.assertIn("invoice.docstatus = 0 AND item.docstatus = 0", query)
        self.assertIn("COALESCE(invoice.is_return, 0) = 0", query)
        self.assertIn("invoice.docstatus = 1 AND item.docstatus = 1", query)
        self.assertIn("FOR UPDATE", query)

    @patch("ury.ury_pos.price_options.frappe.db.sql", return_value=[])
    def test_preflight_usage_read_does_not_lock_invoice_rows(self, db_sql):
        self.assertEqual(_get_used_promotion_qty(["PROMO-1"]), {})
        self.assertNotIn("FOR UPDATE", db_sql.call_args.args[0])

    @patch("ury.ury_pos.price_options._get_used_promotion_qty", return_value={})
    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_authoritative_quantity_validation_locks_usage_rows(
        self,
        group_promotions,
        get_used,
    ):
        group_promotions.return_value = {"CAKE-SLICE": [_promotion()]}

        get_item_price_options(
            "Menu A",
            {"CAKE-SLICE": 100},
            {"CAKE-SLICE": 10},
            lock=True,
        )

        group_promotions.assert_called_once_with(
            "Menu A",
            item_codes=None,
            for_update=True,
        )
        get_used.assert_called_once_with(
            ["PROMO-1"],
            exclude_invoice=None,
            for_update=True,
        )

    @patch("ury.ury_pos.price_options._get_used_promotion_qty", return_value={})
    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_partitions_ten_units_into_six_normal_and_four_promotion(
        self, group_promotions, _get_used
    ):
        group_promotions.return_value = {"CAKE-SLICE": [_promotion()]}

        options = get_item_price_options(
            "Menu A",
            {"CAKE-SLICE": 100},
            {"CAKE-SLICE": 10},
        )["CAKE-SLICE"]

        self.assertEqual(
            options,
            [
                {
                    "id": STANDARD_OPTION_ID,
                    "label": "Normal",
                    "rate": 100.0,
                    "available_qty": 6.0,
                    "is_default": True,
                },
                {
                    "id": "PROMO-1",
                    "label": "Promotion",
                    "rate": 75.0,
                    "available_qty": 4.0,
                    "is_default": False,
                },
            ],
        )
        _get_used.assert_called_once_with(
            ["PROMO-1"],
            exclude_invoice=None,
            for_update=False,
        )

    @patch(
        "ury.ury_pos.price_options._get_used_promotion_qty",
        return_value={"PROMO-1": 2},
    )
    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_used_promotion_reduces_only_promotional_availability(
        self, group_promotions, _get_used
    ):
        group_promotions.return_value = {"CAKE-SLICE": [_promotion()]}

        options = get_item_price_options(
            "Menu A",
            {"CAKE-SLICE": 100},
            {"CAKE-SLICE": 8},
        )["CAKE-SLICE"]

        self.assertEqual(options[0]["available_qty"], 6)
        self.assertEqual(options[1]["available_qty"], 2)

    @patch("ury.ury_pos.price_options._get_used_promotion_qty", return_value={})
    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_keeps_normal_option_in_payload_when_its_availability_is_zero(
        self, group_promotions, _get_used
    ):
        group_promotions.return_value = {
            "CAKE-SLICE": [_promotion(allocated_qty=3)]
        }

        options = get_item_price_options(
            "Menu A",
            {"CAKE-SLICE": 100},
            {"CAKE-SLICE": 3},
        )["CAKE-SLICE"]

        self.assertEqual(
            [option["id"] for option in options],
            [STANDARD_OPTION_ID, "PROMO-1"],
        )
        self.assertEqual(options[0]["available_qty"], 0)
        self.assertEqual(options[1]["available_qty"], 3)

    @patch(
        "ury.ury_pos.price_options._get_used_promotion_qty",
        return_value={"PROMO-1": 3},
    )
    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_keeps_exhausted_promotion_in_payload_with_zero_availability(
        self, group_promotions, _get_used
    ):
        group_promotions.return_value = {
            "CAKE-SLICE": [_promotion(allocated_qty=3)]
        }

        options = get_item_price_options(
            "Menu A",
            {"CAKE-SLICE": 100},
            {"CAKE-SLICE": 5},
        )["CAKE-SLICE"]

        self.assertEqual(
            [option["id"] for option in options],
            [STANDARD_OPTION_ID, "PROMO-1"],
        )
        self.assertEqual(options[0]["available_qty"], 5)
        self.assertEqual(options[1]["available_qty"], 0)

    @patch("ury.ury_pos.price_options._get_used_promotion_qty", return_value={})
    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_configured_zero_promotion_remains_visible_and_cannot_be_sold(
        self, group_promotions, _get_used
    ):
        group_promotions.return_value = {
            "CAKE-SLICE": [_promotion(allocated_qty=0)]
        }
        base_rates = {"CAKE-SLICE": 100}
        physical_availability = {"CAKE-SLICE": 5}

        options = get_item_price_options(
            "Menu A",
            base_rates,
            physical_availability,
        )["CAKE-SLICE"]

        self.assertEqual(
            [option["id"] for option in options],
            [STANDARD_OPTION_ID, "PROMO-1"],
        )
        self.assertEqual(options[0]["available_qty"], 5)
        self.assertEqual(options[1]["available_qty"], 0)

        items = [
            frappe._dict(
                item_code="CAKE-SLICE",
                qty=1,
                conversion_factor=1,
                custom_ury_price_option="PROMO-1",
            )
        ]
        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_price_option_quantities(
                    items,
                    "Menu A",
                    base_rates,
                    physical_availability,
                )

    @patch(
        "ury.ury_pos.price_options._get_used_promotion_qty",
        return_value={"PROMO-1": -3},
    )
    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_returns_cannot_expand_promotion_beyond_allocation(
        self, group_promotions, _get_used
    ):
        group_promotions.return_value = {"CAKE-SLICE": [_promotion()]}

        options = get_item_price_options(
            "Menu A",
            {"CAKE-SLICE": 100},
            {"CAKE-SLICE": 10},
        )["CAKE-SLICE"]

        self.assertEqual(options[1]["available_qty"], 4)

    def test_resolves_standard_and_promotional_rates_authoritatively(self):
        promotions = {"CAKE-SLICE": [_promotion()]}

        standard = resolve_price_option(
            "Menu A",
            "CAKE-SLICE",
            STANDARD_OPTION_ID,
            100,
            promotions_by_item=promotions,
        )
        promotion = resolve_price_option(
            "Menu A",
            "CAKE-SLICE",
            "PROMO-1",
            100,
            promotions_by_item=promotions,
        )

        self.assertEqual((standard.rate, standard.base_rate), (100, 100))
        self.assertEqual((promotion.rate, promotion.base_rate), (75, 100))
        self.assertFalse(promotion.is_standard)

    def test_snapshot_label_is_canonical_and_only_payload_is_translated(self):
        promotions = {"CAKE-SLICE": [_promotion(label="Happy Hour")]}
        with patch(
            "ury.ury_pos.price_options._",
            side_effect=lambda value: f"translated:{value}",
        ):
            selected = resolve_price_option(
                "Menu A",
                "CAKE-SLICE",
                "PROMO-1",
                100,
                promotions_by_item=promotions,
            )
            with patch(
                "ury.ury_pos.price_options.group_menu_promotions",
                return_value=promotions,
            ), patch(
                "ury.ury_pos.price_options._get_used_promotion_qty",
                return_value={},
            ):
                payload = get_item_price_options(
                    "Menu A",
                    {"CAKE-SLICE": 100},
                    {"CAKE-SLICE": 10},
                )["CAKE-SLICE"]

        row = apply_price_option_to_row({}, selected)
        self.assertEqual(row[OPTION_LABEL_FIELD], "Happy Hour")
        self.assertEqual(payload[0]["label"], "translated:Normal")
        self.assertEqual(payload[1]["label"], "translated:Happy Hour")

    def test_rejects_forged_price_option(self):
        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                resolve_price_option(
                    "Menu A",
                    "CAKE-SLICE",
                    "FORGED",
                    100,
                    promotions_by_item={"CAKE-SLICE": [_promotion()]},
                )

    def test_rejects_promotion_that_is_not_below_current_item_price(self):
        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                resolve_price_option(
                    "Menu A",
                    "CAKE-SLICE",
                    STANDARD_OPTION_ID,
                    70,
                    promotions_by_item={"CAKE-SLICE": [_promotion(rate=75)]},
                )

    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_explicit_empty_promotion_map_does_not_query_a_menu(
        self, group_promotions
    ):
        option = resolve_price_option(None, "CAKE-SLICE", None, 100, {})

        self.assertTrue(option.is_standard)
        group_promotions.assert_not_called()

    def test_applies_discounted_rate_but_keeps_normal_price_list_rate(self):
        row = apply_price_option_to_row(
            {"item_code": "CAKE-SLICE", "qty": 1},
            frappe._dict(
                id="PROMO-1",
                label="Promotion",
                rate=75,
                base_rate=100,
            ),
        )

        self.assertEqual(row["rate"], 75)
        self.assertEqual(row["price_list_rate"], 100)
        self.assertEqual(row["base_price_list_rate"], 100)
        self.assertEqual(row[OPTION_FIELD], "PROMO-1")
        self.assertEqual(row[OPTION_LABEL_FIELD], "Promotion")

    def test_rebuilds_percent_manual_discount_from_audited_promotion_rate(self):
        item = frappe._dict(
            qty=2,
            rate=60,
            **{
                RATE_BEFORE_MANUAL_DISCOUNT_FIELD: 75,
                MANUAL_DISCOUNT_TYPE_FIELD: "Percent",
                MANUAL_DISCOUNT_INPUT_FIELD: 20,
                MANUAL_DISCOUNT_AMOUNT_FIELD: 30,
            },
        )

        self.assertEqual(get_expected_rate_after_manual_discount(item, 75), 60)

    def test_rebuilds_fixed_manual_discount_as_total_for_the_line(self):
        item = frappe._dict(
            qty=2,
            rate=62.5,
            **{
                RATE_BEFORE_MANUAL_DISCOUNT_FIELD: 75,
                MANUAL_DISCOUNT_TYPE_FIELD: "Amount",
                MANUAL_DISCOUNT_INPUT_FIELD: 25,
                MANUAL_DISCOUNT_AMOUNT_FIELD: 25,
            },
        )

        self.assertEqual(get_expected_rate_after_manual_discount(item, 75), 62.5)

    def test_rejects_inconsistent_manual_discount_snapshot(self):
        item = frappe._dict(
            qty=1,
            **{
                RATE_BEFORE_MANUAL_DISCOUNT_FIELD: 75,
                MANUAL_DISCOUNT_TYPE_FIELD: "Percent",
                MANUAL_DISCOUNT_INPUT_FIELD: 20,
                MANUAL_DISCOUNT_AMOUNT_FIELD: 10,
            },
        )

        self.assertIsNone(get_expected_rate_after_manual_discount(item, 75))

    @patch("ury.ury_pos.price_options.get_item_price_options")
    def test_rejects_quantity_beyond_selected_option(self, get_options):
        get_options.return_value = {
            "CAKE-SLICE": [
                {
                    "id": STANDARD_OPTION_ID,
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
        items = [
            frappe._dict(
                item_code="CAKE-SLICE",
                qty=5,
                conversion_factor=1,
                custom_ury_price_option="PROMO-1",
            )
        ]

        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_price_option_quantities(
                    items,
                    "Menu A",
                    {"CAKE-SLICE": 100},
                    {"CAKE-SLICE": 10},
                )
        self.assertTrue(get_options.call_args.kwargs["lock"])

    @patch("ury.ury_pos.price_options.get_item_price_options", return_value={})
    def test_rejects_option_that_was_disabled_after_menu_load(self, _get_options):
        items = [
            frappe._dict(
                item_code="CAKE-SLICE",
                qty=1,
                conversion_factor=1,
                custom_ury_price_option="PROMO-1",
            )
        ]

        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_price_option_quantities(
                    items,
                    "Menu A",
                    {"CAKE-SLICE": 100},
                    {"CAKE-SLICE": 10},
                )

    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_price_validation_ignores_translated_snapshot_label(
        self, group_promotions
    ):
        group_promotions.return_value = {"CAKE-SLICE": [_promotion()]}
        item = frappe._dict(
            item_code="CAKE-SLICE",
            rate=75,
            price_list_rate=100,
            custom_ury_price_option="PROMO-1",
            custom_ury_price_option_label="Promotion",
        )

        validate_price_option_row_prices(
            [item], "Menu A", {"CAKE-SLICE": 100}
        )
        group_promotions.assert_called_once_with(
            "Menu A", ["CAKE-SLICE"], for_update=True
        )

    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_price_validation_accepts_only_authorised_audited_manual_discount(
        self, group_promotions
    ):
        group_promotions.return_value = {"CAKE-SLICE": [_promotion()]}
        item = frappe._dict(
            item_code="CAKE-SLICE",
            qty=1,
            rate=60,
            price_list_rate=100,
            custom_ury_price_option="PROMO-1",
            custom_ury_price_option_label="Promotion",
            custom_ury_rate_before_manual_discount=75,
            custom_ury_manual_discount_type="Percent",
            custom_ury_manual_discount_input=20,
            custom_ury_manual_discount_amount=15,
        )

        validate_price_option_row_prices(
            [item],
            "Menu A",
            {"CAKE-SLICE": 100},
            manual_discounts_authorized=True,
        )

        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_price_option_row_prices(
                    [item],
                    "Menu A",
                    {"CAKE-SLICE": 100},
                    manual_discounts_authorized=False,
                )

    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_price_validation_rejects_forged_promotion_label(
        self, group_promotions
    ):
        group_promotions.return_value = {"CAKE-SLICE": [_promotion()]}
        item = frappe._dict(
            item_code="CAKE-SLICE",
            rate=75,
            price_list_rate=100,
            custom_ury_price_option="PROMO-1",
            custom_ury_price_option_label="Manager Special",
        )

        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_price_option_row_prices(
                    [item], "Menu A", {"CAKE-SLICE": 100}
                )

    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_existing_normal_row_without_option_id_remains_compatible(
        self, group_promotions
    ):
        group_promotions.return_value = {"CAKE-SLICE": [_promotion()]}
        item = frappe._dict(
            item_code="CAKE-SLICE",
            rate=100,
            price_list_rate=100,
            custom_ury_price_option=None,
        )

        validate_price_option_row_prices(
            [item], "Menu A", {"CAKE-SLICE": 100}
        )

    @patch("ury.ury_pos.price_options.group_menu_promotions")
    def test_standard_option_rejects_misleading_label(self, group_promotions):
        group_promotions.return_value = {"CAKE-SLICE": [_promotion()]}
        item = frappe._dict(
            item_code="CAKE-SLICE",
            rate=100,
            price_list_rate=100,
            custom_ury_price_option="standard",
            custom_ury_price_option_label="Promotion",
        )

        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_price_option_row_prices(
                    [item], "Menu A", {"CAKE-SLICE": 100}
                )

    @patch("ury.ury_pos.price_options.group_menu_promotions", return_value={})
    def test_missing_option_id_rejects_nonempty_label(self, _group_promotions):
        item = frappe._dict(
            item_code="LEGACY-ITEM",
            rate=100,
            price_list_rate=100,
            custom_ury_price_option=None,
            custom_ury_price_option_label="Promotion",
        )

        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_price_option_row_prices(
                    [item], "Menu A", {"LEGACY-ITEM": 100}
                )

    @patch("ury.ury_pos.price_options.group_menu_promotions", return_value={})
    def test_price_validation_rejects_stale_promotional_snapshot(
        self, _group_promotions
    ):
        item = frappe._dict(
            item_code="CAKE-SLICE",
            rate=75,
            price_list_rate=100,
            custom_ury_price_option="PROMO-1",
        )

        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_price_option_row_prices(
                    [item], "Menu A", {"CAKE-SLICE": 100}
                )


class TestPOSInvoicePriceOptionGuard(TestCase):
    def setUp(self):
        self.previous_split_flag = getattr(frappe.flags, "ury_bill_split", False)
        frappe.flags.ury_bill_split = False

    def tearDown(self):
        frappe.flags.ury_bill_split = self.previous_split_flag

    @staticmethod
    def _invoice(**overrides):
        values = {
            "name": "POS-INV-1",
            "docstatus": 0,
            "is_return": 0,
            "selling_price_list": "Menu A Price List",
            "pos_profile": "POS A",
            "set_warehouse": "Stores - A",
            "items": [
                frappe._dict(
                    name="ROW-1",
                    item_code="CAKE-SLICE",
                    qty=1,
                    stock_qty=1,
                    conversion_factor=1,
                    rate=75,
                    price_list_rate=100,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                )
            ],
        }
        values.update(overrides)
        invoice = frappe._dict(values)
        invoice.is_new = lambda: not bool(invoice.name)
        return invoice

    @patch("ury.ury_pos.price_options.validate_price_option_row_prices")
    @patch("ury.ury_pos.price_options.validate_price_option_quantities")
    @patch("ury.ury_pos.api._get_stock_details")
    @patch("ury.ury.doctype.ury_order.ury_order.get_authoritative_item_prices")
    @patch("ury.ury_pos.price_options.get_menu_promotions")
    @patch("ury.ury_pos.price_options.frappe.db.get_value", return_value="Menu A")
    def test_generic_save_locks_and_validates_authoritative_price_and_quota(
        self,
        _get_value,
        get_promotions,
        get_prices,
        get_stock,
        validate_quantities,
        validate_prices,
    ):
        invoice = self._invoice()
        get_promotions.return_value = [_promotion()]
        get_prices.return_value = {"CAKE-SLICE": 100}
        get_stock.return_value = {
            "CAKE-SLICE": {"available_qty": 4}
        }

        validate_pos_invoice_price_options(invoice)

        get_promotions.assert_called_once_with(
            "Menu A",
            item_codes=["CAKE-SLICE"],
            enabled_only=False,
            for_update=True,
        )
        get_prices.assert_called_once_with(
            ["CAKE-SLICE"], "Menu A Price List", for_update=True
        )
        get_stock.assert_called_once_with(
            ["CAKE-SLICE"],
            "Stores - A",
            exclude_invoice="POS-INV-1",
            for_update=True,
        )
        validate_quantities.assert_called_once_with(
            invoice.get("items"),
            "Menu A",
            {"CAKE-SLICE": 100},
            {"CAKE-SLICE": 4},
            exclude_invoice="POS-INV-1",
        )
        validate_prices.assert_called_once_with(
            invoice.get("items"),
            "Menu A",
            {"CAKE-SLICE": 100},
            manual_discounts_authorized=False,
        )

    @patch("ury.ury_pos.price_options.validate_price_option_row_prices")
    @patch("ury.ury_pos.price_options.validate_price_option_quantities")
    @patch("ury.ury_pos.api._get_stock_details")
    @patch("ury.ury.doctype.ury_order.ury_order.get_authoritative_item_prices")
    @patch("ury.ury_pos.price_options.get_menu_promotions")
    @patch("ury.ury_pos.price_options.frappe.db.get_value", return_value="Menu A")
    def test_manual_discount_on_one_row_fetches_all_sibling_base_rates(
        self,
        _get_value,
        get_promotions,
        get_prices,
        get_stock,
        _validate_quantities,
        _validate_prices,
    ):
        invoice = self._invoice()
        invoice["items"][0].custom_ury_rate_before_manual_discount = 75
        invoice["items"][0].custom_ury_manual_discount_type = "Percent"
        invoice["items"][0].custom_ury_manual_discount_input = 100
        invoice["items"][0].custom_ury_manual_discount_amount = 75
        invoice["items"].append(
            frappe._dict(
                name="ROW-2",
                item_code="NORMAL-ITEM",
                qty=1,
                stock_qty=1,
                conversion_factor=1,
                rate=50,
                price_list_rate=50,
            )
        )
        get_promotions.return_value = []
        get_prices.return_value = {"CAKE-SLICE": 100, "NORMAL-ITEM": 50}
        get_stock.return_value = {
            "CAKE-SLICE": {"available_qty": 4},
            "NORMAL-ITEM": {"available_qty": 4},
        }

        validate_pos_invoice_price_options(invoice)

        get_prices.assert_called_once_with(
            ["CAKE-SLICE", "NORMAL-ITEM"],
            "Menu A Price List",
            for_update=True,
        )
        get_stock.assert_called_once_with(
            ["CAKE-SLICE", "NORMAL-ITEM"],
            "Stores - A",
            exclude_invoice="POS-INV-1",
            for_update=True,
        )

    @patch("ury.ury_pos.price_options.get_menu_promotions")
    @patch("ury.ury_pos.price_options.frappe.db.get_value", return_value=None)
    def test_rejects_forged_promotion_outside_a_menu(
        self, _get_value, get_promotions
    ):
        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_pos_invoice_price_options(self._invoice())

        get_promotions.assert_not_called()

    @patch("ury.ury_pos.price_options.get_menu_promotions")
    def test_split_bill_bypasses_duplicate_reservation_validation(
        self, get_promotions
    ):
        frappe.flags.ury_bill_split = True

        validate_pos_invoice_price_options(self._invoice())

        get_promotions.assert_not_called()

    @patch("ury.ury_pos.price_options.get_menu_promotions")
    @patch("ury.ury_pos.price_options.frappe.get_doc")
    def test_unchanged_submitted_promotion_allows_header_only_save(
        self, get_doc, get_promotions
    ):
        invoice = self._invoice(docstatus=1)
        persisted = self._invoice(docstatus=1)
        persisted.customer_name = "Before"
        invoice.customer_name = "After"
        get_doc.return_value = persisted

        validate_pos_invoice_price_options(invoice)

        get_promotions.assert_not_called()

    @patch("ury.ury_pos.price_options.validate_price_option_row_prices")
    @patch("ury.ury_pos.price_options.validate_price_option_quantities")
    @patch("ury.ury_pos.api._get_stock_details")
    @patch("ury.ury.doctype.ury_order.ury_order.get_authoritative_item_prices")
    @patch("ury.ury_pos.price_options.get_menu_promotions")
    @patch("ury.ury_pos.price_options.frappe.db.get_value", return_value="Menu A")
    @patch("ury.ury_pos.price_options.frappe.get_doc")
    def test_submitted_promotion_label_tampering_is_not_treated_as_header_only(
        self,
        get_doc,
        _get_value,
        get_promotions,
        get_prices,
        get_stock,
        _validate_quantities,
        _validate_prices,
    ):
        invoice = self._invoice(docstatus=1)
        persisted = self._invoice(docstatus=1)
        invoice.get("items")[0].custom_ury_price_option_label = "Forged"
        get_doc.return_value = persisted
        get_promotions.return_value = [_promotion()]
        get_prices.return_value = {"CAKE-SLICE": 100}
        get_stock.return_value = {"CAKE-SLICE": {"available_qty": 4}}

        validate_pos_invoice_price_options(invoice)

        get_promotions.assert_called_once()

    @patch("ury.ury_pos.price_options.lock_invoice_price_options")
    @patch("ury.ury_pos.price_options.frappe.get_doc")
    def test_return_rejects_line_not_linked_to_original_even_when_normal(
        self, get_doc, _lock_options
    ):
        get_doc.return_value = self._invoice(
            name="POS-ORIGINAL",
            docstatus=1,
            items=[],
        )
        returned = self._invoice(
            name=None,
            is_return=1,
            return_against="POS-ORIGINAL",
            items=[
                frappe._dict(
                    item_code="CAKE-SLICE",
                    pos_invoice_item="NOT-AN-ORIGINAL-ROW",
                    qty=-1,
                    stock_qty=-1,
                    conversion_factor=1,
                    rate=100,
                    price_list_rate=100,
                    custom_ury_price_option="standard",
                    custom_ury_price_option_label="Normal",
                )
            ],
        )

        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_pos_invoice_price_options(returned)

    @patch("ury.ury_pos.price_options.lock_invoice_price_options")
    @patch("ury.ury_pos.price_options.frappe.get_doc")
    def test_return_must_match_historical_promotion_snapshot(
        self, get_doc, lock_options
    ):
        original = self._invoice(
            name="POS-ORIGINAL",
            docstatus=1,
            items=[
                frappe._dict(
                    name="ORIGINAL-ROW",
                    item_code="CAKE-SLICE",
                    qty=4,
                    stock_qty=4,
                    conversion_factor=1,
                    rate=75,
                    price_list_rate=100,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                )
            ],
        )
        get_doc.return_value = original
        returned = self._invoice(
            name=None,
            is_return=1,
            return_against="POS-ORIGINAL",
            items=[
                frappe._dict(
                    item_code="CAKE-SLICE",
                    sales_invoice_item="ORIGINAL-ROW",
                    qty=-2,
                    stock_qty=-2,
                    conversion_factor=1,
                    rate=75,
                    price_list_rate=100,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                )
            ],
        )

        validate_pos_invoice_price_options(returned)

        lock_options.assert_called_once_with(original)

    @patch("ury.ury_pos.price_options.lock_invoice_price_options")
    @patch("ury.ury_pos.price_options.frappe.get_doc")
    def test_return_rejects_changed_historical_promotion_rate(
        self, get_doc, _lock_options
    ):
        original = self._invoice(
            name="POS-ORIGINAL",
            docstatus=1,
            items=[
                frappe._dict(
                    name="ORIGINAL-ROW",
                    item_code="CAKE-SLICE",
                    qty=1,
                    stock_qty=1,
                    rate=75,
                    price_list_rate=100,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                )
            ],
        )
        get_doc.return_value = original
        returned = self._invoice(
            name=None,
            is_return=1,
            return_against="POS-ORIGINAL",
            items=[
                frappe._dict(
                    item_code="CAKE-SLICE",
                    sales_invoice_item="ORIGINAL-ROW",
                    qty=-1,
                    stock_qty=-1,
                    conversion_factor=1,
                    rate=70,
                    price_list_rate=100,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                )
            ],
        )

        with patch(
            "ury.ury_pos.price_options.frappe.throw", side_effect=_raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                validate_pos_invoice_price_options(returned)

    @patch("ury.ury_pos.price_options.lock_invoice_price_options")
    @patch("ury.ury_pos.price_options.frappe.get_doc")
    def test_return_rehydrates_missing_manual_discount_snapshot(
        self, get_doc, _lock_options
    ):
        original = self._invoice(
            name="POS-ORIGINAL",
            docstatus=1,
            items=[
                frappe._dict(
                    name="ORIGINAL-ROW",
                    item_code="CAKE-SLICE",
                    qty=2,
                    stock_qty=2,
                    conversion_factor=1,
                    rate=60,
                    price_list_rate=100,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                    custom_ury_rate_before_manual_discount=75,
                    custom_ury_price_option_reduction=25,
                    custom_ury_manual_discount_type="Percent",
                    custom_ury_manual_discount_input=20,
                    custom_ury_manual_discount_amount=30,
                )
            ],
        )
        get_doc.return_value = original
        returned_item = frappe._dict(
            item_code="CAKE-SLICE",
            sales_invoice_item="ORIGINAL-ROW",
            qty=-1,
            stock_qty=-1,
            conversion_factor=1,
            rate=60,
            price_list_rate=100,
            custom_ury_price_option="PROMO-1",
            custom_ury_price_option_label="Promotion",
        )
        returned = self._invoice(
            name=None,
            is_return=1,
            return_against="POS-ORIGINAL",
            items=[returned_item],
        )

        validate_pos_invoice_price_options(returned)

        self.assertEqual(returned_item.custom_ury_manual_discount_type, "Percent")
        self.assertEqual(returned_item.custom_ury_manual_discount_amount, 30)


def run_unit_tests():
    """Run the mock-only price-option regression suite through bench execute."""
    if getattr(frappe.local, "flags", None) is None:
        frappe.local.flags = frappe._dict()
    stream = io.StringIO()
    suite = defaultTestLoader.loadTestsFromModule(
        __import__(__name__, fromlist=["*"])
    )
    result = TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError(stream.getvalue())
    return {"tests_run": result.testsRun, "successful": True}


def run_extended_unit_tests():
    """Run every mock-only regression module touched by price options."""
    if getattr(frappe.local, "flags", None) is None:
        frappe.local.flags = frappe._dict()
    stream = io.StringIO()
    suite = defaultTestLoader.loadTestsFromNames(
        [
            "ury.patches.v2_0.test_install_price_options",
            "ury.ury.doctype.ury_kot.test_ury_kot",
            "ury.ury.doctype.ury_menu.test_ury_menu",
            "ury.ury.doctype.ury_order.test_ury_order",
            "ury.ury.hooks.test_ury_pos_invoice",
            "ury.ury_pos.test_invoice_api",
            "ury.ury_pos.test_price_option_locks",
            __name__,
            "ury.ury_pos.test_stock",
        ]
    )
    result = TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError(stream.getvalue())
    return {"tests_run": result.testsRun, "successful": True}
