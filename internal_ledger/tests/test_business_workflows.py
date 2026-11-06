import unittest
from uuid import uuid4

import frappe
from frappe.utils import add_days, nowdate

from internal_ledger.tests.test_review_api import set_up, tear_down as classification_tear_down


def _delete_test_company():
    company = "_Test Internal Ledger Chart"
    if not frappe.db.exists("Company", company):
        return
    from erpnext.setup.doctype.transaction_deletion_record.test_transaction_deletion_record import (
        create_and_submit_transaction_deletion_doc,
    )
    create_and_submit_transaction_deletion_doc(company)
    frappe.delete_doc("Company", company, force=True, ignore_permissions=True)


def tear_down():
    try:
        classification_tear_down()
    except Exception as exc:
        if "SAVEPOINT classification_test does not exist" not in str(exc):
            raise
        _delete_test_company()
        frappe.db.commit()
        frappe.clear_cache()


def account(number):
    return frappe.db.get_value("Account", {
        "company": frappe.flags.classification_company, "account_number": number,
    }, "name")


def trade_fixture():
    from internal_ledger.api.setup_api import apply_business_setup

    company = frappe.flags.classification_company
    apply_business_setup(company, "trade", {})
    accounts = {number: account(number) for number in ("100201", "1122", "1405", "2202", "5001", "5401", "560201")}
    for number, account_type in {"100201": "Bank", "1122": "Receivable", "1405": "Stock",
                                  "2202": "Payable", "5001": "Income Account",
                                  "5401": "Cost of Goods Sold"}.items():
        frappe.db.set_value("Account", accounts[number], "account_type", account_type)
    company_doc = frappe.get_doc("Company", company)
    for field, value in {
        "default_bank_account": accounts["100201"], "default_receivable_account": accounts["1122"],
        "default_payable_account": accounts["2202"], "default_income_account": accounts["5001"],
        "default_expense_account": accounts["5401"], "default_inventory_account": accounts["1405"],
        "stock_adjustment_account": accounts["560201"], "default_currency": "CNY",
    }.items():
        if company_doc.meta.has_field(field):
            company_doc.set(field, value)
    company_doc.save()
    warehouse = frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": "主仓库"}, "name")
    frappe.db.set_value("Warehouse", warehouse, "account", accounts["1405"])
    cost_center = frappe.db.get_value("Cost Center", {"company": company, "cost_center_name": "主成本中心"}, "name")
    customer_name = "验收客户-" + uuid4().hex[:8]
    supplier_name = "验收供应商-" + uuid4().hex[:8]
    customer = frappe.get_doc({"doctype": "Customer", "customer_name": customer_name,
        "customer_type": "Company", "customer_group": "普通客户", "territory": "All Territories"}).insert()
    supplier = frappe.get_doc({"doctype": "Supplier", "supplier_name": supplier_name,
        "supplier_type": "Company", "supplier_group": "普通供应商"}).insert()
    stock_code, service_code = "验收商品-" + uuid4().hex[:8], "验收服务-" + uuid4().hex[:8]
    stock_item = frappe.get_doc({"doctype": "Item", "item_code": stock_code, "item_name": stock_code,
        "item_group": "商品", "stock_uom": "Nos", "is_stock_item": 1,
        "item_defaults": [{"company": company, "default_warehouse": warehouse,
            "income_account": accounts["5001"], "expense_account": accounts["5401"]}]}).insert()
    service_item = frappe.get_doc({"doctype": "Item", "item_code": service_code, "item_name": service_code,
        "item_group": "服务", "stock_uom": "Nos", "is_stock_item": 0,
        "item_defaults": [{"company": company, "income_account": accounts["5001"],
            "expense_account": accounts["5401"]}]}).insert()
    opening = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt",
        "company": company, "posting_date": "2026-10-01", "set_posting_time": 1,
        "items": [{"item_code": stock_item.name, "t_warehouse": warehouse, "qty": 10,
            "basic_rate": 20, "cost_center": cost_center,
            "expense_account": accounts["560201"]}]}).insert()
    opening.submit()
    return frappe._dict(company=company, accounts=accounts, warehouse=warehouse, cost_center=cost_center,
        customer=customer.name, supplier=supplier.name, stock_item=stock_item.name,
        service_item=service_item.name, opening=opening.name)


def test_lead_opportunity_customer_conversion_and_role_boundary():
    from erpnext.crm.doctype.lead.lead import make_opportunity
    from erpnext.crm.doctype.opportunity.opportunity import make_customer

    fixture = trade_fixture()
    lead = frappe.get_doc({"doctype": "Lead", "first_name": "验收线索", "company_name": "验收潜客",
                           "company": fixture.company, "status": "Lead"}).insert()
    opportunity = make_opportunity(lead.name)
    opportunity.company = fixture.company
    opportunity.opportunity_type = "Sales"
    opportunity.insert()
    customer = make_customer(opportunity.name)
    customer.customer_group = "普通客户"
    customer.territory = "All Territories"
    customer.insert()
    assert opportunity.opportunity_from == "Lead" and opportunity.party_name == lead.name
    assert customer.opportunity_name == opportunity.name

    email = f"sales-{uuid4().hex}@example.com"
    frappe.get_doc({"doctype": "User", "email": email, "first_name": "Sales", "send_welcome_email": 0,
                    "roles": [{"role": "销售"}, {"role": "Sales User"}]}).insert()
    assert frappe.has_permission("Lead", "create", user=email)
    assert not frappe.has_permission("Purchase Order", "create", user=email)


def test_sales_order_delivery_invoice_payment_updates_stock_and_ledger():
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

    fixture = trade_fixture()
    before = frappe.db.get_value("Bin", {"item_code": fixture.stock_item, "warehouse": fixture.warehouse}, "actual_qty")
    order = make_sales_order(company=fixture.company, customer=fixture.customer, item=fixture.stock_item,
        warehouse=fixture.warehouse, qty=2, rate=100, currency="CNY", selling_price_list="标准销售价")
    delivery = make_delivery_note(order.name)
    delivery.insert().submit()
    invoice = make_sales_invoice(delivery.name)
    invoice.insert().submit()
    payment = get_payment_entry("Sales Invoice", invoice.name, bank_account=fixture.accounts["100201"])
    payment.reference_no = "验收销售收款-" + uuid4().hex[:8]
    payment.reference_date = nowdate()
    payment.insert().submit()
    assert frappe.db.get_value("Bin", {"item_code": fixture.stock_item,
        "warehouse": fixture.warehouse}, "actual_qty") == before - 2
    assert frappe.db.count("Stock Ledger Entry", {"voucher_type": "Delivery Note", "voucher_no": delivery.name}) == 1
    assert frappe.db.count("GL Entry", {"voucher_no": invoice.name, "is_cancelled": 0}) >= 2
    assert frappe.db.count("GL Entry", {"voucher_no": payment.name, "is_cancelled": 0}) >= 2
    assert frappe.db.get_value("Sales Invoice", invoice.name, "outstanding_amount") == 0


def test_purchase_order_receipt_invoice_payment_updates_stock_and_ledger():
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

    fixture = trade_fixture()
    before = frappe.db.get_value("Bin", {"item_code": fixture.stock_item, "warehouse": fixture.warehouse}, "actual_qty")
    order = frappe.get_doc({"doctype": "Purchase Order", "company": fixture.company,
        "supplier": fixture.supplier, "currency": "CNY", "conversion_rate": 1,
        "schedule_date": add_days(nowdate(), 7), "items": [{"item_code": fixture.stock_item,
        "warehouse": fixture.warehouse, "qty": 3, "rate": 50, "schedule_date": add_days(nowdate(), 7)}]}).insert()
    order.submit()
    receipt = make_purchase_receipt(order.name)
    receipt.insert().submit()
    invoice = make_purchase_invoice(receipt.name)
    invoice.insert().submit()
    payment = get_payment_entry("Purchase Invoice", invoice.name, bank_account=fixture.accounts["100201"])
    payment.reference_no = "验收采购付款-" + uuid4().hex[:8]
    payment.reference_date = nowdate()
    payment.insert().submit()
    assert frappe.db.get_value("Bin", {"item_code": fixture.stock_item,
        "warehouse": fixture.warehouse}, "actual_qty") == before + 3
    assert frappe.db.count("Stock Ledger Entry", {"voucher_type": "Purchase Receipt", "voucher_no": receipt.name}) == 1
    assert frappe.db.count("GL Entry", {"voucher_no": invoice.name, "is_cancelled": 0}) >= 2
    assert frappe.db.count("GL Entry", {"voucher_no": payment.name, "is_cancelled": 0}) >= 2
    assert frappe.db.get_value("Purchase Invoice", invoice.name, "outstanding_amount") == 0


def test_negative_stock_is_blocked_with_an_actionable_message():
    from frappe.translate import clear_cache

    fixture = trade_fixture()
    frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 0)
    entry = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Issue",
        "company": fixture.company, "items": [{"item_code": fixture.stock_item,
        "s_warehouse": fixture.warehouse, "qty": 999, "basic_rate": 20,
        "cost_center": fixture.cost_center, "expense_account": fixture.accounts["560201"]}]}).insert()
    original_language = frappe.local.lang
    job = getattr(frappe.local, "job", None)
    original_job_language_state = job.lang_resolved if job else None
    clear_cache()
    frappe.local.lang = "zh"
    if job:
        job.lang_resolved = True
    try:
        with unittest.TestCase().assertRaises(Exception) as raised:
            entry.submit()
    finally:
        frappe.local.lang = original_language
        if job:
            job.lang_resolved = original_job_language_state
    message = str(raised.exception)
    assert fixture.stock_item in message and "还缺" in message


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_"))
