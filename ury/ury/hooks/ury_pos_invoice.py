import frappe
from frappe import _
from frappe.utils import flt, get_datetime, get_time, now_datetime
from ury.ury.doctype.ury_order.ury_order import release_merge_cluster_tables
from ury.ury_pos.cashier import (
    POSOpeningError,
    assign_single_cashier_from_opening,
    persist_cashier_owner,
)
from ury.ury_pos.price_options import (
    OPTION_FIELD,
    OPTION_LABEL_FIELD,
    STANDARD_OPTION_ID,
    lock_invoice_price_options,
    validate_pos_invoice_price_options,
)


CREDIT_SETTLEMENT_TYPES = {"Partial Credit", "Full Credit"}
COMMERCIAL_TOLERANCE = 0.01


def before_insert(doc, method):
    pos_invoice_naming(doc, method)
    order_type_update(doc, method)
    restrict_existing_order(doc, method)


def validate(doc, method):
    assign_single_cashier_from_opening(doc)
    validate_invoice(doc, method)
    validate_customer(doc, method)
    validate_price_list(doc, method)
    validate_pos_invoice_price_options(doc)


def before_submit(doc, method):
    assign_single_cashier_from_opening(doc)
    validate_commercial_settlement(doc)
    calculate_and_set_times(doc, method)
    ro_reload_submit(doc, method)


def before_cancel(doc, method):
    """Serialize quota release with concurrent price-option reservations."""
    if doc.get("custom_ury_settlement"):
        frappe.throw(
            _(
                "A commercially settled POS Invoice cannot be cancelled directly. "
                "Use an approved accounting correction procedure."
            )
        )
    lock_invoice_price_options(doc)


def on_trash(doc, method):
    # Frappe deletion invokes on_trash before removing child rows. Cancellation
    # already acquired this lock in before_cancel, so avoid a duplicate query
    # when this handler is reached through the on_cancel hook.
    if method == "on_trash":
        lock_invoice_price_options(doc)
    table_status_delete(doc, method)


def validate_invoice(doc, method):
    if doc.waiter == None or doc.waiter == "":
        doc.waiter = doc.modified_by
    if getattr(frappe.flags, "ury_bill_split", False):
        return
    remove_items = frappe.db.get_value("POS Profile", doc.pos_profile, "remove_items")
    
    if doc.invoice_printed == 1 and remove_items == 0:
        # Get the original items from db
        original_doc = frappe.get_doc("POS Invoice", doc.name)
        
        # Aggregate by item and commercial price option. Two physical lines for
        # Normal and Promotion must never overwrite one another in a dict.
        original_items = _aggregate_invoice_items(
            original_doc.get("items") or []
        )
        current_items = _aggregate_invoice_items(doc.get("items") or [])
          
        # Check for removed items
        removed_items = set(original_items.keys()) - set(current_items.keys())
        
        # Check for quantity reductions
        reduced_qty_items = []
        for item_code, item_data in original_items.items():
            if (item_code in current_items and 
                current_items[item_code]["qty"] < item_data["qty"]):
                reduced_qty_items.append(
                    f"{item_data['display_name']} (qty reduced from {item_data['qty']} "
                    f"to {current_items[item_code]['qty']})"
                )
        
        if removed_items or reduced_qty_items:
            error_msg = []
            if removed_items:
                removed_item_names = [original_items[key]["display_name"] for key in removed_items]
                error_msg.append(f"Removed items: {', '.join(removed_item_names)}")
            if reduced_qty_items:
                error_msg.append(f"Modified quantities: {', '.join(reduced_qty_items)}")
                
            frappe.throw(
                ("Cannot modify items after invoice is printed.\n{0}")
                .format("\n".join(error_msg))
            )


def _aggregate_invoice_items(items):
    aggregated = {}
    for item in items:
        option_id = item.get(OPTION_FIELD) or STANDARD_OPTION_ID
        key = (item.item_code, option_id)
        if key not in aggregated:
            option_label = item.get(OPTION_LABEL_FIELD)
            display_name = item.item_name
            if option_label:
                display_name = f"{display_name} - {option_label}"
            aggregated[key] = {
                "qty": 0,
                "name": item.item_name,
                "display_name": display_name,
            }
        aggregated[key]["qty"] = flt(aggregated[key]["qty"]) + flt(item.qty)
    return aggregated


def validate_customer(doc, method):
    if doc.customer_name == None or doc.customer_name == "":
        frappe.throw(
            (" Failed to load data , Please Refresh the page ").format(
                doc.customer_name
            )
        )


def calculate_and_set_times(doc, method):
    creation = get_datetime(doc.creation)
    doc.arrived_time = creation
    time_difference = now_datetime() - creation
    
    total_seconds = int(time_difference.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    formatted_spend_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    doc.total_spend_time = formatted_spend_time


def validate_commercial_settlement(doc):
    """Reject every short POS payment outside the authorised URY service.

    ``allow_partial_payment`` is a profile-wide ERPNext switch.  Once credit is
    enabled, Desk, legacy POS and direct API calls would otherwise be able to
    submit an accidental short payment.  The request-scoped flag is set only by
    ``ury.ury_pos.settlement.settle_invoice`` while the audited settlement and
    its invoices are saved/submitted in one transaction.
    """
    if doc.get("is_return"):
        original = None
        if doc.get("return_against"):
            original = frappe.db.get_value(
                "POS Invoice",
                doc.return_against,
                [
                    "custom_ury_settlement",
                    "custom_ury_settlement_type",
                    "consolidated_invoice",
                    "pos_profile",
                ],
                as_dict=True,
            )
        if (
            original
            and original.custom_ury_settlement_type in CREDIT_SETTLEMENT_TYPES
            and not original.consolidated_invoice
        ):
            frappe.throw(
                _(
                    "POS returns for credit sales are temporarily unavailable. "
                    "Close the original sale and use an approved accounting "
                    "correction procedure."
                )
            )

        profile_names = {
            profile_name
            for profile_name in (
                doc.get("pos_profile"),
                original.get("pos_profile") if original else None,
            )
            if profile_name
        }
        commercial_checkout_enabled = any(
            frappe.db.get_value(
                "POS Profile",
                profile_name,
                "custom_ury_enable_commercial_checkout",
            )
            for profile_name in profile_names
        )
        if commercial_checkout_enabled or (original and original.custom_ury_settlement):
            frappe.throw(
                _(
                    "POS returns are temporarily unavailable for this commercial "
                    "checkout profile. Use an approved accounting correction procedure."
                )
            )
        return

    precision = 2
    try:
        precision = doc.precision("grand_total")
    except Exception:
        pass

    # Match ERPNext's own POS total semantics.  ``rounded_total`` is normally
    # the numeric value 0 when rounding is disabled, not ``None``.
    total = flt(flt(doc.get("rounded_total")) or flt(doc.get("grand_total")), precision)
    paid = flt(
        sum(flt(row.get("amount")) for row in (doc.get("payments") or [])),
        precision,
    )
    shortage = flt(max(total - paid, 0), precision)
    settlement_type = doc.get("custom_ury_settlement_type")
    settlement = doc.get("custom_ury_settlement")
    active_settlement = getattr(frappe.flags, "ury_pos_settlement", None)

    profile = None
    if doc.get("pos_profile"):
        profile = frappe.db.get_value(
            "POS Profile",
            doc.pos_profile,
            [
                "customer",
                "allow_partial_payment",
                "custom_ury_enable_commercial_checkout",
                "custom_ury_enable_credit_sales",
            ],
            as_dict=True,
        )

    has_header_discount = bool(
        flt(doc.get("additional_discount_percentage"))
        or flt(doc.get("discount_amount"))
    )
    if (
        profile
        and profile.custom_ury_enable_commercial_checkout
        and (not settlement or active_settlement != settlement)
    ):
        if has_header_discount:
            frappe.throw(
                _(
                    "Manual discounts must be completed through the URY "
                    "commercial checkout."
                )
            )
        frappe.throw(
            _(
                "This POS Profile requires every invoice to be completed through "
                "the URY commercial checkout."
            )
        )

    if shortage < COMMERCIAL_TOLERANCE:
        if settlement_type in CREDIT_SETTLEMENT_TYPES:
            if (
                settlement
                and active_settlement == settlement
                and flt(doc.get("custom_ury_credit_amount")) < COMMERCIAL_TOLERANCE
            ):
                # A merged agreement may consume all payments on one member
                # while another member retains the audited aggregate credit.
                return
            frappe.throw(_("A credit settlement must retain an outstanding balance."))
        return

    if (
        not settlement
        or settlement_type not in CREDIT_SETTLEMENT_TYPES
        or active_settlement != settlement
    ):
        frappe.throw(
            _(
                "A payment below the invoice total is allowed only through "
                "Conceder crédito in the URY POS."
            )
        )

    if not profile or not all(
        (
            profile.allow_partial_payment,
            profile.custom_ury_enable_commercial_checkout,
            profile.custom_ury_enable_credit_sales,
        )
    ):
        frappe.throw(_("Credit sales are not enabled for this POS Profile."))
    if not doc.get("customer") or doc.customer == profile.customer:
        frappe.throw(_("Credit requires an identified customer."))
    if not doc.get("custom_ury_credit_due_date"):
        frappe.throw(_("Credit due date is required."))

    allocated_credit = flt(doc.get("custom_ury_credit_amount"), precision)
    if abs(allocated_credit - shortage) >= COMMERCIAL_TOLERANCE:
        frappe.throw(_("The invoice outstanding does not match the credit agreement."))


def table_status_delete(doc, method):
    if doc.restaurant_table:
        release_merge_cluster_tables(doc.restaurant_table)


def pos_invoice_naming(doc, method):
    pos_profile = frappe.get_doc("POS Profile", doc.pos_profile)
    restaurant = pos_profile.restaurant

    if not doc.restaurant_table:
        doc.naming_series = frappe.db.get_value(
            "URY Restaurant", restaurant, "invoice_series_prefix"
        )
        
        if doc.order_type == "Aggregators":
            doc.naming_series = frappe.db.get_value(
                "URY Restaurant", restaurant, "aggregator_series_prefix"
            )
    


def order_type_update(doc, method):
    if doc.restaurant_table:
        if not doc.order_type:
            is_take_away = frappe.db.get_value(
                "URY Table", doc.restaurant_table, "is_take_away"
            )
            if is_take_away == 1:
                doc.order_type = "Take Away"
            else:
                doc.order_type = "Dine In"
    


# reload restaurant order page if submitted invoice is open there
def ro_reload_submit(doc, method):
    frappe.publish_realtime("reload_ro", {"name": doc.name})


def validate_price_list(doc, method):
    waiter_price_list = getattr(
        frappe.flags, "ury_waiter_expected_price_list", None
    )
    if waiter_price_list:
        # The restricted waiter service has already resolved exactly one
        # enabled selling Price List for the table's room menu. Do not let the
        # legacy Dine In fallback overwrite it with the restaurant-wide menu.
        doc.selling_price_list = waiter_price_list
        return
        
    if doc.restaurant:
        
        if doc.restaurant_table:
            room = frappe.db.get_value("URY Table", doc.restaurant_table, "restaurant_room")
            menu_name = (
                frappe.db.get_value("URY Restaurant", doc.restaurant, "active_menu")
                if not frappe.db.get_value(
                    "URY Restaurant", doc.restaurant, "room_wise_menu"
                )
                else frappe.db.get_value(
                    "Menu for Room", {"parent": doc.restaurant, "room": room}, "menu"
                )
            )

            doc.selling_price_list = frappe.db.get_value(
                "Price List", dict(restaurant_menu=menu_name, enabled=1)
            )
        
        if doc.order_type == "Aggregators":
            price_list = frappe.db.get_value("Aggregator Settings",
                {"customer": doc.customer, "parent": doc.branch, "parenttype": "Branch"},
                "price_list",
                )
            
            if not price_list:
                frappe.throw(f"Price list for customer {doc.customer} in branch {doc.branch} not found in Aggregator Settings.")
                
            doc.selling_price_list = price_list
            
        else:
            menu_name = frappe.db.get_value("URY Restaurant", doc.restaurant, "active_menu") 

            doc.selling_price_list = frappe.db.get_value(
                "Price List", dict(restaurant_menu=menu_name, enabled=1)
            )
            

def restrict_existing_order(doc, event):
    if not doc.restaurant_table:
        return

    if getattr(frappe.flags, "ury_bill_split", False):
        return

    if doc.get("custom_split_from"):
        source = frappe.db.get_value(
            "POS Invoice",
            doc.custom_split_from,
            ["docstatus", "restaurant_table"],
            as_dict=True,
        )
        if (
            source
            and source.docstatus == 0
            and (source.restaurant_table or "") == (doc.restaurant_table or "")
        ):
            return

    invoice_exist = frappe.db.exists(
        "POS Invoice",
        {
            "restaurant_table": doc.restaurant_table,
            "docstatus": 0,
            "invoice_printed": 0,
        },
    )
    if invoice_exist:
        frappe.throw(
            ("Table {0} has an existing invoice").format(doc.restaurant_table)
        )

def sync_merged_invoice(doc):
    if getattr(frappe.flags, "in_bill_merge_sync", False):
        return

    linked_invoice = doc.custom_merged_pos_invoice
    if not linked_invoice:
        return

    frappe.flags.in_bill_merge_sync = True

    try:
        if not frappe.db.exists("POS Invoice", linked_invoice):
            return

        target = frappe.get_doc("POS Invoice", linked_invoice)

        # sync invoice printed
        target.invoice_printed = doc.invoice_printed

        # sync payment
        if doc.paid_amount > 0 and not getattr(doc.flags, "ignore_payment_sync", False):

            target.set("payments", [])

            for p in doc.payments:
                target.append("payments", {
                    "mode_of_payment": p.mode_of_payment,
                    "amount": target.rounded_total,
                    "base_amount": target.rounded_total,
                    "account": getattr(p, "account", None)
                })

            target.paid_amount = target.rounded_total

        # sync submit/payment status
        if doc.docstatus == 1 and target.docstatus == 0:

            target.flags.ignore_validate_update_after_submit = True

            if target.docstatus == 0:
                target.save(ignore_permissions=True)
                target.submit()

        else:
            target.save(ignore_permissions=True)

        frappe.publish_realtime(
            "pos_invoice_updated",
            {
                "name": target.name,
                "docstatus": target.docstatus,
                "paid_amount": target.paid_amount,
                "invoice_printed": target.invoice_printed
            }
        )

    except POSOpeningError:
        raise
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Merged Invoice Sync Failed"
        )

    finally:
        frappe.flags.in_bill_merge_sync = False


def on_update(doc, method):
    persist_cashier_owner(doc)
    sync_merged_invoice(doc)


def on_submit(doc, method):
    sync_merged_invoice(doc)
    release_merged_tables(doc)

def release_merged_tables(doc):

    invoices = [doc]

    if doc.custom_merged_pos_invoice:
        try:
            invoices.append(
                frappe.get_doc(
                    "POS Invoice",
                    doc.custom_merged_pos_invoice
                )
            )
        except Exception:
            pass

    for invoice in invoices:

        # only release dine in tables
        if invoice.order_type != "Dine In":
            continue

        if not invoice.restaurant_table:
            continue

        frappe.db.set_value(
            "URY Table",
            invoice.restaurant_table,
            {
                "occupied": 0,
                "latest_invoice_time": None,
            },
            update_modified=False,
        )
