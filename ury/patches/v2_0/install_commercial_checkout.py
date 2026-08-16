from ury.setup import add_custom_fields, install_commercial_checkout_runtime_artifacts


def execute():
	"""Install the additive URY checkout model with all feature flags off."""
	add_custom_fields()
	install_commercial_checkout_runtime_artifacts()
