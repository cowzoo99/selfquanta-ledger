import frappe

from internal_ledger.accounting import posting, reversal
from internal_ledger.permissions import require_ledger_admin


@frappe.whitelist(methods=["POST"])
def post_draft(draft_name: str) -> str:
    require_ledger_admin()
    return posting.post_draft(draft_name)


@frappe.whitelist(methods=["POST"])
def reverse_posting(posting_link: str, reason: str) -> str:
    require_ledger_admin()
    return reversal.reverse_posting(posting_link, reason)


@frappe.whitelist(methods=["POST"])
def approve_and_post(draft_names):
    from internal_ledger.accounting.batch_workflow import approve_and_post as post_batch
    return post_batch(frappe.parse_json(draft_names) if isinstance(draft_names, str) else draft_names)
