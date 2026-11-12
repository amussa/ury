from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe

from ury.ury.printing.closing_guard import before_print


class TestClosingPrintGuard(TestCase):
	def check(self, state, *, allowed=False, doctype="POS Closing Entry", docstatus=1, name="CLOSE-1"):
		doc = SimpleNamespace(doctype=doctype, name=name, docstatus=docstatus, status="Submitted")
		with patch("ury.ury.printing.closing_guard.frappe.db.get_value", return_value=state):
			if allowed:
				before_print(doc, "before_print", {"allow_print_for_draft": 1})
			else:
				with self.assertRaises(frappe.ValidationError):
					before_print(doc, "before_print", {"allow_print_for_draft": 1})

	def test_client_cannot_present_persisted_draft_as_submitted(self):
		self.check(SimpleNamespace(docstatus=0, status="Draft", error_message=None))

	def test_processing_and_failed_closings_are_not_printable(self):
		for status in ["Draft", "Queued", "Failed", "Cancelled", None]:
			with self.subTest(status=status):
				self.check(SimpleNamespace(docstatus=1, status=status, error_message=None))

	def test_cancelled_closing_cannot_be_spoofed_as_submitted(self):
		self.check(SimpleNamespace(docstatus=2, status="Submitted", error_message=None))

	def test_unpersisted_and_missing_closings_cannot_print(self):
		self.check(None)
		self.check(None, name=None, docstatus=0)

	def test_error_still_blocks_submitted_status(self):
		self.check(SimpleNamespace(docstatus=1, status="Submitted", error_message="Merge failed"))

	def test_completed_closing_and_reprint_are_allowed(self):
		self.check(SimpleNamespace(docstatus=1, status="Submitted", error_message=""), allowed=True)
		self.check(SimpleNamespace(docstatus=1, status="Submitted", error_message=None), allowed=True)

	def test_draft_payload_of_completed_closing_cannot_print(self):
		self.check(SimpleNamespace(docstatus=1, status="Submitted", error_message=""), docstatus=0)

	def test_sub_closing_requires_submission_without_main_consolidation(self):
		for docstatus in [0, 2]:
			self.check(SimpleNamespace(docstatus=docstatus), doctype="Sub POS Closing")
		self.check(SimpleNamespace(docstatus=1), doctype="Sub POS Closing", allowed=True)
