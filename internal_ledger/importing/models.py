from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
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


def save_batch(source: str, file_hash: str, rows: list[NormalizedTransaction]) -> str:
    """Save all rows atomically; the caller owns the surrounding transaction."""
    import frappe

    from internal_ledger.importing.dedupe import build_dedupe_key
    from internal_ledger.permissions import require_ledger_admin, record_audit

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
        }).insert()
        for row in rows:
            values = {
                "doctype": "Imported Transaction", **asdict(row), "import_batch": batch.name,
                "status": "Imported", "dedupe_key": build_dedupe_key(row),
            }
            frappe.db.savepoint(row_point)
            try:
                frappe.get_doc(values).insert()
            except frappe.UniqueValidationError:
                frappe.db.rollback(save_point=row_point)
                canonical = frappe.db.get_value(
                    "Imported Transaction", {"dedupe_key": values["dedupe_key"]}, "name", for_update=True,
                )
                if not canonical:
                    raise
                values.update(status="Duplicate", dedupe_key=None, duplicate_of=canonical)
                frappe.get_doc(values).insert()
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
