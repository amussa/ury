from unittest import TestCase
from unittest.mock import patch

import frappe

from ury.ury_pos.promotion_balance import (
    allocation_for_remaining, apply_balance_requests, load_balances,
    remaining_quantity, snapshots,
)


class Row(frappe._dict):
    def is_new(self):
        return bool(self.get('__islocal'))


class Menu(frappe._dict):
    def is_new(self):
        return False

    def set_onload(self, key, value):
        self.setdefault('__onload', {})[key] = value


class TestPromotionBalance(TestCase):
    def test_old_history_is_hidden_from_operator(self):
        self.assertEqual(allocation_for_remaining(5, 12, 5), 17)
        self.assertEqual(remaining_quantity(17, 12, 5), 5)

    def test_order_submit_cancel_and_normal_sale(self):
        # A draft and then its submitted version represent the SAME consumed
        # unit; cancellation removes the draft reservation from the sum.
        self.assertEqual(remaining_quantity(17, 13, 4), 4)  # draft
        self.assertEqual(remaining_quantity(17, 13, 4), 4)  # submitted
        self.assertEqual(remaining_quantity(17, 12, 5), 5)  # cancelled draft
        self.assertEqual(remaining_quantity(17, 12, 8), 5)  # normal sale

    def test_stock_and_zero_bounds(self):
        self.assertEqual(remaining_quantity(17, 12, 2), 2)
        self.assertEqual(remaining_quantity(5, 12, 5), 0)
        self.assertEqual(allocation_for_remaining(0, 12, 5), 12)

    @patch('ury.ury_pos.promotion_balance.frappe.throw', side_effect=ValueError)
    def test_invalid_or_excess_quantity_rejected(self, _throw):
        for quantity in [-1, 6, 'invalid', float('nan'), float('inf'), None]:
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                allocation_for_remaining(quantity, 12, 5)

    def make_menu(self, quantity=5, new=False):
        self.row = Row(name='PROMO', item='BOLO-001', allocated_qty=17,
                       __islocal=new, _remaining_request={
                           'quantity': quantity,
                           'expected': {'allocated_qty': 17, 'used_qty': 12, 'available_qty': 5},
                       })
        return Menu(name='Menu Polana', price_options=[self.row])

    @patch('ury.ury_pos.promotion_balance.snapshots')
    def test_unrelated_save_does_not_replenish(self, states):
        menu = self.make_menu()
        self.row._remaining_request = None
        apply_balance_requests(menu)
        states.assert_not_called()
        self.assertEqual(self.row.allocated_qty, 17)

    @patch('ury.ury_pos.promotion_balance.frappe.db.get_value', return_value=17)
    @patch('ury.ury_pos.promotion_balance.snapshots')
    def test_explicit_edit_translates_balance_and_clears_request(self, states, _get):
        menu = self.make_menu(quantity=3)
        states.return_value = {'PROMO': {'used_qty': 12, 'available_qty': 5}}
        apply_balance_requests(menu)
        states.assert_called_once_with(menu, lock=True)
        self.assertEqual(self.row.allocated_qty, 15)
        self.assertEqual(self.row.remaining_qty, 3)
        self.assertIsNone(self.row._remaining_request)

    @patch('ury.ury_pos.promotion_balance.frappe.throw', side_effect=ValueError)
    @patch('ury.ury_pos.promotion_balance.frappe.db.get_value', return_value=17)
    @patch('ury.ury_pos.promotion_balance.snapshots')
    def test_concurrent_order_is_not_silently_replenished(self, states, _get, _throw):
        menu = self.make_menu()
        states.return_value = {'PROMO': {'used_qty': 13, 'available_qty': 4}}
        with self.assertRaises(ValueError):
            apply_balance_requests(menu)
        self.assertEqual(self.row.allocated_qty, 17)

    @patch('ury.ury_pos.promotion_balance.frappe.throw', side_effect=ValueError)
    @patch('ury.ury_pos.promotion_balance.frappe.db.get_value', return_value=20)
    @patch('ury.ury_pos.promotion_balance.snapshots')
    def test_concurrent_admin_edit_rejected(self, states, _get, _throw):
        menu = self.make_menu()
        states.return_value = {'PROMO': {'used_qty': 12, 'available_qty': 5}}
        with self.assertRaises(ValueError):
            apply_balance_requests(menu)

    @patch('ury.ury_pos.promotion_balance.snapshots')
    def test_new_promotion_uses_requested_available_quantity(self, states):
        menu = self.make_menu(quantity=5, new=True)
        states.return_value = {'PROMO': {'used_qty': 0, 'available_qty': 5}}
        apply_balance_requests(menu)
        self.assertEqual(self.row.allocated_qty, 5)

    @patch('ury.ury_pos.promotion_balance.snapshots')
    def test_admin_load_shows_remaining_not_allocation(self, states):
        menu = self.make_menu()
        states.return_value = {'PROMO': {'remaining_qty': 4, 'used_qty': 13}}
        load_balances(menu)
        self.assertEqual(self.row.remaining_qty, 4)
        self.assertEqual(menu.get('__onload')['promotion_balances']['PROMO']['used_qty'], 13)

    @patch('ury.ury_pos.promotion_balance._get_used_promotion_qty', return_value={'PROMO': 13})
    @patch('ury.ury_pos.promotion_balance.menu_stock', return_value={'BOLO-001': {'available_qty': 4}})
    @patch('ury.ury_pos.promotion_balance.lock_menu_price_options_parent')
    def test_snapshot_locks_parent_then_stock_and_consumption(self, parent, stock, used):
        menu = self.make_menu()
        result = snapshots(menu, lock=True)
        parent.assert_called_once_with('Menu Polana')
        stock.assert_called_once_with(menu, ['BOLO-001'], lock=True)
        used.assert_called_once_with(['PROMO'], for_update=True)
        self.assertEqual(result['PROMO']['remaining_qty'], 4)
