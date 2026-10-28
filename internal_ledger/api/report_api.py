import frappe


@frappe.whitelist(methods=["GET", "POST"])
def get_account_balance(account: str, date: str):
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    doc = frappe.get_doc("Account", account)
    doc.check_permission("read")
    if not company or doc.company != company:
        frappe.throw("只能读取当前账套公司", frappe.PermissionError)
    from erpnext.accounts.utils import get_balance_on

    return get_balance_on(account=account, date=date, company=company)


@frappe.whitelist()
def scoped_native_balance(account=None, date=None, company=None, **kwargs):
    from erpnext.accounts.utils import get_balance_on

    if frappe.session.user != "Administrator" and set(frappe.get_roles()) & {"账套管理员", "账务查看者"}:
        ledger_company = frappe.db.get_single_value("Internal Ledger Settings", "company")
        if not ledger_company or (company and company != ledger_company):
            frappe.throw("只能读取当前账套公司", frappe.PermissionError)
        company = ledger_company
        account = account or frappe.form_dict.get("account")
        if account:
            doc = frappe.get_doc("Account", account)
            doc.check_permission("read")
            if doc.company != company:
                frappe.throw("只能读取当前账套公司", frappe.PermissionError)
        kwargs["ignore_account_permission"] = False
    return get_balance_on(account=account, date=date, company=company, **kwargs)
