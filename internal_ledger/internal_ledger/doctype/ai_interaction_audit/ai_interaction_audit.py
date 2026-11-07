import frappe
from frappe.model.document import Document


AI_AUDIT_INSERT = object()


class AIInteractionAudit(Document):
    def before_insert(self):
        if self.flags.ai_audit_insert is not AI_AUDIT_INSERT:
            frappe.throw("AI 审计记录仅允许系统创建", frappe.PermissionError)

    def validate(self):
        if not self.is_new():
            frappe.throw("AI 审计记录不可修改", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("AI 审计记录不可删除", frappe.PermissionError)
