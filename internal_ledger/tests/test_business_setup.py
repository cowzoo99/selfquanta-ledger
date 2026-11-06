import unittest

import frappe

from internal_ledger.tests.test_permissions import user
from internal_ledger.tests.test_review_api import set_up, tear_down


EXPECTED_GLOBAL = {
    "Price List": ("标准销售价", "标准采购价"),
    "Customer Group": ("普通客户",),
    "Supplier Group": ("普通供应商",),
    "Item Group": ("商品", "服务"),
    "Mode of Payment": ("银行转账",),
    "Project Type": ("客户项目",),
}


def test_trade_setup_creates_exact_standard_records_and_is_idempotent():
    from internal_ledger.api.setup_api import apply_business_setup, get_setup_status

    company = frappe.flags.classification_company
    first = apply_business_setup(company, "trade", {})
    assert first["status"] == "ready"
    expense_available = frappe.db.table_exists("Expense Claim Type")
    assert first["summary"] == {"created": 12 if expense_available else 10, "skipped": 0,
                                "conflict": 0, "unavailable": 0 if expense_available else 1}
    assert [row["label"] for row in first["next_steps"]] == [
        "添加客户", "添加供应商", "添加商品或服务", "核对仓库", "录入期初余额",
    ]
    for doctype, names in EXPECTED_GLOBAL.items():
        assert all(frappe.db.exists(doctype, name) for name in names)
    assert frappe.db.exists("Warehouse", {"warehouse_name": "主仓库", "company": company})
    assert frappe.db.exists("Cost Center", {"cost_center_name": "主成本中心", "company": company})
    assert frappe.db.get_value("Price List", "标准销售价", ["selling", "buying", "currency"]) == (1, 0, "CNY")
    assert frappe.db.get_value("Price List", "标准采购价", ["selling", "buying", "currency"]) == (0, 1, "CNY")
    assert frappe.db.get_value("Mode of Payment", "银行转账", "type") == "Bank"
    if expense_available:
        assert all(frappe.db.exists("Expense Claim Type", name) for name in ("差旅费", "办公费"))

    second = apply_business_setup(company, "trade", {})
    assert second["status"] == "ready"
    assert second["summary"] == {"created": 0, "skipped": 12 if expense_available else 10,
                                 "conflict": 0, "unavailable": 0 if expense_available else 1}
    assert get_setup_status(company)["complete"] is True
    assert frappe.db.count("Internal Ledger Audit Event", {
        "action": "apply_business_setup", "reference_name": "Internal Ledger Settings",
    }) == 2


def test_service_setup_omits_inventory_defaults_unless_explicitly_enabled():
    from internal_ledger.api.setup_api import apply_business_setup, get_setup_status

    company = frappe.flags.classification_company
    result = apply_business_setup(company, "service", {})
    assert result["status"] == "ready"
    assert not frappe.db.exists("Item Group", "商品")
    assert not frappe.db.exists("Warehouse", {"warehouse_name": "主仓库", "company": company})
    assert frappe.db.exists("Item Group", "服务")
    assert result["summary"]["created"] == (10 if frappe.db.table_exists("Expense Claim Type") else 8)
    assert get_setup_status(company)["complete"] is True


def test_conflict_is_reported_before_any_business_default_is_created():
    from internal_ledger.api.setup_api import apply_business_setup

    company = frappe.flags.classification_company
    frappe.get_doc({"doctype": "Price List", "price_list_name": "标准销售价",
                    "selling": 0, "buying": 1, "currency": "CNY", "enabled": 1}).insert()
    result = apply_business_setup(company, "trade", {})
    assert result["status"] == "conflict"
    assert result["summary"] == {"created": 0, "skipped": 0, "conflict": 1,
                                 "unavailable": 0 if frappe.db.table_exists("Expense Claim Type") else 1}
    assert result["items"] == [{
        "key": "selling_price_list", "doctype": "Price List", "name": "标准销售价",
        "status": "conflict", "message": "同名价格表的销售/采购类型或币种不一致",
    }]
    assert not frappe.db.exists("Customer Group", "普通客户")
    assert not frappe.db.exists("Supplier Group", "普通供应商")
    assert not frappe.db.exists("Warehouse", {"warehouse_name": "主仓库", "company": company})
    event = frappe.get_doc("Internal Ledger Audit Event", {
        "action": "apply_business_setup", "reference_name": "Internal Ledger Settings",
    })
    assert event.result == ("created:0;skipped:0;conflict:1;unavailable:0"
                            if frappe.db.table_exists("Expense Claim Type") else
                            "created:0;skipped:0;conflict:1;unavailable:1")


def test_company_scope_input_and_business_options_are_strict():
    from internal_ledger.api.setup_api import apply_business_setup, get_setup_status

    company = frappe.flags.classification_company
    for operation in (
        lambda: get_setup_status("_Test Company"),
        lambda: apply_business_setup("_Test Company", "trade", {}),
        lambda: apply_business_setup(company, "unknown", {}),
        lambda: apply_business_setup(company, "trade", {"unknown": True}),
        lambda: apply_business_setup(company, "trade", {"inventory_enabled": "yes"}),
    ):
        with unittest.TestCase().assertRaises((frappe.PermissionError, frappe.ValidationError)):
            operation()


def test_only_enterprise_administrators_can_apply_setup():
    from internal_ledger.api.setup_api import apply_business_setup, get_setup_status

    company = frappe.flags.classification_company
    actors = [(role, user(role)) for role in ("账务查看者", "会计", "出纳", "System Manager")]
    manager = user("账套管理员")
    for role, actor in actors[:3]:
        frappe.set_user(actor)
        assert get_setup_status(company)["company"] == company
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            apply_business_setup(company, "trade", {})
    frappe.set_user(actors[3][1])
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        get_setup_status(company)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        apply_business_setup(company, "trade", {})
    frappe.set_user("Guest")
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        get_setup_status(company)
    frappe.set_user(manager)
    assert apply_business_setup(company, "trade", {})["status"] == "ready"


def load_tests(loader, tests, pattern):
    def business_set_up():
        frappe.set_user("Administrator")
        set_up()
        from internal_ledger.install import ensure_erpnext_setup_fixtures
        ensure_erpnext_setup_fixtures()

    def business_tear_down():
        frappe.set_user("Administrator")
        tear_down()

    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=business_set_up, tearDown=business_tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_"))
