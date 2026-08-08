from unittest import TestCase
from unittest.mock import MagicMock, patch

from ury.ury.hooks import ury_pos_invoice


class TestURYPosInvoiceHooks(TestCase):
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
