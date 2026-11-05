import frappe
import html
import json
from frappe.model.document import Document
from frappe.utils.html_utils import clean_html

from internal_ledger.permissions import document_company


def clean_title(value):
    value = " ".join((value or "").split())
    if len(value) > 140:
        frappe.throw("展示标题不能超过 140 个字符")
    return value


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
        self.display_title = clean_title(self.display_title)
        self.note_html = clean_note(self.note_html)
