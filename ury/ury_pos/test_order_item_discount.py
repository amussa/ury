from unittest import TestCase

import frappe

from ury.ury.doctype.ury_order.ury_order import (
    _apply_order_line_manual_discount,
    _get_price_validation_item_codes,
)


class TestOrderItemDiscount(TestCase):
    def test_discount_validation_includes_normal_sibling_rows(self):
        rows = [
            frappe._dict(item_code="Crepes"),
            frappe._dict(item_code="Arroz-Mariscos"),
        ]

        self.assertEqual(
            _get_price_validation_item_codes(rows, ["Arroz-Mariscos"]),
            ["Crepes", "Arroz-Mariscos"],
        )

    def test_discount_validation_is_skipped_without_protected_rows(self):
        rows = [frappe._dict(item_code="Crepes")]

        self.assertEqual(_get_price_validation_item_codes(rows, []), [])

    def test_percentage_is_rebuilt_from_authoritative_promotion_rate(self):
        row = {
            "item_code": "CAKE",
            "qty": 2,
            "rate": 150,
            "price_list_rate": 200,
        }

        applied = _apply_order_line_manual_discount(
            row,
            {
                "manual_discount": {
                    "type": "Percent",
                    "value": 10,
                    "reason": "Manager approval",
                }
            },
            frappe._dict(rate=150, base_rate=200),
            frappe._dict(
                custom_enable_discount=1,
                custom_ury_max_discount_percentage=50,
            ),
        )

        self.assertTrue(applied)
        self.assertEqual(row["rate"], 135)
        self.assertEqual(row["custom_ury_rate_before_manual_discount"], 150)
        self.assertEqual(row["custom_ury_manual_discount_amount"], 30)
        self.assertEqual(row["custom_ury_manual_discount_reason"], "Manager approval")
        # Standard discount fields represent Normal -> Promotion -> manual.
        self.assertEqual(row["discount_amount"], 65)
        self.assertEqual(row["discount_percentage"], 32.5)

    def test_amount_is_total_for_the_complete_line(self):
        row = {"item_code": "CAKE", "qty": 4, "rate": 80}

        _apply_order_line_manual_discount(
            row,
            {
                "manual_discount": {
                    "type": "Amount",
                    "value": 50,
                    "reason": "Service recovery",
                }
            },
            frappe._dict(rate=80, base_rate=80),
            frappe._dict(
                custom_enable_discount=1,
                custom_ury_max_discount_percentage=100,
            ),
        )

        self.assertEqual(row["rate"], 67.5)
        self.assertEqual(row["custom_ury_manual_discount_input"], 50)
        self.assertEqual(row["custom_ury_manual_discount_amount"], 50)
