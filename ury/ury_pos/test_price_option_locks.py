from unittest import TestCase
from unittest.mock import patch

import frappe

from ury.ury_pos.price_options import (
    get_menu_promotions,
    lock_invoice_price_options,
    lock_menu_price_options_parent,
)


class TestPriceOptionLocks(TestCase):
    @patch("ury.ury_pos.price_options.get_menu_promotions")
    @patch("ury.ury_pos.price_options.frappe.db.get_value", return_value="Menu A")
    def test_cancel_locks_promotions_for_normal_and_promotional_rows(
        self,
        _get_value,
        get_menu_promotions,
    ):
        invoice = frappe._dict(
            selling_price_list="Menu A Price List",
            items=[
                frappe._dict(
                    item_code="CAKE-SLICE",
                    custom_ury_price_option="standard",
                ),
                frappe._dict(
                    item_code="CAKE-SLICE",
                    custom_ury_price_option="PROMO-1",
                ),
            ],
        )
        get_menu_promotions.return_value = [frappe._dict(name="PROMO-1")]

        locked = lock_invoice_price_options(invoice)

        self.assertEqual(locked, [frappe._dict(name="PROMO-1")])
        get_menu_promotions.assert_called_once_with(
            "Menu A",
            item_codes=["CAKE-SLICE"],
            enabled_only=False,
            for_update=True,
        )

    @patch("ury.ury_pos.price_options.frappe.db.sql")
    @patch("ury.ury_pos.price_options.frappe.db.get_value", return_value=None)
    def test_cancel_uses_snapshotted_option_when_menu_link_is_missing(
        self,
        _get_value,
        db_sql,
    ):
        invoice = frappe._dict(
            selling_price_list="Historical Price List",
            items=[
                frappe._dict(
                    item_code="CAKE-SLICE",
                    custom_ury_price_option="PROMO-1",
                )
            ],
        )
        db_sql.return_value = [frappe._dict(name="PROMO-1")]

        with patch(
            "ury.ury_pos.price_options.frappe.get_all",
            return_value=[frappe._dict(name="PROMO-1", parent="Menu A")],
        ):
            lock_invoice_price_options(invoice)

        self.assertEqual(db_sql.call_count, 2)
        parent_query, parent_value = db_sql.call_args_list[0].args
        self.assertIn("FROM `tabURY Menu`", parent_query)
        self.assertIn("FOR UPDATE", parent_query)
        self.assertEqual(parent_value, "Menu A")
        query, values = db_sql.call_args_list[1].args
        self.assertIn("FOR UPDATE", query)
        self.assertEqual(values["option_ids"], ("PROMO-1",))

    @patch("ury.ury_pos.price_options.frappe.db.sql")
    def test_parent_lock_is_acquired_before_option_children(self, db_sql):
        db_sql.side_effect = [[("Menu A",)], [frappe._dict(name="PROMO-1")]]

        rows = get_menu_promotions(
            "Menu A",
            item_codes=["CAKE-SLICE"],
            enabled_only=False,
            for_update=True,
        )

        self.assertEqual(rows, [frappe._dict(name="PROMO-1")])
        self.assertEqual(db_sql.call_count, 2)
        self.assertIn("FROM `tabURY Menu`", db_sql.call_args_list[0].args[0])
        self.assertIn(
            "FROM `tabURY Menu Price Option`",
            db_sql.call_args_list[1].args[0],
        )

    @patch("ury.ury_pos.price_options.frappe.db.sql", return_value=[])
    def test_missing_menu_cannot_be_treated_as_a_lock(self, _db_sql):
        with self.assertRaises(frappe.ValidationError):
            lock_menu_price_options_parent("Deleted Menu")
