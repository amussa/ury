import inspect
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, TextTestRunner, defaultTestLoader
from unittest.mock import Mock, patch

import frappe

from ury import hooks as ury_hooks
from ury.ury_pos.waiter_security import (
    ALLOWED_WAITER_METHODS,
    _is_allowed_waiter_request,
)
from ury.ury_pos.waiter_web import (
    WAITER_API_PATH_PREFIX,
    WAITER_MANIFEST_PATH,
    WAITER_PATH,
    WAITER_PRIVATE_METHODS,
    WAITER_SERVICE_WORKER_PATH,
    set_waiter_response_headers,
)
from ury.www import waiter as waiter_page


APP_ROOT = Path(__file__).resolve().parents[1]


def response_with(vary=None):
    headers = {}
    if vary:
        headers["Vary"] = vary
    return SimpleNamespace(headers=headers)


class TestWaiterGuestLoginBoundary(TestCase):
    def test_guest_page_bootstraps_only_csrf_without_session_boot(self):
        source = inspect.getsource(waiter_page)
        self.assertIn("frappe.sessions.get_csrf_token()", source)
        self.assertIn("context.csrf_token = csrf_token", source)
        self.assertIn("context.session_user = frappe.session.user", source)
        self.assertNotIn("frappe.sessions.get()", source)
        self.assertNotIn("get_boot_data", source)
        self.assertNotIn('"boot"', source)
        self.assertNotIn("redirect", source)

        # Inspect the built runtime template so this regression also runs in
        # production overlay images, where the frontend source tree is absent.
        html = (APP_ROOT / "www" / "waiter.html").read_text()
        self.assertIn('window.csrf_token = "{{ csrf_token }}"', html)
        self.assertIn(
            'data-session-user="{{ session_user | e }}"', html
        )
        self.assertNotIn("frappe.boot", html)
        self.assertNotIn("window.app_name", html)

    def test_page_context_exposes_only_csrf_and_escaped_session_identity(self):
        commit = Mock()
        fake_frappe = SimpleNamespace(
            sessions=SimpleNamespace(get_csrf_token=lambda: "csrf-token"),
            db=SimpleNamespace(commit=commit),
            session=SimpleNamespace(user="Guest"),
        )
        context = SimpleNamespace()

        with patch.object(waiter_page, "frappe", fake_frappe):
            result = waiter_page.get_context(context)

        self.assertIs(result, context)
        self.assertEqual(context.csrf_token, "csrf-token")
        self.assertEqual(context.session_user, "Guest")
        self.assertEqual(vars(context), {
            "csrf_token": "csrf-token",
            "session_user": "Guest",
        })
        commit.assert_called_once_with()

    def test_login_and_session_basics_are_the_only_extra_allowed_methods(self):
        self.assertIn("login", ALLOWED_WAITER_METHODS)
        self.assertIn("logout", ALLOWED_WAITER_METHODS)
        self.assertIn("frappe.auth.get_logged_user", ALLOWED_WAITER_METHODS)
        self.assertTrue(_is_allowed_waiter_request("/api/method/login", "login"))
        self.assertFalse(
            _is_allowed_waiter_request(
                "/api/method/frappe.client.set_value",
                "frappe.client.set_value",
            )
        )


class TestWaiterPWAResponses(TestCase):
    def test_waiter_html_is_private_and_never_cached(self):
        response = response_with("Accept-Encoding")
        set_waiter_response_headers(
            response, SimpleNamespace(path="/waiter/orders")
        )
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["Expires"], "0")
        self.assertEqual(response.headers["Vary"], "Accept-Encoding, Cookie")

    def test_service_worker_has_exact_scope_and_no_cache_headers(self):
        response = response_with()
        set_waiter_response_headers(
            response, SimpleNamespace(path=WAITER_SERVICE_WORKER_PATH)
        )
        self.assertEqual(response.headers["Service-Worker-Allowed"], WAITER_PATH)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(
            response.headers["Content-Type"],
            "application/javascript; charset=utf-8",
        )

        source = (APP_ROOT / "www" / "waiter-service-worker.js").read_text()
        self.assertNotIn('addEventListener("fetch"', source)
        self.assertNotIn("caches.", source)

    def test_login_identity_and_waiter_apis_are_never_cached(self):
        self.assertEqual(WAITER_PRIVATE_METHODS, ALLOWED_WAITER_METHODS)
        for path in (
            "/api/method/login",
            "/api/method/logout",
            "/api/method/frappe.auth.get_logged_user",
            f"{WAITER_API_PATH_PREFIX}get_tables",
        ):
            with self.subTest(path=path):
                response = response_with()
                set_waiter_response_headers(
                    response, SimpleNamespace(path=path)
                )
                self.assertIn("private", response.headers["Cache-Control"])
                self.assertIn("no-store", response.headers["Cache-Control"])
                self.assertIn("Cookie", response.headers["Vary"])

    def test_legacy_cmd_waiter_api_is_never_cached(self):
        response = response_with()
        with patch.object(
            frappe,
            "form_dict",
            {"cmd": " ury.ury_pos.waiter_api.get_tables "},
        ):
            set_waiter_response_headers(
                response, SimpleNamespace(path="/")
            )

        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("Cookie", response.headers["Vary"])

    def test_manifest_contract_and_headers_are_installable(self):
        response = response_with()
        set_waiter_response_headers(
            response, SimpleNamespace(path=WAITER_MANIFEST_PATH)
        )
        self.assertEqual(
            response.headers["Content-Type"],
            "application/manifest+json; charset=utf-8",
        )
        manifest = json.loads(
            (APP_ROOT / "www" / "waiter-manifest.webmanifest").read_text()
        )
        self.assertEqual(manifest["id"], WAITER_PATH)
        self.assertEqual(manifest["name"], "Gelatiamo Atendente")
        self.assertEqual(manifest["short_name"], "Atendente")
        self.assertEqual(manifest["start_url"], WAITER_PATH)
        self.assertEqual(manifest["scope"], WAITER_PATH)
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(
            {icon["sizes"] for icon in manifest["icons"]},
            {"192x192", "512x512"},
        )
        self.assertEqual(
            {icon["purpose"] for icon in manifest["icons"]}, {"any"}
        )

    def test_pwa_routes_are_public_resources_but_apis_remain_blocked(self):
        self.assertTrue(
            _is_allowed_waiter_request(WAITER_MANIFEST_PATH, None)
        )
        self.assertTrue(
            _is_allowed_waiter_request(WAITER_SERVICE_WORKER_PATH, None)
        )
        self.assertFalse(_is_allowed_waiter_request("/api/resource/User", None))

    def test_after_request_hook_is_active(self):
        self.assertIn(
            "ury.ury_pos.waiter_web.set_waiter_response_headers",
            ury_hooks.after_request,
        )


def run_unit_tests():
    """Run the mock/static waiter web regressions through bench execute."""
    stream = io.StringIO()
    suite = defaultTestLoader.loadTestsFromModule(
        __import__(__name__, fromlist=["*"])
    )
    result = TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError(stream.getvalue())
    return {"tests_run": result.testsRun, "successful": True}
