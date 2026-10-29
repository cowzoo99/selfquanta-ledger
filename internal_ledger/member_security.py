import frappe
from frappe.core.doctype.user.user import test_password_strength


ALLOWED_RESET_COMMANDS = {
    "login",
    "frappe.auth.login",
    "frappe.core.doctype.user.user.test_password_strength",
    "frappe.core.doctype.user.user.update_password",
    "logout",
    "frappe.auth.logout",
}
ALLOWED_RESET_PATHS = {"/update-password"}


def validate_temporary_password(password: str, confirm_password: str, user_data: tuple | None = None) -> None:
    if not isinstance(password, str) or not password:
        frappe.throw("请输入临时密码")
    if password != confirm_password:
        frappe.throw("两次密码不一致")
    result = test_password_strength(password, user_data=user_data)
    feedback = result.get("feedback")
    if feedback and not feedback.get("password_policy_validation_passed", False):
        frappe.throw("临时密码不符合密码安全策略")


def revoke_sessions(user: str) -> None:
    table = frappe.qb.DocType("Sessions")
    sessions = frappe.qb.from_(table).select(table.sid).where(table.user == user).run(pluck=True)
    for sid in sessions:
        frappe.cache.hdel("session", sid)
    frappe.db.delete("Sessions", {"user": user})


def redirect_temporary_password(login_manager) -> None:
    user = login_manager.user
    if frappe.db.get_value("User", user, "internal_ledger_must_change_password"):
        url = frappe.get_doc("User", user)._reset_password(send_email=False)
        frappe.cache.hset("redirect_after_login", user, url)


def _request_command():
    command = frappe.form_dict.get("cmd")
    if command:
        return command
    path = getattr(frappe.local.request, "path", "")
    for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/"):
        if path.startswith(prefix):
            return path[len(prefix):]


def _validate_cookie_session() -> None:
    request = getattr(frappe.local, "request", None)
    if not request or frappe.flags.get("internal_ledger_cookie_session_validated"):
        return
    sid = getattr(request, "cookies", {}).get("sid")
    user = frappe.session.user
    if not sid or sid == "Guest" or user in ("", "Guest"):
        return
    if (not frappe.db.get_value("User", user, "enabled")
            or not frappe.db.exists("Sessions", {"sid": sid, "user": user})):
        frappe.cache.hdel("session", sid)
        raise frappe.AuthenticationError("会话已失效，请重新登录")
    frappe.flags.internal_ledger_cookie_session_validated = True


def block_temporary_password_session() -> None:
    _validate_cookie_session()
    user = frappe.session.user
    if user in ("Guest", "Administrator") or not frappe.db.get_value(
        "User", user, "internal_ledger_must_change_password"
    ):
        return
    command = _request_command()
    if command in ALLOWED_RESET_COMMANDS:
        if command in ("login", "frappe.auth.login") and frappe.local.response.get("redirect_to"):
            frappe.local.response["home_page"] = frappe.local.response["redirect_to"]
        return
    if not command and getattr(frappe.local.request, "path", "") in ALLOWED_RESET_PATHS:
        return
    raise frappe.AuthenticationError("请先修改临时密码")


def clear_temporary_password_flag(doc, method=None) -> None:
    # This hook also runs before forgot-password email; only update_password has completed a reset.
    if (_request_command() == "frappe.core.doctype.user.user.update_password"
            and not doc.reset_password_key and doc.internal_ledger_must_change_password):
        doc.db_set("internal_ledger_must_change_password", 0)
