import frappe


SUPPORTED_LANGUAGES = {"en", "zh"}


@frappe.whitelist(methods=["POST"])
def set_language(language: str) -> dict:
    if frappe.session.user == "Guest":
        frappe.throw("请先登录后再切换语言", frappe.AuthenticationError)
    if not isinstance(language, str) or language not in SUPPORTED_LANGUAGES:
        frappe.throw("不支持该界面语言", frappe.ValidationError)
    frappe.db.set_value("User", frappe.session.user, "language", language)
    return {"language": language}
