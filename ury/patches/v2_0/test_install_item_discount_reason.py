from unittest import TestCase
from unittest.mock import patch

from ury.patches.v2_0 import install_item_discount_reason


class TestInstallItemDiscountReason(TestCase):
	@patch.object(install_item_discount_reason.frappe, "clear_cache")
	@patch.object(install_item_discount_reason, "create_custom_fields")
	def test_installer_creates_only_discount_reason(self, mocked_create, mocked_clear_cache):
		install_item_discount_reason.execute()

		custom_fields = mocked_create.call_args.args[0]
		self.assertEqual(
			[field["fieldname"] for field in custom_fields["POS Invoice Item"]],
			["custom_ury_manual_discount_reason"],
		)
		self.assertTrue(mocked_create.call_args.kwargs["update"])
		mocked_clear_cache.assert_called_once_with(doctype="POS Invoice Item")
