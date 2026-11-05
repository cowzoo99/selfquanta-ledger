from hashlib import sha256

import frappe

from internal_ledger.internal_ledger.doctype.ledger_evidence.ledger_evidence import clean_note, clean_title
from internal_ledger.permissions import document_company


def posted_entry_version(tx, draft, link) -> str:
    values = {
        "transaction": tx.modified,
        "draft": draft.modified,
        "posting": link.modified,
        "journal": link.journal_entry,
        "lines": [(row.name, row.account, str(row.debit or 0), str(row.credit or 0))
                  for row in draft.lines],
    }
    # Frappe v16's serializer sorts keys internally and has no sort_keys argument.
    return sha256(frappe.as_json(values).encode()).hexdigest()


def get_posted_entry(transaction: str, *, for_update: bool = False) -> dict:
    tx = frappe.get_doc("Imported Transaction", transaction, for_update=for_update)
    tx.check_permission("read")
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not company or document_company(tx) != company:
        frappe.throw("流水不属于当前账套", frappe.PermissionError)
    draft_name = frappe.db.get_value(
        "Voucher Draft", {"imported_transaction": tx.name}, "name", for_update=for_update
    )
    if not draft_name:
        frappe.throw("该流水尚未生成凭证")
    draft = frappe.get_doc("Voucher Draft", draft_name, for_update=for_update)
    if draft.company != company:
        frappe.throw("凭证不属于当前账套", frappe.PermissionError)
    draft.check_permission("read")
    link_name = frappe.db.get_value(
        "Posting Link", {"voucher_draft": draft.name}, "name", for_update=for_update
    )
    if draft.status != "Posted" or not link_name:
        frappe.throw("只能维护已过账账目")
    link = frappe.get_doc("Posting Link", link_name, for_update=for_update)
    link.check_permission("read")
    if link.status != "Posted" or link.reversal_journal_entry:
        frappe.throw("已冲销凭证不能编辑或删除")
    return serialize_posted_entry(tx, draft, link, posted_entry_version(tx, draft, link))


def serialize_posted_entry(tx, draft, link, version) -> dict:
    lines = []
    for row in draft.lines:
        account = frappe.db.get_value("Account", {"name": row.account, "company": draft.company},
                                      ["account_name", "account_number"], as_dict=True)
        if not account:
            frappe.throw("科目不属于当前账套", frappe.PermissionError)
        lines.append({"name": row.name, "account": row.account, "debit": row.debit, "credit": row.credit,
                      "account_name": account.account_name, "account_number": account.account_number})
    evidence = frappe.db.get_value("Ledger Evidence", {
        "imported_transaction": tx.name, "company": draft.company,
    }, ["name", "display_title", "note_html"], as_dict=True) or frappe._dict()
    return {
        "transaction": {field: tx.get(field) for field in (
            "name", "source", "occurred_at", "amount", "direction", "counterparty", "summary", "payment_account")},
        "draft": {**{field: draft.get(field) for field in (
            "name", "company", "base_currency", "posting_date", "summary", "transaction_type", "status")},
            "lines": lines},
        "posting": {"name": link.name, "journal_entry": link.journal_entry, "status": link.status},
        "evidence": {"name": evidence.name, "display_title": clean_title(evidence.display_title),
                     "note_html": clean_note(evidence.note_html)},
        "version": version,
    }
