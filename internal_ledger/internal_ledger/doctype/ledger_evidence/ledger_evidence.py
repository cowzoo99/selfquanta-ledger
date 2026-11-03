import frappe
from frappe.model.document import Document
from frappe.utils import sanitize_html

from internal_ledger.permissions import document_company


class LedgerEvidence(Document):
    def validate(self):
        tx = frappe.get_doc("Imported Transaction", self.imported_transaction)
        tx.check_permission("read")
        self.company = document_company(tx)
        self.note_html = sanitize_html(self.note_html or "")
