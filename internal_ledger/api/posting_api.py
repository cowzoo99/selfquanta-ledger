import frappe

from internal_ledger.accounting import posting, reversal


@frappe.whitelist(methods=["POST"])
def post_draft(draft_name: str) -> str:
    return posting.post_draft(draft_name)


@frappe.whitelist(methods=["POST"])
def reverse_posting(posting_link: str, reason: str) -> str:
    return reversal.reverse_posting(posting_link, reason)
