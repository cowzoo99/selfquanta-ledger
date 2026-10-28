from decimal import Decimal

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from internal_ledger.accounting.drafts import (
    DraftLine, current_draft, validate_balanced, validate_transaction_accounts,
)


class VoucherDraft(Document):
    def validate(self):
        names = [row.name for row in self.lines if row.name]
        if len(names) != len(set(names)):
            frappe.throw("分录行身份重复，不得复用同一子行名称")

        # Frappe rewrites child parent fields before validate, so check persisted ownership.
        canonical_names = set()
        for row in self.lines:
            identity = frappe.db.get_value("Voucher Draft Line", row.name,
                                           ["name", "parent", "parenttype", "parentfield"], for_update=True)
            if identity:
                if identity[1:] != (self.name, "Voucher Draft", "lines"):
                    frappe.throw("分录行身份不属于当前草稿")
                if identity[0] in canonical_names:
                    frappe.throw("分录行身份重复，不得复用同一数据库子行")
                canonical_names.add(identity[0])
            elif not row.is_new():
                frappe.throw("分录行身份不存在，不得伪造既有行名")

        from internal_ledger.api.review_api import assert_transition

        previous = self.get_doc_before_save()
        if not previous and not self.is_new():
            previous = frappe.get_doc("Voucher Draft", self.name)
        posting = (self.flags.posting_transition and previous and previous.status == "Approved"
                   and self.status == "Posted")
        if self.docstatus != 0 or (self.status not in {"Draft", "Approved", "Rejected"} and not posting):
            frappe.throw("当前状态不可保存；过账须使用专用服务")
        if not previous and (self.status != "Draft" or self.is_manual_override or self.manual_override_by):
            frappe.throw("自动草稿只能处于 Draft 状态，不得伪造人工覆盖")
        if previous and self.imported_transaction != previous.imported_transaction:
            frappe.throw("草稿原流水不可更换")
        if previous and self.status != previous.status:
            if self.flags.review_transition != self.status and not posting:
                frappe.throw("状态只能由审核服务变更")
            assert_transition(previous.status, self.status)
        if previous and not self.flags.review_edit:
            protected = ("is_manual_override", "manual_override_by", "rejection_reason")
            if any(self.get(field) != previous.get(field) for field in protected) and not self.flags.review_transition:
                frappe.throw("审核元数据只能由审核服务变更")
            if previous.is_manual_override or previous.status != "Draft":
                protected = ("summary", "posting_date", "is_manual_override", "manual_override_by")
                old_lines = [(row.account, Decimal(str(row.debit or 0)), Decimal(str(row.credit or 0)))
                             for row in previous.lines]
                new_lines = [(row.account, Decimal(str(row.debit or 0)), Decimal(str(row.credit or 0)))
                             for row in self.lines]
                if old_lines != new_lines or any(self.get(field) != previous.get(field) for field in protected):
                    frappe.throw("人工覆盖或已审核内容只能由审核服务编辑")
        lines = [DraftLine(row.account, Decimal(str(row.debit or 0)), Decimal(str(row.credit or 0)))
                 for row in self.lines]
        validate_balanced(lines)
        _, expected = current_draft(self.imported_transaction)
        fields = ("classification_rule", "transaction_type", "classification_reason", "company", "base_currency")
        if any(self.get(field) != expected[field] for field in fields):
            frappe.throw("草稿必须与当前分类规则、业务类型、原因及流水信息一致")
        if self.is_manual_override:
            tx = frappe.get_doc("Imported Transaction", self.imported_transaction, for_update=True)
            for line in lines:
                if tx.direction == "expense":
                    debit, credit = (line.account, tx.payment_account) if line.debit else (expected["lines"][0]["account"], line.account)
                else:
                    debit, credit = (line.account, expected["lines"][1]["account"]) if line.debit else (tx.payment_account, line.account)
                validate_transaction_accounts(debit, credit, tx.payment_account, self.transaction_type, tx.direction, self.company)
            if sum((line.debit for line in lines), Decimal("0")) != Decimal(str(tx.amount)):
                frappe.throw("人工分录总金额必须等于原流水金额")
            from erpnext.accounts.utils import get_fiscal_year

            get_fiscal_year(self.posting_date, company=self.company)
            frozen = frappe.db.get_value("Company", self.company, "accounts_frozen_till_date")
            if frozen and getdate(self.posting_date) <= getdate(frozen):
                frappe.throw("记账日期处于冻结期间")
            return
        if getdate(self.posting_date) != expected["posting_date"]:
            frappe.throw("草稿日期必须与流水一致；请使用人工审核编辑")
        if lines != [DraftLine(**row) for row in expected["lines"]]:
            frappe.throw("草稿完整借贷科目及金额必须与当前分类规则一致")
