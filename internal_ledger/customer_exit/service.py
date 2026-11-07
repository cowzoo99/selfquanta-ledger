import frappe
from frappe.utils import now_datetime

from internal_ledger.permissions import _CUSTOMER_EXIT_WRITE, require_enterprise_owner


def _insert_request(actor: str):
    actor = require_enterprise_owner(actor)
    doc = frappe.get_doc({
        "doctype": "Customer Exit Export",
        "status": "Generating",
        "requested_by": actor,
        "requested_at": now_datetime(),
    })
    doc.flags.customer_exit_write = _CUSTOMER_EXIT_WRITE
    doc.insert(ignore_permissions=True)
    doc.flags.customer_exit_write = None
    return doc
