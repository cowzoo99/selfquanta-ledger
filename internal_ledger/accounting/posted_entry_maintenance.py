from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Literal
import unicodedata
from uuid import uuid4

import frappe
from frappe.utils import get_datetime, getdate

from internal_ledger.accounting.drafts import validate_balanced, validate_transaction_accounts
from internal_ledger.accounting.posting import post_draft, validate_period, validate_unfrozen_accounts
from internal_ledger.accounting.posting_maintenance import _plain_text, _remove_formal_posting
from internal_ledger.api.review_api import approve_draft
from internal_ledger.importing.dedupe import build_dedupe_key
from internal_ledger.importing.models import NormalizedTransaction

from internal_ledger.internal_ledger.doctype.ledger_evidence.ledger_evidence import clean_note, clean_title
from internal_ledger.permissions import (
    _EVIDENCE_FILE_WRITE, _EVIDENCE_WRITE, _IMPORT_WRITE, _POSTED_ENTRY_REBUILD, _SOURCE_DELETE,
    _physical_file_path, document_company, record_audit, record_deleted_audit, require_ledger_admin,
)


def posted_entry_version(tx, draft, link) -> str:
    values = {
        "transaction": tx.modified,
        "draft": draft.modified,
        "posting": link.modified,
        "journal": link.journal_entry,
        "lines": [(row.name, row.account, str(row.debit or 0), str(row.credit or 0))
                  for row in draft.lines],
    }
    # Frappe v16's serializer sorts keys internally and has no sort_keys argument.
    return sha256(frappe.as_json(values).encode()).hexdigest()


def get_posted_entry(transaction: str, *, for_update: bool = False) -> dict:
    tx, draft, link = _posted_documents(transaction, for_update=for_update)
    return serialize_posted_entry(tx, draft, link, posted_entry_version(tx, draft, link))


def _posted_documents(transaction, *, for_update):
    tx = frappe.get_doc("Imported Transaction", transaction, for_update=for_update)
    tx.check_permission("read")
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not company or document_company(tx) != company:
        frappe.throw("流水不属于当前账套", frappe.PermissionError)
    draft_name = frappe.db.get_value(
        "Voucher Draft", {"imported_transaction": tx.name}, "name", for_update=for_update
    )
    if not draft_name:
        frappe.throw("该流水尚未生成凭证")
    draft = frappe.get_doc("Voucher Draft", draft_name, for_update=for_update)
    if draft.company != company:
        frappe.throw("凭证不属于当前账套", frappe.PermissionError)
    draft.check_permission("read")
    link_name = frappe.db.get_value(
        "Posting Link", {"voucher_draft": draft.name}, "name", for_update=for_update
    )
    if draft.status != "Posted" or not link_name:
        frappe.throw("只能维护已过账账目")
    link = frappe.get_doc("Posting Link", link_name, for_update=for_update)
    link.check_permission("read")
    if link.status != "Posted" or link.reversal_journal_entry:
        frappe.throw("已冲销凭证不能编辑或删除")
    return tx, draft, link


def serialize_posted_entry(tx, draft, link, version) -> dict:
    lines = []
    for row in draft.lines:
        account = frappe.db.get_value("Account", {"name": row.account, "company": draft.company},
                                      ["account_name", "account_number"], as_dict=True)
        if not account:
            frappe.throw("科目不属于当前账套", frappe.PermissionError)
        lines.append({"name": row.name, "account": row.account, "debit": row.debit, "credit": row.credit,
                      "account_name": account.account_name, "account_number": account.account_number})
    evidence = frappe.db.get_value("Ledger Evidence", {
        "imported_transaction": tx.name, "company": draft.company,
    }, ["name", "display_title", "note_html"], as_dict=True) or frappe._dict()
    return {
        "transaction": {field: tx.get(field) for field in (
            "name", "source", "occurred_at", "amount", "direction", "counterparty", "summary", "payment_account")},
        "draft": {**{field: draft.get(field) for field in (
            "name", "company", "base_currency", "posting_date", "summary", "transaction_type", "status")},
            "lines": lines},
        "posting": {"name": link.name, "journal_entry": link.journal_entry, "status": link.status},
        "evidence": {"name": evidence.name, "display_title": clean_title(evidence.display_title),
                     "note_html": clean_note(evidence.note_html)},
        "version": version,
    }


@dataclass(frozen=True)
class NormalizedPostedLine:
    account: str
    debit: Decimal
    credit: Decimal


@dataclass(frozen=True)
class NormalizedPostedEntry:
    occurred_at: datetime
    posting_date: date
    counterparty: str
    summary: str
    direction: Literal["income", "expense"]
    transaction_type: str
    amount: Decimal
    payment_account: str
    lines: tuple[NormalizedPostedLine, ...]


def parse_lines(values):
    if not isinstance(values, list):
        frappe.throw("分录格式无效")
    lines = []
    for row in values:
        if (not isinstance(row, dict) or set(row) - {"account", "debit", "credit"}
                or not isinstance(row.get("account"), str) or not row["account"]):
            frappe.throw("分录格式无效")
        try:
            lines.append(NormalizedPostedLine(
                row["account"], Decimal(str(row.get("debit", 0))), Decimal(str(row.get("credit", 0)))))
        except (InvalidOperation, ValueError, TypeError):
            frappe.throw("分录金额无效")
    return lines


def validate_transaction_accounts_for_all(lines, payment, kind, direction, company):
    debit = next(line.account for line in lines if line.debit)
    credit = next(line.account for line in lines if line.credit)
    for line in lines:
        validate_transaction_accounts(line.account if line.debit else debit,
            line.account if line.credit else credit, payment, kind, direction, company)


def _validated_values(values, tx, company):
    required = {"occurred_at", "posting_date", "counterparty", "summary", "direction",
                "transaction_type", "amount", "payment_account", "lines"}
    if not isinstance(values, dict) or set(values) != required:
        frappe.throw("账目编辑字段不完整")
    if any(not isinstance(values[field], str) for field in (
            "occurred_at", "posting_date", "counterparty", "summary", "direction",
            "transaction_type", "payment_account")):
        frappe.throw("账目编辑字段格式无效")
    if not values["occurred_at"].strip() or not values["posting_date"].strip():
        frappe.throw("账目日期不能为空")
    try:
        amount = Decimal(str(values["amount"]))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("金额无效")
    if not amount.is_finite() or amount <= 0:
        frappe.throw("金额必须大于零")
    if values["direction"] not in {"income", "expense"}:
        frappe.throw("收支方向无效")
    lines = parse_lines(values["lines"])
    validate_balanced(lines)
    if sum((line.debit for line in lines), Decimal("0")) != amount:
        frappe.throw("借贷金额必须等于流水金额")
    try:
        occurred_at = get_datetime(values["occurred_at"])
        posting_date = getdate(values["posting_date"])
    except (ValueError, TypeError, OverflowError):
        frappe.throw("账目日期无效")
    # Keep posting's Company -> Account lock order.
    validate_period(company, posting_date)
    validate_transaction_accounts_for_all(lines, values["payment_account"],
                                          values["transaction_type"].strip(), values["direction"], company)
    validate_unfrozen_accounts(lines)
    return NormalizedPostedEntry(
        occurred_at=occurred_at, posting_date=posting_date,
        counterparty=values["counterparty"].strip(), summary=values["summary"].strip(),
        direction=values["direction"], transaction_type=values["transaction_type"].strip(),
        amount=amount, payment_account=values["payment_account"], lines=tuple(lines),
    )


def _normalized_transaction(tx, parsed):
    return NormalizedTransaction(
        source=tx.source, source_id=tx.source_id, occurred_at=parsed.occurred_at,
        amount=parsed.amount, direction=parsed.direction, counterparty=parsed.counterparty,
        summary=parsed.summary, payment_account=parsed.payment_account, raw_row=tx.raw_row,
    )


def _assert_unique_dedupe_key(tx, parsed):
    key = build_dedupe_key(_normalized_transaction(tx, parsed))
    if frappe.db.get_value("Imported Transaction",
            {"dedupe_key": key, "name": ["!=", tx.name]}, "name", for_update=True):
        frappe.throw("修改后的流水与已有流水重复")
    return key


def _update_imported_transaction(tx, parsed):
    tx.flags.ledger_import_write = _IMPORT_WRITE
    tx.dedupe_key = _assert_unique_dedupe_key(tx, parsed)
    for field in ("occurred_at", "counterparty", "summary", "direction", "amount", "payment_account"):
        tx.set(field, getattr(parsed, field))
    tx.status = "Imported"
    for field in ("duplicate_of", "candidate_dedupe_key", "duplicate_review_status", "duplicate_match_reason",
                  "duplicate_reviewed_by", "duplicate_reviewed_at", "duplicate_review_note"):
        tx.set(field, None)
    tx.save()


def _replace_draft_snapshot(draft, parsed):
    draft.reload()
    draft.flags.posted_entry_rebuild = _POSTED_ENTRY_REBUILD
    draft.update({
        "status": "Draft", "posting_date": parsed.posting_date, "summary": parsed.summary,
        "transaction_type": parsed.transaction_type, "is_manual_override": 1,
        "manual_override_by": frappe.session.user, "classification_rule": None,
        "classification_reason": "人工分类", "rejection_reason": None,
        "lines": [asdict(line) for line in parsed.lines],
    })
    draft.save()


def _existing_evidence_remark(transaction, company):
    evidence = frappe.db.get_value("Ledger Evidence",
        {"imported_transaction": transaction, "company": company},
        ["display_title", "note_html"], as_dict=True, for_update=True)
    if not evidence:
        return None
    return "；".join(part for part in (
        clean_title(evidence.display_title), _plain_text(clean_note(evidence.note_html))) if part) or None


def _locked_documents(transaction):
    tx, draft, link = _posted_documents(transaction, for_update=True)
    tx.check_permission("write")
    draft.check_permission("write")
    link.check_permission("write")
    return tx, draft, link


def rebuild_posted_entry(transaction: str, values: dict, expected_version: str) -> dict:
    require_ledger_admin()
    point = "posted_rebuild_" + uuid4().hex
    frappe.db.savepoint(point)
    with ExitStack() as undo_files:
        try:
            tx, draft, link = _locked_documents(transaction)
            if posted_entry_version(tx, draft, link) != expected_version:
                frappe.throw("账目已被其他人修改，请刷新后重试")
            parsed = _validated_values(values, tx, draft.company)
            _assert_unique_dedupe_key(tx, parsed)
            remark = _existing_evidence_remark(tx.name, draft.company)
            for doctype, name in (("Journal Entry", link.journal_entry), ("Posting Link", link.name)):
                _preserve_attached_files(doctype, name, undo_files)
            old_journal = _remove_formal_posting(link, draft, resulting_status="Draft")
            _update_imported_transaction(tx, parsed)
            _replace_draft_snapshot(draft, parsed)
            approve_draft(draft.name)
            new_journal = post_draft(draft.name, remark_override=remark)
            record_audit("rebuild_posted_entry", "Voucher Draft", draft.name,
                         f"old:{old_journal};new:{new_journal}")
            result = get_posted_entry(tx.name)
        except frappe.QueryDeadlockError:
            raise
        except Exception:
            frappe.db.rollback(save_point=point)
            frappe.db.release_savepoint(point)
            raise
        frappe.db.release_savepoint(point)
        undo_files.pop_all()
    return result


def _preserve_attached_files(doctype, name, undo_files):
    names = frappe.db.get_values("File", {"attached_to_doctype": doctype, "attached_to_name": name},
        "name", order_by="name", for_update=True, pluck=True)
    for file_name in names:
        attachment = frappe.get_doc("File", file_name, for_update=True)
        originals = {}
        for field in ("file_url", "thumbnail_url"):
            path = _physical_file_path(attachment.get(field), attachment.is_private, deletion=True)
            if path and path.exists():
                originals[path] = path.read_bytes()

        def restore_blob(originals=originals, file_name=file_name):
            if frappe.db.exists("File", file_name):
                for path, content in originals.items():
                    if not path.exists():
                        path.write_bytes(content)

        # File rows and metadata use the database rollback; disk bytes need compensation.
        undo_files.callback(restore_blob)
        frappe.db.after_rollback.add(restore_blob)
    return names


def _delete_attached_files(doctype, name, marker, undo_files):
    for file_name in _preserve_attached_files(doctype, name, undo_files):
        frappe.delete_doc("File", file_name, ignore_permissions=True, delete_permanently=True,
                          flags={marker[0]: marker[1]})


def _delete_evidence_and_files(transaction, company, undo_files):
    names = frappe.db.get_values("Ledger Evidence", {"imported_transaction": transaction},
        "name", order_by="name", for_update=True, pluck=True)
    for name in names:
        evidence = frappe.get_doc("Ledger Evidence", name, for_update=True)
        if evidence.company != company:
            frappe.throw("佐证不属于当前账套", frappe.PermissionError)
        _delete_attached_files("Ledger Evidence", name, ("evidence_file_write", _EVIDENCE_FILE_WRITE), undo_files)
        frappe.delete_doc("Ledger Evidence", name, ignore_permissions=True, delete_permanently=True,
                          flags={"ledger_evidence_write": _EVIDENCE_WRITE})


def _release_duplicate_candidates(transaction, company):
    names = frappe.db.get_values("Imported Transaction", {"duplicate_of": transaction},
        "name", order_by="name", for_update=True, pluck=True)
    for name in names:
        candidate = frappe.get_doc("Imported Transaction", name, for_update=True)
        if document_company(candidate) != company:
            frappe.throw("重复流水不属于当前账套", frappe.PermissionError)
        candidate.flags.ledger_import_write = _IMPORT_WRITE
        candidate.status = "Imported"
        candidate.dedupe_key = "released:" + name
        for field in ("duplicate_of", "candidate_dedupe_key", "duplicate_review_status", "duplicate_match_reason",
                      "duplicate_reviewed_by", "duplicate_reviewed_at", "duplicate_review_note"):
            candidate.set(field, None)
        candidate.save()


def _delete_empty_batch_source(batch, undo_files):
    names = frappe.db.get_values("Internal Ledger Source File", {"import_batch": batch.name},
        "name", order_by="name", for_update=True, pluck=True)
    if batch.raw_file and batch.raw_file not in names:
        frappe.throw("原始文件不属于当前导入批次", frappe.PermissionError)
    for name in names:
        _delete_attached_files("Internal Ledger Source File", name, ("source_delete", _SOURCE_DELETE), undo_files)
        # The batch and carrier deliberately link to one another.
        frappe.delete_doc("Internal Ledger Source File", name, force=True, ignore_permissions=True,
                          delete_permanently=True, flags={"source_delete": _SOURCE_DELETE})
    _preserve_attached_files("Import Batch", batch.name, undo_files)
    frappe.delete_doc("Import Batch", batch.name, force=True, ignore_permissions=True, delete_permanently=True,
                      flags={"ledger_import_write": _IMPORT_WRITE, "source_delete": _SOURCE_DELETE})


def _deletion_audit_result(reason):
    if not isinstance(reason, str) or not reason.strip():
        frappe.throw("永久删除必须填写原因")
    reason = unicodedata.normalize("NFKC", reason)
    reason = "".join(char if not unicodedata.category(char).startswith("C") else " " if char.isspace() else ""
                     for char in reason)
    reason = _plain_text(clean_note(reason))
    if not reason:
        frappe.throw("永久删除必须填写原因")
    # Mask IBAN-like values and long digit runs, including grouped card/account numbers.
    reason = re.sub(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}(?![A-Za-z0-9])|\d(?:[\W_]*\d){5,}",
                    "[已隐去]", reason, flags=re.IGNORECASE)
    return ("success;reason:" + reason)[:140]


def _lock_import_batches(transactions):
    batches = frappe.db.get_values("Imported Transaction", {"name": ["in", transactions]}, "import_batch", pluck=True)
    for name in sorted(set(batches)):
        # Shared source cleanup takes this lock before any selected transaction lock.
        frappe.db.get_value("Import Batch", name, "name", for_update=True)


def permanently_delete_transaction(transaction: str, reason: str, expected_version: str) -> dict:
    require_ledger_admin()
    audit_result = _deletion_audit_result(reason)
    point = "posted_delete_" + uuid4().hex
    frappe.db.savepoint(point)
    with ExitStack() as undo_files:
        try:
            _lock_import_batches([transaction])
            tx, draft, link = _locked_documents(transaction)
            if posted_entry_version(tx, draft, link) != expected_version:
                frappe.throw("账目已被其他人修改，请刷新后重试")
            batch = frappe.get_doc("Import Batch", tx.import_batch, for_update=True)
            if batch.company != draft.company:
                frappe.throw("导入批次不属于当前账套", frappe.PermissionError)
            # Native document deletion cascades into ordinary File rows immediately.
            for doctype, name in (("Journal Entry", link.journal_entry), ("Posting Link", link.name),
                                  ("Voucher Draft", draft.name), ("Imported Transaction", tx.name)):
                _preserve_attached_files(doctype, name, undo_files)
            _remove_formal_posting(link, draft, delete_permanently=True)
            _delete_evidence_and_files(tx.name, batch.company, undo_files)
            frappe.delete_doc("Voucher Draft", draft.name, ignore_permissions=True, delete_permanently=True)
            _release_duplicate_candidates(tx.name, batch.company)
            frappe.delete_doc("Imported Transaction", tx.name, ignore_permissions=True, delete_permanently=True,
                              flags={"ledger_import_write": _IMPORT_WRITE})
            # Use a locking current read after waiting for the batch lock.
            if not frappe.db.get_value("Imported Transaction", {"import_batch": batch.name},
                                       "name", for_update=True):
                _delete_empty_batch_source(batch, undo_files)
            record_deleted_audit("permanently_delete_transaction",
                frappe._dict(doctype="Imported Transaction", name=tx.name), batch.company, audit_result)
        except frappe.QueryDeadlockError:
            raise
        except Exception:
            frappe.db.rollback(save_point=point)
            frappe.db.release_savepoint(point)
            raise
        frappe.db.release_savepoint(point)
        undo_files.pop_all()
    return {"transaction": tx.name, "status": "deleted"}


def permanently_delete_transactions(transactions: list[str], reason: str, expected_versions: dict[str, str]) -> list[dict]:
    require_ledger_admin()
    if not isinstance(transactions, list) or not transactions:
        frappe.throw("请选择要永久删除的账目")
    if any(not isinstance(name, str) or not name.strip() for name in transactions):
        frappe.throw("流水参数无效")
    if not isinstance(expected_versions, dict):
        frappe.throw("账目版本参数无效")
    _lock_import_batches(transactions)
    results = []
    for name in dict.fromkeys(transactions):
        try:
            results.append(permanently_delete_transaction(name, reason, expected_versions.get(name)))
        except frappe.QueryDeadlockError:
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, frappe.ValidationError) else "永久删除失败，请刷新后重试"
            results.append({"transaction": name, "status": "failed", "message": message})
    return results
