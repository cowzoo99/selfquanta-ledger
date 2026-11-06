import unittest

import frappe

from internal_ledger.install import ensure_roles, initialize_company, install_chart, load_chart_fixture


def test_install_creates_roles_and_balanced_root_groups():
    for role in ("账套管理员", "账务查看者"):
        if frappe.db.exists("Role", role):
            frappe.delete_doc("Role", role, force=True)
    ensure_roles()
    assert frappe.db.exists("Role", "账套管理员")
    assert frappe.db.exists("Role", "账务查看者")
    chart = load_chart_fixture()
    assert set(chart) == {"资产", "负债", "所有者权益", "成本", "损益"}
    ensure_roles()
    assert frappe.db.count("Role", {"role_name": ["in", ["账套管理员", "账务查看者"]]}) == 2


def test_product_role_presets_use_the_exact_least_privilege_matrix():
    from internal_ledger.install import ROLE_PRESETS

    assert ROLE_PRESETS == {
        "企业负责人": ("企业负责人", "Accounts Manager", "Sales Manager", "Purchase Manager",
                   "Stock Manager", "Projects Manager", "HR Manager"),
        "财务主管": ("财务主管", "Accounts Manager"),
        "会计": ("会计", "Accounts User"),
        "出纳": ("出纳", "Accounts User"),
        "销售": ("销售", "Sales User"),
        "采购": ("采购", "Purchase User"),
        "库存": ("库存", "Stock User"),
        "项目": ("项目", "Projects User"),
        "人事工资": ("人事工资", "HR User"),
        "只读审计": ("只读审计", "账务查看者"),
    }
    ensure_roles()
    assert all(frappe.db.exists("Role", role) for role in ROLE_PRESETS)
    assert all("System Manager" not in roles for roles in ROLE_PRESETS.values())


def test_install_enables_account_and_mobile_login():
    frappe.db.set_single_value("System Settings", "allow_login_using_user_name", 0)
    frappe.db.set_single_value("System Settings", "allow_login_using_mobile_number", 0)
    ensure_roles()
    settings = frappe.get_single("System Settings")
    assert settings.allow_login_using_user_name == 1
    assert settings.allow_login_using_mobile_number == 1


def test_chart_has_unique_codes_and_valid_account_types():
    codes = []
    roots = {"资产": {"Asset"}, "负债": {"Liability"}, "所有者权益": {"Equity"},
             "成本": {"Expense"}, "损益": {"Income", "Expense"}}
    for category, accounts in load_chart_fixture().items():
        for account in accounts:
            codes.append(account["account_number"])
            assert account["root_type"] in roots[category]
            expected_report = "Balance Sheet" if category in {"资产", "负债", "所有者权益"} else "Profit and Loss"
            assert account["report_type"] == expected_report
    assert len(codes) == len(set(codes))
    assert {"1001", "1002", "1122", "2202", "3001", "4001", "5001", "5401", "5602"} <= set(codes)


def make_company():
    if not frappe.db.exists("Warehouse Type", "Transit"):
        frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert()
    return frappe.get_doc({
        "doctype": "Company", "company_name": "_Test Internal Ledger Chart",
        "abbr": "ILTC", "default_currency": "CNY", "country": "China",
        "create_chart_of_accounts_based_on": "Standard Template", "chart_of_accounts": "Standard",
    }).insert().name


def test_install_chart_creates_accounts_and_is_idempotent():
    company = make_company()
    install_chart(company)
    before = frappe.get_all("Account", filters={"company": company}, fields=["*"])
    install_chart(company)
    after = frappe.get_all("Account", filters={"company": company}, fields=["*"])
    assert sorted(before, key=lambda account: account.name) == sorted(after, key=lambda account: account.name)
    for accounts in load_chart_fixture().values():
        for expected in accounts:
            actual = frappe.get_doc("Account", {"company": company, "account_number": expected["account_number"]})
            for field in ("account_name", "root_type", "report_type"):
                assert actual.get(field) == expected[field]
            assert actual.is_group == expected.get("is_group", 0)
            parent = frappe.get_doc("Account", actual.parent_account)
            assert parent.root_type == actual.root_type
            if expected.get("parent_account_number"):
                assert parent.account_number == expected["parent_account_number"]
    assert frappe.db.get_value("Account", {"company": company, "account_number": "5001"}, "root_type") == "Income"
    assert frappe.db.get_value("Account", {"company": company, "account_number": "5401"}, "root_type") == "Expense"


def test_install_chart_rejects_existing_conflict_without_overwriting():
    company = make_company()
    parent = frappe.db.get_value("Account", {"company": company, "root_type": "Asset", "parent_account": ["is", "not set"]}, "name")
    existing = frappe.get_doc({"doctype": "Account", "company": company, "parent_account": parent,
                              "account_number": "1001", "account_name": "Existing custom cash"}).insert()
    original = existing.as_dict()
    try:
        install_chart(company)
    except frappe.ValidationError:
        pass
    else:
        raise AssertionError("Conflicting existing account must be rejected")
    assert frappe.get_doc("Account", existing.name).as_dict() == original


def test_settings_is_singleton_with_company_currency_and_year_start():
    company = make_company()
    settings = frappe.get_single("Internal Ledger Settings")
    assert settings.meta.issingle
    settings.company = company
    settings.base_currency = "CNY"
    settings.fiscal_year_start = "2026-01-01"
    settings.save()
    saved = frappe.get_single("Internal Ledger Settings")
    assert saved.company == company
    assert saved.base_currency == "CNY"
    assert str(saved.fiscal_year_start) == "2026-01-01"


def test_initialize_company_rejects_invalid_or_second_company_without_mutation():
    make_company()
    before = frappe.get_all("Company", pluck="name")
    for name, abbr in (("", "甲"), ("甲公司", ""), ("甲公司", "超过五个字符"), ("另一个客户公司", "另一")):
        try:
            initialize_company(name, abbr)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Invalid or second company must be rejected")
    assert frappe.get_all("Company", pluck="name") == before


def load_tests(loader, tests, pattern):
    def set_up():
        frappe.db.savepoint("internal_ledger_install_test")

    def tear_down():
        frappe.db.rollback(save_point="internal_ledger_install_test")
        frappe.clear_cache()

    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for test in (
            test_install_creates_roles_and_balanced_root_groups,
            test_product_role_presets_use_the_exact_least_privilege_matrix,
            test_install_enables_account_and_mobile_login,
            test_chart_has_unique_codes_and_valid_account_types,
            test_install_chart_creates_accounts_and_is_idempotent,
            test_install_chart_rejects_existing_conflict_without_overwriting,
            test_settings_is_singleton_with_company_currency_and_year_start,
            test_initialize_company_rejects_invalid_or_second_company_without_mutation,
        )
    )
