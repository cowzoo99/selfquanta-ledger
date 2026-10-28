import frappe
from frappe.model.document import Document

from internal_ledger.permissions import _AUDIT_INSERT


class InternalLedgerAuditEvent(Document):
    def before_insert(self):
        if self.flags.audit_insert is not _AUDIT_INSERT:
            frappe.throw("审计记录仅允许系统创建", frappe.PermissionError)

    def validate(self):
        if not self.is_new():
            frappe.throw("审计记录不可修改", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("审计记录不可删除", frappe.PermissionError)
