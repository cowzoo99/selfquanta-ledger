import frappe

from internal_ledger.permissions import require_ledger_admin, record_audit, _IMPORT_WRITE


def execute():
    """Make retained pre-lifecycle duplicates reviewable without changing accounting fields."""
    require_ledger_admin()
    for row in frappe.get_all("Imported Transaction", filters={"status": "Duplicate"}, fields=["name"]):
        candidate = frappe.get_doc("Imported Transaction", row.name, for_update=True)
        if candidate.duplicate_review_status:
            continue
        candidate.check_permission("read")
        candidate.check_permission("write")
        original = frappe.get_doc("Imported Transaction", candidate.duplicate_of)
        original.check_permission("read")
        candidate.duplicate_review_status = "Pending"
        candidate.candidate_dedupe_key = original.dedupe_key
        candidate.duplicate_match_reason = "来源流水号相同" if candidate.source_id else "交易字段指纹相同"
        candidate.flags.ledger_import_write = _IMPORT_WRITE
        candidate.save()
        record_audit("duplicate_migrate_pending", "Imported Transaction", candidate.name, "success")
