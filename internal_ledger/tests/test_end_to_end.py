import base64
from decimal import Decimal
import hashlib
from pathlib import Path
import unittest

import frappe

from internal_ledger.tests.test_classifier import account, add_rule
from internal_ledger.tests.test_importers import fixture
from internal_ledger.tests.test_permissions import user, tear_down
from internal_ledger.tests.test_review_api import set_up


def test_real_three_source_workflow_balances_audit_and_source_trace():
    from erpnext.accounts.utils import get_balance_on
    from internal_ledger.api.import_api import preview_import, confirm_import
    from internal_ledger.api.review_api import prepare_draft, approve_draft, get_review_item, get_dashboard_counts
    from internal_ledger.api.posting_api import post_draft
    from internal_ledger.api.report_api import get_account_balance

    add_rule(value="测试商户甲", debit_account=account("560201"))
    add_rule(value="测试商户丙", debit_account=account("560202"))
    add_rule(value="测试客户乙", direction="income", transaction_type="income",
             debit_account=None, credit_account=account("5001"))
    admin = user("账套管理员")
    viewer = user("账务查看者")
    payment = account("1001")
    before = Decimal(str(get_balance_on(account=payment, date="2026-10-31",
                                        company=frappe.flags.classification_company)))
    frappe.set_user(admin)
    batches, journals = [], []
    for source in ("wechat", "alipay", "bank"):
        content = fixture(source)
        encoded = base64.b64encode(content).decode()
        preview = preview_import(source, encoded, payment)
        assert len(preview["rows"]) == 3 and not preview["errors"]
        batch = confirm_import(source, encoded, payment, preview["file_hash"])["batch_name"]
        batches.append(batch)
        saved = frappe.get_doc("Import Batch", batch)
        assert saved.file_hash == hashlib.sha256(content).hexdigest()
        source_file = frappe.get_doc("Internal Ledger Source File", saved.raw_file)
        file = frappe.get_doc("File", {"file_url": source_file.raw_file})
        assert file.is_private and Path(file.get_full_path()).read_bytes() == content
        rows = frappe.get_list("Imported Transaction", filters={"import_batch": batch}, fields=["name"])
        assert len(rows) == 3
        for row in rows:
            doc = prepare_draft(row.name)
            assert doc.status == "Draft" and doc.classification_rule
            assert approve_draft(doc.name).status == "Approved"
            journal_name = post_draft(doc.name)
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
    print("E2E EVIDENCE", batches, journals, "18 GL; cash delta=2857.50; 3 duplicates; 31 audit events; viewer reads")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))
