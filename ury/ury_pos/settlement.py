"""Transactional, server-authoritative checkout for the URY POS.

This module deliberately owns the complete commercial decision (discount,
payment and credit).  The browser supplies intentions only; every price,
total, payment allocation, actor and POS context is resolved again here.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import frappe
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_checks_for_pl_and_bs_accounts,
)
from erpnext.accounts.party import get_party_account, get_party_details, set_taxes
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, validate_phone_number

from ury.ury.doctype.ury_order.ury_order import get_authoritative_item_prices
from ury.ury_pos.cashier import get_single_cashier_opening
from ury.ury_pos.price_options import (
    MANUAL_DISCOUNT_AMOUNT_FIELD,
    MANUAL_DISCOUNT_INPUT_FIELD,
    MANUAL_DISCOUNT_TYPE_FIELD,
    OPTION_FIELD,
    OPTION_LABEL_FIELD,
    PRICE_OPTION_REDUCTION_FIELD,
    RATE_BEFORE_MANUAL_DISCOUNT_FIELD,
    STANDARD_OPTION_ID,
    group_menu_promotions,
    lock_menu_price_options_parent,
    resolve_price_option,
)

SETTLEMENT_DOCTYPE = "URY POS Settlement"
SETTLEMENT_TYPES = ("Paid", "Partial Credit", "Full Credit", "House Offer")
PRIVILEGED_ROLES = frozenset({"System Manager", "URY Manager"})
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")

INVOICE_SETTLEMENT_FIELDS = (
    "custom_ury_settlement",
    "custom_ury_settlement_type",
    "custom_ury_credit_amount",
    "custom_ury_credit_due_date",
    "custom_ury_manual_discount_total",
)
ITEM_MANUAL_DISCOUNT_FIELDS = (
    RATE_BEFORE_MANUAL_DISCOUNT_FIELD,
    PRICE_OPTION_REDUCTION_FIELD,
    MANUAL_DISCOUNT_TYPE_FIELD,
    MANUAL_DISCOUNT_INPUT_FIELD,
    MANUAL_DISCOUNT_AMOUNT_FIELD,
)


class SettlementValidationError(frappe.ValidationError):
    """Raised for a malformed or commercially invalid checkout request."""


def _fail(message):
    raise SettlementValidationError(message)


def validate_explicit_house_offer(grand_total, house_offer):
    """A zero-value checkout must always be an explicit House Offer."""
    if flt(grand_total) < 0.005 and not house_offer:
        _fail(
            _(
                "Use House Offer for a 100 percent discount on the complete invoice."
            )
        )


def _json_object(value, label="payload"):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            _fail(_("{0} must be valid JSON.").format(label))
    if not isinstance(value, dict):
        _fail(_("{0} must be an object.").format(label))
    return value


def _finite_decimal(value, label):
    if isinstance(value, bool) or value in (None, ""):
        _fail(_("{0} must be a finite number.").format(label))
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _fail(_("{0} must be a finite number.").format(label))
    if not number.is_finite():
        _fail(_("{0} must be a finite number.").format(label))
    return number


def _quantize(value, precision=2):
    quantum = Decimal(1).scaleb(-int(precision))
    return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)


def _number(value, precision=2):
    return float(_quantize(value, precision))


def _strict_boolean(value, label, *, default=False):
    """Accept JSON booleans only, with 0/1 kept for Frappe compatibility."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    _fail(_("{0} must be true or false.").format(label))


def normalise_payments(payments, allowed_modes, precision=2):
    """Pure validation of browser payment rows.

    ``allowed_modes`` maps Mode of Payment names to authoritative metadata.
    Zero-value rows are never accepted from the browser; the service creates
    the ERPNext-required technical zero row itself when necessary.
    """
    if payments is None:
        payments = []
    if not isinstance(payments, (list, tuple)):
        _fail(_("Payments must be a list."))

    normalised = []
    seen = set()
    for index, row in enumerate(payments, 1):
        if not isinstance(row, dict):
            _fail(_("Payment row {0} is invalid.").format(index))
        mode = str(row.get("mode_of_payment") or "").strip()
        if not mode or mode not in allowed_modes:
            _fail(
                _("Payment method {0} is not available in this POS Profile.").format(
                    frappe.bold(mode or _("Not set"))
                )
            )
        if mode in seen:
            _fail(
                _("Payment method {0} was entered more than once.").format(
                    frappe.bold(mode)
                )
            )
        amount = _quantize(_finite_decimal(row.get("amount"), _("Payment amount")), precision)
        if amount <= 0:
            _fail(_("Payment amounts must be greater than zero."))
        seen.add(mode)
        normalised.append(
            {
                "mode_of_payment": mode,
                "amount": float(amount),
                "type": allowed_modes[mode].get("type"),
            }
        )
    return normalised


def normalise_discounts(discounts, invoice_names, item_rows):
    """Pure structural validation for item and invoice discounts."""
    discounts = discounts or {}
    if not isinstance(discounts, dict):
        _fail(_("Discounts must be an object."))
    raw_items = discounts.get("items") or []
    if not isinstance(raw_items, (list, tuple)):
        _fail(_("Item discounts must be a list."))

    result_items = []
    seen = set()
    valid_invoices = set(invoice_names)
    valid_rows = set(item_rows)
    for index, row in enumerate(raw_items, 1):
        if not isinstance(row, dict):
            _fail(_("Item discount row {0} is invalid.").format(index))
        invoice = str(row.get("pos_invoice") or "").strip()
        item_row = str(row.get("item_row") or row.get("name") or "").strip()
        key = (invoice, item_row)
        if invoice not in valid_invoices or key not in valid_rows:
            _fail(_("The selected invoice item is no longer available."))
        if key in seen:
            _fail(_("The same invoice item cannot be discounted twice."))
        discount_type = str(row.get("type") or "").strip()
        if discount_type not in ("Percent", "Amount"):
            _fail(_("Discount type must be Percent or Amount."))
        value = _finite_decimal(row.get("value"), _("Discount value"))
        if value <= 0:
            _fail(_("Discount values must be greater than zero."))
        seen.add(key)
        result_items.append(
            {
                "pos_invoice": invoice,
                "item_row": item_row,
                "type": discount_type,
                "value": float(value),
            }
        )

    raw_invoice = discounts.get("invoice")
    invoice_discount = None
    if raw_invoice:
        if not isinstance(raw_invoice, dict):
            _fail(_("Invoice discount must be an object."))
        discount_type = str(raw_invoice.get("type") or "").strip()
        if discount_type not in ("Percent", "Amount"):
            _fail(_("Discount type must be Percent or Amount."))
        value = _finite_decimal(raw_invoice.get("value"), _("Discount value"))
        if value <= 0:
            _fail(_("Discount values must be greater than zero."))
        invoice_discount = {"type": discount_type, "value": float(value)}

    return {"items": result_items, "invoice": invoice_discount}


def allocate_proportionally(total, weights, precision=2):
    """Pure money allocation whose rounded parts always equal ``total``."""
    total = _quantize(total, precision)
    weights = [max(Decimal(0), Decimal(str(weight))) for weight in weights]
    weight_total = sum(weights, Decimal(0))
    if not weights:
        return []
    if total == 0:
        return [0.0 for _weight in weights]
    if weight_total <= 0:
        _fail(_("An amount cannot be allocated across zero-value invoices."))

    result = []
    allocated = Decimal(0)
    for index, weight in enumerate(weights):
        if index == len(weights) - 1:
            part = total - allocated
        else:
            part = _quantize(total * weight / weight_total, precision)
            allocated += part
        result.append(float(part))
    return result


def allocate_payment_rows(payments, invoice_totals, allow_credit, precision=2):
    """Pure allocation of real tenders over one or more POS Invoices."""
    totals = [_quantize(value, precision) for value in invoice_totals]
    total_due = sum(totals, Decimal(0))
    tendered = sum((_quantize(row["amount"], precision) for row in payments), Decimal(0))

    if total_due == 0 and tendered:
        _fail(_("A zero-value sale cannot contain payments."))
    if allow_credit:
        if tendered >= total_due:
            _fail(_("Credit requires a balance greater than zero."))
        change = Decimal(0)
    else:
        if tendered < total_due:
            _fail(_("Payment does not cover the sale total. Enable credit explicitly."))
        change = tendered - total_due
        if change and not any(row.get("type") == "Cash" for row in payments):
            _fail(_("Change can only be given when a cash payment is present."))

    # Non-cash tenders are allocated first. Any amount above the total is then
    # necessarily carried by a cash row, which lets ERPNext calculate change
    # without attributing it to M-Pesa/Card.
    ordered = sorted(enumerate(payments), key=lambda pair: pair[1].get("type") == "Cash")
    allocations = [[] for _total in totals]
    balances = list(totals)
    for _original_index, payment in ordered:
        remaining = _quantize(payment["amount"], precision)
        for index, balance in enumerate(balances):
            if remaining <= 0 or balance <= 0:
                continue
            part = min(remaining, balance)
            allocations[index].append(
                {"mode_of_payment": payment["mode_of_payment"], "amount": float(part)}
            )
            balances[index] -= part
            remaining -= part
        if remaining:
            if payment.get("type") != "Cash" or not allocations:
                _fail(_("Payments could not be allocated to the invoices."))
            target = max((i for i, value in enumerate(totals) if value > 0), default=0)
            existing = next(
                (
                    row
                    for row in allocations[target]
                    if row["mode_of_payment"] == payment["mode_of_payment"]
                ),
                None,
            )
            if existing:
                existing["amount"] = _number(existing["amount"] + float(remaining), precision)
            else:
                allocations[target].append(
                    {"mode_of_payment": payment["mode_of_payment"], "amount": float(remaining)}
                )

    if not allow_credit and any(balance != 0 for balance in balances):
        _fail(_("Payments do not cover every invoice in the checkout."))

    return {
        "allocations": allocations,
        "tendered_amount": float(tendered),
        "change_amount": float(change),
        "paid_now": float(tendered - change),
        "credit_amount": float(sum(balances, Decimal(0))) if allow_credit else 0.0,
    }


def make_revision(invoices):
    """Return a deterministic hash of every mutable checkout input."""
    snapshot = []
    for invoice in sorted(invoices, key=lambda doc: doc.name):
        snapshot.append(
            {
                "name": invoice.name,
                "modified": str(invoice.get("modified") or ""),
                "docstatus": int(invoice.get("docstatus") or 0),
                "status": invoice.get("status"),
                "customer": invoice.get("customer"),
                "branch": invoice.get("branch"),
                "pos_profile": invoice.get("pos_profile"),
                "selling_price_list": invoice.get("selling_price_list"),
                "taxes_and_charges": invoice.get("taxes_and_charges"),
                "merged_invoice": invoice.get("custom_merged_pos_invoice"),
                "grand_total": flt(invoice.get("grand_total")),
                "rounded_total": flt(invoice.get("rounded_total")),
                "items": sorted(
                    [
                        {
                            "name": row.get("name"),
                            "item_code": row.get("item_code"),
                            "qty": flt(row.get("qty")),
                            "rate": flt(row.get("rate")),
                            "price_list_rate": flt(row.get("price_list_rate")),
                            "option": row.get(OPTION_FIELD),
                            "option_label": row.get(OPTION_LABEL_FIELD),
                        }
                        for row in invoice.get("items", [])
                    ],
                    key=lambda row: row["name"] or "",
                ),
                "payments": sorted(
                    [
                        {
                            "mode": row.get("mode_of_payment"),
                            "amount": flt(row.get("amount")),
                        }
                        for row in invoice.get("payments", [])
                    ],
                    key=lambda row: row["mode"] or "",
                ),
                "taxes": [
                    {
                        "account_head": row.get("account_head"),
                        "charge_type": row.get("charge_type"),
                        "rate": flt(row.get("rate")),
                        "tax_amount": flt(row.get("tax_amount")),
                    }
                    for row in invoice.get("taxes", [])
                ],
            }
        )
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _invoice_names(invoice_name):
    selected = frappe.db.get_value(
        "POS Invoice",
        invoice_name,
        ["name", "custom_merged_pos_invoice", "docstatus"],
        as_dict=True,
    )
    if not selected:
        _fail(_("POS Invoice {0} was not found.").format(frappe.bold(invoice_name)))

    names = [selected.name]
    linked = selected.custom_merged_pos_invoice
    if linked:
        names.append(linked)
    else:
        reverse = frappe.get_all(
            "POS Invoice",
            filters={"custom_merged_pos_invoice": selected.name, "docstatus": 0},
            pluck="name",
            limit=2,
        )
        if len(reverse) > 1:
            _fail(_("The merged bill has more than one linked invoice."))
        names.extend(reverse)
    return list(dict.fromkeys(names))


def _lock_commercial_configuration(invoices):
    menus = set()
    for invoice in invoices:
        if invoice.get("selling_price_list"):
            menu = frappe.db.get_value(
                "Price List", invoice.selling_price_list, "restaurant_menu"
            )
            if menu:
                menus.add(menu)
    for menu in sorted(menus):
        lock_menu_price_options_parent(menu)


def _lock_stock_bins(invoices):
    """Serialize the physical stock rows touched by this checkout.

    ERPNext still performs the authoritative stock validation during submit;
    these locks only ensure two URY settlements cannot both validate against
    the same pre-submit Bin snapshot.
    """
    by_warehouse = {}
    for invoice in invoices:
        default_warehouse = invoice.get("set_warehouse")
        for table in ("items", "packed_items"):
            for row in invoice.get(table, []):
                warehouse = row.get("warehouse") or default_warehouse
                if warehouse and row.get("item_code"):
                    by_warehouse.setdefault(warehouse, set()).add(row.item_code)
    for warehouse in sorted(by_warehouse):
        item_codes = tuple(sorted(by_warehouse[warehouse]))
        frappe.db.sql(
            """
            SELECT name
            FROM `tabBin`
            WHERE warehouse = %(warehouse)s
              AND item_code IN %(item_codes)s
            ORDER BY item_code, name
            FOR UPDATE
            """,
            {"warehouse": warehouse, "item_codes": item_codes},
        )


def _load_invoices(invoice_name, for_update=False):
    names = _invoice_names(invoice_name)
    initial = [frappe.get_doc("POS Invoice", name) for name in names]
    if for_update:
        _lock_commercial_configuration(initial)
        placeholders = ", ".join(["%s"] * len(names))
        frappe.db.sql(
            f"""
            SELECT name
            FROM `tabPOS Invoice`
            WHERE name IN ({placeholders})
            ORDER BY name
            FOR UPDATE
            """,
            tuple(sorted(names)),
        )
        frappe.db.sql(
            f"""
            SELECT name
            FROM `tabPOS Invoice Item`
            WHERE parent IN ({placeholders})
            ORDER BY parent, name
            FOR UPDATE
            """,
            tuple(sorted(names)),
        )
        invoices_by_name = {
            name: frappe.get_doc("POS Invoice", name) for name in names
        }
        invoices = [invoices_by_name[name] for name in names]
        _lock_stock_bins(invoices)
        if set(_invoice_names(invoice_name)) != set(names):
            raise frappe.TimestampMismatchError(
                _("The merged bill changed while checkout was starting. Reload and retry.")
            )
        return invoices
    return initial


def _validate_invoice_set(invoices):
    if not invoices:
        _fail(_("No POS Invoice was selected."))
    first = invoices[0]
    for invoice in invoices:
        if cint(invoice.docstatus) != 0 or invoice.get("status") not in (None, "", "Draft"):
            _fail(
                _("POS Invoice {0} is no longer a draft.").format(
                    frappe.bold(invoice.name)
                )
            )
        if invoice.get("consolidated_invoice"):
            _fail(_("A consolidated POS Invoice cannot be settled again."))
        if invoice.get("is_return"):
            _fail(_("Returns cannot receive a new discount in this checkout."))
        if invoice.get("custom_ury_settlement"):
            _fail(_("POS Invoice {0} is already settled.").format(frappe.bold(invoice.name)))
        if not invoice.get("items"):
            _fail(_("POS Invoice {0} has no items.").format(frappe.bold(invoice.name)))
        shared_fields = (
            "pos_profile",
            "branch",
            "company",
            "currency",
            "conversion_rate",
            "selling_price_list",
            "plc_conversion_rate",
            "taxes_and_charges",
            "set_warehouse",
            "cashier",
            "customer",
            "cost_center",
            "project",
            *tuple(
                dimension.fieldname
                for dimension in get_checks_for_pl_and_bs_accounts()
            ),
        )
        for fieldname in shared_fields:
            if (invoice.get(fieldname) or "") != (first.get(fieldname) or ""):
                _fail(_("All invoices in a merged checkout must share {0}.").format(fieldname))

    if len(invoices) > 1:
        names = {invoice.name for invoice in invoices}
        for invoice in invoices:
            linked = invoice.get("custom_merged_pos_invoice")
            if linked not in names:
                _fail(_("The merged bill links are inconsistent. Unmerge and retry."))
    return first


def _validate_invoice_warehouses(invoices, profile):
    """Keep every stock row inside the warehouse configured for this POS."""
    expected = str(profile.get("warehouse") or "").strip()
    if not expected:
        _fail(_("The POS Profile has no warehouse."))
    for invoice in invoices:
        header = str(invoice.get("set_warehouse") or expected).strip()
        if header != expected:
            _fail(
                _("POS Invoice {0} uses a warehouse outside this POS Profile.").format(
                    frappe.bold(invoice.name)
                )
            )
        for table in ("items", "packed_items"):
            for row in invoice.get(table) or []:
                warehouse = str(row.get("warehouse") or header).strip()
                if warehouse != expected:
                    _fail(
                        _(
                            "Item {0} in POS Invoice {1} uses a warehouse outside "
                            "this POS Profile."
                        ).format(
                            frappe.bold(row.get("item_code") or row.get("name")),
                            frappe.bold(invoice.name),
                        )
                    )


def _profile_payment_modes(profile):
    modes = {}
    for row in profile.get("payments") or []:
        mode = row.get("mode_of_payment")
        if not mode or mode in modes:
            continue
        mode_type = frappe.get_cached_value("Mode of Payment", mode, "type")
        modes[mode] = {
            "id": mode,
            "name": mode,
            "type": mode_type,
            "default": bool(row.get("default")),
        }
    if not modes:
        _fail(_("The POS Profile has no payment methods."))
    return modes


def _user_branches(user):
    rows = frappe.db.sql(
        """
        SELECT DISTINCT branch.branch
        FROM `tabURY User` ury_user
        INNER JOIN `tabBranch` branch ON branch.name = ury_user.parent
        WHERE ury_user.user = %s
        """,
        user,
        as_dict=True,
    )
    return {row.branch for row in rows}


def _resolve_opening(profile, invoices, for_update=False):
    branch = invoices[0].branch
    if not cint(profile.get("custom_enable_multiple_cashier")):
        opening = get_single_cashier_opening(
            profile.name,
            required=True,
            for_update=for_update,
        )
        if opening.branch != branch:
            _fail(_("The active POS Opening Entry belongs to another branch."))
        return opening

    filters = {
        "pos_profile": profile.name,
        "branch": branch,
        "status": "Open",
        "docstatus": 1,
    }
    rows = frappe.db.get_values(
        "POS Opening Entry",
        filters=filters,
        fieldname=["name", "user", "branch", "pos_profile", "period_start_date"],
        as_dict=True,
        order_by="period_start_date desc, creation desc",
        limit=20,
        for_update=for_update,
    )
    if not rows:
        _fail(_("Open the POS before settling an order."))
    invoice_cashier = invoices[0].get("cashier")
    candidates = [row for row in rows if invoice_cashier and row.user == invoice_cashier]
    if not candidates:
        candidates = [row for row in rows if row.user == frappe.session.user]
    if not candidates and len(rows) == 1:
        candidates = rows

    room = invoices[0].get("custom_restaurant_room")
    if room and len(candidates) > 1:
        opening_names = frappe.get_all(
            "Multiple Rooms",
            filters={
                "parent": ["in", [row.name for row in candidates]],
                "parenttype": "POS Opening Entry",
                "room": room,
            },
            pluck="parent",
        )
        candidates = [row for row in candidates if row.name in set(opening_names)]

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        _fail(_("The invoice is not linked to an active cashier opening."))
    _fail(_("More than one cashier opening matches this checkout."))


def _authorise(invoices, profile, opening, require_features=True):
    user = frappe.session.user
    if not user or user == "Guest":
        raise frappe.AuthenticationError
    roles = set(frappe.get_roles(user))
    permitted_roles = {
        row.get("role") for row in profile.get("role_allowed_for_billing") or [] if row.get("role")
    }
    privileged = user == "Administrator" or bool(roles & PRIVILEGED_ROLES)
    if not privileged and not roles.intersection(permitted_roles):
        frappe.throw(_("You are not allowed to bill with this POS Profile."), frappe.PermissionError)
    if not privileged and invoices[0].branch not in _user_branches(user):
        frappe.throw(_("This invoice belongs to another branch."), frappe.PermissionError)
    if profile.branch != invoices[0].branch or opening.branch != invoices[0].branch:
        frappe.throw(_("Invoice, POS Profile and opening branch do not match."), frappe.PermissionError)
    if require_features and not cint(profile.get("custom_ury_enable_commercial_checkout")):
        _fail(_("Commercial checkout is disabled for this POS Profile."))
    for invoice in invoices:
        invoice.check_permission("write")
    return user


def _load_context(invoice_name, for_update=False):
    # Read the profile first so the active opening is locked before invoice
    # rows, matching the established order-service lock order.
    initial = _load_invoices(invoice_name, for_update=False)
    first = _validate_invoice_set(initial)
    profile = frappe.get_doc("POS Profile", first.pos_profile)
    opening = _resolve_opening(profile, initial, for_update=for_update)
    invoices = _load_invoices(invoice_name, for_update=for_update) if for_update else initial
    _validate_invoice_set(invoices)
    _validate_invoice_warehouses(invoices, profile)
    _authorise(invoices, profile, opening)
    return invoices, profile, opening


def _customer_mobile(customer):
    fields = ["mobile_no"]
    if frappe.get_meta("Customer").has_field("mobile_number"):
        fields.append("mobile_number")
    values = frappe.db.get_value("Customer", customer, fields, as_dict=True) or {}
    return values.get("mobile_no") or values.get("mobile_number")


def _validate_existing_customer(customer):
    customer = str(customer or "").strip()
    if not customer or not frappe.db.exists("Customer", customer):
        _fail(_("Select an existing customer."))
    doc = frappe.get_doc("Customer", customer)
    doc.check_permission("read")
    if cint(doc.get("disabled")):
        _fail(_("Customer {0} is disabled.").format(frappe.bold(customer)))
    return frappe._dict(
        id=doc.name,
        name=doc.get("customer_name") or doc.name,
        phone=_customer_mobile(doc.name),
        is_new=False,
    )


def _find_customer_by_phone(mobile_no):
    customer_meta = frappe.get_meta("Customer")
    fields = [field for field in ("mobile_no", "mobile_number") if customer_meta.has_field(field)]
    matches = set()
    for fieldname in fields:
        matches.update(
            frappe.get_all("Customer", filters={fieldname: mobile_no}, pluck="name", limit=2)
        )
    if len(matches) > 1:
        _fail(_("More than one customer uses this phone number. Resolve the duplicate first."))
    return next(iter(matches), None)


def _resolve_customer_request(payload, invoices, profile, create=False):
    request = payload.get("customer")
    if request is None:
        return _validate_existing_customer(invoices[0].customer)
    if not isinstance(request, dict):
        _fail(_("Customer must be an object."))
    existing = request.get("existing")
    new = request.get("new")
    if bool(existing) == bool(new):
        _fail(_("Choose either an existing customer or a new customer."))
    if existing:
        return _validate_existing_customer(existing)
    if not isinstance(new, dict):
        _fail(_("New customer details are invalid."))

    customer_name = str(new.get("customer_name") or "").strip()
    mobile_no = str(new.get("mobile_no") or "").strip()
    tax_id = str(new.get("tax_id") or "").strip() or None
    if not customer_name or not mobile_no:
        _fail(_("Customer name and phone are required."))
    try:
        validate_phone_number(mobile_no, throw=True)
    except Exception:
        _fail(_("Invalid customer phone number."))
    if create:
        # Customer phone is not a unique database field in ERPNext. Lock one
        # stable parent row so two settlement requests cannot both pass the
        # duplicate check and create the same person concurrently.
        frappe.db.sql(
            "SELECT name FROM `tabDocType` WHERE name = 'Customer' FOR UPDATE"
        )
    duplicate = _find_customer_by_phone(mobile_no)
    if duplicate:
        _fail(
            _("Customer {0} already uses this phone number. Select that customer.").format(
                frappe.bold(duplicate)
            )
        )
    if tax_id:
        duplicate_tax_id = frappe.db.get_value("Customer", {"tax_id": tax_id}, "name")
        if duplicate_tax_id:
            _fail(
                _("Customer {0} already uses this tax identifier.").format(
                    frappe.bold(duplicate_tax_id)
                )
            )
    if not create:
        return frappe._dict(
            id=None,
            name=customer_name,
            phone=mobile_no,
            tax_id=tax_id,
            is_new=True,
        )

    settings = frappe.get_cached_doc("Selling Settings")
    customer_group = _default_leaf_customer_group(
        settings.customer_group, invoices[0].customer
    )
    values = {
        "doctype": "Customer",
        "customer_name": customer_name,
        "customer_type": "Individual",
        "customer_group": customer_group,
        "territory": settings.territory,
        "mobile_no": mobile_no,
    }
    if tax_id:
        values["tax_id"] = tax_id
    if frappe.get_meta("Customer").has_field("mobile_number"):
        values["mobile_number"] = mobile_no
    customer_doc = frappe.get_doc(values)
    customer_doc.insert()
    return frappe._dict(
        id=customer_doc.name,
        name=customer_doc.customer_name,
        phone=mobile_no,
        tax_id=tax_id,
        is_new=True,
    )


def _default_leaf_customer_group(configured_group, fallback_customer=None):
    """Resolve a valid leaf group for customers created during checkout."""
    if configured_group and not frappe.db.get_value(
        "Customer Group", configured_group, "is_group"
    ):
        return configured_group

    fallback_group = None
    if fallback_customer:
        fallback_group = frappe.db.get_value(
            "Customer", fallback_customer, "customer_group"
        )
    if fallback_group and not frappe.db.get_value(
        "Customer Group", fallback_group, "is_group"
    ):
        return fallback_group

    leaf_group = frappe.db.get_value(
        "Customer Group", {"is_group": 0}, "name", order_by="lft asc"
    )
    if not leaf_group:
        _fail(_("No leaf Customer Group is configured for new customers."))
    return leaf_group


PARTY_DETAIL_FIELDS = (
    "customer_name",
    "customer_group",
    "territory",
    "language",
    "debit_to",
    "party_account_currency",
    "due_date",
    "customer_address",
    "address_display",
    "contact_person",
    "contact_display",
    "contact_mobile",
    "contact_email",
    "shipping_address_name",
    "shipping_address",
    "tax_category",
    "taxes_and_charges",
    "payment_terms_template",
)


def _new_customer_party_details(customer, invoice, profile):
    """Build the same generic defaults that the newly inserted Customer gets.

    Preview must remain read-only, so a new Customer is not inserted merely to
    calculate the bill.  New customers created by this flow have no custom
    address, currency, account or terms; their group/company defaults can be
    resolved deterministically without a database write.
    """
    settings = frappe.get_cached_doc("Selling Settings")
    customer_group = _default_leaf_customer_group(
        settings.customer_group, invoice.customer
    )
    account = frappe.db.get_value(
        "Party Account",
        {
            "parenttype": "Customer Group",
            "parent": customer_group,
            "company": invoice.company,
        },
        "account",
    )
    if not account:
        account = get_party_account("Customer", None, invoice.company)
    account_currency = (
        frappe.get_cached_value("Account", account, "account_currency")
        if account
        else None
    )
    tax_category = profile.get("tax_category")
    taxes_and_charges = set_taxes(
        customer.name,
        "Customer",
        invoice.posting_date,
        invoice.company,
        customer_group=customer_group,
        tax_category=tax_category,
    )
    payment_terms_template = (
        frappe.get_cached_value(
            "Customer Group", customer_group, "payment_terms"
        )
        or frappe.get_cached_value("Company", invoice.company, "payment_terms")
    )
    return frappe._dict(
        customer_name=customer.name,
        customer_group=customer_group,
        territory=settings.territory,
        language=None,
        debit_to=account,
        party_account_currency=account_currency,
        due_date=invoice.posting_date,
        customer_address=None,
        address_display=None,
        contact_person=None,
        contact_display=None,
        contact_mobile=customer.phone,
        contact_email=None,
        shipping_address_name=None,
        shipping_address=None,
        tax_category=tax_category,
        taxes_and_charges=taxes_and_charges,
        payment_terms_template=payment_terms_template,
    )


def _apply_customer_details(invoice, customer, profile):
    """Replace every customer-derived field, never only the Customer link."""
    if customer.id:
        details = get_party_details(
            party=customer.id,
            account=None,
            party_type="Customer",
            company=invoice.company,
            posting_date=invoice.posting_date,
            price_list=invoice.selling_price_list,
            currency=invoice.currency,
            doctype="POS Invoice",
            fetch_payment_terms_template=True,
            pos_profile=profile.name,
        )
    else:
        details = _new_customer_party_details(customer, invoice, profile)

    invoice.customer = customer.id or customer.name
    for fieldname in PARTY_DETAIL_FIELDS:
        if invoice.meta.has_field(fieldname):
            invoice.set(fieldname, details.get(fieldname))
    _set_if_field(invoice, "mobile_number", customer.phone)

    # POS prices always remain those of the order's Menu/Price List.  Customer
    # defaults may change tax/account/address data, but must not silently move
    # an already ordered meal to another price list or currency at checkout.
    invoice.set("selling_price_list", invoice.selling_price_list)
    invoice.set("currency", invoice.currency)

    if invoice.meta.has_field("taxes"):
        invoice.set("taxes", [])
        if invoice.get("taxes_and_charges"):
            tax_master = invoice.meta.get_field("taxes_and_charges").options
            invoice.append_taxes_from_master(tax_master)


def _precision(invoice, fieldname="grand_total", fallback=2):
    try:
        return invoice.precision(fieldname)
    except Exception:
        return fallback


def _total(invoice):
    rounded = flt(invoice.get("rounded_total"))
    return rounded or flt(invoice.get("grand_total"))


def _set_if_field(doc, fieldname, value):
    if doc.meta.has_field(fieldname):
        doc.set(fieldname, value)


def _authoritative_options(invoice, for_update=False):
    item_codes = list(
        dict.fromkeys(row.item_code for row in invoice.get("items", []) if row.item_code)
    )
    base_rates = get_authoritative_item_prices(
        item_codes,
        invoice.selling_price_list,
        for_update=for_update,
    )
    menu = frappe.db.get_value(
        "Price List", invoice.selling_price_list, "restaurant_menu"
    )
    if not menu:
        _fail(_("The invoice Price List is not linked to a URY Menu."))
    promotions = group_menu_promotions(menu, item_codes, for_update=for_update)
    options = {}
    for row in invoice.get("items", []):
        options[row.name] = resolve_price_option(
            menu,
            row.item_code,
            row.get(OPTION_FIELD) or STANDARD_OPTION_ID,
            base_rates[row.item_code],
            promotions_by_item=promotions,
        )
    return options


def _reset_commercial_fields(invoice, options):
    invoice.apply_discount_on = "Grand Total"
    invoice.additional_discount_percentage = 0
    invoice.discount_amount = 0
    invoice.base_discount_amount = 0
    for row in invoice.get("items", []):
        option = options[row.name]
        normal_rate = flt(option.base_rate)
        option_rate = flt(option.rate)
        row.rate = option_rate
        row.price_list_rate = normal_rate
        row.base_price_list_rate = normal_rate
        row.discount_amount = flt(normal_rate - option_rate, _precision(row, "discount_amount", 6))
        row.discount_percentage = (
            flt((normal_rate - option_rate) * 100 / normal_rate, _precision(row, "discount_percentage", 6))
            if normal_rate
            else 0
        )
        row.set(OPTION_FIELD, option.id)
        row.set(OPTION_LABEL_FIELD, option.label)
        _set_if_field(row, RATE_BEFORE_MANUAL_DISCOUNT_FIELD, option_rate)
        _set_if_field(row, PRICE_OPTION_REDUCTION_FIELD, normal_rate - option_rate)
        _set_if_field(row, MANUAL_DISCOUNT_TYPE_FIELD, None)
        _set_if_field(row, MANUAL_DISCOUNT_INPUT_FIELD, 0)
        _set_if_field(row, MANUAL_DISCOUNT_AMOUNT_FIELD, 0)
    invoice.calculate_taxes_and_totals()


def _apply_item_discount(invoice, row, request, option, max_percentage, precision):
    qty = flt(row.qty)
    option_rate = flt(option.rate)
    eligible = _quantize(option_rate * qty, precision)
    if qty <= 0 or eligible <= 0:
        _fail(_("The selected item cannot receive a discount."))
    value = _finite_decimal(request["value"], _("Discount value"))
    if request["type"] == "Percent":
        if value > Decimal(str(max_percentage)) or value > 100:
            _fail(_("The item discount exceeds the POS Profile limit."))
        manual_amount = _quantize(eligible * value / Decimal(100), precision)
    else:
        manual_amount = _quantize(value, precision)
        if manual_amount > eligible:
            _fail(_("The item discount exceeds the eligible line amount."))
    if manual_amount <= 0:
        _fail(_("The item discount rounds to zero."))

    rate_precision = _precision(row, "rate", 6)
    net_rate = flt(option_rate - float(manual_amount) / qty, rate_precision)
    normal_rate = flt(option.base_rate)
    row.rate = net_rate
    row.discount_amount = flt(normal_rate - net_rate, _precision(row, "discount_amount", 6))
    row.discount_percentage = (
        flt((normal_rate - net_rate) * 100 / normal_rate, _precision(row, "discount_percentage", 6))
        if normal_rate
        else 0
    )
    _set_if_field(row, RATE_BEFORE_MANUAL_DISCOUNT_FIELD, option_rate)
    _set_if_field(row, PRICE_OPTION_REDUCTION_FIELD, normal_rate - option_rate)
    _set_if_field(row, MANUAL_DISCOUNT_TYPE_FIELD, request["type"])
    _set_if_field(row, MANUAL_DISCOUNT_INPUT_FIELD, float(value))
    _set_if_field(row, MANUAL_DISCOUNT_AMOUNT_FIELD, float(manual_amount))
    return float(manual_amount)


def _clean_financial_state(invoice):
    invoice.set("payments", [])
    for fieldname in (
        "paid_amount",
        "base_paid_amount",
        "change_amount",
        "base_change_amount",
        "write_off_amount",
        "base_write_off_amount",
        "outstanding_amount",
    ):
        invoice.set(fieldname, 0)
    invoice.write_off_outstanding_amount_automatically = 0


def _validate_no_external_credits(invoices):
    """Do not mix URY settlement money with other payment-like mechanisms."""
    labels = {
        "redeem_loyalty_points": _("loyalty points"),
        "loyalty_points": _("loyalty points"),
        "loyalty_amount": _("loyalty amount"),
        "total_advance": _("customer advance"),
        "advance_paid": _("customer advance"),
        "coupon_code": _("coupon"),
    }
    for invoice in invoices:
        used = [label for fieldname, label in labels.items() if invoice.get(fieldname)]
        if used:
            _fail(
                _(
                    "POS Invoice {0} contains {1}, which cannot be combined with "
                    "the URY commercial checkout."
                ).format(frappe.bold(invoice.name), ", ".join(sorted(set(used))))
            )


def _max_discount_percentage(profile):
    configured = profile.get("custom_ury_max_discount_percentage")
    return 100.0 if configured in (None, "") else flt(configured)


def _prepare_documents(invoices, profile, payload, customer, for_update=False):
    _validate_no_external_credits(invoices)
    precision = _precision(invoices[0])
    invoice_names = [invoice.name for invoice in invoices]
    item_rows = {
        (invoice.name, row.name)
        for invoice in invoices
        for row in invoice.get("items", [])
    }
    discounts = normalise_discounts(payload.get("discounts"), invoice_names, item_rows)
    house_offer = _strict_boolean(payload.get("house_offer"), _("House Offer"))
    credit_request = payload.get("credit") or {}
    if not isinstance(credit_request, dict):
        _fail(_("Credit must be an object."))
    credit_enabled = _strict_boolean(
        credit_request.get("enabled"), _("Credit enabled")
    )
    reason = str(payload.get("reason") or "").strip()

    has_manual_discount = bool(discounts["items"] or discounts["invoice"])
    if house_offer and (credit_enabled or discounts["items"] or discounts["invoice"]):
        _fail(_("House Offer cannot be combined with another discount or credit."))
    if (has_manual_discount or house_offer or credit_enabled) and not reason:
        _fail(_("A reason is required for discount, House Offer or credit."))
    if len(reason) > 500:
        _fail(_("The settlement reason is too long."))
    if (has_manual_discount or house_offer) and not cint(profile.get("custom_enable_discount")):
        _fail(_("Discounts are disabled for this POS Profile."))
    if credit_enabled:
        if not cint(profile.get("custom_ury_enable_credit_sales")):
            _fail(_("Credit sales are disabled for this POS Profile."))
        if not cint(profile.get("allow_partial_payment")):
            _fail(_("Allow Partial Payment must be enabled before granting credit."))

    max_percentage = _max_discount_percentage(profile)
    if max_percentage < 0 or max_percentage > 100:
        _fail(_("The POS Profile discount limit is invalid."))
    if house_offer and max_percentage < 100:
        _fail(_("House Offer exceeds the POS Profile discount limit."))

    working = [copy.deepcopy(invoice) for invoice in invoices]
    options_by_invoice = {}
    total_catalogue = 0.0
    price_option_reduction = 0.0
    for invoice in working:
        _apply_customer_details(invoice, customer, profile)
        if (
            credit_enabled
            and invoice.get("party_account_currency")
            and invoice.party_account_currency != invoice.currency
        ):
            _fail(
                _(
                    "Credit checkout requires the customer receivable account "
                    "to use the POS currency."
                )
            )
        options = _authoritative_options(invoice, for_update=for_update)
        options_by_invoice[invoice.name] = options
        _reset_commercial_fields(invoice, options)
        total_catalogue += sum(
            flt(options[row.name].base_rate) * flt(row.qty) for row in invoice.items
        )
        price_option_reduction += sum(
            (flt(options[row.name].base_rate) - flt(options[row.name].rate)) * flt(row.qty)
            for row in invoice.items
        )

    before_manual_totals = [_total(invoice) for invoice in working]
    total_before_manual = sum(before_manual_totals)
    request_by_row = {
        (row["pos_invoice"], row["item_row"]): row for row in discounts["items"]
    }
    discount_audit_rows = []
    item_discount_total = 0.0
    for invoice in working:
        options = options_by_invoice[invoice.name]
        for row in invoice.items:
            request = request_by_row.get((invoice.name, row.name))
            if not request:
                continue
            before_amount = flt(options[row.name].rate) * flt(row.qty)
            amount = _apply_item_discount(
                invoice,
                row,
                request,
                options[row.name],
                max_percentage,
                precision,
            )
            item_discount_total += amount
            discount_audit_rows.append(
                {
                    "pos_invoice": invoice.name,
                    "pos_invoice_item": row.name,
                    "item_code": row.item_code,
                    "discount_type": request["type"],
                    "input_value": request["value"],
                    "base_amount": _number(before_amount, precision),
                    "discount_amount": _number(amount, precision),
                    "final_amount": _number(before_amount - amount, precision),
                }
            )
        invoice.calculate_taxes_and_totals()

    before_header_totals = [_total(invoice) for invoice in working]
    invoice_discount_total = 0.0
    invoice_discount_allocations = [0.0 for _invoice in working]
    if house_offer:
        for invoice in working:
            invoice.apply_discount_on = "Grand Total"
            invoice.additional_discount_percentage = 100
            invoice.discount_amount = 0
            invoice.calculate_taxes_and_totals()
        invoice_discount_total = sum(before_header_totals)
        invoice_discount_allocations = list(before_header_totals)
    elif discounts["invoice"]:
        header = discounts["invoice"]
        if header["type"] == "Percent":
            if header["value"] > max_percentage or header["value"] > 100:
                _fail(_("The invoice discount exceeds the POS Profile limit."))
            for invoice in working:
                invoice.apply_discount_on = "Grand Total"
                invoice.additional_discount_percentage = header["value"]
                invoice.discount_amount = 0
                invoice.calculate_taxes_and_totals()
            invoice_discount_allocations = [
                _number(before - _total(invoice), precision)
                for before, invoice in zip(before_header_totals, working, strict=True)
            ]
        else:
            amount = _quantize(header["value"], precision)
            combined = _quantize(sum(before_header_totals), precision)
            if amount > combined:
                _fail(_("The invoice discount exceeds the sale total."))
            invoice_discount_allocations = allocate_proportionally(
                amount, before_header_totals, precision
            )
            for invoice, allocation in zip(
                working, invoice_discount_allocations, strict=True
            ):
                invoice.apply_discount_on = "Grand Total"
                invoice.additional_discount_percentage = 0
                invoice.discount_amount = allocation
                invoice.calculate_taxes_and_totals()
        invoice_discount_total = sum(invoice_discount_allocations)

    header_audit = (
        {"type": "Percent", "value": 100.0}
        if house_offer
        else discounts["invoice"]
    )
    if header_audit:
        for invoice, before, allocation in zip(
            working,
            before_header_totals,
            invoice_discount_allocations,
            strict=True,
        ):
            if flt(allocation) <= 0:
                continue
            discount_audit_rows.append(
                {
                    "pos_invoice": invoice.name,
                    "pos_invoice_item": None,
                    "item_code": None,
                    "discount_type": header_audit["type"],
                    "input_value": header_audit["value"],
                    "base_amount": _number(before, precision),
                    "discount_amount": _number(allocation, precision),
                    "final_amount": _number(before - allocation, precision),
                }
            )

    final_totals = [_number(_total(invoice), precision) for invoice in working]
    grand_total = _number(sum(final_totals), precision)
    validate_explicit_house_offer(grand_total, house_offer)
    manual_discount_total = _number(total_before_manual - grand_total, precision)
    manual_discount_allocations = [
        _number(before - final, precision)
        for before, final in zip(before_manual_totals, final_totals, strict=True)
    ]
    if manual_discount_allocations:
        manual_discount_allocations[-1] = _number(
            manual_discount_allocations[-1]
            + manual_discount_total
            - sum(manual_discount_allocations),
            precision,
        )
    if total_before_manual > 0 and not house_offer:
        effective_percentage = manual_discount_total * 100 / total_before_manual
        if effective_percentage > max_percentage + 1e-9:
            _fail(_("The combined manual discount exceeds the POS Profile limit."))

    due_date = None
    if credit_enabled:
        requested_due_date = credit_request.get("due_date")
        if not requested_due_date:
            _fail(_("Credit due date is required."))
        due_date = getdate(requested_due_date)
        posting_date = max(getdate(invoice.posting_date) for invoice in working)
        if due_date < posting_date:
            _fail(_("Credit due date cannot be before the sale date."))
        if customer.id == profile.get("customer"):
            _fail(_("Credit cannot be granted to the default counter customer."))

    modes = _profile_payment_modes(profile)
    auto_payment_mode = str(payload.get("auto_payment_mode") or "").strip()
    if auto_payment_mode:
        if auto_payment_mode not in modes:
            _fail(_("The automatic payment method is not available in this POS Profile."))
        if credit_enabled or house_offer:
            _fail(_("Automatic payment cannot be combined with credit or House Offer."))
        payments = [
            {
                "mode_of_payment": auto_payment_mode,
                "amount": grand_total,
                "type": modes[auto_payment_mode].get("type"),
            }
        ]
    else:
        payments = normalise_payments(payload.get("payments"), modes, precision)
    if house_offer and payments:
        _fail(_("House Offer cannot contain payments."))
    payment_plan = allocate_payment_rows(
        payments,
        final_totals,
        allow_credit=credit_enabled,
        precision=precision,
    )
    if credit_enabled and payment_plan["credit_amount"] <= 0:
        _fail(_("Credit requires a balance greater than zero."))

    settlement_type = (
        "House Offer"
        if house_offer
        else "Full Credit"
        if credit_enabled and payment_plan["paid_now"] == 0
        else "Partial Credit"
        if credit_enabled
        else "Paid"
    )
    return frappe._dict(
        invoices=working,
        precision=precision,
        customer=customer,
        reason=reason,
        discounts=discounts,
        discount_audit_rows=discount_audit_rows,
        invoice_discount_allocations=invoice_discount_allocations,
        manual_discount_allocations=manual_discount_allocations,
        house_offer=house_offer,
        credit_enabled=credit_enabled,
        due_date=due_date,
        payment_modes=modes,
        payment_plan=payment_plan,
        settlement_type=settlement_type,
        totals=frappe._dict(
            total_catalogue=_number(total_catalogue, precision),
            price_option_reduction=_number(price_option_reduction, precision),
            total_before_manual_discount=_number(total_before_manual, precision),
            item_discount=_number(item_discount_total, precision),
            invoice_discount=_number(invoice_discount_total, precision),
            manual_discount_total=manual_discount_total,
            taxes=_number(
                sum(flt(invoice.get("total_taxes_and_charges")) for invoice in working),
                precision,
            ),
            adjustment=_number(
                sum(flt(invoice.get("rounding_adjustment")) for invoice in working),
                precision,
            ),
            grand_total=grand_total,
            tendered_amount=_number(payment_plan["tendered_amount"], precision),
            paid_now=_number(payment_plan["paid_now"], precision),
            change_amount=_number(payment_plan["change_amount"], precision),
            credit_amount=_number(payment_plan["credit_amount"], precision),
        ),
    )


def _serialise_prepared(prepared, revision, selected_invoice):
    items = []
    for invoice in prepared.invoices:
        for row in invoice.items:
            items.append(
                {
                    "pos_invoice": invoice.name,
                    "name": row.name,
                    "item_row": row.name,
                    "item_code": row.item_code,
                    "item_name": row.item_name,
                    "qty": flt(row.qty),
                    "price_list_rate": flt(row.price_list_rate),
                    "rate": flt(row.rate),
                    "amount": flt(row.amount),
                    "price_option": row.get(OPTION_FIELD) or STANDARD_OPTION_ID,
                    "price_option_label": row.get(OPTION_LABEL_FIELD),
                    "manual_discount_amount": flt(row.get(MANUAL_DISCOUNT_AMOUNT_FIELD)),
                }
            )
    payment_totals = {}
    for allocation in prepared.payment_plan["allocations"]:
        for row in allocation:
            if row["amount"]:
                mode = row["mode_of_payment"]
                payment_totals[mode] = flt(payment_totals.get(mode)) + flt(
                    row["amount"]
                )
    return {
        "invoice": selected_invoice,
        "invoices": [invoice.name for invoice in prepared.invoices],
        "revision": revision,
        "settlement_type": prepared.settlement_type,
        "customer": {
            "id": prepared.customer.id,
            "name": prepared.customer.name,
            "phone": prepared.customer.phone,
            "is_new": bool(prepared.customer.is_new),
        },
        "items": items,
        "totals": dict(prepared.totals),
        "payments": [
            {"mode_of_payment": mode, "amount": _number(amount, prepared.precision)}
            for mode, amount in payment_totals.items()
        ],
        "credit": {
            "enabled": prepared.credit_enabled,
            "due_date": str(prepared.due_date) if prepared.due_date else None,
            "amount": prepared.totals.credit_amount,
        },
        "discounts": prepared.discounts,
        "house_offer": prepared.house_offer,
    }


def _validate_revision(payload, invoices):
    expected = str(payload.get("revision") or "").strip()
    current = make_revision(invoices)
    if not expected or not hmac.compare_digest(expected, current):
        raise frappe.TimestampMismatchError(
            _("The order changed after checkout was opened. Reload and review it again.")
        )
    return current


def _validate_idempotency_key(value):
    value = str(value or "").strip()
    if not IDEMPOTENCY_PATTERN.fullmatch(value):
        _fail(_("A valid idempotency key is required."))
    return value


def _idempotency_savepoint(idempotency_key):
    digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]
    return f"ury_settlement_{digest}"


def _settlement_table_exists():
    return bool(frappe.db.exists("DocType", SETTLEMENT_DOCTYPE))


def _existing_settlement(idempotency_key, for_update=False):
    if not _settlement_table_exists():
        return None
    if for_update:
        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabURY POS Settlement`
            WHERE idempotency_key = %s
            FOR UPDATE
            """,
            idempotency_key,
        )
        name = rows[0][0] if rows else None
    else:
        name = frappe.db.get_value(
            SETTLEMENT_DOCTYPE, {"idempotency_key": idempotency_key}, "name"
        )
    return frappe.get_doc(SETTLEMENT_DOCTYPE, name) if name else None


def _settlement_invoice_names(settlement):
    for fieldname in ("invoices", "pos_invoices", "settlement_invoices"):
        if settlement.meta.has_field(fieldname):
            return [
                row.get("pos_invoice") or row.get("invoice")
                for row in settlement.get(fieldname) or []
                if row.get("pos_invoice") or row.get("invoice")
            ]
    return []


def _serialise_replay(settlement, requested_invoice):
    user = frappe.session.user
    if not user or user == "Guest":
        raise frappe.AuthenticationError
    settlement.check_permission("read")
    roles = set(frappe.get_roles(user))
    privileged = user == "Administrator" or bool(roles & PRIVILEGED_ROLES)
    if not privileged and settlement.branch not in _user_branches(user):
        frappe.throw(_("This settlement belongs to another branch."), frappe.PermissionError)
    invoice_names = _settlement_invoice_names(settlement)
    if requested_invoice not in invoice_names:
        frappe.throw(_("The idempotency key belongs to another checkout."), frappe.PermissionError)
    return {
        "status": "completed",
        "idempotent_replay": True,
        "settlement": settlement.name,
        "invoice": requested_invoice,
        "invoices": invoice_names,
        "settlement_type": settlement.get("settlement_type"),
        "customer": settlement.get("customer"),
        "totals": {
            "grand_total": flt(settlement.get("grand_total")),
            "paid_now": flt(settlement.get("paid_now")),
            "credit_amount": flt(settlement.get("credit_amount")),
            "manual_discount_total": flt(settlement.get("manual_discount_total")),
        },
        "credit": {
            "enabled": settlement.get("settlement_type") in ("Partial Credit", "Full Credit"),
            "due_date": str(settlement.get("due_date")) if settlement.get("due_date") else None,
            "amount": flt(settlement.get("credit_amount")),
        },
    }


def _require_settlement_schema():
    if not _settlement_table_exists():
        _fail(_("Run bench migrate before enabling commercial checkout."))
    required = {
        "idempotency_key",
        "settlement_type",
        "customer",
        "grand_total",
        "paid_now",
        "credit_amount",
        "manual_discount_total",
    }
    meta = frappe.get_meta(SETTLEMENT_DOCTYPE)
    missing = sorted(fieldname for fieldname in required if not meta.has_field(fieldname))
    if missing:
        _fail(_("URY POS Settlement is missing fields: {0}.").format(", ".join(missing)))
    if not any(meta.has_field(fieldname) for fieldname in ("invoices", "pos_invoices", "settlement_invoices")):
        _fail(_("URY POS Settlement has no invoice child table."))


def _new_settlement(prepared, original_invoices, profile, opening, revision, idempotency_key):
    values = {
        "doctype": SETTLEMENT_DOCTYPE,
        "pos_profile": profile.name,
        "branch": original_invoices[0].branch,
        "pos_opening_entry": opening.name,
        "company": original_invoices[0].company,
        "customer_original": original_invoices[0].customer,
        "customer": prepared.customer.id,
        "settlement_type": prepared.settlement_type,
        "currency": original_invoices[0].currency,
        "total_catalogue": prepared.totals.total_catalogue,
        "price_option_reduction": prepared.totals.price_option_reduction,
        "total_before_manual_discount": prepared.totals.total_before_manual_discount,
        "item_discount_total": prepared.totals.item_discount,
        "invoice_discount_total": prepared.totals.invoice_discount,
        "manual_discount_total": prepared.totals.manual_discount_total,
        "grand_total": prepared.totals.grand_total,
        "paid_now": prepared.totals.paid_now,
        "credit_amount": prepared.totals.credit_amount,
        "due_date": prepared.due_date,
        "reason": prepared.reason,
        "settled_by": frappe.session.user,
        "settled_at": now_datetime(),
        "idempotency_key": idempotency_key,
        "revision_hash": revision,
    }
    settlement = frappe.new_doc(SETTLEMENT_DOCTYPE)
    for fieldname, value in values.items():
        if fieldname == "doctype" or settlement.meta.has_field(fieldname):
            settlement.set(fieldname, value)

    invoice_table = next(
        fieldname
        for fieldname in ("invoices", "pos_invoices", "settlement_invoices")
        if settlement.meta.has_field(fieldname)
    )
    for index, invoice in enumerate(prepared.invoices):
        total = _total(invoice)
        allocation = prepared.payment_plan["allocations"][index]
        tendered = sum(flt(row["amount"]) for row in allocation)
        change = max(0.0, tendered - total) if not prepared.credit_enabled else 0.0
        paid_now = tendered - change
        credit = max(0.0, total - paid_now) if prepared.credit_enabled else 0.0
        child = {
            "pos_invoice": invoice.name,
            "invoice": invoice.name,
            "total_final": total,
            "paid_now": paid_now,
            "credit_amount": credit,
            "due_date": prepared.due_date if credit else None,
        }
        settlement.append(invoice_table, child)

    discount_table = next(
        (
            fieldname
            for fieldname in ("discounts", "discount_lines", "settlement_discounts")
            if settlement.meta.has_field(fieldname)
        ),
        None,
    )
    if discount_table:
        for row in prepared.discount_audit_rows:
            row = dict(row)
            item_name = row.get("pos_invoice_item")
            if item_name:
                invoice = next(
                    doc
                    for doc in prepared.invoices
                    if doc.name == row["pos_invoice"]
                )
                item = next(item for item in invoice.items if item.name == item_name)
                row["price_option"] = (
                    item.get(OPTION_FIELD) or STANDARD_OPTION_ID
                )
            else:
                row["price_option"] = None
            settlement.append(discount_table, row)
    settlement.flags.ignore_permissions = True
    settlement.insert(ignore_permissions=True)
    return settlement


def _ensure_invoice_schema(invoices, manual_discount):
    invoice_meta = frappe.get_meta("POS Invoice")
    missing = [fieldname for fieldname in INVOICE_SETTLEMENT_FIELDS if not invoice_meta.has_field(fieldname)]
    if missing:
        _fail(_("POS Invoice is missing settlement fields: {0}.").format(", ".join(missing)))
    if manual_discount:
        item_meta = frappe.get_meta("POS Invoice Item")
        missing = [fieldname for fieldname in ITEM_MANUAL_DISCOUNT_FIELDS if not item_meta.has_field(fieldname)]
        if missing:
            _fail(_("POS Invoice Item is missing discount fields: {0}.").format(", ".join(missing)))


def _apply_payments_and_audit(invoice, prepared, allocation, settlement_name, index):
    _clean_financial_state(invoice)
    for row in allocation:
        invoice.append(
            "payments",
            {"mode_of_payment": row["mode_of_payment"], "amount": row["amount"]},
        )
    if not allocation:
        default_mode = next(
            (mode for mode in prepared.payment_modes.values() if mode.get("default")),
            next(iter(prepared.payment_modes.values())),
        )
        invoice.append(
            "payments", {"mode_of_payment": default_mode["name"], "amount": 0}
        )

    invoice.customer = prepared.customer.id
    invoice.custom_ury_settlement = settlement_name
    invoice.custom_ury_settlement_type = prepared.settlement_type
    invoice.custom_ury_manual_discount_total = prepared.manual_discount_allocations[index]
    if prepared.credit_enabled:
        tendered = sum(flt(row["amount"]) for row in allocation)
        invoice_credit = max(0.0, _total(invoice) - tendered)
        invoice.custom_ury_credit_amount = _number(invoice_credit, prepared.precision)
        invoice.custom_ury_credit_due_date = prepared.due_date
        invoice.due_date = prepared.due_date
        # ERPNext does not calculate outstanding_amount for POS Invoice in its
        # server-side taxes controller.  Persist the audited allocation so the
        # native POS status becomes Unpaid/Overdue until consolidation.
        invoice.outstanding_amount = invoice.custom_ury_credit_amount
    else:
        invoice.custom_ury_credit_amount = 0
        invoice.custom_ury_credit_due_date = None
        invoice.outstanding_amount = 0
    invoice.cashier = prepared.opening_user
    invoice.calculate_taxes_and_totals()


@frappe.whitelist()
def get_settlement_context(invoice):
    invoices, profile, opening = _load_context(invoice, for_update=False)
    revision = make_revision(invoices)
    modes = _profile_payment_modes(profile)
    precision = _precision(invoices[0])
    default_days = max(0, cint(profile.get("custom_ury_default_credit_days") or 30))
    suggested_due_date = max(getdate(doc.posting_date) for doc in invoices) + timedelta(
        days=default_days
    )
    customer = _validate_existing_customer(invoices[0].customer)
    items = []
    for doc in invoices:
        for row in doc.items:
            items.append(
                {
                    "pos_invoice": doc.name,
                    "name": row.name,
                    "item_row": row.name,
                    "item_code": row.item_code,
                    "item_name": row.item_name,
                    "qty": flt(row.qty),
                    "rate": flt(row.rate),
                    "amount": flt(row.amount),
                    "price_list_rate": flt(row.price_list_rate),
                    "price_option": row.get(OPTION_FIELD) or STANDARD_OPTION_ID,
                    "price_option_label": row.get(OPTION_LABEL_FIELD),
                }
            )
    total_catalogue = sum(flt(row["price_list_rate"]) * flt(row["qty"]) for row in items)
    grand_total = sum(_total(doc) for doc in invoices)
    return {
        "invoice": invoice,
        "invoices": [doc.name for doc in invoices],
        "revision": revision,
        "customer": {"id": customer.id, "name": customer.name, "phone": customer.phone},
        "default_customer": profile.get("customer"),
        "items": items,
        "totals": {
            "total_catalogue": _number(total_catalogue, precision),
            "total_before_manual_discount": _number(grand_total, precision),
            "grand_total": _number(grand_total, precision),
        },
        "payment_modes": list(modes.values()),
        "flags": {
            "commercial_checkout": bool(profile.get("custom_ury_enable_commercial_checkout")),
            "discount": bool(profile.get("custom_enable_discount")),
            "credit": bool(profile.get("custom_ury_enable_credit_sales")),
        },
        "limits": {
            "max_discount_percentage": _max_discount_percentage(profile)
        },
        "suggested_due_date": str(suggested_due_date),
        "currency": invoices[0].currency,
        "precision": precision,
        "pos_opening_entry": opening.name,
    }


@frappe.whitelist()
def preview_settlement(payload):
    payload = _json_object(payload)
    invoice_name = str(payload.get("invoice") or "").strip()
    if not invoice_name:
        _fail(_("POS Invoice is required."))
    invoices, profile, _opening = _load_context(invoice_name, for_update=False)
    revision = _validate_revision(payload, invoices)
    customer = _resolve_customer_request(payload, invoices, profile, create=False)
    prepared = _prepare_documents(
        invoices,
        profile,
        payload,
        customer,
        for_update=False,
    )
    return _serialise_prepared(prepared, revision, invoice_name)


@frappe.whitelist()
def settle_invoice(payload):
    payload = _json_object(payload)
    invoice_name = str(payload.get("invoice") or "").strip()
    if not invoice_name:
        _fail(_("POS Invoice is required."))
    idempotency_key = _validate_idempotency_key(payload.get("idempotency_key"))
    replay = _existing_settlement(idempotency_key)
    if replay:
        return _serialise_replay(replay, invoice_name)

    try:
        invoices, profile, opening = _load_context(invoice_name, for_update=True)
    except (SettlementValidationError, frappe.TimestampMismatchError):
        replay = _existing_settlement(idempotency_key, for_update=True)
        if replay:
            return _serialise_replay(replay, invoice_name)
        raise
    revision = _validate_revision(payload, invoices)
    replay = _existing_settlement(idempotency_key, for_update=True)
    if replay:
        return _serialise_replay(replay, invoice_name)

    # A unique idempotency key is the final arbiter when two different invoice
    # locks race.  Keep customer creation and the audit insert behind one
    # savepoint so the losing request cannot leave an orphan Customer behind.
    savepoint = _idempotency_savepoint(idempotency_key)
    frappe.db.savepoint(savepoint)
    customer = _resolve_customer_request(payload, invoices, profile, create=True)
    prepared = _prepare_documents(
        invoices,
        profile,
        payload,
        customer,
        for_update=True,
    )
    prepared.opening_user = opening.user
    _require_settlement_schema()
    _ensure_invoice_schema(invoices, bool(prepared.discount_audit_rows))
    try:
        settlement = _new_settlement(
            prepared,
            invoices,
            profile,
            opening,
            revision,
            idempotency_key,
        )
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point=savepoint)
        replay = _existing_settlement(idempotency_key, for_update=True)
        if replay:
            return _serialise_replay(replay, invoice_name)
        raise

    previous_sync_flag = getattr(frappe.flags, "in_bill_merge_sync", False)
    previous_settlement_flag = getattr(frappe.flags, "ury_pos_settlement", None)
    frappe.flags.in_bill_merge_sync = True
    frappe.flags.ury_pos_settlement = settlement.name
    submitted = []
    try:
        for index, invoice in enumerate(prepared.invoices):
            _apply_payments_and_audit(
                invoice,
                prepared,
                prepared.payment_plan["allocations"][index],
                settlement.name,
                index,
            )
            invoice.save()
        for invoice in prepared.invoices:
            invoice.submit()
            submitted.append(invoice)

        expected_credit = _number(prepared.totals.credit_amount, prepared.precision)
        actual_credit = 0.0
        for invoice in submitted:
            total = _number(_total(invoice), prepared.precision)
            tendered = _number(
                sum(flt(row.amount) for row in invoice.get("payments") or []),
                prepared.precision,
            )
            calculated_credit = _number(
                max(total - tendered + flt(invoice.get("change_amount")), 0),
                prepared.precision,
            )
            recorded_credit = _number(
                invoice.get("custom_ury_credit_amount"), prepared.precision
            )
            if calculated_credit != recorded_credit:
                _fail(
                    _(
                        "Submitted invoice {0} does not match its credit allocation."
                    ).format(frappe.bold(invoice.name))
                )
            actual_credit += recorded_credit
        actual_credit = _number(actual_credit, prepared.precision)
        if expected_credit != actual_credit:
            _fail(_("Submitted invoices do not match the credit agreement."))

        if settlement.meta.is_submittable:
            settlement.submit()
    finally:
        frappe.flags.in_bill_merge_sync = previous_sync_flag
        frappe.flags.ury_pos_settlement = previous_settlement_flag

    result = _serialise_prepared(prepared, revision, invoice_name)
    result.update(
        {
            "status": "completed",
            "idempotent_replay": False,
            "settlement": settlement.name,
            "invoices": [invoice.name for invoice in submitted],
            "invoice_statuses": {
                invoice.name: invoice.status for invoice in submitted
            },
        }
    )
    return result
