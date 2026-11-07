from decimal import Decimal

import frappe
from frappe.utils import getdate


ALLOWED_ROUTES = {
    "/app/sales-order", "/app/sales-invoice", "/app/purchase-order", "/app/purchase-invoice",
    "/app/account", "/app/stock-balance", "/app/expense-claim", "/app/project", "/app/task",
    "/app/leave-application", "/app/payroll-entry", "/app/salary-slip",
    "/app/ledger-workbench/month-end",
}
EXECUTIVE_ROLES = {"账套管理员", "企业负责人"}
FINANCE_ROLES = {"财务主管", "会计", "出纳", "账务查看者", "只读审计",
                 "Accounts Manager", "Accounts User"}
SECTION_ROLES = {
    "sales": {"销售", "Sales User", "Sales Manager"},
    "receivables": {"销售", "Sales User", "Sales Manager"} | FINANCE_ROLES,
    "purchases": {"采购", "Purchase User", "Purchase Manager"},
    "payables": {"采购", "Purchase User", "Purchase Manager"} | FINANCE_ROLES,
    "cash": FINANCE_ROLES,
    "low_stock": {"库存", "Stock User", "Stock Manager"},
    "expenses": FINANCE_ROLES,
    "projects": {"项目", "Projects User", "Projects Manager"},
    "hr": {"人事工资", "HR User", "HR Manager"} | FINANCE_ROLES,
    "month_end": FINANCE_ROLES,
}


def _company(company):
    configured = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not configured or company != configured:
        frappe.throw("只能查看当前账套公司", frappe.PermissionError)
    if frappe.session.user == "Guest":
        frappe.throw("请先登录", frappe.PermissionError)
    return configured


def _number(value):
    value = Decimal(str(value or 0))
    return int(value) if value == value.to_integral() else float(value)


def _installed(doctype):
    return frappe.db.table_exists(doctype)


def _roles():
    return {row[0] for row in frappe.db.sql(
        "select role from `tabHas Role` where parent = %s and parenttype = 'User'", frappe.session.user
    )}


def _card(label, value, unit, severity, route, as_of, source):
    if route not in ALLOWED_ROUTES:
        frappe.throw("经营首页跳转地址不在允许范围")
    return {"label": label, "value": _number(value), "unit": unit, "severity": severity,
            "target_route": route, "as_of": str(as_of),
            "tooltip": f"数据来源：{source}；更新至 {as_of}"}


def _aggregate(doctype, company, date_field, from_date, to_date, amount_field):
    if doctype in {"Expense Claim", "Salary Slip"} and not _installed(doctype):
        return None
    allowed = {
        "Sales Order": ("transaction_date", "grand_total"),
        "Sales Invoice": ("posting_date", "outstanding_amount"),
        "Purchase Order": ("transaction_date", "grand_total"),
        "Purchase Invoice": ("posting_date", "outstanding_amount"),
        "Expense Claim": ("posting_date", "total_claimed_amount"),
        "Salary Slip": ("posting_date", "gross_pay"),
    }
    if allowed.get(doctype) != (date_field, amount_field):
        frappe.throw("经营指标字段不在允许范围")
    return frappe.db.sql(
        f"select count(name) as count, coalesce(sum(`{amount_field}`), 0) as total "
        f"from `tab{doctype}` where company = %s and `{date_field}` between %s and %s and docstatus = 1",
        (company, from_date, to_date), as_dict=True,
    )[0]


def _order_section(doctype, company, start, end, label, route):
    row = _aggregate(doctype, company, "transaction_date", start, end, "grand_total")
    if row is None:
        return None
    return [_card(f"{label}金额", row.total, "CNY", "info", route, end, doctype),
            _card(f"{label}数量", row.count, "笔", "info", route, end, doctype)]


def _invoice_section(doctype, company, start, end, label, route):
    row = _aggregate(doctype, company, "posting_date", start, end, "outstanding_amount")
    return None if row is None else [_card(label, row.total, "CNY", "warning" if row.total else "normal",
                                            route, end, doctype)]


def _sales(company, start, end, roles=None):
    return _order_section("Sales Order", company, start, end, "销售订单", "/app/sales-order")


def _receivables(company, start, end, roles=None):
    return _invoice_section("Sales Invoice", company, start, end, "应收余额", "/app/sales-invoice")


def _purchases(company, start, end, roles=None):
    return _order_section("Purchase Order", company, start, end, "采购订单", "/app/purchase-order")


def _payables(company, start, end, roles=None):
    return _invoice_section("Purchase Invoice", company, start, end, "应付余额", "/app/purchase-invoice")


def _cash(company, start, end, roles=None):
    row = frappe.db.sql("""
        select coalesce(sum(gl.debit - gl.credit), 0) as movement
        from `tabGL Entry` gl join `tabAccount` account on account.name = gl.account
        where gl.company = %s and gl.posting_date between %s and %s and gl.is_cancelled = 0
          and account.account_type in ('Bank', 'Cash')
    """, (company, start, end), as_dict=True)[0]
    return [_card("资金净变动", row.movement, "CNY", "info", "/app/account", end, "总账银行及现金科目")]


def _low_stock(company, start, end, roles=None):
    count = frappe.db.sql("""
        select count(*) from `tabBin` bin join `tabWarehouse` warehouse on warehouse.name = bin.warehouse
        where warehouse.company = %s and warehouse.is_group = 0 and bin.actual_qty <= 0
    """, company)[0][0]
    return [_card("缺货商品", count, "项", "warning" if count else "normal",
                  "/app/stock-balance", end, "库存余额")]


def _expenses(company, start, end, roles=None):
    if not _installed("Expense Claim"):
        return None
    row = frappe.db.sql("""
        select coalesce(sum(case when docstatus = 1 then total_claimed_amount else 0 end), 0) as total,
               coalesce(sum(case when docstatus = 0 and approval_status = 'Draft' then 1 else 0 end), 0) as pending
        from `tabExpense Claim` where company = %s and posting_date between %s and %s
    """, (company, start, end), as_dict=True)[0]
    return [_card("费用报销", row.total, "CNY", "info", "/app/expense-claim", end, "费用报销单"),
            _card("待审批报销", row.pending, "项", "warning" if row.pending else "normal",
                  "/app/expense-claim", end, "费用报销单")]


def _projects(company, start, end, roles=None):
    row = frappe.db.sql("""
        select count(name) as open_count, coalesce(avg(percent_complete), 0) as progress,
               (select count(name) from `tabTask`
                where company = %s and exp_end_date < %s
                  and status not in ('Completed', 'Cancelled', 'Template')) as overdue
        from `tabProject` where company = %s and status = 'Open'
    """, (company, end, company), as_dict=True)[0]
    return [_card("进行中项目", row.open_count, "个", "info", "/app/project", end, "项目"),
            _card("项目平均进度", row.progress, "%", "info", "/app/project", end, "项目"),
            _card("逾期任务", row.overdue, "项", "warning" if row.overdue else "normal",
                  "/app/task", end, "项目任务")]


def _hr(company, start, end, roles=None):
    if not _installed("Leave Application"):
        return None
    privileged = frappe.session.user == "Administrator" or bool(
        (roles or set()) & (EXECUTIVE_ROLES | FINANCE_ROLES | {"人事工资", "HR User", "HR Manager"})
    )
    employee = None
    if not privileged:
        employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user, "company": company}, "name")
    pending = frappe.db.sql("""
        select count(name) from `tabLeave Application`
        where company = %s and from_date <= %s and to_date >= %s and status = 'Open'
          and (%s is null or employee = %s)
    """, (company, end, start, None if privileged else employee or "__none__",
            None if privileged else employee or "__none__"))[0][0]
    cards = [_card("待处理请假", pending, "项", "warning" if pending else "normal",
                   "/app/leave-application", end, "请假申请")]
    if privileged and _installed("Salary Slip") and _installed("Payroll Entry"):
        row = frappe.db.sql("""
            select
              (select count(name) from `tabPayroll Entry`
               where company = %s and start_date <= %s and end_date >= %s and docstatus = 0) as drafts,
              (select coalesce(sum(gross_pay), 0) from `tabSalary Slip`
               where company = %s and posting_date between %s and %s and docstatus = 1) as total
        """, (company, end, start, company, start, end), as_dict=True)[0]
        cards.append(_card("工资草稿", row.drafts, "项", "warning" if row.drafts else "normal",
                           "/app/payroll-entry", end, "工资处理"))
        cards.append(_card("工资金额", row.total, "CNY", "info", "/app/salary-slip", end, "工资单"))
    return cards


def _month_end(company, start, end, roles=None):
    count = frappe.db.sql(
        "select count(name) from `tabMonth End Run` where company = %s "
        "and period_end between %s and %s and status != 'Completed'", (company, start, end)
    )[0][0]
    return [_card("待完成月结", count, "项", "warning" if count else "normal",
                  "/app/ledger-workbench/month-end", end, "月结检查")]


SECTION_LOADERS = {"sales": _sales, "receivables": _receivables, "purchases": _purchases,
                   "payables": _payables, "cash": _cash, "low_stock": _low_stock,
                   "expenses": _expenses, "projects": _projects, "hr": _hr,
                   "month_end": _month_end}


@frappe.whitelist(methods=["GET", "POST"])
def get_business_dashboard(company: str, from_date: str, to_date: str) -> dict:
    company = _company(company)
    start, end = getdate(from_date), getdate(to_date)
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")
    roles = set() if frappe.session.user == "Administrator" else _roles()
    unrestricted = frappe.session.user == "Administrator" or bool(roles & EXECUTIVE_ROLES)
    sections, partial = {}, False
    for key, loader in SECTION_LOADERS.items():
        allowed = key == "hr" or unrestricted or bool(roles & SECTION_ROLES[key])
        if not allowed:
            sections[key] = {"available": False, "restricted": True, "cards": [],
                             "message": "当前角色不显示此模块"}
            continue
        try:
            cards = loader(company, start, end, roles)
            sections[key] = {"available": cards is not None, "restricted": False,
                             "cards": cards or [], "message": "" if cards is not None else "该模块尚未安装"}
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"经营首页读取失败：{key}")
            sections[key] = {"available": False, "restricted": False, "cards": [],
                             "message": "该模块暂时无法读取"}
            partial = True
    return {"company": company, "from_date": str(start), "to_date": str(end),
            "partial": partial, "sections": sections}
