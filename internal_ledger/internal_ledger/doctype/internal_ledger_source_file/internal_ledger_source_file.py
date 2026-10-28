import frappe
from frappe.model.document import Document

from internal_ledger.permissions import _SOURCE_INSERT


class InternalLedgerSourceFile(Document):
    def before_insert(self):
        if self.flags.source_insert is not _SOURCE_INSERT:
            frappe.throw("原始文件仅允许导入服务保存", frappe.PermissionError)

    def validate(self):
        old = self.get_doc_before_save()
        if not self.is_new() and (self.flags.source_insert is not _SOURCE_INSERT or not old
                or old.raw_file or old.import_batch != self.import_batch):
            frappe.throw("原始文件只读保存", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("原始文件只读保存", frappe.PermissionError)
