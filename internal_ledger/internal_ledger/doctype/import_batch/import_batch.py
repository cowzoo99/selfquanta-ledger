from frappe.model.document import Document
import frappe
from internal_ledger.permissions import _SOURCE_INSERT


class ImportBatch(Document):
    def validate(self):
        old = self.get_doc_before_save()
        if old and old.raw_file and any(self.get(field) != old.get(field)
                for field in ("source", "file_hash", "company", "base_currency", "raw_file")):
            frappe.throw("原始导入文件及来源不可修改", frappe.PermissionError)
        if self.raw_file and (not old or not old.raw_file) and self.flags.source_insert is not _SOURCE_INSERT:
            frappe.throw("原始文件仅允许导入服务保存", frappe.PermissionError)
