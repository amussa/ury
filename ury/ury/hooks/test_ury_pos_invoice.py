import io
from unittest import TestCase, TextTestRunner, defaultTestLoader
from unittest.mock import MagicMock, patch

import frappe

from ury.ury.hooks import ury_pos_invoice


class TestURYPosInvoiceHooks(TestCase):
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
    @patch("ury.ury.hooks.ury_pos_invoice.assign_single_cashier_from_opening")
    def test_unprinted_table_invoice_can_be_submitted(
        self,
        assign_cashier,
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
        calculate_times.assert_called_once_with(invoice, "before_submit")
        reload_submit.assert_called_once_with(invoice, "before_submit")

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
