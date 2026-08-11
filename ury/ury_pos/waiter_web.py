"""HTTP response policy for the mobile waiter shell and PWA resources."""

from __future__ import annotations


WAITER_PATH = "/waiter"
WAITER_MANIFEST_PATH = "/waiter-manifest.webmanifest"
WAITER_SERVICE_WORKER_PATH = "/waiter-service-worker.js"
WAITER_API_PATH_PREFIX = "/api/method/ury.ury_pos.waiter_api."
WAITER_API_METHOD_PREFIX = "ury.ury_pos.waiter_api."
WAITER_API_METHODS = frozenset(
    {
        f"{WAITER_API_METHOD_PREFIX}get_context",
        f"{WAITER_API_METHOD_PREFIX}get_tables",
        f"{WAITER_API_METHOD_PREFIX}get_menu",
        f"{WAITER_API_METHOD_PREFIX}get_table_order",
        f"{WAITER_API_METHOD_PREFIX}register_order",
    }
)
WAITER_SESSION_PATHS = frozenset(
    {
        "/api/method/login",
        "/api/method/logout",
        "/api/method/frappe.auth.get_logged_user",
    }
)
WAITER_SESSION_METHODS = frozenset(
    {"login", "logout", "frappe.auth.get_logged_user"}
)
WAITER_PRIVATE_METHODS = WAITER_API_METHODS | WAITER_SESSION_METHODS


def _request_command():
    """Read Frappe's normalized RPC command for legacy ``/?cmd=...`` calls."""
    try:
        import frappe

        return str(frappe.form_dict.get("cmd") or "").strip()
    except (AttributeError, RuntimeError):
        # Static-resource requests and isolated unit tests may not have a
        # fully initialised Frappe local context.
        return ""


def _add_vary(headers, value):
    current = str(headers.get("Vary") or "")
    values = [part.strip() for part in current.split(",") if part.strip()]
    if value.lower() not in {part.lower() for part in values}:
        values.append(value)
    headers["Vary"] = ", ".join(values)


def _set_private_no_store(headers):
    headers["Cache-Control"] = (
        "private, no-store, no-cache, must-revalidate, max-age=0"
    )
    headers["Pragma"] = "no-cache"
    headers["Expires"] = "0"
    headers["X-Content-Type-Options"] = "nosniff"
    _add_vary(headers, "Cookie")


def set_waiter_response_headers(response, request):
    """Keep session HTML private and make the root-scoped PWA deterministic.

    The service worker intentionally has no fetch handler, but its script is
    still served with no-store so a deployment is revalidated immediately.
    """
    if response is None or request is None:
        return

    path = str(getattr(request, "path", "") or "")
    command = _request_command()
    headers = getattr(response, "headers", None)
    if headers is None:
        return

    if path == WAITER_SERVICE_WORKER_PATH:
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        headers["Pragma"] = "no-cache"
        headers["Expires"] = "0"
        headers["Content-Type"] = "application/javascript; charset=utf-8"
        headers["Service-Worker-Allowed"] = WAITER_PATH
        headers["Cross-Origin-Resource-Policy"] = "same-origin"
        headers["X-Content-Type-Options"] = "nosniff"
        return

    if path == WAITER_MANIFEST_PATH:
        headers["Cache-Control"] = "public, max-age=300, must-revalidate"
        headers["Content-Type"] = "application/manifest+json; charset=utf-8"
        headers["Cross-Origin-Resource-Policy"] = "same-origin"
        headers["X-Content-Type-Options"] = "nosniff"
        return

    if (
        path in WAITER_SESSION_PATHS
        or path.startswith(WAITER_API_PATH_PREFIX)
        or command in WAITER_PRIVATE_METHODS
    ):
        # Identity, CSRF-adjacent login state, stock, tables and active orders
        # are always session-specific and time-sensitive.
        _set_private_no_store(headers)
        return

    if path == WAITER_PATH or path.startswith(f"{WAITER_PATH}/"):
        # This HTML embeds the current session boot data and CSRF token. It
        # must never be stored by a browser, shared proxy, or service worker.
        _set_private_no_store(headers)
