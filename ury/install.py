from ury.setup import after_install as setup
from ury.setup import after_migrate as migrate_setup


def after_install():
	"""Install every URY custom field on a fresh site."""
	setup()


def after_migrate():
	"""Restore URY runtime invariants after patches and fixtures."""
	migrate_setup()
