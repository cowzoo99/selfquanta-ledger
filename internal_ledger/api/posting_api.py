import frappe

from internal_ledger.accounting import posted_entry_maintenance, posting, posting_maintenance, reversal
from internal_ledger.permissions import require_ledger_admin


@frappe.whitelist(methods=["GET", "POST"])
def get_posted_entry(transaction: str) -> dict:
    return posted_entry_maintenance.get_posted_entry(transaction)


@frappe.whitelist(methods=["POST"])
def post_draft(draft_name: str) -> str:
    require_ledger_admin()
    return posting.post_draft(draft_name)


@frappe.whitelist(methods=["POST"])
def reverse_posting(posting_link: str, reason: str) -> str:
    require_ledger_admin()
    # This request owns the transaction; the accounting service never rolls back its caller.
    for attempt in range(3):
        try:
            return reversal.reverse_posting(posting_link, reason)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist(methods=["POST"])
def approve_and_post(draft_names):
    from internal_ledger.accounting.batch_workflow import approve_and_post as post_batch
    return post_batch(frappe.parse_json(draft_names) if isinstance(draft_names, str) else draft_names)


@frappe.whitelist(methods=["POST"])
def delete_postings(posting_links, reason: str) -> list[dict]:
    require_ledger_admin()
    posting_links = frappe.parse_json(posting_links) if isinstance(posting_links, str) else posting_links
    if not isinstance(posting_links, list) or not posting_links:
        frappe.throw("请选择要同步删除的凭证")
    if any(not isinstance(name, str) or not name for name in posting_links):
        frappe.throw("凭证关联参数无效")
    posting_links = list(dict.fromkeys(posting_links))
    return [posting_maintenance.delete_posting(name, reason) for name in posting_links]


@frappe.whitelist(methods=["POST"])
def rebuild_posted_entry(transaction: str, values, expected_version: str) -> dict:
    require_ledger_admin()
    parsed = frappe.parse_json(values) if isinstance(values, str) else values
    return posted_entry_maintenance.rebuild_posted_entry(transaction, parsed, expected_version)
