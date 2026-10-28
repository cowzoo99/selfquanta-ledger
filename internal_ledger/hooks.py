app_name = "internal_ledger"
app_title = "Internal Ledger"
app_publisher = "Internal Ledger"
app_description = "Internal ledger for a single company."
app_email = "internal-ledger@example.com"
app_license = "AGPL-3.0-only"

after_install = "internal_ledger.install.ensure_roles"
after_migrate = "internal_ledger.install.ensure_roles"

_ledger_doctypes = ("Import Batch", "Imported Transaction", "Classification Rule", "Voucher Draft",
                    "Posting Link", "Internal Ledger Settings", "Internal Ledger Audit Event",
                    "Internal Ledger Source File")
has_permission = {doctype: "internal_ledger.permissions.has_ledger_permission" for doctype in _ledger_doctypes}
permission_query_conditions = {doctype: "internal_ledger.permissions.company_query"
                              for doctype in _ledger_doctypes if doctype != "Internal Ledger Settings"}
for _doctype in ("Journal Entry", "Account", "GL Entry"):
    has_permission[_doctype] = "internal_ledger.permissions.has_reporting_permission"
    permission_query_conditions[_doctype] = "internal_ledger.permissions.company_query"
doc_events = {doctype: {"before_validate": "internal_ledger.permissions.protect_business_write",
                       "on_trash": "internal_ledger.permissions.protect_business_write"}
              for doctype in _ledger_doctypes if doctype != "Internal Ledger Audit Event"}
doc_events["File"] = {"before_validate": "internal_ledger.permissions.protect_source_file",
                      "on_trash": "internal_ledger.permissions.protect_source_file"}
override_whitelisted_methods = {
    "erpnext.accounts.utils.get_balance_on": "internal_ledger.api.report_api.scoped_native_balance",
}
