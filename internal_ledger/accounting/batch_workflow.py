from uuid import uuid4

import frappe

from internal_ledger.accounting.classifier import classify
from internal_ledger.accounting.drafts import create_draft
from internal_ledger.permissions import require_ledger_admin, record_audit


def prepare_matched_transactions(tx_names: list[str]) -> list[dict]:
    require_ledger_admin()
    frappe.has_permission("Voucher Draft", ptype="create", throw=True)
    frappe.has_permission("Voucher Draft", ptype="write", throw=True)
    if not isinstance(tx_names, list):
        frappe.throw("流水必须为编号列表")
    outcomes = []
    for name in tx_names:
        point = "ledger_prepare_" + uuid4().hex
        frappe.db.savepoint(point)
        reference = None
        outcome_name = name if isinstance(name, str) else ""
        message_count = len(frappe.local.message_log or [])
        try:
            if not isinstance(name, str) or not name.strip():
                frappe.throw("流水编号无效")
            tx = frappe.get_doc("Imported Transaction", name, for_update=True)
            tx.check_permission("read")
            tx.check_permission("write")
            batch = frappe.get_doc("Import Batch", tx.import_batch, for_update=True)
            batch.check_permission("read")
            batch.check_permission("write")
            reference = tx.name
            draft = None
            status = "skipped"
            if tx.status != "Imported" or tx.duplicate_review_status in {"Pending", "Confirmed"}:
                message = "重复或非待处理流水，请先核对重复记录"
            else:
                draft = frappe.db.get_value("Voucher Draft", {"imported_transaction": name}, "name", for_update=True)
                if draft:
                    existing = frappe.get_doc("Voucher Draft", draft)
                    existing.check_permission("read")
                    existing.check_permission("write")
                    message = "已有凭证草稿，无需重复生成"
                else:
                    result = classify(name)
                    if result.status == "matched":
                        draft = create_draft(name, result)
                        status, message = "created", "已生成待审核凭证草稿"
                    elif result.status == "conflict":
                        message = "分类规则冲突，请核对规则或人工分类"
                    else:
                        message = "未匹配规则，请人工分类"
            outcomes.append({"name": outcome_name, "status": status, "draft": draft, "message": message})
        except frappe.QueryDeadlockError:
            # MariaDB has rolled back the entire transaction; its savepoints are invalid.
            raise
        except Exception:
            frappe.db.rollback(save_point=point)
            frappe.local.message_log = (frappe.local.message_log or [])[:message_count]
            # Store only the action/result, never exception text or request data.
            record_audit("prepare_draft", "Imported Transaction" if reference else "Internal Ledger Settings",
                         reference or "Internal Ledger Settings", "failed")
            outcomes.append({"name": outcome_name, "status": "failed", "draft": None,
                             "message": "生成失败，请检查流水权限、科目及会计期间后重试"})
        frappe.db.release_savepoint(point)
    return outcomes
