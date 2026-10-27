app_name = "internal_ledger"
app_title = "Internal Ledger"
app_publisher = "Internal Ledger"
app_description = "Internal ledger for a single company."
app_email = "internal-ledger@example.com"
app_license = "AGPL-3.0-only"

after_install = "internal_ledger.install.ensure_roles"
after_migrate = "internal_ledger.install.ensure_roles"
