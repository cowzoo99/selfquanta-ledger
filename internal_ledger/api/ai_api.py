import frappe

from internal_ledger.ai.service import ask_assistant as _ask_assistant


@frappe.whitelist(methods=["POST"])
def ask_assistant(feature: str, context_type: str, context_name: str, question: str):
    return _ask_assistant(feature, context_type, context_name, question)
