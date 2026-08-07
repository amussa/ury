import json
from datetime import date, datetime, timedelta

import frappe
from erpnext.accounts.doctype.pos_invoice.pos_invoice import (
    get_product_bundle_stock_availability,
)
from frappe import _
from frappe.utils import cint, flt, validate_phone_number

from ury.ury_pos.cashier import get_single_cashier_opening


def get_pos_profile_for_current_branch(pos_profile=None):
    """Return a POS Profile only when it belongs to the current user's branch."""
    branch = getBranch()
    if not pos_profile:
        pos_profile = frappe.db.get_value("POS Profile", {"branch": branch}, "name")

    if not pos_profile:
        frappe.throw(_("No POS Profile found for branch {0}.").format(frappe.bold(branch)))

    profile = frappe.get_doc("POS Profile", pos_profile)
    if profile.branch != branch:
        frappe.throw(
            _("POS Profile {0} does not belong to your branch.").format(
                frappe.bold(profile.name)
            ),
            frappe.PermissionError,
        )

    if not profile.warehouse:
        frappe.throw(
            _("Please set a warehouse in POS Profile {0}.").format(
                frappe.bold(profile.name)
            )
        )

    return profile, branch


def _normalise_item_codes(item_code=None, item_codes=None):
    if isinstance(item_codes, str):
        try:
            item_codes = json.loads(item_codes)
        except (TypeError, ValueError):
            frappe.throw(_("item_codes must be a JSON array."))

    if item_codes is None:
        item_codes = []
    elif not isinstance(item_codes, (list, tuple)):
        frappe.throw(_("item_codes must be a list."))

    requested = []
    if item_code:
        requested.append(item_code)
    requested.extend(item_codes)

    return list(dict.fromkeys(str(code).strip() for code in requested if str(code).strip()))


def _get_item_metadata(item_codes):
    if not item_codes:
        return {}

    rows = frappe.get_all(
        "Item",
        filters={"name": ["in", item_codes]},
        fields=[
            "name",
            "image",
            "disabled",
            "is_stock_item",
            "stock_uom",
            "allow_negative_stock",
        ],
    )
    metadata = {row.name: row for row in rows}
    missing = [item for item in item_codes if item not in metadata]
    if missing:
        frappe.throw(
            _("Item {0} was not found.").format(", ".join(frappe.bold(item) for item in missing))
        )

    return metadata


def _get_active_product_bundle_codes(item_codes, item_metadata):
    candidates = [
        item_code
        for item_code in item_codes
        if not cint(item_metadata[item_code].is_stock_item)
    ]
    if not candidates:
        return set()

    return set(
        frappe.get_all(
            "Product Bundle",
            filters={"name": ["in", candidates], "disabled": 0},
            pluck="name",
        )
    )


def _get_bin_qty_map(item_codes, warehouse):
    if not item_codes:
        return {}

    return {
        row.item_code: flt(row.actual_qty)
        for row in frappe.get_all(
            "Bin",
            filters={"warehouse": warehouse, "item_code": ["in", item_codes]},
            fields=["item_code", "actual_qty"],
        )
    }


def _get_reserved_qty_from_child_table(
    child_table,
    qty_field,
    item_codes,
    warehouse,
    docstatus,
    exclude_invoice=None,
    exclude_returns=False,
    for_update=False,
):
    if not item_codes:
        return {}

    conditions = [
        "invoice.name = item.parent",
        "invoice.docstatus = %(docstatus)s",
        "item.docstatus = %(docstatus)s",
        "item.item_code IN %(item_codes)s",
        "item.warehouse = %(warehouse)s",
    ]
    values = {
        "docstatus": docstatus,
        "item_codes": tuple(item_codes),
        "warehouse": warehouse,
    }

    if docstatus == 1:
        # Match ERPNext get_pos_reserved_qty: submitted but not consolidated.
        conditions.append("COALESCE(invoice.consolidated_invoice, '') = ''")
    if exclude_returns:
        conditions.append("COALESCE(invoice.is_return, 0) = 0")
    if exclude_invoice:
        conditions.append("invoice.name != %(exclude_invoice)s")
        values["exclude_invoice"] = exclude_invoice

    select_qty = (
        f"item.`{qty_field}` AS reserved_qty"
        if for_update
        else f"COALESCE(SUM(item.`{qty_field}`), 0) AS reserved_qty"
    )
    group_or_lock = (
        "ORDER BY invoice.name, item.name FOR UPDATE"
        if for_update
        else "GROUP BY item.item_code"
    )
    rows = frappe.db.sql(
        f"""
        SELECT item.item_code, {select_qty}
        FROM `tabPOS Invoice` invoice
        INNER JOIN `tab{child_table}` item ON invoice.name = item.parent
        WHERE {" AND ".join(conditions)}
        {group_or_lock}
        """,
        values,
        as_dict=True,
    )
    reserved = {}
    for row in rows:
        reserved[row.item_code] = flt(reserved.get(row.item_code)) + flt(
            row.reserved_qty
        )
    return reserved


def _merge_qty_maps(*qty_maps):
    merged = {}
    for qty_map in qty_maps:
        for item_code, qty in qty_map.items():
            merged[item_code] = flt(merged.get(item_code)) + flt(qty)
    return merged


def get_submitted_reserved_qty_map(item_codes, warehouse, for_update=False):
    """Bulk equivalent of ERPNext get_pos_reserved_qty for several items."""
    return _merge_qty_maps(
        _get_reserved_qty_from_child_table(
            "POS Invoice Item",
            "stock_qty",
            item_codes,
            warehouse,
            1,
            for_update=for_update,
        ),
        _get_reserved_qty_from_child_table(
            "Packed Item",
            "qty",
            item_codes,
            warehouse,
            1,
            for_update=for_update,
        ),
    )


def get_draft_reserved_qty_map(
    item_codes,
    warehouse,
    exclude_invoice=None,
    for_update=False,
):
    """Reserve stock already promised by active URY draft POS Invoices."""
    return _merge_qty_maps(
        _get_reserved_qty_from_child_table(
            "POS Invoice Item",
            "stock_qty",
            item_codes,
            warehouse,
            0,
            exclude_invoice=exclude_invoice,
            exclude_returns=True,
            for_update=for_update,
        ),
        _get_reserved_qty_from_child_table(
            "Packed Item",
            "qty",
            item_codes,
            warehouse,
            0,
            exclude_invoice=exclude_invoice,
            exclude_returns=True,
            for_update=for_update,
        ),
    )


def _get_stock_details(item_codes, warehouse, exclude_invoice=None, item_metadata=None):
    """Return fresh stock for items, including submitted and active draft reservations."""
    if not item_codes:
        return {}

    item_metadata = item_metadata or _get_item_metadata(item_codes)
    bundle_codes = _get_active_product_bundle_codes(item_codes, item_metadata)
    direct_stock_codes = {
        item_code
        for item_code in item_codes
        if cint(item_metadata[item_code].is_stock_item)
    }

    bundle_components = {}
    physical_item_codes = set(direct_stock_codes)
    for bundle_code in bundle_codes:
        availability, _, _ = get_product_bundle_stock_availability(
            bundle_code, warehouse, 1
        )
        required_by_component = {}
        available_by_component = {}
        for component in availability:
            component_code = component["item_code"]
            required_by_component[component_code] = flt(
                required_by_component.get(component_code)
            ) + flt(component["required"])
            current_available = available_by_component.get(component_code)
            component_available = flt(component["available"])
            available_by_component[component_code] = (
                component_available
                if current_available is None
                else min(current_available, component_available)
            )
            physical_item_codes.add(component_code)
        bundle_components[bundle_code] = (required_by_component, available_by_component)

    reservation_codes = physical_item_codes | bundle_codes
    bin_qty = _get_bin_qty_map(direct_stock_codes, warehouse)
    submitted_reserved = get_submitted_reserved_qty_map(direct_stock_codes, warehouse)
    draft_reserved = get_draft_reserved_qty_map(
        reservation_codes, warehouse, exclude_invoice=exclude_invoice
    )
    global_negative_stock = cint(
        frappe.db.get_single_value("Stock Settings", "allow_negative_stock", cache=True)
    )

    details = {}
    for item_code in item_codes:
        item = item_metadata[item_code]
        negative_stock_allowed = bool(
            global_negative_stock or cint(item.allow_negative_stock)
        )

        if item_code in direct_stock_codes:
            available_qty = (
                flt(bin_qty.get(item_code))
                - flt(submitted_reserved.get(item_code))
                - flt(draft_reserved.get(item_code))
            )
            is_stock_item = True
        elif item_code in bundle_codes:
            required_by_component, available_by_component = bundle_components[item_code]
            possible_bundle_qty = []
            for component_code, required_qty in required_by_component.items():
                if required_qty > 0:
                    possible_bundle_qty.append(
                        (
                            flt(available_by_component[component_code])
                            - flt(draft_reserved.get(component_code))
                        )
                        / required_qty
                    )

            # Product Bundles containing only non-stock items are effectively unlimited,
            # matching ERPNext's get_bundle_availability sentinel.
            available_qty = (
                min(possible_bundle_qty)
                if possible_bundle_qty
                else 1000000 - flt(draft_reserved.get(item_code))
            )
            is_stock_item = True
        else:
            available_qty = 0
            is_stock_item = False

        details[item_code] = {
            "item_code": item_code,
            "available_qty": flt(available_qty),
            "is_stock_item": bool(is_stock_item),
            "stock_uom": item.stock_uom,
            "negative_stock_allowed": negative_stock_allowed,
        }

    return details


def _validate_excluded_invoice(exclude_invoice, profile):
    if not exclude_invoice:
        return

    invoice = frappe.db.get_value(
        "POS Invoice",
        exclude_invoice,
        ["name", "docstatus", "branch", "pos_profile"],
        as_dict=True,
    )
    if (
        not invoice
        or invoice.docstatus != 0
        or invoice.branch != profile.branch
        or invoice.pos_profile != profile.name
    ):
        frappe.throw(_("The order to exclude is not an active order for this POS Profile."), frappe.PermissionError)


#GetTable  decripted temporarily
# @frappe.whitelist()
# def getTable(room):
#     branch_name = getBranch()   
#     tables = frappe.get_all(
#         "URY Table",
#         fields=["name", "occupied", "latest_invoice_time", "is_take_away", "restaurant_room","table_shape","no_of_seats","layout_x","layout_y"],
#         filters={"branch": branch_name,"restaurant_room":room,}
#     )    
#     return tables

@frappe.whitelist()
def getRestaurantMenu(pos_profile, room=None, order_type=None):
    menu_items = []
    menu_items_with_image = []

    user_role = frappe.get_roles()

    pos_profile, branch_name = get_pos_profile_for_current_branch(pos_profile)

    cashier = any(
        role.role in user_role for role in pos_profile.role_allowed_for_billing
    )
    restaurant = frappe.db.get_value("URY Restaurant", {"branch": branch_name}, "name")
    
    if room:
    
        room_wise_menu = frappe.db.get_value(
            "URY Restaurant", restaurant, "room_wise_menu"
        )
        
        if room_wise_menu:
            menu = frappe.db.get_value(
                "Menu for Room",
                {"parent": restaurant, "room": room},
                "menu"
            )
            if not menu:
                 menu = frappe.db.get_value("URY Restaurant", restaurant, "active_menu")
        else:
            menu = frappe.db.get_value("URY Restaurant", restaurant, "active_menu")

    elif cashier and order_type:
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

    # Default menu if nothing is selected
    else:
        menu = frappe.db.get_value("URY Restaurant", restaurant, "active_menu")
    
    if not menu:
        frappe.throw(_("Please set an active menu for Restaurant {0}").format(restaurant))
    
    
    # Get menu items (your existing code)
    menu_items = frappe.get_all(
        "URY Menu Item",
        filters={"parent": menu, "disabled": 0},
        fields=["item", "item_name", "rate", "special_dish", "disabled", "course"],
        order_by="item_name asc"
    )

    item_codes = list(dict.fromkeys(item.item for item in menu_items))
    item_metadata = _get_item_metadata(item_codes)
    stock_details = _get_stock_details(
        item_codes,
        pos_profile.warehouse,
        item_metadata=item_metadata,
    )
    for item in menu_items:
        menu_item = {
            "item": item.item,
            "item_name": _(item.item_name) if item.item_name else item.item_name,
            "rate": item.rate,
            "special_dish": item.special_dish,
            "disabled": item.disabled,
            "item_image": item_metadata[item.item].image,
            "course": item.course,
            "course_label": _(item.course) if item.course else item.course,
        }
        menu_item.update(stock_details[item.item])
        menu_items_with_image.append(menu_item)
    modified = frappe.db.get_value("URY Menu", menu, "modified")
    
    
    return {
        "items": menu_items_with_image,
        "modified_time": modified,
        "name": menu
    }


@frappe.whitelist()
def getStockAvailability(
    pos_profile,
    item_code=None,
    item_codes=None,
    exclude_invoice=None,
):
    """Return fresh stock for one item or a JSON/list batch in a stable map shape."""
    profile, _ = get_pos_profile_for_current_branch(pos_profile)
    _validate_excluded_invoice(exclude_invoice, profile)

    requested = _normalise_item_codes(item_code=item_code, item_codes=item_codes)
    if not requested:
        frappe.throw(_("Please provide item_code or item_codes."))

    return {
        "stocks": _get_stock_details(
            requested,
            profile.warehouse,
            exclude_invoice=exclude_invoice,
        )
    }

@frappe.whitelist()
def getMenuCourses():
    courses = frappe.get_all("URY Menu Course", fields=["name"])
    return [{"name": d.name, "label": _(d.name)} for d in courses]

@frappe.whitelist()
def getBranch():
    user = frappe.session.user
    sql_query = """
        SELECT b.branch
        FROM `tabURY User` AS a
        INNER JOIN `tabBranch` AS b ON a.parent = b.name
        WHERE a.user = %s
    """
    branch_array = frappe.db.sql(sql_query, user, as_dict=True)
    if not branch_array:
        frappe.throw("User is not Associated with any Branch.Please refresh Page")

    branch_name = branch_array[0].get("branch")

    return branch_name

@frappe.whitelist()
def getBranchRoom():
    user = frappe.session.user
    sql_query = """
        SELECT b.branch , a.room
        FROM `tabURY User` AS a
        INNER JOIN `tabBranch` AS b ON a.parent = b.name
        WHERE a.user = %s
    """
    branch_array = frappe.db.sql(sql_query, user, as_dict=True)
    
    branch_name = branch_array[0].get("branch")
    room_name = branch_array[0].get("room")

    if not branch_name:
        frappe.throw("Branch information is missing for the user. Please contact your administrator.")

    if not room_name:
        frappe.throw("No room assigned to this user. Please contact your administrator.")

    return [{
        "name":room_name ,
        "branch": branch_name,
    }]

@frappe.whitelist()
def getRoom():
    user = frappe.session.user
    sql_query = """
        SELECT b.branch, a.room
        FROM `tabURY User` AS a
        INNER JOIN `tabBranch` AS b ON a.parent = b.name
        WHERE a.user = %s
    """
    branch_array = frappe.db.sql(sql_query, user, as_dict=True)
    
    if not branch_array:
        frappe.throw("No branch or room information found for the user. Please contact your administrator.")
    
    room_details = [
        {
            "name": row.get("room"),
            "branch": row.get("branch")
        } 
        for row in branch_array
    ]

    return room_details

@frappe.whitelist()
def getModeOfPayment():
    posDetails = getPosProfile()
    posProfile = posDetails["pos_profile"]
    posProfiles = frappe.get_doc("POS Profile", posProfile)
    mode_of_payments = posProfiles.payments
    modeOfPayments = []
    for mop in mode_of_payments:
        modeOfPayments.append(
            {"mode_of_payment": mop.mode_of_payment, "opening_amount": float(0)}
        )
    return modeOfPayments


def format_merged_table_label(primary, merged_tables=None):
    if not primary:
        return ""
    partners = [p.strip() for p in (merged_tables or "").split(",") if p.strip()]
    if not partners:
        return primary
    return " + ".join([primary] + sorted(partners))


def _backfill_split_groups(invoices):
    parent_names = [
        inv["custom_split_from"]
        for inv in invoices
        if inv.get("custom_split_from") and not inv.get("custom_split_group")
    ]
    if not parent_names:
        return

    parent_rows = frappe.get_all(
        "POS Invoice",
        filters={"name": ["in", parent_names]},
        fields=["name", "custom_split_group"],
    )
    parent_group_map = {
        row.name: row.custom_split_group
        for row in parent_rows
        if row.custom_split_group
    }
    for inv in invoices:
        if not inv.get("custom_split_group") and inv.get("custom_split_from"):
            inv["custom_split_group"] = parent_group_map.get(inv["custom_split_from"])


def _enrich_split_group_meta(invoices):
    if not invoices:
        return invoices

    _backfill_split_groups(invoices)

    groups = list(
        {inv.get("custom_split_group") for inv in invoices if inv.get("custom_split_group")}
    )
    if not groups:
        for inv in invoices:
            inv["split_index"] = 0
            inv["split_total"] = 0
            inv["split_siblings"] = []
        return invoices

    group_members = frappe.db.sql(
        """
        SELECT name, custom_split_group
        FROM `tabPOS Invoice`
        WHERE custom_split_group IN %(groups)s AND docstatus < 2
        ORDER BY creation asc
        """,
        {"groups": groups},
        as_dict=True,
    )

    group_order = {}
    for row in group_members:
        group_order.setdefault(row.custom_split_group, []).append(row.name)

    for group, names in list(group_order.items()):
        children = frappe.get_all(
            "POS Invoice",
            filters={"custom_split_from": ["in", names], "docstatus": ["<", 2]},
            fields=["name"],
            order_by="creation asc",
        )
        for child in children:
            if child.name not in names:
                names.append(child.name)

    for inv in invoices:
        group = inv.get("custom_split_group")
        if not group or group not in group_order:
            inv["split_index"] = 0
            inv["split_total"] = 0
            inv["split_siblings"] = []
            continue
        names = group_order[group]
        inv["split_total"] = len(names)
        inv["split_siblings"] = [name for name in names if name != inv["name"]]
        try:
            inv["split_index"] = names.index(inv["name"]) + 1
        except ValueError:
            inv["split_index"] = 0
            inv["split_total"] = 0
            inv["split_siblings"] = []

    return invoices


@frappe.whitelist()
def get_split_group(invoice):
    group = frappe.db.get_value("POS Invoice", invoice, "custom_split_group")
    if not group:
        split_from = frappe.db.get_value("POS Invoice", invoice, "custom_split_from")
        if split_from:
            group = frappe.db.get_value("POS Invoice", split_from, "custom_split_group")
    if not group:
        return {"invoices": [], "current": invoice, "group": None}

    split_fields = [
        "name",
        "custom_split_from",
        "custom_split_group",
        "invoice_printed",
        "restaurant_table",
        "custom_merged_tables",
        "rounded_total",
        "grand_total",
        "customer",
        "customer_name",
        "status",
        "docstatus",
        "posting_date",
        "posting_time",
        "order_type",
        "cashier",
        "waiter",
        "mobile_number",
        "net_total",
        "total_taxes_and_charges",
        "creation",
        "additional_discount_percentage",
        "discount_amount",
    ]

    invoices = frappe.get_all(
        "POS Invoice",
        filters={"custom_split_group": group, "docstatus": ["<", 2]},
        fields=split_fields,
        order_by="creation asc",
    )

    member_names = [inv["name"] for inv in invoices]
    if member_names:
        children = frappe.get_all(
            "POS Invoice",
            filters={"custom_split_from": ["in", member_names], "docstatus": ["<", 2]},
            fields=split_fields,
            order_by="creation asc",
        )
        existing = {inv["name"] for inv in invoices}
        for child in children:
            if child.name not in existing:
                invoices.append(child)
                existing.add(child.name)

    invoices.sort(key=lambda row: row.get("creation") or row.get("name"))

    total = len(invoices)
    for index, inv in enumerate(invoices, start=1):
        inv["split_index"] = index
        inv["split_total"] = total
        inv["is_original"] = not inv.get("custom_split_from")
        inv["split_siblings"] = [row["name"] for row in invoices if row["name"] != inv["name"]]

    return {"invoices": invoices, "current": invoice, "group": group}


@frappe.whitelist()
def getInvoiceForCashier(status, cashier, limit, limit_start):
    branch = getBranch()
    updatedlist = []
    limit = int(limit)+1
    limit_start = int(limit_start)
    if status == "Draft":
        invoices = frappe.db.sql(
            """
            SELECT 
                name, invoice_printed, grand_total, restaurant_table, custom_merged_tables,
                cashier, waiter, net_total, posting_time, 
                total_taxes_and_charges, customer, status, mobile_number, 
                posting_date, rounded_total, order_type 
            FROM `tabPOS Invoice` 
            WHERE branch = %s AND status = %s AND cashier = %s
            AND (invoice_printed = 1 OR (invoice_printed = 0 AND COALESCE(restaurant_table, '') = ''))
            ORDER BY modified desc
            LIMIT %s OFFSET %s
            """,
            (branch, status, cashier, limit,limit_start),
            as_dict=True,
        )
        updatedlist.extend(invoices)
    elif status == "Unbilled":
        
        docstatus = "Draft"
        invoices = frappe.db.sql(
            """
            SELECT 
                name, invoice_printed, grand_total, restaurant_table, custom_merged_tables,
                cashier, waiter, net_total, posting_time, 
                total_taxes_and_charges, customer, status, mobile_number, 
                posting_date, rounded_total, order_type 
            FROM `tabPOS Invoice` 
            WHERE branch = %s AND status = %s AND cashier = %s
            AND (invoice_printed = 0 AND restaurant_table IS NOT NULL)
            ORDER BY modified desc
            LIMIT %s OFFSET %s
            """,
            (branch, docstatus, cashier, limit, limit_start),
            as_dict=True,
        )
        updatedlist.extend(invoices)
    elif status == "Recently Paid":
        docstatus = "Paid"
        invoices = frappe.db.sql(
            """
            SELECT 
                name, invoice_printed, grand_total, restaurant_table, custom_merged_tables,
                cashier, waiter, net_total, posting_time, 
                total_taxes_and_charges, customer, status, mobile_number,
                posting_date, rounded_total, order_type,additional_discount_percentage,discount_amount 
            FROM `tabPOS Invoice` 
            WHERE branch = %s AND status = %s AND cashier = %s
            ORDER BY modified desc
            LIMIT %s OFFSET %s
            """,
            (branch, docstatus, cashier, limit, limit_start),
            as_dict=True,
        )
        updatedlist.extend(invoices)    
    else:
        
        invoices = frappe.db.sql(
            """
            SELECT 
                name, invoice_printed, grand_total, restaurant_table, custom_merged_tables,
                cashier, waiter, net_total, posting_time, 
                total_taxes_and_charges, customer, status, mobile_number,
                posting_date, rounded_total, order_type,additional_discount_percentage,discount_amount
            FROM `tabPOS Invoice` 
            WHERE branch = %s AND status = %s AND cashier = %s
            ORDER BY modified desc
            LIMIT %s OFFSET %s
            """,
            (branch, status, cashier, limit, limit_start),
            as_dict=True,
        )

        updatedlist.extend(invoices)
    if len(updatedlist) == limit and status != "Recently Paid":
            next = True
            updatedlist.pop()
    else:
            next = False   
    return  { "data":updatedlist,"next":next}



@frappe.whitelist()
def getPosInvoice(status, limit, limit_start):
    branch = getBranch()
    updatedlist = []
    limit = int(limit)+1
    limit_start = int(limit_start)
    if status == "Draft":
        invoices = frappe.db.sql(
            """
            SELECT 
                name, invoice_printed, grand_total, restaurant_table, custom_merged_tables,
                cashier, waiter, net_total, posting_time, 
                total_taxes_and_charges, customer, status, mobile_number, 
                posting_date, rounded_total, order_type,
                custom_split_group, custom_split_from,
                custom_merged_pos_invoice, custom_merged_total,
                additional_discount_percentage, discount_amount
            FROM `tabPOS Invoice` 
            WHERE branch = %s AND status = %s 
            AND (invoice_printed = 1 OR (invoice_printed = 0 AND COALESCE(restaurant_table, '') = ''))
            ORDER BY modified desc
            LIMIT %s OFFSET %s
            """,
            (branch, status, limit,limit_start),
            as_dict=True,
        )
        updatedlist.extend(invoices)
    elif status == "Unbilled":
        
        docstatus = "Draft"
        invoices = frappe.db.sql(
            """
            SELECT 
                name, invoice_printed, grand_total, restaurant_table, custom_merged_tables,
                cashier, waiter, net_total, posting_time, 
                total_taxes_and_charges, customer, status, mobile_number, 
                posting_date, rounded_total, order_type,
                custom_split_group, custom_split_from,
                custom_merged_pos_invoice, custom_merged_total,
                additional_discount_percentage, discount_amount
            FROM `tabPOS Invoice` 
            WHERE branch = %s AND status = %s 
            AND (invoice_printed = 0 AND restaurant_table IS NOT NULL)
            ORDER BY modified desc
            LIMIT %s OFFSET %s
            """,
            (branch, docstatus, limit, limit_start),
            as_dict=True,
        )
        updatedlist.extend(invoices)
    elif status == "Recently Paid":
        docstatus = "Paid"
        invoices = frappe.db.sql(
            """
            SELECT 
                name, invoice_printed, grand_total, restaurant_table, custom_merged_tables,
                cashier, waiter, net_total, posting_time, 
                total_taxes_and_charges, customer, status, mobile_number,
                posting_date, rounded_total, order_type, additional_discount_percentage,
                discount_amount, custom_split_group, custom_split_from,
                custom_merged_pos_invoice, custom_merged_total
            FROM `tabPOS Invoice` 
            WHERE branch = %s AND status = %s 
            ORDER BY modified desc
            LIMIT %s OFFSET %s
            """,
            (branch, docstatus, limit, limit_start),
            as_dict=True,
        )
        updatedlist.extend(invoices)    
    else:
        
        invoices = frappe.db.sql(
            """
            SELECT 
                name, invoice_printed, grand_total, restaurant_table, custom_merged_tables,
                cashier, waiter, net_total, posting_time, 
                total_taxes_and_charges, customer, status, mobile_number,
                posting_date, rounded_total, order_type, additional_discount_percentage,
                discount_amount, custom_split_group, custom_split_from,
                custom_merged_pos_invoice, custom_merged_total
            FROM `tabPOS Invoice` 
            WHERE branch = %s AND status = %s 
            ORDER BY modified desc
            LIMIT %s OFFSET %s
            """,
            (branch, status, limit, limit_start),
            as_dict=True,
        )

        updatedlist.extend(invoices)
    if len(updatedlist) == limit and status != "Recently Paid":
            next = True
            updatedlist.pop()
    else:
            next = False
    updatedlist = _enrich_split_group_meta(updatedlist)
    return  { "data":updatedlist,"next":next}


@frappe.whitelist()
def searchPosInvoice(query,status):
    if not query:
        return {"data": [], "next": False}
    query = query.lower()
    filters = {"status": "Paid" if status == "Recently Paid" else status}
    
    # Add additional conditions for Unbilled status
    if status == "Unbilled":
        filters.update({
            "status":"draft",
            "restaurant_table": ["not in", [None, ""]],  # Check if restaurant_table has value
            "invoice_printed": 0  # Check if invoice_printed is 0
        })
    pos_invoices = frappe.get_all(
        "POS Invoice",
        filters=filters,           
        or_filters=[
            ["name", "like", f"%{query}%"],
            ["customer", "like", f"%{query}%"],
            ["mobile_number", "like", f"%{query}%"],
        ],
        fields=[
            "name",
            "customer",
            "grand_total",
            "posting_date",
            "posting_time",
            "order_type",
            "restaurant_table",
            "custom_merged_tables",
            "status",
            "rounded_total",
            "net_total",
            "mobile_number",
            "invoice_printed",
            "cashier",
            "waiter",
            "total_taxes_and_charges",
            "custom_split_group",
            "custom_split_from",
            "custom_merged_pos_invoice",
            "custom_merged_total",
            "additional_discount_percentage",
            "discount_amount"
        ],
        limit_page_length=10 
    )
    pos_invoices = _enrich_split_group_meta(pos_invoices)
    
    return {"data": pos_invoices, "next": len(pos_invoices) == 10}
    

@frappe.whitelist()
def get_select_field_options():
    options = frappe.get_meta("POS Invoice").get_field("order_type").options
    if options:
        return [{"name": option} for option in options.split("\n")]
    else:
        return []


@frappe.whitelist()
def fav_items(customer):
    pos_invoices = frappe.get_all(
        "POS Invoice", filters={"customer": customer}, fields=["name"]
    )
    item_qty = {}

    for invoice in pos_invoices:
        pos_invoice = frappe.get_doc("POS Invoice", invoice.name)
        for item in pos_invoice.items:
            item_name = item.item_name
            qty = item.qty
            if item_name not in item_qty:
                item_qty[item_name] = 0
            item_qty[item_name] += qty

    favorite_items = [
        {"item_name": item_name, "qty": qty} for item_name, qty in item_qty.items()
    ]
    return favorite_items

@frappe.whitelist()
def getCashier(room):
    branch = getBranch()
    cashier = None
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
    if pos_opening_list:
        cashier = frappe.db.get_value(
            "POS Opening Entry",
            {"name": pos_opening_list[0].name},
            "user",)
    return cashier       
    

@frappe.whitelist()
def getPosProfile():
    branchName = getBranch()
    waiter = frappe.session.user
    bill_present = False
    qz_host = None
    printer = None
    cashier = None
    owner = None
    posProfile = frappe.db.exists("POS Profile", {"branch": branchName})
    pos_profiles = frappe.get_doc("POS Profile", posProfile)
    global_defaults = frappe.get_single('Global Defaults')
    disable_rounded_total = global_defaults.disable_rounded_total
    

    if pos_profiles.branch == branchName:
        pos_profile_name = pos_profiles.name
        warehouse = pos_profiles.warehouse
        branch = pos_profiles.branch
        company = pos_profiles.company
        tableAttention = pos_profiles.table_attention_time
        get_cashier = frappe.get_doc("POS Profile", pos_profile_name)
        print_format = pos_profiles.print_format
        paid_limit=pos_profiles.paid_limit
        enable_discount = pos_profiles.custom_enable_discount
        multiple_cashier = pos_profiles.custom_enable_multiple_cashier
        edit_order_type = pos_profiles.custom_edit_order_type
        enable_kot_reprint = pos_profiles.custom_enable_kot_reprint
        if multiple_cashier:
            details = getBranchRoom()
            room = details[0].get('name') 
            branch = details[0].get('branch')

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
            if pos_opening_list:
                pos_opened_cashier = frappe.db.get_value(
                    "POS Opening Entry",
                    {"name": pos_opening_list[0].name},
                    "user",)
            else:
                pos_opened_cashier = None
            for user_details in get_cashier.applicable_for_users:
                if user_details.custom_main_cashier:
                    owner = user_details.user
                
                if frappe.session.user == owner:
                    cashier = owner
                else:
                    cashier = pos_opened_cashier    
                
        else:
            opening = get_single_cashier_opening(
                pos_profile_name,
                required=False,
            )
            if opening:
                cashier = opening.user
                owner = opening.user
            else:
                # The legacy POS uses this value to create the opening entry.
                # POS Invoice validation still blocks sales until it is Open.
                cashier = frappe.session.user
                owner = frappe.session.user
        
        qz_print = pos_profiles.qz_print
        print_type = None

        printers = []
        for pos_profile in pos_profiles.printer_settings:
            
            if pos_profile.bill == 1:
                printers.append(pos_profile.printer)
                bill_present = True
                
        if printers:
            printer = ",".join(printers)

        if qz_print == 1:
            print_type = "qz"
            qz_host = pos_profiles.qz_host

        elif bill_present == True:
            print_type = "network"

        else:
            print_type = "socket"

    invoice_details = {
        "pos_profile": pos_profile_name,
        "branch": branch,
        "company": company,
        "waiter": waiter,
        "warehouse": warehouse,
        "cashier": cashier,
        "print_format": print_format,
        "qz_print": qz_print,
        "qz_host": qz_host,
        "printer": printer,
        "print_type": print_type,
        "tableAttention": tableAttention,
        "paid_limit":paid_limit,
        "disable_rounded_total":disable_rounded_total,
        "enable_discount":enable_discount,
        "multiple_cashier":multiple_cashier,
        "owner":owner,
        "edit_order_type":edit_order_type,
        "enable_kot_reprint":enable_kot_reprint

    }

    return invoice_details


@frappe.whitelist()
def getPosInvoiceItems(invoice):
    itemDetails = []
    taxDetails = []
    orderdItems = frappe.get_doc("POS Invoice", invoice)
    posItems = orderdItems.items
    for items in posItems:
        itemDetails.append(
            {
                "name": items.name,
                "item_name": items.item_name,
                "qty": items.qty,
                "rate": items.rate,
                "amount": items.amount,
            }
        )
    taxDetail = orderdItems.taxes
    for tax in taxDetail:
        description = tax.description
        rate = tax.tax_amount
        taxDetails.append(
            {
                "description": description,
                "rate": rate,
            }
        )
    return itemDetails, taxDetails


@frappe.whitelist()
def posOpening():
    branchName = getBranch()
    pos_opening_list = frappe.get_all(
        "POS Opening Entry",
        fields=["name", "docstatus", "status", "posting_date"],
        filters={"branch": branchName},
    )
    flag = 1
    for pos_opening in pos_opening_list:
        if pos_opening.status == "Open" and pos_opening.docstatus == 1:
            flag = 0
    if flag == 1:
        frappe.msgprint(title="Message", indicator="red", msg=("Please Open POS Entry"))
    return flag


@frappe.whitelist()
def getAggregator():
    branchName = getBranch()
    aggregatorList = frappe.get_all(
        "Aggregator Settings",
        fields=["customer"],
        filters={"parent": branchName, "parenttype": "Branch"},
    )
    return aggregatorList


@frappe.whitelist()
def getAggregatorItem(aggregator, pos_profile=None):
    pos_profile, branchName = get_pos_profile_for_current_branch(pos_profile)
    aggregatorItem = []
    aggregatorItemList = []
    priceList = frappe.db.get_value(
        "Aggregator Settings",
        {"customer": aggregator, "parent": branchName, "parenttype": "Branch"},
        "price_list",
    )
    aggregatorItem = frappe.get_all(
        "Item Price",
        fields=["item_code", "item_name", "price_list_rate"],
        filters={"selling": 1, "price_list": priceList},
    )
    item_codes = list(dict.fromkeys(item.item_code for item in aggregatorItem))
    item_metadata = _get_item_metadata(item_codes)
    stock_details = _get_stock_details(
        item_codes,
        pos_profile.warehouse,
        item_metadata=item_metadata,
    )
    for item in aggregatorItem:
        metadata = item_metadata[item.item_code]
        if cint(metadata.disabled):
            continue

        aggregator_item = {
            "item": item.item_code,
            "item_name": item.item_name,
            "rate": item.price_list_rate,
            "item_image": metadata.image,
        }
        aggregator_item.update(stock_details[item.item_code])
        aggregatorItemList.append(aggregator_item)

    return aggregatorItemList

@frappe.whitelist()
def getAggregatorMOP(aggregator):
    branchName = getBranch()
    
    modeOfPayment = frappe.db.get_value(
        "Aggregator Settings",
        {"customer": aggregator, "parent": branchName, "parenttype": "Branch"},
        "mode_of_payments",
    )
    modeOfPaymentsList = []
    modeOfPaymentsList.append(
            {"mode_of_payment": modeOfPayment, "opening_amount": float(0)}
    )
    return modeOfPaymentsList
@frappe.whitelist()
def create_customer(customer_name, mobile_number=None, customer_group="Individual", territory="India"):
    if not customer_name:
        frappe.throw("Customer name is required")
    if not mobile_number:
        frappe.throw("Mobile Number is required")
    try:
        validate_phone_number(mobile_number, throw=True)
    except Exception:
        frappe.throw("Invalid mobile number format")

    """Create a new customer"""
    try:
        customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name,
            "mobile_number": mobile_number,
            "customer_group": customer_group,
            "territory": territory
        })
        customer.insert(ignore_permissions=True)
        frappe.db.commit()

        return {
            "status": "success",
            "message": "Customer created successfully",
            "customer_name": customer_name,
            "mobile_number": mobile_number,
            "customer_group": customer_group,
            "territory": territory
        }

    except Exception as e:
        frappe.log_error(message=frappe.get_traceback(), title="Customer Creation Failed")
        return {
            "status": "error",
            "message": str(e)
        }

@frappe.whitelist()
def validate_pos_close(pos_profile): 
    enable_unclosed_pos_check = frappe.db.get_value("POS Profile",pos_profile,"custom_daily_pos_close")
    
    if enable_unclosed_pos_check:
        current_datetime = frappe.utils.now_datetime()
        start_of_day = current_datetime.replace(hour=5, minute=0, second=0, microsecond=0)
        
        if current_datetime > start_of_day:
            previous_day = start_of_day - timedelta(days=1)
            
        else:
            previous_day = start_of_day
    
        unclosed_pos_opening = frappe.db.exists(
            "POS Opening Entry",
            {
                "posting_date": previous_day.date(),
                "status": "Open",
                "pos_profile": pos_profile,
                "docstatus": 1
            }
        )
    
        if unclosed_pos_opening:
            return "Failed"
        
        return "Success"
    
    return "Success"


@frappe.whitelist()
def merge_bills(primary_invoice, secondary_invoice):

    try:

        if primary_invoice == secondary_invoice:
            frappe.throw("Cannot merge an invoice with itself.")

        primary_doc = frappe.get_doc("POS Invoice",primary_invoice,)
        secondary_doc = frappe.get_doc("POS Invoice",secondary_invoice,)

        # Validation
        if (primary_doc.docstatus != 0 or secondary_doc.docstatus != 0):
            frappe.throw("Both invoices must be in Draft state to merge.")

        if primary_doc.custom_merged_pos_invoice:
            frappe.throw("This bill already includes a merged bill.")

        if secondary_doc.custom_merged_pos_invoice:
            frappe.throw("The selected bill already includes another bill.")

        if not secondary_doc.items:
            frappe.throw("The selected bill has no items to merge.")


        def update_merge_details(target_invoice,source_invoice,):

            doc = frappe.get_doc("POS Invoice",target_invoice,)

            # clear old rows
            doc.set("custom_merged_pos_invoice_details",[],)

            # only linked invoice items
            for item in source_invoice.items:

                doc.append(
                    "custom_merged_pos_invoice_details",
                    {
                        "item_code": item.item_code,
                        "item_name": item.item_name,
                        "qty": item.qty,
                        "rate": item.rate,
                        "amount": item.amount,
                    },
                )

            doc.custom_merged_total = source_invoice.rounded_total

            doc.flags.ignore_version = True

            doc.save(
                ignore_permissions=True,
                ignore_version=True,
            )


        # Update merge references directly
        frappe.db.set_value(
            "POS Invoice",
            primary_doc.name,
            "custom_merged_pos_invoice",
            secondary_doc.name,
            update_modified=False,
        )

        frappe.db.set_value(
            "POS Invoice",
            secondary_doc.name,
            "custom_merged_pos_invoice",
            primary_doc.name,
            update_modified=False,
        )


        # Build detail table
        update_merge_details(primary_doc.name,secondary_doc,)

        update_merge_details(secondary_doc.name,primary_doc,)


        frappe.db.commit()


        return {
            "status": "success",
            "message": "Bills merged successfully",
            "name": primary_doc.name,
        }


    except Exception as e:

        frappe.db.rollback()

        frappe.log_error(
            title="Bill Merge Error",
            message=frappe.get_traceback(),
        )

        return {
            "status": "error",
            "message": str(e),
        }
