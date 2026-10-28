import frappe

from internal_ledger.accounting.posting import validate_period, validate_unfrozen_accounts, write_permission


def reverse_posting(posting_link: str, reason: str) -> str:
    write_permission()
    if not isinstance(reason, str) or not reason.strip():
        frappe.throw("冲销必须填写原因")
    link = frappe.get_doc("Posting Link", posting_link, for_update=True)
    link.check_permission("write")
    if link.status != "Posted" or link.reversal_journal_entry:
        frappe.throw("该过账已冲销，不得重复冲销")
    original = frappe.get_doc("Journal Entry", link.journal_entry, for_update=True)
    original.check_permission("read")
    if original.docstatus != 1 or original.reversal_of:
        frappe.throw("只能冲销已提交的原始 Journal Entry")
    if frappe.db.get_value("Journal Entry", {"reversal_of": original.name, "docstatus": 1}, "name", for_update=True):
        frappe.throw("原分录已存在冲销凭证")
    validate_period(original.company, original.posting_date)
    validate_unfrozen_accounts(original.accounts)
    from erpnext.accounts.doctype.journal_entry.journal_entry import make_reverse_journal_entry

    # Native v16 mapping copies accounts/currencies/dimensions and swaps both amounts.
    reverse = make_reverse_journal_entry(original.name)
    reverse.posting_date = original.posting_date
    reverse.user_remark = reason.strip()
    reverse.insert()
    reverse.submit()
    link.status = "Reversed"
    link.reversal_journal_entry = reverse.name
    link.reversal_reason = reason.strip()
    link.flags.posting_service = True
    link.save()
    return reverse.name
