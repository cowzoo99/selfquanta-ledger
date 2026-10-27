import frappe
from frappe.model.document import Document


class ImportedTransaction(Document):
    def validate(self):
        if self.status == "Duplicate":
            if self.dedupe_key or not self.duplicate_of:
                frappe.throw("A duplicate must reference its original and leave the dedupe key empty.")
        elif not self.dedupe_key or self.duplicate_of:
            frappe.throw("An imported transaction must hold a dedupe key and have no duplicate reference.")
