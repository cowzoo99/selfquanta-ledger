from dataclasses import replace
from decimal import Decimal
import unittest

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


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_")
    )
