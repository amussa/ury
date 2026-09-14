from unittest import TestCase
from unittest.mock import patch
from types import SimpleNamespace

import frappe
from ury.ury_pos.consolidation_rounding import apply_rounding, source_rounding_plan


class Record(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def precision(self, field):
        return 2


def record(**values):
    return Record(**values)


class TestConsolidationRounding(TestCase):
    def doc(self, grand=21209.94, lines=79):
        return record(grand_total=grand, base_grand_total=grand,
            conversion_rate=1, items=[record() for _ in range(lines)],
            taxes=[record(rate=16)], disable_rounded_total=1)

    def test_six_cents_use_native_rounding_fields(self):
        doc = self.doc()
        apply_rounding(doc, 21210)
        self.assertEqual((doc.rounded_total, doc.rounding_adjustment,
            doc.base_rounded_total, doc.base_rounding_adjustment), (21210, .06, 21210, .06))
        self.assertEqual(doc.grand_total, 21209.94)
        self.assertEqual(doc.disable_rounded_total, 0)

    def test_negative_residual_and_return(self):
        for grand, target, expected in [(21210.06,21210,-.06),(-21209.94,-21210,-.06)]:
            doc=self.doc(grand)
            apply_rounding(doc,target)
            self.assertEqual(doc.rounding_adjustment,expected)

    def test_reject_real_price_difference(self):
        with self.assertRaises(frappe.ValidationError):
            apply_rounding(self.doc(), 21220)

    def test_house_offer_zero_and_decimal_commercial_price(self):
        doc=self.doc(0,1)
        apply_rounding(doc,0)
        self.assertEqual(doc.rounded_total,0)
        doc=self.doc(9.99,1)
        apply_rounding(doc,9.99)
        self.assertEqual(doc.rounded_total,9.99)

    def test_existing_source_rounding_and_exchange_rate(self):
        doc=self.doc(9.60,1)
        doc.conversion_rate=2
        doc.base_grand_total=19.20
        apply_rounding(doc,10,.40)
        self.assertEqual((doc.rounded_total,doc.base_rounded_total,doc.base_rounding_adjustment),(10,20,.80))

    def source(self):
        return record(name='POS-1',docstatus=1,company='G',currency='MZN',conversion_rate=1,
            is_return=0,rounded_total=90,grand_total=90,rounding_adjustment=0,paid_amount=20,
            items=[record(name='ROW-1',item_code='ITEM',qty=1,net_amount=90)])

    def invoice(self):
        return record(company='G',currency='MZN',conversion_rate=1,is_return=0,
            items=[record(pos_invoice='POS-1',pos_invoice_item='ROW-1',item_code='ITEM',qty=1,amount=90)])

    def test_credit_uses_sale_total_not_partial_payment(self):
        with patch('frappe.get_doc',return_value=self.source()):
            self.assertEqual(source_rounding_plan(self.invoice()),(90,0))

    def test_reject_duplicate_missing_and_changed_source_rows(self):
        for variant in ['duplicate','missing','amount','qty','currency','cancelled']:
            with self.subTest(variant=variant):
                doc,source=self.invoice(),self.source()
                if variant=='duplicate':doc.items.append(doc.items[0])
                if variant=='missing':source.items.append(record(name='ROW-2'))
                if variant=='amount':doc.items[0].amount=91
                if variant=='qty':doc.items[0].qty=2
                if variant=='currency':doc.currency='USD'
                if variant=='cancelled':source.docstatus=2
                with patch('frappe.get_doc',return_value=source):
                    with self.assertRaises(frappe.ValidationError):source_rounding_plan(doc)
