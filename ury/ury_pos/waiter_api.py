"""Restricted mobile order-taking API for restaurant waiters.

The endpoints in this module deliberately expose only the subset required by
the ``/waiter`` UI.  Till ownership, customer, payment mode, prices, branch,
rooms and POS Profile are always resolved on the server.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime

from ury.ury.api.ury_kot_generate import _kot_execute
from ury.ury.doctype.ury_order.ury_order import (
    _sync_order,
    get_authoritative_item_prices,
    get_authoritative_menu_price_list,
    get_restaurant_and_menu_name,
)
from ury.ury_pos.api import getRestaurantMenu
from ury.ury_pos.cashier import POSOpeningError, get_single_cashier_opening
from ury.ury_pos.waiter_security import is_dedicated_waiter_user


IDEMPOTENCY_DOCTYPE = "URY Waiter Request"
MAX_PENDING_LINES = 100
MAX_ITEM_QTY = 999
MAX_REQUEST_ID_LENGTH = 128
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class WaiterConflictError(frappe.ValidationError):
    http_status_code = 409


class WaiterRequestInProgressError(frappe.ValidationError):
    http_status_code = 425


def _conflict(message, title=None):
    frappe.throw(
        message,
        WaiterConflictError,
        title=title or _("Order Conflict"),
    )


def _require_waiter_user():
    user = getattr(frappe.session, "user", None)
    if not user or user == "Guest":
        raise frappe.AuthenticationError

    if not is_dedicated_waiter_user(user):
        frappe.throw(
            _("Only an authorised waiter can use this page."),
            frappe.PermissionError,
        )
    return user


def _get_user_assignment(user):
    rows = frappe.db.sql(
        """
        SELECT branch.name AS branch, ury_user.room AS room
        FROM `tabURY User` ury_user
        INNER JOIN `tabBranch` branch ON branch.name = ury_user.parent
        WHERE ury_user.parenttype = 'Branch'
          AND ury_user.user = %s
        ORDER BY branch.name, ury_user.idx
        """,
        user,
        as_dict=True,
    )
    if not rows:
        frappe.throw(
            _("Your user is not assigned to a URY branch."),
            frappe.PermissionError,
        )

    branches = list(dict.fromkeys(row.branch for row in rows if row.branch))
    if len(branches) != 1:
        frappe.throw(
            _("The waiter page requires one unambiguous branch assignment."),
            frappe.PermissionError,
        )

    branch = branches[0]
    rooms = list(
        dict.fromkeys(row.room for row in rows if row.branch == branch and row.room)
    )
    if not rooms:
        frappe.throw(
            _("No room is assigned to your URY user."),
            frappe.PermissionError,
        )

    valid_rooms = set(
        frappe.get_all(
            "URY Room",
            filters={"name": ["in", rooms], "branch": branch},
            pluck="name",
        )
    )
    if valid_rooms != set(rooms):
        frappe.throw(
            _("One or more assigned rooms do not belong to your branch."),
            frappe.PermissionError,
        )
    return branch, rooms


def _get_exact_pos_profile(branch, user):
    assigned_profiles = list(
        dict.fromkeys(
            frappe.get_all(
                "POS Profile User",
                filters={
                    "parenttype": "POS Profile",
                    "user": user,
                },
                pluck="parent",
                order_by="parent",
            )
        )
    )
    if not assigned_profiles:
        frappe.throw(
            _("Your user is not assigned to a POS Profile."),
            frappe.PermissionError,
        )

    profiles = frappe.get_all(
        "POS Profile",
        filters={
            "name": ["in", assigned_profiles],
            "branch": branch,
            "disabled": 0,
        },
        fields=["name"],
        order_by="name",
    )
    if not profiles:
        frappe.throw(
            _(
                "No active POS Profile assigned to your user is configured "
                "for branch {0}."
            ).format(frappe.bold(branch)),
            frappe.PermissionError,
        )
    profile_names = [profile.name for profile in profiles]
    openings = frappe.get_all(
        "POS Opening Entry",
        filters={
            "branch": branch,
            "pos_profile": ["in", profile_names],
            "status": "Open",
            "docstatus": 1,
        },
        fields=["pos_profile"],
        order_by="period_start_date desc, creation desc",
        limit=2,
    )
    if len(openings) > 1:
        frappe.throw(
            _("More than one submitted POS opening exists for this branch."),
            title=_("Ambiguous POS Opening"),
        )

    if openings:
        profile_name = openings[0].pos_profile
    elif len(profile_names) == 1:
        # Keep context available so the UI can present the closed-till state.
        profile_name = profile_names[0]
    else:
        frappe.throw(
            _(
                "More than one assigned active POS Profile is configured and "
                "none has an open till."
            ),
            title=_("Ambiguous POS Profile"),
        )

    profile = frappe.get_doc("POS Profile", profile_name)
    if not profile.warehouse:
        frappe.throw(
            _("POS Profile {0} has no warehouse.").format(
                frappe.bold(profile.name)
            )
        )
    return profile


def _get_actor_context(require_supported=True):
    user = _require_waiter_user()
    branch, rooms = _get_user_assignment(user)
    profile = _get_exact_pos_profile(branch, user)
    multiple_cashier = bool(cint(profile.custom_enable_multiple_cashier))
    if require_supported and multiple_cashier:
        frappe.throw(
            _(
                "The waiter page is not enabled for POS Profiles with multiple "
                "cashiers. Configure a safe room-specific flow first."
            )
        )
    return frappe._dict(
        user=user,
        branch=branch,
        rooms=rooms,
        profile=profile,
        multiple_cashier=multiple_cashier,
    )


def _resolve_room(actor, room=None):
    room = str(room or "").strip()
    if not room and len(actor.rooms) == 1:
        room = actor.rooms[0]
    if not room or room not in actor.rooms:
        frappe.throw(
            _("You are not assigned to this room."),
            frappe.PermissionError,
        )
    return room


def _get_opening(profile, required=False, for_update=False):
    if cint(profile.custom_enable_multiple_cashier):
        if required:
            frappe.throw(
                _("Multiple-cashier mode is not supported by the waiter page.")
            )
        return None
    return get_single_cashier_opening(
        profile.name,
        required=required,
        for_update=for_update,
    )


def _opening_state(actor):
    if actor.multiple_cashier:
        return {
            "is_open": False,
            "reason_code": "multiple_cashier_unsupported",
            "reason": _("This POS Profile is not yet supported by the waiter page."),
        }
    try:
        opening = _get_opening(actor.profile, required=False, for_update=False)
    except POSOpeningError:
        return {
            "is_open": False,
            "reason_code": "opening_configuration_error",
            "reason": _("The open till configuration requires attention."),
        }
    if not opening:
        return {
            "is_open": False,
            "reason_code": "till_closed",
            "reason": _("The till is closed. Contact the responsible person."),
        }
    return {"is_open": True, "reason_code": None, "reason": None}


def _table_sql(lock=False):
    return f"""
        SELECT name, occupied, latest_invoice_time, restaurant_room, branch,
               restaurant, table_shape, no_of_seats, minimum_seating,
               layout_x, layout_y, merged_with, is_take_away
        FROM `tabURY Table`
        WHERE name = %(table)s
          AND branch = %(branch)s
          AND restaurant_room = %(room)s
          AND COALESCE(is_take_away, 0) = 0
        {"FOR UPDATE" if lock else ""}
    """


def _get_table(actor, table, room, lock=False):
    table = str(table or "").strip()
    if not table or len(table) > 140:
        frappe.throw(_("Please select a valid table."))
    rows = frappe.db.sql(
        _table_sql(lock=lock),
        {"table": table, "branch": actor.branch, "room": room},
        as_dict=True,
    )
    if not rows:
        frappe.throw(
            _("This table is not available in your assigned room."),
            frappe.PermissionError,
        )
    return rows[0]


def _active_table_invoices(branch, table, lock=False):
    return frappe.db.sql(
        f"""
        SELECT name, waiter, modified, invoice_printed, restaurant_table,
               custom_merged_tables, custom_merged_pos_invoice, pos_profile,
               branch, custom_restaurant_room, no_of_pax, custom_comments,
               customer, grand_total, custom_split_from, custom_split_group
        FROM `tabPOS Invoice`
        WHERE branch = %(branch)s
          AND docstatus = 0
          AND (
              restaurant_table = %(table)s
              OR FIND_IN_SET(
                  REPLACE(%(table)s, ', ', ','),
                  REPLACE(COALESCE(custom_merged_tables, ''), ', ', ',')
              ) > 0
          )
        ORDER BY creation, name
        {"FOR UPDATE" if lock else ""}
        """,
        {"branch": branch, "table": table},
        as_dict=True,
    )


def _merged_table_names(value):
    return {
        table.strip()
        for table in str(value or "").split(",")
        if table.strip()
    }


def _validate_invoice_scope(actor, table, room, invoice):
    if invoice.branch != actor.branch or invoice.pos_profile != actor.profile.name:
        frappe.throw(
            _("This order does not belong to your POS Profile."),
            frappe.PermissionError,
        )
    if invoice.restaurant_table != table:
        _conflict(_("Merged tables cannot be edited from the waiter page."))
    if _merged_table_names(invoice.custom_merged_tables) or invoice.custom_merged_pos_invoice:
        _conflict(_("Merged or split bills must be handled in the standard POS."))
    if invoice.custom_split_from or invoice.custom_split_group:
        _conflict(_("Split bills must be handled in the standard POS."))
    if invoice.custom_restaurant_room and invoice.custom_restaurant_room != room:
        frappe.throw(
            _("This order belongs to a different room."),
            frappe.PermissionError,
        )


def _select_editable_invoice(actor, table_row, room, invoices):
    if table_row.merged_with:
        _conflict(_("Merged tables must be handled in the standard POS."))
    if len(invoices) > 1:
        _conflict(
            _("More than one active order exists for this table. Use the standard POS."),
            title=_("Ambiguous Table Order"),
        )
    if not invoices:
        if cint(table_row.occupied):
            _conflict(
                _("This table is marked occupied but has no single editable order."),
                title=_("Table Not Available"),
            )
        return None

    invoice = invoices[0]
    _validate_invoice_scope(actor, table_row.name, room, invoice)
    if invoice.waiter != actor.user:
        frappe.throw(
            _("This table belongs to another waiter."),
            frappe.PermissionError,
        )
    if cint(invoice.invoice_printed):
        _conflict(
            _("This order has already been sent for billing. Use the standard POS."),
            title=_("Order Locked"),
        )
    return invoice


def _validate_expected_modified(invoice, expected_modified):
    if not expected_modified:
        _conflict(
            _("Reload this table before adding another round."),
            title=_("Order Version Required"),
        )
    try:
        matches = get_datetime(invoice.modified) == get_datetime(expected_modified)
    except (TypeError, ValueError):
        matches = False
    if not matches:
        _conflict(
            _("This order was changed on another device. Reload the table."),
            title=_("Order Changed"),
        )


def _invoice_table_map(invoices, table_names):
    mapped = {name: [] for name in table_names}
    for invoice in invoices:
        members = {invoice.restaurant_table} | _merged_table_names(
            invoice.custom_merged_tables
        )
        for table in members.intersection(mapped):
            mapped[table].append(invoice)
    return mapped


def _table_state(actor, table, invoices, opening_is_open):
    if table.merged_with:
        return "locked", None, False, "merged_table"
    if len(invoices) > 1:
        return "unavailable", None, False, "multiple_orders"
    if not invoices:
        if cint(table.occupied):
            return "unavailable", None, False, "stale_occupied"
        return "free", None, bool(opening_is_open), (
            None if opening_is_open else "till_closed"
        )

    invoice = invoices[0]
    ownership = "mine" if invoice.waiter == actor.user else "other"
    merged = bool(
        invoice.restaurant_table != table.name
        or _merged_table_names(invoice.custom_merged_tables)
        or invoice.custom_merged_pos_invoice
        or invoice.custom_split_from
        or invoice.custom_split_group
    )
    scoped = (
        invoice.branch == actor.branch
        and invoice.pos_profile == actor.profile.name
        and (
            not invoice.custom_restaurant_room
            or invoice.custom_restaurant_room == table.restaurant_room
        )
    )
    editable = bool(
        opening_is_open
        and ownership == "mine"
        and not cint(invoice.invoice_printed)
        and not merged
        and scoped
    )
    if editable:
        return "mine", ownership, True, None
    if ownership == "other":
        return "occupied", ownership, False, "other_waiter"
    if cint(invoice.invoice_printed):
        return "locked", ownership, False, "sent_for_billing"
    if merged:
        return "locked", ownership, False, "merged_or_split"
    if not opening_is_open:
        return "mine", ownership, False, "till_closed"
    return "unavailable", ownership, False, "scope_mismatch"


def _format_order(invoice, editable):
    return {
        "invoice": invoice.name,
        "modified": str(invoice.modified),
        "table": invoice.restaurant_table,
        "room": invoice.custom_restaurant_room,
        "waiter_is_current_user": invoice.waiter == frappe.session.user,
        "no_of_pax": cint(invoice.no_of_pax) or 1,
        "comments": invoice.custom_comments or None,
        "grand_total": flt(invoice.grand_total),
        "editable": bool(editable),
        "items": [
            {
                "line_id": item.name,
                "item": item.item_code,
                "item_name": item.item_name,
                "qty": flt(item.qty),
                "uom": item.uom,
                "rate": flt(item.rate),
                "amount": flt(item.amount),
                "comment": item.comment or None,
                "sent": True,
            }
            for item in invoice.items
        ],
    }


def _normalise_text(value, field_label, max_length, allow_empty=True):
    if value is None:
        return None if allow_empty else ""
    value = str(value).replace("\x00", "").strip()
    if len(value) > max_length:
        frappe.throw(
            _("{0} cannot exceed {1} characters.").format(
                field_label, max_length
            )
        )
    return value or (None if allow_empty else "")


def _normalise_positive_integer(value, field_label, maximum):
    if isinstance(value, bool):
        frappe.throw(_("{0} must be a whole number.").format(field_label))
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw(_("{0} must be a whole number.").format(field_label))
    if not number.is_finite() or number != number.to_integral_value():
        frappe.throw(_("{0} must be a whole number.").format(field_label))
    number = int(number)
    if not 1 <= number <= maximum:
        frappe.throw(
            _("{0} must be between 1 and {1}.").format(field_label, maximum)
        )
    return number


def _normalise_expected_rate(value):
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw(_("Each item must include the displayed price."))
    if not rate.is_finite() or rate < 0 or rate > Decimal("999999999999"):
        frappe.throw(_("Each item must include a valid displayed price."))
    return rate


def _normalise_pending_items(items):
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (TypeError, ValueError):
            frappe.throw(_("Items must be a valid JSON array."))
    if not isinstance(items, (list, tuple)) or not items:
        frappe.throw(_("Add at least one item before registering the order."))
    if len(items) > MAX_PENDING_LINES:
        frappe.throw(
            _("A waiter request cannot contain more than {0} lines.").format(
                MAX_PENDING_LINES
            )
        )

    combined = {}
    order = []
    for row in items:
        if not isinstance(row, dict):
            frappe.throw(_("Each item must be an object."))
        item = str(row.get("item") or row.get("item_code") or "").strip()
        if not item or len(item) > 140:
            frappe.throw(_("Each line must contain a valid item code."))
        qty = _normalise_positive_integer(
            row.get("qty"), _("Item quantity"), MAX_ITEM_QTY
        )
        comment = _normalise_text(row.get("comment"), _("Item comment"), 500)
        expected_rate = _normalise_expected_rate(row.get("expected_rate"))
        key = (item, comment or "", str(expected_rate))
        if key not in combined:
            combined[key] = {
                "item": item,
                "qty": 0,
                "comment": comment,
                "expected_rate": expected_rate,
            }
            order.append(key)
        combined[key]["qty"] += qty
        if combined[key]["qty"] > MAX_ITEM_QTY:
            frappe.throw(
                _("The total quantity for item {0} is too large.").format(
                    frappe.bold(item)
                )
            )
    return [combined[key] for key in order]


def _normalise_request_id(request_id):
    request_id = str(request_id or "").strip()
    if (
        len(request_id) > MAX_REQUEST_ID_LENGTH
        or not REQUEST_ID_PATTERN.fullmatch(request_id)
    ):
        frappe.throw(
            _(
                "request_id is required and must be 8-128 letters, numbers, "
                "dots, colons, underscores or hyphens."
            )
        )
    return request_id


def _request_fingerprint(payload):
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _idempotency_key(user, request_id):
    return hashlib.sha256(f"{user}\x00{request_id}".encode("utf-8")).hexdigest()


def _load_waiter_request(key, lock=False):
    rows = frappe.db.sql(
        f"""
        SELECT name, payload_hash, status, invoice, response_json
        FROM `tabURY Waiter Request`
        WHERE name = %s
        {"FOR UPDATE" if lock else ""}
        """,
        key,
        as_dict=True,
    )
    return rows[0] if rows else None


def _replay_waiter_request(row, payload_hash):
    if row.payload_hash != payload_hash:
        _conflict(
            _("This request_id was already used with a different order."),
            title=_("Idempotency Conflict"),
        )
    if row.status != "Completed" or not row.response_json:
        frappe.throw(
            _("This request is still being processed. Please retry shortly."),
            WaiterRequestInProgressError,
            title=_("Request In Progress"),
        )
    try:
        response = json.loads(row.response_json)
    except (TypeError, ValueError):
        frappe.throw(_("The stored waiter response is invalid."))
    if not isinstance(response, dict):
        frappe.throw(_("The stored waiter response is invalid."))
    response = copy.deepcopy(response)
    response["replayed"] = True
    response["idempotent"] = True
    return response


def _reserve_waiter_request(user, request_id, payload_hash):
    key = _idempotency_key(user, request_id)
    existing = _load_waiter_request(key, lock=False)
    if existing:
        return None, _replay_waiter_request(existing, payload_hash)

    savepoint = f"waiter_request_{key[:16]}"
    frappe.db.savepoint(savepoint)
    request_doc = frappe.get_doc(
        {
            "doctype": IDEMPOTENCY_DOCTYPE,
            "idempotency_key": key,
            "request_id": request_id,
            "user": user,
            "payload_hash": payload_hash,
            "status": "Processing",
        }
    )
    try:
        request_doc.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point=savepoint)
        existing = _load_waiter_request(key, lock=True)
        if not existing:
            raise
        return None, _replay_waiter_request(existing, payload_hash)
    return request_doc, None


def _complete_waiter_request(request_doc, invoice, response):
    request_doc.status = "Completed"
    request_doc.invoice = invoice
    request_doc.response_json = json.dumps(
        response,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    request_doc.save(ignore_permissions=True)


def _get_authoritative_menu(actor, room, table=None):
    menu = getRestaurantMenu(actor.profile.name, room=room)
    if table:
        table_branch, table_menu, _restaurant = get_restaurant_and_menu_name(
            table
        )
        if table_branch != actor.branch or table_menu != menu.get("name"):
            frappe.throw(
                _("The table menu changed. Reload the room and retry."),
                WaiterConflictError,
            )
    raw_items = menu.get("items", [])
    item_codes = list(dict.fromkeys(row["item"] for row in raw_items))
    metadata = {
        row.name: row
        for row in frappe.get_all(
            "Item",
            filters={"name": ["in", item_codes]},
            fields=["name", "disabled"],
        )
    }
    enabled_items = [
        row
        for row in raw_items
        if row["item"] in metadata and not cint(metadata[row["item"]].disabled)
    ]
    price_list = get_authoritative_menu_price_list(menu.get("name"))
    prices = get_authoritative_item_prices(
        [row["item"] for row in enabled_items], price_list
    )
    items = []
    for row in enabled_items:
        authoritative = dict(row)
        authoritative["rate"] = prices[row["item"]]
        items.append(authoritative)
    return frappe._dict(
        name=menu.get("name"),
        modified_time=menu.get("modified_time"),
        price_list=price_list,
        items=items,
    )


def _hydrate_pending_items(menu, pending_items):
    menu_items = {row["item"]: row for row in menu.get("items", [])}
    hydrated = []
    for row in pending_items:
        menu_item = menu_items.get(row["item"])
        if not menu_item:
            frappe.throw(
                _("Item {0} is not available in this room's active menu.").format(
                    frappe.bold(row["item"])
                ),
                frappe.PermissionError,
            )
        authoritative_rate = Decimal(str(menu_item["rate"]))
        if row["expected_rate"] != authoritative_rate:
            _conflict(
                _(
                    "The price of item {0} changed from {1} to {2}. Reload "
                    "the menu before registering this order."
                ).format(
                    frappe.bold(row["item"]),
                    frappe.bold(row["expected_rate"]),
                    frappe.bold(authoritative_rate),
                ),
                title=_("Item Price Changed"),
            )
        hydrated.append(
            {
                "item": menu_item["item"],
                "item_name": menu_item["item_name"],
                "qty": row["qty"],
                "comment": row["comment"],
                "_authoritative_rate": flt(menu_item["rate"]),
            }
        )
    return hydrated


def _validate_production_routes(branch, item_codes):
    item_codes = list(dict.fromkeys(item_codes))
    items = frappe.get_all(
        "Item",
        filters={"name": ["in", item_codes]},
        fields=["name", "item_group", "disabled"],
    )
    item_map = {row.name: row for row in items}
    missing = [item for item in item_codes if item not in item_map]
    disabled = [item for item in item_codes if item in item_map and cint(item_map[item].disabled)]
    if missing or disabled:
        frappe.throw(
            _("One or more requested items are missing or disabled: {0}.").format(
                ", ".join(missing + disabled)
            )
        )

    productions = frappe.get_all(
        "URY Production Unit",
        filters={"branch": branch},
        fields=["name"],
        order_by="name",
    )
    production_names = [row.name for row in productions]
    if not production_names:
        frappe.throw(_("No production unit is configured for this branch."))

    assignments = frappe.get_all(
        "URY Production Item Groups",
        filters={
            "parent": ["in", production_names],
            "parenttype": "URY Production Unit",
        },
        fields=["parent", "item_group"],
    )
    units_by_group = {}
    for assignment in assignments:
        units_by_group.setdefault(assignment.item_group, set()).add(assignment.parent)

    routes = {}
    unrouted = []
    duplicated = []
    for item_code in item_codes:
        units = sorted(units_by_group.get(item_map[item_code].item_group, set()))
        if not units:
            unrouted.append(item_code)
        elif len(units) > 1:
            duplicated.append(item_code)
        else:
            routes[item_code] = units[0]

    if unrouted:
        frappe.throw(
            _("No production route is configured for: {0}.").format(
                ", ".join(frappe.bold(item) for item in unrouted)
            ),
            title=_("Missing Production Route"),
        )
    if duplicated:
        frappe.throw(
            _("More than one production route is configured for: {0}.").format(
                ", ".join(frappe.bold(item) for item in duplicated)
            ),
            title=_("Duplicate Production Route"),
        )
    return routes


def _first_payment_mode(profile):
    for payment in profile.payments:
        if payment.mode_of_payment:
            return payment.mode_of_payment
    frappe.throw(
        _("POS Profile {0} has no payment mode configured.").format(
            frappe.bold(profile.name)
        )
    )


def _verify_kot_result(kot_result, routes):
    kot_result = kot_result or {}
    if kot_result.get("unrouted_items"):
        frappe.throw(
            _("The KOT backend did not route every requested item."),
            title=_("KOT Routing Failed"),
        )
    created = kot_result.get("created_kots") or []
    expected_units = set(routes.values())
    created_units = {row.get("production") for row in created if row.get("production")}
    if created_units != expected_units or len(created) != len(expected_units):
        frappe.throw(
            _("The KOT backend did not create every expected production ticket."),
            title=_("KOT Creation Failed"),
        )
    if any(not row.get("name") for row in created):
        frappe.throw(
            _("The KOT backend returned an invalid ticket."),
            title=_("KOT Creation Failed"),
        )
    return [
        {"name": row["name"], "production": row["production"]}
        for row in created
    ]


def _create_waiter_kots(
    invoice_id,
    customer,
    table,
    pending_items,
    comments,
):
    """Create KOTs through a server-only permission bypass.

    The dedicated mobile waiter role intentionally has no KOT DocPerm. This
    helper is not whitelisted, and the public ``kot_execute`` RPC never exposes
    the ``ignore_permissions`` control.
    """
    return _kot_execute(
        invoice_id,
        customer,
        table,
        pending_items,
        [],
        comments,
        ignore_permissions=True,
    )


@frappe.whitelist(methods=["GET"], allow_guest=True)
def get_context():
    actor = _get_actor_context(require_supported=False)
    opening = _opening_state(actor)
    full_name = frappe.db.get_value("User", actor.user, "full_name") or actor.user
    return {
        "user": {"name": actor.user, "full_name": full_name},
        "branch": actor.branch,
        "pos_profile": actor.profile.name,
        "rooms": [
            {"name": room, "is_open": bool(opening["is_open"])}
            for room in actor.rooms
        ],
        "opening": opening,
        "can_register": bool(opening["is_open"]),
    }


@frappe.whitelist(methods=["GET"], allow_guest=True)
def get_tables(room=None):
    actor = _get_actor_context(require_supported=True)
    selected_rooms = [_resolve_room(actor, room)] if room else actor.rooms
    opening = _opening_state(actor)
    tables = frappe.get_all(
        "URY Table",
        filters={
            "branch": actor.branch,
            "restaurant_room": ["in", selected_rooms],
            "is_take_away": 0,
        },
        fields=[
            "name",
            "occupied",
            "latest_invoice_time",
            "restaurant_room",
            "table_shape",
            "no_of_seats",
            "minimum_seating",
            "layout_x",
            "layout_y",
            "merged_with",
        ],
        order_by="restaurant_room, name",
    )
    invoices = frappe.get_all(
        "POS Invoice",
        filters={"branch": actor.branch, "docstatus": 0},
        fields=[
            "name",
            "waiter",
            "modified",
            "invoice_printed",
            "restaurant_table",
            "custom_merged_tables",
            "custom_merged_pos_invoice",
            "custom_split_from",
            "custom_split_group",
            "pos_profile",
            "branch",
            "custom_restaurant_room",
        ],
        order_by="creation, name",
    )
    invoice_map = _invoice_table_map(invoices, {table.name for table in tables})

    response_tables = []
    for table in tables:
        table_invoices = invoice_map[table.name]
        state, ownership, editable, reason = _table_state(
            actor, table, table_invoices, opening["is_open"]
        )
        own_invoice = (
            table_invoices[0]
            if len(table_invoices) == 1 and ownership == "mine"
            else None
        )
        response_tables.append(
            {
                "name": table.name,
                "room": table.restaurant_room,
                "occupied": bool(cint(table.occupied) or table_invoices),
                "state": state,
                "ownership": ownership,
                "editable": editable,
                "reason": reason,
                "invoice": own_invoice.name if own_invoice else None,
                "modified": str(own_invoice.modified) if own_invoice else None,
                "latest_invoice_time": (
                    str(table.latest_invoice_time)
                    if table.latest_invoice_time
                    else None
                ),
                "table_shape": table.table_shape,
                "no_of_seats": cint(table.no_of_seats),
                "minimum_seating": cint(table.minimum_seating),
                "layout_x": flt(table.layout_x),
                "layout_y": flt(table.layout_y),
            }
        )
    return {
        "rooms": selected_rooms,
        "opening": opening,
        "tables": response_tables,
    }


@frappe.whitelist(methods=["GET"], allow_guest=True)
def get_menu(room):
    actor = _get_actor_context(require_supported=True)
    room = _resolve_room(actor, room)
    menu = _get_authoritative_menu(actor, room)
    items = []
    for item in menu.get("items", []):
        available = bool(
            not item.get("is_stock_item")
            or item.get("negative_stock_allowed")
            or flt(item.get("available_qty")) > 0
        )
        items.append(
            {
                "item": item["item"],
                "item_name": item["item_name"],
                "item_image": item.get("item_image"),
                "rate": flt(item.get("rate")),
                "course": item.get("course"),
                "course_label": item.get("course_label"),
                "special_dish": bool(cint(item.get("special_dish"))),
                "available": available,
                "available_qty": flt(item.get("available_qty")),
                "is_stock_item": bool(item.get("is_stock_item")),
                "stock_uom": item.get("stock_uom"),
                "negative_stock_allowed": bool(
                    item.get("negative_stock_allowed")
                ),
            }
        )
    return {
        "room": room,
        "menu": {
            "name": menu.name,
            "modified": str(menu.modified_time),
        },
        "items": items,
    }


@frappe.whitelist(methods=["GET"], allow_guest=True)
def get_table_order(table):
    actor = _get_actor_context(require_supported=True)
    table_name = str(table or "").strip()
    table_details = frappe.db.get_value(
        "URY Table",
        table_name,
        ["restaurant_room", "branch"],
        as_dict=True,
    )
    if not table_details or table_details.branch != actor.branch:
        frappe.throw(_("This table is not available."), frappe.PermissionError)
    room = _resolve_room(actor, table_details.restaurant_room)
    table_row = _get_table(actor, table_name, room, lock=False)
    if table_row.merged_with:
        _conflict(_("Merged tables must be handled in the standard POS."))
    invoices = _active_table_invoices(actor.branch, table_name, lock=False)
    if len(invoices) > 1:
        _conflict(
            _("More than one active order exists for this table. Use the standard POS."),
            title=_("Ambiguous Table Order"),
        )
    opening = _opening_state(actor)
    if not invoices:
        return {
            "table": table_name,
            "room": room,
            "editable": bool(opening["is_open"] and not cint(table_row.occupied)),
            "opening": opening,
            "order": None,
        }

    invoice_row = invoices[0]
    _validate_invoice_scope(actor, table_name, room, invoice_row)
    if invoice_row.waiter != actor.user:
        frappe.throw(
            _("This table belongs to another waiter."),
            frappe.PermissionError,
        )
    editable = bool(opening["is_open"] and not cint(invoice_row.invoice_printed))
    invoice = frappe.get_doc("POS Invoice", invoice_row.name)
    return {
        "table": table_name,
        "room": room,
        "editable": editable,
        "opening": opening,
        "order": _format_order(invoice, editable),
    }


@frappe.whitelist(methods=["POST"], allow_guest=True)
def register_order(
    table,
    room,
    items,
    no_of_pax=1,
    comments=None,
    expected_modified=None,
    request_id=None,
):
    """Append one pending waiter round and create its KOTs atomically."""
    actor = _get_actor_context(require_supported=True)
    room = _resolve_room(actor, room)
    table = str(table or "").strip()
    pending = _normalise_pending_items(items)
    no_of_pax = _normalise_positive_integer(no_of_pax, _("Number of guests"), 99)
    comments = _normalise_text(comments, _("Order comments"), 1000)
    expected_modified = (
        str(expected_modified).strip() if expected_modified else None
    )
    request_id = _normalise_request_id(request_id)
    fingerprint = _request_fingerprint(
        {
            "table": table,
            "room": room,
            "items": pending,
            "no_of_pax": no_of_pax,
            "comments": comments,
            "expected_modified": expected_modified,
        }
    )
    request_doc, replay = _reserve_waiter_request(
        actor.user, request_id, fingerprint
    )
    if replay:
        return replay

    # Authorise the table before resolving its exact room-wise menu.
    _get_table(actor, table, room, lock=False)
    menu = _get_authoritative_menu(actor, room, table=table)
    pending = _hydrate_pending_items(menu, pending)
    authoritative_prices = {
        row["item"]: row.pop("_authoritative_rate") for row in pending
    }
    routes = _validate_production_routes(
        actor.branch, [row["item"] for row in pending]
    )
    # Match the global order lock order: opening -> table -> invoice -> stock.
    # The private save service rechecks the same opening before persisting.
    _get_opening(actor.profile, required=True, for_update=True)
    table_row = _get_table(actor, table, room, lock=True)
    invoices = _active_table_invoices(actor.branch, table, lock=True)
    invoice_row = _select_editable_invoice(
        actor, table_row, room, invoices
    )

    existing_invoice = None
    if invoice_row:
        _validate_expected_modified(invoice_row, expected_modified)
        existing_invoice = frappe.get_doc("POS Invoice", invoice_row.name)
        final_items = pending
        customer = existing_invoice.customer
        last_invoice = invoice_row.name
        invoice_name = invoice_row.name
        last_modified_time = invoice_row.modified
    else:
        if expected_modified:
            _conflict(
                _("The referenced order no longer exists. Reload the table."),
                title=_("Order Changed"),
            )
        final_items = pending
        customer = actor.profile.customer
        last_invoice = None
        invoice_name = None
        last_modified_time = None

    if not customer:
        frappe.throw(
            _("POS Profile {0} has no default customer.").format(
                frappe.bold(actor.profile.name)
            )
        )

    saved = _sync_order(
        items=final_items,
        cashier=None,
        owner=None,
        mode_of_payment=_first_payment_mode(actor.profile),
        customer=customer,
        no_of_pax=no_of_pax,
        last_invoice=last_invoice,
        waiter=actor.user,
        pos_profile=actor.profile.name,
        last_modified_time=last_modified_time,
        table=table,
        invoice=invoice_name,
        comments=comments,
        order_type="Dine In",
        room=room,
        skip_kot=True,
        strict_invoice=True,
        expected_invoice_name=invoice_name,
        append_only=True,
        expected_price_list=menu.get("price_list"),
        expected_item_prices=authoritative_prices,
    )
    if not saved or saved.get("status") == "Failure" or not saved.get("name"):
        _conflict(_("The order could not be saved. Reload the table and retry."))

    saved_invoice = frappe.get_doc("POS Invoice", saved["name"])
    kot_result = _create_waiter_kots(
        saved_invoice.name,
        saved_invoice.customer,
        table,
        pending,
        comments,
    )
    created_kots = _verify_kot_result(kot_result, routes)

    response = {
        "status": "success",
        "replayed": False,
        "idempotent": False,
        "request_id": request_id,
        "invoice": saved_invoice.name,
        "modified": str(saved_invoice.modified),
        "table": table,
        "room": room,
        "kots": created_kots,
        "kot_warning": None,
        "order": _format_order(saved_invoice, editable=True),
    }
    _complete_waiter_request(request_doc, saved_invoice.name, response)
    return response
