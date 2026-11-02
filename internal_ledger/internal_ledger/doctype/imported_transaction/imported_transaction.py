import frappe
from frappe.model.document import Document


class ImportedTransaction(Document):
    def validate(self):
        if self.status == "Duplicate":
            if (self.duplicate_review_status not in {"Pending", "Confirmed"}
                    or self.dedupe_key or not self.duplicate_of):
                frappe.throw("A duplicate must reference its original and leave the dedupe key empty.")
        elif self.status == "Imported" and self.duplicate_review_status == "Released":
            if self.dedupe_key != f"released:{self.name}" or not self.duplicate_of:
                frappe.throw("A released duplicate must preserve its original and use its own released key.")
        elif not self.dedupe_key or self.duplicate_of:
            frappe.throw("An imported transaction must hold a dedupe key and have no duplicate reference.")
