from unittest import TestCase
from unittest.mock import patch

import frappe

from ury.ury.permissions import ury_pos_settlement


class TestURYPosSettlementPermissions(TestCase):
	@patch("ury.ury.permissions.ury_pos_settlement.frappe.get_roles", return_value=["URY Cashier"])
	@patch("ury.ury.permissions.ury_pos_settlement.frappe.db.escape", return_value="'cashier@example.com'")
	def test_query_is_scoped_by_branch_assignment(self, _escape, _get_roles):
		condition = ury_pos_settlement.get_permission_query_conditions("cashier@example.com")
		self.assertIn("`tabURY POS Settlement`.branch", condition)
		self.assertIn("ury_user.parenttype = 'Branch'", condition)
		self.assertIn("'cashier@example.com'", condition)

	@patch("ury.ury.permissions.ury_pos_settlement.frappe.get_roles", return_value=["System Manager"])
	def test_system_manager_has_global_query_access(self, _get_roles):
		self.assertEqual(ury_pos_settlement.get_permission_query_conditions("manager@example.com"), "")

	@patch("ury.ury.permissions.ury_pos_settlement.frappe.get_roles", return_value=["URY Manager"])
	def test_ury_manager_can_query_and_replay_every_settlement(self, _get_roles):
		doc = frappe._dict(branch="Polana")

		self.assertEqual(
			ury_pos_settlement.get_permission_query_conditions("ury-manager@example.com"),
			"",
		)
		self.assertTrue(
			ury_pos_settlement.has_permission(doc, "read", "ury-manager@example.com")
		)

	@patch("ury.ury.permissions.ury_pos_settlement.frappe.get_roles", return_value=["URY Cashier"])
	@patch("ury.ury.permissions.ury_pos_settlement.frappe.db.exists", return_value=True)
	def test_cashier_can_read_assigned_branch_but_cannot_write(self, exists, _get_roles):
		doc = frappe._dict(branch="Polana")
		self.assertTrue(ury_pos_settlement.has_permission(doc, "read", "cashier@example.com"))
		self.assertFalse(ury_pos_settlement.has_permission(doc, "write", "cashier@example.com"))
		exists.assert_called_once()
