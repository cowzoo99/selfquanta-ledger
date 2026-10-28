from decimal import Decimal

import frappe
from frappe.utils import getdate
from internal_ledger.permissions import require_ledger_admin, record_audit, _JOURNAL_WRITE


def validate_unfrozen_accounts(accounts):
    for row in accounts:
        if frappe.db.get_value("Account", row.account, "freeze_account", for_update=True) == "Yes":
            frappe.throw("冻结科目不得过账或冲销")


def validate_period(company, posting_date):
    from erpnext.accounts.utils import get_fiscal_year

    company_doc = frappe.get_doc("Company", company, for_update=True)
    get_fiscal_year(posting_date, company=company)
    if company_doc.accounts_frozen_till_date and getdate(posting_date) <= getdate(company_doc.accounts_frozen_till_date):
        frappe.throw("记账日期处于冻结期间")
    # This service never uses ERPNext's role exemptions for closed periods.
    closed = frappe.db.sql("""
        SELECT ap.name FROM `tabAccounting Period` ap
        INNER JOIN `tabClosed Document` cd ON cd.parent = ap.name
        WHERE ap.company = %s AND ap.disabled = 0 AND cd.closed = 1
          AND cd.document_type = 'Journal Entry' AND %s BETWEEN ap.start_date AND ap.end_date
        FOR UPDATE
    """, (company, posting_date))
    if closed:
        frappe.throw("记账日期处于关闭会计期间")
    return company_doc


def post_draft(draft_name: str) -> str:
    """Post once in the caller's transaction; let every failure propagate."""
    require_ledger_admin()
    draft = frappe.get_doc("Voucher Draft", draft_name, for_update=True)
    draft.check_permission("write")
    existing = frappe.db.get_value("Posting Link", {"voucher_draft": draft.name}, "name", for_update=True)
    if existing:
        link = frappe.get_doc("Posting Link", existing, for_update=True)
        link.check_permission("read")
        if draft.status != "Posted":
            frappe.throw("草稿与过账关联状态不一致")
        return link.journal_entry
    if draft.status != "Approved":
        frappe.throw("仅 Approved 草稿可过账")
    # Match reversal's Company -> Account order before draft.validate locks accounts.
    company = validate_period(draft.company, draft.posting_date)
    draft.validate()
    validate_unfrozen_accounts(draft.lines)
    if company.default_currency != draft.base_currency:
        frappe.throw("草稿本位币必须与公司币种一致")
    journal = frappe.get_doc({
        "doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": draft.company,
        "posting_date": draft.posting_date, "user_remark": draft.summary or "", "accounts": [],
    })
    for line in draft.lines:
        currency = frappe.db.get_value("Account", line.account, "account_currency", for_update=True)
        if currency and currency != draft.base_currency:
            frappe.throw("草稿仅支持本位币科目，不得推测外币汇率")
        journal.append("accounts", {
            "account": line.account, "account_currency": draft.base_currency, "exchange_rate": 1,
            "debit_in_account_currency": line.debit, "credit_in_account_currency": line.credit,
            "cost_center": company.cost_center,
        })
    # Standard JE validation rounds currency fields. Refuse loss before insert.
    for source, row in zip(draft.lines, journal.accounts):
        for side in ("debit", "credit"):
            amount = Decimal(str(source.get(side) or 0))
            for field in (side, side + "_in_account_currency"):
                unit = Decimal(1).scaleb(-row.precision(field))
                if amount != amount.quantize(unit):
                    frappe.throw("草稿金额超出 Journal Entry 币种精度，不得舍入")
    journal.flags.ledger_journal_write = _JOURNAL_WRITE
    journal.insert()
    journal.submit()
    link = frappe.get_doc({"doctype": "Posting Link", "voucher_draft": draft.name,
                           "journal_entry": journal.name, "status": "Posted"})
    link.flags.posting_service = True
    link.insert()
    draft.status = "Posted"
    draft.flags.posting_transition = True
    # Validate the service-only transition, then update only the parent status.
    # Document.save() would also rewrite the already-approved child rows.
    draft.validate()
    draft.db_set("status", "Posted")
    record_audit("post_draft", "Voucher Draft", draft.name, "success")
    return journal.name
