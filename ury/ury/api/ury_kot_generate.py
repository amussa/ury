import json
from collections import OrderedDict

import frappe
from frappe.utils import flt

from ury.ury.api.ury_production_routing import (
    get_order_menu,
    get_production_route_state,
)


# Load JSON data or return as is if it's already a Python dictionary
def load_json(data):
    if isinstance(data, str):
        return json.loads(data)
    return data


# Create a kitchen-facing list of order items from invoice/cart lines. Price
# options are deliberately not part of the identity: the kitchen prepares the
# same item whether it was sold at the normal or promotional price. Comments
# remain part of the identity so distinct preparation instructions stay on
# distinct KOT lines.
def create_order_items(items):
    order_items = OrderedDict()
    for item in items:
        item_code = item.get("item", item.get("item_code"))
        comments = item.get("comment", item.get("comments", "")) or ""
        key = (item_code, comments)
        if key not in order_items:
            order_items[key] = {
                "item_code": item_code,
                "qty": 0,
                "item_name": item["item_name"],
                "comments": comments,
            }
        order_items[key]["qty"] = flt(order_items[key]["qty"]) + flt(item["qty"])
    return list(order_items.values())


def _order_item_key(item):
    return (item.get("item_code"), item.get("comments") or "")


# Create a KOT (Kitchen Order Ticket) document
def create_kot_doc(
    invoice_id,
    customer,
    restaurant_table,
    items,
    kot_type,
    comments,
    pos_profile_id,
    kot_naming_series,
    production,
    *,
    ignore_permissions=False,
    menu=None,
):
    pos_invoice = frappe.get_doc("POS Invoice", invoice_id)
    order_number = pos_invoice.custom_ury_order_number
    is_aggregator = 0
    if pos_invoice.order_type == "Aggregators":
        is_aggregator = 1
    kot_doc = frappe.get_doc(
        {
            "doctype": "URY KOT",
            "invoice": invoice_id,
            "restaurant_table": restaurant_table,
            "custom_merged_tables": pos_invoice.get("custom_merged_tables"),
            "customer_name": customer,
            "pos_profile": pos_profile_id,
            "comments": comments,
            "type": kot_type,
            "naming_series": kot_naming_series,
            "production": production,
            "aggregator_id":pos_invoice.custom_aggregator_id,
            "is_aggregator":is_aggregator,
            "order_no":order_number
        }
    )
    if not menu:
        branch = frappe.db.get_value("POS Profile", pos_profile_id, "branch")
        menu = get_order_menu(branch, restaurant_table)

    for item in items:
        course = frappe.db.get_value("URY Menu Item", {"item": item["item_code"],"parent":menu}, "course")
        kot_doc.append(
            "kot_items",
            {
                "item": item["item_code"],
                "item_name": item["item_name"],
                "quantity": item["qty"],
                "comments": item["comments"],
                "course":course
            },
        )
    if ignore_permissions:
        # Only the private waiter order service can opt into this. The flag is
        # retained by the document for the subsequent submit permission check.
        kot_doc.flags.ignore_permissions = True
    kot_doc.insert()
    kot_doc.submit()
    return kot_doc.name

# Function to get all production item groups for a given branch
def get_all_production_item_groups(branch):
    productions = frappe.db.get_all(
        "URY Production Unit", filters={"branch": branch}, fields=["name"]
    )
    if productions:
        all_production_item_groups = set()
        for production in productions:
            productionItemGroupslist = frappe.get_all(
                "URY Production Item Groups",
                fields=["item_group"],
                filters={
                    "parent": production.name,
                    "parenttype": "URY Production Unit",
                },
                order_by="idx",
            )
            productionItemGroups = [
                item_group.item_group for item_group in productionItemGroupslist
            ]
            all_production_item_groups.update(productionItemGroups)
        return all_production_item_groups


# Process items to create KOT documents
def process_items_for_kot(
    invoice_id,
    customer,
    restaurant_table,
    items,
    comments,
    pos_profile_id,
    kot_naming_series,
    kot_type,
    *,
    ignore_permissions=False,
):
    result = {"created_kots": [], "unrouted_items": []}
    kot_items = create_order_items(items)
    pos_profile = frappe.get_doc("POS Profile", pos_profile_id)
    productions = frappe.db.get_all(
        "URY Production Unit", filters={"branch": pos_profile.branch}, fields=["name"]
    )

    if productions:
        menu = get_order_menu(pos_profile.branch, restaurant_table)
        route_state = get_production_route_state(
            pos_profile.branch,
            [item["item_code"] for item in kot_items],
            menu,
        )
        if route_state["duplicated"]:
            details = ", ".join(
                f"{item}: {' / '.join(units)}"
                for item, units in route_state["duplicated"].items()
            )
            frappe.throw(f"More than one production route is configured for: {details}")

        for item in kot_items:
            item_code = item["item_code"]
            if item_code not in route_state["routes"]:
                result["unrouted_items"].append(item_code)
                item_group = frappe.db.get_value("Item", item_code, "item_group")
                frappe.msgprint(
                    f"No production route is configured for item '{item_code}' "
                    f"(item group '{item_group}', menu '{menu or '-'}')."
                )
        for production in productions:
            production_items = [
                item
                for item in kot_items
                if route_state["routes"].get(item["item_code"]) == production.name
            ]

            if production_items:
                invoice_exist = frappe.db.exists(
                    "URY KOT",
                    {
                        "invoice": invoice_id,
                        "docstatus": 1,
                        "production": production.name,
                    },
                )
                if invoice_exist:
                    kot_type = "Order Modified"

                kot_name = create_kot_doc(
                    invoice_id,
                    customer,
                    restaurant_table,
                    production_items,
                    kot_type,
                    comments,
                    pos_profile_id,
                    kot_naming_series,
                    production.name,
                    ignore_permissions=ignore_permissions,
                    menu=menu,
                )
                result["created_kots"].append(
                    {"name": kot_name, "production": production.name}
                )
        result["unrouted_items"] = list(
            dict.fromkeys(result["unrouted_items"])
        )
        return result
    else:
        frappe.throw(
            "Create URY Production unit against POS Profile: %s " % pos_profile.name
        )


# Process items to create a cancel KOT document
def process_items_for_cancel_kot(
    invoice_id,
    customer,
    restaurant_table,
    items,
    comments,
    pos_profile_id,
    cancel_kot_naming_series,
    kot_type,
    invoiceItems,
):

    kot_items = create_order_items(items)
    invoiceItems = create_order_items(invoiceItems)
    pos_profile = frappe.get_doc("POS Profile", pos_profile_id)
    productions = frappe.db.get_all(
        "URY Production Unit", filters={"branch": pos_profile.branch}, fields=["name"]
    )

    menu = get_order_menu(pos_profile.branch, restaurant_table)
    route_state = get_production_route_state(
        pos_profile.branch,
        [item["item_code"] for item in kot_items],
        menu,
    )
    if route_state["duplicated"]:
        details = ", ".join(
            f"{item}: {' / '.join(units)}"
            for item, units in route_state["duplicated"].items()
        )
        frappe.throw(f"More than one production route is configured for: {details}")

    for production in productions:
        production_items = [
            item
            for item in kot_items
            if route_state["routes"].get(item["item_code"]) == production.name
        ]

        if production_items:
            create_cancel_kot_doc(
                invoice_id,
                restaurant_table,
                production_items,
                kot_type,
                customer,
                comments,
                pos_profile_id,
                cancel_kot_naming_series,
                invoiceItems,
                production.name,
                menu=menu,
            )


# Create a cancel KOT document
def create_cancel_kot_doc(
    invoice_id,
    restaurant_table,
    cancel_items,
    kot_type,
    customer,
    comments,
    pos_profile_id,
    cancel_kot_naming_series,
    invoiceItems,
    production,
    *,
    menu=None,
):
    pos_invoice = frappe.get_doc("POS Invoice", invoice_id)
    order_number = pos_invoice.custom_ury_order_number  
    is_aggregator = 0
    if pos_invoice.order_type == "Aggregators":
        is_aggregator = 1
    kot_list = frappe.db.get_list(
        "URY KOT",
        filters={
            "invoice": invoice_id,
            "type": ("in", ("New Order", "Order Modified")),
        },
        fields=("name"),
    )

    # Find original KOTs related to the cancel items
    original_kots = []
    for cancelItem in cancel_items:
        for kot in kot_list:
            kot_doc = frappe.get_doc("URY KOT", kot.name)
            kot_cancel_items = kot_doc.kot_items
            itemCheckFlag = False
            for kotItem in kot_cancel_items:
                if cancelItem["item_code"] == kotItem.item:
                    itemCheckFlag = True
            if itemCheckFlag:
                original_kots.append(kot_doc.name)
                break

    # Remove duplicate KOT names and join them into a single string
    set_kots = [*set(original_kots)]
    set_kots = ",".join(set_kots)
    kot_cancel_doc = frappe.get_doc(
        {
            "doctype": "URY KOT",
            "naming_series": cancel_kot_naming_series,
            "original_kot": set_kots,
            "restaurant_table": restaurant_table,
            "customer_name": customer,
            "type": kot_type,
            "invoice": invoice_id,
            "pos_profile": pos_profile_id,
            "comments": comments,
            "production": production,
            "is_aggregator":is_aggregator,
            "order_no":order_number
        }
    )

    if not menu:
        branch = frappe.db.get_value("POS Profile", pos_profile_id, "branch")
        menu = get_order_menu(branch, restaurant_table)
    invoice_items_by_key = {
        _order_item_key(item): item for item in create_order_items(invoiceItems)
    }
    for cancelItem in cancel_items:
        course = frappe.db.get_value("URY Menu Item", {"item": cancelItem["item_code"],"parent":menu}, "course")
        item = invoice_items_by_key.get(_order_item_key(cancelItem))
        if not item:
            # Historical KOT payloads did not always preserve line comments.
            item = next(
                (
                    row
                    for row in invoiceItems
                    if cancelItem["item_code"] == row["item_code"]
                ),
                None,
            )
        if item:
            kot_cancel_doc.append(
                "kot_items",
                {
                    "item": cancelItem["item_code"],
                    "item_name": cancelItem["item_name"],
                    "cancelled_qty": abs(flt(cancelItem["qty"])),
                    "quantity": item["qty"],
                    "comments": cancelItem["comments"],
                    "course":course
                },
            )

    kot_cancel_doc.insert()
    kot_cancel_doc.submit()


# Whitelisted function to handle KOT entry
@frappe.whitelist()
def kot_execute(
    invoice_id,
    customer,
    restaurant_table=None,
    current_items=[],
    previous_items=[],
    comments=None,
):
    """Public legacy KOT endpoint; it never bypasses DocType permissions."""
    return _kot_execute(
        invoice_id,
        customer,
        restaurant_table,
        current_items,
        previous_items,
        comments,
    )


def _kot_execute(
    invoice_id,
    customer,
    restaurant_table=None,
    current_items=None,
    previous_items=None,
    comments=None,
    *,
    ignore_permissions=False,
):
    """Internal KOT service with a server-only permission control."""
    result = {"created_kots": [], "unrouted_items": []}
    current_items = load_json(current_items or [])
    previous_items = load_json(previous_items or [])
    new_invoice_items_array = create_order_items(previous_items)
    new_Order_items_array = create_order_items(current_items)

    final_array = compare_two_array(new_Order_items_array, new_invoice_items_array)
    removed_item = get_removed_items(new_invoice_items_array, new_Order_items_array)

    pos_invoice = frappe.get_doc("POS Invoice", invoice_id)
    pos_profile_id = pos_invoice.pos_profile
    pos_profile = frappe.get_doc("POS Profile", pos_profile_id)
    kot_naming_series = pos_profile.custom_kot_naming_series
    if kot_naming_series:
        cancel_kot_naming_series = "CNCL-" + kot_naming_series
    else:
        frappe.throw(
            "KOT Naming Series is mandatory for the auto creation of KOT.Ensure it is configured in the POS Profile: %s"
            % pos_profile.name
        )

    positive_qty_items = [item for item in final_array if flt(item["qty"]) > 0]
    negative_qty_items = [item for item in final_array if flt(item["qty"]) < 0]
    total_cancel_items = negative_qty_items + removed_item
    if positive_qty_items:
        positive_result = process_items_for_kot(
            invoice_id,
            customer,
            restaurant_table,
            positive_qty_items,
            comments,
            pos_profile_id,
            kot_naming_series,
            "New Order",
            ignore_permissions=ignore_permissions,
        )
        result["created_kots"].extend(positive_result["created_kots"])
        result["unrouted_items"].extend(positive_result["unrouted_items"])
    if total_cancel_items:
        process_items_for_cancel_kot(
            invoice_id,
            customer,
            restaurant_table,
            total_cancel_items,
            comments,
            pos_profile_id,
            cancel_kot_naming_series,
            "Partially cancelled",
            new_invoice_items_array,
        )
    result["unrouted_items"] = list(dict.fromkeys(result["unrouted_items"]))
    return result


# Compare two arrays and return the items that are different
def compare_two_array(array_1, array_2):
    current = create_order_items(array_1)
    previous_by_key = {
        _order_item_key(item): item for item in create_order_items(array_2)
    }
    changed = []
    for item in current:
        previous_qty = flt(previous_by_key.get(_order_item_key(item), {}).get("qty"))
        delta = flt(item["qty"]) - previous_qty
        if abs(delta) <= 1e-9:
            continue
        changed.append({**item, "qty": delta})
    return changed


# Get the items that have been removed from the second array compared to the first array
def get_removed_items(array_1, array_2):
    previous = create_order_items(array_1)
    current_keys = {
        _order_item_key(item) for item in create_order_items(array_2)
    }
    return [item for item in previous if _order_item_key(item) not in current_keys]
