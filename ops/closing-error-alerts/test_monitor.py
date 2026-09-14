import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import monitor


class ClosingAlertTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(name="POS-CLO-TEST", status="Failed", docstatus=1,
                        modified="2026-09-15 01:00:00", error_message="Original error",
                        pos_profile="POS Polana", user="cashier@example.com", grand_total=120)
        self.state = {"version": 1, "alerts": {}}
        self.frappe = SimpleNamespace(db=Mock(), sendmail=Mock(return_value=SimpleNamespace(name="queue-test")))
        self.frappe.db.get_value.return_value = None
        self.persist = Mock()

    def run_rows(self, rows=None, **kwargs):
        return monitor.process_rows(self.frappe, [self.row] if rows is None else rows,
                                    self.state, self.persist, "gelati.app.co.mz", "akilmussa@gmail.com", **kwargs)

    def test_failed_document_queues_once_across_polls(self):
        self.assertEqual(self.run_rows()[0]["result"], "queued")
        self.assertEqual(self.run_rows()[0]["result"], "already_queued")
        self.frappe.sendmail.assert_called_once()
        args = self.frappe.sendmail.call_args.kwargs
        self.assertEqual(args["recipients"], ["akilmussa@gmail.com"])
        self.assertEqual(args["reference_name"], self.row["name"])
        self.assertFalse(args["now"])
        self.assertNotIn("<", args["message_id"])
        self.frappe.db.commit.assert_called_once()

    def test_success_cancelled_and_draft_do_not_alert(self):
        for status, docstatus in [("Submitted", 1), ("Queued", 1), ("Cancelled", 2), ("Failed", 0)]:
            self.assertEqual(self.run_rows([dict(self.row, status=status, docstatus=docstatus)]), [])
        self.frappe.sendmail.assert_not_called()

    def test_new_failure_of_same_closing_alerts_again(self):
        self.run_rows()
        self.row["modified"] = "2026-09-15 01:10:00"
        self.run_rows()
        self.assertEqual(self.frappe.sendmail.call_count, 2)

    def test_crash_after_db_commit_recovers_existing_queue(self):
        self.frappe.db.get_value.return_value = "queue-before-crash"
        self.assertEqual(self.run_rows()[0]["queue"], "queue-before-crash")
        self.frappe.sendmail.assert_not_called()
        self.persist.assert_called_once()

    def test_failed_queue_creation_does_not_mark_alert_processed(self):
        self.frappe.sendmail.side_effect = RuntimeError("SMTP configuration unavailable")
        with self.assertRaises(RuntimeError):
            self.run_rows()
        self.assertEqual(self.state["alerts"], {})
        self.frappe.db.commit.assert_not_called()

    def test_dry_run_does_not_enqueue_or_modify_state(self):
        original = copy.deepcopy(self.state)
        self.assertEqual(self.run_rows(dry_run=True)[0]["result"], "would_queue")
        self.assertEqual(original, self.state)
        self.frappe.sendmail.assert_not_called()
        self.persist.assert_not_called()

    def test_test_email_is_labelled_and_not_linked_to_real_document(self):
        self.run_rows(test=True)
        args = self.frappe.sendmail.call_args.kwargs
        self.assertIn("[TESTE]", args["subject"])
        self.assertIn("TESTE SIMULADO", args["message"])
        self.assertNotIn("reference_name", args)
        self.assertNotEqual(monitor.failure_key(self.row, "a@b.com"), monitor.failure_key(self.row, "a@b.com", True))

    def test_html_from_error_is_escaped(self):
        self.row["error_message"] = '<script>alert("error")</script>'
        _, body = monitor.render_message(self.row, "gelati.app.co.mz")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("https://gelati.app.co.mz/app/pos-closing-entry/POS-CLO-TEST", body)

    def test_state_survives_restart(self):
        self.run_rows()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.json"
            monitor.save_state(path, self.state)
            self.state = monitor.json.loads(path.read_text())
            self.assertEqual(self.run_rows()[0]["result"], "already_queued")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
