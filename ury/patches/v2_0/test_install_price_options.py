from unittest import TestCase
from unittest.mock import patch

from ury import install
from ury.patches.v2_0 import install_price_options


class TestInstallPriceOptions(TestCase):
	@patch("ury.install.setup")
	def test_after_install_runs_full_setup_even_when_called_again(self, mocked_setup):
		install.after_install()
		install.after_install()

		self.assertEqual(mocked_setup.call_count, 2)

	@patch.object(install_price_options.frappe, "clear_cache")
	@patch.object(install_price_options, "create_custom_fields")
	def test_installer_creates_all_invoice_item_fields(self, mocked_create, mocked_clear_cache):
		install_price_options.execute()

		custom_fields = mocked_create.call_args.args[0]
		self.assertEqual(
			{field["fieldname"] for field in custom_fields["POS Invoice Item"]},
			{"custom_ury_price_option", "custom_ury_price_option_label"},
		)
		self.assertEqual(
			{field["fieldname"] for field in custom_fields["Sales Invoice Item"]},
			{"custom_ury_price_option", "custom_ury_price_option_label"},
		)
		self.assertTrue(mocked_create.call_args.kwargs["update"])
		self.assertEqual(mocked_clear_cache.call_count, 2)
