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
