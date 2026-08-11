import frappe
import frappe.sessions


no_cache = 1


def get_context(context):
	csrf_token = frappe.sessions.get_csrf_token()
	# Persist the CSRF token before the page starts making same-origin API calls.
	frappe.db.commit()  # nosemgrep
	context.csrf_token = csrf_token
	context.session_user = frappe.session.user
	return context
