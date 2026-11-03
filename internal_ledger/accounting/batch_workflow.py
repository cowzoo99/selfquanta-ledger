from uuid import uuid4

import frappe

from internal_ledger.accounting.classifier import classify
from internal_ledger.accounting.drafts import create_draft
from internal_ledger.permissions import require_ledger_admin, record_audit


PROCESSING_STATUSES = ("created", "manual", "conflict", "duplicate", "skipped", "failed")


def reconcile_unprepared_transactions() -> dict:
    require_ledger_admin()
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not company:
        frappe.throw("请先设置当前账套公司")
    batches = frappe.get_all("Import Batch", filters={"company": company}, pluck="name")
    rows = frappe.get_all("Imported Transaction", filters={"status": "Imported", "import_batch": ["in", batches]},
                          fields=["name", "duplicate_review_status"], order_by="creation") if batches else []
    drafted = set(frappe.get_all("Voucher Draft", filters={"imported_transaction": ["in", [row.name for row in rows]]},
                                pluck="imported_transaction")) if rows else set()
    names = [row.name for row in rows if row.name not in drafted
             and row.duplicate_review_status not in {"Pending", "Confirmed"}]
    outcomes = process_transactions(names)
    summary = {status: 0 for status in PROCESSING_STATUSES}
    for row in outcomes:
        summary[row["status"]] += 1
    run_id = uuid4().hex
    for outcome in outcomes:
        if outcome["status"] == "failed":
            record_audit("auto_classification_failure", "Imported Transaction", outcome["name"], run_id)
    result = ";".join([run_id] + [f"{key}:{summary[key]}" for key in PROCESSING_STATUSES])
    record_audit("auto_classification_sweep", "Internal Ledger Settings",
                 "Internal Ledger Settings", result)
    return {"outcomes": outcomes, "summary": summary}


def process_transactions(tx_names: list[str]) -> list[dict]:
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
            if tx.status == "Duplicate" or tx.duplicate_review_status in {"Pending", "Confirmed"}:
                status, message = "duplicate", "疑似或已确认重复，未进入分类制证"
            elif tx.status != "Imported":
                status, message = "skipped", "流水已处理，无需重复生成"
            else:
                draft = frappe.db.get_value("Voucher Draft", {"imported_transaction": name}, "name", for_update=True)
                if draft:
                    existing = frappe.get_doc("Voucher Draft", draft)
                    existing.check_permission("read")
                    existing.check_permission("write")
                    status, message = "skipped", "已有凭证草稿，无需重复生成"
                else:
                    result = classify(name)
                    if result.status == "matched":
                        draft = create_draft(name, result)
                        status, message = "created", "已自动分类并生成待审核凭证草稿"
                    elif result.status == "conflict":
                        status, message = "conflict", "分类规则冲突，请人工确认"
                    else:
                        status, message = "manual", "未匹配分类规则，请人工分类"
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


def prepare_matched_transactions(tx_names: list[str]) -> list[dict]:
    return process_transactions(tx_names)


def approve_and_post(draft_names: list[str]) -> list[dict]:
    from internal_ledger.accounting import posting
    from internal_ledger.api.review_api import approve_draft

    require_ledger_admin()
    frappe.has_permission("Voucher Draft", ptype="read", throw=True)
    frappe.has_permission("Voucher Draft", ptype="write", throw=True)
    if not isinstance(draft_names, list):
        frappe.throw("凭证必须为编号列表")
    outcomes = []
    for name in draft_names:
        point = "ledger_post_" + uuid4().hex
        frappe.db.savepoint(point)
        reference = None
        outcome_name = name if isinstance(name, str) else ""
        message_count = len(frappe.local.message_log or [])
        try:
            if not isinstance(name, str) or not name.strip():
                frappe.throw("凭证编号无效")
            doc = frappe.get_doc("Voucher Draft", name, for_update=True)
            doc.check_permission("read")
            doc.check_permission("write")
            reference = doc.name
            existing = frappe.db.get_value("Posting Link", {"voucher_draft": doc.name}, "name", for_update=True)
            was_posted = doc.status == "Posted"
            if bool(existing) != was_posted:
                frappe.throw("草稿与过账关联状态不一致")
            if doc.status == "Draft":
                approve_draft(doc.name)
            elif doc.status not in {"Approved", "Posted"}:
                frappe.throw("当前状态不可审核过账")
            journal_name = posting.post_draft(doc.name)
            link_name = existing or frappe.db.get_value("Posting Link", {"voucher_draft": doc.name}, "name", for_update=True)
            link = frappe.get_doc("Posting Link", link_name, for_update=True)
            link.check_permission("read")
            journal = frappe.get_doc("Journal Entry", journal_name, for_update=True)
            journal.check_permission("read")
            if link.voucher_draft != doc.name or link.journal_entry != journal.name or journal.docstatus != 1 or journal.company != doc.company:
                frappe.throw("过账关联与正式凭证不一致")
            outcomes.append({"name": outcome_name, "status": "skipped" if was_posted else "created",
                "draft": doc.name, "journal_entry": journal.name, "posting_link": link.name,
                "message": "已过账，返回既有正式凭证" if was_posted else "已审核并过账"})
        except frappe.QueryDeadlockError:
            # MariaDB rolls back the whole transaction and invalidates all savepoints.
            raise
        except Exception:
            frappe.db.rollback(save_point=point)
            frappe.local.message_log = (frappe.local.message_log or [])[:message_count]
            record_audit("approve_and_post", "Voucher Draft" if reference else "Internal Ledger Settings",
                         reference or "Internal Ledger Settings", "failed")
            outcomes.append({"name": outcome_name, "status": "failed", "draft": reference,
                "journal_entry": None, "posting_link": None,
                "message": "审核过账失败，请检查权限、草稿分录、科目及会计期间后重试"})
        frappe.db.release_savepoint(point)
    return outcomes
