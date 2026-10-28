import frappe
from frappe.model.document import Document


class PostingLink(Document):
    def validate(self):
        if not self.flags.posting_service:
            frappe.throw("过账关联只能由专用过账或冲销服务维护")
        if self.status == "Reversed" and (not self.reversal_journal_entry or not (self.reversal_reason or "").strip()):
            frappe.throw("冲销必须关联凭证并填写原因")
