"""Operator-facing remaining quantities; preserve the internal sales history.

The internal allocation stays cumulative. Operators only see/edit a stock-bound
remaining balance. Changing that balance translates it to consumed + requested
under the same menu/stock locks as order validation. No invoice is rewritten.
"""

import math

import frappe
from frappe import _
from frappe.utils import flt

from ury.ury_pos.price_options import (
    _get_used_promotion_qty,
    lock_menu_price_options_parent,
)


def remaining_quantity(allocation, consumed, available):
    return min(max(0, flt(available)), max(0, flt(allocation) - flt(consumed)))


def allocation_for_remaining(requested, consumed, available):
    try:
        requested = float(requested)
    except (TypeError, ValueError):
        frappe.throw(_("Enter a valid promotional quantity."))
    if not math.isfinite(requested) or requested < 0:
        frappe.throw(_("Enter a valid promotional quantity."))
    if requested > max(0, flt(available)):
        frappe.throw(_("Promotional quantity cannot exceed available stock ({0}).").format(available))
    return flt(consumed) + requested


def menu_stock(menu, item_codes, lock=False):
    from ury.ury_pos.api import _get_stock_details

    branch = menu.branch
    warehouses = sorted({
        row.warehouse for row in frappe.get_all(
            "POS Profile", filters={"branch": branch, "disabled": 0}, fields=["warehouse"]
        ) if row.warehouse
    })
    if len(warehouses) != 1:
        frappe.throw(_("The menu must have exactly one POS warehouse to set promotional quantities."))
    return _get_stock_details(item_codes, warehouses[0], for_update=lock)


def snapshots(menu, lock=False):
    rows = menu.price_options or []
    if not rows:
        return {}
    if lock and not menu.is_new():
        lock_menu_price_options_parent(menu.name)
    codes = sorted({row.item for row in rows if row.item})
    stock = menu_stock(menu, codes, lock=lock)
    used = _get_used_promotion_qty([row.name for row in rows if row.name], for_update=lock)
    return {
        row.name: {
            "allocated_qty": flt(row.allocated_qty),
            "used_qty": flt(used.get(row.name)),
            "available_qty": flt(stock.get(row.item, {}).get("available_qty")),
            "remaining_qty": remaining_quantity(
                row.allocated_qty, used.get(row.name),
                stock.get(row.item, {}).get("available_qty"),
            ),
        }
        for row in rows
    }


def load_balances(menu):
    """Populate virtual fields and optimistic edit baselines on a fresh form."""
    balances = snapshots(menu)
    menu.set_onload("promotion_balances", balances)
    for row in menu.price_options or []:
        row.remaining_qty = balances[row.name]["remaining_qty"]


def apply_balance_requests(menu):
    """Only explicit edits replenish/reset the balance; ordinary saves do not."""
    requested = [row for row in menu.price_options or [] if row.get("_remaining_request")]
    if not requested:
        return
    current = snapshots(menu, lock=True)
    for row in requested:
        request = row.get("_remaining_request")
        if not isinstance(request, dict):
            frappe.throw(_("Reload the menu before changing promotional quantities."))
        state = current[row.name]
        if not row.is_new():
            # Re-read persisted allocation: the form may predate another edit.
            allocation = frappe.db.get_value("URY Menu Price Option", row.name, "allocated_qty")
            expected = request.get("expected") or {}
            if any(
                key not in expected or flt(expected[key]) != flt(value)
                for key, value in {
                    "allocated_qty": allocation,
                    "used_qty": state["used_qty"],
                    "available_qty": state["available_qty"],
                }.items()
            ):
                frappe.throw(_("Promotion or stock changed while editing. Reload the menu and try again."))
        row.allocated_qty = allocation_for_remaining(
            request.get("quantity"), state["used_qty"], state["available_qty"]
        )
        row.remaining_qty = flt(request.get("quantity"))
        row._remaining_request = None
