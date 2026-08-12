from ury.setup import after_install as setup


def after_install():
	"""Install every URY custom field on a fresh site."""
	setup()
