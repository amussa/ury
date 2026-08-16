# Copyright (c) 2023, Tridz Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json
from contextlib import contextmanager

import frappe
from erpnext.accounts.doctype.pos_invoice.pos_invoice import (
    get_product_bundle_stock_availability,
    get_stock_availability,
)
from erpnext.controllers.queries import item_query
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from ury.ury.api.ury_kot_generate import kot_execute, process_items_for_cancel_kot
from ury.ury_pos.api import (
    _get_stock_details,
    get_draft_reserved_qty_map,
    get_pos_profile_for_current_branch,
    get_submitted_reserved_qty_map,
    getBranch,
    getBranchRoom,
)
from ury.ury_pos.cashier import get_single_cashier_opening
from ury.ury_pos.price_options import (
    OPTION_FIELD,
    OPTION_LABEL_FIELD,
    STANDARD_OPTION_ID,
    apply_price_option_to_row,
    get_menu_promotions,
    group_menu_promotions,
    lock_invoice_price_options,
    lock_menu_price_options_parent,
    resolve_price_option,
    validate_price_option_quantities,
    validate_price_option_row_prices,
)


class URYOrder(Document):
    pass


_APPEND_ONLY_SYSTEM_FIELDS = frozenset({"modified", "modified_by"})


@contextmanager
def _temporary_order_actor(user):
    """Run strict waiter validation as the cashier who owns the open till."""
    original_user = getattr(frappe.session, "user", None)
    should_switch = bool(user and user != original_user)
    if should_switch:
        frappe.set_user(user)
    try:
        yield
    finally:
        if should_switch:
            frappe.set_user(original_user)


def _persisted_order_item_values(item):
    """Return every persisted child-row field except save audit timestamps."""
    get_valid_dict = getattr(item, "get_valid_dict", None)
    if callable(get_valid_dict):
        values = get_valid_dict(
            convert_dates_to_str=True,
            ignore_virtual=True,
        )
    else:
        # Lightweight frappe._dict rows are useful in the mock-only regression
        # suite; production POS Invoice Item documents use get_valid_dict().
        values = dict(item)
    return {
        fieldname: value
        for fieldname, value in values.items()
        if fieldname not in _APPEND_ONLY_SYSTEM_FIELDS
    }


def _snapshot_existing_order_items(invoice):
    """Capture fields that a waiter append must never rewrite."""
    snapshot = []
    for item in invoice.items:
        if not item.name:
            frappe.throw(_("An existing order line has no identity."))
        snapshot.append(
            (
                item.name,
                _persisted_order_item_values(item),
            )
        )
    return snapshot


def _assert_existing_order_items_unchanged(invoice, snapshot):
    """Fail the transaction if validation rewrites an already-sent line."""
    if not snapshot:
        return

    expected_names = [name for name, _values in snapshot]
    expected_name_set = set(expected_names)
    current_by_name = {item.name: item for item in invoice.items if item.name}
    current_names = [
        item.name for item in invoice.items if item.name in expected_name_set
    ]
    if current_names != expected_names or any(
        name not in current_by_name for name in expected_names
    ):
        frappe.throw(_("An existing order line was replaced or reordered."))

    for name, values in snapshot:
        item = current_by_name[name]
        current_values = _persisted_order_item_values(item)
        changed = [
            fieldname
            for fieldname, expected in values.items()
            if current_values.get(fieldname) != expected
        ]
        changed.extend(
            fieldname
            for fieldname in current_values
            if fieldname not in values
        )
        if changed:
            frappe.throw(
                _("Existing order line {0} was modified in fields: {1}.").format(
                    frappe.bold(name), ", ".join(changed)
                )
            )


def get_authoritative_menu_price_list(menu):
    """Resolve the only enabled selling Price List bound to a URY Menu."""
    rows = frappe.get_all(
        "Price List",
        filters={"restaurant_menu": menu, "enabled": 1, "selling": 1},
        fields=["name"],
        order_by="name",
        limit=2,
    )
    if not rows:
        frappe.throw(
            _("No enabled selling Price List is configured for menu {0}.").format(
                frappe.bold(menu)
            )
        )
    if len(rows) > 1:
        frappe.throw(
            _("More than one enabled selling Price List is configured for menu {0}.").format(
                frappe.bold(menu)
            ),
            title=_("Ambiguous Price List"),
        )
    return rows[0].name


def get_authoritative_item_prices(item_codes, price_list, for_update=False):
    """Resolve exactly one selling Item Price per item for a Price List."""
    item_codes = list(dict.fromkeys(code for code in item_codes if code))
    if not price_list:
        frappe.throw(_("A selling Price List is required."))
    if not item_codes:
        return {}

    if for_update:
        rows = frappe.db.sql(
            """
            SELECT name, item_code, price_list_rate
            FROM `tabItem Price`
            WHERE item_code IN %(item_codes)s
              AND price_list = %(price_list)s
              AND selling = 1
            ORDER BY item_code, name
            FOR UPDATE
            """,
            {
                "item_codes": tuple(sorted(item_codes)),
                "price_list": price_list,
            },
            as_dict=True,
        )
    else:
        rows = frappe.get_all(
            "Item Price",
            filters={
                "item_code": ["in", item_codes],
                "price_list": price_list,
                "selling": 1,
            },
            fields=["name", "item_code", "price_list_rate"],
            order_by="item_code, name",
        )
    rows_by_item = {}
    for row in rows:
        rows_by_item.setdefault(row.item_code, []).append(row)

    missing = [code for code in item_codes if not rows_by_item.get(code)]
    ambiguous = [
        code for code in item_codes if len(rows_by_item.get(code, [])) > 1
    ]
    if missing:
        frappe.throw(
            _("No selling Item Price exists in {0} for: {1}.").format(
                frappe.bold(price_list),
                ", ".join(frappe.bold(code) for code in missing),
            )
        )
    if ambiguous:
        frappe.throw(
            _("More than one selling Item Price exists in {0} for: {1}.").format(
                frappe.bold(price_list),
                ", ".join(frappe.bold(code) for code in ambiguous),
            ),
            title=_("Ambiguous Item Price"),
        )

    return {
        code: flt(rows_by_item[code][0].price_list_rate)
        for code in item_codes
    }


def _get_locked_waiter_menu(
    table,
    room,
    expected_menu,
    item_codes,
):
    """Resolve and validate the current waiter menu under database locks.

    Plain reads can keep returning an older snapshot under MariaDB's default
    REPEATABLE READ isolation.  These locking reads therefore re-check the
    table, its restaurant menu configuration and the requested menu rows after
    the waiter transaction has acquired its opening/table locks and before it
    locks the active invoice.
    """
    table_rows = frappe.db.sql(
        """
        SELECT name, restaurant, restaurant_room
        FROM `tabURY Table`
        WHERE name = %(table)s
        FOR UPDATE
        """,
        {"table": table},
        as_dict=True,
    )
    if not table_rows:
        frappe.throw(
            _("The selected table no longer exists. Reload the room and retry."),
            frappe.TimestampMismatchError,
            title=_("Table Changed"),
        )
    table_row = table_rows[0]
    if table_row.restaurant_room != room or not table_row.restaurant:
        frappe.throw(
            _("The table room changed. Reload the room and retry."),
            frappe.TimestampMismatchError,
            title=_("Table Changed"),
        )

    restaurant_rows = frappe.db.sql(
        """
        SELECT name, active_menu, room_wise_menu
        FROM `tabURY Restaurant`
        WHERE name = %(restaurant)s
        FOR UPDATE
        """,
        {"restaurant": table_row.restaurant},
        as_dict=True,
    )
    if not restaurant_rows:
        frappe.throw(
            _("The table restaurant changed. Reload the room and retry."),
            frappe.TimestampMismatchError,
            title=_("Menu Changed"),
        )
    restaurant = restaurant_rows[0]

    if cint(restaurant.room_wise_menu):
        room_menu_rows = frappe.db.sql(
            """
            SELECT name, menu
            FROM `tabMenu for Room`
            WHERE parent = %(restaurant)s
              AND room = %(room)s
            ORDER BY name
            FOR UPDATE
            """,
            {"restaurant": restaurant.name, "room": room},
            as_dict=True,
        )
        current_menu = room_menu_rows[0].menu if room_menu_rows else None
    else:
        current_menu = restaurant.active_menu

    if not current_menu or current_menu != expected_menu:
        frappe.throw(
            _("The table menu changed. Reload the room and retry."),
            frappe.TimestampMismatchError,
            title=_("Menu Changed"),
        )

    # The parent lock serialises menu edits even when no matching child row
    # exists. Requested child rows are then locked in a stable order so all
    # waiter writers acquire this part of the lock graph identically.
    lock_menu_price_options_parent(current_menu)
    item_codes = sorted(set(code for code in item_codes if code))
    if not item_codes:
        return current_menu
    menu_items = frappe.db.sql(
        """
        SELECT name, item, disabled
        FROM `tabURY Menu Item`
        WHERE parent = %(menu)s
          AND item IN %(item_codes)s
        ORDER BY name
        FOR UPDATE
        """,
        {"menu": current_menu, "item_codes": tuple(item_codes)},
        as_dict=True,
    )
    enabled_items = {
        row.item for row in menu_items if not cint(row.disabled)
    }
    unavailable = [code for code in item_codes if code not in enabled_items]
    if unavailable:
        frappe.throw(
            _(
                "Items are no longer enabled in the active menu: {0}. "
                "Reload the menu and retry."
            ).format(
                ", ".join(frappe.bold(code) for code in unavailable)
            ),
            frappe.TimestampMismatchError,
            title=_("Menu Item Changed"),
        )

    item_rows = frappe.db.sql(
        """
        SELECT name, disabled
        FROM `tabItem`
        WHERE name IN %(item_codes)s
        ORDER BY name
        FOR UPDATE
        """,
        {"item_codes": tuple(item_codes)},
        as_dict=True,
    )
    enabled_master_items = {
        row.name for row in item_rows if not cint(row.disabled)
    }
    unavailable = [
        code for code in item_codes if code not in enabled_master_items
    ]
    if unavailable:
        frappe.throw(
            _(
                "Items are no longer enabled: {0}. Reload the menu and retry."
            ).format(
                ", ".join(frappe.bold(code) for code in unavailable)
            ),
            frappe.TimestampMismatchError,
            title=_("Item Changed"),
        )

    return current_menu


def _get_locked_waiter_price_list(menu, expected_price_list):
    """Lock and validate the Price List after the active invoice is locked."""
    price_list_rows = frappe.db.sql(
        """
        SELECT name, enabled, selling, restaurant_menu
        FROM `tabPrice List`
        WHERE name = %(price_list)s
        FOR UPDATE
        """,
        {"price_list": expected_price_list},
        as_dict=True,
    )
    price_list = price_list_rows[0] if price_list_rows else None
    if (
        not price_list
        or not cint(price_list.enabled)
        or not cint(price_list.selling)
        or price_list.restaurant_menu != menu
    ):
        frappe.throw(
            _(
                "The active menu Price List changed. Reload the menu and retry."
            ),
            frappe.TimestampMismatchError,
            title=_("Price List Changed"),
        )
    return price_list.name


def _get_locked_waiter_item_prices(
    menu,
    item_codes,
    price_list,
    expected_item_prices,
    items=None,
):
    """Re-read waiter prices after locking the menu pricing configuration."""
    lock_menu_price_options_parent(menu)
    _get_locked_waiter_price_list(menu, price_list)
    item_codes = list(dict.fromkeys(code for code in item_codes if code))
    current_prices = get_authoritative_item_prices(
        item_codes, price_list, for_update=True
    )
    changed = [
        item_code
        for item_code in item_codes
        if item_code not in expected_item_prices
        or item_code not in current_prices
        or flt(expected_item_prices[item_code]) != flt(current_prices[item_code])
    ]
    if changed:
        frappe.throw(
            _(
                "Item price changed while registering this order: {0}. "
                "Reload the menu and retry."
            ).format(
                ", ".join(frappe.bold(item_code) for item_code in changed)
            ),
            frappe.TimestampMismatchError,
            title=_("Item Price Changed"),
        )
    # Promotion details were shown before the transaction acquired the menu
    # lock. Re-resolve every selected option against the locked rows and ensure
    # its displayed rate still matches; base Item Price validation above alone
    # cannot detect a promotion rate/id change.
    if items and any(
        item.get("_expected_option_rate") is not None
        or (
            item.get("price_option")
            and item.get("price_option") != STANDARD_OPTION_ID
        )
        for item in items
    ):
        promotions_by_item = group_menu_promotions(
            menu, item_codes, for_update=True
        )
        changed_options = []
        for item in items:
            item_code = item.get("item")
            requested_option_id = item.get("price_option")
            if requested_option_id and requested_option_id != STANDARD_OPTION_ID:
                promotion = next(
                    (
                        row
                        for row in promotions_by_item.get(item_code, [])
                        if row.name == requested_option_id
                    ),
                    None,
                )
                option_rate = promotion.rate if promotion else None
            else:
                option_rate = current_prices[item_code]
            expected_rate = item.get("_expected_option_rate")
            if (
                option_rate is None
                or expected_rate is None
                or flt(expected_rate) != flt(option_rate)
            ):
                changed_options.append(item_code)
        if changed_options:
            frappe.throw(
                _(
                    "Price option changed while registering this order: {0}. "
                    "Reload the menu and retry."
                ).format(
                    ", ".join(
                        frappe.bold(item_code)
                        for item_code in sorted(set(changed_options))
                    )
                ),
                frappe.TimestampMismatchError,
                title=_("Price Option Changed"),
            )
    return current_prices


def _append_server_priced_order_items(
    invoice,
    items,
    menu,
    price_list,
    pos_profile,
    authoritative_prices=None,
):
    """Append new rows with server prices without touching existing child rows."""
    appended = []
    cost_center = frappe.db.get_value("POS Profile", pos_profile, "cost_center")
    item_codes = list(dict.fromkeys(item.get("item") for item in items))
    prices = (
        dict(authoritative_prices)
        if authoritative_prices is not None
        else get_authoritative_item_prices(item_codes, price_list)
    )
    if any(item_code not in prices for item_code in item_codes):
        frappe.throw(_("An authoritative price is missing for this waiter round."))
    promotions_by_item = group_menu_promotions(
        menu, item_codes, for_update=True
    )
    for item in items:
        item_code = item.get("item")
        course = frappe.db.get_value(
            "URY Menu Item", {"item": item_code, "parent": menu}, "course"
        )
        price_option = resolve_price_option(
            menu,
            item_code,
            item.get("price_option"),
            prices[item_code],
            promotions_by_item=promotions_by_item,
        )
        row_values = apply_price_option_to_row(
            dict(
                item_code=item_code,
                item_name=item.get("item_name"),
                qty=item.get("qty"),
                **({"custom_course": course} if course else {}),
                comment=item.get("comment"),
                cost_center=cost_center,
            ),
            price_option,
        )
        appended.append(
            invoice.append("items", row_values)
        )
    return appended


def _snapshot_appended_item_prices(items):
    return [
        (item, flt(item.get("rate")), flt(item.get("price_list_rate")))
        for item in items
    ]


def _assert_appended_item_prices_unchanged(snapshot):
    """Ensure invoice validation cannot silently reprice a waiter round."""
    changed = [
        item.get("item_code")
        for item, rate, price_list_rate in snapshot
        if flt(item.get("rate")) != rate
        or flt(item.get("price_list_rate")) != price_list_rate
    ]
    if changed:
        frappe.throw(
            _(
                "Server pricing changed while validating: {0}. Handle this "
                "order in the standard POS."
            ).format(", ".join(frappe.bold(code) for code in changed)),
            title=_("Item Price Changed"),
        )


def _snapshot_persisted_appended_item_prices(snapshot):
    persisted = []
    for item, rate, price_list_rate in snapshot:
        if not item.get("name"):
            frappe.throw(_("A newly appended order line has no identity."))
        persisted.append(
            (
                item.get("name"),
                item.get("item_code"),
                rate,
                price_list_rate,
            )
        )
    return persisted


def _assert_persisted_appended_item_prices_unchanged(invoice, snapshot):
    current_by_name = {item.name: item for item in invoice.items if item.name}
    changed = []
    for name, item_code, rate, price_list_rate in snapshot:
        item = current_by_name.get(name)
        if (
            not item
            or flt(item.get("rate")) != rate
            or flt(item.get("price_list_rate")) != price_list_rate
        ):
            changed.append(item_code)
    if changed:
        frappe.throw(
            _(
                "Persisted server pricing changed for: {0}. Handle this order "
                "in the standard POS."
            ).format(", ".join(frappe.bold(code) for code in changed)),
            title=_("Item Price Changed"),
        )


def _aggregate_order_stock_qty(items):
    """Aggregate the final order payload in stock UOM, preserving fractional qty."""
    quantities = {}
    for item in items:
        item_code = item.get("item_code")
        if not item_code:
            continue

        conversion_factor = flt(item.get("conversion_factor")) or 1
        stock_qty = flt(item.get("qty")) * conversion_factor
        quantities[item_code] = flt(quantities.get(item_code)) + stock_qty

    return quantities


def _get_stock_lock_item_codes(item_codes):
    """Include Product Bundle components so different bundles share the same lock."""
    lock_items = set(item_codes)
    if item_codes:
        lock_items.update(
            frappe.get_all(
                "Product Bundle Item",
                filters={"parent": ["in", list(item_codes)]},
                pluck="item_code",
            )
        )
    return sorted(lock_items)


def _lock_stock_bins(warehouse, item_codes):
    """Serialize stock promises for the same physical items until request commit."""
    if not item_codes:
        return {}

    rows = frappe.db.sql(
        """
        SELECT item_code, actual_qty
        FROM `tabBin`
        WHERE warehouse = %(warehouse)s
          AND item_code IN %(item_codes)s
        ORDER BY item_code
        FOR UPDATE
        """,
        {"warehouse": warehouse, "item_codes": tuple(sorted(item_codes))},
        as_dict=True,
    )
    return {row.item_code: flt(row.actual_qty) for row in rows}


def _get_active_product_bundle_codes(item_codes):
    if not item_codes:
        return set()
    return set(
        frappe.get_all(
            "Product Bundle",
            filters={"name": ["in", list(item_codes)], "disabled": 0},
            pluck="name",
        )
    )


def _validate_order_stock(
    ordered_qty,
    warehouse,
    exclude_invoice=None,
    locked_bin_qty=None,
):
    """Validate the complete final order against submitted and draft reservations."""
    bundle_codes = _get_active_product_bundle_codes(ordered_qty)
    required_by_stock_item = {}
    available_by_stock_item = {}

    for item_code in sorted(ordered_qty):
        required_qty = flt(ordered_qty[item_code])
        if item_code in bundle_codes:
            (
                availability,
                _bundle_has_stock,
                _bundle_allows_negative_stock,
            ) = get_product_bundle_stock_availability(
                item_code, warehouse, required_qty
            )
            for component in availability:
                component_code = component["item_code"]
                required_by_stock_item[component_code] = flt(
                    required_by_stock_item.get(component_code)
                ) + flt(component["required"])
                component_available = flt(component["available"])
                previous_available = available_by_stock_item.get(component_code)
                available_by_stock_item[component_code] = (
                    component_available
                    if previous_available is None
                    else min(previous_available, component_available)
                )
            continue

        (
            availability,
            is_stock_item,
            _allows_negative_stock,
        ) = get_stock_availability(item_code, warehouse)
        if not is_stock_item:
            continue

        required_by_stock_item[item_code] = flt(
            required_by_stock_item.get(item_code)
        ) + required_qty
        scalar_availability = flt(availability)
        previous_available = available_by_stock_item.get(item_code)
        available_by_stock_item[item_code] = (
            scalar_availability
            if previous_available is None
            else min(previous_available, scalar_availability)
        )

    stock_item_codes = required_by_stock_item.keys()
    if locked_bin_qty is not None:
        submitted_reserved = get_submitted_reserved_qty_map(
            stock_item_codes,
            warehouse,
            for_update=True,
        )
        available_by_stock_item = {
            item_code: flt(locked_bin_qty.get(item_code))
            - flt(submitted_reserved.get(item_code))
            for item_code in stock_item_codes
        }
    draft_reserved = get_draft_reserved_qty_map(
        stock_item_codes,
        warehouse,
        exclude_invoice=exclude_invoice,
        for_update=True,
    )
    shortages = []
    for item_code in sorted(required_by_stock_item):
        required_qty = flt(required_by_stock_item[item_code])
        available_qty = flt(available_by_stock_item[item_code]) - flt(
            draft_reserved.get(item_code)
        )
        if available_qty < required_qty:
            shortages.append(
                _("Item {0}: Required {1}, Available {2}").format(
                    frappe.bold(item_code),
                    frappe.bold(required_qty),
                    frappe.bold(available_qty),
                )
            )

    if shortages:
        frappe.throw(
            _("Insufficient stock in warehouse {0}:<br>{1}").format(
                frappe.bold(warehouse),
                "<br>".join(shortages),
            ),
            title=_("Insufficient Stock"),
        )


def set_pos_profile(invoice, pos_profile):
    if not pos_profile:
        frappe.throw(_("POS Profile is required."))
    if invoice.pos_profile and invoice.pos_profile != pos_profile:
        frappe.throw(
            _("POS Profile cannot be changed from {0} to {1} on an existing order.").format(
                frappe.bold(invoice.pos_profile),
                frappe.bold(pos_profile),
            )
        )
    invoice.pos_profile = pos_profile

@frappe.whitelist()
def merge_free_tables(table1, table2):
    """Merges two tables in the same room; allows one occupied and one free."""
    return merge_tables_batch(table1, [table2])


@frappe.whitelist()
def merge_tables_batch(anchor_table, tables):

    if isinstance(tables, str):
        tables = json.loads(tables)

    targets = list(
        dict.fromkeys(
            t
            for t in tables
            if t and t != anchor_table
        )
    )

    if not targets:
        frappe.throw(
            _("Select at least one table to merge.")
        )

    room = frappe.db.get_value(
        "URY Table",
        anchor_table,
        "restaurant_room",
    )

    if not room:
        frappe.throw(
            _("Table not found.")
        )

    # Start from anchor cluster only
    cluster, table_map = _get_merge_cluster(
        anchor_table
    )

    cluster = set(cluster)

    for target in targets:

        target_room = frappe.db.get_value(
            "URY Table",
            target,
            "restaurant_room",
        )

        if target_room != room:
            frappe.throw(
                _("Cannot merge tables from different rooms.")
            )

        target_cluster, _target_table_map = _get_merge_cluster(
            target
        )

        # Prevent importing another merged group
        if len(target_cluster) > 1:
            frappe.throw(
                _(
                    "Cannot merge an already merged table."
                )
            )

        # Prevent occupied table merge
        occupied = frappe.db.get_value(
            "URY Table",
            target,
            "occupied",
        )

        if occupied:
            frappe.throw(
                _("Occupied tables cannot be merged.")
            )

        cluster.add(target)

    if _count_separate_active_orders(cluster) > 1:
        frappe.throw(
            _(
                "Cannot merge tables with separate active orders."
            )
        )

    cluster = sorted(cluster)

    # Make relationships symmetric
    for table in cluster:

        partners = [
            t
            for t in cluster
            if t != table
        ]

        frappe.db.set_value(
            "URY Table",
            table,
            "merged_with",
            ",".join(partners)
            if partners
            else None,
            update_modified=False,
        )

    # Sync order only to selected cluster
    _sync_active_order_with_merge_cluster(
        anchor_table
    )

    _reconcile_open_invoices_for_tables(
        cluster
    )

    frappe.db.commit()

    return True
def _append_merged_partner(table_name, partner):
    merged = frappe.db.get_value(
        "URY Table",
        table_name,
        "merged_with"
    ) or ""
    partners = _parse_merged_with(merged)
    if partner not in partners:
        partners.append(partner)
    frappe.db.set_value(
        "URY Table",
        table_name,
        "merged_with",
        ",".join(sorted(set(partners))),
    )

def _sync_active_order_with_merge_cluster(table):

    members = _get_cluster_table_names(table)

    invoices = frappe.get_all(
        "POS Invoice",
        filters={
            "docstatus": 0,
            "invoice_printed": 0,
        },
        fields=[
            "name",
            "restaurant_table",
            "creation",
        ],
    )

    active = [
        x
        for x in invoices
        if x.restaurant_table in members
    ]

    if not active:
        return

    primary = sorted(
        active,
        key=lambda x: x.creation
    )[0]

    merged_tables = ",".join(
        sorted(
            [
                x
                for x in members
                if x != primary.restaurant_table
            ]
        )
    )

    frappe.db.set_value(
        "POS Invoice",
        primary.name,
        "custom_merged_tables",
        merged_tables,
        update_modified=False,
    )

    for table_name in members:

        frappe.db.set_value(
            "URY Table",
            table_name,
            {
                "occupied": 1,
                "latest_invoice_time": primary.creation,
            },
        )

def _parse_merged_with(merged_with):
    if not merged_with:
        return []
    return [partner.strip() for partner in merged_with.split(",") if partner.strip()]


def _table_has_active_order(table_name):
    return bool(
        frappe.db.exists(
            "POS Invoice",
            {
                "docstatus": 0,
                "restaurant_table": table_name,
                "invoice_printed": 0,
            },
        )
    )


def _count_separate_active_orders(table_names):
    return sum(1 for name in table_names if _table_has_active_order(name))


def _get_merge_cluster(table):
    room = frappe.db.get_value("URY Table", table, "restaurant_room")
    if not room:
        frappe.throw(_("Table not found."))

    room_tables = frappe.get_all(
        "URY Table",
        filters={"restaurant_room": room},
        fields=["name", "merged_with", "occupied"],
    )
    table_by_name = {row.name: row for row in room_tables}

    if table not in table_by_name:
        frappe.throw(_("Table not found."))

    visited = set()
    members = []
    queue = [table]

    while queue:
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        members.append(name)

        row = table_by_name.get(name)
        if not row:
            continue

        for partner in _parse_merged_with(row.merged_with):
            if partner in table_by_name and partner not in visited:
                queue.append(partner)

    return members, table_by_name


def _get_cluster_table_names(table):
    if not table:
        return []
    try:
        members, _ = _get_merge_cluster(table)
        return members
    except Exception:
        return [table]


def _merged_partners_for_primary(primary_table):
    if not primary_table:
        return []
    members = _get_cluster_table_names(primary_table)
    return sorted(name for name in members if name != primary_table)


def _merged_partners_csv(primary_table):
    partners = _merged_partners_for_primary(primary_table)
    return ",".join(partners) if partners else None


def _normalize_merged_partners_csv(csv_value):
    if not csv_value:
        return []
    return sorted(_parse_merged_with(csv_value))


def _reconcile_invoice_merged_tables(invoice, persist=False):
    """Align custom_merged_tables on an invoice with the live table merge cluster."""
    primary = invoice.get("restaurant_table")
    if not primary:
        return invoice

    expected = _merged_partners_for_primary(primary)
    current = _normalize_merged_partners_csv(invoice.get("custom_merged_tables"))

    if expected == current:
        return invoice

    partners_value = ",".join(expected) if expected else None
    invoice.custom_merged_tables = partners_value

    if persist and invoice.get("name"):
        frappe.db.set_value(
            "POS Invoice",
            invoice.name,
            "custom_merged_tables",
            partners_value,
            update_modified=False,
        )

    return invoice


def _open_invoice_names_for_table(table):
    names = set()
    for row in frappe.get_all(
        "POS Invoice",
        filters={"docstatus": 0, "restaurant_table": table},
        fields=["name"],
    ):
        names.add(row.name)
    for row in frappe.get_all(
        "POS Invoice",
        filters={"docstatus": 0, "custom_merged_tables": ["like", f"%{table}%"]},
        fields=["name"],
    ):
        names.add(row.name)
    return names


def _reconcile_open_invoices_for_tables(table_names):
    seen = set()
    for table in table_names:
        for invoice_name in _open_invoice_names_for_table(table):
            if invoice_name in seen:
                continue
            seen.add(invoice_name)
            invoice = frappe.get_doc("POS Invoice", invoice_name)
            _reconcile_invoice_merged_tables(invoice, persist=True)


TABLE_RELEASE_FIELDS = {
    "occupied": 0,
    "latest_invoice_time": None,
    "merged_with": None,
}


def release_merge_cluster_tables(table_or_tables, commit=True):

    if isinstance(table_or_tables, (list, tuple, set)):
        cluster = list(table_or_tables)
    else:
        cluster = _get_table_group(table_or_tables)

    for member in cluster:
        frappe.db.set_value(
            "URY Table",
            member,
            TABLE_RELEASE_FIELDS,
            update_modified=False,
        )

    if commit:
        frappe.db.commit()

@frappe.whitelist()
def release_tables_after_print(invoice):
    """Compatibility endpoint: printing only marks the invoice as printed."""
    frappe.db.set_value(
        "POS Invoice",
        invoice,
        "invoice_printed",
        1,
        update_modified=False,
    )

    frappe.db.commit()

    return True


def _has_open_pos_invoices_for_cluster(tables):

    if not tables:
        return False

    for table in tables:

        invoices = frappe.get_all(
            "POS Invoice",
            filters={
                "docstatus": 0,
                "restaurant_table": table,
            },
            fields=[
                "name",
                "invoice_printed",
            ],
        )

        for inv in invoices:
            if inv.invoice_printed == 0:
                return True


        merged = frappe.get_all(
            "POS Invoice",
            filters={
                "docstatus": 0,
                "custom_merged_tables": ["like", f"%{table}%"],
            },
            fields=[
                "name",
                "invoice_printed",
            ],
        )

        for inv in merged:

            if inv.invoice_printed == 0:
                return True

    return False


@frappe.whitelist()
def unmerge_tables(table):

    cluster = _get_cluster_table_names(
        table
    )

    if len(cluster) <= 1:
        frappe.throw(
            _("Table is not merged.")
        )

    if _has_open_pos_invoices_for_cluster(
        cluster
    ):
        frappe.throw(
            _("Cannot unmerge active tables.")
        )

    for member in cluster:

        frappe.db.set_value(
            "URY Table",
            member,
            "merged_with",
            None,
        )

    frappe.db.commit()

    return True


def _get_table_group(restaurant_table, custom_merged_tables=None):
    tables = _get_cluster_table_names(restaurant_table)
    if custom_merged_tables:
        for table_name in custom_merged_tables.split(","):
            table_name = table_name.strip()
            if table_name and table_name not in tables:
                tables.append(table_name)
    return tables


def _has_open_pos_invoices_for_tables(tables):
    return _has_open_pos_invoices_for_cluster(tables)


def _free_tables_if_no_open_invoices(
    restaurant_table,
    custom_merged_tables=None,
):

    if not restaurant_table:
        return

    tables = _get_table_group(
        restaurant_table,
        custom_merged_tables,
    )

    if _has_open_pos_invoices_for_cluster(tables):
        return

    release_merge_cluster_tables(tables)


def _copy_invoice_item_fields(item_row, qty):
    return dict(
        item_code=item_row.item_code,
        item_name=item_row.item_name,
        qty=qty,
        rate=item_row.rate,
        price_list_rate=item_row.price_list_rate,
        base_price_list_rate=item_row.base_price_list_rate,
        comment=item_row.get("comment"),
        custom_course=item_row.get("custom_course"),
        custom_ury_price_option=item_row.get(OPTION_FIELD),
        custom_ury_price_option_label=item_row.get(OPTION_LABEL_FIELD),
        cost_center=item_row.cost_center,
        uom=item_row.uom,
        conversion_factor=item_row.conversion_factor,
        warehouse=item_row.warehouse,
    )


@frappe.whitelist()
def split_bill(source_invoice, items_to_move, customer=None):
    """Move selected line items from a printed draft bill to a new sibling POS Invoice."""
    if isinstance(items_to_move, str):
        items_to_move = json.loads(items_to_move)

    source = frappe.get_doc("POS Invoice", source_invoice)

    if source.docstatus != 0:
        frappe.throw(_("Only draft invoices can be split."))

    # A split moves an existing reservation between two invoices. Keep the
    # promotion row locked until both documents reflect the transfer so a
    # concurrent order cannot observe the intermediate partition.
    lock_invoice_price_options(source)

    move_map = {
        row["name"]: float(row["qty"])
        for row in items_to_move
        if row.get("name") and float(row.get("qty", 0)) > 0
    }
    if not move_map:
        frappe.throw(_("Select at least one item to move."))

    total_moving_qty = 0.0
    total_remaining_qty = 0.0
    for item in source.items:
        move_qty = move_map.get(item.name, 0)
        if move_qty > item.qty:
            frappe.throw(
                _("Cannot move more than available quantity for {0}.").format(item.item_name)
            )
        total_moving_qty += move_qty
        total_remaining_qty += item.qty - move_qty

    if total_moving_qty <= 0:
        frappe.throw(_("Select at least one item to move."))
    if total_remaining_qty <= 0:
        frappe.throw(_("At least one item must remain on the original bill."))

    new_invoice = frappe.new_doc("POS Invoice")
    header_fields = [
        "is_pos",
        "update_stock",
        "naming_series",
        "restaurant",
        "branch",
        "restaurant_table",
        "custom_restaurant_room",
        "custom_merged_tables",
        "waiter",
        "cashier",
        "pos_profile",
        "order_type",
        "no_of_pax",
        "customer",
        "customer_name",
        "selling_price_list",
        "taxes_and_charges",
        "company",
        "currency",
        "conversion_rate",
        "price_list_currency",
    ]
    for field in header_fields:
        if source.get(field) is not None:
            new_invoice.set(field, source.get(field))

    split_group = source.get("custom_split_group") or frappe.generate_hash(length=10)
    source.custom_split_group = split_group

    new_invoice.custom_split_from = source.name
    new_invoice.custom_split_group = split_group
    new_invoice.invoice_printed = 0
    new_invoice.invoice_created = 0

    if customer:
        new_invoice.customer = customer
        new_invoice.customer_name = frappe.db.get_value("Customer", customer, "customer_name")
        new_invoice.mobile_number = frappe.db.get_value("Customer", customer, "mobile_no")

    items_to_remove = []
    for item in source.items:
        move_qty = move_map.get(item.name, 0)
        if move_qty <= 0:
            continue
        if move_qty >= item.qty:
            new_invoice.append("items", _copy_invoice_item_fields(item, item.qty))
            items_to_remove.append(item)
        else:
            new_invoice.append("items", _copy_invoice_item_fields(item, move_qty))
            item.qty -= move_qty

    for item in items_to_remove:
        source.remove(item)

    payment_mode = source.payments[0].mode_of_payment if source.payments else None

    frappe.flags.ury_bill_split = True
    try:
        source.set_missing_values()
        source.run_method("set_missing_values")
        source.calculate_taxes_and_totals()

        new_invoice.set_missing_values()
        new_invoice.run_method("set_missing_values")
        new_invoice.calculate_taxes_and_totals()

        if payment_mode and new_invoice.invoice_created == 0:
            new_invoice.append(
                "payments",
                dict(mode_of_payment=payment_mode, amount=new_invoice.rounded_total),
            )
            new_invoice.invoice_created = 1

        new_invoice.insert()
        new_invoice.reload()
        frappe.db.set_value(
            "POS Invoice",
            new_invoice.name,
            {
                "custom_split_from": source.name,
                "custom_split_group": split_group,
            },
            update_modified=False,
        )
        source.save()
    finally:
        frappe.flags.ury_bill_split = False

    return {
        "source_invoice": source.name,
        "new_invoice": new_invoice.name,
    }



@frappe.whitelist()
def get_order_invoice(table=None, invoiceNo=None, order_type=None, is_payment=None):
    """returns the active invoice linked to the given table"""

    return _get_order_invoice(table, invoiceNo, order_type, is_payment)


def _get_order_invoice(
    table=None,
    invoiceNo=None,
    order_type=None,
    is_payment=None,
    preserve_existing_price_list=False,
):
    """Internal variant with waiter-only preservation controls."""

    if table:
        filters = {"docstatus": 0}
        if invoiceNo:
            filters["name"] = invoiceNo
        elif is_payment != "Payments":
            filters["invoice_printed"] = 0
            
        or_filters = {
            "restaurant_table": table,
            "custom_merged_tables": ["like", f"%{table}%"]
        }
        
        invoices = frappe.get_all("POS Invoice", filters=filters, or_filters=or_filters, limit=1)
        invoice_name = invoices[0].name if invoices else None
        branch, menu_name, restaurant = get_restaurant_and_menu_name(table)

        if invoice_name:
            invoice = frappe.get_doc("POS Invoice", invoice_name)

        else:
            invoice = frappe.new_doc("POS Invoice")

            invoice.naming_series = frappe.db.get_value(
                "URY Restaurant", restaurant, "invoice_series_prefix"
            )

            invoice.is_pos = 1
            invoice.update_stock = 1
            invoice.restaurant = restaurant
            invoice.branch = branch

            is_take_away = frappe.db.get_value("URY Table", table, "is_take_away")
            if is_take_away == 1:
                invoice.order_type = "Take Away"
            else:
                invoice.order_type= "Dine In"

        invoice.taxes_and_charges = frappe.db.get_value(
            "URY Restaurant", restaurant, "default_tax_template"
        )

        if not (
            preserve_existing_price_list
            and invoice_name
            and invoice.selling_price_list
        ):
            # Preserve the established desktop POS resolution semantics. The
            # waiter path validates this value against its strict menu lookup.
            invoice.selling_price_list = frappe.db.get_value(
                "Price List", dict(restaurant_menu=menu_name, enabled=1)
            )

        if invoice_name and invoice.restaurant_table:
            _reconcile_invoice_merged_tables(invoice, persist=True)

    else:

        if is_payment == "Payments":
            invoice_name = frappe.get_value(
                "POS Invoice", dict(restaurant_table=table, docstatus=0, name=invoiceNo)
            )
            
        else:
            invoice_name = frappe.get_value(
                "POS Invoice", dict(docstatus=0, name=invoiceNo)
            )
            
        if invoice_name:
            invoice = frappe.get_doc("POS Invoice", invoice_name)
            

        else:
            invoice = frappe.new_doc("POS Invoice")
            invoice.is_pos = 1
            invoice.update_stock = 1
        
        branch = getBranch()
        restaurant = frappe.db.get_value("URY Restaurant", {"branch": branch}, "name")
   
        menu=get_menu_name(order_type)
 
        if (order_type == "Aggregators" and frappe.db.get_value("Branch", branch, "custom_no_taxes") == 0) or order_type != "Aggregators":
            invoice.taxes_and_charges = frappe.db.get_value("URY Restaurant", restaurant, "default_tax_template")
        
        invoice.selling_price_list = frappe.db.get_value(
            "Price List", dict(restaurant_menu=menu, enabled=1)
        )

        if invoice_name and invoice.restaurant_table:
            _reconcile_invoice_merged_tables(invoice, persist=True)

    return invoice


@frappe.whitelist()
def sync_order(
    items,
    cashier,
    owner,
    mode_of_payment,
    customer,
    no_of_pax,
    last_invoice,
    waiter,
    pos_profile,
    last_modified_time=None,
    table=None,
    invoice=None,
    comments=None,
    order_type=None,
    aggregator_id=None,
    room=None,
    merged_tables=None
):
    """Public POS compatibility wrapper around the shared order save service."""
    return _sync_order(
        items=items,
        cashier=cashier,
        owner=owner,
        mode_of_payment=mode_of_payment,
        customer=customer,
        no_of_pax=no_of_pax,
        last_invoice=last_invoice,
        waiter=waiter,
        pos_profile=pos_profile,
        last_modified_time=last_modified_time,
        table=table,
        invoice=invoice,
        comments=comments,
        order_type=order_type,
        aggregator_id=aggregator_id,
        room=room,
        merged_tables=merged_tables,
    )


def _sync_order(
    items,
    cashier,
    owner,
    mode_of_payment,
    customer,
    no_of_pax,
    last_invoice,
    waiter,
    pos_profile,
    last_modified_time=None,
    table=None,
    invoice=None,
    comments=None,
    order_type=None,
    aggregator_id=None,
    room=None,
    merged_tables=None,
    skip_kot=False,
    strict_invoice=False,
    expected_invoice_name=None,
    append_only=False,
    expected_menu=None,
    expected_price_list=None,
    expected_item_prices=None,
):
    """Save an order while preserving the public POS behaviour by default.

    The waiter-only controls are intentionally available only on this private
    Python service. Browser clients cannot set them through the public
    ``sync_order`` endpoint.
    """
    if append_only and (not strict_invoice or not skip_kot):
        frappe.throw(_("Append-only mode requires strict invoice and KOT handling."))
    if append_only and not expected_menu:
        frappe.throw(_("Append-only mode requires an authoritative menu."))
    if append_only and not expected_price_list:
        frappe.throw(_("Append-only mode requires an authoritative Price List."))
    if append_only and not expected_item_prices:
        frappe.throw(_("Append-only mode requires authoritative item prices."))
    
    user_role = frappe.get_roles()
    posprofile, user_branch = get_pos_profile_for_current_branch(pos_profile)
    opening = get_single_cashier_opening(
        posprofile.name,
        required=True,
        for_update=True,
    )
    if opening:
        cashier = opening.user
        owner = opening.user
    
    billing_user = any(
        role.role in user_role for role in posprofile.role_allowed_for_billing
    )

    # Serialize every table order writer, including the desktop POS, before it
    # selects or creates the draft invoice. This prevents a waiter request and a
    # billing user from both treating the same table as free.
    if table:
        locked_tables = frappe.db.sql(
            """
            SELECT name
            FROM `tabURY Table`
            WHERE name = %s AND branch = %s
            FOR UPDATE
            """,
            (table, user_branch),
        )
        if not locked_tables:
            frappe.throw(_("The selected table does not belong to your branch."))

    # Match URY Menu's lock order (menu before open invoice). Price List and
    # Item Price are deliberately validated later, after the invoice lock.
    locked_waiter_menu = None
    waiter_item_codes = []
    if append_only:
        if isinstance(items, str):
            items = json.loads(items)
        waiter_item_codes = list(
            dict.fromkeys(
                item.get("item") for item in items if item.get("item")
            )
        )
        locked_waiter_menu = _get_locked_waiter_menu(
            table,
            room,
            expected_menu,
            waiter_item_codes,
        )

    # Check if the last invoice was already billed
    if (
        last_invoice
        and frappe.db.get_value("POS Invoice", last_invoice, "invoice_printed") == 1
        and (not billing_user)
    ):
        frappe.msgprint(
            title="Invoice Already Billed",
            indicator="red",
            msg=("This order has already been billed. Please reload the page."),
        )
        return {"status": "Failure"}

    requested_invoice = invoice
    invoice = _get_order_invoice(
        table,
        requested_invoice,
        order_type,
        preserve_existing_price_list=append_only,
    )
    if strict_invoice:
        if expected_invoice_name:
            if invoice.is_new() or invoice.name != expected_invoice_name:
                frappe.throw(
                    _("The active table order changed. Reload the table."),
                    frappe.TimestampMismatchError,
                )
        elif not invoice.is_new():
            frappe.throw(
                _("The table is no longer free. Reload the table."),
                frappe.TimestampMismatchError,
            )
    if invoice.branch and invoice.branch != user_branch:
        frappe.throw(
            _("This order does not belong to your branch."),
            frappe.PermissionError,
        )
    if not invoice.branch:
        invoice.branch = user_branch

    if last_invoice and last_modified_time:
        lastModifiedTime = invoice.modified
        from datetime import datetime

        if isinstance(last_modified_time, str):
            try:
                last_modified_time = datetime.strptime(
                    last_modified_time, "%Y-%m-%d %H:%M:%S.%f"
                )
            except ValueError:
                last_modified_time = datetime.strptime(
                    last_modified_time, "%Y-%m-%d %H:%M:%S"
                )
        if isinstance(lastModifiedTime, str):
            try:
                lastModifiedTime = datetime.strptime(
                    lastModifiedTime, "%Y-%m-%d %H:%M:%S.%f"
                )
            except ValueError:
                lastModifiedTime = datetime.strptime(
                    lastModifiedTime, "%Y-%m-%d %H:%M:%S"
                )
        if lastModifiedTime != last_modified_time:
            frappe.msgprint(
                title="Order has been modified",
                indicator="red",
                msg=(
                    "This order has been modified. Please reload the page to retrieve the latest edits."
                ),
            )
            return {"status": "Failure"}
    else:
        if invoice.name and invoice.invoice_printed == 0 and not billing_user:
            frappe.msgprint(
                title="Table occupied ",
                indicator="red",
                msg=("{0} is already occupied . Please refresh the page.").format(
                    table
                ),
            )
            return {"status": "Failure"}

    if not customer:
        frappe.throw("Please enter valid customer details")
    else:
        invoice.customer = customer

    if order_type:
        invoice.order_type = order_type

    customerdoc = frappe.get_doc("Customer", customer)
    invoice.mobile_number = customerdoc.mobile_number
    if append_only:
        invoice.custom_comments = comments or ""
    elif comments:
        invoice.custom_comments = comments
    invoice.no_of_pax = no_of_pax
    set_pos_profile(invoice, pos_profile)
    invoice.cashier = cashier
    invoice.waiter = waiter
    if append_only and invoice.is_new():
        invoice.owner = owner
    invoice.custom_aggregator_id = aggregator_id
    invoice.custom_restaurant_room =room
    if not invoice.restaurant_table:
        invoice.restaurant_table = table

    if invoice.restaurant_table:
        _reconcile_invoice_merged_tables(invoice)
    
    if order_type == "Aggregators":
        price_list = frappe.db.get_value("Aggregator Settings",{"customer": customer, "parent": invoice.branch, "parenttype": "Branch"},"price_list",)
        
        if not price_list:
            frappe.throw(f"Price list for customer {customer} in branch {invoice.branch} not found in Aggregator Settings.")
    else:
        price_list = invoice.selling_price_list
    if append_only and expected_price_list and price_list != expected_price_list:
        frappe.throw(
            _(
                "The existing order Price List no longer matches the active "
                "room menu. Handle this order in the standard POS."
            ),
            title=_("Price List Changed"),
        )

    # dummy payment
    if invoice.invoice_created == 0:
        invoice.append(
            "payments",
            dict(mode_of_payment=mode_of_payment, amount=invoice.grand_total),
        )
        invoice.invoice_created = 1

    past_item = []
    for item in invoice.items:
        previous_item = {
            "item_code": item.item_code,
            "item_name": item.item_name,
            "qty": item.qty,
            "comments": item.get("comment") or "",
        }
        past_item.append(previous_item)
        

    # Conditional checking for 'items' type:
    # - 'ury': JSON passed, hence using isinstance
    # - 'ury_pos': Already formatted list, hence using else
    if isinstance(items, str):
        items = json.loads(items)
    existing_item_snapshot = (
        _snapshot_existing_order_items(invoice)
        if append_only and not invoice.is_new()
        else []
    )
    if not append_only:
        invoice.items = []
    
    if append_only:
        menu = locked_waiter_menu
        # Lock before reading option rows or relying on Item Price values. A
        # later child-only lock would protect a stale pricing snapshot and
        # would not cover creation of the first promotion.
        locked_item_prices = _get_locked_waiter_item_prices(
            menu,
            waiter_item_codes,
            price_list,
            expected_item_prices,
            items=items,
        )
        for item in items:
            item.pop("_expected_option_rate", None)
        appended_items = _append_server_priced_order_items(
            invoice,
            items,
            menu,
            price_list,
            pos_profile,
            authoritative_prices=locked_item_prices,
        )
        appended_item_price_snapshot = _snapshot_appended_item_prices(
            appended_items
        )
    else:
        appended_item_price_snapshot = []
        # Keep the established desktop/takeaway/aggregator behaviour intact:
        # the legacy path resolves the branch menu and uses the first matching
        # Item Price row exactly as it did before the waiter module existed.
        promotion_menu = frappe.db.get_value(
            "Price List", price_list, "restaurant_menu"
        )
        # Course lookup still needs the operational branch menu when a Price
        # List has no explicit menu, but promotions belong only to a Price List
        # explicitly bound to its URY Menu. Preserve the legacy lookup against
        # the invoice branch instead of deriving it again from the session user.
        menu = promotion_menu or frappe.db.get_value(
            "URY Menu", {"branch": invoice.branch}, "name"
        )
        if promotion_menu:
            lock_menu_price_options_parent(promotion_menu)
        requested_item_codes = list(dict.fromkeys(item.get("item") for item in items))
        locked_promotion_base_rates = (
            get_authoritative_item_prices(
                requested_item_codes,
                price_list,
                for_update=True,
            )
            if promotion_menu
            else {}
        )
        promotions_by_item = (
            group_menu_promotions(
                promotion_menu,
                requested_item_codes,
                for_update=True,
            )
            if promotion_menu
            else {}
        )
        for item in items:
            course = frappe.db.get_value(
                "URY Menu Item",
                {"item": item.get("item"), "parent": menu},
                "course",
            )
            if promotion_menu:
                item_rate = flt(locked_promotion_base_rates[item.get("item")])
            else:
                item_prices = frappe.db.get_list(
                    "Item Price",
                    filters={
                        "item_code": item.get("item"),
                        "price_list": price_list,
                    },
                    fields=["price_list_rate"],
                )
                if not item_prices:
                    frappe.throw(
                        _(
                            "No item price found for Item: {0} in Price List: {1}. "
                            "Please check the price list settings."
                        ).format(item.get("item"), price_list)
                    )
                item_rate = flt(item_prices[0].price_list_rate)
            price_option = resolve_price_option(
                promotion_menu,
                item.get("item"),
                item.get("price_option"),
                item_rate,
                promotions_by_item=promotions_by_item,
            )
            row_values = apply_price_option_to_row(
                dict(
                    item_code=item.get("item"),
                    item_name=item.get("item_name"),
                    qty=item.get("qty"),
                    **({"custom_course": course} if course else {}),
                    comment=item.get("comment"),
                    cost_center=frappe.db.get_value(
                        "POS Profile", pos_profile, "cost_center"
                    ),
                ),
                price_option,
            )
            invoice.append("items", row_values)

    # Populate warehouse/UOM conversion before validating the final payload.
    # The current invoice is excluded from draft reservations when it is edited,
    # because ordered_qty already represents its complete replacement state.
    with _temporary_order_actor(opening.user if append_only else None):
        invoice.set_missing_values(for_validate=True)
    _assert_existing_order_items_unchanged(invoice, existing_item_snapshot)
    _assert_appended_item_prices_unchanged(appended_item_price_snapshot)
    ordered_qty = _aggregate_order_stock_qty(invoice.items)
    # Every writer (including a normal-price line) locks the promotion rows
    # before stock bins, so the reserved Normal/Promotion partition cannot race.
    locked_price_options = (
        get_menu_promotions(
            promotion_menu,
            list(ordered_qty),
            enabled_only=False,
            for_update=True,
        )
        if not append_only and promotion_menu
        else get_menu_promotions(
            menu,
            list(ordered_qty),
            enabled_only=False,
            for_update=True,
        )
        if append_only
        else []
    )
    active_promoted_item_codes = [
        row.item for row in locked_price_options if cint(row.enabled)
    ]
    selected_promotional_item_codes = [
        row.get("item_code")
        for row in invoice.items
        if row.get(OPTION_FIELD)
        and row.get(OPTION_FIELD) != STANDARD_OPTION_ID
    ]
    price_option_item_codes = list(
        dict.fromkeys(
            active_promoted_item_codes + selected_promotional_item_codes
        )
    )
    base_rates = (
        get_authoritative_item_prices(
            price_option_item_codes, price_list, for_update=True
        )
        if price_option_item_codes
        else {}
    )
    stock_lock_items = _get_stock_lock_item_codes(ordered_qty)
    locked_bin_qty = _lock_stock_bins(posprofile.warehouse, stock_lock_items)
    exclude_invoice = None if invoice.is_new() else invoice.name
    _validate_order_stock(
        ordered_qty,
        posprofile.warehouse,
        exclude_invoice=exclude_invoice,
        locked_bin_qty=locked_bin_qty,
    )
    if price_option_item_codes:
        physical_details = _get_stock_details(
            price_option_item_codes,
            posprofile.warehouse,
            exclude_invoice=exclude_invoice,
            for_update=True,
        )
        validate_price_option_quantities(
            invoice.items,
            promotion_menu if not append_only else menu,
            base_rates,
            {
                item_code: details["available_qty"]
                for item_code, details in physical_details.items()
            },
            exclude_invoice=exclude_invoice,
        )
        validate_price_option_row_prices(
            invoice.items,
            promotion_menu if not append_only else menu,
            base_rates,
        )

    waiter_price_list_flag = "ury_waiter_expected_price_list"
    previous_waiter_price_list = getattr(
        frappe.flags, waiter_price_list_flag, None
    )
    if append_only:
        setattr(frappe.flags, waiter_price_list_flag, expected_price_list)
    with _temporary_order_actor(opening.user if append_only else None):
        try:
            if append_only:
                invoice.save(ignore_permissions=True)
            else:
                invoice.save()
        except Exception as e:
            frappe.throw(f"Error while updating order: {e}")
        finally:
            setattr(
                frappe.flags,
                waiter_price_list_flag,
                previous_waiter_price_list,
            )
    if price_option_item_codes:
        validate_price_option_row_prices(
            invoice.items,
            promotion_menu if not append_only else menu,
            base_rates,
        )
    if append_only and invoice.selling_price_list != expected_price_list:
        frappe.throw(
            _(
                "The order Price List changed during validation. Handle this "
                "order in the standard POS."
            ),
            title=_("Price List Changed"),
        )
    _assert_existing_order_items_unchanged(invoice, existing_item_snapshot)
    _assert_appended_item_prices_unchanged(appended_item_price_snapshot)
    persisted_appended_item_price_snapshot = (
        _snapshot_persisted_appended_item_prices(
            appended_item_price_snapshot
        )
    )
    if append_only:
        invoice.reload()
        if invoice.selling_price_list != expected_price_list:
            frappe.throw(
                _(
                    "The persisted order Price List changed during validation. "
                    "Handle this order in the standard POS."
                ),
                title=_("Price List Changed"),
            )
        _assert_existing_order_items_unchanged(
            invoice, existing_item_snapshot
        )
        _assert_persisted_appended_item_prices_unchanged(
            invoice, persisted_appended_item_price_snapshot
        )


    if not skip_kot:
        try:
            kot_execute(invoice.name, customer, table, items, past_item, comments)

        except Exception as e:
            # Keep the existing desktop POS behaviour. The waiter API uses the
            # private skip path and handles KOT failures transactionally.
            error_msg = f"KOT Creation Failes {str(e)}"
            frappe.log_error(error_msg, "KOT Error")

    # table status
    if invoice.invoice_printed == 0:
        frappe.db.set_value(
            "URY Table", invoice.restaurant_table, {"occupied": 1, "latest_invoice_time": invoice.creation}
        )
        if invoice.custom_merged_tables:
            for merged_table in invoice.custom_merged_tables.split(","):
                frappe.db.set_value(
                    "URY Table", merged_table.strip(), {"occupied": 1, "latest_invoice_time": invoice.creation}
                )

    return invoice.as_dict()


@frappe.whitelist()
def item_query_restaurant(
    doctype="Item",
    txt="",
    searchfield="name",
    start=0,
    page_len=20,
    filters=None,
    as_dict=False,
):
    """Return items that are selected in active menu of the restaurant"""
    restaurant, menu = get_restaurant_and_menu_name(filters["table"])
    items = frappe.db.get_all("URY Menu Item", ["item"], dict(parent=menu, disabled=0))
    del filters["table"]
    filters["name"] = ("in", [d.item for d in items])

    return item_query("Item", txt, searchfield, start, page_len, filters, as_dict)


@frappe.whitelist()
def get_restaurant_and_menu_name(table):
    if not table:
        frappe.throw(_("Please select a table"))

    restaurant, branch, room = frappe.get_value(
        "URY Table",
        table,
        ["restaurant", "branch", "restaurant_room"],
    )
    room_wise_menu = frappe.db.get_value(
        "URY Restaurant",
        restaurant,
        "room_wise_menu",
    )

    if not room_wise_menu:
        menu = frappe.db.get_value("URY Restaurant", restaurant, "active_menu")
    else:
        menu = frappe.db.get_value(
            "Menu for Room",
            {"parent": restaurant, "room": room},
            "menu",
        )

    if not menu:
        frappe.throw(
            _("Please set an active menu for Restaurant {0}").format(restaurant)
        )

    return branch, menu, restaurant

@frappe.whitelist()
def get_menu_name(order_type):
    branch = getBranch()
    restaurant = frappe.get_value(
        "URY Restaurant",
        {"branch": branch},
        "name",
    )
    order_type_wise_menu = frappe.db.get_value(
            "URY Restaurant", restaurant, "order_type_wise_menu"
        )
    
    if order_type_wise_menu:
        menu = frappe.db.get_value(
            "Order Type Menu",
            {"parent": restaurant, "order_type": order_type},
            "menu"
        )
        if not menu:
            menu = frappe.db.get_value("URY Restaurant", restaurant, "active_menu")
    else:
        menu = frappe.db.get_value("URY Restaurant", restaurant, "active_menu")   
    return menu  
    

@frappe.whitelist()
def pos_opening_check():
    
    user = frappe.session.user
    # Handle the administrator case differently
    if user == "Administrator":
        return {
            "opening_exists": False,  # Assuming no POS opening entry is needed for Administrator
            "cashier": None,
            "pos_profile": None,
        }
    
    details = getBranchRoom()
    room = details[0].get('name')    # 'Beach'
    branch = details[0].get('branch') # 'Beach'
    
    pos_opening_list = frappe.db.sql("""
        SELECT DISTINCT `tabPOS Opening Entry`.name 
        FROM `tabPOS Opening Entry`
        INNER JOIN `tabMultiple Rooms` 
        ON `tabMultiple Rooms`.parent = `tabPOS Opening Entry`.name
        WHERE `tabPOS Opening Entry`.branch = %s
        AND `tabPOS Opening Entry`.status = 'Open'
        AND `tabPOS Opening Entry`.docstatus = 1
        AND `tabMultiple Rooms`.room = %s
    """, (branch, room), as_dict=True)
    
    
    result = {
        "opening_exists": len(pos_opening_list) > 0,
        "cashier": None,
        "pos_profile": None,
    }

    if result["opening_exists"]:
        # If POS opening entry exists, fetch the cashier from the first entry
        opening_entry = frappe.get_doc("POS Opening Entry", pos_opening_list[0].name)
        result["cashier"] = (
            opening_entry.user
        )  # Fetch values from POS Profile linked to POS Opening Entry
        result["pos_profile"] = opening_entry.pos_profile
        
    return result


@frappe.whitelist()
def table_transfer(table, newTable, invoice):
    current_table = frappe.get_doc("URY Table", table)
    pos_invoice = frappe.get_doc("POS Invoice", invoice)
    new_table = frappe.get_doc("URY Table", newTable)

    merge_members, _table_map = _get_merge_cluster(table)
    if len(merge_members) > 1:
        frappe.throw(_("Table transfer is not allowed for merged tables. Unmerge first."))

    if current_table.branch != new_table.branch:
        frappe.throw(_("Table transfer between different branches is restricted."))

    if new_table.occupied == 1:
        frappe.throw(f"Table {new_table.name} is already occupied")

    frappe.db.set_value(
        "URY Table",
        new_table.name,
        {"occupied": 1, "latest_invoice_time": pos_invoice.creation},
    )
    frappe.db.set_value(
        "URY Table",
        current_table.name,
        {"occupied": 0, "latest_invoice_time": None},
    )

    pos_invoice.restaurant_table = new_table.name
    pos_invoice.custom_restaurant_room = new_table.restaurant_room
    pos_invoice.save()

    try:
        change_table_in_kot(
            pos_invoice.name, new_table.name, pos_invoice.branch
        )
    except Exception:
        pass


@frappe.whitelist()
def captain_transfer(currentCaptain, newCaptain, invoice):
    pos_profile=frappe.get_value("POS Invoice", invoice,"pos_profile")
    multiple_cashier = frappe.db.get_value("POS Profile",pos_profile,"custom_enable_multiple_cashier")
    branch=frappe.get_value("POS Invoice", invoice,"branch")
    if multiple_cashier:
        table=pos_profile=frappe.get_value("POS Invoice", invoice,"restaurant_table")
        current_room = frappe.get_value("URY Table", table,"restaurant_room")
        new_captain_room =  frappe.db.sql("""
                SELECT room
                FROM `tabURY User`
                WHERE parent=%s AND user=%s         
            """,(branch,newCaptain),as_dict=True)
        room_match = any(room['room'] == current_room for room in new_captain_room)
        if not room_match:
            frappe.throw(_("Captain transfer is not allowed between different rooms"))
        else:
            current_captain_doc = frappe.get_doc("User", currentCaptain)
            pos_invoice = frappe.get_doc("POS Invoice", invoice)
            new_captain_doc = frappe.get_doc("User", newCaptain)

            # Update the waiter field of the POS Invoice
            pos_invoice.waiter = new_captain_doc.name
            pos_invoice.save()

    else:
        current_captain_doc = frappe.get_doc("User", currentCaptain)
        pos_invoice = frappe.get_doc("POS Invoice", invoice)
        new_captain_doc = frappe.get_doc("User", newCaptain)

        # Update the waiter field of the POS Invoice
        pos_invoice.waiter = new_captain_doc.name
        pos_invoice.save()


@frappe.whitelist()
def customer_favourite_item(customer_name):
    pos = frappe.db.get_list(
        "POS Invoice", filters={"customer": customer_name}, fields=["name"]
    )

    item_qty = {}

    for invoice in pos:
        pos_invoice = frappe.get_doc("POS Invoice", invoice)
        for item in pos_invoice.items:
            item_name = item.item_name
            item_qty[item_name] = item_qty.get(item_name, 0) + item.qty

    result = [
        {"item_name": item_name, "qty": qty}
        for item_name, qty in item_qty.items()
        if qty > 1
    ]
    result = sorted(result, key=lambda x: x["qty"], reverse=True)[:3]

    return result


@frappe.whitelist()
def cancel_order(invoice_id, reason):
    pos_invoice = frappe.get_doc("POS Invoice", invoice_id)
    pos_invoice.check_permission("write")
    if pos_invoice.docstatus != 0:
        frappe.throw(_("Only a draft POS Invoice can be cancelled from Orders."))
    if pos_invoice.get("custom_ury_settlement"):
        frappe.throw(
            _(
                "A commercially settled POS Invoice cannot be cancelled from Orders."
            )
        )
    if not (reason or "").strip():
        frappe.throw(_("A cancellation reason is required."))

    # Use the same promotion-row lock as order creation. This prevents a
    # concurrent order from calculating its quota while this invoice is being
    # released.
    lock_invoice_price_options(pos_invoice)

    # Release the full merge cluster, not only the primary table and CSV partners.
    if pos_invoice.restaurant_table:
        release_merge_cluster_tables(
            pos_invoice.restaurant_table,
            commit=False,
        )

    try:
        cancel_kot(invoice_id)

    except Exception as e:
        # If an exception occurs (e.g., "kot" app not found), it will be caught here without effecting execution
        pass

    # Update invoice status
    frappe.db.sql("""
        UPDATE `tabPOS Invoice Item`
        SET docstatus = 2
        WHERE parent = %s
    """, (invoice_id,))

    frappe.db.set_value("POS Invoice", invoice_id, "docstatus", 2)
    frappe.db.set_value("POS Invoice", invoice_id, "status", "Cancelled")
    frappe.db.set_value("POS Invoice", invoice_id, "cancel_reason", reason)

# Method for URY POS
@frappe.whitelist()
def make_invoice(customer, payments, cashier, pos_profile,owner, additionalDiscount=None, table=None, invoice=None):
    order_type =  invoice_name = frappe.get_value("POS Invoice",invoice , "order_type")
    invoice = get_order_invoice(table, invoice, order_type, "Payments")

    # Once the audited checkout is enabled, the legacy endpoint must fail
    # before changing customer, discounts or payments.  This also closes the
    # old /urypos route and direct-API bypass for merged/short settlements.
    if frappe.db.get_value(
        "POS Profile",
        invoice.pos_profile,
        "custom_ury_enable_commercial_checkout",
    ):
        frappe.throw(
            _(
                "This POS Profile requires the URY commercial checkout. "
                "Reload the current POS and complete the order there."
            )
        )

    if table:
        restaurant = get_restaurant_and_menu_name(table)
        invoice.restaurant = restaurant

    invoice.customer = customer
    set_pos_profile(invoice, pos_profile)
    invoice.additional_discount_percentage=additionalDiscount
    invoice.calculate_taxes_and_totals()

    invoice.set("payments", [])

    if invoice.custom_merged_pos_invoice:
        target = frappe.get_doc("POS Invoice", invoice.custom_merged_pos_invoice)
        target.calculate_taxes_and_totals()
        
        doc_req = invoice.rounded_total
        target_req = target.rounded_total
        
        target.set("payments", [])
        
        for d in payments:
            amt = float(d["amount"])
            mode = d["mode_of_payment"]
            
            if doc_req > 0:
                give = min(amt, doc_req)
                invoice.append("payments", dict(mode_of_payment=mode, amount=give))
                amt -= give
                doc_req -= give
                
            if target_req > 0 and amt > 0:
                give = min(amt, target_req)
                target.append("payments", dict(mode_of_payment=mode, amount=give))
                amt -= give
                target_req -= give
                
        target.flags.ignore_payment_sync = True
        
        frappe.flags.in_bill_merge_sync = True
        try:
            target.save(ignore_permissions=True)
        finally:
            frappe.flags.in_bill_merge_sync = False
            
        invoice.flags.ignore_payment_sync = True
    else:
        for d in payments:
            invoice.append(
                "payments", dict(mode_of_payment=d["mode_of_payment"], amount=d["amount"])
            )

    invoice.save()
    try:
        invoice.submit()
    except Exception as e:
        frappe.throw(f"Error while settling order: {e}")
        
    # Free the table when no other open drafts remain on this table group

    release_invoice = invoice

    # If this invoice is a secondary merged bill,
    # resolve the primary invoice that owns the tables.
    if (
        not release_invoice.restaurant_table
        and release_invoice.custom_merged_pos_invoice
    ):
        release_invoice = frappe.get_doc(
            "POS Invoice",
            release_invoice.custom_merged_pos_invoice,
        )

    if release_invoice.restaurant_table:
        _free_tables_if_no_open_invoices(
            release_invoice.restaurant_table,
            release_invoice.custom_merged_tables,
        )
        
        

# Cancel KOT Doc Creation
def cancel_kot(invoice_id):

    pos_invoice = frappe.get_doc("POS Invoice", invoice_id)
    pos_profile_id = pos_invoice.pos_profile
    pos_profile = frappe.get_doc("POS Profile", pos_profile_id)
    kot_naming_series = pos_profile.custom_kot_naming_series
    cancel_kot_naming_series = "CNCL-" + kot_naming_series

    items = []
    # Create a list of items for the canceled KOT
    for item in pos_invoice.items:
        order_item = {
            "item_code": item.get("item", item.get("item_code")),
            "qty": item.qty,
            "item_name": item.item_name,
            "comments": item.get("comment") or "",
        }
        items.append(order_item)

    if pos_invoice.restaurant_table:
        restaurant_table = pos_invoice.restaurant_table
    else:
        restaurant_table = None

    # Process items for a canceled KOT
    process_items_for_cancel_kot(
        invoice_id,
        pos_invoice.customer,
        restaurant_table,
        items,
        "",
        pos_profile_id,
        cancel_kot_naming_series,
        "Cancelled",
        items,
    )

    # Set the KOTs associated with the invoice as canceled
    kot_list = frappe.db.get_list(
        "URY KOT",
        filters={
            "invoice": invoice_id,
            "type": ("in", ("New Order", "Order Modified")),
            "docstatus": 1,
        },
        fields=("*"),
    )

    for item in kot_list:
        kot_doc = frappe.get_doc("URY KOT", item.name)
        kot_doc.docstatus = 2
        kot_doc.save()


def change_table_in_kot(invoice, new_table, branch):
    # Get a list of KOTs associated with the POS Invoice
    kot_list = frappe.get_all(
        "URY KOT",
        filters={
            "invoice": invoice,
            "docstatus": 1,
            "order_status": "Ready For Prepare",
            "verified": 0,
        },
    )

    # Update each KOT's restaurant_table and send a real-time update
    for kot in kot_list:
        frappe.db.set_value("URY KOT", kot.name, "restaurant_table", new_table)
        production = frappe.db.get_value("URY KOT", kot.name, "production")
        kot_channel = "{}_{}_{}".format("kot_update", branch, production)
        frappe.publish_realtime(kot_channel)
