from __future__ import annotations

import frappe

READ_ONLY_PERMISSION_TYPES = {None, "read", "select", "report", "export", "print", "email"}
GLOBAL_ACCESS_ROLES = {"System Manager", "URY Manager"}


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if has_global_access(user):
		return ""

	escaped_user = frappe.db.escape(user)
	return f"""
		exists (
			select 1
			from `tabURY User` ury_user
			where ury_user.parenttype = 'Branch'
			  and ury_user.user = {escaped_user}
			  and ury_user.parent = `tabURY POS Settlement`.branch
		)
	"""


def has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if has_global_access(user):
		return True
	if ptype not in READ_ONLY_PERMISSION_TYPES:
		return False
	if not doc or not doc.get("branch"):
		return False

	return bool(
		frappe.db.exists(
			"URY User",
			{
				"parenttype": "Branch",
				"parent": doc.branch,
				"user": user,
			},
		)
	)


def has_global_access(user):
	return user == "Administrator" or bool(
		GLOBAL_ACCESS_ROLES.intersection(frappe.get_roles(user))
	)
