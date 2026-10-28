from decimal import Decimal

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from internal_ledger.accounting.drafts import DraftLine, current_draft, validate_balanced


class VoucherDraft(Document):
    def validate(self):
        if self.status != "Draft" or self.docstatus != 0:
            frappe.throw("自动草稿只能处于 Draft 状态")
        lines = [DraftLine(row.account, Decimal(str(row.debit or 0)), Decimal(str(row.credit or 0)))
                 for row in self.lines]
        validate_balanced(lines)
        _, expected = current_draft(self.imported_transaction)
        fields = ("classification_rule", "transaction_type", "classification_reason", "company", "base_currency")
        if any(self.get(field) != expected[field] for field in fields) or getdate(self.posting_date) != expected["posting_date"]:
            frappe.throw("草稿必须与当前分类规则、业务类型、原因及流水信息一致")
        if lines != [DraftLine(**row) for row in expected["lines"]]:
            frappe.throw("草稿完整借贷科目及金额必须与当前分类规则一致")
