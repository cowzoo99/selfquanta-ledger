import frappe
import html
import json
from frappe.model.document import Document
from frappe.utils.html_utils import clean_html

from internal_ledger.permissions import document_company


def clean_note(value):
    value = value or ""
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return clean_html(value)

    def clean_json(item):
        if isinstance(item, str):
            return clean_html(item)
        if isinstance(item, list):
            return [clean_json(child) for child in item]
        if isinstance(item, dict):
            return {key: clean_json(child) for key, child in item.items()}
        return item

    return html.escape(json.dumps(clean_json(parsed), ensure_ascii=False))


class LedgerEvidence(Document):
    def validate(self):
        tx = frappe.get_doc("Imported Transaction", self.imported_transaction)
        tx.check_permission("read")
        self.company = document_company(tx)
        self.note_html = clean_note(self.note_html)
