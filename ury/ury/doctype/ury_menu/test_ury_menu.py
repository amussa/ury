# Copyright (c) 2023, Tridz Technologies  and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from ury.ury.doctype.ury_menu.ury_menu import URYMenu


def _raise_frappe(message, exc=frappe.ValidationError, **_kwargs):
	if isinstance(exc, type):
		raise exc(message)
	raise frappe.ValidationError(message)


class TestURYMenu(FrappeTestCase):
	@patch.object(URYMenu, "_protect_price_options_used_by_open_orders")
	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.get_value")
	def test_enabled_promotion_allows_zero_quantity(self, get_value, _protect):
		get_value.return_value = frappe._dict(
			is_stock_item=1,
			has_serial_no=0,
			has_batch_no=0,
		)
		menu = object.__new__(URYMenu)
		menu.items = [
			frappe._dict(item="CAKE-SLICE", rate=100, disabled=0)
		]
		menu.price_options = [
			frappe._dict(
				item="CAKE-SLICE",
				enabled=1,
				label="Promotion",
				rate=75,
				allocated_qty=0,
			)
		]

		menu.validate_price_options()

		self.assertEqual(menu.price_options[0].allocated_qty, 0)
		self.assertEqual(menu.price_options[0].rate, 75)
		self.assertEqual(menu.price_options[0].label, "Promotion")

	@patch.object(URYMenu, "_protect_price_options_used_by_open_orders")
	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.get_value")
	def test_enabled_promotion_rejects_negative_quantity(self, get_value, _protect):
		get_value.return_value = frappe._dict(
			is_stock_item=1,
			has_serial_no=0,
			has_batch_no=0,
		)
		menu = object.__new__(URYMenu)
		menu.items = [
			frappe._dict(item="CAKE-SLICE", rate=100, disabled=0)
		]
		menu.price_options = [
			frappe._dict(
				item="CAKE-SLICE",
				enabled=1,
				label="Promotion",
				rate=75,
				allocated_qty=-1,
			)
		]

		with patch(
			"ury.ury.doctype.ury_menu.ury_menu.frappe.throw",
			side_effect=_raise_frappe,
		):
			with self.assertRaisesRegex(
				frappe.ValidationError, "cannot be negative"
			):
				menu.validate_price_options()

	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.sql")
	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.has_column", return_value=True)
	def test_saved_price_option_item_is_immutable(self, _has_column, db_sql):
		db_sql.return_value = [
			frappe._dict(
				name="PROMO-1",
				item="CAKE-SLICE",
				enabled=1,
				rate=75,
				allocated_qty=4,
			)
		]
		menu = object.__new__(URYMenu)
		menu.name = "Menu A"
		menu.price_options = [
			frappe._dict(
				name="PROMO-1",
				item="OTHER-ITEM",
				enabled=1,
				rate=70,
				allocated_qty=4,
			)
		]
		menu.items = []
		menu.is_new = lambda: False

		with patch(
			"ury.ury.doctype.ury_menu.ury_menu.frappe.throw",
			side_effect=_raise_frappe,
		):
			with self.assertRaises(frappe.ValidationError):
				menu._protect_price_options_used_by_open_orders()

	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.sql")
	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.has_column", return_value=True)
	def test_open_order_blocks_promotion_repricing(self, _has_column, db_sql):
		db_sql.side_effect = [[("Menu A",)], [
			frappe._dict(
				name="PROMO-1",
				item="CAKE-SLICE",
				label="Promotion",
				enabled=1,
				rate=75,
				allocated_qty=4,
			)
		], [
			frappe._dict(
				name="MENU-ITEM-1",
				item="CAKE-SLICE",
				rate=100,
				disabled=0,
			)
		], [("ACC-POS-INV-1",)]]
		menu = object.__new__(URYMenu)
		menu.name = "Menu A"
		menu.price_options = [
			frappe._dict(
				name="PROMO-1",
				item="CAKE-SLICE",
				label="Promotion",
				enabled=1,
				rate=70,
				allocated_qty=4,
			)
		]
		menu.items = [
			frappe._dict(item="CAKE-SLICE", rate=100, disabled=0)
		]
		menu.is_new = lambda: False

		with patch(
			"ury.ury.doctype.ury_menu.ury_menu.frappe.throw",
			side_effect=_raise_frappe,
		):
			with self.assertRaises(frappe.ValidationError):
				menu._protect_price_options_used_by_open_orders()

		open_order_query, values = db_sql.call_args_list[3].args
		self.assertIn("price_list.restaurant_menu = %(menu)s", open_order_query)
		self.assertIn("item.custom_ury_price_option", open_order_query)
		self.assertEqual(values["menu"], "Menu A")
		self.assertEqual(values["option_ids"], ("PROMO-1",))

	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.sql")
	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.has_column", return_value=True)
	def test_submitted_history_does_not_block_future_promotion_changes(
		self, _has_column, db_sql
	):
		db_sql.side_effect = [[("Menu A",)], [
			frappe._dict(
				name="PROMO-1",
				item="CAKE-SLICE",
				label="Promotion",
				enabled=1,
				rate=75,
				allocated_qty=4,
			)
		], [
			frappe._dict(
				name="MENU-ITEM-1",
				item="CAKE-SLICE",
				rate=100,
				disabled=0,
			)
		], []]
		menu = object.__new__(URYMenu)
		menu.name = "Menu A"
		menu.price_options = []
		menu.items = [
			frappe._dict(item="CAKE-SLICE", rate=100, disabled=0)
		]
		menu.is_new = lambda: False

		menu._protect_price_options_used_by_open_orders()

		parent_query = db_sql.call_args_list[0].args[0]
		first_child_query = db_sql.call_args_list[1].args[0]
		self.assertIn("FROM `tabURY Menu`", parent_query)
		self.assertIn("FOR UPDATE", parent_query)
		self.assertIn("ORDER BY name", first_child_query)
		self.assertIn("FOR UPDATE", first_child_query)

	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.sql")
	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.has_column", return_value=True)
	def test_new_promotion_is_blocked_by_an_open_normal_order(
		self, _has_column, db_sql
	):
		db_sql.side_effect = [[("Menu A",)], [], [], [("ACC-POS-INV-2",)]]
		menu = object.__new__(URYMenu)
		menu.name = "Menu A"
		menu.price_options = [
			frappe._dict(
				name="new-ury-menu-price-option-1",
				item="CAKE-SLICE",
				enabled=1,
				rate=75,
				allocated_qty=4,
			)
		]
		menu.items = [
			frappe._dict(item="CAKE-SLICE", rate=100, disabled=0)
		]
		menu.is_new = lambda: False

		with patch(
			"ury.ury.doctype.ury_menu.ury_menu.frappe.throw",
			side_effect=_raise_frappe,
		):
			with self.assertRaises(frappe.ValidationError):
				menu._protect_price_options_used_by_open_orders()

		open_order_query = db_sql.call_args_list[3].args[0]
		self.assertIn("item.item_code IN", open_order_query)
		self.assertIn("price_list.restaurant_menu = %(menu)s", open_order_query)
		self.assertIn("FOR UPDATE", open_order_query)

	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.sql")
	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.has_column", return_value=True)
	def test_open_order_blocks_promotion_label_change(self, _has_column, db_sql):
		db_sql.side_effect = [
			[("Menu A",)],
			[
				frappe._dict(
					name="PROMO-1",
					item="CAKE-SLICE",
					label="Promotion",
					enabled=1,
					rate=75,
					allocated_qty=4,
				)
			],
			[
				frappe._dict(
					name="MENU-ITEM-1",
					item="CAKE-SLICE",
					rate=100,
					disabled=0,
				)
			],
			[("ACC-POS-INV-4",)],
		]
		menu = object.__new__(URYMenu)
		menu.name = "Menu A"
		menu.price_options = [
			frappe._dict(
				name="PROMO-1",
				item="CAKE-SLICE",
				label="Happy Hour",
				enabled=1,
				rate=75,
				allocated_qty=4,
			)
		]
		menu.items = [
			frappe._dict(item="CAKE-SLICE", rate=100, disabled=0)
		]
		menu.is_new = lambda: False

		with patch(
			"ury.ury.doctype.ury_menu.ury_menu.frappe.throw",
			side_effect=_raise_frappe,
		):
			with self.assertRaises(frappe.ValidationError):
				menu._protect_price_options_used_by_open_orders()

	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.sql")
	@patch("ury.ury.doctype.ury_menu.ury_menu.frappe.db.has_column", return_value=True)
	def test_menu_delete_blocks_open_normal_or_historical_promotion_order(
		self, _has_column, db_sql
	):
		db_sql.side_effect = [
			[("Menu A",)],
			[
				frappe._dict(
					name="PROMO-OLD",
					item="CAKE-SLICE",
				)
			],
			[("ACC-POS-INV-3",)],
		]
		menu = object.__new__(URYMenu)
		menu.name = "Menu A"
		menu.is_new = lambda: False

		with patch(
			"ury.ury.doctype.ury_menu.ury_menu.frappe.throw",
			side_effect=_raise_frappe,
		):
			with self.assertRaises(frappe.ValidationError):
				menu._protect_price_options_on_trash()

		parent_query = db_sql.call_args_list[0].args[0]
		child_query = db_sql.call_args_list[1].args[0]
		open_order_query, values = db_sql.call_args_list[2].args
		self.assertIn("FROM `tabURY Menu`", parent_query)
		self.assertIn("FROM `tabURY Menu Price Option`", child_query)
		self.assertIn("price_list.restaurant_menu", open_order_query)
		self.assertIn("item.custom_ury_price_option", open_order_query)
		self.assertIn("FOR UPDATE", open_order_query)
		self.assertEqual(values["option_ids"], ("PROMO-OLD",))
		self.assertEqual(values["item_codes"], ("CAKE-SLICE",))
