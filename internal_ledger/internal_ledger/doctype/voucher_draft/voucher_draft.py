from decimal import Decimal

import frappe
from frappe.model.document import Document

from internal_ledger.accounting.drafts import DraftLine, validate_balanced, validate_transaction_accounts


class VoucherDraft(Document):
    def validate(self):
        if self.status != "Draft" or self.docstatus != 0:
            frappe.throw("自动草稿只能处于 Draft 状态")
        lines = [DraftLine(row.account, Decimal(str(row.debit or 0)), Decimal(str(row.credit or 0)))
                 for row in self.lines]
        validate_balanced(lines)
        tx = frappe.get_doc("Imported Transaction", self.imported_transaction)
        batch = frappe.get_doc("Import Batch", tx.import_batch)
        if tx.status != "Imported" or self.company != batch.company or self.base_currency != batch.base_currency:
            frappe.throw("草稿公司、币种必须与有效流水批次一致")
        if len(lines) != 2 or lines[0].debit != Decimal(str(tx.amount)) or lines[1].credit != Decimal(str(tx.amount)):
            frappe.throw("草稿分录必须与流水金额一致")
        validate_transaction_accounts(lines[0].account, lines[1].account, tx.payment_account,
                                      self.transaction_type, tx.direction, self.company)
