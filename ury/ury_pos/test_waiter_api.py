import ast
import inspect
import io
import json
from datetime import datetime
from decimal import Decimal
from unittest import TestCase, TextTestRunner, defaultTestLoader
from unittest.mock import Mock, patch

import frappe
from ury import hooks as ury_hooks

from ury.ury.api.ury_kot_generate import _kot_execute, kot_execute
from ury.ury.doctype.ury_kot.ury_kot import URYKOT
from ury.ury.doctype.ury_order.ury_order import (
    _append_server_priced_order_items,
    _assert_existing_order_items_unchanged,
    _get_locked_waiter_menu,
    _get_locked_waiter_item_prices,
    _get_locked_waiter_price_list,
    _snapshot_existing_order_items,
    _sync_order,
    sync_order,
)
from ury.ury.hooks.ury_pos_invoice import validate_price_list
from ury.ury_pos.waiter_api import (
    WaiterConflictError,
    WaiterRequestInProgressError,
    _active_table_invoices,
    _create_waiter_kots,
    _format_order,
    _get_authoritative_menu,
    _get_exact_pos_profile,
    _hydrate_pending_items,
    _normalise_pending_items,
    _replay_waiter_request,
    _reserve_waiter_request,
    _require_waiter_user,
    _select_editable_invoice,
    _validate_expected_modified,
    _validate_production_routes,
    get_context,
    get_menu,
    get_table_order,
    get_tables,
    register_order,
)
from ury.ury_pos.waiter_security import (
    ALLOWED_WAITER_METHODS,
    WAITER_ROLE,
    _is_allowed_waiter_request,
    is_dedicated_waiter_user,
    restrict_waiter_requests,
)
from ury.ury_pos.waiter_user_setup import (
    FORBIDDEN_ACTIONS,
    FORBIDDEN_DOCTYPES,
)


def raise_frappe(message, exc=frappe.ValidationError, **_kwargs):
    if isinstance(exc, type):
        raise exc(message)
    raise exc


def actor_context():
    return frappe._dict(
        user="waiter@example.com",
        branch="Branch A",
        rooms=["Room A"],
        multiple_cashier=False,
        profile=frappe._dict(
            name="POS A",
            branch="Branch A",
            warehouse="Warehouse A",
            customer="Walk In",
            custom_enable_multiple_cashier=0,
            payments=[frappe._dict(mode_of_payment="Cash")],
        ),
    )


class FakeSyncInvoice:
    """Small POS Invoice double for exercising the two _sync_order branches."""

    def __init__(self, *, name=None, is_new=True):
        self.name = name
        self._is_new = is_new
        self.branch = "Branch A"
        self.restaurant = "Restaurant A"
        self.restaurant_table = "Table 1" if not is_new else None
        self.selling_price_list = "ROOM-PRICE-LIST"
        self.pos_profile = None
        self.invoice_printed = 0
        self.invoice_created = 1
        self.grand_total = 0
        self.items = []
        self.payments = []
        self.custom_comments = "existing general note"
        self.custom_merged_tables = None
        self.creation = datetime(2026, 8, 10, 11, 0, 0)
        self.modified = datetime(2026, 8, 10, 12, 0, 0)
        self.saved_waiter_price_list = None
        self.saved_ignore_permissions = None
        self.reload_count = 0

    def is_new(self):
        return self._is_new

    def append(self, fieldname, values):
        row = frappe._dict(values)
        row.name = f"{fieldname.upper()}-{len(getattr(self, fieldname)) + 1}"
        row.idx = len(getattr(self, fieldname)) + 1
        getattr(self, fieldname).append(row)
        return row

    def set_missing_values(self, **_kwargs):
        return None

    def save(self, ignore_permissions=None):
        self.saved_waiter_price_list = getattr(
            frappe.flags, "ury_waiter_expected_price_list", None
        )
        self.saved_ignore_permissions = ignore_permissions
        if not self.name:
            self.name = "INV-NEW"
        self._is_new = False

    def reload(self):
        self.reload_count += 1
        return self

    def as_dict(self):
        return {"name": self.name}


class TestWaiterAPI(TestCase):
    def test_waiter_rejects_menu_changed_after_table_lock(self):
        current_rows = [
            [
                frappe._dict(
                    name="Table 1",
                    restaurant="Restaurant A",
                    restaurant_room="Room A",
                )
            ],
            [
                frappe._dict(
                    name="Restaurant A",
                    active_menu="NEW-MENU",
                    room_wise_menu=0,
                )
            ],
        ]
        with patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.sql",
            side_effect=current_rows,
        ) as sql, patch(
            "ury.ury.doctype.ury_order.ury_order.lock_menu_price_options_parent"
        ) as lock_menu, patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.throw",
            side_effect=raise_frappe,
        ):
            with self.assertRaises(frappe.TimestampMismatchError):
                _get_locked_waiter_menu(
                    "Table 1",
                    "Room A",
                    "OLD-MENU",
                    ["ITEM-A"],
                )

        self.assertEqual(sql.call_count, 2)
        self.assertTrue(
            all("FOR UPDATE" in call.args[0] for call in sql.call_args_list)
        )
        lock_menu.assert_not_called()

    def test_waiter_rejects_item_disabled_after_menu_lock(self):
        events = []

        def current_rows(query, _values, as_dict=False):
            self.assertTrue(as_dict)
            events.append(
                next(
                    table
                    for table in (
                        "tabURY Table",
                        "tabURY Restaurant",
                        "tabURY Menu Item",
                    )
                    if table in query
                )
            )
            if "tabURY Table" in query:
                return [
                    frappe._dict(
                        name="Table 1",
                        restaurant="Restaurant A",
                        restaurant_room="Room A",
                    )
                ]
            if "tabURY Restaurant" in query:
                return [
                    frappe._dict(
                        name="Restaurant A",
                        active_menu="ROOM-MENU",
                        room_wise_menu=0,
                    )
                ]
            return [
                frappe._dict(
                    name="MENU-ITEM-1",
                    item="ITEM-A",
                    disabled=1,
                )
            ]

        with patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.sql",
            side_effect=current_rows,
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.lock_menu_price_options_parent",
            side_effect=lambda _menu: events.append("tabURY Menu"),
        ) as lock_menu, patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.throw",
            side_effect=raise_frappe,
        ):
            with self.assertRaises(frappe.TimestampMismatchError):
                _get_locked_waiter_menu(
                    "Table 1",
                    "Room A",
                    "ROOM-MENU",
                    ["ITEM-A"],
                )

        lock_menu.assert_called_once_with("ROOM-MENU")
        self.assertEqual(
            events,
            [
                "tabURY Table",
                "tabURY Restaurant",
                "tabURY Menu",
                "tabURY Menu Item",
            ],
        )

    def test_waiter_rejects_globally_disabled_item_after_menu_lock(self):
        current_rows = [
            [
                frappe._dict(
                    name="Table 1",
                    restaurant="Restaurant A",
                    restaurant_room="Room A",
                )
            ],
            [
                frappe._dict(
                    name="Restaurant A",
                    active_menu="ROOM-MENU",
                    room_wise_menu=0,
                )
            ],
            [
                frappe._dict(
                    name="MENU-ITEM-1",
                    item="ITEM-A",
                    disabled=0,
                )
            ],
            [frappe._dict(name="ITEM-A", disabled=1)],
        ]
        with patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.sql",
            side_effect=current_rows,
        ) as sql, patch(
            "ury.ury.doctype.ury_order.ury_order.lock_menu_price_options_parent"
        ) as lock_menu, patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.throw",
            side_effect=raise_frappe,
        ):
            with self.assertRaises(frappe.TimestampMismatchError):
                _get_locked_waiter_menu(
                    "Table 1",
                    "Room A",
                    "ROOM-MENU",
                    ["ITEM-A"],
                )

        self.assertEqual(sql.call_count, 4)
        self.assertTrue(
            all("FOR UPDATE" in call.args[0] for call in sql.call_args_list)
        )
        lock_menu.assert_called_once_with("ROOM-MENU")

    def test_waiter_rejects_disabled_or_rebound_price_list_under_lock(self):
        with patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.sql",
            return_value=[
                frappe._dict(
                    name="ROOM-PRICE-LIST",
                    enabled=0,
                    selling=1,
                    restaurant_menu="OTHER-MENU",
                )
            ],
        ) as sql, patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.throw",
            side_effect=raise_frappe,
        ):
            with self.assertRaises(frappe.TimestampMismatchError):
                _get_locked_waiter_price_list(
                    "ROOM-MENU",
                    "ROOM-PRICE-LIST",
                )

        sql.assert_called_once()
        self.assertIn("FOR UPDATE", sql.call_args.args[0])

    @patch(
        "ury.ury.doctype.ury_order.ury_order.get_authoritative_item_prices",
        return_value={"ITEM-A": 180},
    )
    @patch(
        "ury.ury.doctype.ury_order.ury_order._get_locked_waiter_price_list"
    )
    @patch(
        "ury.ury.doctype.ury_order.ury_order.lock_menu_price_options_parent"
    )
    def test_waiter_rejects_price_snapshot_changed_before_menu_lock(
        self,
        lock_menu_parent,
        lock_price_list,
        get_current_prices,
    ):
        events = []
        lock_menu_parent.side_effect = lambda _menu: events.append("parent")
        get_current_prices.side_effect = lambda *_args, **_kwargs: (
            events.append("prices") or {"ITEM-A": 180}
        )
        with patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.throw",
            side_effect=raise_frappe,
        ):
            with self.assertRaises(frappe.TimestampMismatchError):
                _get_locked_waiter_item_prices(
                    "ROOM-MENU",
                    ["ITEM-A"],
                    "ROOM-PRICE-LIST",
                    {"ITEM-A": 170},
                )

        lock_menu_parent.assert_called_once_with("ROOM-MENU")
        lock_price_list.assert_called_once_with(
            "ROOM-MENU", "ROOM-PRICE-LIST"
        )
        get_current_prices.assert_called_once_with(
            ["ITEM-A"], "ROOM-PRICE-LIST", for_update=True
        )
        self.assertEqual(events, ["parent", "prices"])

    @patch(
        "ury.ury.doctype.ury_order.ury_order.get_authoritative_item_prices",
        return_value={},
    )
    @patch(
        "ury.ury.doctype.ury_order.ury_order._get_locked_waiter_price_list"
    )
    @patch(
        "ury.ury.doctype.ury_order.ury_order.lock_menu_price_options_parent"
    )
    def test_waiter_reports_missing_current_price_as_conflict(
        self,
        lock_menu_parent,
        lock_price_list,
        _get_current_prices,
    ):
        with patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.throw",
            side_effect=raise_frappe,
        ):
            with self.assertRaises(frappe.TimestampMismatchError):
                _get_locked_waiter_item_prices(
                    "ROOM-MENU",
                    ["ITEM-A"],
                    "ROOM-PRICE-LIST",
                    {"ITEM-A": 170},
                )

        lock_menu_parent.assert_called_once_with("ROOM-MENU")
        lock_price_list.assert_called_once_with(
            "ROOM-MENU", "ROOM-PRICE-LIST"
        )
        _get_current_prices.assert_called_once_with(
            ["ITEM-A"], "ROOM-PRICE-LIST", for_update=True
        )

    def test_formatted_order_preserves_price_option_snapshot(self):
        invoice = frappe._dict(
            name="POS-INV-1",
            modified=datetime(2026, 8, 12, 10, 0, 0),
            restaurant_table="Table 1",
            custom_restaurant_room="Room A",
            waiter=frappe.session.user,
            no_of_pax=1,
            custom_comments=None,
            grand_total=80,
            items=[
                frappe._dict(
                    name="ROW-1",
                    item_code="CAKE-SLICE",
                    item_name="Cake Slice",
                    qty=1,
                    uom="Nos",
                    rate=80,
                    amount=80,
                    comment=None,
                    custom_ury_price_option="PROMO-1",
                    custom_ury_price_option_label="Promotion",
                )
            ],
        )

        formatted = _format_order(invoice, editable=True)

        self.assertEqual(formatted["items"][0]["price_option"], "PROMO-1")
        self.assertEqual(
            formatted["items"][0]["price_option_label"], "Promotion"
        )

    def test_pending_items_are_integer_positive_and_preserve_distinct_comments(self):
        items = _normalise_pending_items(
            [
                {"item": "ITEM-A", "qty": 1, "expected_rate": 170, "comment": "sem gelo"},
                {"item_code": "ITEM-A", "qty": "2", "expected_rate": 170, "comment": "sem gelo"},
                {"item": "ITEM-A", "qty": 1, "expected_rate": 170, "comment": "pouco açúcar"},
            ]
        )

        self.assertEqual(
            items,
            [
                {"item": "ITEM-A", "qty": 3, "comment": "sem gelo", "expected_rate": Decimal("170")},
                {"item": "ITEM-A", "qty": 1, "comment": "pouco açúcar", "expected_rate": Decimal("170")},
            ],
        )

        with patch(
            "ury.ury_pos.waiter_api.frappe.throw", side_effect=raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                _normalise_pending_items([{"item": "ITEM-A", "qty": 0.5, "expected_rate": 170}])
            with self.assertRaises(frappe.ValidationError):
                _normalise_pending_items([{"item": "ITEM-A", "qty": -1, "expected_rate": 170}])

    def test_pending_item_price_must_match_the_authoritative_menu(self):
        menu = frappe._dict(
            items=[
                {"item": "ITEM-A", "item_name": "Item A", "rate": 170}
            ]
        )
        pending = _normalise_pending_items(
            [{"item": "ITEM-A", "qty": 1, "expected_rate": 170}]
        )
        hydrated = _hydrate_pending_items(menu, pending)
        self.assertEqual(hydrated[0]["_authoritative_base_rate"], 170)
        self.assertEqual(hydrated[0]["_expected_option_rate"], 170)

        stale = _normalise_pending_items(
            [{"item": "ITEM-A", "qty": 1, "expected_rate": 150}]
        )
        with patch(
            "ury.ury_pos.waiter_api.frappe.throw", side_effect=raise_frappe
        ):
            with self.assertRaises(WaiterConflictError):
                _hydrate_pending_items(menu, stale)

    def test_pending_promotion_is_distinct_and_validated_by_option_rate(self):
        menu = frappe._dict(
            items=[
                {
                    "item": "ITEM-A",
                    "item_name": "Item A",
                    "rate": 170,
                    "price_options": [
                        {
                            "id": "standard",
                            "label": "Normal",
                            "rate": 170,
                            "available_qty": 6,
                        },
                        {
                            "id": "PROMO-1",
                            "label": "Promoção",
                            "rate": 120,
                            "available_qty": 4,
                        },
                    ],
                }
            ]
        )
        pending = _normalise_pending_items(
            [
                {
                    "item": "ITEM-A",
                    "qty": 2,
                    "expected_rate": 170,
                    "price_option": "standard",
                },
                {
                    "item": "ITEM-A",
                    "qty": 4,
                    "expected_rate": 120,
                    "price_option": "PROMO-1",
                },
            ]
        )

        hydrated = _hydrate_pending_items(menu, pending)

        self.assertEqual([row["price_option"] for row in hydrated], [
            "standard", "PROMO-1",
        ])
        self.assertEqual(
            [row["_authoritative_base_rate"] for row in hydrated],
            [170, 170],
        )
        self.assertEqual(
            [row["_expected_option_rate"] for row in hydrated],
            [170, 120],
        )

        stale = _normalise_pending_items(
            [{
                "item": "ITEM-A",
                "qty": 1,
                "expected_rate": 110,
                "price_option": "PROMO-1",
            }]
        )
        with patch(
            "ury.ury_pos.waiter_api.frappe.throw", side_effect=raise_frappe
        ):
            with self.assertRaises(WaiterConflictError):
                _hydrate_pending_items(menu, stale)

    def test_pending_promotion_rejects_quantity_above_option_partition(self):
        menu = frappe._dict(
            items=[{
                "item": "ITEM-A",
                "item_name": "Item A",
                "rate": 170,
                "price_options": [{
                    "id": "PROMO-1",
                    "label": "Promoção",
                    "rate": 120,
                    "available_qty": 1,
                }],
            }]
        )
        pending = _normalise_pending_items(
            [{
                "item": "ITEM-A",
                "qty": 2,
                "expected_rate": 120,
                "price_option": "PROMO-1",
            }]
        )
        with patch(
            "ury.ury_pos.waiter_api.frappe.throw", side_effect=raise_frappe
        ):
            with self.assertRaises(WaiterConflictError):
                _hydrate_pending_items(menu, pending)

    @patch("ury.ury_pos.waiter_api.frappe.db.sql", return_value=[])
    def test_active_invoice_query_uses_exact_csv_membership_and_lock(self, db_sql):
        _active_table_invoices("Branch A", "Table 1", lock=True)

        query = db_sql.call_args.args[0]
        self.assertIn("FIND_IN_SET", query)
        self.assertIn("FOR UPDATE", query)
        self.assertNotIn(" LIKE ", query.upper())
        self.assertEqual(
            db_sql.call_args.args[1],
            {"branch": "Branch A", "table": "Table 1"},
        )

    def test_only_own_single_unprinted_invoice_is_editable(self):
        actor = actor_context()
        table = frappe._dict(name="Table 1", occupied=1, merged_with=None)
        own = frappe._dict(
            name="INV-1",
            waiter=actor.user,
            invoice_printed=0,
            restaurant_table=table.name,
            custom_merged_tables=None,
            custom_merged_pos_invoice=None,
            custom_split_from=None,
            custom_split_group=None,
            pos_profile=actor.profile.name,
            branch=actor.branch,
            custom_restaurant_room="Room A",
        )

        self.assertIs(
            _select_editable_invoice(actor, table, "Room A", [own]), own
        )

        other = frappe._dict(own.copy())
        other.waiter = "another@example.com"
        with patch(
            "ury.ury_pos.waiter_api.frappe.throw", side_effect=raise_frappe
        ):
            with self.assertRaises(frappe.PermissionError):
                _select_editable_invoice(actor, table, "Room A", [other])
            with self.assertRaises(WaiterConflictError):
                _select_editable_invoice(actor, table, "Room A", [own, other])

            merged_table = frappe._dict(table.copy())
            merged_table.merged_with = "Table 2"
            with self.assertRaises(WaiterConflictError):
                _select_editable_invoice(actor, merged_table, "Room A", [])

            split = frappe._dict(own.copy())
            split.custom_split_group = "SPLIT-1"
            with self.assertRaises(WaiterConflictError):
                _select_editable_invoice(actor, table, "Room A", [split])

    def test_existing_order_requires_exact_modified_timestamp(self):
        invoice = frappe._dict(
            modified=datetime(2026, 8, 10, 12, 0, 0)
        )
        _validate_expected_modified(invoice, "2026-08-10 12:00:00")

        with patch(
            "ury.ury_pos.waiter_api.frappe.throw", side_effect=raise_frappe
        ):
            with self.assertRaises(WaiterConflictError):
                _validate_expected_modified(invoice, None)
            with self.assertRaises(WaiterConflictError):
                _validate_expected_modified(invoice, "2026-08-10 12:00:01")

    @patch("ury.ury_pos.waiter_api.frappe.get_all")
    def test_production_route_must_be_exactly_one_per_item(self, get_all):
        def one_route(doctype, **_kwargs):
            if doctype == "Item":
                return [
                    frappe._dict(name="ITEM-A", item_group="Drinks", disabled=0)
                ]
            if doctype == "URY Production Unit":
                return [frappe._dict(name="Bar")]
            return [frappe._dict(parent="Bar", item_group="Drinks")]

        get_all.side_effect = one_route
        self.assertEqual(
            _validate_production_routes("Branch A", ["ITEM-A"]),
            {"ITEM-A": "Bar"},
        )

        def duplicate_route(doctype, **_kwargs):
            if doctype == "Item":
                return [
                    frappe._dict(name="ITEM-A", item_group="Drinks", disabled=0)
                ]
            if doctype == "URY Production Unit":
                return [frappe._dict(name="Bar"), frappe._dict(name="Kitchen")]
            return [
                frappe._dict(parent="Bar", item_group="Drinks"),
                frappe._dict(parent="Kitchen", item_group="Drinks"),
            ]

        get_all.side_effect = duplicate_route
        with patch(
            "ury.ury_pos.waiter_api.frappe.throw", side_effect=raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                _validate_production_routes("Branch A", ["ITEM-A"])

        def missing_route(doctype, **_kwargs):
            if doctype == "Item":
                return [
                    frappe._dict(name="ITEM-A", item_group="Drinks", disabled=0)
                ]
            if doctype == "URY Production Unit":
                return [frappe._dict(name="Kitchen")]
            return []

        get_all.side_effect = missing_route
        with patch(
            "ury.ury_pos.waiter_api.frappe.throw", side_effect=raise_frappe
        ):
            with self.assertRaises(frappe.ValidationError):
                _validate_production_routes("Branch A", ["ITEM-A"])

    @patch("ury.ury.api.ury_production_routing.frappe.get_all")
    def test_menu_course_route_overrides_item_group_route(self, get_all):
        def route_data(doctype, **_kwargs):
            if doctype == "Item":
                return [
                    frappe._dict(name="CAFE-1", item_group="Gelados", disabled=0),
                    frappe._dict(name="CONE-1", item_group="Gelados", disabled=0),
                ]
            if doctype == "URY Production Unit":
                return [frappe._dict(name="Balcão"), frappe._dict(name="Bar")]
            if doctype == "URY Production Item Groups":
                return [frappe._dict(parent="Balcão", item_group="Gelados")]
            if doctype == "URY Production Menu Courses":
                return [frappe._dict(parent="Bar", menu_course="Café")]
            if doctype == "URY Menu Item":
                return [
                    frappe._dict(item="CAFE-1", course="Café"),
                    frappe._dict(item="CONE-1", course="Gelados"),
                ]
            return []

        get_all.side_effect = route_data
        self.assertEqual(
            _validate_production_routes(
                "Polana", ["CAFE-1", "CONE-1"], "Menu Polana"
            ),
            {"CAFE-1": "Bar", "CONE-1": "Balcão"},
        )

    def test_persistent_idempotency_replays_same_payload_and_rejects_conflict(self):
        stored_response = {
            "status": "success",
            "replayed": False,
            "idempotent": False,
            "invoice": "INV-1",
        }
        row = frappe._dict(
            name="key",
            payload_hash="same-hash",
            status="Completed",
            invoice="INV-1",
            response_json=json.dumps(stored_response),
        )

        replay = _replay_waiter_request(row, "same-hash")
        self.assertTrue(replay["replayed"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["invoice"], "INV-1")

        with patch(
            "ury.ury_pos.waiter_api.frappe.throw", side_effect=raise_frappe
        ):
            with self.assertRaises(WaiterConflictError):
                _replay_waiter_request(row, "different-hash")

        with patch(
            "ury.ury_pos.waiter_api._load_waiter_request", return_value=row
        ), patch("ury.ury_pos.waiter_api.frappe.get_doc") as get_doc:
            request_doc, replay = _reserve_waiter_request(
                "waiter@example.com", "request-0001", "same-hash"
            )
        self.assertIsNone(request_doc)
        self.assertTrue(replay["replayed"])
        get_doc.assert_not_called()

    @patch("ury.ury_pos.waiter_api.frappe.db.get_value")
    @patch("ury.ury_pos.waiter_api._opening_state")
    @patch("ury.ury_pos.waiter_api._get_actor_context")
    def test_context_exposes_open_flag_but_not_till_identity(
        self, get_actor, opening_state, get_value
    ):
        get_actor.return_value = actor_context()
        opening_state.return_value = {
            "is_open": True,
            "reason_code": None,
            "reason": None,
        }
        get_value.return_value = "Waiter One"

        result = get_context()

        self.assertEqual(
            result["user"],
            {"name": "waiter@example.com", "full_name": "Waiter One"},
        )
        self.assertTrue(result["opening"]["is_open"])
        serialised = json.dumps(result)
        self.assertNotIn("cashier", serialised)
        self.assertNotIn("POS-OPE", serialised)

    def test_public_sync_order_does_not_expose_skip_kot(self):
        self.assertNotIn("skip_kot", inspect.signature(sync_order).parameters)
        self.assertNotIn("strict_invoice", inspect.signature(sync_order).parameters)
        self.assertNotIn("append_only", inspect.signature(sync_order).parameters)
        self.assertNotIn(
            "expected_price_list", inspect.signature(sync_order).parameters
        )

    @patch("ury.ury_pos.waiter_api.frappe.get_doc")
    @patch("ury.ury_pos.waiter_api.frappe.get_all")
    def test_profile_resolver_selects_the_only_submitted_open_profile(
        self, get_all, get_doc
    ):
        def rows(doctype, **_kwargs):
            if doctype == "POS Profile User":
                return ["POS A", "POS B"]
            if doctype == "POS Profile":
                return [
                    frappe._dict(name="POS A"),
                    frappe._dict(name="POS B"),
                ]
            if doctype == "POS Opening Entry":
                return [frappe._dict(pos_profile="POS B")]
            self.fail(f"Unexpected DocType lookup: {doctype}")

        get_all.side_effect = rows
        profile = frappe._dict(name="POS B", warehouse="Warehouse B")
        get_doc.return_value = profile

        self.assertIs(
            _get_exact_pos_profile("Branch A", "waiter@example.com"),
            profile,
        )
        get_doc.assert_called_once_with("POS Profile", "POS B")

    def test_profile_resolver_fails_closed_for_ambiguous_profiles(self):
        profiles = [
            frappe._dict(name="POS A"),
            frappe._dict(name="POS B"),
        ]

        def profile_rows(openings):
            def rows(doctype, **_kwargs):
                if doctype == "POS Profile User":
                    return ["POS A", "POS B"]
                return profiles if doctype == "POS Profile" else openings

            return rows

        with patch(
            "ury.ury_pos.waiter_api.frappe.throw", side_effect=raise_frappe
        ), patch("ury.ury_pos.waiter_api.frappe.get_doc") as get_doc:
            with patch(
                "ury.ury_pos.waiter_api.frappe.get_all",
                side_effect=profile_rows(
                    [
                        frappe._dict(pos_profile="POS A"),
                        frappe._dict(pos_profile="POS B"),
                    ]
                ),
            ):
                with self.assertRaises(frappe.ValidationError):
                    _get_exact_pos_profile(
                        "Branch A", "waiter@example.com"
                    )

            with patch(
                "ury.ury_pos.waiter_api.frappe.get_all",
                side_effect=profile_rows([]),
            ):
                with self.assertRaises(frappe.ValidationError):
                    _get_exact_pos_profile(
                        "Branch A", "waiter@example.com"
                    )

        get_doc.assert_not_called()

    @patch("ury.ury_pos.waiter_api.get_item_price_options", return_value={})
    @patch("ury.ury_pos.waiter_api.get_authoritative_item_prices")
    @patch("ury.ury_pos.waiter_api.get_authoritative_menu_price_list")
    @patch("ury.ury_pos.waiter_api.get_restaurant_and_menu_name")
    @patch("ury.ury_pos.waiter_api.frappe.get_all")
    @patch("ury.ury_pos.waiter_api.getRestaurantMenu")
    def test_authoritative_menu_uses_item_price_and_filters_disabled_items(
        self,
        get_restaurant_menu,
        get_all,
        get_table_menu,
        get_price_list,
        get_prices,
        get_price_options,
    ):
        get_restaurant_menu.return_value = {
            "name": "ROOM-MENU",
            "modified_time": "2026-08-10 12:00:00",
            "items": [
                {
                    "item": "ITEM-A",
                    "item_name": "Enabled Item",
                    "rate": 100,
                },
                {
                    "item": "ITEM-B",
                    "item_name": "Disabled Item",
                    "rate": 200,
                },
            ],
        }
        get_all.return_value = [
            frappe._dict(name="ITEM-A", disabled=0),
            frappe._dict(name="ITEM-B", disabled=1),
        ]
        get_table_menu.return_value = (
            "Branch A",
            "ROOM-MENU",
            "Restaurant A",
        )
        get_price_list.return_value = "ROOM-PRICE-LIST"
        get_prices.return_value = {"ITEM-A": 170}
        actor = actor_context()

        menu = _get_authoritative_menu(actor, "Room A", table="Table 1")

        self.assertEqual(menu.name, "ROOM-MENU")
        self.assertEqual(menu.price_list, "ROOM-PRICE-LIST")
        self.assertEqual(
            menu.get("items"),
            [
                {
                    "item": "ITEM-A",
                    "item_name": "Enabled Item",
                    "rate": 170,
                }
            ],
        )
        get_restaurant_menu.assert_called_once_with("POS A", room="Room A")
        get_table_menu.assert_called_once_with("Table 1")
        get_price_list.assert_called_once_with("ROOM-MENU")
        get_prices.assert_called_once_with(
            ["ITEM-A"], "ROOM-PRICE-LIST"
        )
        get_price_options.assert_called_once_with(
            "ROOM-MENU",
            {"ITEM-A": 170},
            {"ITEM-A": 0},
            item_codes=["ITEM-A"],
        )

    @patch("ury.ury_pos.waiter_api.get_item_price_options")
    @patch(
        "ury.ury_pos.waiter_api.get_authoritative_item_prices",
        return_value={"ITEM-A": 170},
    )
    @patch(
        "ury.ury_pos.waiter_api.get_authoritative_menu_price_list",
        return_value="ROOM-PRICE-LIST",
    )
    @patch("ury.ury_pos.waiter_api.frappe.get_all")
    @patch("ury.ury_pos.waiter_api.getRestaurantMenu")
    def test_authoritative_menu_rebuilds_options_from_item_price_and_total_stock(
        self,
        get_restaurant_menu,
        get_all,
        _get_price_list,
        _get_prices,
        get_price_options,
    ):
        get_restaurant_menu.return_value = {
            "name": "ROOM-MENU",
            "modified_time": "2026-08-12 12:00:00",
            "items": [{
                "item": "ITEM-A",
                "item_name": "Item A",
                "rate": 100,
                "available_qty": 6,
                "total_available_qty": 10,
                "price_options": [{
                    "id": "STALE-PROMO",
                    "rate": 1,
                    "available_qty": 99,
                }],
            }],
        }
        get_all.return_value = [frappe._dict(name="ITEM-A", disabled=0)]
        rebuilt = [
            {
                "id": "standard",
                "label": "Normal",
                "rate": 170,
                "available_qty": 6,
                "is_default": True,
            },
            {
                "id": "PROMO-1",
                "label": "Promoção",
                "rate": 120,
                "available_qty": 4,
                "is_default": False,
            },
        ]
        get_price_options.return_value = {"ITEM-A": rebuilt}

        menu = _get_authoritative_menu(actor_context(), "Room A")

        authoritative_item = menu.get("items")[0]
        self.assertEqual(authoritative_item["rate"], 170)
        self.assertEqual(authoritative_item["price_options"], rebuilt)
        self.assertEqual(authoritative_item["available_qty"], 6)
        self.assertEqual(authoritative_item["total_available_qty"], 10)
        get_price_options.assert_called_once_with(
            "ROOM-MENU",
            {"ITEM-A": 170},
            {"ITEM-A": 10},
            item_codes=["ITEM-A"],
        )

    @patch("ury.ury_pos.waiter_api._get_authoritative_menu")
    @patch("ury.ury_pos.waiter_api._resolve_room", return_value="Room A")
    @patch("ury.ury_pos.waiter_api._get_actor_context")
    def test_waiter_menu_uses_total_availability_when_only_promotion_remains(
        self,
        get_actor,
        _resolve_room,
        get_authoritative_menu,
    ):
        get_actor.return_value = actor_context()
        get_authoritative_menu.return_value = frappe._dict(
            name="ROOM-MENU",
            modified_time="2026-08-12 12:00:00",
            items=[{
                "item": "ITEM-A",
                "item_name": "Item A",
                "rate": 170,
                "price_options": [
                    {
                        "id": "standard",
                        "label": "Normal",
                        "rate": 170,
                        "available_qty": 0,
                        "is_default": True,
                    },
                    {
                        "id": "PROMO-1",
                        "label": "Promoção",
                        "rate": 120,
                        "available_qty": 4,
                        "is_default": False,
                    },
                ],
                "available_qty": 0,
                "total_available_qty": 4,
                "is_stock_item": True,
                "negative_stock_allowed": False,
            }],
        )

        payload = get_menu("Room A")

        self.assertTrue(payload["items"][0]["available"])
        self.assertEqual(payload["items"][0]["available_qty"], 4)
        self.assertEqual(
            payload["items"][0]["price_options"][1]["id"], "PROMO-1"
        )

    @patch(
        "ury.ury.doctype.ury_order.ury_order.group_menu_promotions"
    )
    @patch(
        "ury.ury.doctype.ury_order.ury_order._get_locked_waiter_price_list"
    )
    @patch(
        "ury.ury.doctype.ury_order.ury_order.get_authoritative_item_prices",
        return_value={"ITEM-A": 170},
    )
    @patch(
        "ury.ury.doctype.ury_order.ury_order.lock_menu_price_options_parent"
    )
    def test_waiter_rejects_promotion_rate_changed_before_menu_lock(
        self,
        _lock_menu_parent,
        _get_prices,
        _lock_price_list,
        group_promotions,
    ):
        group_promotions.return_value = {
            "ITEM-A": [
                frappe._dict(
                    name="PROMO-1",
                    item="ITEM-A",
                    label="Promoção",
                    rate=125,
                )
            ]
        }

        with patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.throw",
            side_effect=raise_frappe,
        ):
            with self.assertRaises(frappe.TimestampMismatchError):
                _get_locked_waiter_item_prices(
                    "ROOM-MENU",
                    ["ITEM-A"],
                    "ROOM-PRICE-LIST",
                    {"ITEM-A": 170},
                    items=[{
                        "item": "ITEM-A",
                        "price_option": "PROMO-1",
                        "_expected_option_rate": 120,
                    }],
                )

        group_promotions.assert_called_once_with(
            "ROOM-MENU", ["ITEM-A"], for_update=True
        )

    def test_append_only_sync_strips_internal_option_rate_before_append(self):
        source = inspect.getsource(_sync_order)
        price_check = source.index("locked_item_prices =")
        strip_token = source.index('item.pop("_expected_option_rate", None)')
        append_items = source.index("appended_items =")
        self.assertLess(price_check, strip_token)
        self.assertLess(strip_token, append_items)

    @patch(
        "ury.ury.doctype.ury_order.ury_order.group_menu_promotions",
        return_value={},
    )
    @patch(
        "ury.ury.doctype.ury_order.ury_order.get_authoritative_item_prices",
        return_value={"ITEM-NEW": 170},
    )
    @patch("ury.ury.doctype.ury_order.ury_order.frappe.db.get_value")
    def test_waiter_append_preserves_existing_line_and_uses_exact_server_price(
        self, get_value, get_prices, get_promotions
    ):
        existing = frappe._dict(
            name="ROW-OLD",
            idx=1,
            item_code="ITEM-OLD",
            item_name="Old Item",
            description="Original description",
            qty=2,
            stock_qty=2,
            uom="Nos",
            stock_uom="Nos",
            conversion_factor=1,
            warehouse="Warehouse A",
            rate=215,
            amount=430,
            net_rate=200,
            net_amount=400,
            base_rate=215,
            base_amount=430,
            base_net_rate=200,
            base_net_amount=400,
            price_list_rate=225,
            base_price_list_rate=225,
            discount_percentage=5,
            discount_amount=10,
            distributed_discount_amount=20,
            item_tax_rate='{"VAT": 16}',
            income_account="Sales - G",
            expense_account="COGS - G",
            cost_center="Old Cost Center - G",
            item_tax_template="VAT 16% - G",
            comment="old comment",
            custom_course="Starter",
            custom_future_metadata="must remain exact",
        )

        class FakeInvoice:
            def __init__(self, row):
                self.items = [row]

            def append(self, fieldname, values):
                self.assert_field(fieldname)
                row = frappe._dict(values)
                row.name = "ROW-NEW"
                row.idx = len(self.items) + 1
                self.items.append(row)
                return row

            @staticmethod
            def assert_field(fieldname):
                if fieldname != "items":
                    raise AssertionError(f"Unexpected child table: {fieldname}")

        def trusted_value(doctype, filters, fieldname):
            if doctype == "POS Profile":
                self.assertEqual((filters, fieldname), ("POS A", "cost_center"))
                return "New Cost Center - G"
            if doctype == "URY Menu Item":
                self.assertEqual(
                    filters,
                    {"item": "ITEM-NEW", "parent": "ROOM-MENU"},
                )
                self.assertEqual(fieldname, "course")
                return "Main"
            self.fail(f"Unexpected trusted lookup: {doctype}")

        get_value.side_effect = trusted_value
        invoice = FakeInvoice(existing)
        snapshot = _snapshot_existing_order_items(invoice)

        appended = _append_server_priced_order_items(
            invoice,
            [
                {
                    "item": "ITEM-NEW",
                    "item_name": "New Item",
                    "qty": 1,
                    "comment": "no onions",
                }
            ],
            "ROOM-MENU",
            "ROOM-PRICE-LIST",
            "POS A",
        )

        self.assertEqual(len(invoice.items), 2)
        self.assertIs(invoice.items[0], existing)
        self.assertEqual(existing.name, "ROW-OLD")
        self.assertEqual(existing.rate, 215)
        self.assertEqual(existing.discount_percentage, 5)
        self.assertEqual(existing.discount_amount, 10)
        self.assertEqual(existing.comment, "old comment")
        self.assertEqual(existing.item_tax_rate, '{"VAT": 16}')
        self.assertEqual(existing.base_net_rate, 200)
        self.assertEqual(existing.base_net_amount, 400)
        self.assertEqual(existing.expense_account, "COGS - G")
        self.assertEqual(existing.cost_center, "Old Cost Center - G")
        self.assertEqual(existing.custom_course, "Starter")
        self.assertEqual(existing.custom_future_metadata, "must remain exact")
        self.assertEqual(appended, [invoice.items[1]])
        self.assertEqual(invoice.items[1].rate, 170)
        self.assertEqual(invoice.items[1].price_list_rate, 170)
        self.assertEqual(invoice.items[1].custom_course, "Main")
        _assert_existing_order_items_unchanged(invoice, snapshot)
        existing.custom_future_metadata = "changed"
        with patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.throw",
            side_effect=raise_frappe,
        ):
            with self.assertRaises(frappe.ValidationError):
                _assert_existing_order_items_unchanged(invoice, snapshot)
        get_prices.assert_called_once_with(
            ["ITEM-NEW"], "ROOM-PRICE-LIST"
        )
        get_promotions.assert_called_once_with(
            "ROOM-MENU", ["ITEM-NEW"], for_update=True
        )

    def test_waiter_price_list_flag_bypasses_legacy_price_list_rewrite(self):
        doc = frappe._dict(
            selling_price_list="LEGACY-PRICE-LIST",
            restaurant="Restaurant A",
            restaurant_table="Table 1",
            order_type="Dine In",
        )
        with patch.dict(
            frappe.local.flags,
            {"ury_waiter_expected_price_list": "ROOM-PRICE-LIST"},
        ), patch(
            "ury.ury.hooks.ury_pos_invoice.frappe.db.get_value"
        ) as get_value:
            validate_price_list(doc, None)

        self.assertEqual(doc.selling_price_list, "ROOM-PRICE-LIST")
        get_value.assert_not_called()

    def test_append_only_sync_uses_exact_table_menu_and_clears_general_note(self):
        invoice = FakeSyncInvoice(name="INV-1", is_new=False)
        pending = [
            {
                "item": "ITEM-A",
                "item_name": "Item A",
                "qty": 1,
                "comment": None,
            }
        ]
        profile = frappe._dict(
            name="POS A",
            warehouse="Warehouse A",
            role_allowed_for_billing=[],
        )

        def append_item(
            invoice_arg,
            items,
            menu,
            price_list,
            pos_profile,
            authoritative_prices=None,
        ):
            self.assertIs(invoice_arg, invoice)
            self.assertEqual(items, pending)
            self.assertEqual(menu, "ROOM-MENU")
            self.assertEqual(price_list, "ROOM-PRICE-LIST")
            self.assertEqual(pos_profile, "POS A")
            self.assertEqual(authoritative_prices, {"ITEM-A": 170})
            row = invoice_arg.append(
                "items",
                {
                    "item_code": "ITEM-A",
                    "item_name": "Item A",
                    "qty": 1,
                    "conversion_factor": 1,
                    "rate": 170,
                    "price_list_rate": 170,
                },
            )
            return [row]

        with patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.get_roles",
            return_value=[],
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.get_pos_profile_for_current_branch",
            return_value=(profile, "Branch A"),
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.get_single_cashier_opening",
            return_value=frappe._dict(user="cashier@example.com"),
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.sql",
            return_value=[("Table 1",)],
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.get_value",
            return_value=0,
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.set_value"
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._get_order_invoice",
            return_value=invoice,
        ) as get_order_invoice, patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.get_doc",
            return_value=frappe._dict(mobile_number="840000000"),
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._reconcile_invoice_merged_tables"
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._get_locked_waiter_menu",
            return_value="ROOM-MENU",
        ) as get_locked_menu, patch(
            "ury.ury.doctype.ury_order.ury_order._get_locked_waiter_price_list",
            return_value="ROOM-PRICE-LIST",
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.lock_menu_price_options_parent"
        ) as lock_menu_parent, patch(
            "ury.ury.doctype.ury_order.ury_order.get_authoritative_item_prices",
            return_value={"ITEM-A": 170},
        ) as get_locked_prices, patch(
            "ury.ury.doctype.ury_order.ury_order._append_server_priced_order_items",
            side_effect=append_item,
        ) as append_server_items, patch(
            "ury.ury.doctype.ury_order.ury_order.get_menu_promotions",
            return_value=[],
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._get_stock_lock_item_codes",
            return_value=[],
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._lock_stock_bins",
            return_value={},
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._validate_order_stock"
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.set_user"
        ), patch.dict(
            frappe.local.flags,
            {"ury_waiter_expected_price_list": None},
        ):
            result = _sync_order(
                items=pending,
                cashier=None,
                owner=None,
                mode_of_payment="Cash",
                customer="Walk In",
                no_of_pax=1,
                last_invoice="INV-1",
                waiter="waiter@example.com",
                pos_profile="POS A",
                last_modified_time=invoice.modified,
                table="Table 1",
                invoice="INV-1",
                comments=None,
                order_type="Dine In",
                room="Room A",
                skip_kot=True,
                strict_invoice=True,
                expected_invoice_name="INV-1",
                append_only=True,
                expected_menu="ROOM-MENU",
                expected_price_list="ROOM-PRICE-LIST",
                expected_item_prices={"ITEM-A": 170},
            )

        self.assertEqual(result, {"name": "INV-1"})
        self.assertEqual(invoice.custom_comments, "")
        self.assertEqual(invoice.saved_waiter_price_list, "ROOM-PRICE-LIST")
        self.assertTrue(invoice.saved_ignore_permissions)
        self.assertEqual(invoice.reload_count, 1)
        self.assertEqual(invoice.items[0].rate, 170)
        get_order_invoice.assert_called_once_with(
            "Table 1",
            "INV-1",
            "Dine In",
            preserve_existing_price_list=True,
        )
        get_locked_menu.assert_called_once_with(
            "Table 1",
            "Room A",
            "ROOM-MENU",
            ["ITEM-A"],
        )
        lock_menu_parent.assert_called_once_with("ROOM-MENU")
        get_locked_prices.assert_called_once_with(
            ["ITEM-A"], "ROOM-PRICE-LIST", for_update=True
        )
        append_server_items.assert_called_once()

    def test_legacy_sync_keeps_branch_menu_first_item_price_and_comment_rule(self):
        invoice = FakeSyncInvoice(is_new=True)
        invoice.selling_price_list = "LEGACY-PRICE-LIST"
        profile = frappe._dict(
            name="POS A",
            warehouse="Warehouse A",
            role_allowed_for_billing=[],
        )

        def legacy_value(doctype, filters, fieldname):
            if doctype == "Price List":
                self.assertEqual(
                    (filters, fieldname),
                    ("LEGACY-PRICE-LIST", "restaurant_menu"),
                )
                return None
            if doctype == "URY Menu":
                self.assertEqual(filters, {"branch": "Branch A"})
                self.assertEqual(fieldname, "name")
                return "BRANCH-MENU"
            if doctype == "URY Menu Item":
                self.assertEqual(
                    filters,
                    {"item": "ITEM-A", "parent": "BRANCH-MENU"},
                )
                self.assertEqual(fieldname, "course")
                return "Legacy Course"
            if doctype == "POS Profile":
                self.assertEqual((filters, fieldname), ("POS A", "cost_center"))
                return "Legacy Cost Center - G"
            self.fail(f"Unexpected legacy lookup: {doctype}")

        with patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.get_roles",
            return_value=[],
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.get_pos_profile_for_current_branch",
            return_value=(profile, "Branch A"),
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.get_single_cashier_opening",
            return_value=frappe._dict(user="cashier@example.com"),
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.sql",
            return_value=[("Table 1",)],
        ), patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.get_value",
            side_effect=legacy_value,
        ) as get_value, patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.get_list",
            return_value=[
                frappe._dict(price_list_rate=111),
                frappe._dict(price_list_rate=222),
            ],
        ) as get_item_prices, patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.db.set_value"
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._get_order_invoice",
            return_value=invoice,
        ) as get_order_invoice, patch(
            "ury.ury.doctype.ury_order.ury_order.frappe.get_doc",
            return_value=frappe._dict(mobile_number="840000000"),
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._reconcile_invoice_merged_tables"
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._get_stock_lock_item_codes",
            return_value=[],
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._lock_stock_bins",
            return_value={},
        ), patch(
            "ury.ury.doctype.ury_order.ury_order._validate_order_stock"
        ), patch.dict(
            frappe.local.flags,
            {"ury_waiter_expected_price_list": None},
        ):
            result = _sync_order(
                items=[
                    {
                        "item": "ITEM-A",
                        "item_name": "Item A",
                        "qty": 1,
                        "comment": "legacy item note",
                    }
                ],
                cashier="cashier@example.com",
                owner="cashier@example.com",
                mode_of_payment="Cash",
                customer="Walk In",
                no_of_pax=1,
                last_invoice=None,
                waiter="waiter@example.com",
                pos_profile="POS A",
                table="Table 1",
                invoice=None,
                comments=None,
                order_type="Dine In",
                room="Room A",
                skip_kot=True,
                append_only=False,
            )

        self.assertEqual(result, {"name": "INV-NEW"})
        self.assertEqual(invoice.custom_comments, "existing general note")
        self.assertIsNone(invoice.saved_waiter_price_list)
        self.assertIsNone(invoice.saved_ignore_permissions)
        self.assertEqual(invoice.reload_count, 0)
        self.assertEqual(len(invoice.items), 1)
        self.assertEqual(invoice.items[0].rate, 111)
        self.assertEqual(invoice.items[0].price_list_rate, 111)
        self.assertEqual(invoice.items[0].custom_course, "Legacy Course")
        get_value.assert_any_call(
            "URY Menu", {"branch": "Branch A"}, "name"
        )
        get_item_prices.assert_called_once_with(
            "Item Price",
            filters={
                "item_code": "ITEM-A",
                "price_list": "LEGACY-PRICE-LIST",
            },
            fields=["price_list_rate"],
        )
        get_order_invoice.assert_called_once_with(
            "Table 1",
            None,
            "Dine In",
            preserve_existing_price_list=False,
        )

    def test_order_lock_sequence_is_opening_then_table_then_stock(self):
        source = inspect.getsource(_sync_order)
        self.assertLess(
            source.index("get_single_cashier_opening"),
            source.index("FROM `tabURY Table`"),
        )
        self.assertLess(
            source.index("FROM `tabURY Table`"),
            source.index("get_order_invoice"),
        )
        self.assertLess(
            source.index("_get_locked_waiter_menu("),
            source.index("get_order_invoice"),
        )
        self.assertLess(
            source.index("_get_locked_waiter_menu("),
            source.index("_append_server_priced_order_items("),
        )
        self.assertLess(
            source.index("get_order_invoice"),
            source.index("_get_locked_waiter_item_prices("),
        )
        self.assertLess(
            source.index("_get_locked_waiter_menu("),
            source.index("invoice.save(ignore_permissions=True)"),
        )
        self.assertLess(
            source.index("get_order_invoice"),
            source.index("_lock_stock_bins"),
        )
        self.assertLess(
            source.index("lock_menu_price_options_parent(promotion_menu)"),
            source.index("locked_promotion_base_rates ="),
        )
        self.assertIn(
            "price_validation_item_codes, price_list, for_update=True",
            source,
        )
        self.assertIn("for_update=True,\n        )", source)

        register_source = inspect.getsource(register_order)
        self.assertLess(
            register_source.index("_get_opening("),
            register_source.index("_get_locked_waiter_menu("),
        )
        self.assertLess(
            register_source.index("_get_locked_waiter_menu("),
            register_source.index("_active_table_invoices("),
        )

    @patch("ury.ury_pos.waiter_api._complete_waiter_request")
    @patch("ury.ury_pos.waiter_api._format_order", return_value={"invoice": "INV-1"})
    @patch("ury.ury_pos.waiter_api._create_waiter_kots")
    @patch("ury.ury_pos.waiter_api._sync_order")
    @patch("ury.ury_pos.waiter_api.frappe.get_doc")
    @patch("ury.ury_pos.waiter_api._active_table_invoices")
    @patch("ury.ury_pos.waiter_api._get_table")
    @patch("ury.ury_pos.waiter_api._get_locked_waiter_menu")
    @patch("ury.ury_pos.waiter_api._get_opening")
    @patch("ury.ury_pos.waiter_api._validate_production_routes")
    @patch("ury.ury_pos.waiter_api._hydrate_pending_items")
    @patch("ury.ury_pos.waiter_api._get_authoritative_menu")
    @patch("ury.ury_pos.waiter_api._reserve_waiter_request")
    @patch("ury.ury_pos.waiter_api._get_actor_context")
    def test_register_derives_trusted_fields_and_kot_uses_only_pending_delta(
        self,
        get_actor,
        reserve_request,
        get_authoritative_menu,
        hydrate_items,
        validate_routes,
        get_opening,
        get_locked_menu,
        get_table,
        active_invoices,
        get_doc,
        sync_service,
        create_kot,
        _format_order,
        complete_request,
    ):
        actor = actor_context()
        get_actor.return_value = actor
        request_doc = Mock()
        reserve_request.return_value = (request_doc, None)
        menu = frappe._dict(
            name="ROOM-MENU",
            price_list="ROOM-PRICE-LIST",
            items=[],
        )
        get_authoritative_menu.return_value = menu
        pending = [
            {
                "item": "ITEM-A",
                "item_name": "Server Item Name",
                "qty": 2,
                "comment": "sem gelo",
                "_expected_option_rate": 170,
                "_authoritative_base_rate": 170,
            }
        ]
        hydrate_items.return_value = pending
        validate_routes.return_value = {"ITEM-A": "Bar"}
        get_opening.return_value = frappe._dict(user="cashier@example.com")
        get_table.return_value = frappe._dict(
            name="Table 1", occupied=1, merged_with=None
        )
        modified = datetime(2026, 8, 10, 12, 0, 0)
        active_invoices.return_value = [
            frappe._dict(
                name="INV-1",
                waiter=actor.user,
                modified=modified,
                invoice_printed=0,
                restaurant_table="Table 1",
                custom_merged_tables=None,
                custom_merged_pos_invoice=None,
                custom_split_from=None,
                custom_split_group=None,
                pos_profile=actor.profile.name,
                branch=actor.branch,
                custom_restaurant_room="Room A",
            )
        ]
        existing_item = frappe._dict(
            item_code="ITEM-OLD",
            item_name="Old Item",
            qty=1,
            comment="old comment",
        )
        invoice = frappe._dict(
            name="INV-1",
            customer="Walk In",
            items=[existing_item],
            modified=datetime(2026, 8, 10, 12, 0, 1),
        )
        get_doc.return_value = invoice
        sync_service.return_value = {"name": "INV-1"}
        create_kot.return_value = {
            "created_kots": [{"name": "KOT-1", "production": "Bar"}],
            "unrouted_items": [],
        }

        result = register_order(
            table="Table 1",
            room="Room A",
            items=[
                {
                    "item": "ITEM-A",
                    "item_name": "Spoofed Name",
                    "rate": 1,
                    "qty": 2,
                    "expected_rate": 170,
                    "comment": "sem gelo",
                }
            ],
            expected_modified=str(modified),
            request_id="request-0001",
        )

        save_kwargs = sync_service.call_args.kwargs
        self.assertIsNone(save_kwargs["cashier"])
        self.assertIsNone(save_kwargs["owner"])
        self.assertEqual(save_kwargs["waiter"], "waiter@example.com")
        self.assertEqual(save_kwargs["pos_profile"], "POS A")
        self.assertEqual(save_kwargs["customer"], "Walk In")
        self.assertEqual(save_kwargs["mode_of_payment"], "Cash")
        self.assertTrue(save_kwargs["skip_kot"])
        self.assertTrue(save_kwargs["strict_invoice"])
        self.assertTrue(save_kwargs["append_only"])
        self.assertEqual(save_kwargs["expected_invoice_name"], "INV-1")
        self.assertEqual(save_kwargs["expected_menu"], "ROOM-MENU")
        self.assertEqual(save_kwargs["expected_price_list"], "ROOM-PRICE-LIST")
        self.assertEqual(save_kwargs["expected_item_prices"], {"ITEM-A": 170})
        clean_pending = [
            {
                "item": "ITEM-A",
                "item_name": "Server Item Name",
                "qty": 2,
                "comment": "sem gelo",
                "_expected_option_rate": 170,
            }
        ]
        self.assertEqual(save_kwargs["items"], clean_pending)
        self.assertNotEqual(save_kwargs["items"][0]["item_name"], "Spoofed Name")
        self.assertNotIn("_authoritative_base_rate", save_kwargs["items"][0])
        get_authoritative_menu.assert_called_once_with(
            actor, "Room A", table="Table 1"
        )
        get_locked_menu.assert_called_once_with(
            "Table 1", "Room A", "ROOM-MENU", ["ITEM-A"]
        )
        hydrate_items.assert_called_once_with(
            menu,
            [
                {
                    "item": "ITEM-A",
                    "qty": 2,
                    "comment": "sem gelo",
                    "expected_rate": Decimal("170"),
                }
            ],
        )

        create_kot.assert_called_once_with(
            "INV-1",
            "Walk In",
            "Table 1",
            [{
                "item": "ITEM-A",
                "item_name": "Server Item Name",
                "qty": 2,
                "comment": "sem gelo",
            }],
            None,
        )
        self.assertEqual(
            result["kots"], [{"name": "KOT-1", "production": "Bar"}]
        )
        self.assertNotIn("cashier", result)
        self.assertNotIn("owner", result)
        complete_request.assert_called_once()

    @patch("ury.ury_pos.waiter_api._complete_waiter_request")
    @patch("ury.ury_pos.waiter_api._format_order")
    @patch(
        "ury.ury_pos.waiter_api._create_waiter_kots",
        side_effect=RuntimeError("KOT failed"),
    )
    @patch("ury.ury_pos.waiter_api._sync_order", return_value={"name": "INV-1"})
    @patch("ury.ury_pos.waiter_api.frappe.get_doc")
    @patch("ury.ury_pos.waiter_api._active_table_invoices", return_value=[])
    @patch("ury.ury_pos.waiter_api._get_table")
    @patch("ury.ury_pos.waiter_api._get_locked_waiter_menu")
    @patch("ury.ury_pos.waiter_api._get_opening")
    @patch("ury.ury_pos.waiter_api._validate_production_routes")
    @patch("ury.ury_pos.waiter_api._hydrate_pending_items")
    @patch("ury.ury_pos.waiter_api._get_authoritative_menu")
    @patch("ury.ury_pos.waiter_api._reserve_waiter_request")
    @patch("ury.ury_pos.waiter_api._get_actor_context")
    def test_kot_failure_is_not_reported_as_success(
        self,
        get_actor,
        reserve_request,
        get_authoritative_menu,
        hydrate_items,
        validate_routes,
        get_opening,
        _get_locked_menu,
        get_table,
        _active_invoices,
        get_doc,
        _sync_service,
        _create_kot,
        _format_order,
        complete_request,
    ):
        actor = actor_context()
        get_actor.return_value = actor
        reserve_request.return_value = (Mock(), None)
        get_authoritative_menu.return_value = frappe._dict(
            name="ROOM-MENU",
            price_list="ROOM-PRICE-LIST",
            items=[],
        )
        pending = [
            {
                "item": "ITEM-A",
                "item_name": "Item A",
                "qty": 1,
                "comment": None,
                "_expected_option_rate": 170,
                "_authoritative_base_rate": 170,
            }
        ]
        hydrate_items.return_value = pending
        validate_routes.return_value = {"ITEM-A": "Kitchen"}
        get_opening.return_value = frappe._dict(user="cashier@example.com")
        get_table.return_value = frappe._dict(
            name="Table 1", occupied=0, merged_with=None
        )
        get_doc.return_value = frappe._dict(
            name="INV-1",
            customer="Walk In",
            modified=datetime(2026, 8, 10, 12, 0, 0),
        )

        with self.assertRaisesRegex(RuntimeError, "KOT failed"):
            register_order(
                table="Table 1",
                room="Room A",
                items=[{"item": "ITEM-A", "qty": 1, "expected_rate": 170}],
                request_id="request-0002",
            )
        complete_request.assert_not_called()


class TestWaiterKOTMetadata(TestCase):
    @patch("ury.ury.doctype.ury_kot.ury_kot.frappe.publish_realtime")
    @patch("ury.ury.doctype.ury_kot.ury_kot.frappe.cache")
    @patch("ury.ury.doctype.ury_kot.ury_kot.frappe.db.get_value", return_value=None)
    @patch("ury.ury.doctype.ury_kot.ury_kot.frappe.db.after_commit")
    def test_kds_visibility_and_cache_update_wait_for_commit(
        self,
        after_commit,
        _get_value,
        get_cache,
        publish_realtime,
    ):
        cache = Mock()
        cache.get_value.return_value = "previous-time"
        get_cache.return_value = cache
        callbacks = []
        after_commit.add.side_effect = callbacks.append
        kot = frappe._dict(
            branch="Branch A",
            production=None,
            pos_profile="POS A",
            time="12:34:56",
            name="KOT-1",
        )

        URYKOT.kotDisplayRealtime(kot)

        publish_realtime.assert_called_once()
        self.assertTrue(publish_realtime.call_args.kwargs["after_commit"])
        cache.set_value.assert_not_called()
        self.assertEqual(len(callbacks), 1)

        callbacks[0]()

        cache.set_value.assert_called_once_with(
            "Branch A_None_last_kot_time", "12:34:56"
        )

    @patch("ury.ury.api.ury_kot_generate.process_items_for_kot")
    @patch("ury.ury.api.ury_kot_generate.frappe.get_doc")
    def test_kot_execute_returns_created_ticket_and_unit_names(
        self, get_doc, process_items
    ):
        get_doc.side_effect = [
            frappe._dict(pos_profile="POS A"),
            frappe._dict(name="POS A", custom_kot_naming_series="KOT-.#####"),
        ]
        process_items.return_value = {
            "created_kots": [{"name": "KOT-1", "production": "Kitchen"}],
            "unrouted_items": [],
        }

        result = kot_execute(
            "INV-1",
            "Walk In",
            "Table 1",
            [
                {
                    "item": "ITEM-A",
                    "item_name": "Item A",
                    "qty": 1,
                    "comment": "well done",
                }
            ],
            [],
        )

        self.assertEqual(
            result,
            {
                "created_kots": [
                    {"name": "KOT-1", "production": "Kitchen"}
                ],
                "unrouted_items": [],
            },
        )


class TestWaiterDedicatedRoleSecurity(TestCase):
    def test_all_five_waiter_endpoints_are_guest_dispatchable_then_authorise(self):
        endpoints = {
            "get_context": (get_context, "GET"),
            "get_tables": (get_tables, "GET"),
            "get_menu": (get_menu, "GET"),
            "get_table_order": (get_table_order, "GET"),
            "register_order": (register_order, "POST"),
        }
        module = inspect.getmodule(get_context)
        tree = ast.parse(inspect.getsource(module))
        definitions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in endpoints
        }
        self.assertEqual(set(definitions), set(endpoints))

        for name, (endpoint, http_method) in endpoints.items():
            with self.subTest(endpoint=name):
                definition = definitions[name]
                whitelist = next(
                    decorator
                    for decorator in definition.decorator_list
                    if isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "whitelist"
                )
                keywords = {keyword.arg: keyword.value for keyword in whitelist.keywords}
                self.assertIsInstance(keywords.get("allow_guest"), ast.Constant)
                self.assertIs(keywords["allow_guest"].value, True)
                self.assertIn(endpoint, frappe.guest_methods)
                self.assertEqual(
                    frappe.allowed_http_methods_for_whitelisted_func[endpoint],
                    [http_method],
                )

                body = definition.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body = body[1:]
                first_call = body[0].value
                self.assertIsInstance(first_call, ast.Call)
                self.assertIsInstance(first_call.func, ast.Name)
                self.assertEqual(first_call.func.id, "_get_actor_context")

        waiter_rpcs = {
            command
            for command in ALLOWED_WAITER_METHODS
            if command.startswith("ury.ury_pos.waiter_api.")
        }
        self.assertEqual(
            waiter_rpcs,
            {f"ury.ury_pos.waiter_api.{name}" for name in endpoints},
        )

    def test_guest_is_rejected_with_401_before_any_waiter_data_or_action(self):
        invocations = (
            (get_context, ()),
            (get_tables, ()),
            (get_menu, ("Room A",)),
            (get_table_order, ("Table 1",)),
            (register_order, ("Table 1", "Room A", [])),
        )
        with patch.dict(
            frappe.local.session,
            {"user": "Guest"},
        ), patch(
            "ury.ury_pos.waiter_api.is_dedicated_waiter_user"
        ) as role_check, patch(
            "ury.ury_pos.waiter_api._get_user_assignment"
        ) as assignment, patch(
            "ury.ury_pos.waiter_api._reserve_waiter_request"
        ) as reserve:
            for endpoint, args in invocations:
                with self.subTest(endpoint=endpoint.__name__):
                    with self.assertRaises(frappe.AuthenticationError) as raised:
                        endpoint(*args)
                    self.assertEqual(raised.exception.http_status_code, 401)

        role_check.assert_not_called()
        assignment.assert_not_called()
        reserve.assert_not_called()

    def test_only_dedicated_role_can_enter_waiter_rpcs(self):
        with patch.dict(
            frappe.local.session,
            {"user": "elisio@example.com"},
        ), patch(
            "ury.ury_pos.waiter_api.frappe.throw",
            side_effect=raise_frappe,
        ), patch(
            "ury.ury_pos.waiter_api.is_dedicated_waiter_user",
            return_value=False,
        ):
            with self.assertRaises(frappe.PermissionError):
                _require_waiter_user()

        with patch.dict(
            frappe.local.session,
            {"user": "elisio@example.com"},
        ), patch(
            "ury.ury_pos.waiter_api.is_dedicated_waiter_user",
            return_value=True,
        ):
            self.assertEqual(_require_waiter_user(), "elisio@example.com")

        for endpoint in (
            get_context,
            get_tables,
            get_menu,
            get_table_order,
            register_order,
        ):
            self.assertIn("_get_actor_context", inspect.getsource(endpoint))

    def test_auth_hook_allows_only_waiter_and_session_methods(self):
        self.assertIn(
            "ury.ury_pos.waiter_security.restrict_waiter_requests",
            ury_hooks.auth_hooks,
        )
        self.assertNotIn("before_request", ury_hooks.__dict__)
        self.assertIn("frappe.auth.get_logged_user", ALLOWED_WAITER_METHODS)
        self.assertIn("login", ALLOWED_WAITER_METHODS)
        self.assertIn("logout", ALLOWED_WAITER_METHODS)
        self.assertIn("web_logout", ALLOWED_WAITER_METHODS)
        self.assertTrue(
            _is_allowed_waiter_request("/", "web_logout")
        )

        for method in (
            "get_context",
            "get_tables",
            "get_menu",
            "get_table_order",
            "register_order",
        ):
            command = f"ury.ury_pos.waiter_api.{method}"
            self.assertTrue(
                _is_allowed_waiter_request(
                    f"/api/method/{command}", command
                )
            )

        self.assertFalse(
            _is_allowed_waiter_request(
                "/api/method/ury.ury_pos.api.cancel_order",
                "ury.ury_pos.api.cancel_order",
            )
        )
        self.assertFalse(
            _is_allowed_waiter_request(
                "/", "frappe.client.set_value"
            )
        )
        self.assertFalse(
            _is_allowed_waiter_request("/api/resource/POS Invoice", None)
        )
        self.assertFalse(_is_allowed_waiter_request("/app", None))
        self.assertFalse(
            _is_allowed_waiter_request("/private/files/secret.pdf", None)
        )
        for path in ("/waiter", "/assets/ury/waiter.js", "/about"):
            self.assertTrue(_is_allowed_waiter_request(path, None))

        # auth_hooks execute after both cookie session resumption and
        # Bearer/API-key authentication in Frappe's validate_auth flow.
        guard_source = inspect.getsource(restrict_waiter_requests)
        self.assertIn("is_dedicated_waiter_user(user)", guard_source)

    def test_dedicated_waiter_identity_requires_an_explicit_role_assignment(self):
        with patch(
            "ury.ury_pos.waiter_security.frappe.get_roles",
            return_value=[WAITER_ROLE, "System Manager"],
        ), patch(
            "ury.ury_pos.waiter_security.frappe.db.exists",
            return_value=True,
        ) as exists:
            self.assertFalse(is_dedicated_waiter_user("Administrator"))
            self.assertFalse(is_dedicated_waiter_user("manager@example.com"))
            exists.assert_not_called()

        with patch(
            "ury.ury_pos.waiter_security.frappe.get_roles",
            return_value=[WAITER_ROLE],
        ), patch(
            "ury.ury_pos.waiter_security.frappe.db.exists",
            return_value=True,
        ) as exists:
            self.assertTrue(is_dedicated_waiter_user("elisio@example.com"))
            exists.assert_called_once_with(
                "Has Role",
                {
                    "parent": "elisio@example.com",
                    "parenttype": "User",
                    "role": WAITER_ROLE,
                },
            )

    def test_auth_hook_never_restricts_administrators_with_implicit_waiter_role(self):
        previous_request = getattr(frappe.local, "request", None)
        frappe.local.request = frappe._dict(path="/app")
        try:
            with patch.dict(
                frappe.local.session,
                {"user": "Administrator"},
            ), patch(
                "ury.ury_pos.waiter_security.frappe.get_roles",
                return_value=[WAITER_ROLE, "System Manager"],
            ), patch(
                "ury.ury_pos.waiter_security.frappe.db.exists",
                return_value=True,
            ), patch(
                "ury.ury_pos.waiter_security.frappe.throw",
                side_effect=raise_frappe,
            ) as throw:
                restrict_waiter_requests()
                throw.assert_not_called()

            with patch.dict(
                frappe.local.session,
                {"user": "manager@example.com"},
            ), patch(
                "ury.ury_pos.waiter_security.frappe.get_roles",
                return_value=[WAITER_ROLE, "System Manager"],
            ), patch(
                "ury.ury_pos.waiter_security.frappe.db.exists",
                return_value=True,
            ), patch(
                "ury.ury_pos.waiter_security.frappe.throw",
                side_effect=raise_frappe,
            ) as throw:
                restrict_waiter_requests()
                throw.assert_not_called()
        finally:
            frappe.local.request = previous_request

    def test_auth_hook_still_blocks_dedicated_waiter_from_desk(self):
        previous_request = getattr(frappe.local, "request", None)
        frappe.local.request = frappe._dict(path="/app")
        try:
            with patch.dict(
                frappe.local.session,
                {"user": "elisio@example.com"},
            ), patch(
                "ury.ury_pos.waiter_security.frappe.get_roles",
                return_value=[WAITER_ROLE],
            ), patch(
                "ury.ury_pos.waiter_security.frappe.db.exists",
                return_value=True,
            ), patch(
                "ury.ury_pos.waiter_security.frappe.throw",
                side_effect=raise_frappe,
            ):
                with self.assertRaises(frappe.PermissionError):
                    restrict_waiter_requests()
        finally:
            frappe.local.request = previous_request

    def test_setup_denies_direct_pos_document_mutations(self):
        self.assertEqual(
            set(FORBIDDEN_DOCTYPES),
            {
                "POS Invoice",
                "URY KOT",
                "POS Opening Entry",
                "POS Closing Entry",
            },
        )
        self.assertEqual(
            set(FORBIDDEN_ACTIONS),
            {"write", "create", "submit", "cancel"},
        )

    @patch("ury.ury_pos.waiter_api._kot_execute")
    def test_waiter_kot_bypass_is_private_and_public_rpc_has_no_flag(
        self, private_kot
    ):
        private_kot.return_value = {
            "created_kots": [{"name": "KOT-1", "production": "Bar"}],
            "unrouted_items": [],
        }
        result = _create_waiter_kots(
            "INV-1",
            "Walk In",
            "Table 1",
            [{"item": "ITEM-A", "qty": 1}],
            None,
        )
        self.assertEqual(result["created_kots"][0]["name"], "KOT-1")
        self.assertNotIn(
            "ignore_permissions", inspect.signature(kot_execute).parameters
        )
        self.assertIn(
            "ignore_permissions", inspect.signature(_kot_execute).parameters
        )
        private_kot.assert_called_once_with(
            "INV-1",
            "Walk In",
            "Table 1",
            [{"item": "ITEM-A", "qty": 1}],
            [],
            None,
            ignore_permissions=True,
        )

    def test_processing_idempotency_uses_425_not_deterministic_409(self):
        row = frappe._dict(
            payload_hash="same-hash",
            status="Processing",
            response_json=None,
        )
        with patch(
            "ury.ury_pos.waiter_api.frappe.throw",
            side_effect=raise_frappe,
        ):
            with self.assertRaises(WaiterRequestInProgressError):
                _replay_waiter_request(row, "same-hash")
        self.assertEqual(WaiterRequestInProgressError.http_status_code, 425)
        self.assertEqual(WaiterConflictError.http_status_code, 409)


class TestWaiterAssignedProfileSecurity(TestCase):
    @patch("ury.ury_pos.waiter_api.frappe.get_doc")
    @patch("ury.ury_pos.waiter_api.frappe.get_all")
    def test_profile_and_opening_are_intersected_with_user_assignment(
        self, get_all, get_doc
    ):
        def rows(doctype, **kwargs):
            if doctype == "POS Profile User":
                self.assertEqual(
                    kwargs["filters"]["user"], "elisio@example.com"
                )
                return ["POS A"]
            if doctype == "POS Profile":
                self.assertEqual(
                    kwargs["filters"]["name"], ["in", ["POS A"]]
                )
                return [frappe._dict(name="POS A")]
            if doctype == "POS Opening Entry":
                self.assertEqual(
                    kwargs["filters"]["pos_profile"], ["in", ["POS A"]]
                )
                return [frappe._dict(pos_profile="POS A")]
            self.fail(f"Unexpected DocType lookup: {doctype}")

        get_all.side_effect = rows
        profile = frappe._dict(name="POS A", warehouse="Warehouse A")
        get_doc.return_value = profile

        self.assertIs(
            _get_exact_pos_profile("Branch A", "elisio@example.com"),
            profile,
        )

    def test_missing_profile_assignment_fails_before_opening_lookup(self):
        with patch(
            "ury.ury_pos.waiter_api.frappe.get_all", return_value=[]
        ) as get_all, patch(
            "ury.ury_pos.waiter_api.frappe.throw",
            side_effect=raise_frappe,
        ):
            with self.assertRaises(frappe.PermissionError):
                _get_exact_pos_profile("Branch A", "elisio@example.com")
        get_all.assert_called_once()


def run_unit_tests():
    """Run the mock-only waiter backend regression suite via bench execute."""
    # ``bench execute`` loads the site but does not construct an HTTP request,
    # so these LocalProxy values are otherwise None. The production code always
    # receives them from a real request; the suite supplies the smallest test
    # context needed by mocks that patch flags/session fields.
    if getattr(frappe.local, "flags", None) is None:
        frappe.local.flags = frappe._dict()
    if getattr(frappe.local, "session", None) is None:
        frappe.local.session = frappe._dict(user="Administrator")
    stream = io.StringIO()
    suite = defaultTestLoader.loadTestsFromModule(
        __import__(__name__, fromlist=["*"])
    )
    result = TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError(stream.getvalue())
    return {"tests_run": result.testsRun, "successful": True}
