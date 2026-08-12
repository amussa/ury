"""Request boundary for the dedicated mobile waiter account."""

from __future__ import annotations

import frappe
from frappe import _


WAITER_ROLE = "URY Waiter Mobile"
WAITER_API_PREFIX = "ury.ury_pos.waiter_api."
ALLOWED_WAITER_METHODS = frozenset(
    {
        f"{WAITER_API_PREFIX}get_context",
        f"{WAITER_API_PREFIX}get_tables",
        f"{WAITER_API_PREFIX}get_menu",
        f"{WAITER_API_PREFIX}get_table_order",
        f"{WAITER_API_PREFIX}register_order",
        "frappe.auth.get_logged_user",
        # Authentication is completed before auth_hooks execute.
        "login",
        # frappe-js-sdk FrappeAuth.logout() posts to /api/method/logout.
        "logout",
        # Frappe's website/portal user menu links to /?cmd=web_logout.
        "web_logout",
    }
)


def is_dedicated_waiter_user(user):
    """Return true only for an explicitly assigned, non-administrator waiter."""
    if not user or user in {"Guest", "Administrator"}:
        return False

    roles = set(frappe.get_roles(user))
    if "System Manager" in roles or WAITER_ROLE not in roles:
        return False

    # Administrator implicitly receives every role in get_roles(). Requiring
    # the real User child-row also prevents any future implicit/default role
    # expansion from turning an unrelated account into a waiter-only account.
    return bool(
        frappe.db.exists(
            "Has Role",
            {
                "parent": user,
                "parenttype": "User",
                "role": WAITER_ROLE,
            },
        )
    )


def _request_command(request):
    command = str(frappe.form_dict.get("cmd") or "").strip()
    if command:
        return command

    path = str(getattr(request, "path", "") or "").rstrip("/")
    prefix = "/api/method/"
    if path.startswith(prefix):
        return path[len(prefix) :]
    return None


def _is_allowed_waiter_request(path, command):
    if command:
        return command in ALLOWED_WAITER_METHODS
    if path.startswith("/api/"):
        return False
    if path == "/app" or path.startswith("/app/"):
        return False
    if path.startswith("/private/files/") or path.startswith("/backups"):
        return False
    # /waiter, public website routes, /assets and /files remain available.
    return True


def restrict_waiter_requests():
    """Deny every API/RPC outside the five waiter methods and session basics."""
    request = getattr(frappe.local, "request", None)
    user = getattr(frappe.session, "user", None)
    if not request or not user or user == "Guest":
        return
    if not is_dedicated_waiter_user(user):
        return

    path = str(getattr(request, "path", "") or "")
    command = _request_command(request)
    if not _is_allowed_waiter_request(path, command):
        frappe.throw(
            _("This account can only use the mobile waiter order API."),
            frappe.PermissionError,
        )
