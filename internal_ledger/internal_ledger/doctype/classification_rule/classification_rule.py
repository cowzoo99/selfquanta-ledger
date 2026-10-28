from frappe.model.document import Document

from internal_ledger.accounting.drafts import validate_account


class ClassificationRule(Document):
    def validate(self):
        for account in (self.debit_account, self.credit_account):
            if account:
                validate_account(account, self.company, {"Asset", "Liability", "Equity", "Income", "Expense"})
