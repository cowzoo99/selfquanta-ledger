from dataclasses import dataclass
from typing import Literal

import frappe

from internal_ledger.accounting.drafts import validate_transaction_accounts


RULE_ORDER = {"counterparty": 3, "keyword": 2, "confirmed_history": 1}
REASONS = {"counterparty": "交易对象精确匹配", "keyword": "摘要关键词匹配",
           "confirmed_history": "已确认历史匹配"}


@dataclass(frozen=True)
class ClassificationResult:
    status: Literal["matched", "unmatched", "conflict"]
    debit_account: str | None
    credit_account: str | None
    rule_name: str | None
    reason: str


def classify(tx_name: str) -> ClassificationResult:
    tx = frappe.get_doc("Imported Transaction", tx_name)
    if tx.status != "Imported":
        frappe.throw("重复流水不得分类或生成草稿")
    company = frappe.db.get_value("Import Batch", tx.import_batch, "company")
    rules = frappe.get_all("Classification Rule", filters={
        "company": company, "enabled": 1, "direction": tx.direction,
    }, fields=["*"])
    matches = []
    for rule in rules:
        if not rule.value:
            continue
        if rule.kind == "keyword":
            matched = rule.value in (tx.summary or "")
        else:
            matched = rule.value == tx.counterparty and (
                rule.kind == "counterparty" or (rule.kind == "confirmed_history" and rule.confirmed)
            )
        if matched:
            matches.append(rule)
    if not matches:
        return ClassificationResult("unmatched", None, None, None, "未匹配分类规则")
    highest = max(RULE_ORDER[rule.kind] for rule in matches)
    matches = sorted((rule for rule in matches if RULE_ORDER[rule.kind] == highest), key=lambda r: r.name)
    # Numeric priority never hides disagreement within the highest matching layer.
    targets = set()
    for rule in matches:
        debit = rule.debit_account or (tx.payment_account if tx.direction == "income" else None)
        credit = rule.credit_account or (tx.payment_account if tx.direction == "expense" else None)
        targets.add((debit, credit, rule.transaction_type))
    if len(targets) != 1:
        return ClassificationResult("conflict", None, None, None, "同一最高匹配层存在不同科目或业务类型")
    debit, credit, kind = targets.pop()
    validate_transaction_accounts(debit, credit, tx.payment_account, kind, tx.direction, company)
    rule = matches[0]
    return ClassificationResult("matched", debit, credit, rule.name, REASONS[rule.kind])
