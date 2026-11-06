import csv
from datetime import date
from decimal import Decimal
import hashlib
import io
import json
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import frappe
from frappe.utils import get_last_day, getdate, now_datetime

from internal_ledger.permissions import record_audit, require_ledger_admin


PACKAGE_TYPES = {"monthly_standard", "quarterly_standard", "annual_standard"}
DISCLAIMER = "本材料包仅供申报准备和人工复核，不代表已向税务机关提交。"
MANIFEST = (
    "材料清单.csv", "科目余额表.csv", "总账.csv", "明细账.csv", "利润表.csv", "资产负债表.csv",
    "现金流量表.csv", "销项发票.csv", "进项发票.csv", "工资汇总.csv", "费用汇总.csv", "异常清单.csv", "说明.txt",
)


def _period(value: str, package_type: str) -> tuple[date, date]:
    if package_type == "monthly_standard" and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value or ""):
        start = getdate(value + "-01")
        return start, get_last_day(start)
    match = re.fullmatch(r"(\d{4})-Q([1-4])", value or "")
    if package_type == "quarterly_standard" and match:
        month = (int(match.group(2)) - 1) * 3 + 1
        start = getdate(f"{match.group(1)}-{month:02d}-01")
        return start, get_last_day(getdate(f"{match.group(1)}-{month + 2:02d}-01"))
    if package_type == "annual_standard" and re.fullmatch(r"\d{4}", value or ""):
        return getdate(value + "-01-01"), getdate(value + "-12-31")
    frappe.throw("期间格式与材料包类型不匹配")


def _source_row(doctype, date_field, company, start, end, submitted=False):
    if not frappe.db.table_exists(doctype):
        return {"count": 0, "modified": ""}
    filters = {"company": company, date_field: ["between", [start, end]]}
    if submitted:
        filters["docstatus"] = 1
    latest = frappe.get_all(doctype, filters=filters, fields=["modified"], order_by="modified desc", limit=1)
    return {"count": frappe.db.count(doctype, filters), "modified": str(latest[0].modified) if latest else ""}


def _source_state(company, start, end):
    state = {
        "GL Entry": _source_row("GL Entry", "posting_date", company, start, end),
        "Sales Invoice": _source_row("Sales Invoice", "posting_date", company, start, end, True),
        "Purchase Invoice": _source_row("Purchase Invoice", "posting_date", company, start, end, True),
    }
    if frappe.db.table_exists("Salary Slip"):
        row = frappe.db.sql("""
            SELECT COUNT(*) AS count, MAX(modified) AS modified FROM `tabSalary Slip`
            WHERE company = %s AND start_date <= %s AND end_date >= %s AND docstatus = 1
        """, (company, end, start), as_dict=True)[0]
        state["Salary Slip"] = {"count": int(row.count or 0), "modified": str(row.modified or "")}
    else:
        state["Salary Slip"] = {"count": 0, "modified": ""}
    return state


def _csv_bytes(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def _money(value):
    return f"{Decimal(str(value or 0)):.2f}"


def _gl_rows(company, start, end):
    return frappe.db.sql("""
        SELECT posting_date, account, debit, credit, voucher_type, voucher_no,
               party_type, party, against, remarks
        FROM `tabGL Entry`
        WHERE company = %s AND posting_date BETWEEN %s AND %s AND is_cancelled = 0
        ORDER BY posting_date, creation, name
    """, (company, start, end), as_dict=True)


def _account_summary(company, start, end):
    return frappe.db.sql("""
        SELECT gle.account, account.account_name, account.root_type,
               SUM(gle.debit) AS debit, SUM(gle.credit) AS credit
        FROM `tabGL Entry` gle
        LEFT JOIN `tabAccount` account ON account.name = gle.account
        WHERE gle.company = %s AND gle.posting_date BETWEEN %s AND %s AND gle.is_cancelled = 0
        GROUP BY gle.account, account.account_name, account.root_type
        ORDER BY gle.account
    """, (company, start, end), as_dict=True)


def _invoice_rows(doctype, company, start, end):
    if not frappe.db.table_exists(doctype):
        return []
    party_field = "customer_name" if doctype == "Sales Invoice" else "supplier_name"
    return frappe.get_all(doctype, filters={"company": company, "posting_date": ["between", [start, end]],
        "docstatus": 1}, fields=["name", "posting_date", f"{party_field} as party_name", "base_net_total", "base_grand_total",
        "base_total_taxes_and_charges", "outstanding_amount"], order_by="posting_date, name")


def _salary_rows(company, start, end):
    if not frappe.db.table_exists("Salary Slip"):
        return []
    return frappe.get_all("Salary Slip", filters={"company": company, "start_date": ["<=", end],
        "end_date": [">=", start], "docstatus": 1}, fields=["name", "employee", "employee_name",
        "start_date", "end_date", "gross_pay", "net_pay"], order_by="start_date, name")


def _anomaly_rows(company, start, end):
    run = frappe.get_all("Month End Run", filters={"company": company, "period_start": start,
        "period_end": end}, fields=["name"], order_by="executed_at desc", limit=1)
    if not run:
        return []
    return frappe.get_all("Month End Check", filters={"parent": run[0].name, "status": "Fail"},
        fields=["code", "severity", "count", "message", "target_route"], order_by="idx")


def _contents(company, period, package_type, start, end):
    gl = _gl_rows(company, start, end)
    accounts = _account_summary(company, start, end)
    sales = _invoice_rows("Sales Invoice", company, start, end)
    purchases = _invoice_rows("Purchase Invoice", company, start, end)
    salaries = _salary_rows(company, start, end)
    anomalies = _anomaly_rows(company, start, end)
    total_debit = sum(Decimal(str(row.debit or 0)) for row in gl)
    total_credit = sum(Decimal(str(row.credit or 0)) for row in gl)

    files = {}
    files["科目余额表.csv"] = _csv_bytes(
        ["科目", "科目名称", "科目类别", "借方发生额", "贷方发生额", "期末净额"],
        [[row.account, row.account_name or "", row.root_type or "", _money(row.debit), _money(row.credit),
          _money(Decimal(str(row.debit or 0)) - Decimal(str(row.credit or 0)))] for row in accounts])
    ledger_headers = ["过账日期", "科目", "借方", "贷方", "凭证类型", "凭证号", "往来类型", "往来单位", "对方科目", "摘要"]
    ledger_rows = [[row.posting_date, row.account, _money(row.debit), _money(row.credit), row.voucher_type or "",
        row.voucher_no or "", row.party_type or "", row.party or "", row.against or "", row.remarks or ""] for row in gl]
    files["总账.csv"] = _csv_bytes(ledger_headers, ledger_rows)
    files["明细账.csv"] = _csv_bytes(ledger_headers, ledger_rows)
    files["利润表.csv"] = _csv_bytes(["科目", "科目名称", "类别", "本期金额"],
        [[row.account, row.account_name or "", row.root_type,
          _money(Decimal(str(row.credit or 0)) - Decimal(str(row.debit or 0)) if row.root_type == "Income"
                 else Decimal(str(row.debit or 0)) - Decimal(str(row.credit or 0)))]
         for row in accounts if row.root_type in ("Income", "Expense")])
    files["资产负债表.csv"] = _csv_bytes(["科目", "科目名称", "类别", "期末余额"],
        [[row.account, row.account_name or "", row.root_type,
          _money(Decimal(str(row.debit or 0)) - Decimal(str(row.credit or 0)) if row.root_type == "Asset"
                 else Decimal(str(row.credit or 0)) - Decimal(str(row.debit or 0)))]
         for row in accounts if row.root_type in ("Asset", "Liability", "Equity")])
    cash_accounts = set(frappe.get_all("Account", filters={"company": company, "account_type": ["in", ["Bank", "Cash"]]}, pluck="name"))
    files["现金流量表.csv"] = _csv_bytes(["过账日期", "资金科目", "流入", "流出", "净变动", "凭证号", "摘要"],
        [[row.posting_date, row.account, _money(row.debit), _money(row.credit),
          _money(Decimal(str(row.debit or 0)) - Decimal(str(row.credit or 0))), row.voucher_no or "", row.remarks or ""]
         for row in gl if row.account in cash_accounts])
    invoice_headers = ["单据号", "日期", "往来单位", "不含税金额", "税额", "价税合计", "未结金额"]
    invoice = lambda rows: [[row.name, row.posting_date, row.party_name or "", _money(row.base_net_total),
        _money(row.base_total_taxes_and_charges), _money(row.base_grand_total), _money(row.outstanding_amount)] for row in rows]
    files["销项发票.csv"] = _csv_bytes(invoice_headers, invoice(sales))
    files["进项发票.csv"] = _csv_bytes(invoice_headers, invoice(purchases))
    files["工资汇总.csv"] = _csv_bytes(["工资单", "员工", "姓名", "开始日期", "结束日期", "应发工资", "实发工资"],
        [[row.name, row.employee, row.employee_name or "", row.start_date, row.end_date,
          _money(row.gross_pay), _money(row.net_pay)] for row in salaries])
    files["费用汇总.csv"] = _csv_bytes(["科目", "科目名称", "本期费用"],
        [[row.account, row.account_name or "", _money(Decimal(str(row.debit or 0)) - Decimal(str(row.credit or 0)))]
         for row in accounts if row.root_type == "Expense"])
    files["异常清单.csv"] = _csv_bytes(["检查项", "级别", "数量", "说明", "处理入口"],
        [[row.code, row.severity, row.count, row.message, row.target_route] for row in anomalies])
    files["材料清单.csv"] = _csv_bytes(["材料名称", "用途", "记录数"], [
        [name, "申报准备与人工复核", len(files[name].decode("utf-8-sig").splitlines()) - 1]
        for name in MANIFEST[1:-1]])
    files["说明.txt"] = (f"企业：{company}\r\n期间：{period}\r\n材料包类型：{package_type}\r\n"
                           f"借方合计：{_money(total_debit)}\r\n贷方合计：{_money(total_credit)}\r\n\r\n{DISCLAIMER}\r\n").encode("utf-8")
    totals = {"debit": _money(total_debit), "credit": _money(total_credit), "gl_entries": len(gl),
              "sales_invoices": len(sales), "purchase_invoices": len(purchases), "salary_slips": len(salaries),
              "anomalies": len(anomalies)}
    return files, totals


def _zip_bytes(files):
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name in MANIFEST:
            info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, files[name])
    return output.getvalue()


def _serialize(doc):
    return {"name": doc.name, "company": doc.company, "period": doc.period, "package_type": doc.package_type,
            "status": doc.status, "version": int(doc.version), "source_hash": doc.source_hash, "file": doc.file,
            "generated_by": doc.generated_by, "generated_at": str(doc.generated_at),
            "totals": json.loads(doc.totals_json or "{}"), "disclaimer": doc.disclaimer}


def get_tax_material_package(name: str):
    doc = frappe.get_doc("Tax Material Package", name)
    doc.check_permission("read")
    configured = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not configured or doc.company != configured:
        frappe.throw("只能读取当前账套的税务材料包", frappe.PermissionError)
    return _serialize(doc)


def build_tax_material_package(company: str, period: str, package_type: str):
    require_ledger_admin()
    configured = frappe.db.get_single_value("Internal Ledger Settings", "company")
    if not configured or company != configured:
        frappe.throw("只能生成当前账套公司的税务材料包", frappe.PermissionError)
    if package_type not in PACKAGE_TYPES:
        frappe.throw("不支持的材料包类型")
    start, end = _period(period, package_type)
    source_hash = hashlib.sha256(json.dumps({"company": company, "period": period, "package_type": package_type,
        "state": _source_state(company, start, end)}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    existing = frappe.db.get_value("Tax Material Package", {"company": company, "period": period,
        "package_type": package_type, "source_hash": source_hash, "status": "Ready"}, "name")
    if existing:
        return get_tax_material_package(existing)
    latest = frappe.get_all("Tax Material Package", filters={"company": company, "period": period,
        "package_type": package_type}, fields=["version"], order_by="version desc", limit=1)
    version = int(latest[0].version) + 1 if latest else 1
    files, totals = _contents(company, period, package_type, start, end)
    doc = frappe.get_doc({"doctype": "Tax Material Package", "company": company, "period": period,
        "package_type": package_type, "status": "Generating", "version": version, "source_hash": source_hash,
        "generated_by": frappe.session.user, "generated_at": now_datetime(), "totals_json": json.dumps(totals),
        "disclaimer": DISCLAIMER}).insert(ignore_permissions=True)
    attachment = frappe.get_doc({"doctype": "File", "file_name": f"税务申报准备材料-{period}-V{version}.zip",
        "content": _zip_bytes(files), "is_private": 1, "attached_to_doctype": doc.doctype,
        "attached_to_name": doc.name}).insert(ignore_permissions=True)
    doc.status = "Ready"
    doc.file = attachment.file_url
    doc.save(ignore_permissions=True)
    record_audit("build_tax_material_package", doc.doctype, doc.name, "success")
    return _serialize(doc)
