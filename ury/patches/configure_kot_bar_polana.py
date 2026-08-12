"""Configure the Polana bar KOT route and ESC/POS printer.

This is an explicit operational command, not an automatic migration patch.
Run ``preview`` first and ``configure`` only after the site backup succeeds.
"""

import frappe

from ury.ury.api.ury_production_routing import get_production_route_state


PRODUCTION_UNIT = "Bar Polana"
POS_PROFILE = "POS Polana"
MENU = "Menu Polana"
MENU_COURSES = ("Bebidas", "Chá", "Café")
PRINT_FORMAT = "Gelatiamo KOT ESC/POS 80mm"
TRANSPORT = "ESC/POS TCP"
HOST = "192.168.18.3"
PORT = 9100
TIMEOUT = 5


def preview() -> dict:
	profile = frappe.get_doc("POS Profile", POS_PROFILE)
	return {
		"production_unit": PRODUCTION_UNIT,
		"pos_profile": POS_PROFILE,
		"branch": profile.branch,
		"warehouse": profile.warehouse,
		"menu": MENU,
		"menu_courses": list(MENU_COURSES),
		"active_menu_items": _active_menu_item_counts(),
		"print_format_exists": bool(frappe.db.exists("Print Format", PRINT_FORMAT)),
		"desired_printer": {
			"custom_kot_print": 1,
			"custom_kot_print_format": PRINT_FORMAT,
			"transport": TRANSPORT,
			"escpos_host": HOST,
			"escpos_port": PORT,
			"escpos_timeout": TIMEOUT,
		},
		"current": _current(),
		"route_verification": verify_routes(),
	}


def configure() -> dict:
	missing_courses = [
		course
		for course in MENU_COURSES
		if not frappe.db.exists("URY Menu Course", course)
	]
	if missing_courses:
		frappe.throw(f"Menu courses not found: {', '.join(missing_courses)}")
	if not frappe.db.exists("URY Menu", MENU):
		frappe.throw(f"Menu not found: {MENU}")
	if not frappe.db.exists("Print Format", PRINT_FORMAT):
		frappe.throw(f"Print Format not found: {PRINT_FORMAT}")

	profile = frappe.get_doc("POS Profile", POS_PROFILE)
	if frappe.db.exists("URY Production Unit", PRODUCTION_UNIT):
		unit = frappe.get_doc("URY Production Unit", PRODUCTION_UNIT)
		if unit.pos_profile != POS_PROFILE or unit.branch != profile.branch:
			frappe.throw(
				f"{PRODUCTION_UNIT} belongs to a different POS Profile or branch; refusing to overwrite it."
			)
	else:
		unit = frappe.new_doc("URY Production Unit")
		unit.production = PRODUCTION_UNIT
		unit.pos_profile = POS_PROFILE
		unit.branch = profile.branch
		unit.warehouse = profile.warehouse

	unit.set("menu_courses", [])
	for course in MENU_COURSES:
		unit.append("menu_courses", {"menu_course": course})

	active_kot_rows = [row for row in unit.printer_settings if row.custom_kot_print]
	conflicts = [
		row
		for row in active_kot_rows
		if not (
			row.transport == TRANSPORT
			and row.escpos_host == HOST
			and row.escpos_port == PORT
		)
	]
	if conflicts:
		frappe.throw(
			f"{PRODUCTION_UNIT} already has a different active KOT printer; refusing to create duplicate output."
		)

	matching = [
		row
		for row in unit.printer_settings
		if row.transport == TRANSPORT
		and row.escpos_host == HOST
		and row.escpos_port == PORT
	]
	printer = matching[0] if matching else unit.append("printer_settings", {})
	printer.update(
		{
			"bill": 0,
			"printer": None,
			"custom_kot_print": 1,
			"custom_kot_print_format": PRINT_FORMAT,
			"custom_block_takeaway_kot": 0,
			"transport": TRANSPORT,
			"escpos_host": HOST,
			"escpos_port": PORT,
			"escpos_timeout": TIMEOUT,
		}
	)

	unit.save(ignore_permissions=True)
	frappe.db.commit()
	return {"status": "configured", **preview()}


def verify_routes() -> dict:
	menu_rows = frappe.get_all(
		"URY Menu Item",
		filters={"parent": MENU, "disabled": 0},
		fields=["item", "course"],
		order_by="idx",
	)
	course_by_item = {row.item: row.course for row in menu_rows}
	state = get_production_route_state(
		"Polana",
		list(course_by_item),
		MENU,
	)
	expected_bar = {
		item for item, course in course_by_item.items() if course in MENU_COURSES
	}
	actual_bar = {
		item for item, production in state["routes"].items() if production == PRODUCTION_UNIT
	}
	route_counts = {}
	for production in state["routes"].values():
		route_counts[production] = route_counts.get(production, 0) + 1

	return {
		"active_unique_menu_items": len(course_by_item),
		"expected_bar_items": len(expected_bar),
		"actual_bar_items": len(actual_bar),
		"route_counts": route_counts,
		"wrong_bar_routes": sorted(expected_bar - actual_bar),
		"unexpected_bar_routes": sorted(actual_bar - expected_bar),
		"unrouted": state["unrouted"],
		"duplicated": state["duplicated"],
	}


def disable() -> dict:
	"""Stop automatic bar printing without deleting its route or settings."""
	unit = frappe.get_doc("URY Production Unit", PRODUCTION_UNIT)
	changed = []
	for row in unit.printer_settings:
		if (
			row.transport == TRANSPORT
			and row.escpos_host == HOST
			and row.escpos_port == PORT
		):
			row.custom_kot_print = 0
			changed.append(row.name)
	if changed:
		unit.save(ignore_permissions=True)
		frappe.db.commit()
	return {"status": "disabled", "rows": changed, "current": _current()}


def _active_menu_item_counts() -> dict:
	counts = {}
	for course in MENU_COURSES:
		counts[course] = frappe.db.count(
			"URY Menu Item",
			{"parent": MENU, "course": course, "disabled": 0},
		)
	return counts


def _current() -> dict | None:
	if not frappe.db.exists("URY Production Unit", PRODUCTION_UNIT):
		return None
	unit = frappe.get_doc("URY Production Unit", PRODUCTION_UNIT)
	return {
		"menu_courses": [row.menu_course for row in unit.menu_courses],
		"item_groups": [row.item_group for row in unit.item_groups],
		"printers": [
			{
				"name": row.name,
				"custom_kot_print": row.custom_kot_print,
				"custom_kot_print_format": row.custom_kot_print_format,
				"transport": row.transport,
				"escpos_host": row.escpos_host,
				"escpos_port": row.escpos_port,
				"escpos_timeout": row.escpos_timeout,
			}
			for row in unit.printer_settings
		],
	}
