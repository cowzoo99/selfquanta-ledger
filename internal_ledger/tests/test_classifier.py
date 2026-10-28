import unittest
from uuid import uuid4

import frappe

from internal_ledger.importing.models import save_batch
from internal_ledger.install import install_chart
from internal_ledger.tests.test_dedupe import tx
from internal_ledger.tests.test_install import make_company


def set_up():
    frappe.db.savepoint("classification_test")
    company = make_company()
    install_chart(company)
    settings = frappe.get_single("Internal Ledger Settings")
    settings.company = company
    settings.base_currency = "CNY"
    settings.fiscal_year_start = "2026-01-01"
    settings.save()
    frappe.flags.classification_company = company
    frappe.flags.classification_journals = frappe.db.count("Journal Entry")


def tear_down():
    frappe.db.rollback(save_point="classification_test")
    frappe.clear_cache()


def account(number):
    return frappe.db.get_value("Account", {
        "company": frappe.flags.classification_company, "account_number": number,
    }, "name")


def transaction(**values):
    values.setdefault("source_id", uuid4().hex)
    values.setdefault("payment_account", account("1001"))
    batch = save_batch("wechat", uuid4().hex, [tx(**values)])
    return frappe.db.get_value("Imported Transaction", {"import_batch": batch}, "name")


def add_rule(**values):
    fields = dict(
        company=frappe.flags.classification_company, kind="counterparty", value="测试商户",
        transaction_type="expense", direction="expense", debit_account=account("560201"), enabled=1,
    )
    fields.update(values)
    return frappe.get_doc({"doctype": "Classification Rule", **fields}).insert()


def test_exact_counterparty_beats_keyword_regardless_of_priority():
    from internal_ledger.accounting.classifier import classify

    add_rule(kind="keyword", value="打车", debit_account=account("560202"), priority=999)
    rule = add_rule(priority=1)
    result = classify(transaction(summary="打车"))
    assert result.status == "matched"
    assert (result.debit_account, result.credit_account) == (account("560201"), account("1001"))
    assert result.rule_name == rule.name
    assert result.reason == "交易对象精确匹配"


def test_highest_layer_conflict_cannot_be_hidden_by_priority():
    from internal_ledger.accounting.classifier import classify

    add_rule(priority=999)
    add_rule(debit_account=account("560202"), priority=1)
    add_rule(kind="keyword", value="办公", priority=1000)
    result = classify(transaction())
    assert result.status == "conflict"
    assert result.reason == "同一最高匹配层存在不同科目或业务类型"
    assert (result.debit_account, result.credit_account, result.rule_name) == (None, None, None)


def test_history_requires_confirmation_and_keyword_beats_history():
    from internal_ledger.accounting.classifier import classify

    history = add_rule(kind="confirmed_history", confirmed=0)
    name = transaction()
    assert classify(name).status == "unmatched"
    history.confirmed = 1
    history.save()
    assert classify(name).reason == "已确认历史匹配"
    add_rule(kind="keyword", value="办公", debit_account=account("560202"))
    assert classify(name).reason == "摘要关键词匹配"
    assert classify(name).debit_account == account("560202")


def test_unmatched_ignores_disabled_foreign_and_partial_counterparty():
    from internal_ledger.accounting.classifier import classify

    add_rule(enabled=0)
    add_rule(value="测试")
    foreign = add_rule()
    foreign.db_set("company", "_Test Company")
    result = classify(transaction())
    assert result.status == "unmatched"
    assert result.reason == "未匹配分类规则"
    assert (result.debit_account, result.credit_account, result.rule_name) == (None, None, None)


def test_same_accounts_have_stable_rule_selection():
    from internal_ledger.accounting.classifier import classify

    rules = [add_rule(), add_rule()]
    name = transaction()
    first = classify(name)
    assert first.status == "matched"
    assert first.rule_name == min(rule.name for rule in rules)
    assert classify(name) == first


def test_different_business_types_conflict_even_with_same_accounts():
    from internal_ledger.accounting.classifier import classify

    add_rule()
    add_rule(transaction_type="fee")
    assert classify(transaction()).status == "conflict"


def test_duplicate_transaction_is_not_classified():
    from internal_ledger.accounting.classifier import classify

    name = transaction()
    frappe.db.set_value("Imported Transaction", name, "status", "Duplicate")
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        classify(name)


def test_different_explicit_funding_accounts_are_also_a_conflict():
    from internal_ledger.accounting.classifier import classify

    add_rule(credit_account=account("1001"), priority=999)
    add_rule(credit_account=account("100201"), priority=1)
    result = classify(transaction())
    assert result.status == "conflict"
    assert (result.debit_account, result.credit_account, result.rule_name) == (None, None, None)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_")
    )
