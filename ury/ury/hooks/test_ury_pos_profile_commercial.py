from unittest import TestCase

import frappe

from ury.ury.hooks.ury_pos_profile import validate_commercial_checkout


class TestURYPosProfileCommercialCheckout(TestCase):
    def test_credit_requires_master_flag(self):
        doc = _profile(credit=1, checkout=0, partial=1)
        with self.assertRaises(frappe.ValidationError):
            validate_commercial_checkout(doc)

    def test_credit_requires_partial_payment(self):
        doc = _profile(credit=1, checkout=1, partial=0)
        with self.assertRaises(frappe.ValidationError):
            validate_commercial_checkout(doc)

    def test_flags_off_do_not_change_partial_payment(self):
        doc = _profile(credit=0, checkout=0, partial=0)
        validate_commercial_checkout(doc)
        self.assertEqual(doc.allow_partial_payment, 0)

    def test_valid_credit_configuration(self):
        validate_commercial_checkout(_profile(credit=1, checkout=1, partial=1))


def _profile(*, credit, checkout, partial):
    return frappe._dict(
        custom_ury_enable_credit_sales=credit,
        custom_ury_enable_commercial_checkout=checkout,
        allow_partial_payment=partial,
        custom_ury_default_credit_days=30,
        custom_ury_max_discount_percentage=100,
    )
