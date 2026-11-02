import frappe


WORKSPACE = "SelfLedger 企业经营与智能内账"
LEDGER_ROLES = ("账套管理员", "账务查看者")


def execute():
    if not frappe.db.exists("Workspace", WORKSPACE):
        return
    users = {"Administrator"}
    users.update(frappe.get_all("Has Role", filters={
        "parenttype": "User", "role": ["in", LEDGER_ROLES],
    }, pluck="parent"))
    for user in users:
        if not frappe.db.get_value("User", user, "default_workspace"):
            frappe.db.set_value("User", user, "default_workspace", WORKSPACE)
            frappe.clear_cache(user=user)
