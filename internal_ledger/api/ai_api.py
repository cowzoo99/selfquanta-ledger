import frappe

from internal_ledger.ai.service import ask_assistant as _ask_assistant
from internal_ledger.ai.service import get_assistant_status as _get_assistant_status


@frappe.whitelist(methods=["GET"])
def get_assistant_status():
    return _get_assistant_status()


@frappe.whitelist(methods=["POST"])
def ask_assistant(feature: str, context_type: str, context_name: str, question: str):
    return _ask_assistant(feature, context_type, context_name, question)
