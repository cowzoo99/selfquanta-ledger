import frappe

from internal_ledger.accounting.manual_classification import classify_manually as _classify_manually
from internal_ledger.install import load_chart_fixture


ROOT_LABELS = {
    "Asset": "资产", "Liability": "负债", "Equity": "所有者权益",
    "Income": "收入", "Expense": "成本费用",
}


@frappe.whitelist(methods=["GET", "POST"])
def list_leaf_accounts():
    frappe.has_permission("Internal Ledger Settings", ptype="read", throw=True)
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not company:
        return []
    numbers = [row["account_number"] for group in load_chart_fixture().values()
               for row in group if row.get("is_group") != 1]
    rows = frappe.get_list("Account", filters={"company": company, "disabled": 0, "is_group": 0,
                           "account_number": ["in", numbers]},
                           fields=["name", "account_number", "account_name", "root_type"],
                           order_by="account_number, name", limit_page_length=0)
    return [{**row, "root_label": ROOT_LABELS[row["root_type"]]} for row in rows]


@frappe.whitelist(methods=["POST"])
def classify_manually(tx_name, transaction_type, debit_account, credit_account, summary, remember_counterparty):
    return _classify_manually(tx_name, transaction_type, debit_account, credit_account, summary, remember_counterparty)
