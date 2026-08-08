import sys
from unittest import TestCase
from unittest.mock import MagicMock, mock_open, patch

from ury.ury.api import ury_print
from ury.ury.doctype.ury_order.ury_order import release_tables_after_print


class TestURYPrint(TestCase):
    @patch("ury.ury.doctype.ury_order.ury_order.frappe.db.commit")
    @patch("ury.ury.doctype.ury_order.ury_order.frappe.db.set_value")
    def test_legacy_print_endpoint_does_not_release_tables(self, set_value, commit):
        self.assertTrue(release_tables_after_print("POS-INV-1"))

        set_value.assert_called_once_with(
            "POS Invoice",
            "POS-INV-1",
            "invoice_printed",
            1,
            update_modified=False,
        )
        commit.assert_called_once_with()

    @patch("ury.ury.api.ury_print.frappe.db.get_value", return_value=1)
    @patch("ury.ury.api.ury_print.frappe.db.set_value")
    def test_mark_invoice_printed_does_not_update_table(self, set_value, get_value):
        self.assertTrue(ury_print._mark_invoice_printed("POS-INV-1"))

        set_value.assert_called_once_with(
            "POS Invoice",
            "POS-INV-1",
            "invoice_printed",
            1,
            update_modified=False,
        )
        get_value.assert_called_once_with(
            "POS Invoice", "POS-INV-1", "invoice_printed"
        )

    @patch("ury.ury.api.ury_print._mark_invoice_printed", return_value=True)
    def test_qz_print_only_marks_invoice(self, mark_invoice_printed):
        self.assertEqual(
            ury_print.qz_print_update("POS-INV-1"),
            {"status": "Success"},
        )
        mark_invoice_printed.assert_called_once_with("POS-INV-1")

    @patch("ury.ury.api.ury_print._mark_invoice_printed")
    @patch("ury.ury.api.ury_print.frappe.publish_realtime")
    @patch("ury.ury.api.ury_print.frappe.db.get_value", return_value="Polana")
    def test_websocket_print_only_marks_invoice(
        self,
        get_value,
        publish_realtime,
        mark_invoice_printed,
    ):
        ury_print.print_pos_page("POS Invoice", "POS-INV-1", "Receipt")

        get_value.assert_called_once_with("POS Invoice", "POS-INV-1", "branch")
        publish_realtime.assert_called_once_with(
            "print_Polana",
            {
                "data": {
                    "name": "POS-INV-1",
                    "doctype": "POS Invoice",
                    "print_format": "Receipt",
                }
            },
        )
        mark_invoice_printed.assert_called_once_with("POS-INV-1")

    @patch("ury.ury.api.ury_print._mark_invoice_printed")
    @patch("ury.ury.api.ury_print.frappe.generate_hash", return_value="hash")
    @patch("ury.ury.api.ury_print.frappe.get_print")
    @patch("ury.ury.api.ury_print.frappe.get_doc")
    @patch("builtins.open", new_callable=mock_open)
    def test_network_print_only_marks_invoice(
        self,
        _open,
        get_doc,
        get_print,
        _generate_hash,
        mark_invoice_printed,
    ):
        print_settings = MagicMock(
            server_ip="127.0.0.1",
            port=631,
            printer_name="receipt-printer",
        )
        get_doc.return_value = print_settings
        get_print.return_value = MagicMock()

        connection = MagicMock()
        cups = MagicMock()
        cups.Connection.return_value = connection

        with patch.dict(sys.modules, {"cups": cups}):
            result = ury_print.network_printing(
                "POS Invoice",
                "POS-INV-1",
                "Printer Settings",
                "Receipt",
            )

        self.assertEqual(result, "Success")
        connection.printFile.assert_called_once()
        mark_invoice_printed.assert_called_once_with("POS-INV-1")
