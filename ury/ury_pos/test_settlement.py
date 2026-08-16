from unittest import TestCase
from unittest.mock import patch

import frappe

from ury.ury_pos.settlement import (
    SettlementValidationError,
    _default_leaf_customer_group,
    _idempotency_savepoint,
    _strict_boolean,
    allocate_payment_rows,
    allocate_proportionally,
    make_revision,
    normalise_discounts,
    normalise_payments,
    validate_explicit_house_offer,
)

PAYMENT_MODES = {
    "Numerário": {"type": "Cash"},
    "M-Pesa": {"type": "Phone"},
    "Cartão": {"type": "Bank"},
}


class TestSettlementPureRules(TestCase):
    @patch("ury.ury_pos.settlement.frappe.db.get_value")
    def test_new_customer_uses_existing_customer_leaf_when_default_is_group(
        self, get_value
    ):
        get_value.side_effect = (1, "Individual", 0)

        self.assertEqual(
            _default_leaf_customer_group("Todos os grupos de clientes", "Cliente Balcão"),
            "Individual",
        )

    def test_uuid_idempotency_key_produces_sql_safe_savepoint(self):
        savepoint = _idempotency_savepoint("550e8400-e29b-41d4-a716-446655440000")
        self.assertRegex(savepoint, r"^ury_settlement_[a-f0-9]{12}$")

    def test_accepts_only_real_json_booleans(self):
        self.assertTrue(_strict_boolean(True, "Flag"))
        self.assertFalse(_strict_boolean(False, "Flag"))
        self.assertTrue(_strict_boolean(1, "Flag"))
        self.assertFalse(_strict_boolean(0, "Flag"))
        for value in ("true", "false", 2, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(SettlementValidationError):
                    _strict_boolean(value, "Flag")

    def test_normalises_distinct_authorised_payments(self):
        rows = normalise_payments(
            [
                {"mode_of_payment": "Numerário", "amount": "500.00"},
                {"mode_of_payment": "M-Pesa", "amount": 250},
            ],
            PAYMENT_MODES,
        )

        self.assertEqual(
            rows,
            [
                {"mode_of_payment": "Numerário", "amount": 500.0, "type": "Cash"},
                {"mode_of_payment": "M-Pesa", "amount": 250.0, "type": "Phone"},
            ],
        )

    def test_rejects_duplicate_payment_mode(self):
        with self.assertRaises(SettlementValidationError):
            normalise_payments(
                [
                    {"mode_of_payment": "Numerário", "amount": 100},
                    {"mode_of_payment": "Numerário", "amount": 200},
                ],
                PAYMENT_MODES,
            )

    def test_rejects_unknown_nonpositive_and_nonfinite_payments(self):
        bad_rows = (
            {"mode_of_payment": "Bitcoin", "amount": 100},
            {"mode_of_payment": "Numerário", "amount": 0},
            {"mode_of_payment": "Numerário", "amount": -1},
            {"mode_of_payment": "Numerário", "amount": "NaN"},
            {"mode_of_payment": "Numerário", "amount": "Infinity"},
        )
        for row in bad_rows:
            with self.subTest(row=row):
                with self.assertRaises(SettlementValidationError):
                    normalise_payments([row], PAYMENT_MODES)

    def test_normalises_item_and_invoice_discounts(self):
        result = normalise_discounts(
            {
                "items": [
                    {
                        "pos_invoice": "POS-1",
                        "item_row": "ROW-1",
                        "type": "Amount",
                        "value": "25",
                    }
                ],
                "invoice": {"type": "Percent", "value": 10},
            },
            ["POS-1"],
            {("POS-1", "ROW-1")},
        )

        self.assertEqual(result["items"][0]["value"], 25.0)
        self.assertEqual(result["invoice"], {"type": "Percent", "value": 10.0})

    def test_zero_total_requires_explicit_house_offer(self):
        with self.assertRaises(SettlementValidationError):
            validate_explicit_house_offer(0, house_offer=False)

        validate_explicit_house_offer(0, house_offer=True)

    def test_full_discount_on_one_item_is_valid_when_an_amount_remains(self):
        validate_explicit_house_offer(100, house_offer=False)

    def test_rejects_duplicate_or_foreign_discount_rows(self):
        valid = {
            "pos_invoice": "POS-1",
            "item_row": "ROW-1",
            "type": "Percent",
            "value": 10,
        }
        with self.assertRaises(SettlementValidationError):
            normalise_discounts(
                {"items": [valid, dict(valid)]},
                ["POS-1"],
                {("POS-1", "ROW-1")},
            )
        with self.assertRaises(SettlementValidationError):
            normalise_discounts(
                {"items": [{**valid, "item_row": "FORGED"}]},
                ["POS-1"],
                {("POS-1", "ROW-1")},
            )

    def test_proportional_allocation_assigns_rounding_residual_once(self):
        self.assertEqual(
            allocate_proportionally(100, [1, 1, 1], precision=2),
            [33.33, 33.33, 33.34],
        )

    def test_exact_payment_is_distributed_across_merged_invoices(self):
        plan = allocate_payment_rows(
            [
                {"mode_of_payment": "M-Pesa", "amount": 500, "type": "Phone"},
                {"mode_of_payment": "Numerário", "amount": 500, "type": "Cash"},
            ],
            [600, 400],
            allow_credit=False,
        )

        self.assertEqual(plan["paid_now"], 1000)
        self.assertEqual(plan["credit_amount"], 0)
        self.assertEqual(plan["change_amount"], 0)
        self.assertEqual(
            sum(row["amount"] for rows in plan["allocations"] for row in rows),
            1000,
        )

    def test_partial_credit_keeps_only_the_real_tenders(self):
        plan = allocate_payment_rows(
            [{"mode_of_payment": "M-Pesa", "amount": 400, "type": "Phone"}],
            [600, 400],
            allow_credit=True,
        )

        self.assertEqual(plan["paid_now"], 400)
        self.assertEqual(plan["credit_amount"], 600)
        self.assertEqual(plan["change_amount"], 0)

    def test_full_credit_has_no_browser_payment(self):
        plan = allocate_payment_rows([], [1000], allow_credit=True)

        self.assertEqual(plan["allocations"], [[]])
        self.assertEqual(plan["paid_now"], 0)
        self.assertEqual(plan["credit_amount"], 1000)

    def test_short_normal_payment_is_not_silently_converted_to_credit(self):
        with self.assertRaises(SettlementValidationError):
            allocate_payment_rows(
                [{"mode_of_payment": "Numerário", "amount": 999, "type": "Cash"}],
                [1000],
                allow_credit=False,
            )

    def test_credit_cannot_be_fully_paid_or_overpaid(self):
        for amount in (1000, 1100):
            with self.subTest(amount=amount):
                with self.assertRaises(SettlementValidationError):
                    allocate_payment_rows(
                        [{"mode_of_payment": "Numerário", "amount": amount, "type": "Cash"}],
                        [1000],
                        allow_credit=True,
                    )

    def test_non_cash_overpayment_is_rejected(self):
        with self.assertRaises(SettlementValidationError):
            allocate_payment_rows(
                [{"mode_of_payment": "M-Pesa", "amount": 1100, "type": "Phone"}],
                [1000],
                allow_credit=False,
            )

    def test_cash_overpayment_is_change_not_credit(self):
        plan = allocate_payment_rows(
            [
                {"mode_of_payment": "Cartão", "amount": 500, "type": "Bank"},
                {"mode_of_payment": "Numerário", "amount": 600, "type": "Cash"},
            ],
            [1000],
            allow_credit=False,
        )

        self.assertEqual(plan["tendered_amount"], 1100)
        self.assertEqual(plan["paid_now"], 1000)
        self.assertEqual(plan["change_amount"], 100)
        self.assertEqual(plan["credit_amount"], 0)

    @staticmethod
    def _invoice(name="POS-1", rate=100):
        return frappe._dict(
            name=name,
            modified="2026-08-16 12:00:00",
            docstatus=0,
            status="Draft",
            customer="Cliente Balcão",
            branch="Polana",
            pos_profile="POS Polana",
            selling_price_list="Menu Polana",
            taxes_and_charges=None,
            custom_merged_pos_invoice=None,
            items=[
                frappe._dict(
                    name=f"{name}-ROW-1",
                    item_code="ITEM-1",
                    qty=1,
                    rate=rate,
                    price_list_rate=100,
                    custom_ury_price_option="standard",
                    custom_ury_price_option_label="Normal",
                )
            ],
            payments=[],
        )

    def test_revision_is_order_independent_but_detects_commercial_change(self):
        first = self._invoice("POS-1")
        second = self._invoice("POS-2")
        revision = make_revision([first, second])

        self.assertEqual(revision, make_revision([second, first]))
        first["items"][0].rate = 99
        self.assertNotEqual(revision, make_revision([first, second]))
