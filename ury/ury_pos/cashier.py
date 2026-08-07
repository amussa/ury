import frappe
from frappe import _


class POSOpeningError(frappe.ValidationError):
    pass


def get_single_cashier_opening(pos_profile, required=True, for_update=False):
    """Return the only submitted open entry for a single-cashier POS profile.

    The POS opening is the source of truth for who owns the till.  The order of
    ``POS Profile.applicable_for_users`` is configuration/permission data and
    must not decide who owns sales in the current shift.
    """
    profile = frappe.db.get_value(
        "POS Profile",
        pos_profile,
        ["name", "branch", "custom_enable_multiple_cashier"],
        as_dict=True,
    )
    if not profile:
        frappe.throw(
            _("POS Profile {0} does not exist.").format(
                frappe.bold(pos_profile)
            ),
            title=_("Invalid POS Profile"),
            exc=POSOpeningError,
        )

    if profile.custom_enable_multiple_cashier:
        return None

    if not profile.branch:
        frappe.throw(
            _("POS Profile {0} has no branch.").format(
                frappe.bold(profile.name)
            ),
            title=_("Invalid POS Profile"),
            exc=POSOpeningError,
        )

    filters = {
        "pos_profile": profile.name,
        "status": "Open",
        "docstatus": 1,
    }
    filters["branch"] = profile.branch

    openings = frappe.db.get_values(
        "POS Opening Entry",
        filters=filters,
        fieldname=["name", "user", "branch", "pos_profile", "period_start_date"],
        as_dict=True,
        order_by="period_start_date desc, creation desc",
        limit=2,
        for_update=for_update,
    )

    if not openings:
        if not required:
            return None
        frappe.throw(
            _(
                "No submitted open POS Opening Entry exists for POS Profile {0}. "
                "Open the till before creating or paying an order."
            ).format(frappe.bold(profile.name)),
            title=_("POS Not Opened"),
            exc=POSOpeningError,
        )

    if len(openings) > 1:
        names = ", ".join(opening.name for opening in openings)
        frappe.throw(
            _(
                "More than one submitted open POS Opening Entry exists for POS "
                "Profile {0}: {1}. Close the duplicate entries before continuing."
            ).format(frappe.bold(profile.name), frappe.bold(names)),
            title=_("Multiple POS Openings"),
            exc=POSOpeningError,
        )

    opening = openings[0]
    if not opening.user:
        frappe.throw(
            _("POS Opening Entry {0} has no user.").format(
                frappe.bold(opening.name)
            ),
            title=_("Invalid POS Opening"),
            exc=POSOpeningError,
        )

    return opening


def assign_single_cashier_from_opening(doc, required=True):
    """Set a POS Invoice's cashier and owner from its active opening entry."""
    if not doc.pos_profile:
        return None

    opening = get_single_cashier_opening(
        doc.pos_profile,
        required=required,
        for_update=True,
    )
    if not opening:
        # Multiple-cashier mode keeps the cashier selected by the existing
        # room flow.  New documents need owner restored after Frappe assigns
        # the session user in set_user_and_timestamp().
        if doc.cashier and doc.is_new():
            doc.owner = doc.cashier
        return None

    if not doc.branch:
        doc.branch = opening.branch
    elif doc.branch != opening.branch:
        frappe.throw(
            _(
                "POS Invoice branch {0} does not match the active POS Opening "
                "Entry {1} branch {2}."
            ).format(
                frappe.bold(doc.branch),
                frappe.bold(opening.name),
                frappe.bold(opening.branch),
            ),
            title=_("Invalid POS Opening"),
            exc=POSOpeningError,
        )

    doc.cashier = opening.user
    if doc.is_new():
        doc.owner = opening.user
    return opening


def persist_cashier_owner(doc):
    """Persist the resolved owner after normal validation has completed.

    ``owner`` is a standard set-only-once field.  For an existing draft, it
    cannot be changed during validate.  This controlled post-save write uses
    only the server-resolved opening user (or the established room cashier in
    multiple-cashier mode), never the owner supplied by the browser.
    """
    opening = assign_single_cashier_from_opening(doc)
    owner = opening.user if opening else doc.cashier
    if owner and doc.owner != owner:
        doc.db_set("owner", owner, update_modified=False)
    return opening
