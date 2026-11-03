from dataclasses import asdict
from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.utils import cint, getdate

from internal_ledger.accounting.classifier import ClassificationResult, classify
from internal_ledger.accounting.drafts import build_lines, create_draft, validate_transaction_accounts
from internal_ledger.permissions import require_ledger_admin, record_audit


_MANUAL_CLASSIFICATION = object()


def classify_manually(tx_name, transaction_type, debit_account, credit_account, summary, remember_counterparty):
    require_ledger_admin()
    if not isinstance(tx_name, str) or not tx_name.strip():
        frappe.throw("必须选择流水")
    if not isinstance(summary, str):
        frappe.throw("摘要必须为文本")
    remember = bool(cint(remember_counterparty))
    tx = frappe.get_doc("Imported Transaction", tx_name, for_update=True)
    tx.check_permission("read")
    tx.check_permission("write")
    batch = frappe.get_doc("Import Batch", tx.import_batch, for_update=True)
    batch.check_permission("read")
    batch.check_permission("write")
    if tx.status != "Imported":
        frappe.throw("重复流水不得分类或生成草稿")
    if remember and not (tx.counterparty or "").strip():
        frappe.throw("记住规则必须填写交易对象")
    # Serialize rule learning across batches of the same company.
    frappe.get_doc("Company", batch.company, for_update=True)
    validate_transaction_accounts(debit_account, credit_account, tx.payment_account,
                                  transaction_type, tx.direction, batch.company)
    result = ClassificationResult("matched", debit_account, credit_account, None, "人工分类")
    lines = build_lines(Decimal(str(tx.amount)), result, transaction_type)
    existing = frappe.db.get_value("Voucher Draft", {"imported_transaction": tx_name}, "name", for_update=True)
    if existing:
        doc = frappe.get_doc("Voucher Draft", existing, for_update=True)
        doc.check_permission("read")
        doc.check_permission("write")
        if (doc.transaction_type != transaction_type or (doc.summary or "") != summary
                or bool(doc.classification_rule) != remember
                or [(row.account, Decimal(str(row.debit or 0)), Decimal(str(row.credit or 0))) for row in doc.lines]
                != [(row.account, row.debit, row.credit) for row in lines]):
            frappe.throw("已有草稿与本次分类冲突，请使用草稿审核编辑")
        if not remember:
            doc.validate()
            return doc
    changed = not existing
    savepoint = "ledger_manual_" + uuid4().hex
    frappe.db.savepoint(savepoint)
    try:
        if remember:
            rules = frappe.db.get_values("Classification Rule", filters={"company": batch.company,
                "kind": "counterparty", "value": tx.counterparty, "enabled": 1},
                fieldname=["*"], as_dict=True, for_update=True, order_by="name")
            rules = [rule for rule in rules if rule.value == tx.counterparty]
            for rule in rules:
                debit = rule.debit_account or (tx.payment_account if rule.direction == "income" else None)
                credit = rule.credit_account or (tx.payment_account if rule.direction == "expense" else None)
                if (rule.direction, rule.transaction_type, debit, credit) != (
                        tx.direction, transaction_type, debit_account, credit_account):
                    frappe.throw("交易对象精确规则冲突，请先核对已有规则")
            if not rules:
                frappe.get_doc({"doctype": "Classification Rule", "company": batch.company,
                    "kind": "counterparty", "value": tx.counterparty, "direction": tx.direction,
                    "transaction_type": transaction_type,
                    "debit_account": debit_account if tx.direction == "expense" else None,
                    "credit_account": credit_account if tx.direction == "income" else None,
                    "is_learned": 1, "enabled": 1}).insert()
            current = classify(tx_name)
            if existing:
                if (doc.classification_rule, doc.classification_reason) != (current.rule_name, current.reason):
                    if doc.status != "Draft":
                        frappe.throw("已有草稿已审核或驳回，不可更换分类规则")
                    doc.classification_rule = current.rule_name
                    doc.classification_reason = current.reason
                    doc.save()
                    changed = True
                else:
                    doc.validate()
            else:
                doc = frappe.get_doc("Voucher Draft", create_draft(tx_name, current))
            if changed:
                from internal_ledger.api.review_api import update_draft

                doc = update_draft(doc.name, [row.as_dict() for row in doc.lines], summary, str(doc.posting_date))
        else:
            doc = frappe.get_doc({"doctype": "Voucher Draft", "imported_transaction": tx_name,
                "company": batch.company, "base_currency": batch.base_currency,
                "posting_date": getdate(tx.occurred_at), "transaction_type": transaction_type,
                "summary": summary, "status": "Draft", "classification_reason": "人工分类",
                "is_manual_override": 1, "manual_override_by": frappe.session.user,
                "lines": [asdict(line) for line in lines]})
            doc.flags.manual_classification = _MANUAL_CLASSIFICATION
            doc.insert()
        if changed:
            record_audit("classify_manually", "Voucher Draft", doc.name, "success")
    except frappe.QueryDeadlockError:
        raise
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        raise
    frappe.db.release_savepoint(savepoint)
    return doc