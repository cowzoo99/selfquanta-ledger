from uuid import uuid4

import frappe
from frappe.model.document import Document
from frappe.utils import validate_email_address

from internal_ledger.member_security import validate_temporary_password
from internal_ledger.permissions import record_member_audit, require_ledger_admin


def _company(for_update=False):
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not company or not frappe.db.exists("Company", company):
        frappe.throw("请先配置当前企业")
    if for_update:
        frappe.get_doc("Company", company, for_update=True)
    return company


def _managed_member(user: str, company: str, for_update: bool = False) -> Document:
    require_ledger_admin()
    if (user in ("Administrator", "Guest") or not frappe.db.exists("User", user)
            or not frappe.db.exists("Has Role", {"parenttype": "User", "parent": user, "role": "账务查看者"})
            or not frappe.db.exists("User Permission", {"user": user, "allow": "Company", "for_value": company})):
        frappe.throw("该用户不是当前企业的只读成员", frappe.PermissionError)
    return frappe.get_doc("User", user, for_update=for_update)


@frappe.whitelist(methods=["GET"])
def list_enterprise_members() -> dict:
    require_ledger_admin()
    company = _company()
    permitted = frappe.get_all("User Permission", filters={"allow": "Company", "for_value": company}, pluck="user")
    viewers = set(frappe.get_all("Has Role", filters={"parenttype": "User", "role": "账务查看者"}, pluck="parent"))
    users = sorted(set(permitted) & viewers - {"Administrator", "Guest"})
    rows = frappe.get_all("User", filters={"name": ["in", users]},
                          fields=["name", "full_name", "enabled", "creation", "last_login"],
                          order_by="enabled desc, full_name asc, name asc") if users else []
    members = [{"email": row.name, "full_name": row.full_name, "enabled": int(row.enabled),
                "creation": row.creation, "last_login": row.last_login} for row in rows]
    return {"company": company, "enabled_count": sum(row["enabled"] for row in members),
            "disabled_count": sum(not row["enabled"] for row in members), "members": members}


@frappe.whitelist(methods=["POST"])
def create_enterprise_member(first_name: str, email: str, temporary_password: str, confirm_password: str) -> dict:
    require_ledger_admin()
    company = _company(for_update=True)
    first_name, email = first_name.strip(), email.strip().lower()
    if not first_name:
        frappe.throw("请输入成员姓名")
    if not email or validate_email_address(email, throw=True) != email or "," in email:
        frappe.throw("请输入有效的登录邮箱")
    if not temporary_password:
        frappe.throw("请输入临时密码")
    validate_temporary_password(temporary_password, confirm_password, (first_name, "", "", email, None))
    if frappe.db.exists("User", email):
        frappe.throw("该登录邮箱已存在")
    savepoint = "member_create_" + uuid4().hex
    frappe.db.savepoint(savepoint)
    try:
        user = frappe.get_doc({"doctype": "User", "email": email, "first_name": first_name,
            "enabled": 1, "send_welcome_email": 0, "user_type": "System User",
            "roles": [{"role": "账务查看者"}], "new_password": temporary_password,
            "internal_ledger_must_change_password": 1})
        try:
            user.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            frappe.throw("该登录邮箱已存在")
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
            "for_value": company, "apply_to_all_doctypes": 1}).insert(ignore_permissions=True)
        record_member_audit("create_enterprise_member", user.name, company)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    return {"email": user.name, "full_name": user.full_name, "enabled": int(user.enabled),
            "must_change_password": int(user.internal_ledger_must_change_password)}
