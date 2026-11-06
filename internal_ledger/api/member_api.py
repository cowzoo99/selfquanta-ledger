import re
from uuid import uuid4

import frappe
from frappe.model.document import Document
from frappe.utils import validate_email_address

from internal_ledger.member_security import revoke_sessions, validate_temporary_password
from internal_ledger.install import ROLE_PRESETS
from internal_ledger.permissions import record_member_audit, require_member_manager


MANAGEMENT_ROLES = ("账套管理员", "企业负责人", "System Manager")
MEMBER_ROLES = set(ROLE_PRESETS) | {"账务查看者"}
SELFLEDGER_WORKSPACE = "SelfLedger 企业经营与智能内账"
MEMBER_EMAIL_SUFFIX = "@members.selfledger.invalid"
LOGIN_ACCOUNT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
MOBILE_PATTERN = re.compile(r"^\+?[0-9]{6,20}$")


def _company(for_update=False):
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not company or not frappe.db.exists("Company", company):
        frappe.throw("请先配置当前企业")
    if for_update:
        frappe.get_doc("Company", company, for_update=True)
    return company


def _managed_member(user: str, company: str, for_update: bool = False) -> Document:
    require_member_manager()
    if user == frappe.session.user:
        frappe.throw("不能操作当前账户", frappe.PermissionError)
    if (user in ("Administrator", "Guest") or not frappe.db.exists("User", user)
            or not frappe.db.exists("Has Role", {"parenttype": "User", "parent": user,
                                                  "role": ["in", list(MEMBER_ROLES)]})
            or frappe.db.exists("Has Role", {"parenttype": "User", "parent": user,
                                              "role": ["in", MANAGEMENT_ROLES]})
            or not frappe.db.exists("User Permission", {"user": user, "allow": "Company", "for_value": company})):
        frappe.throw("该用户不是当前企业的只读成员", frappe.PermissionError)
    return frappe.get_doc("User", user, for_update=for_update)


@frappe.whitelist(methods=["GET"])
def list_enterprise_members() -> dict:
    require_member_manager()
    company = _company()
    permitted = frappe.get_all("User Permission", filters={"allow": "Company", "for_value": company}, pluck="user")
    members_by_role = frappe.get_all("Has Role", filters={"parenttype": "User",
        "role": ["in", list(MEMBER_ROLES)]}, fields=["parent", "role"])
    viewers = {row.parent for row in members_by_role}
    managers = set(frappe.get_all("Has Role", filters={"parenttype": "User",
        "role": ["in", MANAGEMENT_ROLES]}, pluck="parent"))
    users = sorted(set(permitted) & viewers - managers - {"Administrator", "Guest"})
    rows = frappe.get_all("User", filters={"name": ["in", users]},
                          fields=["name", "full_name", "username", "mobile_no", "enabled", "creation", "last_login"],
                          order_by="enabled desc, full_name asc, name asc") if users else []
    role_by_user = {user: next((role for role in ROLE_PRESETS if any(
        row.parent == user and row.role == role for row in members_by_role)), "账务查看者") for user in users}
    members = [{"user": row.name, "email": row.name, "login_account": row.username or "",
                "contact_email": "" if row.name.endswith(MEMBER_EMAIL_SUFFIX) else row.name,
                "mobile_no": row.mobile_no or "", "full_name": row.full_name,
                "role_preset": role_by_user[row.name],
                "enabled": int(row.enabled), "creation": row.creation,
                "last_login": row.last_login} for row in rows]
    return {"company": company, "enabled_count": sum(row["enabled"] for row in members),
            "disabled_count": sum(not row["enabled"] for row in members), "members": members}


@frappe.whitelist(methods=["POST"])
def create_enterprise_member(first_name: str, email: str | None = None, temporary_password: str = "",
                             confirm_password: str = "", login_account: str | None = None,
                             role_preset: str = "只读审计") -> dict:
    require_member_manager()
    # Only this request boundary may restart its transaction after a database deadlock.
    for attempt in range(3):
        try:
            return _create_enterprise_member(first_name, email, temporary_password, confirm_password,
                                             login_account, role_preset)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


def _create_enterprise_member(first_name: str, email: str | None, temporary_password: str,
                              confirm_password: str, login_account: str | None = None,
                              role_preset: str = "只读审计") -> dict:
    require_member_manager()
    company = _company(for_update=True)
    first_name = first_name.strip()
    email = (email or "").strip().lower()
    login_account = (login_account or "").strip().lower()
    role_preset = (role_preset or "").strip()
    if role_preset not in ROLE_PRESETS:
        frappe.throw("请选择有效的成员角色")
    if role_preset == "企业负责人" and frappe.session.user != "Administrator" and "账套管理员" not in frappe.get_roles():
        frappe.throw("只有系统开通管理员可以新增企业负责人", frappe.PermissionError)
    missing_roles = [role for role in ROLE_PRESETS[role_preset] if not frappe.db.exists("Role", role)]
    if missing_roles:
        frappe.throw("成员角色尚未安装完整，请联系系统管理员")
    if not first_name:
        frappe.throw("请输入成员姓名")
    if login_account:
        if not LOGIN_ACCOUNT_PATTERN.fullmatch(login_account):
            frappe.throw("登录账号须为 3-32 位小写英文、数字或下划线，并以英文字母开头")
        if frappe.db.get_value("User", {"username": login_account}, "name", for_update=True):
            frappe.throw("登录账号已存在")
        email = f"{login_account}{MEMBER_EMAIL_SUFFIX}"
    elif not email or validate_email_address(email, throw=True) != email or "," in email:
        frappe.throw("请输入登录账号")
    if not temporary_password:
        frappe.throw("请输入临时密码")
    validate_temporary_password(temporary_password, confirm_password, (first_name, "", "", email, None))
    if frappe.db.get_value("User", email, "name", for_update=True):
        frappe.throw("该登录邮箱已存在")
    savepoint = "member_create_" + uuid4().hex
    frappe.db.savepoint(savepoint)
    try:
        user = frappe.get_doc({"doctype": "User", "email": email, "first_name": first_name,
            "enabled": 1, "send_welcome_email": 0, "user_type": "System User", "language": "zh",
            "default_workspace": SELFLEDGER_WORKSPACE,
            "roles": [{"role": role} for role in ROLE_PRESETS[role_preset]], "new_password": temporary_password,
            "internal_ledger_must_change_password": 1})
        if login_account:
            user.username = login_account
        try:
            user.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            frappe.throw("该登录邮箱已存在")
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
            "for_value": company, "apply_to_all_doctypes": 1}).insert(ignore_permissions=True)
        record_member_audit("create_enterprise_member", user.name, company)
    except frappe.QueryDeadlockError:
        # MariaDB has already invalidated the transaction and its savepoints.
        raise
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    return {"email": user.name, "full_name": user.full_name, "enabled": int(user.enabled),
            "must_change_password": int(user.internal_ledger_must_change_password),
            "role_preset": role_preset}


def _current_ledger_user() -> Document:
    user = frappe.session.user
    if user in (None, "", "Guest") or not frappe.db.exists("User", user):
        frappe.throw("请先登录", frappe.AuthenticationError)
    if user != "Administrator" and not set(frappe.get_roles(user)) & (MEMBER_ROLES | {"账套管理员"}):
        frappe.throw("当前账号不是企业成员", frappe.PermissionError)
    return frappe.get_doc("User", user)


def _account_response(user: Document, login_required: int | None = None) -> dict:
    result = {"login_account": user.username or "",
              "email": "" if user.name.endswith(MEMBER_EMAIL_SUFFIX) else user.name,
              "mobile_no": user.mobile_no or ""}
    if login_required is not None:
        result["login_required"] = login_required
    return result


@frappe.whitelist(methods=["GET"])
def get_my_account() -> dict:
    return _account_response(_current_ledger_user())


@frappe.whitelist(methods=["POST"])
def update_my_account(email: str = "", mobile_no: str = "", current_password: str = "") -> dict:
    from frappe.utils.password import check_password

    user = _current_ledger_user()
    if user.name in ("Administrator", "Guest"):
        frappe.throw("系统账号不能在此修改", frappe.PermissionError)
    if not current_password:
        frappe.throw("请输入当前密码")
    check_password(user.name, current_password)
    email, mobile_no = email.strip().lower(), mobile_no.strip()
    if email:
        if validate_email_address(email, throw=True) != email or "," in email:
            frappe.throw("请输入有效邮箱")
        existing = frappe.db.get_value("User", email, "name", for_update=True)
        if existing and existing != user.name:
            frappe.throw("邮箱已被其他账号绑定")
    if mobile_no:
        if not MOBILE_PATTERN.fullmatch(mobile_no):
            frappe.throw("请输入有效手机号")
        existing = frappe.db.get_value("User", {"mobile_no": mobile_no}, "name", for_update=True)
        if existing and existing != user.name:
            frappe.throw("手机号已被其他账号绑定")
    if not email and not mobile_no:
        frappe.throw("请输入要绑定的邮箱或手机号")

    savepoint = "member_contact_" + uuid4().hex
    frappe.db.savepoint(savepoint)
    try:
        name = user.name
        if email and email != name:
            name = frappe.rename_doc("User", name, email, force=True, show_alert=False)
        if mobile_no:
            frappe.db.set_value("User", name, "mobile_no", mobile_no)
        revoke_sessions(name)
        updated = frappe.get_doc("User", name)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    return _account_response(updated, login_required=1)


@frappe.whitelist(methods=["POST"])
def set_enterprise_member_enabled(user: str, enabled: int) -> dict:
    require_member_manager()
    company = _company(for_update=True)
    if not isinstance(enabled, (int, bool)) or enabled not in (0, 1):
        frappe.throw("成员启用状态必须为 0 或 1")
    member = _managed_member(user, company, for_update=True)
    enabled = int(enabled)
    savepoint = "member_enabled_" + uuid4().hex
    frappe.db.savepoint(savepoint)
    try:
        frappe.db.set_value("User", member.name, "enabled", enabled)
        if not enabled:
            revoke_sessions(member.name)
        record_member_audit("enable_enterprise_member" if enabled else "disable_enterprise_member",
                            member.name, company)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    return {"email": member.name, "enabled": enabled}


@frappe.whitelist(methods=["POST"])
def reset_enterprise_member_password(user: str, temporary_password: str, confirm_password: str) -> dict:
    require_member_manager()
    company = _company(for_update=True)
    member = _managed_member(user, company, for_update=True)
    validate_temporary_password(temporary_password, confirm_password,
                                (member.first_name, member.middle_name, member.last_name, member.email, member.birth_date))
    # This lower-level helper avoids email and the committing core session-clear helper.
    from frappe.utils.password import update_password

    savepoint = "member_password_" + uuid4().hex
    frappe.db.savepoint(savepoint)
    try:
        update_password(member.name, temporary_password, logout_all_sessions=False)
        frappe.db.set_value("User", member.name, {
            "internal_ledger_must_change_password": 1,
            "reset_password_key": "",
            "last_reset_password_key_generated_on": None,
        })
        frappe.cache.hdel("redirect_after_login", member.name)
        revoke_sessions(member.name)
        record_member_audit("reset_enterprise_member_password", member.name, company)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    return {"email": member.name, "must_change_password": 1}
