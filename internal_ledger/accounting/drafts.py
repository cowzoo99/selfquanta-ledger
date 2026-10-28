from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
from typing import Sequence, TYPE_CHECKING

import frappe
from frappe.utils import getdate
from internal_ledger.permissions import require_ledger_admin, record_audit

if TYPE_CHECKING:
    from internal_ledger.accounting.classifier import ClassificationResult


@dataclass(frozen=True)
class DraftLine:
    account: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")


def validate_balanced(lines: Sequence[DraftLine]) -> None:
    if len(lines) < 2:
        frappe.throw("草稿至少需要两条分录")
    amounts = []
    for line in lines:
        if not line.account or any(not isinstance(value, Decimal) or not value.is_finite() or value < 0
                                   for value in (line.debit, line.credit)):
            frappe.throw("分录科目及金额无效")
        if (line.debit > 0) == (line.credit > 0):
            frappe.throw("每条分录必须且只能有一方为正金额")
        amounts.extend((line.debit, line.credit))
    with localcontext() as context:
        context.prec = max(28, max(value.adjusted() for value in amounts)
                           - min(value.as_tuple().exponent for value in amounts)
                           + len(str(len(lines))) + 2)
        if sum((line.debit for line in lines), Decimal("0")) != sum(
            (line.credit for line in lines), Decimal("0")
        ):
            frappe.throw("借贷不平")
    if any(value > Decimal("999999999999.999999999") or value != value.quantize(Decimal("0.000000001"))
           for value in amounts):
        frappe.throw("分录金额超过存储精度，不得舍入")


def validate_account(name: str, company: str, roots: set[str]) -> None:
    account = frappe.db.get_value("Account", name, ["company", "is_group", "disabled", "root_type"],
                                  as_dict=True, for_update=True)
    if not account or account.company != company or account.is_group or account.disabled or account.root_type not in roots:
        frappe.throw("科目必须为同公司、启用、非组且根类型合法的 Account 链接")


def validate_transaction_accounts(debit, credit, payment, kind, direction, company):
    if direction not in {"income", "expense"}:
        frappe.throw("流水收支方向无效")
    validate_account(payment, company, {"Asset"})
    if (debit if direction == "income" else credit) != payment or debit == credit:
        frappe.throw("借贷科目必须包含流水资金账户，且不能相同")
    if kind in {"expense", "fee"} and direction == "expense":
        roots = {"Expense"}
    elif kind == "income" and direction == "income":
        roots = {"Income"}
    elif kind == "transfer":
        roots = {"Asset"}
    elif kind == "refund":
        roots = {"Expense"} if direction == "income" else {"Income"}
    else:
        frappe.throw("业务类型与流水收支方向不一致")
    validate_account(credit if direction == "income" else debit, company, roots)


def expense_lines(amount, expense, payment):
    return [DraftLine(expense, debit=amount), DraftLine(payment, credit=amount)]


def income_lines(amount, payment, income):
    return [DraftLine(payment, debit=amount), DraftLine(income, credit=amount)]


def transfer_lines(amount, destination, source):
    return [DraftLine(destination, debit=amount), DraftLine(source, credit=amount)]


def refund_lines(amount, debit, credit):
    return [DraftLine(debit, debit=amount), DraftLine(credit, credit=amount)]


def fee_lines(amount, fee, payment):
    return [DraftLine(fee, debit=amount), DraftLine(payment, credit=amount)]


def build_lines(amount: Decimal, result: "ClassificationResult", kind: str) -> list[DraftLine]:
    """Generate and validate the complete lines without database reads or writes."""
    builders = {"expense": expense_lines, "income": income_lines, "transfer": transfer_lines,
                "refund": refund_lines, "fee": fee_lines}
    lines = builders[kind](amount, result.debit_account, result.credit_account)
    validate_balanced(lines)
    return lines


def current_draft(tx_name: str):
    """Keep current classification reads locked in the caller's transaction."""
    from internal_ledger.accounting.classifier import classify

    result = classify(tx_name)
    if result.status != "matched":
        frappe.throw("仅当前匹配且无冲突的分类结果可以生成草稿")
    tx = frappe.get_doc("Imported Transaction", tx_name, for_update=True)
    batch = frappe.get_doc("Import Batch", tx.import_batch, for_update=True)
    kind = frappe.db.get_value("Classification Rule", result.rule_name, "transaction_type", for_update=True)
    lines = build_lines(Decimal(str(tx.amount)), result, kind)
    return result, {
        "doctype": "Voucher Draft", "imported_transaction": tx_name,
        "classification_rule": result.rule_name, "classification_reason": result.reason,
        "company": batch.company, "base_currency": batch.base_currency,
        "posting_date": getdate(tx.occurred_at), "transaction_type": kind, "status": "Draft",
        "lines": [asdict(line) for line in lines],
    }


def create_draft(tx_name: str, result: "ClassificationResult") -> str:
    require_ledger_admin()
    frappe.get_doc("Imported Transaction", tx_name).check_permission("read")
    current, values = current_draft(tx_name)
    if result != current:
        frappe.throw("仅当前匹配且无冲突的分类结果可以生成草稿")
    # The unique imported_transaction index rejects both retries and races.
    # Do not catch unique errors: unrelated constraint failures must propagate too.
    name = frappe.get_doc(values).insert().name
    record_audit("prepare_draft", "Voucher Draft", name, "success")
    return name
