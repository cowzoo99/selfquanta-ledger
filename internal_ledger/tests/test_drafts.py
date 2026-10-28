from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
import unittest
from uuid import uuid4

import frappe

from internal_ledger.tests.test_classifier import account, add_rule, set_up, tear_down, transaction


def make_draft(kind, direction, debit, credit):
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft

    rule = add_rule(transaction_type=kind, direction=direction, debit_account=debit, credit_account=credit)
    name = transaction(direction=direction, amount=Decimal("12.50"))
    result = classify(name)
    draft = frappe.get_doc("Voucher Draft", create_draft(name, result))
    assert draft.status == "Draft"
    assert draft.docstatus == 0
    assert draft.imported_transaction == name
    assert draft.classification_rule == rule.name
    assert draft.company == frappe.flags.classification_company
    assert draft.transaction_type == kind
    assert draft.posting_date.isoformat() == "2026-10-01"
    actual = [(row.account, Decimal(str(row.debit)), Decimal(str(row.credit))) for row in draft.lines]
    assert actual == [(debit or account("1001"), Decimal("12.50"), Decimal("0")),
                      (credit or account("1001"), Decimal("0"), Decimal("12.50"))]
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals
    return draft


def test_expense_draft():
    make_draft("expense", "expense", account("560201"), None)


def test_income_draft():
    make_draft("income", "income", None, account("5001"))


def test_transfer_drafts_support_both_directions():
    make_draft("transfer", "expense", account("100201"), None)
    make_draft("transfer", "income", None, account("100201"))


def test_refund_drafts_reverse_expense_and_income():
    make_draft("refund", "income", None, account("560201"))
    make_draft("refund", "expense", account("5001"), None)


def test_fee_draft():
    make_draft("fee", "expense", account("5603"), None)


def test_unbalanced_or_invalid_lines_are_rejected_exactly():
    from internal_ledger.accounting.drafts import DraftLine, validate_balanced

    for lines in (
        [DraftLine("a", Decimal("10")), DraftLine("b", credit=Decimal("9.99"))],
        [DraftLine("a", Decimal("10")), DraftLine("b", credit=Decimal("9.999999999"))],
    ):
        with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "借贷不平"):
            validate_balanced(lines)
    for lines in (
        [], [DraftLine("a")],
        [DraftLine("a", Decimal("-1")), DraftLine("b", credit=Decimal("-1"))],
        [DraftLine("a", Decimal("1"), Decimal("1"))],
        [DraftLine("a", Decimal("NaN"))], [DraftLine("a", Decimal("Infinity"))],
        [DraftLine("", Decimal("1")), DraftLine("b", credit=Decimal("1"))],
    ):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            validate_balanced(lines)
    validate_balanced([DraftLine("a", Decimal("0.10")), DraftLine("a", Decimal("0.20")),
                       DraftLine("b", credit=Decimal("0.30"))])


def test_unmatched_conflict_and_forged_results_cannot_create_drafts():
    from internal_ledger.accounting.classifier import ClassificationResult, classify
    from internal_ledger.accounting.drafts import create_draft

    name = transaction()
    before = frappe.db.count("Voucher Draft")
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        create_draft(name, classify(name))
    add_rule()
    good = classify(name)
    for invalid in (
        ClassificationResult("conflict", None, None, None, "冲突"),
        replace(good, debit_account=account("560202")),
    ):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            create_draft(name, invalid)
    add_rule(debit_account=account("560202"))
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        create_draft(name, good)
    assert frappe.db.count("Voucher Draft") == before
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals


def test_accounts_must_be_real_active_leaf_links_in_company_and_valid_roots():
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft

    rule = add_rule()
    name = transaction()
    for field, invalid in (("payment_account", "库存现金"), ("payment_account", account("1002")),
                           ("payment_account", account("5001"))):
        frappe.db.set_value("Imported Transaction", name, field, invalid)
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            classify(name)
    frappe.db.set_value("Imported Transaction", name, "payment_account", account("1001"))
    for invalid in (account("5602"), account("5001")):
        rule.db_set("debit_account", invalid)
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            classify(name)
    rule.db_set("debit_account", account("560201"))
    result = classify(name)
    frappe.db.set_value("Account", account("560201"), "disabled", 1)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        create_draft(name, result)
    frappe.db.set_value("Account", account("560201"), {"disabled": 0, "company": "_Test Company"})
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        create_draft(name, result)
    assert frappe.db.count("Voucher Draft") == 0


def test_direction_mismatch_and_unmapped_payment_account_are_rejected():
    from internal_ledger.accounting.classifier import classify

    add_rule()
    add_rule(direction="income")
    for values in ({"direction": "income"}, {"payment_account": "微信余额"}):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            classify(transaction(**values))


def test_missing_account_is_rejected():
    from internal_ledger.accounting.drafts import validate_account

    for missing in (None, ""):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            validate_account(missing, frappe.flags.classification_company, {"Expense", "Asset"})


def test_draft_document_revalidates_manual_edits_and_stays_draft():
    draft = make_draft("expense", "expense", account("560201"), None)
    draft.lines[0].debit = 12.49
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "借贷不平"):
        draft.save()
    draft.reload()
    draft.status = "Reviewed"
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        draft.save()
    assert not draft.meta.is_submittable
    for doctype in ("Classification Rule", "Voucher Draft"):
        permissions = {row.role: row for row in frappe.get_meta(doctype).permissions}
        assert permissions["账套管理员"].write
        assert permissions["账套管理员"].create
        viewer = permissions["账务查看者"]
        assert viewer.read and not viewer.write and not viewer.create and not viewer.submit


def test_direct_insert_rejects_unmatched_and_conflicting_classification():
    original = make_draft("expense", "expense", account("560201"), None)
    unmatched = transaction(counterparty="未匹配商户")
    conflict = transaction(counterparty="冲突商户")
    add_rule(value="冲突商户")
    add_rule(value="冲突商户", debit_account=account("560202"))
    before = frappe.db.count("Voucher Draft")
    for name in (unmatched, conflict):
        forged = frappe.copy_doc(original)
        forged.imported_transaction = name
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            forged.insert()
    assert frappe.db.count("Voucher Draft") == before
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals


def test_save_rejects_another_valid_expense_account():
    draft = make_draft("expense", "expense", account("560201"), None)
    draft.lines[0].account = account("560202")
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        draft.save()
    assert frappe.get_doc("Voucher Draft", draft.name).lines[0].account == account("560201")


def test_save_rejects_tampered_rule_type_and_reason():
    draft = make_draft("expense", "expense", account("560201"), None)
    other = add_rule(value="不匹配商户")
    for field, value in (("classification_rule", other.name), ("transaction_type", "fee"),
                         ("classification_reason", "伪造原因")):
        draft.reload()
        draft.set(field, value)
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            draft.save()


def test_save_rejects_disabled_or_changed_rule():
    draft = make_draft("expense", "expense", account("560201"), None)
    rule = frappe.get_doc("Classification Rule", draft.classification_rule)
    rule.enabled = 0
    rule.save()
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        draft.save()
    rule.enabled = 1
    rule.debit_account = account("560202")
    rule.save()
    draft.reload()
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        draft.save()


def test_same_transaction_retry_is_rejected_without_another_draft():
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft

    draft = make_draft("expense", "expense", account("560201"), None)
    with unittest.TestCase().assertRaises(frappe.UniqueValidationError):
        create_draft(draft.imported_transaction, classify(draft.imported_transaction))
    assert frappe.db.count("Voucher Draft", {"imported_transaction": draft.imported_transaction}) == 1


def test_database_unique_index_rejects_direct_second_draft():
    draft = make_draft("expense", "expense", account("560201"), None)
    copied = frappe.copy_doc(draft)
    with unittest.TestCase().assertRaises(frappe.UniqueValidationError):
        copied.db_insert()
    assert frappe.db.count("Voucher Draft", {"imported_transaction": draft.imported_transaction}) == 1


def test_creation_belongs_to_callers_transaction():
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft

    add_rule()
    name = transaction()
    frappe.db.savepoint("draft_caller")
    draft_name = create_draft(name, classify(name))
    assert frappe.db.exists("Voucher Draft", draft_name)
    frappe.db.rollback(save_point="draft_caller")
    assert not frappe.db.exists("Voucher Draft", draft_name)
    assert frappe.db.exists("Imported Transaction", name)


def test_two_independent_connections_create_only_one_draft():
    from internal_ledger.accounting.classifier import classify
    from internal_ledger.accounting.drafts import create_draft
    from internal_ledger.install import install_chart
    from internal_ledger.tests.test_dedupe import tx
    from internal_ledger.tests.test_install import make_company

    site, sites_path = frappe.local.site, frappe.local.sites_path
    # Rollback-to-savepoint retains InnoDB locks from preceding company fixtures.
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
        batch = frappe.get_doc({"doctype": "Import Batch", "source": "wechat", "file_hash": uuid4().hex,
                                "company": company, "base_currency": "CNY"}).insert()
        from dataclasses import asdict

        row = tx(source_id=uuid4().hex, payment_account=account("1001"))
        imported = frappe.get_doc({"doctype": "Imported Transaction", **asdict(row),
                                   "import_batch": batch.name, "status": "Imported",
                                   "dedupe_key": "wechat:" + row.source_id}).insert()
        result = classify(imported.name)
        frappe.db.commit()
        return company, rule.name, batch.name, imported.name, result

    def insert_one():
        barrier.wait(timeout=20)
        try:
            name = create_draft(fixture[3], fixture[4])
            frappe.db.commit()
            return "inserted", name
        except frappe.UniqueValidationError:
            frappe.db.rollback()
            return "duplicate", None

    def verify_and_clean():
        names = frappe.get_all("Voucher Draft", filters={"imported_transaction": fixture[3]}, pluck="name")
        for name in names:
            frappe.db.delete("Voucher Draft Line", {"parent": name})
            frappe.db.delete("Voucher Draft", {"name": name})
        frappe.delete_doc("Classification Rule", fixture[1], force=True)
        frappe.delete_doc("Imported Transaction", fixture[3], force=True)
        frappe.delete_doc("Import Batch", fixture[2], force=True)
        frappe.delete_doc("Company", fixture[0], force=True)
        frappe.db.commit()
        return len(names)

    with ThreadPoolExecutor(max_workers=2) as pool:
        fixture = pool.submit(connected, prepare).result(timeout=60)
        try:
            futures = [pool.submit(connected, insert_one) for _ in range(2)]
            results = [future.result(timeout=60) for future in futures]
        finally:
            count = pool.submit(connected, verify_and_clean).result(timeout=60)
    assert sorted(status for status, name in results) == ["duplicate", "inserted"]
    assert count == 1


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=None if name == "test_two_independent_connections_create_only_one_draft" else set_up,
                                  tearDown=None if name == "test_two_independent_connections_create_only_one_draft" else tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_")
    )
