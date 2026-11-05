from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from unicodedata import normalize
from uuid import uuid4


@dataclass(frozen=True)
class NormalizedTransaction:
    source: str
    source_id: str | None
    occurred_at: datetime
    amount: Decimal
    direction: Literal["income", "expense"]
    counterparty: str
    summary: str
    payment_account: str
    raw_row: int


def _match_text(value: str) -> str:
    return " ".join(normalize("NFKC", value or "").split()).casefold()


def _similar_payment(frappe, company: str, row: NormalizedTransaction) -> str | None:
    candidates = frappe.db.sql(
        """
        SELECT tx.name, tx.counterparty
        FROM `tabImported Transaction` tx
        INNER JOIN `tabImport Batch` batch ON batch.name = tx.import_batch
        WHERE batch.company = %s
          AND tx.status = 'Imported'
          AND DATE(tx.occurred_at) = %s
          AND tx.direction = %s
          AND tx.amount = %s
          AND tx.payment_account = %s
        ORDER BY tx.creation, tx.name
        FOR UPDATE
        """,
        (company, row.occurred_at.date(), row.direction, row.amount, row.payment_account),
        as_dict=True,
    )
    counterparty = _match_text(row.counterparty)
    return next((candidate.name for candidate in candidates
                 if _match_text(candidate.counterparty) == counterparty), None)


def save_batch(source: str, file_hash: str, rows: list[NormalizedTransaction]) -> str:
    """Save all rows atomically; the caller owns the surrounding transaction."""
    import frappe

    from internal_ledger.importing.dedupe import build_dedupe_key
    from internal_ledger.permissions import require_ledger_admin, record_audit, _IMPORT_WRITE

    require_ledger_admin()

    if any(row.source != source for row in rows):
        frappe.throw("Every row must match the batch source.")
    settings = frappe.get_single("Internal Ledger Settings")
    if not settings.company or not settings.base_currency:
        frappe.throw("Configure the ledger company and base currency before importing.")

    batch_point = "ledger_batch_" + uuid4().hex
    row_point = "ledger_row_" + uuid4().hex
    frappe.db.savepoint(batch_point)
    try:
        batch = frappe.get_doc({
            "doctype": "Import Batch", "source": source, "file_hash": file_hash,
            "company": settings.company, "base_currency": settings.base_currency,
        })
        batch.flags.ledger_import_write = _IMPORT_WRITE
        batch.insert()
        for row in rows:
            values = {
                "doctype": "Imported Transaction", **asdict(row), "import_batch": batch.name,
                "status": "Imported", "dedupe_key": build_dedupe_key(row), "duplicate_review_status": "",
            }
            frappe.db.savepoint(row_point)
            try:
                canonical = frappe.db.get_value(
                    "Imported Transaction", {"dedupe_key": values["dedupe_key"]}, "name", for_update=True,
                )
                reason = "来源流水号相同" if canonical and row.source_id else (
                    "交易字段指纹相同" if canonical else "")
                if not canonical:
                    canonical = _similar_payment(frappe, settings.company, row)
                    if canonical:
                        reason = "同日、同方向、同资金账户、同交易对象且同金额（标题或备注可不同）"
                if canonical:
                    values.update(status="Duplicate", dedupe_key=None, duplicate_of=canonical,
                                  duplicate_review_status="Pending", candidate_dedupe_key=values["dedupe_key"],
                                  duplicate_match_reason=reason)
                doc = frappe.get_doc(values)
                doc.flags.ledger_import_write = _IMPORT_WRITE
                doc.insert()
            except frappe.UniqueValidationError:
                frappe.db.rollback(save_point=row_point)
                canonical = frappe.db.get_value(
                    "Imported Transaction", {"dedupe_key": values["dedupe_key"]}, "name", for_update=True,
                )
                if not canonical:
                    raise
                values.update(status="Duplicate", dedupe_key=None, duplicate_of=canonical,
                              duplicate_review_status="Pending", candidate_dedupe_key=values["dedupe_key"],
                              duplicate_match_reason="来源流水号相同" if row.source_id else "交易字段指纹相同")
                doc = frappe.get_doc(values)
                doc.flags.ledger_import_write = _IMPORT_WRITE
                doc.insert()
            frappe.db.release_savepoint(row_point)
        record_audit("confirm_import", "Import Batch", batch.name, "success")
    except frappe.QueryDeadlockError:
        # InnoDB already rolled back the transaction and invalidated its savepoints.
        raise
    except Exception:
        frappe.db.rollback(save_point=batch_point)
        frappe.db.release_savepoint(batch_point)
        raise
    frappe.db.release_savepoint(batch_point)
    return batch.name
