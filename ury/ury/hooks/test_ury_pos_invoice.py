import io
from datetime import datetime
from unittest import TestCase, TextTestRunner, defaultTestLoader
from unittest.mock import MagicMock, patch

import frappe

from ury.ury.hooks import ury_pos_invoice


class TestURYPosInvoiceHooks(TestCase):
    @patch(
        "ury.ury.hooks.ury_pos_invoice.now_datetime",
        return_value=datetime(2026, 8, 15, 18, 30, 0),
    )
    def test_calculate_times_accepts_string_creation(self, _now_datetime):
        invoice = frappe._dict(creation="2026-08-15 18:26:43.000000")

        ury_pos_invoice.calculate_and_set_times(invoice, "before_submit")

        self.assertEqual(invoice.arrived_time, datetime(2026, 8, 15, 18, 26, 43))
        self.assertEqual(invoice.total_spend_time, "00:03:17")

    @patch("ury.ury.hooks.ury_pos_invoice.validate_pos_invoice_price_options")
    @patch("ury.ury.hooks.ury_pos_invoice.validate_price_list")
    @patch("ury.ury.hooks.ury_pos_invoice.validate_customer")
    @patch("ury.ury.hooks.ury_pos_invoice.validate_invoice")
    @patch("ury.ury.hooks.ury_pos_invoice.assign_single_cashier_from_opening")
    def test_validate_enforces_price_options_after_resolving_price_list(
        self,
        assign_cashier,
        validate_invoice,
        validate_customer,
        validate_price_list,
        validate_price_options,
    ):
        events = []
        invoice = MagicMock()
        validate_price_list.side_effect = lambda *_args: events.append("price-list")
        validate_price_options.side_effect = lambda *_args: events.append(
            "price-options"
        )

        ury_pos_invoice.validate(invoice, "validate")

        assign_cashier.assert_called_once_with(invoice)
        validate_invoice.assert_called_once_with(invoice, "validate")
        validate_customer.assert_called_once_with(invoice, "validate")
        self.assertEqual(events, ["price-list", "price-options"])
        validate_price_options.assert_called_once_with(invoice)

    @patch("ury.ury.hooks.ury_pos_invoice.ro_reload_submit")
    @patch("ury.ury.hooks.ury_pos_invoice.calculate_and_set_times")
    @patch("ury.ury.hooks.ury_pos_invoice.validate_commercial_settlement")
    @patch("ury.ury.hooks.ury_pos_invoice.assign_single_cashier_from_opening")
    def test_unprinted_table_invoice_can_be_submitted(
        self,
        assign_cashier,
        validate_settlement,
        calculate_times,
        reload_submit,
    ):
        invoice = MagicMock(
            name="POS-INV-1",
            restaurant_table="Mesa 1",
            invoice_printed=0,
        )

        ury_pos_invoice.before_submit(invoice, "before_submit")

        assign_cashier.assert_called_once_with(invoice)
        validate_settlement.assert_called_once_with(invoice)
        calculate_times.assert_called_once_with(invoice, "before_submit")
        reload_submit.assert_called_once_with(invoice, "before_submit")

    def test_full_payment_does_not_require_a_commercial_settlement(self):
        invoice = frappe._dict(
            is_return=0,
            grand_total=100,
            rounded_total=100,
            payments=[frappe._dict(amount=100)],
        )

        ury_pos_invoice.validate_commercial_settlement(invoice)

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.throw")
    def test_zero_rounded_total_does_not_hide_an_unpaid_grand_total(self, throw):
        throw.side_effect = RuntimeError
        invoice = frappe._dict(
            is_return=0,
            grand_total=100,
            rounded_total=0,
            payments=[frappe._dict(amount=0)],
            custom_ury_settlement=None,
            custom_ury_settlement_type=None,
        )

        with self.assertRaises(RuntimeError):
            ury_pos_invoice.validate_commercial_settlement(invoice)

        self.assertIn("Conceder cr\u00e9dito", throw.call_args.args[0])

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.db.get_value")
    @patch("ury.ury.hooks.ury_pos_invoice.frappe.throw")
    def test_legacy_discount_is_rejected_when_commercial_checkout_is_enabled(
        self, throw, get_value
    ):
        throw.side_effect = RuntimeError
        get_value.return_value = frappe._dict(
            customer="Cliente Balc\u00e3o",
            allow_partial_payment=1,
            custom_ury_enable_commercial_checkout=1,
            custom_ury_enable_credit_sales=1,
        )
        invoice = frappe._dict(
            is_return=0,
            pos_profile="POS Polana",
            grand_total=90,
            rounded_total=90,
            payments=[frappe._dict(amount=90)],
            additional_discount_percentage=10,
            custom_ury_settlement=None,
            custom_ury_settlement_type=None,
        )

        with self.assertRaises(RuntimeError):
            ury_pos_invoice.validate_commercial_settlement(invoice)

        self.assertIn("commercial checkout", throw.call_args.args[0])

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.throw")
    def test_short_payment_outside_settlement_service_is_rejected(self, throw):
        throw.side_effect = RuntimeError
        invoice = frappe._dict(
            is_return=0,
            grand_total=100,
            rounded_total=100,
            payments=[frappe._dict(amount=40)],
            custom_ury_settlement=None,
            custom_ury_settlement_type=None,
        )

        with self.assertRaises(RuntimeError):
            ury_pos_invoice.validate_commercial_settlement(invoice)

        throw.assert_called_once()
        self.assertIn("Conceder cr\u00e9dito", throw.call_args.args[0])

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.db.get_value")
    @patch("ury.ury.hooks.ury_pos_invoice.frappe.throw")
    def test_commercial_profile_rejects_direct_full_payment(self, throw, get_value):
        throw.side_effect = RuntimeError
        get_value.return_value = frappe._dict(
            customer="Cliente Balc\u00e3o",
            allow_partial_payment=1,
            custom_ury_enable_commercial_checkout=1,
            custom_ury_enable_credit_sales=1,
        )
        invoice = frappe._dict(
            is_return=0,
            pos_profile="POS Polana",
            grand_total=100,
            rounded_total=100,
            payments=[frappe._dict(amount=100)],
            custom_ury_settlement=None,
            custom_ury_settlement_type=None,
        )

        with self.assertRaises(RuntimeError):
            ury_pos_invoice.validate_commercial_settlement(invoice)

        self.assertIn("every invoice", throw.call_args.args[0])

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.db.get_value")
    def test_short_payment_from_authorised_credit_settlement_is_accepted(
        self, get_value
    ):
        get_value.return_value = frappe._dict(
            customer="Cliente Balc\u00e3o",
            allow_partial_payment=1,
            custom_ury_enable_commercial_checkout=1,
            custom_ury_enable_credit_sales=1,
        )
        invoice = frappe._dict(
            is_return=0,
            grand_total=100,
            rounded_total=100,
            payments=[frappe._dict(amount=40)],
            pos_profile="POS Polana",
            customer="Sal\u00e9sio David",
            custom_ury_settlement="URY-SET-0001",
            custom_ury_settlement_type="Partial Credit",
            custom_ury_credit_due_date="2026-09-14",
            custom_ury_credit_amount=60,
        )
        previous = getattr(frappe.flags, "ury_pos_settlement", None)
        frappe.flags.ury_pos_settlement = "URY-SET-0001"
        try:
            ury_pos_invoice.validate_commercial_settlement(invoice)
        finally:
            frappe.flags.ury_pos_settlement = previous

        get_value.assert_called_once_with(
            "POS Profile",
            "POS Polana",
            [
                "customer",
                "allow_partial_payment",
                "custom_ury_enable_commercial_checkout",
                "custom_ury_enable_credit_sales",
            ],
            as_dict=True,
        )

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.throw")
    def test_fully_paid_invoice_cannot_be_marked_as_credit(self, throw):
        invoice = frappe._dict(
            is_return=0,
            grand_total=100,
            rounded_total=100,
            payments=[frappe._dict(amount=100)],
            custom_ury_settlement_type="Full Credit",
        )

        ury_pos_invoice.validate_commercial_settlement(invoice)

        throw.assert_called_once()
        self.assertIn("outstanding balance", throw.call_args.args[0])

    def test_fully_paid_member_is_allowed_inside_active_merged_credit(self):
        invoice = frappe._dict(
            is_return=0,
            grand_total=100,
            rounded_total=100,
            payments=[frappe._dict(amount=100)],
            custom_ury_settlement="URY-SET-0001",
            custom_ury_settlement_type="Partial Credit",
            custom_ury_credit_amount=0,
        )
        previous = getattr(frappe.flags, "ury_pos_settlement", None)
        frappe.flags.ury_pos_settlement = "URY-SET-0001"
        try:
            ury_pos_invoice.validate_commercial_settlement(invoice)
        finally:
            frappe.flags.ury_pos_settlement = previous

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.db.get_value")
    @patch("ury.ury.hooks.ury_pos_invoice.frappe.throw")
    def test_unconsolidated_credit_return_is_blocked(self, throw, get_value):
        throw.side_effect = RuntimeError
        get_value.return_value = frappe._dict(
            custom_ury_settlement="URY-SET-0001",
            custom_ury_settlement_type="Full Credit",
            consolidated_invoice=None,
            pos_profile="POS Polana",
        )
        invoice = frappe._dict(
            is_return=1,
            return_against="POS-1",
            pos_profile="POS Polana",
        )

        with self.assertRaises(RuntimeError):
            ury_pos_invoice.validate_commercial_settlement(invoice)

        self.assertIn("temporarily unavailable", throw.call_args.args[0])

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.throw")
    @patch("ury.ury.hooks.ury_pos_invoice.frappe.db.get_value")
    def test_settled_pos_return_requires_approved_accounting_correction(
        self, get_value, throw
    ):
        throw.side_effect = RuntimeError
        get_value.side_effect = [
            frappe._dict(
                custom_ury_settlement="URY-SET-0001",
                custom_ury_settlement_type="Full Credit",
                consolidated_invoice="SINV-1",
                pos_profile="POS Polana",
            ),
            1,
        ]
        invoice = frappe._dict(
            is_return=1,
            return_against="POS-1",
            pos_profile="POS Polana",
        )

        with self.assertRaises(RuntimeError):
            ury_pos_invoice.validate_commercial_settlement(invoice)

        self.assertIn("accounting correction", throw.call_args.args[0])

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.throw")
    @patch("ury.ury.hooks.ury_pos_invoice.frappe.db.get_value")
    def test_commercial_checkout_blocks_legacy_pos_returns(self, get_value, throw):
        throw.side_effect = RuntimeError
        get_value.side_effect = [
            frappe._dict(
                custom_ury_settlement=None,
                custom_ury_settlement_type=None,
                consolidated_invoice=None,
                pos_profile="POS Polana",
            ),
            1,
        ]
        invoice = frappe._dict(
            is_return=1,
            return_against="POS-LEGACY-1",
            pos_profile="POS Polana",
        )

        with self.assertRaises(RuntimeError):
            ury_pos_invoice.validate_commercial_settlement(invoice)

        self.assertIn("commercial checkout", throw.call_args.args[0])

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.throw")
    @patch("ury.ury.hooks.ury_pos_invoice.frappe.db.get_value")
    def test_original_profile_prevents_return_profile_bypass(self, get_value, throw):
        throw.side_effect = RuntimeError
        get_value.side_effect = [
            frappe._dict(
                custom_ury_settlement=None,
                custom_ury_settlement_type=None,
                consolidated_invoice=None,
                pos_profile="POS Polana",
            ),
            0,
            1,
        ]
        invoice = frappe._dict(
            is_return=1,
            return_against="POS-POLANA-1",
            pos_profile="POS Other",
        )

        with self.assertRaises(RuntimeError):
            ury_pos_invoice.validate_commercial_settlement(invoice)

        self.assertIn("temporarily unavailable", throw.call_args.args[0])

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.db.get_value")
    def test_legacy_return_remains_available_when_checkout_is_disabled(self, get_value):
        get_value.side_effect = [
            frappe._dict(
                custom_ury_settlement=None,
                custom_ury_settlement_type=None,
                consolidated_invoice=None,
                pos_profile="POS Legacy",
            ),
            0,
        ]
        invoice = frappe._dict(
            is_return=1,
            return_against="POS-LEGACY-1",
            pos_profile="POS Legacy",
        )

        ury_pos_invoice.validate_commercial_settlement(invoice)

    def test_invoice_items_are_aggregated_by_item_and_price_option(self):
        items = [
            frappe._dict(
                item_code="CAKE-SLICE",
                item_name="Cake Slice",
                qty=2,
                custom_ury_price_option="standard",
                custom_ury_price_option_label="Normal",
            ),
            frappe._dict(
                item_code="CAKE-SLICE",
                item_name="Cake Slice",
                qty=4,
                custom_ury_price_option="PROMO-1",
                custom_ury_price_option_label="Promotion",
            ),
            frappe._dict(
                item_code="CAKE-SLICE",
                item_name="Cake Slice",
                qty=4,
                custom_ury_price_option="standard",
                custom_ury_price_option_label="Normal",
            ),
        ]

        aggregated = ury_pos_invoice._aggregate_invoice_items(items)

        self.assertEqual(aggregated[("CAKE-SLICE", "standard")]["qty"], 6.0)
        self.assertEqual(aggregated[("CAKE-SLICE", "PROMO-1")]["qty"], 4.0)

    @patch("ury.ury.hooks.ury_pos_invoice.frappe.throw")
    @patch("ury.ury.hooks.ury_pos_invoice.frappe.get_doc")
    @patch("ury.ury.hooks.ury_pos_invoice.frappe.db.get_value", return_value=0)
    def test_printed_invoice_cannot_move_qty_between_price_options(
        self,
        _get_value,
        get_doc,
        throw,
    ):
        get_doc.return_value = frappe._dict(
            items=[
                frappe._dict(
                    item_code="CAKE-SLICE",
                    item_name="Cake Slice",
                    qty=6,
                    custom_ury_price_option="standard",
                    custom_ury_price_option_label="Normal",
                ),
                frappe._dict(
                    item_code="CAKE-SLICE",
                    item_name="Cake Slice",
                    qty=4,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                ),
            ]
        )
        invoice = frappe._dict(
            name="POS-INV-1",
            waiter="waiter@example.com",
            modified_by="waiter@example.com",
            pos_profile="POS A",
            invoice_printed=1,
            items=[
                frappe._dict(
                    item_code="CAKE-SLICE",
                    item_name="Cake Slice",
                    qty=5,
                    custom_ury_price_option="standard",
                    custom_ury_price_option_label="Normal",
                ),
                frappe._dict(
                    item_code="CAKE-SLICE",
                    item_name="Cake Slice",
                    qty=5,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                ),
            ],
        )

        ury_pos_invoice.validate_invoice(invoice, "validate")

        throw.assert_called_once()
        self.assertIn("Cake Slice - Normal", throw.call_args.args[0])

    @patch("ury.ury.hooks.ury_pos_invoice.lock_invoice_price_options")
    def test_before_cancel_locks_price_options(self, lock_price_options):
        invoice = MagicMock()
        invoice.get.return_value = None

        ury_pos_invoice.before_cancel(invoice, "before_cancel")

        lock_price_options.assert_called_once_with(invoice)

    @patch("ury.ury.hooks.ury_pos_invoice.table_status_delete")
    @patch("ury.ury.hooks.ury_pos_invoice.lock_invoice_price_options")
    def test_trash_locks_price_options_before_releasing_table(
        self,
        lock_price_options,
        release_table,
    ):
        events = []
        invoice = MagicMock()
        lock_price_options.side_effect = lambda _invoice: events.append("lock")
        release_table.side_effect = lambda _invoice, _method: events.append(
            "release"
        )

        ury_pos_invoice.on_trash(invoice, "on_trash")

        self.assertEqual(events, ["lock", "release"])

    @patch("ury.ury.hooks.ury_pos_invoice.table_status_delete")
    @patch("ury.ury.hooks.ury_pos_invoice.lock_invoice_price_options")
    def test_cancel_does_not_lock_price_options_twice(
        self,
        lock_price_options,
        release_table,
    ):
        invoice = MagicMock()

        ury_pos_invoice.on_trash(invoice, "on_cancel")

        lock_price_options.assert_not_called()
        release_table.assert_called_once_with(invoice, "on_cancel")


def run_unit_tests():
    """Run the mock-only POS Invoice hook suite through bench execute."""
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
