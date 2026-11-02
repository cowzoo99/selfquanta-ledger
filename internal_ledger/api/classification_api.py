import frappe

from internal_ledger.accounting.manual_classification import classify_manually as _classify_manually


@frappe.whitelist(methods=["GET", "POST"])
def list_leaf_accounts():
    frappe.has_permission("Internal Ledger Settings", ptype="read", throw=True)
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not company:
        return []
    return frappe.get_list("Account", filters={"company": company, "disabled": 0, "is_group": 0},
                           fields=["name", "account_number", "account_name", "root_type"],
                           order_by="account_number, name", limit_page_length=0)


@frappe.whitelist(methods=["POST"])
def classify_manually(tx_name, transaction_type, debit_account, credit_account, summary, remember_counterparty):
    return _classify_manually(tx_name, transaction_type, debit_account, credit_account, summary, remember_counterparty)