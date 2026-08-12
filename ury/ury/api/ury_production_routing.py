import frappe
from frappe.utils import cint


def get_order_menu(branch, restaurant_table=None):
	"""Return the menu that determines item courses for an order."""
	if restaurant_table:
		room, restaurant = frappe.db.get_value(
			"URY Table",
			restaurant_table,
			["restaurant_room", "restaurant"],
		) or (None, None)
		if room and restaurant:
			menu = frappe.db.get_value(
				"Menu for Room",
				{"room": room, "parent": restaurant},
				"menu",
			)
			if menu:
				return menu

	return frappe.db.get_value("URY Restaurant", {"branch": branch}, "active_menu")


def get_production_route_state(branch, item_codes, menu=None):
	"""Resolve one production unit per item.

	Menu-course assignments are more specific than item-group assignments. This
	allows a branch to send, for example, drinks to the bar while retaining the
	broader ``Gelados`` item-group fallback for counter items. Item masters remain
	unchanged, which is important because they are shared by multiple branches.
	"""
	item_codes = list(dict.fromkeys(item_codes))
	items = (
		frappe.get_all(
			"Item",
			filters={"name": ["in", item_codes]},
			fields=["name", "item_group", "disabled"],
		)
		if item_codes
		else []
	)
	item_map = {row.name: row for row in items}

	productions = frappe.get_all(
		"URY Production Unit",
		filters={"branch": branch},
		fields=["name"],
		order_by="name",
	)
	production_names = [row.name for row in productions]

	group_assignments = (
		frappe.get_all(
			"URY Production Item Groups",
			filters={
				"parent": ["in", production_names],
				"parenttype": "URY Production Unit",
			},
			fields=["parent", "item_group"],
		)
		if production_names
		else []
	)
	units_by_group = {}
	for assignment in group_assignments:
		units_by_group.setdefault(assignment.item_group, set()).add(assignment.parent)

	units_by_course = {}
	courses_by_item = {}
	if menu and production_names and item_codes:
		course_assignments = frappe.get_all(
			"URY Production Menu Courses",
			filters={
				"parent": ["in", production_names],
				"parenttype": "URY Production Unit",
			},
			fields=["parent", "menu_course"],
		)
		for assignment in course_assignments:
			units_by_course.setdefault(assignment.menu_course, set()).add(
				assignment.parent
			)

		menu_items = frappe.get_all(
			"URY Menu Item",
			filters={
				"parent": menu,
				"item": ["in", item_codes],
				"disabled": 0,
			},
			fields=["item", "course"],
		)
		for menu_item in menu_items:
			if menu_item.course:
				courses_by_item.setdefault(menu_item.item, set()).add(menu_item.course)

	routes = {}
	route_sources = {}
	unrouted = []
	duplicated = {}
	for item_code in item_codes:
		item = item_map.get(item_code)
		if not item:
			continue

		course_units = set()
		for course in courses_by_item.get(item_code, set()):
			course_units.update(units_by_course.get(course, set()))

		if course_units:
			units = sorted(course_units)
			route_sources[item_code] = "menu_course"
		else:
			units = sorted(units_by_group.get(item.item_group, set()))
			route_sources[item_code] = "item_group"

		if not units:
			unrouted.append(item_code)
		elif len(units) > 1:
			duplicated[item_code] = units
		else:
			routes[item_code] = units[0]

	return {
		"routes": routes,
		"route_sources": route_sources,
		"unrouted": unrouted,
		"duplicated": duplicated,
		"missing": [item for item in item_codes if item not in item_map],
		"disabled": [
			item
			for item in item_codes
			if item in item_map and cint(item_map[item].disabled)
		],
		"productions": production_names,
		"courses_by_item": courses_by_item,
	}
