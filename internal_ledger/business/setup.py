from uuid import uuid4

import frappe

from internal_ledger.permissions import MEMBER_MANAGER_ROLES, record_audit, require_ledger_admin


NEXT_STEPS = (
    ("添加客户", "/app/customer/new-customer"),
    ("添加供应商", "/app/supplier/new-supplier"),
    ("添加商品或服务", "/app/item/new-item"),
    ("核对仓库", "/app/warehouse"),
    ("录入期初余额", "/app/opening-invoice-creation-tool"),
)


def _configured_company(company):
    settings = frappe.get_single("Internal Ledger Settings")
    settings.check_permission("read")
    if not settings.company or company != settings.company:
        frappe.throw("只能初始化当前账套公司", frappe.PermissionError)
    return frappe.get_doc("Company", company)


def _require_setup_admin():
    if frappe.session.user != "Administrator" and not set(frappe.get_roles()) & MEMBER_MANAGER_ROLES:
        frappe.throw("需要企业管理员权限", frappe.PermissionError)


def _root(doctype, parent_field, filters=None):
    filters = {**(filters or {}), parent_field: ["is", "not set"], "is_group": 1}
    roots = frappe.get_all(doctype, filters=filters, pluck="name")
    if len(roots) != 1:
        frappe.throw(f"{doctype} 必须且只能有一个根节点")
    return roots[0]


def _expense_specs():
    if not frappe.db.table_exists("Expense Claim Type"):
        return [], 1
    meta = frappe.get_meta("Expense Claim Type")
    if meta.has_field("expense_type"):
        field = "expense_type"
    elif meta.has_field("expense_claim_type"):
        field = "expense_claim_type"
    else:
        frappe.throw("Expense Claim Type 缺少费用类型字段")
    return [
        _spec(f"expense_{index}", "Expense Claim Type", {field: name}, {field: name}, (field,))
        for index, name in enumerate(("差旅费", "办公费"), 1)
    ], 0


def _spec(key, doctype, lookup, values, compare):
    return {"key": key, "doctype": doctype, "lookup": lookup, "values": values,
            "compare": compare, "name": next(iter(lookup.values()))}


def _specs(company, business_type, options):
    currency = frappe.db.get_value("Company", company, "default_currency") or "CNY"
    customer_root = _root("Customer Group", "parent_customer_group")
    supplier_root = _root("Supplier Group", "parent_supplier_group")
    item_root = _root("Item Group", "parent_item_group")
    cost_root = _root("Cost Center", "parent_cost_center", {"company": company})
    specs = [
        _spec("selling_price_list", "Price List", {"name": "标准销售价"},
              {"price_list_name": "标准销售价", "selling": 1, "buying": 0,
               "currency": currency, "enabled": 1}, ("selling", "buying", "currency")),
        _spec("buying_price_list", "Price List", {"name": "标准采购价"},
              {"price_list_name": "标准采购价", "selling": 0, "buying": 1,
               "currency": currency, "enabled": 1}, ("selling", "buying", "currency")),
        _spec("customer_group", "Customer Group", {"name": "普通客户"},
              {"customer_group_name": "普通客户", "parent_customer_group": customer_root,
               "is_group": 0}, ("parent_customer_group", "is_group")),
        _spec("supplier_group", "Supplier Group", {"name": "普通供应商"},
              {"supplier_group_name": "普通供应商", "parent_supplier_group": supplier_root,
               "is_group": 0}, ("parent_supplier_group", "is_group")),
        _spec("service_item_group", "Item Group", {"name": "服务"},
              {"item_group_name": "服务", "parent_item_group": item_root,
               "is_group": 0}, ("parent_item_group", "is_group")),
        _spec("cost_center", "Cost Center", {"cost_center_name": "主成本中心", "company": company},
              {"cost_center_name": "主成本中心", "parent_cost_center": cost_root,
               "company": company, "is_group": 0}, ("parent_cost_center", "company", "is_group")),
        _spec("bank_payment", "Mode of Payment", {"name": "银行转账"},
              {"mode_of_payment": "银行转账", "type": "Bank", "enabled": 1}, ("type",)),
        _spec("customer_project", "Project Type", {"name": "客户项目"},
              {"project_type": "客户项目"}, ("project_type",)),
    ]
    inventory_enabled = options.get("inventory_enabled", business_type == "trade")
    if inventory_enabled:
        warehouse_root = _root("Warehouse", "parent_warehouse", {"company": company})
        specs[5:5] = [
            _spec("goods_item_group", "Item Group", {"name": "商品"},
                  {"item_group_name": "商品", "parent_item_group": item_root,
                   "is_group": 0}, ("parent_item_group", "is_group")),
            _spec("main_warehouse", "Warehouse", {"warehouse_name": "主仓库", "company": company},
                  {"warehouse_name": "主仓库", "parent_warehouse": warehouse_root,
                   "company": company, "is_group": 0}, ("parent_warehouse", "company", "is_group")),
        ]
    expenses, unavailable = _expense_specs()
    return specs + expenses, unavailable


def _existing(spec):
    name = frappe.db.get_value(spec["doctype"], spec["lookup"], "name")
    return frappe.get_doc(spec["doctype"], name) if name else None


def _conflict(spec, doc):
    for field in spec["compare"]:
        expected = spec["values"].get(field)
        actual = doc.get(field)
        if isinstance(expected, int):
            actual = int(actual or 0)
        if actual != expected:
            if spec["doctype"] == "Price List":
                return "同名价格表的销售/采购类型或币种不一致"
            return f"同名 {spec['doctype']} 的公司或类型不一致"
    return None


def _inspect(spec):
    doc = _existing(spec)
    if not doc:
        return "missing", None
    message = _conflict(spec, doc)
    return ("conflict", message) if message else ("skipped", None)


def _summary(items, unavailable):
    return {status: sum(row["status"] == status for row in items)
            for status in ("created", "skipped", "conflict")} | {"unavailable": unavailable}


def _serialize_item(spec, status, message=None):
    return {"key": spec["key"], "doctype": spec["doctype"], "name": spec["name"],
            "status": status, "message": message or ""}


def get_setup_status(company: str):
    _configured_company(company)
    inventory_enabled = bool(frappe.db.exists(
        "Warehouse", {"warehouse_name": "主仓库", "company": company}
    ))
    business_type = "trade" if inventory_enabled else "service"
    specs, unavailable = _specs(company, business_type, {"inventory_enabled": inventory_enabled})
    items = []
    for spec in specs:
        status, message = _inspect(spec)
        items.append(_serialize_item(spec, status, message))
    return {"company": company, "complete": all(row["status"] == "skipped" for row in items),
            "summary": _summary(items, unavailable), "items": items, "unavailable": unavailable}


def apply_business_setup(company: str, business_type: str, options: dict):
    _require_setup_admin()
    _configured_company(company)
    if business_type not in {"trade", "service"}:
        frappe.throw("企业类型仅支持 trade 或 service")
    if not isinstance(options, dict) or set(options) - {"inventory_enabled"}:
        frappe.throw("初始化选项无效")
    if "inventory_enabled" in options and not isinstance(options["inventory_enabled"], bool):
        frappe.throw("inventory_enabled 必须是布尔值")
    specs, unavailable = _specs(company, business_type, options)
    inspected = [(spec, *_inspect(spec)) for spec in specs]
    conflicts = [_serialize_item(spec, status, message)
                 for spec, status, message in inspected if status == "conflict"]
    if conflicts:
        summary = _summary(conflicts, unavailable)
        record_audit("apply_business_setup", "Internal Ledger Settings", "Internal Ledger Settings",
                     ";".join(f"{key}:{value}" for key, value in summary.items()))
        return {"company": company, "business_type": business_type, "status": "conflict",
                "summary": summary, "items": conflicts, "next_steps": []}

    savepoint = "business_setup_" + uuid4().hex
    frappe.db.savepoint(savepoint)
    items = []
    actor = frappe.session.user
    try:
        try:
            frappe.set_user("Administrator")
            for spec, status, _ in inspected:
                if status == "missing":
                    frappe.get_doc({"doctype": spec["doctype"], **spec["values"]}).insert(ignore_permissions=True)
                    status = "created"
                items.append(_serialize_item(spec, status))
        finally:
            frappe.set_user(actor)
        summary = _summary(items, unavailable)
        record_audit("apply_business_setup", "Internal Ledger Settings", "Internal Ledger Settings",
                     ";".join(f"{key}:{value}" for key, value in summary.items()))
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        raise
    frappe.db.release_savepoint(savepoint)
    return {"company": company, "business_type": business_type, "status": "ready",
            "summary": summary, "items": items,
            "next_steps": [{"label": label, "target_route": route} for label, route in NEXT_STEPS]}
