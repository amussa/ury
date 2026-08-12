# Copyright (c) 2023, Tridz Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from ury.ury_pos.price_options import lock_menu_price_options_parent


class URYMenu(Document):
    def validate(self):
        for d in self.items:
            if not d.rate:
                d.rate = frappe.db.get_value("Item", d.item, "standard_rate")
        self.validate_price_options()

    def validate_price_options(self):
        self._protect_price_options_used_by_open_orders()
        menu_rates = {row.item: flt(row.rate) for row in self.items if not row.disabled}
        enabled_by_item = {}
        for option in self.price_options:
            if option.item not in menu_rates:
                frappe.throw(
                    _("Price option {0} refers to an item that is not enabled in this menu.").format(
                        frappe.bold(option.item)
                    )
                )
            if not cint(option.enabled):
                continue
            if option.item in enabled_by_item:
                frappe.throw(
                    _("Item {0} can have only one active promotional price.").format(
                        frappe.bold(option.item)
                    )
                )
            enabled_by_item[option.item] = option

            metadata = frappe.db.get_value(
                "Item",
                option.item,
                ["is_stock_item", "has_serial_no", "has_batch_no"],
                as_dict=True,
            )
            if not metadata or not cint(metadata.is_stock_item):
                frappe.throw(
                    _("Promotional quantities require a stock item: {0}.").format(
                        frappe.bold(option.item)
                    )
                )
            if cint(metadata.has_serial_no) or cint(metadata.has_batch_no):
                frappe.throw(
                    _("Serialised or batched items are not supported by promotional quantities: {0}.").format(
                        frappe.bold(option.item)
                    )
                )
            # Zero keeps the configured promotion visible as sold out in POS
            # and waiter; only a negative allocation is invalid.
            if flt(option.allocated_qty) < 0:
                frappe.throw(
                    _("Promotional quantity for {0} cannot be negative.").format(
                        frappe.bold(option.item)
                    )
                )
            if flt(option.rate) >= menu_rates[option.item]:
                frappe.throw(
                    _("Promotional price for {0} must be lower than its normal price.").format(
                        frappe.bold(option.item)
                    )
                )
            if not str(option.label or "").strip():
                frappe.throw(_("Every promotional price must have a label."))
            option.label = str(option.label).strip()

    def _protect_price_options_used_by_open_orders(self):
        """Do not strand an open order by removing or repricing its option."""
        if self.is_new() or not frappe.db.has_column(
            "POS Invoice Item", "custom_ury_price_option"
        ):
            return

        # Frappe normally locks a saved Document before validate, but keep the
        # promotion invariant explicit for every call path: parent first, then
        # child options/menu items. This also serialises insertion of the first
        # promotion, for which no child row exists to lock yet.
        lock_menu_price_options_parent(self.name)
        persisted = frappe.db.sql(
            """
            SELECT name, item, label, enabled, rate, allocated_qty
            FROM `tabURY Menu Price Option`
            WHERE parent = %(menu)s
              AND parenttype = 'URY Menu'
              AND parentfield = 'price_options'
            ORDER BY name
            FOR UPDATE
            """,
            {"menu": self.name},
            as_dict=True,
        )
        current = {row.name: row for row in self.price_options if row.name}
        persisted_by_name = {row.name: row for row in persisted}
        changed_item_codes = set()
        active_option_item_codes = set()
        for previous in persisted:
            option = current.get(previous.name)
            if option and option.item != previous.item:
                frappe.throw(
                    _(
                        "The item of saved price option {0} cannot be changed. "
                        "Remove it and add a new option instead."
                    ).format(frappe.bold(previous.name))
                )
            was_enabled = cint(previous.enabled)
            is_enabled = cint(option.enabled) if option else 0
            if was_enabled:
                active_option_item_codes.add(previous.item)
            if is_enabled:
                active_option_item_codes.add(option.item)
            if (was_enabled or is_enabled) and (
                not option
                or is_enabled != was_enabled
                or option.item != previous.item
                or str(option.label or "").strip()
                != str(previous.label or "").strip()
                or flt(option.rate) != flt(previous.rate)
                or flt(option.allocated_qty) != flt(previous.allocated_qty)
            ):
                changed_item_codes.add(previous.item)
                if option:
                    changed_item_codes.add(option.item)

        for option in self.price_options:
            if cint(option.enabled) and option.name not in persisted_by_name:
                active_option_item_codes.add(option.item)
                changed_item_codes.add(option.item)

        if active_option_item_codes:
            persisted_menu_items = frappe.db.sql(
                """
                SELECT name, item, rate, disabled
                FROM `tabURY Menu Item`
                WHERE parent = %(menu)s
                  AND parenttype = 'URY Menu'
                  AND parentfield = 'items'
                  AND item IN %(item_codes)s
                ORDER BY name
                FOR UPDATE
                """,
                {
                    "menu": self.name,
                    "item_codes": tuple(sorted(active_option_item_codes)),
                },
                as_dict=True,
            )
            current_menu_items = {
                row.item: row for row in self.items if row.item
            }
            for previous_item in persisted_menu_items:
                current_item = current_menu_items.get(previous_item.item)
                if (
                    not current_item
                    or cint(current_item.disabled) != cint(previous_item.disabled)
                    or flt(current_item.rate) != flt(previous_item.rate)
                ):
                    changed_item_codes.add(previous_item.item)

        if not changed_item_codes:
            return

        affected_option_ids = sorted(
            {
                row.name
                for row in persisted
                if row.name and row.item in changed_item_codes
            }
        )
        order_scope = [
            "(price_list.restaurant_menu = %(menu)s "
            "AND item.item_code IN %(item_codes)s)"
        ]
        order_values = {
            "menu": self.name,
            "item_codes": tuple(sorted(changed_item_codes)),
        }
        if affected_option_ids:
            order_scope.append(
                "item.custom_ury_price_option IN %(option_ids)s"
            )
            order_values["option_ids"] = tuple(affected_option_ids)

        open_order = frappe.db.sql(
            f"""
            SELECT invoice.name
            FROM `tabPOS Invoice` invoice
            INNER JOIN `tabPOS Invoice Item` item ON item.parent = invoice.name
            LEFT JOIN `tabPrice List` price_list
              ON price_list.name = invoice.selling_price_list
            WHERE invoice.docstatus = 0
              AND item.docstatus = 0
              AND ({" OR ".join(order_scope)})
            LIMIT 1
            FOR UPDATE
            """,
            order_values,
        )
        if open_order:
            frappe.throw(
                _(
                    "Finish or cancel open order {0} before changing its promotional price option."
                ).format(frappe.bold(open_order[0][0]))
            )

    def on_update(self):
        """Sync Price List"""
        self.make_price_list()

    def on_trash(self):
        """clear prices"""
        self._protect_price_options_on_trash()
        self.clear_item_price()

    def _protect_price_options_on_trash(self):
        """Do not orphan price-option snapshots on an open POS order."""
        if self.is_new() or not frappe.db.has_column(
            "POS Invoice Item", "custom_ury_price_option"
        ):
            return

        lock_menu_price_options_parent(self.name)
        persisted = frappe.db.sql(
            """
            SELECT name, item
            FROM `tabURY Menu Price Option`
            WHERE parent = %(menu)s
              AND parenttype = 'URY Menu'
              AND parentfield = 'price_options'
            ORDER BY name
            FOR UPDATE
            """,
            {"menu": self.name},
            as_dict=True,
        )
        option_ids = sorted({row.name for row in persisted if row.name})
        item_codes = sorted({row.item for row in persisted if row.item})
        if not option_ids and not item_codes:
            return

        clauses = []
        values = {"menu": self.name}
        if item_codes:
            clauses.append(
                "(price_list.restaurant_menu = %(menu)s "
                "AND item.item_code IN %(item_codes)s)"
            )
            values["item_codes"] = tuple(item_codes)
        if option_ids:
            clauses.append(
                "item.custom_ury_price_option IN %(option_ids)s"
            )
            values["option_ids"] = tuple(option_ids)

        open_order = frappe.db.sql(
            f"""
            SELECT invoice.name
            FROM `tabPOS Invoice` invoice
            INNER JOIN `tabPOS Invoice Item` item ON item.parent = invoice.name
            LEFT JOIN `tabPrice List` price_list
              ON price_list.name = invoice.selling_price_list
            WHERE invoice.docstatus = 0
              AND item.docstatus = 0
              AND ({" OR ".join(clauses)})
            LIMIT 1
            FOR UPDATE
            """,
            values,
        )
        if open_order:
            frappe.throw(
                _(
                    "Finish or cancel open order {0} before deleting this menu."
                ).format(frappe.bold(open_order[0][0]))
            )

    def clear_item_price(self, price_list=None):
        """clear all item prices for this menu"""
        if not price_list:
            price_list = self.get_price_list().name
        frappe.db.sql("delete from `tabItem Price` where price_list = %s", price_list)

    def make_price_list(self):
        # create price list for menu
        price_list = self.get_price_list()
        self.db_set("price_list", price_list.name)

        # delete old items
        self.clear_item_price(price_list.name)

        for d in self.items:
            frappe.get_doc(
                dict(
                    doctype="Item Price",
                    price_list=price_list.name,
                    item_code=d.item,
                    price_list_rate=d.rate,
                )
            ).insert()

    def get_price_list(self):
        """Create price list for menu if missing"""
        price_list_name = frappe.db.get_value(
            "Price List", dict(restaurant_menu=self.name)
        )
        if price_list_name:
            price_list = frappe.get_doc("Price List", price_list_name)
        else:
            price_list = frappe.new_doc("Price List")
            price_list.restaurant_menu = self.name
            price_list.price_list_name = self.name

        price_list.enabled = 1
        price_list.selling = 1
        price_list.save()

        return price_list
