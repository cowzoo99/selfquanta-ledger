import base64
from decimal import Decimal
import hashlib
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import frappe

from internal_ledger.tests.test_classifier import account, add_rule
from internal_ledger.tests.test_importers import fixture
from internal_ledger.tests.test_member_api import PasswordSafeFunctionTestCase
from internal_ledger.tests.test_permissions import user, tear_down
from internal_ledger.tests.test_review_api import set_up


def import_duplicate_acceptance_batch():
    from internal_ledger.api.import_api import preview_import, confirm_import

    # The first row remains evidence only; select released + unmatched for posting.
    source_id, unmatched_id = uuid4().hex, uuid4().hex
    lines = fixture("wechat").decode("utf-8-sig").splitlines()
    original = lines[2].replace("WX-001", source_id)
    unmatched = lines[4].replace("WX-003", unmatched_id).replace("测试商户丙", "无规则验收商户")
    content = "\n".join([*lines[:2], original, original, unmatched]).encode()
    encoded = base64.b64encode(content).decode()
    preview = preview_import("wechat", encoded, account("1001"))
    assert len(preview["rows"]) == 3 and not preview["errors"]
    imported = confirm_import("wechat", encoded, account("1001"), preview["file_hash"])
    assert imported["summary"] == {"total": 3, "created": 0, "duplicate": 1,
                                   "manual": 2, "conflict": 0, "failed": 0}
    batch = imported["batch_name"]
    rows = frappe.get_all("Imported Transaction", filters={"import_batch": batch},
                          fields=["*"], order_by="raw_row")
    assert len(rows) == 3
    original, candidate, unmatched = rows
    assert original.status == unmatched.status == "Imported"
    assert candidate.status == "Duplicate" and candidate.duplicate_review_status == "Pending"
    assert candidate.duplicate_of == original.name
    assert frappe.get_doc("Import Batch", batch).company == frappe.flags.classification_company
    return original, candidate, unmatched


def assert_duplicate_excluded(candidate):
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.api.classification_api import classify_manually
    from internal_ledger.api.review_api import prepare_draft, prepare_matched_transactions, list_review_items

    item = next(row for row in list_review_items() if row["transaction"]["name"] == candidate.name)
    assert item["draft"] is None and item["classification"] is None and not item["can_approve"]
    for operation in (lambda: classify(candidate.name), lambda: prepare_draft(candidate.name),
                      lambda: classify_manually(candidate.name, "expense", account("560201"),
                                                account("1001"), "重复不得分类", 0)):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            operation()
    assert prepare_matched_transactions([candidate.name])[0]["status"] == "duplicate"
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": candidate.name})


def test_duplicate_pending_and_confirmed_stay_out_of_classification_drafts_and_totals():
    from internal_ledger.api.duplicate_api import resolve_duplicate
    from internal_ledger.api.review_api import get_dashboard_counts

    original, candidate, unmatched = import_duplicate_acceptance_batch()
    pending = get_dashboard_counts("2026-10")
    assert pending["pending_classification"] == 2
    assert Decimal(pending["expense"]) == Decimal("47.50")
    assert_duplicate_excluded(candidate)
    resolved = resolve_duplicate(candidate.name, "confirm", "核对为同一笔交易")
    assert resolved["transaction"]["duplicate_review_status"] == "Confirmed"
    assert resolved["processing"]["status"] == "duplicate"
    assert_duplicate_excluded(candidate)
    assert get_dashboard_counts("2026-10") == pending
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals


def test_released_and_unmatched_only_post_two_balanced_journals_and_retry_without_gl_increment():
    from erpnext.accounts.utils import get_balance_on
    from internal_ledger.api.classification_api import classify_manually
    from internal_ledger.api.duplicate_api import resolve_duplicate
    from internal_ledger.api.posting_api import approve_and_post
    from internal_ledger.api.review_api import get_dashboard_counts, get_review_item, prepare_matched_transactions

    original, candidate, unmatched = import_duplicate_acceptance_batch()
    admin = user("账套管理员")
    frappe.set_user(admin)
    assert_duplicate_excluded(candidate)
    released = resolve_duplicate(candidate.name, "release", "  独立真实交易，经人工核对  ")
    assert released["transaction"]["status"] == "Imported" and released["transaction"]["duplicate_review_status"] == "Released"
    assert released["transaction"]["duplicate_review_note"] == "独立真实交易，经人工核对"
    assert released["transaction"]["duplicate_of"] == original.name
    assert released["processing"]["status"] == "manual" and released["processing"]["draft"] is None
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        resolve_duplicate(candidate.name, "release", "不得重复释放")
    assert frappe.db.count("Internal Ledger Audit Event", {
        "action": "duplicate_release", "reference_name": candidate.name, "result": "success"}) == 1
    counts = get_dashboard_counts("2026-10")
    assert counts["pending_classification"] == 3 and Decimal(counts["expense"]) == Decimal("60.00")
    names = [candidate.name, unmatched.name]
    assert [row["status"] for row in prepare_matched_transactions(names)] == ["manual", "manual"]
    drafts = [classify_manually(name, "expense", account("560201"), account("1001"),
                                "人工核对独立交易", 0).name for name in names]
    for draft_name, transaction_name, amount in zip(drafts, names, (Decimal("12.50"), Decimal("35.00"))):
        draft = get_review_item(draft_name)["draft"]
        assert draft["status"] == "Draft" and draft["imported_transaction"] == transaction_name
        assert draft["is_manual_override"] and draft["manual_override_by"] == admin
        assert sum(Decimal(str(line["debit"])) for line in draft["lines"]) == amount
        assert sum(Decimal(str(line["credit"])) for line in draft["lines"]) == amount
        assert all(line["account_number"] and line["account_name"] for line in draft["lines"])
    company, payment = frappe.flags.classification_company, account("1001")
    before = Decimal(str(get_balance_on(account=payment, date="2026-10-31", company=company)))
    links_before, gl_count_before = frappe.db.count("Posting Link"), frappe.db.count("GL Entry")
    outcomes = approve_and_post(drafts)
    assert [row["status"] for row in outcomes] == ["created", "created"]
    journals = [row["journal_entry"] for row in outcomes]
    links = [row["posting_link"] for row in outcomes]
    assert len(set(journals)) == len(set(links)) == 2
    assert frappe.db.count("Voucher Draft", {"company": company}) == 2
    assert frappe.db.count("Journal Entry", {"company": company, "docstatus": 1}) == 2
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals + 2
    assert frappe.db.count("Posting Link", {"voucher_draft": ["in", drafts]}) == 2
    assert frappe.db.count("Posting Link") == links_before + 2
    assert frappe.db.count("GL Entry") == gl_count_before + 4
    for draft_name, transaction_name, journal_name, link_name in zip(drafts, names, journals, links):
        journal = frappe.get_doc("Journal Entry", journal_name)
        link = frappe.get_doc("Posting Link", link_name)
        assert journal.docstatus == 1 and journal.company == company
        assert link.voucher_draft == draft_name and link.journal_entry == journal_name
        assert frappe.get_doc("Voucher Draft", draft_name).imported_transaction == transaction_name
        assert sum(Decimal(str(line.debit)) for line in journal.accounts) == sum(
            Decimal(str(line.credit)) for line in journal.accounts)
        assert frappe.db.count("GL Entry", {"voucher_no": journal_name, "is_cancelled": 0}) == 2
    balance = Decimal(str(get_balance_on(account=payment, date="2026-10-31", company=company)))
    assert balance - before == Decimal("-47.50")
    gl_before = frappe.get_all("GL Entry", filters={"voucher_no": ["in", journals]}, fields=["*"], order_by="name")
    assert [row["status"] for row in prepare_matched_transactions(names)] == ["skipped", "skipped"]
    retried = approve_and_post(drafts)
    assert [row["status"] for row in retried] == ["skipped", "skipped"]
    assert [row["journal_entry"] for row in retried] == journals
    assert [row["posting_link"] for row in retried] == links
    assert frappe.db.count("Voucher Draft", {"company": company}) == 2
    assert frappe.db.count("Journal Entry", {"company": company}) == 2
    assert frappe.db.count("Posting Link", {"voucher_draft": ["in", drafts]}) == 2
    assert frappe.db.count("Posting Link") == links_before + 2
    assert frappe.db.count("GL Entry") == gl_count_before + 4
    assert frappe.get_all("GL Entry", filters={"voucher_no": ["in", journals]}, fields=["*"], order_by="name") == gl_before
    assert Decimal(str(get_balance_on(account=payment, date="2026-10-31", company=company))) == balance
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": original.name})
    print("E2E UNIFIED: released + unmatched; 2 drafts; 2 submitted JE; 2 links; 4 GL; retry delta=0")


def test_import_learning_and_duplicate_release_auto_draft_but_require_explicit_posting():
    from internal_ledger.api.classification_api import classify_manually
    from internal_ledger.api.duplicate_api import resolve_duplicate
    from internal_ledger.api.import_api import confirm_import
    from internal_ledger.api.posting_api import approve_and_post

    add_rule(value="测试商户甲")
    payment = account("1001")
    lines = fixture("wechat").decode("utf-8-sig").splitlines()
    original = lines[2].replace("WX-001", uuid4().hex)
    unlearned = lines[4].replace("WX-003", uuid4().hex).replace("测试商户丙", "学习验收商户")
    content = "\n".join([*lines[:2], original, original, unlearned]).encode()
    imported = confirm_import("wechat", base64.b64encode(content).decode(), payment,
                              hashlib.sha256(content).hexdigest())
    assert imported["summary"] == {"total": 3, "created": 1, "duplicate": 1,
                                   "manual": 1, "conflict": 0, "failed": 0}
    rows = frappe.get_all("Imported Transaction", filters={"import_batch": imported["batch_name"]},
                          fields=["*"], order_by="raw_row")
    matched, duplicate, manual = rows
    assert frappe.db.count("Voucher Draft", {"imported_transaction": matched.name}) == 1
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": duplicate.name})
    learned_draft = classify_manually(manual.name, "expense", account("560202"), payment,
                                      "记住交易对象", 1)
    next_row = unlearned.replace(manual.source_id, uuid4().hex).replace(
        "2026-10-03 09:00:00", "2026-10-04 09:00:00"
    )
    next_content = "\n".join([*lines[:2], next_row]).encode()
    learned = confirm_import("wechat", base64.b64encode(next_content).decode(), payment,
                             hashlib.sha256(next_content).hexdigest())
    learned_tx = learned["processing"][0]["name"]
    assert learned["summary"]["created"] == 1
    assert frappe.db.count("Voucher Draft", {"imported_transaction": learned_tx}) == 1
    released = resolve_duplicate(duplicate.name, "release", "人工确认是独立交易")
    assert released["processing"]["status"] == "created"
    assert frappe.db.count("Voucher Draft", {"imported_transaction": duplicate.name}) == 1
    drafts = [imported["processing"][0]["draft"], learned_draft.name,
              learned["processing"][0]["draft"], released["processing"]["draft"]]
    assert all(frappe.get_doc("Voucher Draft", name).status == "Draft" for name in drafts)
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals
    assert not frappe.db.exists("Journal Entry", {"user_remark": ["like", "%自动过账%"]})
    results = approve_and_post(frappe.as_json(drafts))
    assert all(row["status"] == "created" and row["journal_entry"] for row in results)
    assert all(frappe.get_doc("Journal Entry", row["journal_entry"]).docstatus == 1 for row in results)
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals + 4


def test_viewer_reads_duplicate_workbench_and_voucher_but_all_accounting_writes_are_denied():
    from internal_ledger.api.classification_api import classify_manually
    from internal_ledger.api.duplicate_api import list_duplicates, resolve_duplicate
    from internal_ledger.api.posting_api import approve_and_post, post_draft
    from internal_ledger.api.review_api import (approve_draft, get_dashboard_counts, get_review_item,
                                               list_review_items, prepare_draft, prepare_matched_transactions)

    original, candidate, unmatched = import_duplicate_acceptance_batch()
    draft = classify_manually(unmatched.name, "expense", account("560201"), account("1001"), "人工核对", 0)
    viewer = user("账务查看者")
    before = [frappe.db.count(doctype) for doctype in ("Voucher Draft", "Journal Entry", "Posting Link", "GL Entry")]
    frappe.set_user(viewer)
    try:
        assert get_dashboard_counts("2026-10")["pending_review"] == 1
        item = next(row for row in list_duplicates() if row["candidate"]["name"] == candidate.name)
        assert item["original"]["name"] == original.name
        assert get_review_item(draft.name)["draft"]["status"] == "Draft"
        for row in list_review_items():
            assert not {"source_id", "dedupe_key", "candidate_dedupe_key"} & row["transaction"].keys()
        for evidence in (item["candidate"], item["original"]):
            assert not {"source_id", "dedupe_key", "candidate_dedupe_key"} & evidence.keys()
        operations = [lambda decision=decision: resolve_duplicate(candidate.name, decision, "查看者不可修改")
                      for decision in ("confirm", "release")]
        operations.extend((lambda: classify_manually(original.name, "expense", account("560201"), account("1001"), "分类", 0),
                           lambda: prepare_draft(original.name), lambda: prepare_matched_transactions([original.name]),
                           lambda: approve_draft(draft.name), lambda: post_draft(draft.name),
                           lambda: approve_and_post([draft.name])))
        for operation in operations:
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()
    finally:
        frappe.set_user("Administrator")
    assert [frappe.db.count(doctype) for doctype in ("Voucher Draft", "Journal Entry", "Posting Link", "GL Entry")] == before
    assert frappe.get_doc("Imported Transaction", candidate.name).duplicate_review_status == "Pending"
    assert frappe.get_doc("Voucher Draft", draft.name).status == "Draft"


def test_real_three_source_workflow_balances_audit_and_source_trace():
    from frappe.desk.query_report import run as run_report
    from erpnext.accounts.utils import get_balance_on
    from internal_ledger.api.import_api import preview_import, confirm_import
    from internal_ledger.api.review_api import get_review_item, get_dashboard_counts
    from internal_ledger.api.posting_api import approve_and_post
    from internal_ledger.api.report_api import get_account_balance

    add_rule(value="测试商户甲", debit_account=account("560201"))
    add_rule(value="测试商户丙", debit_account=account("560202"))
    add_rule(value="测试客户乙", direction="income", transaction_type="income",
             debit_account=None, credit_account=account("5001"))
    admin = user("账套管理员")
    from frappe.core.doctype.user.user import update_password
    from internal_ledger.api.member_api import (create_enterprise_member, reset_enterprise_member_password,
                                                set_enterprise_member_enabled)

    frappe.set_user(admin)
    temporary, replacement, final = (uuid4().hex + "!aB7" for _ in range(3))
    viewer = create_enterprise_member("Workflow viewer", f"workflow-{uuid4().hex}@example.invalid",
                                      temporary, temporary)["email"]
    set_enterprise_member_enabled(viewer, 0)
    set_enterprise_member_enabled(viewer, 1)
    reset_enterprise_member_password(viewer, replacement, replacement)
    events = frappe.get_all("Internal Ledger Audit Event", filters={"reference_name": viewer}, fields=["*"])
    assert {event.action for event in events} == {"create_enterprise_member", "disable_enterprise_member",
                                                  "enable_enterprise_member", "reset_enterprise_member_password"}
    assert all(secret not in frappe.as_json(events) for secret in (temporary, replacement, final))
    url = frappe.get_doc("User", viewer)._reset_password(send_email=False)
    frappe.local.form_dict = frappe._dict(cmd="frappe.core.doctype.user.user.update_password")
    with patch("frappe.core.doctype.user.user.test_password_strength", return_value={}), \
            patch.object(frappe.local, "login_manager", unittest.mock.Mock(login_as=Mock()), create=True):
        update_password(final, key=url.split("key=", 1)[1])
    assert frappe.db.get_value("User", viewer, "internal_ledger_must_change_password") == 0
    payment = account("1001")
    before = Decimal(str(get_balance_on(account=payment, date="2026-10-31",
                                        company=frappe.flags.classification_company)))
    frappe.set_user(admin)
    batches, journals = [], []
    for source in ("wechat", "alipay", "bank"):
        content = fixture(source)
        day_offset = {"wechat": 0, "alipay": 10, "bank": 20}[source]
        if day_offset:
            text = content.decode("utf-8-sig")
            for day in (1, 2, 3):
                text = text.replace(f"2026-10-{day:02d}", f"2026-10-{day + day_offset:02d}")
            content = text.encode("utf-8-sig")
        encoded = base64.b64encode(content).decode()
        preview = preview_import(source, encoded, payment)
        assert len(preview["rows"]) == 3 and not preview["errors"]
        imported = confirm_import(source, encoded, payment, preview["file_hash"])
        assert imported["summary"] == {"total": 3, "created": 3, "duplicate": 0,
                                       "manual": 0, "conflict": 0, "failed": 0}
        batch = imported["batch_name"]
        batches.append(batch)
        saved = frappe.get_doc("Import Batch", batch)
        assert saved.file_hash == hashlib.sha256(content).hexdigest()
        source_file = frappe.get_doc("Internal Ledger Source File", saved.raw_file)
        file = frappe.get_doc("File", {"file_url": source_file.raw_file})
        assert file.is_private and Path(file.get_full_path()).read_bytes() == content
        rows = frappe.get_list("Imported Transaction", filters={"import_batch": batch}, fields=["name"])
        assert len(rows) == 3
        drafts = {row["name"]: row["draft"] for row in imported["processing"]}
        for row in rows:
            doc = frappe.get_doc("Voucher Draft", drafts[row.name])
            assert doc.status == "Draft" and doc.classification_rule
            assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals + len(journals)
            outcome = approve_and_post(frappe.as_json([doc.name]))[0]
            assert outcome["status"] == "created"
            journal_name = outcome["journal_entry"]
            journals.append(journal_name)
            journal = frappe.get_doc("Journal Entry", journal_name)
            assert journal.docstatus == 1
            assert frappe.db.count("GL Entry", {"voucher_no": journal_name, "is_cancelled": 0}) == 2
            link = frappe.get_doc("Posting Link", {"journal_entry": journal_name})
            traced_draft = frappe.get_doc("Voucher Draft", link.voucher_draft)
            traced_tx = frappe.get_doc("Imported Transaction", traced_draft.imported_transaction)
            assert traced_tx.name == row.name and traced_tx.import_batch == batch and traced_tx.raw_row > 0
    duplicate = confirm_import("bank", base64.b64encode(fixture("bank")).decode(), payment,
                               hashlib.sha256(fixture("bank")).hexdigest())["batch_name"]
    duplicates = frappe.get_all("Imported Transaction", filters={"import_batch": duplicate},
                                fields=["name", "status", "duplicate_of"])
    assert len(duplicates) == 3 and all(row.status == "Duplicate" and row.duplicate_of for row in duplicates)
    balance = Decimal(str(get_account_balance(payment, "2026-10-31")))
    assert balance - before == Decimal("2857.50")
    assert frappe.db.count("Journal Entry", {"name": ["in", journals], "docstatus": 1}) == 9
    for action, count in (("confirm_import", 4), ("prepare_draft", 9), ("approve_draft", 9), ("post_draft", 9)):
        assert frappe.db.count("Internal Ledger Audit Event", {"actor": admin, "action": action,
                                                               "result": "success"}) == count
    frappe.set_user(viewer)
    assert Decimal(str(get_account_balance(payment, "2026-10-31"))) == balance
    counts = get_dashboard_counts("2026-10")
    assert counts["posted"] == 9 and counts["income"] == "3000.0" and counts["expense"] == "142.5"
    first = frappe.db.get_value("Posting Link", {"journal_entry": journals[0]}, "voucher_draft")
    assert get_review_item(first)["draft"]["status"] == "Posted"
    company = frappe.flags.classification_company
    common = {"company": company, "from_date": "2026-01-01", "to_date": "2026-12-31"}
    general_ledger = run_report("General Ledger", filters={
        **common, "account": [payment], "categorize_by": "Categorize by Voucher (Consolidated)",
        "include_default_book_entries": 1,
    })
    trial_balance = run_report("Trial Balance", filters={
        **common, "fiscal_year": "2026", "with_period_closing_entry_for_opening": 1,
        "with_period_closing_entry_for_current_period": 1, "include_default_book_entries": 1,
        "show_net_values": 1, "show_group_accounts": 1,
    })
    statement_filters = {
        "company": company, "filter_based_on": "Date Range", "period_start_date": "2026-01-01",
        "period_end_date": "2026-12-31", "periodicity": "Yearly", "selected_view": "Report",
        "include_default_book_entries": 1, "show_zero_values": 1,
    }
    balance_sheet = run_report("Balance Sheet", filters={**statement_filters, "accumulated_values": 1})
    profit_and_loss = run_report(
        "Profit and Loss Statement", filters={**statement_filters, "accumulated_values": 0}
    )
    assert len(general_ledger["result"]) >= 9 and any(row.get("voucher_no") in journals for row in general_ledger["result"])
    assert trial_balance["columns"] and trial_balance["result"]
    assert balance_sheet["columns"] and balance_sheet["result"]
    assert profit_and_loss["columns"] and profit_and_loss["result"]
    print("E2E EVIDENCE", batches, journals,
          "18 GL; cash delta=2857.50; 3 duplicates; 31 audit events; viewer reads; 4 reports")


def test_import_reconcile_evidence_review_and_post_end_to_end():
    from frappe.handler import download_file
    from frappe.utils.response import download_private_file
    from werkzeug.exceptions import Forbidden
    from werkzeug.wrappers import Request
    from internal_ledger.api.evidence_api import (delete_evidence_file, get_evidence,
                                                 save_evidence, upload_evidence)
    from internal_ledger.api.import_api import confirm_import, preview_import
    from internal_ledger.api.posting_api import approve_and_post
    from internal_ledger.api.review_api import auto_classify_pending, get_review_item

    admin, viewer = user("账套管理员"), user("账务查看者")
    frappe.set_user(admin)
    content = fixture("bank")
    encoded = base64.b64encode(content).decode("ascii")
    payment = account("1001")
    preview = preview_import("bank", encoded, payment)
    assert len(preview["rows"]) == 3 and not preview["errors"]
    imported = confirm_import("bank", encoded, payment, preview["file_hash"])
    assert imported["summary"] == {"total": 3, "created": 0, "duplicate": 0,
                                   "manual": 3, "conflict": 0, "failed": 0}
    rows = frappe.get_all("Imported Transaction", filters={"import_batch": imported["batch_name"]},
                          fields=["*"], order_by="raw_row")
    original, *unmatched = rows
    repeated = confirm_import("bank", encoded, payment, preview["file_hash"])
    assert repeated["summary"] == {"total": 3, "created": 0, "duplicate": 3,
                                   "manual": 0, "conflict": 0, "failed": 0}
    candidates = frappe.get_all("Imported Transaction", filters={"import_batch": repeated["batch_name"]},
                                fields=["*"], order_by="raw_row")
    assert len(candidates) == 3
    for candidate, row in zip(candidates, rows):
        assert candidate.status == "Duplicate" and candidate.duplicate_review_status == "Pending"
        assert candidate.duplicate_of == row.name
        assert_duplicate_excluded(candidate)

    add_rule(value=original.counterparty)
    before_journals = frappe.db.count("Journal Entry")
    result = auto_classify_pending()
    assert result["summary"]["created"] == 1 and result["summary"]["manual"] == 2
    draft = frappe.get_doc("Voucher Draft", {"imported_transaction": original.name})
    assert draft.status == "Draft"
    assert sum(Decimal(str(row.debit)) for row in draft.lines) == Decimal("12.50")
    assert sum(Decimal(str(row.credit)) for row in draft.lines) == Decimal("12.50")
    assert auto_classify_pending()["summary"]["created"] == 0
    assert frappe.db.count("Voucher Draft", {"company": frappe.flags.classification_company}) == 1
    assert frappe.db.count("Journal Entry") == before_journals
    assert not frappe.db.exists("Posting Link", {"voucher_draft": draft.name})
    for candidate in candidates:
        assert_duplicate_excluded(candidate)
        assert frappe.db.get_value("Imported Transaction", candidate.name, "duplicate_review_status") == "Pending"
    for row in unmatched:
        assert not frappe.db.exists("Voucher Draft", {"imported_transaction": row.name})

    saved = save_evidence(original.name, "<p>合同已核对</p><table><tr><td>编号</td></tr></table>",
                          get_evidence(original.name)["note_version"])
    assert "合同已核对" in saved["note_html"] and "<table" in saved["note_html"]
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")
    uploaded = upload_evidence(original.name, "回单.png", base64.b64encode(png).decode("ascii"), "image/png")
    info, = uploaded["attachments"]
    attached = frappe.get_doc("File", info["name"])
    assert attached.is_private == 1 and attached.attached_to_name == saved["name"]
    assert info["is_image"] is True and info["file_url"].startswith("/private/files/")
    assert Path(attached.get_full_path()).read_bytes() == png
    assert get_evidence(original.name) == uploaded
    assert frappe.db.count("Journal Entry") == before_journals

    posted = approve_and_post([draft.name])[0]
    assert posted["status"] == "created"
    journal = posted["journal_entry"]
    snapshot = frappe.get_doc("Journal Entry", journal).as_dict()
    gl = frappe.get_all("GL Entry", filters={"voucher_no": journal}, fields=["*"], order_by="name")
    assert snapshot["docstatus"] == 1 and len(gl) == 2
    assert Decimal(str(snapshot["total_debit"])) == Decimal("12.50")
    assert snapshot["total_debit"] == snapshot["total_credit"]
    save_evidence(original.name, "<p>过账后补充说明</p>", get_evidence(original.name)["note_version"])
    late = upload_evidence(original.name, "补充说明.txt", base64.b64encode("补充凭据".encode()).decode(), "text/plain")
    late_info = next(row for row in late["attachments"] if row["name"] != info["name"])
    final = delete_evidence_file(original.name, late_info["name"])
    assert final["note_html"] == "<p>过账后补充说明</p>" and final["attachments"] == [info]
    snapshot["user_remark"] = "过账后补充说明"
    for row in gl:
        row["remarks"] = "过账后补充说明"
    assert frappe.get_doc("Journal Entry", journal).as_dict() == snapshot
    assert frappe.get_all("GL Entry", filters={"voucher_no": journal}, fields=["*"], order_by="name") == gl
    assert frappe.db.count("Journal Entry") == before_journals + 1
    for action, count in (("update_evidence_note", 2), ("upload_evidence", 2), ("delete_evidence", 1)):
        assert frappe.db.count("Internal Ledger Audit Event", {"actor": admin, "action": action,
            "reference_name": original.name, "result": "success"}) == count

    source = frappe.get_doc("Internal Ledger Source File", {"import_batch": imported["batch_name"]})
    before = [frappe.db.count(doctype) for doctype in
              ("Ledger Evidence", "File", "Voucher Draft", "Journal Entry", "Internal Ledger Audit Event")]
    frappe.set_user(viewer)
    try:
        assert get_review_item(draft.name)["draft"]["status"] == "Posted"
        assert get_evidence(original.name) == final
        assert frappe.has_permission("File", "read", info["name"])
        with patch.object(frappe.local, "request", Request.from_values(), create=True):
            response = download_private_file(info["file_url"])
            try:
                response.direct_passthrough = False
                assert response.get_data() == png
            finally:
                response.close()
        for operation in (lambda: save_evidence(original.name, "越权"),
                          lambda: upload_evidence(original.name, "禁止.txt", encoded, "text/plain"),
                          lambda: delete_evidence_file(original.name, info["name"]),
                          auto_classify_pending, lambda: approve_and_post([draft.name]),
                          lambda: frappe.get_list("Internal Ledger Source File"),
                          lambda: source.check_permission("read"),
                          lambda: download_file(source.raw_file)):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()
        with unittest.TestCase().assertRaises(Forbidden):
            download_private_file(source.raw_file)
        assert get_evidence(original.name) == final
    finally:
        frappe.set_user("Administrator")
    assert [frappe.db.count(doctype) for doctype in
            ("Ledger Evidence", "File", "Voucher Draft", "Journal Entry", "Internal Ledger Audit Event")] == before
    assert frappe.get_doc("Journal Entry", journal).as_dict() == snapshot
    print("E2E CLASSIFICATION EVIDENCE: bank fixture; 3 pending duplicates; 1 balanced draft; "
          "explicit admin posting; post-posting evidence immutable JE/GL; viewer read/download only")


def load_tests(loader, tests, pattern):
    def test_site_set_up():
        assert frappe.local.site == "test.localhost", "Synthetic accounting acceptance requires the isolated test site"
        set_up()

    return unittest.TestSuite(PasswordSafeFunctionTestCase(test, setUp=test_site_set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))
