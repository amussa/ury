"""Server-authoritative, quantity-limited menu price options.

Promotions are an URY feature. ERPNext keeps one normal Item Price and remains
untouched; the option id and label are merely snapshotted on invoice rows.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt


OPTION_FIELD = "custom_ury_price_option"
OPTION_LABEL_FIELD = "custom_ury_price_option_label"
STANDARD_OPTION_ID = "standard"


def lock_menu_price_options_parent(menu):
    """Lock the menu row before any of its price-option child rows.

    The parent lock also serialises the first option inserted for an item: a
    child-only ``FOR UPDATE`` cannot lock a row which does not exist yet.
    """
    if not menu:
        return None
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabURY Menu`
        WHERE name = %s
        FOR UPDATE
        """,
        menu,
    )
    if not rows:
        frappe.throw(
            _("Menu {0} no longer exists. Reload and retry.").format(
                frappe.bold(menu)
            )
        )
    return menu


def get_menu_promotions(menu, item_codes=None, enabled_only=True, for_update=False):
    filters = {
        "parent": menu,
        "parenttype": "URY Menu",
        "parentfield": "price_options",
    }
    if enabled_only:
        filters["enabled"] = 1
    if item_codes is not None:
        item_codes = list(dict.fromkeys(code for code in item_codes if code))
        if not item_codes:
            return []
        filters["item"] = ["in", item_codes]

    if not for_update:
        return frappe.get_all(
            "URY Menu Price Option",
            filters=filters,
            fields=[
                "name", "parent", "item", "item_name", "label", "rate",
                "allocated_qty", "enabled", "idx",
            ],
            order_by="idx asc, name asc",
        )

    # Always acquire locks in parent -> children order. Besides reducing
    # deadlocks, the parent row covers the zero-child/first-promotion case.
    lock_menu_price_options_parent(menu)
    conditions = [
        "parent = %(menu)s",
        "parenttype = 'URY Menu'",
        "parentfield = 'price_options'",
    ]
    if enabled_only:
        conditions.append("enabled = 1")
    values = {"menu": menu}
    if item_codes is not None:
        conditions.append("item IN %(item_codes)s")
        values["item_codes"] = tuple(item_codes)
    return frappe.db.sql(
        f"""
        SELECT name, parent, item, item_name, label, rate,
               allocated_qty, enabled, idx
        FROM `tabURY Menu Price Option`
        WHERE {" AND ".join(conditions)}
        ORDER BY name
        FOR UPDATE
        """,
        values,
        as_dict=True,
    )


def group_menu_promotions(menu, item_codes=None, for_update=False):
    grouped = defaultdict(list)
    for row in get_menu_promotions(
        menu,
        item_codes=item_codes,
        for_update=for_update,
    ):
        grouped[row.item].append(row)
    return dict(grouped)


def lock_invoice_price_options(invoice):
    """Lock every promotion that can affect an invoice before releasing it.

    Normal-price rows matter too: cancelling one changes the physical stock
    partition available to Normal versus Promotion. Resolving through the
    invoice Price List therefore locks all active options for its item codes.
    """
    item_codes = list(
        dict.fromkeys(
            item.get("item_code") for item in invoice.get("items", []) if item.get("item_code")
        )
    )
    if not item_codes:
        return []

    menu = None
    if invoice.get("selling_price_list"):
        menu = frappe.db.get_value(
            "Price List", invoice.selling_price_list, "restaurant_menu"
        )
    if menu:
        return get_menu_promotions(
            menu,
            item_codes=item_codes,
            enabled_only=False,
            for_update=True,
        )

    # Defensive fallback for historical invoices whose Price List is no longer
    # linked to a menu. Promotion ids snapshotted on their child rows remain
    # sufficient to serialize quota release.
    option_ids = sorted(
        {
            item.get(OPTION_FIELD)
            for item in invoice.get("items", [])
            if item.get(OPTION_FIELD)
            and item.get(OPTION_FIELD) != STANDARD_OPTION_ID
        }
    )
    if not option_ids:
        return []

    # Historical invoices can outlive the Price List -> menu link. Resolve the
    # snapshotted option parents without a lock, then lock every parent in a
    # stable order before locking the child rows themselves.
    option_parents = frappe.get_all(
        "URY Menu Price Option",
        filters={"name": ["in", option_ids]},
        fields=["name", "parent"],
    )
    for menu_name in sorted({row.parent for row in option_parents if row.parent}):
        lock_menu_price_options_parent(menu_name)
    return frappe.db.sql(
        """
        SELECT name
        FROM `tabURY Menu Price Option`
        WHERE name IN %(option_ids)s
        ORDER BY name
        FOR UPDATE
        """,
        {"option_ids": tuple(option_ids)},
        as_dict=True,
    )


def _get_used_promotion_qty(
    option_ids,
    exclude_invoice=None,
    for_update=False,
):
    if not option_ids:
        return {}
    conditions = [
        "invoice.name = item.parent",
        """(
            (invoice.docstatus = 0 AND item.docstatus = 0
             AND COALESCE(invoice.is_return, 0) = 0)
            OR (invoice.docstatus = 1 AND item.docstatus = 1)
        )""",
        f"item.`{OPTION_FIELD}` IN %(option_ids)s",
    ]
    values = {"option_ids": tuple(option_ids)}
    if exclude_invoice:
        conditions.append("invoice.name != %(exclude_invoice)s")
        values["exclude_invoice"] = exclude_invoice

    lock_clause = "FOR UPDATE" if for_update else ""
    rows = frappe.db.sql(
        f"""
        SELECT invoice.name AS invoice_name,
               item.name AS item_name,
               item.`{OPTION_FIELD}` AS price_option,
               item.stock_qty AS used_qty
        FROM `tabPOS Invoice` invoice
        INNER JOIN `tabPOS Invoice Item` item ON invoice.name = item.parent
        WHERE {" AND ".join(conditions)}
        ORDER BY invoice.name, item.name
        {lock_clause}
        """,
        values,
        as_dict=True,
    )
    used = {}
    for row in rows:
        # Promotional allocations count units sold and are intentionally not
        # recycled by returns. This keeps cancellation of a submitted return
        # from making an already reused quota exceed its allocation.
        used[row.price_option] = flt(used.get(row.price_option)) + max(
            0.0, flt(row.used_qty)
        )
    return used


def get_item_price_options(
    menu,
    base_rates,
    physical_availability,
    item_codes=None,
    exclude_invoice=None,
    lock=False,
):
    """Build Normal/Promotion payloads and partition the physical availability."""
    promotions_by_item = group_menu_promotions(
        menu,
        item_codes=item_codes,
        for_update=lock,
    )
    promotions = [row for rows in promotions_by_item.values() for row in rows]
    used = _get_used_promotion_qty(
        [row.name for row in promotions],
        exclude_invoice=exclude_invoice,
        for_update=lock,
    )

    result = {}
    for item_code, item_promotions in promotions_by_item.items():
        if len(item_promotions) > 1:
            frappe.throw(
                _("Item {0} has more than one active promotion.").format(
                    frappe.bold(item_code)
                )
            )
        promotion = item_promotions[0]
        if item_code not in base_rates:
            frappe.throw(
                _("The normal price is missing for promoted item {0}.").format(
                    frappe.bold(item_code)
                )
            )
        total_available = max(0.0, flt(physical_availability.get(item_code)))
        used_qty = max(0.0, flt(used.get(promotion.name)))
        promotion_remaining = max(
            0.0,
            flt(promotion.allocated_qty) - used_qty,
        )
        promotion_remaining = min(flt(promotion.allocated_qty), promotion_remaining)
        promotion_available = min(total_available, promotion_remaining)
        normal_available = max(0.0, total_available - promotion_available)
        result[item_code] = [
            {
                "id": STANDARD_OPTION_ID,
                "label": _("Normal"),
                "rate": flt(base_rates[item_code]),
                "available_qty": normal_available,
                "is_default": True,
            },
            {
                "id": promotion.name,
                "label": _(promotion.label),
                "rate": flt(promotion.rate),
                "available_qty": promotion_available,
                "is_default": False,
            },
        ]
    return result


def resolve_price_option(menu, item_code, option_id, base_rate, promotions_by_item=None):
    if promotions_by_item is None:
        promotions_by_item = group_menu_promotions(menu, [item_code])
    promotions = promotions_by_item.get(item_code, [])
    if not promotions:
        if option_id and option_id != STANDARD_OPTION_ID:
            frappe.throw(
                _("The selected price option is not valid for item {0}.").format(
                    frappe.bold(item_code)
                )
            )
        return frappe._dict(
            id=None,
            label=None,
            rate=flt(base_rate),
            base_rate=flt(base_rate),
            is_standard=True,
        )

    invalid_promotion = next(
        (
            promotion
            for promotion in promotions
            if flt(promotion.rate) >= flt(base_rate)
        ),
        None,
    )
    if invalid_promotion:
        frappe.throw(
            _(
                "Promotional price for {0} is no longer lower than its normal "
                "price. Update the menu before selling this item."
            ).format(frappe.bold(item_code)),
            title=_("Invalid Promotional Price"),
        )

    if not option_id or option_id == STANDARD_OPTION_ID:
        return frappe._dict(
            id=STANDARD_OPTION_ID,
            label="Normal",
            rate=flt(base_rate),
            base_rate=flt(base_rate),
            is_standard=True,
        )

    promotion = next((row for row in promotions if row.name == option_id), None)
    if not promotion:
        frappe.throw(
            _("The selected price option is not valid for item {0}.").format(
                frappe.bold(item_code)
            )
        )
    return frappe._dict(
        id=promotion.name,
        label=promotion.label,
        rate=flt(promotion.rate),
        base_rate=flt(base_rate),
        is_standard=False,
    )


def apply_price_option_to_row(row, option):
    row.update(
        {
            "rate": flt(option.rate),
            "price_list_rate": flt(option.base_rate),
            "base_price_list_rate": flt(option.base_rate),
            OPTION_FIELD: option.id,
            OPTION_LABEL_FIELD: option.label,
        }
    )
    return row


def validate_price_option_quantities(
    invoice_items,
    menu,
    base_rates,
    physical_availability,
    exclude_invoice=None,
):
    """Validate the final invoice partition while promotion rows are locked."""
    item_codes = list(
        dict.fromkeys(item.get("item_code") for item in invoice_items if item.get("item_code"))
    )
    options_by_item = get_item_price_options(
        menu,
        base_rates,
        physical_availability,
        item_codes=item_codes,
        exclude_invoice=exclude_invoice,
        lock=True,
    )
    requested = defaultdict(float)
    for item in invoice_items:
        item_code = item.get("item_code")
        option_id = item.get(OPTION_FIELD) or STANDARD_OPTION_ID
        conversion_factor = flt(item.get("conversion_factor")) or 1
        requested[(item_code, option_id)] += flt(item.get("qty")) * conversion_factor

    for item_code in item_codes:
        options = options_by_item.get(item_code, [])
        valid_ids = {option["id"] for option in options}
        valid_ids.add(STANDARD_OPTION_ID)
        invalid = [
            option_id
            for code, option_id in requested
            if code == item_code and option_id not in valid_ids
        ]
        if invalid:
            frappe.throw(
                _("The selected price option for {0} is no longer valid. Reload the menu.").format(
                    frappe.bold(item_code)
                )
            )
        if not options:
            continue
        for option in options:
            requested_qty = flt(requested.get((item_code, option["id"])))
            available_qty = flt(option["available_qty"])
            if requested_qty > available_qty + 1e-9:
                frappe.throw(
                    _("Only {0} unit(s) remain for {1} - {2}; {3} were requested.").format(
                        available_qty,
                        frappe.bold(item_code),
                        frappe.bold(option["label"]),
                        requested_qty,
                    ),
                    title=_("Price Option Sold Out"),
                )


def validate_price_option_row_prices(invoice_items, menu, base_rates):
    """Ensure ERPNext validation did not rewrite an URY-selected price."""
    promoted_codes = list(base_rates)
    promotions_by_item = group_menu_promotions(
        menu, promoted_codes, for_update=True
    )
    changed = []
    for item in invoice_items:
        item_code = item.get("item_code")
        raw_option_id = item.get(OPTION_FIELD)
        persisted_label = item.get(OPTION_LABEL_FIELD)
        if not raw_option_id and persisted_label:
            changed.append(item_code)
            continue
        if item_code not in promotions_by_item:
            persisted_option_id = raw_option_id
            if persisted_option_id and persisted_option_id != STANDARD_OPTION_ID:
                changed.append(item_code)
            elif (
                persisted_option_id == STANDARD_OPTION_ID
                and persisted_label not in (None, "", "Normal")
            ):
                changed.append(item_code)
            continue
        persisted_option_id = raw_option_id or STANDARD_OPTION_ID
        option = resolve_price_option(
            menu,
            item_code,
            persisted_option_id,
            base_rates[item_code],
            promotions_by_item=promotions_by_item,
        )
        expected_label = option.label
        if (
            flt(item.get("rate")) != flt(option.rate)
            or flt(item.get("price_list_rate")) != flt(option.base_rate)
            or persisted_option_id != (option.id or STANDARD_OPTION_ID)
            or (
                persisted_option_id != STANDARD_OPTION_ID
                and persisted_label != expected_label
            )
            or (
                persisted_option_id == STANDARD_OPTION_ID
                and persisted_label not in (None, "", "Normal")
            )
        ):
            changed.append(item_code)
    if changed:
        frappe.throw(
            _("Price option changed while validating: {0}. Reload the menu.").format(
                ", ".join(frappe.bold(code) for code in sorted(set(changed)))
            ),
            title=_("Price Option Changed"),
        )


def _has_promotional_snapshot(items):
    return any(
        item.get(OPTION_FIELD)
        and item.get(OPTION_FIELD) != STANDARD_OPTION_ID
        for item in items
    )


def _get_pos_invoice_menu(invoice):
    price_list = invoice.get("selling_price_list")
    if not price_list:
        return None
    return frappe.db.get_value("Price List", price_list, "restaurant_menu")


def _get_pos_profile_warehouse(invoice):
    warehouse = invoice.get("set_warehouse")
    if not warehouse and invoice.get("pos_profile"):
        warehouse = frappe.db.get_value(
            "POS Profile", invoice.pos_profile, "warehouse"
        )
    if not warehouse:
        warehouse = next(
            (item.get("warehouse") for item in invoice.get("items", []) if item.get("warehouse")),
            None,
        )
    if not warehouse:
        frappe.throw(
            _("A warehouse is required to validate promotional quantities."),
            title=_("Missing Warehouse"),
        )
    return warehouse


def _commercial_item_snapshot(items):
    """Return the persisted fields that affect promotion identity or quota."""
    return sorted(
        (
            item.get("name") or "",
            item.get("item_code") or "",
            item.get(OPTION_FIELD) or STANDARD_OPTION_ID,
            item.get(OPTION_LABEL_FIELD) or "",
            flt(item.get("qty")),
            flt(item.get("stock_qty")),
            flt(item.get("rate")),
            flt(item.get("price_list_rate")),
        )
        for item in items
    )


def _submitted_promotion_is_unchanged(invoice):
    """Allow header/payment saves of historical submitted promotion invoices."""
    if invoice.get("docstatus") != 1 or not invoice.get("name"):
        return False
    previous = frappe.get_doc("POS Invoice", invoice.name)
    if (previous.get("selling_price_list") or "") != (
        invoice.get("selling_price_list") or ""
    ):
        return False
    return _commercial_item_snapshot(previous.get("items", [])) == (
        _commercial_item_snapshot(invoice.get("items", []))
    )


def _validate_return_price_option_snapshots(invoice):
    """Validate a return against the exact historical commercial snapshots."""
    if not invoice.get("return_against"):
        if _has_promotional_snapshot(invoice.get("items", [])):
            frappe.throw(
                _("A promotional return must reference its original POS Invoice."),
                title=_("Invalid Promotional Return"),
            )
        return

    original = frappe.get_doc("POS Invoice", invoice.return_against)
    lock_invoice_price_options(original)
    original_by_row = {
        item.get("name"): item for item in original.get("items", []) if item.get("name")
    }

    changed = []
    returned_by_row = defaultdict(float)
    for item in invoice.get("items", []):
        original_row_name = item.get("pos_invoice_item") or item.get(
            "sales_invoice_item"
        )
        original_row = original_by_row.get(original_row_name)
        option_id = item.get(OPTION_FIELD) or STANDARD_OPTION_ID
        if not original_row:
            changed.append(item.get("item_code"))
            continue

        original_option_id = original_row.get(OPTION_FIELD) or STANDARD_OPTION_ID
        if (
            item.get("item_code") != original_row.get("item_code")
            or option_id != original_option_id
            or (item.get(OPTION_LABEL_FIELD) or "")
            != (original_row.get(OPTION_LABEL_FIELD) or "")
            or flt(item.get("rate")) != flt(original_row.get("rate"))
            or flt(item.get("price_list_rate"))
            != flt(original_row.get("price_list_rate"))
        ):
            changed.append(item.get("item_code"))
        returned_by_row[original_row_name] += abs(
            flt(item.get("stock_qty"))
            or flt(item.get("qty")) * (flt(item.get("conversion_factor")) or 1)
        )

    over_returned = [
        original_by_row[row_name].get("item_code")
        for row_name, returned_qty in returned_by_row.items()
        if row_name in original_by_row
        and returned_qty > abs(flt(original_by_row[row_name].get("stock_qty"))) + 1e-9
    ]
    changed.extend(over_returned)
    if changed:
        frappe.throw(
            _(
                "Promotional return does not match the original price option: {0}."
            ).format(
                ", ".join(
                    frappe.bold(code)
                    for code in sorted({code for code in changed if code})
                )
            ),
            title=_("Invalid Promotional Return"),
        )


def validate_pos_invoice_price_options(invoice):
    """Enforce URY price-option identity, price and quota for every writer.

    The dedicated order service performs the same checks earlier, but standard
    Document API/Desk/integration saves must not be able to forge a snapshot or
    bypass the allocated quantity.
    """
    if getattr(frappe.flags, "ury_bill_split", False):
        # split_bill has already locked the option set and only moves unchanged
        # item snapshots between two draft invoices in the same transaction.
        return
    if invoice.get("docstatus") == 2 or not invoice.get("items"):
        return
    if invoice.get("is_return"):
        _validate_return_price_option_snapshots(invoice)
        return
    if _submitted_promotion_is_unchanged(invoice):
        return

    item_codes = list(
        dict.fromkeys(
            item.get("item_code")
            for item in invoice.get("items", [])
            if item.get("item_code")
        )
    )
    menu = _get_pos_invoice_menu(invoice)
    if not menu:
        if _has_promotional_snapshot(invoice.get("items", [])):
            frappe.throw(
                _("Promotional price options require a Price List linked to a URY Menu."),
                title=_("Invalid Price Option"),
            )
        return

    # This locks parent -> every relevant option child before authoritative
    # rates and quota usage are read. It also covers a concurrent first option.
    all_options = get_menu_promotions(
        menu,
        item_codes=item_codes,
        enabled_only=False,
        for_update=True,
    )
    active_codes = {row.item for row in all_options if row.enabled}
    selected_codes = {
        item.get("item_code")
        for item in invoice.get("items", [])
        if item.get(OPTION_FIELD)
        and item.get(OPTION_FIELD) != STANDARD_OPTION_ID
    }
    protected_codes = sorted(active_codes | selected_codes)
    if not protected_codes:
        misleading_labels = [
            item.get("item_code")
            for item in invoice.get("items", [])
            if item.get(OPTION_LABEL_FIELD)
            and (
                not item.get(OPTION_FIELD)
                or item.get(OPTION_FIELD) == STANDARD_OPTION_ID
            )
            and item.get(OPTION_LABEL_FIELD) != "Normal"
        ]
        if misleading_labels:
            frappe.throw(
                _(
                    "Price option label is invalid for: {0}. Reload the menu."
                ).format(
                    ", ".join(
                        frappe.bold(code)
                        for code in sorted(set(misleading_labels))
                    )
                ),
                title=_("Invalid Price Option"),
            )
        return

    from ury.ury.doctype.ury_order.ury_order import get_authoritative_item_prices
    from ury.ury_pos.api import _get_stock_details

    base_rates = get_authoritative_item_prices(
        protected_codes,
        invoice.selling_price_list,
        for_update=True,
    )
    exclude_invoice = None if invoice.is_new() else invoice.name
    stock_details = _get_stock_details(
        protected_codes,
        _get_pos_profile_warehouse(invoice),
        exclude_invoice=exclude_invoice,
        for_update=True,
    )
    physical_availability = {
        item_code: details["available_qty"]
        for item_code, details in stock_details.items()
    }
    validate_price_option_quantities(
        invoice.get("items", []),
        menu,
        base_rates,
        physical_availability,
        exclude_invoice=exclude_invoice,
    )
    validate_price_option_row_prices(
        invoice.get("items", []), menu, base_rates
    )
