from unittest import TestCase
from unittest.mock import patch

import frappe

from ury.ury_pos.api import (
    _enrich_commercial_meta,
    getPosInvoiceItems,
    searchPosInvoice,
)


class TestInvoiceAPI(TestCase):
    @patch("ury.ury_pos.api.frappe.get_all")
    def test_consolidated_credit_uses_live_sales_invoice_balance(self, get_all):
        get_all.side_effect = [
            [
                frappe._dict(
                    name="POS-1",
                    consolidated_invoice="SINV-1",
                    paid_amount=100,
                    outstanding_amount=900,
                    due_date="2026-09-01",
                    custom_ury_settlement="SET-1",
                    custom_ury_settlement_type="Partial Credit",
                    custom_ury_credit_amount=900,
                    custom_ury_credit_due_date="2026-09-01",
                    custom_ury_manual_discount_total=0,
                )
            ],
            [
                frappe._dict(
                    name="SINV-1",
                    custom_ury_credit_settlement="SET-1",
                    paid_amount=100,
                    outstanding_amount=400,
                    due_date="2026-09-01",
                )
            ],
        ]
        rows = [frappe._dict(name="POS-1", status="Consolidated")]

        result = _enrich_commercial_meta(rows)

        self.assertEqual(result[0].outstanding_amount, 400)
        self.assertEqual(result[0].custom_ury_credit_sales_invoice, "SINV-1")

    @patch("ury.ury_pos.api.frappe.get_doc")
    def test_reload_items_include_price_option_identity(self, get_doc):
        get_doc.return_value = frappe._dict(
            items=[
                frappe._dict(
                    name="ROW-1",
                    item_code="CAKE-SLICE",
                    item_name="Cake Slice",
                    qty=4,
                    rate=80,
                    amount=320,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                    custom_ury_rate_before_manual_discount=80,
                    custom_ury_manual_discount_type="Amount",
                    custom_ury_manual_discount_input=20,
                    custom_ury_manual_discount_amount=20,
                    custom_ury_manual_discount_reason="Manager approval",
                )
            ],
            taxes=[],
        )

        item_details, tax_details = getPosInvoiceItems("POS-INV-1")

        self.assertEqual(tax_details, [])
        self.assertEqual(item_details[0]["item_code"], "CAKE-SLICE")
        self.assertEqual(
            item_details[0]["custom_ury_price_option"], "PROMO-1"
        )
        self.assertEqual(
            item_details[0]["custom_ury_price_option_label"], "Promotion"
        )
        self.assertEqual(
            item_details[0]["custom_ury_manual_discount_type"], "Amount"
        )
        self.assertEqual(
            item_details[0]["custom_ury_manual_discount_amount"], 20
        )
        self.assertEqual(
            item_details[0]["custom_ury_manual_discount_reason"],
            "Manager approval",
        )

    @patch("ury.ury_pos.api._enrich_split_group_meta", side_effect=lambda rows: rows)
    @patch("ury.ury_pos.api._get_active_credit_invoices", return_value=[])
    @patch("ury.ury_pos.api.getBranch", return_value="Polana")
    def test_credit_search_is_scoped_to_branch(
        self, _get_branch, get_credit, _enrich
    ):
        searchPosInvoice("salesio", "Credit")

        get_credit.assert_called_once_with("Polana", 10, query="salesio")

    @patch("ury.ury_pos.api._enrich_commercial_meta", side_effect=lambda rows: rows)
    @patch("ury.ury_pos.api._enrich_split_group_meta", side_effect=lambda rows: rows)
    @patch("ury.ury_pos.api.frappe.get_all", return_value=[])
    @patch("ury.ury_pos.api.getBranch", return_value="Polana")
    def test_regular_search_is_scoped_to_branch(
        self, _get_branch, get_all, _enrich, _commercial
    ):
        searchPosInvoice("pol", "Paid")

        self.assertEqual(
            get_all.call_args.kwargs["filters"],
            {"branch": "Polana", "status": "Paid"},
        )
