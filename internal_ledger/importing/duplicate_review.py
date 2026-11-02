from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import frappe
from frappe.utils import now_datetime

from internal_ledger.permissions import require_ledger_admin, record_audit, _IMPORT_WRITE

if TYPE_CHECKING:
    from internal_ledger.internal_ledger.doctype.imported_transaction.imported_transaction import ImportedTransaction


def resolve(name: str, decision: Literal["confirm", "release"], actor: str, note: str) -> "ImportedTransaction":
    require_ledger_admin()
    if actor != frappe.session.user:
        frappe.throw("处理人必须为当前用户", frappe.PermissionError)
    if decision not in ("confirm", "release"):
        frappe.throw("重复处理决定无效")
    if note is not None and not isinstance(note, str):
        frappe.throw("重复处理说明必须为文本")
    tx = frappe.get_doc("Imported Transaction", name, for_update=True)
    tx.check_permission("read")
    tx.check_permission("write")
    if tx.status != "Duplicate" or tx.duplicate_review_status != "Pending":
        frappe.throw("该重复候选已经处理")
    original = frappe.get_doc("Imported Transaction", tx.duplicate_of)
    original.check_permission("read")
    savepoint = "ledger_duplicate_" + uuid4().hex
    frappe.db.savepoint(savepoint)
    try:
        tx.duplicate_reviewed_by = actor
        tx.duplicate_reviewed_at = now_datetime()
        tx.duplicate_review_note = (note or "").strip()
        if decision == "confirm":
            tx.duplicate_review_status = "Confirmed"
        else:
            tx.status = "Imported"
            tx.duplicate_review_status = "Released"
            tx.dedupe_key = f"released:{tx.name}"
        tx.flags.ledger_import_write = _IMPORT_WRITE
        tx.save()
        record_audit(f"duplicate_{decision}", "Imported Transaction", tx.name, "success")
    except frappe.QueryDeadlockError:
        raise
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        raise
    frappe.db.release_savepoint(savepoint)
    return tx


def release_duplicate(name: str, actor: str, note: str) -> "ImportedTransaction":
    return resolve(name, "release", actor, note)
