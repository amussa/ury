import io
from unittest import TestCase, TextTestRunner, defaultTestLoader
from unittest.mock import patch

import frappe

from ury.ury_pos.cashier import (
    assign_single_cashier_from_opening,
    get_single_cashier_opening,
    persist_cashier_owner,
)
from ury.ury.hooks.ury_pos_opening_entry import (
    validate_single_cashier_opening,
)


def single_cashier_profile():
    return frappe._dict(
        name="POS Polana",
        branch="Polana",
        custom_enable_multiple_cashier=0,
    )


def raise_validation(message, **_kwargs):
    raise frappe.ValidationError(message)


class TestSingleCashierOpening(TestCase):
    @patch("ury.ury_pos.cashier.frappe.db.get_values")
    @patch("ury.ury_pos.cashier.frappe.db.get_value")
    def test_opening_user_owns_the_till(self, get_value, get_values):
        get_value.return_value = single_cashier_profile()
        get_values.return_value = [
            frappe._dict(
                name="POS-OPE-2026-00008",
                user="edsia@app.co.mz",
                branch="Polana",
                pos_profile="POS Polana",
            )
        ]

        invoice = frappe._dict(
            pos_profile="POS Polana",
            cashier="jonas@app.co.mz",
            owner="jonas@app.co.mz",
            is_new=lambda: True,
        )
        opening = assign_single_cashier_from_opening(invoice)

        self.assertEqual(opening.name, "POS-OPE-2026-00008")
        self.assertEqual(invoice.cashier, "edsia@app.co.mz")
        self.assertEqual(invoice.owner, "edsia@app.co.mz")
        self.assertEqual(
            get_values.call_args.kwargs["filters"],
            {
                "pos_profile": "POS Polana",
                "branch": "Polana",
                "status": "Open",
                "docstatus": 1,
            },
        )
        self.assertTrue(get_values.call_args.kwargs["for_update"])
        self.assertNotIn("for_update", get_value.call_args.kwargs)

    @patch("ury.ury_pos.cashier.frappe.db.get_values")
    @patch("ury.ury_pos.cashier.frappe.db.get_value")
    def test_existing_draft_owner_changes_only_after_save(
        self,
        get_value,
        get_values,
    ):
        get_value.return_value = single_cashier_profile()
        get_values.return_value = [
            frappe._dict(
                name="POS-OPE-2026-00008",
                user="edsia@app.co.mz",
                branch="Polana",
                pos_profile="POS Polana",
            )
        ]
        writes = []
        invoice = frappe._dict(
            pos_profile="POS Polana",
            cashier="jonas@app.co.mz",
            owner="jonas@app.co.mz",
            is_new=lambda: False,
        )

        opening = assign_single_cashier_from_opening(invoice)
        self.assertEqual(opening.user, "edsia@app.co.mz")
        self.assertEqual(invoice.cashier, "edsia@app.co.mz")
        self.assertEqual(invoice.owner, "jonas@app.co.mz")

        def db_set(fieldname, value, update_modified=True):
            writes.append((fieldname, value, update_modified))
            invoice[fieldname] = value

        invoice.db_set = db_set
        persist_cashier_owner(invoice)

        self.assertEqual(
            writes,
            [("owner", "edsia@app.co.mz", False)],
        )
        self.assertEqual(invoice.owner, "edsia@app.co.mz")

    @patch("ury.ury_pos.cashier.frappe.db.get_values", return_value=[])
    @patch("ury.ury_pos.cashier.frappe.db.get_value")
    @patch(
        "ury.ury_pos.cashier.frappe.throw",
        side_effect=raise_validation,
    )
    def test_missing_opening_fails_closed(
        self,
        _throw,
        get_value,
        _get_values,
    ):
        get_value.return_value = single_cashier_profile()

        with self.assertRaises(frappe.ValidationError):
            get_single_cashier_opening("POS Polana")

        self.assertIsNone(
            get_single_cashier_opening("POS Polana", required=False)
        )

    @patch("ury.ury_pos.cashier.frappe.db.get_values")
    @patch("ury.ury_pos.cashier.frappe.db.get_value")
    @patch(
        "ury.ury_pos.cashier.frappe.throw",
        side_effect=raise_validation,
    )
    def test_multiple_openings_fail_as_ambiguous(
        self,
        _throw,
        get_value,
        get_values,
    ):
        get_value.return_value = single_cashier_profile()
        get_values.return_value = [
            frappe._dict(name="POS-OPE-2026-00007", user="edsia@app.co.mz"),
            frappe._dict(name="POS-OPE-2026-00008", user="jonas@app.co.mz"),
        ]

        with self.assertRaises(frappe.ValidationError):
            get_single_cashier_opening("POS Polana")

    @patch("ury.ury_pos.cashier.frappe.db.get_values")
    @patch("ury.ury_pos.cashier.frappe.db.get_value")
    @patch(
        "ury.ury_pos.cashier.frappe.throw",
        side_effect=raise_validation,
    )
    def test_invoice_branch_must_match_opening(
        self,
        _throw,
        get_value,
        get_values,
    ):
        get_value.return_value = single_cashier_profile()
        get_values.return_value = [
            frappe._dict(
                name="POS-OPE-2026-00008",
                user="edsia@app.co.mz",
                branch="Polana",
                pos_profile="POS Polana",
            )
        ]
        invoice = frappe._dict(
            pos_profile="POS Polana",
            branch="Fomento",
            cashier="jonas@app.co.mz",
            owner="jonas@app.co.mz",
        )

        with self.assertRaises(frappe.ValidationError):
            assign_single_cashier_from_opening(invoice)

    @patch("ury.ury_pos.cashier.frappe.db.get_values")
    @patch("ury.ury_pos.cashier.frappe.db.get_value")
    def test_multiple_cashier_mode_keeps_existing_flow(
        self,
        get_value,
        get_values,
    ):
        profile = single_cashier_profile()
        profile.custom_enable_multiple_cashier = 1
        get_value.return_value = profile

        invoice = frappe._dict(
            pos_profile="POS Polana",
            cashier="room-cashier@app.co.mz",
            owner="main-cashier@app.co.mz",
            is_new=lambda: True,
        )
        opening = assign_single_cashier_from_opening(invoice)

        self.assertIsNone(opening)
        self.assertEqual(invoice.cashier, "room-cashier@app.co.mz")
        self.assertEqual(invoice.owner, "room-cashier@app.co.mz")
        get_values.assert_not_called()

    @patch("ury.ury.hooks.ury_pos_opening_entry.frappe.db.get_values")
    @patch("ury.ury.hooks.ury_pos_opening_entry.frappe.db.get_value")
    @patch(
        "ury.ury.hooks.ury_pos_opening_entry.frappe.throw",
        side_effect=raise_validation,
    )
    def test_second_opening_is_rejected(
        self,
        _throw,
        get_value,
        get_values,
    ):
        get_value.return_value = single_cashier_profile()
        get_values.return_value = [
            frappe._dict(
                name="POS-OPE-2026-00008",
                user="edsia@app.co.mz",
            )
        ]
        opening = frappe._dict(
            name="POS-OPE-2026-00009",
            pos_profile="POS Polana",
            branch="Polana",
        )

        with self.assertRaises(frappe.ValidationError):
            validate_single_cashier_opening(opening, "validate")

        self.assertTrue(get_value.call_args.kwargs["for_update"])
        self.assertTrue(get_values.call_args.kwargs["for_update"])


def run_unit_tests():
    """Run this mock-only suite through ``bench execute`` without test data."""
    stream = io.StringIO()
    suite = defaultTestLoader.loadTestsFromTestCase(TestSingleCashierOpening)
    result = TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError(stream.getvalue())
    return {
        "tests_run": result.testsRun,
        "successful": True,
    }


def verify_existing_draft_set_once(invoice_name, opening_user):
    """Read-only regression check against a real draft and its Frappe meta."""
    invoice = frappe.get_doc("POS Invoice", invoice_name)
    if invoice.docstatus != 0:
        raise AssertionError(f"{invoice_name} is not a draft")

    owner_before = invoice.owner
    invoice._doc_before_save = frappe.get_doc("POS Invoice", invoice_name)
    opening = frappe._dict(
        name="TEST-OPENING",
        user=opening_user,
        branch=invoice.branch,
        pos_profile=invoice.pos_profile,
    )

    with patch(
        "ury.ury_pos.cashier.get_single_cashier_opening",
        return_value=opening,
    ):
        assign_single_cashier_from_opening(invoice)
        invoice.validate_set_only_once()

        with patch.object(invoice, "db_set") as db_set:
            persist_cashier_owner(invoice)

    db_set.assert_called_once_with(
        "owner",
        opening_user,
        update_modified=False,
    )
    return {
        "invoice": invoice_name,
        "owner_before": owner_before,
        "owner_during_validation": invoice.owner,
        "cashier_during_validation": invoice.cashier,
        "owner_after_save_target": opening_user,
        "database_writes": 0,
    }
