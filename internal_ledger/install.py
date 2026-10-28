import json
from pathlib import Path

import frappe
from internal_ledger.permissions import require_ledger_admin


ROLE_PERMISSIONS = {
    "账套管理员": {"read", "write", "create", "submit", "report"},
    "账务查看者": {"read", "report"},
}


def ensure_roles() -> None:
    require_ledger_admin()
    for role in ROLE_PERMISSIONS:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert()
    from frappe.permissions import add_permission, rights
    from internal_ledger.permissions import LEDGER_DOCTYPES

    # App schemas are authoritative, including sites with legacy Custom DocPerm overrides.
    frappe.db.delete("Custom DocPerm", {"parent": ["in", LEDGER_DOCTYPES]})
    for role, allowed in ROLE_PERMISSIONS.items():
        for doctype in ("Journal Entry", "Account", "GL Entry"):
            add_permission(doctype, role, ptype="read")
            actions = allowed if doctype == "Journal Entry" else {"read", "report"}
            frappe.db.set_value("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0},
                                {action: int(action in actions) for action in rights})
    frappe.clear_cache()


def load_chart_fixture() -> dict:
    path = Path(__file__).parent / "chart_templates" / "small_business_chart_cn.json"
    return json.loads(path.read_text(encoding="utf-8"))


def install_chart(company: str) -> None:
    require_ledger_admin()
    frappe.get_doc("Company", company)
    installed = {}
    roots = {}
    for accounts in load_chart_fixture().values():
        for account in accounts:
            values = dict(account)
            parent_number = values.pop("parent_account_number", None)
            root_type = values["root_type"]
            if parent_number:
                parent = installed[parent_number]
            else:
                if root_type not in roots:
                    matches = frappe.get_all("Account", filters={
                        "company": company, "root_type": root_type, "is_group": 1,
                        "parent_account": ["is", "not set"],
                    }, pluck="name")
                    if len(matches) != 1:
                        frappe.throw(f"Company {company} must have exactly one {root_type} root group.")
                    roots[root_type] = matches[0]
                parent = roots[root_type]
            values.update(company=company, parent_account=parent)
            values.setdefault("is_group", 0)
            matches = frappe.get_all("Account", filters={
                "company": company, "account_number": values["account_number"],
            }, fields=["*"])
            if matches:
                if len(matches) != 1 or any(matches[0].get(field) != value for field, value in values.items()):
                    frappe.throw(f"Account {values['account_number']} conflicts with the small business chart.")
                name = matches[0].name
            else:
                name = frappe.get_doc({"doctype": "Account", **values}).insert().name
            installed[values["account_number"]] = name
