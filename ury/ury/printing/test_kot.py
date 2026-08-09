from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ury.ury.printing.kot import (
	TRANSPORT_ESCPOS,
	build_job_key,
	run_print_job,
	sanitize_escpos_text,
	validate_escpos_target,
)
from ury.ury.printing.kot_format import FEED_AND_CUT, INIT, RAW_COMMANDS


class TestKOTPrinting(TestCase):
	def test_job_key_is_deterministic_and_target_specific(self):
		first = build_job_key("KOT-0001", "7", "Kitchen")
		self.assertEqual(first, build_job_key("KOT-0001", "7", "Kitchen"))
		self.assertEqual(first, build_job_key("KOT-0001", 7, "Kitchen"))
		self.assertNotEqual(first, build_job_key("KOT-0001", "8", "Kitchen"))

	def test_valid_escpos_target_accepts_ip_or_hostname(self):
		self.assertEqual(validate_escpos_target("192.168.18.2", 9100, 5), ("192.168.18.2", 9100, 5.0))
		self.assertEqual(validate_escpos_target("kot.local", 9100, 2), ("kot.local", 9100, 2.0))

	def test_format_has_initialize_and_cut_commands(self):
		self.assertTrue(RAW_COMMANDS.startswith(INIT))
		self.assertTrue(RAW_COMMANDS.endswith(FEED_AND_CUT))
		frappe.get_jenv().from_string(RAW_COMMANDS)

	def test_text_cannot_inject_escpos_control_commands(self):
		self.assertEqual(sanitize_escpos_text("Sem sal\x1b@\nurgente"), "Sem sal @ urgente")

	@patch("ury.ury.printing.kot._finish_job")
	@patch("ury.ury.printing.kot.socket.create_connection")
	@patch("ury.ury.printing.kot._get_printer_timeout", return_value=5)
	@patch("ury.ury.printing.kot.validate_escpos_target", return_value=("192.168.18.2", 9100, 5.0))
	@patch("ury.ury.printing.kot.render_escpos_payload", return_value=b"test-payload")
	@patch("ury.ury.printing.kot._start_job")
	def test_successful_tcp_send_is_recorded_as_sent(
		self,
		start_job,
		render_payload,
		validate_target,
		get_timeout,
		create_connection,
		finish_job,
	):
		start_job.return_value = _escpos_job()
		connection = MagicMock()
		create_connection.return_value = connection

		with patch.object(frappe.db, "set_value"), patch.object(frappe.db, "commit"):
			result = run_print_job("job-1")

		self.assertEqual(result["status"], "Sent")
		connection.sendall.assert_called_once_with(b"test-payload")
		finish_job.assert_called_once()
		self.assertEqual(finish_job.call_args.args[:2], ("job-1", "Sent"))

	@patch("ury.ury.printing.kot.frappe.log_error")
	@patch("ury.ury.printing.kot._finish_job")
	@patch("ury.ury.printing.kot.socket.create_connection")
	@patch("ury.ury.printing.kot._get_printer_timeout", return_value=5)
	@patch("ury.ury.printing.kot.validate_escpos_target", return_value=("192.168.18.2", 9100, 5.0))
	@patch("ury.ury.printing.kot.render_escpos_payload", return_value=b"test-payload")
	@patch("ury.ury.printing.kot._start_job")
	def test_send_error_is_ambiguous_and_not_automatically_retried(
		self,
		start_job,
		render_payload,
		validate_target,
		get_timeout,
		create_connection,
		finish_job,
		log_error,
	):
		start_job.return_value = _escpos_job()
		connection = MagicMock()
		connection.sendall.side_effect = OSError("connection lost")
		create_connection.return_value = connection

		with patch.object(frappe.db, "set_value"), patch.object(frappe.db, "commit"):
			result = run_print_job("job-1")

		self.assertEqual(result["status"], "Ambiguous")
		self.assertEqual(finish_job.call_args.args[:2], ("job-1", "Ambiguous"))


def _escpos_job():
	return frappe._dict(
		name="job-1",
		transport=TRANSPORT_ESCPOS,
		kot="KOT-0001",
		print_format="Kitchen",
		escpos_host="192.168.18.2",
		escpos_port=9100,
		printer_setting="7",
	)
