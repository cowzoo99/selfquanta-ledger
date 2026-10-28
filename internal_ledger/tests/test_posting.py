import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

import frappe

from internal_ledger.tests.test_classifier import account, tear_down
from internal_ledger.tests.test_review_api import draft, set_up


def approved_draft():
    from internal_ledger.api.review_api import approve_draft

    return approve_draft(draft().name)


def link_for(name):
    return frappe.db.get_value("Posting Link", {"voucher_draft": name}, "name")


def test_posting_is_idempotent_and_uses_real_journal_and_gl():
    from internal_ledger.accounting.posting import post_draft

    doc = approved_draft()
    doc.reload()
    rows = [row.as_dict() for row in doc.lines]
    name = post_draft(doc.name)
    assert post_draft(doc.name) == name
    journal = frappe.get_doc("Journal Entry", name)
    assert journal.docstatus == 1 and journal.company == doc.company
    assert str(journal.posting_date) == str(doc.posting_date)
    assert journal.user_remark == (doc.summary or "")
    assert [(row.account, Decimal(str(row.debit)), Decimal(str(row.credit))) for row in journal.accounts] == [
        (account("560201"), Decimal("12.5"), Decimal("0")),
        (account("1001"), Decimal("0"), Decimal("12.5"))]
    assert frappe.db.count("GL Entry", {"voucher_no": name, "is_cancelled": 0}) == 2
    doc.reload()
    assert doc.status == "Posted" and [row.as_dict() for row in doc.lines] == rows
    assert frappe.db.count("Posting Link", {"voucher_draft": doc.name}) == 1
    print("POSTING EVIDENCE", doc.name, name, link_for(doc.name))


def test_only_approved_and_ordinary_save_cannot_post_or_edit_posted():
    from internal_ledger.accounting.posting import post_draft

    doc = draft()
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        post_draft(doc.name)
    doc.status = "Posted"
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        doc.save()
    from internal_ledger.api.review_api import approve_draft

    approve_draft(doc.name)
    post_draft(doc.name)
    doc.reload()
    for field, value in (("status", "Approved"), ("summary", "changed")):
        doc.reload()
        doc.set(field, value)
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            doc.save()
    doc.reload()
    doc.lines[0].debit = 20
    doc.lines[1].credit = 20
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        doc.save()


def test_submit_link_and_draft_failures_belong_to_caller_transaction():
    from internal_ledger.accounting.posting import post_draft
    from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry
    from internal_ledger.internal_ledger.doctype.posting_link.posting_link import PostingLink
    from internal_ledger.internal_ledger.doctype.voucher_draft.voucher_draft import VoucherDraft

    doc = approved_draft()
    before = frappe.db.count("Journal Entry")
    gl_before = frappe.db.count("GL Entry")
    for cls, method in ((JournalEntry, "on_submit"), (PostingLink, "after_insert"), (VoucherDraft, "db_set")):
        frappe.db.savepoint("posting_failure")
        with patch.object(cls, method, side_effect=RuntimeError("injected posting failure"), create=True):
            with unittest.TestCase().assertRaisesRegex(RuntimeError, "injected"):
                post_draft(doc.name)
        frappe.db.rollback(save_point="posting_failure")
        assert frappe.db.count("Journal Entry") == before
        assert frappe.db.count("GL Entry") == gl_before
        assert not link_for(doc.name)
        assert frappe.db.get_value("Voucher Draft", doc.name, "status") == "Approved"
    print("ROLLBACK EVIDENCE", doc.name, "Approved; JE/GL unchanged; no Posting Link; 3 failure stages")


def test_success_does_not_commit_callers_transaction():
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.accounting.reversal import reverse_posting

    doc = approved_draft()
    frappe.db.savepoint("posting_success")
    name = post_draft(doc.name)
    reverse = reverse_posting(link_for(doc.name), "回滚验证")
    frappe.db.rollback(save_point="posting_success")
    assert not frappe.db.exists("Journal Entry", name)
    assert not frappe.db.exists("Journal Entry", reverse)
    assert not link_for(doc.name)
    assert frappe.db.get_value("Voucher Draft", doc.name, "status") == "Approved"


def test_database_unique_constraints_reject_direct_duplicate_links():
    from internal_ledger.accounting.posting import post_draft

    doc = approved_draft()
    post_draft(doc.name)
    original = frappe.get_doc("Posting Link", link_for(doc.name))
    for field in ("voucher_draft", "journal_entry"):
        copied = frappe.copy_doc(original)
        copied.name = uuid4().hex
        copied.set("journal_entry" if field == "voucher_draft" else "voucher_draft", uuid4().hex)
        with unittest.TestCase().assertRaises(frappe.UniqueValidationError):
            copied.db_insert()


def test_reversal_has_native_link_exact_swapped_amounts_and_requires_reason():
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.accounting.reversal import reverse_posting

    doc = approved_draft()
    name = post_draft(doc.name)
    link = link_for(doc.name)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        reverse_posting(link, "  ")
    reverse = reverse_posting(link, " 科目选择错误 ")
    journal = frappe.get_doc("Journal Entry", reverse)
    assert journal.docstatus == 1 and journal.reversal_of == name
    assert [(row.account, Decimal(str(row.debit)), Decimal(str(row.credit))) for row in journal.accounts] == [
        (account("560201"), Decimal("0"), Decimal("12.5")),
        (account("1001"), Decimal("12.5"), Decimal("0"))]
    saved = frappe.get_doc("Posting Link", link)
    assert saved.status == "Reversed" and saved.reversal_journal_entry == reverse
    assert saved.reversal_reason == "科目选择错误"
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        reverse_posting(link, "重复冲销")
    assert post_draft(doc.name) == name
    assert frappe.db.count("Journal Entry", {"reversal_of": name}) == 1
    assert frappe.db.count("GL Entry", {"voucher_no": reverse, "is_cancelled": 0}) == 2
    print("REVERSAL EVIDENCE", name, reverse, "reversal_of; swapped 12.5; duplicate refused")


def test_reversal_submit_failure_rolls_back_without_partial_link():
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.accounting.reversal import reverse_posting
    from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry

    doc = approved_draft()
    name = post_draft(doc.name)
    link = link_for(doc.name)
    before = frappe.db.count("Journal Entry")
    frappe.db.savepoint("reversal_failure")
    with patch.object(JournalEntry, "on_submit", side_effect=RuntimeError("injected reverse failure")):
        with unittest.TestCase().assertRaises(RuntimeError):
            reverse_posting(link, "失败测试")
    frappe.db.rollback(save_point="reversal_failure")
    assert frappe.db.count("Journal Entry") == before
    assert not frappe.db.exists("Journal Entry", {"reversal_of": name})
    saved = frappe.get_doc("Posting Link", link)
    assert saved.status == "Posted" and not saved.reversal_journal_entry


def test_invalid_current_accounts_lines_source_or_rules_prevent_posting():
    from internal_ledger.accounting.posting import post_draft

    doc = approved_draft()
    for doctype, name, field, invalid, valid in (
        ("Account", account("560201"), "disabled", 1, 0),
        ("Classification Rule", doc.classification_rule, "enabled", 0, 1),
        ("Voucher Draft Line", doc.lines[0].name, "debit", 12.49, 12.5),
        ("Voucher Draft", doc.name, "company", "_Test Company", doc.company),
    ):
        frappe.db.set_value(doctype, name, field, invalid)
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            post_draft(doc.name)
        frappe.db.set_value(doctype, name, field, valid)
        assert not link_for(doc.name)


def test_freeze_and_closed_period_refuse_even_exempt_admin():
    from internal_ledger.accounting.posting import post_draft

    doc = approved_draft()
    frappe.db.set_value("Company", doc.company, "accounts_frozen_till_date", "2026-10-01")
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        post_draft(doc.name)
    frappe.db.set_value("Company", doc.company, "accounts_frozen_till_date", None)
    frappe.get_doc({"doctype": "Accounting Period", "period_name": "posting-" + uuid4().hex,
                    "company": doc.company, "start_date": "2026-10-01", "end_date": "2026-10-01",
                    "exempted_role": "System Manager",
                    "closed_documents": [{"document_type": "Journal Entry", "closed": 1}]}).insert()
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        post_draft(doc.name)
    assert not link_for(doc.name)


def test_manual_override_posts_the_approved_values():
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.api.review_api import approve_draft, update_draft

    doc = draft()
    update_draft(doc.name, [{"account": account("560202"), "debit": "12.5"},
                            {"account": account("1001"), "credit": "12.5"}], "人工摘要", "2026-10-02")
    approve_draft(doc.name)
    journal = frappe.get_doc("Journal Entry", post_draft(doc.name))
    assert journal.accounts[0].account == account("560202")
    assert journal.user_remark == "人工摘要" and str(journal.posting_date) == "2026-10-02"


def test_posting_api_is_post_only_and_non_admin_cannot_write():
    from internal_ledger.api.posting_api import post_draft, reverse_posting

    doc = approved_draft()
    name = post_draft(doc.name)
    for method in (post_draft, reverse_posting):
        assert frappe.allowed_http_methods_for_whitelisted_func[method] == ["POST"]
    user = "posting-viewer@example.com"
    frappe.get_doc({"doctype": "User", "email": user, "first_name": "Viewer", "send_welcome_email": 0,
                    "roles": [{"role": "账务查看者"}]}).insert()
    frappe.set_user(user)
    try:
        for function, args in ((post_draft, (doc.name,)), (reverse_posting, (link_for(doc.name), "禁止"))):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                function(*args)
    finally:
        frappe.set_user("Administrator")
    assert frappe.db.get_value("Journal Entry", name, "docstatus") == 1


def test_regular_ledger_admin_posts_and_reverses_with_normal_permissions():
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.accounting.reversal import reverse_posting
    from internal_ledger.install import ensure_roles

    ensure_roles()
    ensure_roles()
    perms = frappe.get_all("Custom DocPerm", filters={"parent": "Journal Entry", "role": "账套管理员"}, fields=["*"])
    assert len(perms) == 1
    assert all(perms[0].get(action) for action in ("read", "write", "create", "submit"))
    assert not perms[0].cancel and not perms[0].delete
    doc = approved_draft()
    user = "posting-admin@example.com"
    frappe.get_doc({"doctype": "User", "email": user, "first_name": "Ledger Admin", "send_welcome_email": 0,
                    "roles": [{"role": "账套管理员"}]}).insert()
    frappe.set_user(user)
    try:
        name = post_draft(doc.name)
        reverse = reverse_posting(link_for(doc.name), "正常权限验证")
        assert frappe.db.get_value("Journal Entry", reverse, "reversal_of") == name
    finally:
        frappe.set_user("Administrator")


def test_foreign_currency_missing_year_and_rounding_are_rejected():
    from internal_ledger.accounting.posting import post_draft

    doc = approved_draft()
    frappe.db.set_value("Account", account("1001"), "account_currency", "USD")
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        post_draft(doc.name)
    frappe.db.set_value("Account", account("1001"), "account_currency", "CNY")
    frappe.db.set_value("Fiscal Year", "2026", "disabled", 1)
    frappe.cache.hdel("fiscal_years", doc.company)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        post_draft(doc.name)
    frappe.db.set_value("Fiscal Year", "2026", "disabled", 0)
    frappe.cache.hdel("fiscal_years", doc.company)
    frappe.db.set_value("Imported Transaction", doc.imported_transaction, "amount", 12.5001)
    for line in doc.lines:
        frappe.db.set_value("Voucher Draft Line", line.name, "debit" if line.debit else "credit", 12.5001)
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "精度"):
        post_draft(doc.name)
    assert not link_for(doc.name)


def test_frozen_account_is_rejected_before_insert_even_for_exempt_admin():
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.accounting.reversal import reverse_posting

    doc = approved_draft()
    frappe.db.set_value("Account", account("560201"), "freeze_account", "Yes")
    frappe.db.set_value("Company", doc.company, "role_allowed_for_frozen_entries", "System Manager")
    frappe.clear_document_cache("Account", account("560201"))
    frappe.clear_document_cache("Company", doc.company)
    before = frappe.db.count("Journal Entry")
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "冻结"):
        post_draft(doc.name)
    assert frappe.db.count("Journal Entry") == before
    assert not link_for(doc.name)
    frappe.db.set_value("Account", account("560201"), "freeze_account", "No")
    frappe.clear_document_cache("Account", account("560201"))
    post_draft(doc.name)
    frappe.db.set_value("Account", account("560201"), "freeze_account", "Yes")
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "冻结"):
        reverse_posting(link_for(doc.name), "冻结科目冲销")
    assert frappe.db.count("Journal Entry") == before + 1


def test_two_connections_post_once_and_reverse_once():
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.accounting.reversal import reverse_posting
    from internal_ledger.api.review_api import approve_draft
    from internal_ledger.install import install_chart
    from internal_ledger.tests.test_classifier import add_rule
    from internal_ledger.tests.test_dedupe import tx
    from internal_ledger.tests.test_install import make_company

    site, sites_path = frappe.local.site, frappe.local.sites_path
    frappe.db.rollback()
    barrier = Barrier(2)

    def connected(action):
        frappe.init(site=site, sites_path=sites_path)
        frappe.connect()
        frappe.set_user("Administrator")
        try:
            return action()
        finally:
            frappe.db.rollback()
            frappe.destroy()

    def prepare():
        company = make_company()
        install_chart(company)
        frappe.flags.classification_company = company
        rule = add_rule()
        created_year = not frappe.db.exists("Fiscal Year", "2026")
        if created_year:
            frappe.get_doc({"doctype": "Fiscal Year", "year": "2026", "year_start_date": "2026-01-01",
                            "year_end_date": "2026-12-31"}).insert()
        batch = frappe.get_doc({"doctype": "Import Batch", "source": "wechat", "file_hash": uuid4().hex,
                                "company": company, "base_currency": "CNY"}).insert()
        row = tx(source_id=uuid4().hex, payment_account=account("1001"))
        imported = frappe.get_doc({"doctype": "Imported Transaction", **asdict(row),
                                   "import_batch": batch.name, "status": "Imported",
                                   "dedupe_key": "wechat:" + row.source_id}).insert()
        name = create_draft(imported.name, classify(imported.name))
        approve_draft(name)
        frappe.db.commit()
        return company, rule.name, batch.name, imported.name, name, created_year

    def post_one():
        barrier.wait(timeout=20)
        name = post_draft(fixture[4])
        frappe.db.commit()
        return name

    def reverse_one():
        barrier.wait(timeout=20)
        try:
            name = reverse_posting(link_for(fixture[4]), "并发冲销")
            frappe.db.commit()
            return "reversed", name
        except frappe.ValidationError:
            frappe.db.rollback()
            return "refused", None

    def verify_and_clean():
        links = frappe.get_all("Posting Link", filters={"voucher_draft": fixture[4]}, fields=["*"])
        journals = frappe.get_all("Journal Entry", filters={"company": fixture[0]}, pluck="name")
        counts = len(links), len(journals)
        for name in journals:
            frappe.db.delete("GL Entry", {"voucher_no": name, "voucher_type": "Journal Entry"})
            frappe.db.delete("Journal Entry Account", {"parent": name})
            frappe.db.delete("Journal Entry", {"name": name})
        frappe.db.delete("Posting Link", {"voucher_draft": fixture[4]})
        frappe.db.delete("Voucher Draft Line", {"parent": fixture[4]})
        frappe.db.delete("Voucher Draft", {"name": fixture[4]})
        for doctype, name in (("Classification Rule", fixture[1]), ("Imported Transaction", fixture[3]),
                              ("Import Batch", fixture[2]), ("Company", fixture[0])):
            frappe.delete_doc(doctype, name, force=True)
        if fixture[5]:
            frappe.delete_doc("Fiscal Year", "2026", force=True)
        frappe.db.commit()
        return counts

    with ThreadPoolExecutor(max_workers=2) as pool:
        fixture = pool.submit(connected, prepare).result(timeout=60)
        try:
            futures = [pool.submit(connected, post_one) for _ in range(2)]
            names = [future.result(timeout=60) for future in futures]
            assert names[0] == names[1]
            futures = [pool.submit(connected, reverse_one) for _ in range(2)]
            results = [future.result(timeout=60) for future in futures]
            assert sorted(status for status, name in results) == ["refused", "reversed"]
        finally:
            counts = pool.submit(connected, verify_and_clean).result(timeout=60)
    assert counts == (1, 2)
    print("CONCURRENCY EVIDENCE", names, results, "one link; two JE including reversal")


def test_two_connections_interleave_post_and_reverse_without_deadlock():
    from internal_ledger.accounting import posting, reversal
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft
    from internal_ledger.api.review_api import approve_draft
    from internal_ledger.install import install_chart
    from internal_ledger.tests.test_classifier import add_rule
    from internal_ledger.tests.test_dedupe import tx
    from internal_ledger.tests.test_install import make_company

    site, sites_path = frappe.local.site, frappe.local.sites_path
    frappe.db.rollback()
    reverse_has_company, post_requests_company = Event(), Event()
    real_validate_period = posting.validate_period

    def connected(action):
        frappe.init(site=site, sites_path=sites_path)
        frappe.connect()
        frappe.set_user("Administrator")
        try:
            return action()
        finally:
            frappe.db.rollback()
            frappe.destroy()

    def prepare():
        company = make_company()
        install_chart(company)
        frappe.flags.classification_company = company
        rule = add_rule()
        created_year = not frappe.db.exists("Fiscal Year", "2026")
        if created_year:
            frappe.get_doc({"doctype": "Fiscal Year", "year": "2026", "year_start_date": "2026-01-01",
                            "year_end_date": "2026-12-31"}).insert()
        batch = frappe.get_doc({"doctype": "Import Batch", "source": "wechat", "file_hash": uuid4().hex,
                                "company": company, "base_currency": "CNY"}).insert()
        imported, drafts = [], []
        for _ in range(2):
            row = tx(source_id=uuid4().hex, payment_account=account("1001"))
            item = frappe.get_doc({"doctype": "Imported Transaction", **asdict(row),
                                   "import_batch": batch.name, "status": "Imported",
                                   "dedupe_key": "wechat:" + row.source_id}).insert()
            imported.append(item.name)
            name = create_draft(item.name, classify(item.name))
            approve_draft(name)
            drafts.append(name)
        original = posting.post_draft(drafts[0])
        link = link_for(drafts[0])
        frappe.db.commit()
        return company, rule.name, batch.name, imported, drafts, original, link, created_year

    def interleave_period(company, posting_date):
        if frappe.flags.lock_order_action == "reverse":
            result = real_validate_period(company, posting_date)
            reverse_has_company.set()
            assert post_requests_company.wait(timeout=20), "post did not reach Company lock"
            return result
        assert reverse_has_company.wait(timeout=20), "reverse did not acquire Company lock"
        # Old code reaches here holding Account; fixed code holds no Account yet.
        post_requests_company.set()
        return real_validate_period(company, posting_date)

    def post_new():
        frappe.flags.lock_order_action = "post"
        assert reverse_has_company.wait(timeout=20)
        name = posting.post_draft(fixture[4][1])
        frappe.db.commit()
        return name

    def reverse_existing():
        frappe.flags.lock_order_action = "reverse"
        name = reversal.reverse_posting(fixture[6], "共享资金科目交错锁测试")
        frappe.db.commit()
        return name

    def verify_and_clean():
        links = frappe.get_all("Posting Link", filters={"voucher_draft": ["in", fixture[4]]}, fields=["*"])
        journals = frappe.get_all("Journal Entry", filters={"company": fixture[0]}, fields=["name", "docstatus", "reversal_of"])
        statuses = [frappe.db.get_value("Voucher Draft", name, "status") for name in fixture[4]]
        for journal in journals:
            frappe.db.delete("GL Entry", {"voucher_no": journal.name, "voucher_type": "Journal Entry"})
            frappe.db.delete("Journal Entry Account", {"parent": journal.name})
            frappe.db.delete("Journal Entry", {"name": journal.name})
        frappe.db.delete("Posting Link", {"voucher_draft": ["in", fixture[4]]})
        for name in fixture[4]:
            frappe.db.delete("Voucher Draft Line", {"parent": name})
            frappe.db.delete("Voucher Draft", {"name": name})
        for name in fixture[3]:
            frappe.delete_doc("Imported Transaction", name, force=True)
        for doctype, name in (("Classification Rule", fixture[1]), ("Import Batch", fixture[2]), ("Company", fixture[0])):
            frappe.delete_doc(doctype, name, force=True)
        if fixture[7]:
            frappe.delete_doc("Fiscal Year", "2026", force=True)
        frappe.db.commit()
        return links, journals, statuses

    with ThreadPoolExecutor(max_workers=2) as pool:
        fixture = pool.submit(connected, prepare).result(timeout=60)
        try:
            with patch.object(posting, "validate_period", interleave_period), patch.object(reversal, "validate_period", interleave_period):
                reverse_future = pool.submit(connected, reverse_existing)
                post_future = pool.submit(connected, post_new)
                # Inspect both outcomes; do not hide either connection's exception.
                results = [future.exception(timeout=60) for future in (post_future, reverse_future)]
                assert results == [None, None], results
                posted, reversed_name = post_future.result(), reverse_future.result()
        finally:
            links, journals, statuses = pool.submit(connected, verify_and_clean).result(timeout=60)
    assert statuses == ["Posted", "Posted"]
    assert len(links) == 2 and len(journals) == 3
    assert all(row.docstatus == 1 for row in journals)
    assert len([row for row in journals if row.reversal_of == fixture[5]]) == 1
    assert {row.voucher_draft: row.journal_entry for row in links} == {
        fixture[4][0]: fixture[5], fixture[4][1]: posted}
    old_link = next(row for row in links if row.name == fixture[6])
    assert old_link.status == "Reversed" and old_link.reversal_journal_entry == reversed_name
    print("MIXED LOCK EVIDENCE", posted, reversed_name, "no deadlock; 2 unique links; 3 submitted JE")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(
        test, setUp=None if name.startswith("test_two_connections_") else set_up,
        tearDown=None if name.startswith("test_two_connections_") else tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_"))
