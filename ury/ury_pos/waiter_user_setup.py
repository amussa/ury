"""Guarded, bench-only provisioning for a restricted URY waiter account."""

from __future__ import annotations

import sys

import frappe
from frappe.defaults import set_user_default
from frappe.utils.password import update_password

from ury.ury_pos.waiter_security import WAITER_ROLE


LEGACY_WAITER_ROLE = "URY Captain"
FORBIDDEN_ROLES = {
    LEGACY_WAITER_ROLE,
    "URY Cashier",
    "URY Manager",
    "System Manager",
}
FORBIDDEN_DOCTYPES = (
    "POS Invoice",
    "URY KOT",
    "POS Opening Entry",
    "POS Closing Entry",
)
FORBIDDEN_ACTIONS = ("write", "create", "submit", "cancel")
USER_PERMISSION_FIELDS = (
    "allow",
    "for_value",
    "applicable_for",
    "is_default",
    "hide_descendants",
    "apply_to_all_doctypes",
)


def _normalise_permission(row):
    return tuple(row.get(field) for field in USER_PERMISSION_FIELDS)


def _user_permissions(user):
    return frappe.get_all(
        "User Permission",
        filters={"user": user},
        fields=["name", *USER_PERMISSION_FIELDS],
        order_by="allow asc, for_value asc, applicable_for asc",
        limit_page_length=100,
    )


def _expected_permissions(branch, warehouse, pos_profile, price_list):
    return (
        {
            "allow": "Branch",
            "for_value": branch,
            "applicable_for": None,
            "is_default": 1,
            "hide_descendants": 0,
            "apply_to_all_doctypes": 1,
        },
        {
            "allow": "POS Profile",
            "for_value": pos_profile,
            "applicable_for": None,
            "is_default": 1,
            "hide_descendants": 0,
            "apply_to_all_doctypes": 1,
        },
        {
            "allow": "Price List",
            "for_value": price_list,
            "applicable_for": "Item Price",
            "is_default": 0,
            "hide_descendants": 0,
            "apply_to_all_doctypes": 0,
        },
        {
            "allow": "Warehouse",
            "for_value": warehouse,
            "applicable_for": None,
            "is_default": 1,
            "hide_descendants": 0,
            "apply_to_all_doctypes": 1,
        },
    )


def _ensure_waiter_role():
    if frappe.db.exists("Role", WAITER_ROLE):
        role = frappe.get_doc("Role", WAITER_ROLE)
        if role.disabled or role.desk_access:
            frappe.throw(
                f"The dedicated role {WAITER_ROLE} must be enabled without Desk access."
            )
        return role

    return frappe.get_doc(
        {
            "doctype": "Role",
            "role_name": WAITER_ROLE,
            "desk_access": 0,
            "is_custom": 0,
        }
    ).insert(ignore_permissions=True)


def _validate_configuration(branch, room, pos_profile, price_list):
    for doctype, name in (
        ("Branch", branch),
        ("URY Room", room),
        ("POS Profile", pos_profile),
        ("Price List", price_list),
    ):
        if not frappe.db.exists(doctype, name):
            frappe.throw(f"Missing prerequisite: {doctype} {name}")

    profile = frappe.db.get_value(
        "POS Profile",
        pos_profile,
        ["branch", "warehouse", "disabled"],
        as_dict=True,
    )
    if profile.disabled or profile.branch != branch or not profile.warehouse:
        frappe.throw("The selected POS Profile is not an active profile for this branch.")

    room_branch = frappe.db.get_value("URY Room", room, "branch")
    if room_branch != branch:
        frappe.throw("The selected room does not belong to this branch.")

    menus = frappe.get_all(
        "Price List",
        filters={
            "name": price_list,
            "enabled": 1,
            "selling": 1,
        },
        fields=["name", "restaurant_menu"],
        limit=2,
    )
    if len(menus) != 1 or not menus[0].restaurant_menu:
        frappe.throw("The selected Price List is not one active restaurant selling list.")

    return profile.warehouse


def _validate_identity(email, username, full_name):
    users = frappe.get_all(
        "User",
        filters={"username": username},
        pluck="name",
        limit_page_length=20,
    )
    if users and users != [email]:
        frappe.throw(f"Username {username} already belongs to another account.")

    if frappe.db.exists("User", email):
        user = frappe.get_doc("User", email)
        if user.username != username or user.full_name != full_name:
            frappe.throw("An incompatible account already uses this email address.")


def _ensure_user(email, username, full_name):
    values = {
        "email": email,
        "first_name": full_name,
        "username": username,
        "enabled": 1,
        "send_welcome_email": 0,
        "language": "pt",
        "time_zone": "Africa/Maputo",
    }
    if frappe.db.exists("User", email):
        user = frappe.get_doc("User", email)
        roles = {row.role for row in user.roles}
        if roles not in ({WAITER_ROLE}, {LEGACY_WAITER_ROLE}):
            frappe.throw(f"The existing account has unexpected roles: {sorted(roles)}")
        if roles == {LEGACY_WAITER_ROLE}:
            # Safe one-time migration from the earlier waiter setup. This does
            # not change URY Captain itself or any other Captain account.
            user.set("roles", [])
            user.append("roles", {"role": WAITER_ROLE})
        for fieldname, value in values.items():
            user.set(fieldname, value)
        user.save(ignore_permissions=True)
    else:
        user = frappe.get_doc({"doctype": "User", **values})
        user.append("roles", {"role": WAITER_ROLE})
        user.insert(ignore_permissions=True)

    user.reload()
    if user.user_type != "Website User":
        frappe.throw("The waiter account was not created as a restricted Website User.")
    return user


def _ensure_permissions(email, expected):
    existing = _user_permissions(email)
    expected_values = {_normalise_permission(row) for row in expected}
    if existing:
        actual_values = {_normalise_permission(row) for row in existing}
        if actual_values != expected_values:
            frappe.throw("The existing account has unexpected User Permissions.")
        return

    for permission in expected:
        frappe.get_doc(
            {"doctype": "User Permission", "user": email, **permission}
        ).insert(ignore_permissions=True)


def _ensure_branch_room(email, branch_name, room):
    existing = frappe.get_all(
        "URY User",
        filters={"user": email},
        fields=["parent", "room"],
        limit_page_length=100,
    )
    expected = [(branch_name, room)]
    if existing:
        actual = [(row.parent, row.room) for row in existing]
        if actual != expected:
            frappe.throw("The existing account has an unexpected branch/room assignment.")
        return

    branch = frappe.get_doc("Branch", branch_name)
    branch.append("user", {"user": email, "room": room})
    branch.save(ignore_permissions=True)


def _ensure_profile_user(email, profile_name):
    existing = frappe.get_all(
        "POS Profile User",
        filters={"user": email},
        fields=["parent", "default"],
        limit_page_length=100,
    )
    expected = [(profile_name, 1)]
    if existing:
        actual = [(row.parent, row.default) for row in existing]
        if actual != expected:
            frappe.throw("The existing account has an unexpected POS Profile assignment.")
        return

    profile = frappe.get_doc("POS Profile", profile_name)
    profile.append("applicable_for_users", {"user": email, "default": 1})
    profile.save(ignore_permissions=True)


def _validate_effective_access(email, username, password, branch, room, profile):
    from frappe.core.doctype.user.user import User

    authenticated = User.find_by_credentials(username, password)
    if (
        not authenticated
        or authenticated.get("name") != email
        or not authenticated.get("enabled")
        or not authenticated.get("is_authenticated")
    ):
        frappe.throw("The waiter credentials could not be validated.")

    roles = set(frappe.get_roles(email))
    if WAITER_ROLE not in roles or roles.intersection(FORBIDDEN_ROLES):
        frappe.throw("The waiter received an invalid role set.")

    original_user = frappe.session.user
    frappe.clear_cache(user=email)
    frappe.set_user(email)
    try:
        forbidden_actions = {
            doctype: {
                action: bool(frappe.has_permission(doctype, ptype=action))
                for action in FORBIDDEN_ACTIONS
            }
            for doctype in FORBIDDEN_DOCTYPES
        }
        if any(
            allowed
            for actions in forbidden_actions.values()
            for allowed in actions.values()
        ):
            frappe.throw(
                "The waiter has a forbidden direct document permission."
            )
    finally:
        frappe.set_user(original_user)

    return {
        "user": email,
        "username": username,
        "full_name": frappe.db.get_value("User", email, "full_name"),
        "roles": sorted(roles - {"All", "Guest", "Desk User"}),
        "branch": branch,
        "room": room,
        "pos_profile": profile,
        "forbidden_document_actions": forbidden_actions,
        "can_modify_pos_documents": False,
        "login_verified": True,
    }


def run(
    password=None,
    *,
    email,
    username,
    full_name,
    branch,
    room,
    pos_profile,
    price_list,
):
    """Create or verify one strictly-scoped waiter account transactionally."""
    if not password:
        frappe.throw("A password is required.")

    frappe.set_user("Administrator")
    _ensure_waiter_role()
    warehouse = _validate_configuration(branch, room, pos_profile, price_list)
    _validate_identity(email, username, full_name)
    user = _ensure_user(email, username, full_name)
    update_password(user.name, password)
    expected = _expected_permissions(branch, warehouse, pos_profile, price_list)
    _ensure_permissions(email, expected)
    set_user_default("time_zone", "Africa/Maputo", email)
    _ensure_branch_room(email, branch, room)
    _ensure_profile_user(email, pos_profile)
    return _validate_effective_access(
        email, username, password, branch, room, pos_profile
    )


def run_from_stdin(**kwargs):
    """Read the password from a pipe so it never appears in process arguments."""
    password = sys.stdin.readline().rstrip("\r\n")
    return run(password=password, **kwargs)
