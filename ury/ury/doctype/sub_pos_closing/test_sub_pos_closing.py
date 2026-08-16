# Copyright (c) 2025, Tridz Technologies Pvt. Ltd and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from ury.ury.doctype.sub_pos_closing.sub_pos_closing import SubPOSClosing


class TestSubPOSClosing(FrappeTestCase):
	@patch("ury.ury.doctype.sub_pos_closing.sub_pos_closing.frappe.get_doc")
	def test_submit_keeps_opening_open_for_main_closing(self, get_doc):
		opening = SimpleNamespace(
			status="Open",
			custom_sub_pos_close_entry=None,
			save=lambda: None,
		)
		get_doc.return_value = opening
		doc = SimpleNamespace(name="SUB-CLO-1", pos_opening_entry="POS-OPE-1")

		SubPOSClosing.on_submit(doc)

		self.assertEqual(opening.status, "Open")
		self.assertEqual(opening.custom_sub_pos_close_entry, "SUB-CLO-1")
