from html.parser import HTMLParser

import frappe

from internal_ledger.permissions import (
    _JOURNAL_DELETE, _JOURNAL_WRITE, record_audit, require_ledger_admin,
)


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _plain_text(value):
    parser = _PlainText()
    parser.feed(value or "")
    return " ".join("".join(parser.parts).split())


def sync_posted_summary(transaction: str, display_title: str, note_html: str) -> None:
    """Keep the user-facing title and note identical on the posted voucher and ledger."""
    require_ledger_admin()
    draft_name = frappe.db.get_value(
        "Voucher Draft", {"imported_transaction": transaction}, "name", for_update=True
    )
    if not draft_name:
        return
    draft = frappe.get_doc("Voucher Draft", draft_name, for_update=True)
    if draft.status != "Posted":
        return
    link_name = frappe.db.get_value("Posting Link", {"voucher_draft": draft.name}, "name", for_update=True)
    if not link_name:
        frappe.throw("已过账草稿缺少正式凭证关联")
    link = frappe.get_doc("Posting Link", link_name, for_update=True)
    if link.status != "Posted" or link.reversal_journal_entry:
        frappe.throw("已冲销凭证不能修改标题或备注")
    summary = "；".join(part for part in (display_title.strip(), _plain_text(note_html)) if part)
    if not summary:
        summary = frappe.db.get_value("Imported Transaction", transaction, "summary") or draft.summary or ""
    frappe.db.set_value("Voucher Draft", draft.name, "summary", summary, update_modified=False)
    frappe.db.set_value("Journal Entry", link.journal_entry, "user_remark", summary, update_modified=False)
    frappe.db.set_value("GL Entry", {"voucher_type": "Journal Entry", "voucher_no": link.journal_entry},
                        "remarks", summary, update_modified=False)


def delete_posting(posting_link: str, reason: str) -> dict:
    """Delete one formal posting as a single ledger-admin service operation."""
    require_ledger_admin()
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        frappe.throw("同步删除必须填写原因")
    link = frappe.get_doc("Posting Link", posting_link, for_update=True)
    link.check_permission("write")
    if link.status != "Posted" or link.reversal_journal_entry:
        frappe.throw("只能同步删除尚未冲销的已过账凭证")
    draft = frappe.get_doc("Voucher Draft", link.voucher_draft, for_update=True)
    journal = frappe.get_doc("Journal Entry", link.journal_entry, for_update=True)
    if draft.status != "Posted" or journal.docstatus != 1 or journal.reversal_of:
        frappe.throw("草稿、凭证与过账关联状态不一致")

    journal_name = _remove_formal_posting(link, draft, resulting_status="Rejected")
    frappe.db.set_value("Voucher Draft", draft.name, {
        "rejection_reason": f"已同步删除：{reason}",
    }, update_modified=False)
    record_audit("delete_posting", "Voucher Draft", draft.name, "success")
    return {"posting_link": link.name, "journal_entry": journal_name,
            "draft": draft.name, "status": "deleted"}


def _remove_formal_posting(link, draft, *, resulting_status: str | None = None,
                           delete_permanently: bool = False) -> str:
    require_ledger_admin()
    journal = frappe.get_doc("Journal Entry", link.journal_entry, for_update=True)
    if journal.company != draft.company:
        frappe.throw("凭证不属于当前账套", frappe.PermissionError)
    if journal.docstatus != 1 or journal.reversal_of:
        frappe.throw("正式凭证状态不允许重建")
    series = None
    if resulting_status == "Draft":
        from frappe.model.naming import NamingSeries, parse_naming_series

        def capture_counter(prefix, digits):
            nonlocal series
            # Resolve date placeholders from the old name, not today's date.
            key = journal.name[:len(prefix)]
            series = (key, frappe.db.get_value("Series", key, "current", order_by="name", for_update=True))
            return "#" * digits

        parse_naming_series(NamingSeries(journal.naming_series).series,
                            doc=journal, number_generator=capture_counter)
    journal.flags.ledger_journal_write = _JOURNAL_WRITE
    # Ledger admins have no generic cancel right; this checked service owns cancellation.
    journal.flags.ignore_permissions = True
    journal.cancel()
    frappe.db.delete("GL Entry", {"voucher_type": "Journal Entry", "voucher_no": journal.name})
    frappe.delete_doc("Posting Link", link.name, ignore_permissions=True, delete_permanently=delete_permanently)
    previous_delete = frappe.flags.get("ledger_journal_delete")
    frappe.flags.ledger_journal_delete = _JOURNAL_DELETE
    try:
        frappe.delete_doc("Journal Entry", journal.name, ignore_permissions=True, delete_permanently=delete_permanently)
    finally:
        frappe.flags.ledger_journal_delete = previous_delete
    # Frappe decrements the last number on delete. Rebuild must not reuse its identity.
    if series and series[1] is not None:
        frappe.db.sql("UPDATE `tabSeries` SET current = %s WHERE name = %s AND current < %s",
                      (series[1], series[0], series[1]))
    if resulting_status:
        frappe.db.set_value("Voucher Draft", draft.name, "status", resulting_status, update_modified=False)
    return journal.name
