import json
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from internal_ledger.permissions import require_ledger_admin, audit_install_change


ROLE_PERMISSIONS = {
    "账套管理员": {"read", "write", "create", "submit", "report"},
    "账务查看者": {"read", "report"},
    "企业负责人": {"read", "write", "create", "submit", "report"},
    "财务主管": {"read", "write", "create", "submit", "report"},
    "会计": {"read", "write", "create", "submit", "report"},
    "出纳": {"read", "write", "create", "report"},
    "只读审计": {"read", "report"},
}

ROLE_PRESETS = {
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

LEDGER_REPORTS = ("General Ledger", "Trial Balance", "Balance Sheet", "Profit and Loss Statement")


def ensure_roles() -> None:
    require_ledger_admin()
    before = _permission_state()
    frappe.db.set_single_value("System Settings", "language", "zh")
    frappe.db.set_single_value("System Settings", "allow_login_using_user_name", 1)
    frappe.db.set_single_value("System Settings", "allow_login_using_mobile_number", 1)
    create_custom_fields({"User": [{
        "fieldname": "internal_ledger_must_change_password",
        "label": "Internal Ledger Must Change Password",
        "fieldtype": "Check",
        "hidden": 1,
        "no_copy": 1,
        "read_only": 1,
        "default": "0",
    }]}, update=True)
    for role in set(ROLE_PERMISSIONS) | set(ROLE_PRESETS):
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert()
    ensure_workspace()
    from frappe.permissions import add_permission, rights
    from internal_ledger.permissions import LEDGER_DOCTYPES

    # App schemas are authoritative, including sites with legacy Custom DocPerm overrides.
    frappe.db.delete("Custom DocPerm", {"parent": ["in", LEDGER_DOCTYPES]})
    for role, allowed in ROLE_PERMISSIONS.items():
        for doctype in (*LEDGER_DOCTYPES, "Journal Entry", "Account", "GL Entry", "Accounting Dimension"):
            add_permission(doctype, role, ptype="read")
            actions = allowed if doctype in (*LEDGER_DOCTYPES, "Journal Entry") else {"read", "report"}
            frappe.db.set_value("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0},
                                {action: int(action in actions) for action in rights})
    for report_name in LEDGER_REPORTS:
        report = frappe.get_doc("Report", report_name)
        expected = sorted({row.role for row in report.roles} | set(ROLE_PERMISSIONS))
        name = frappe.db.get_value("Custom Role", {"report": report_name}, "name")
        custom = frappe.get_doc("Custom Role", name) if name else frappe.get_doc({
            "doctype": "Custom Role", "report": report_name,
        })
        if sorted(row.role for row in custom.roles) != expected:
            custom.set("roles", [{"role": role} for role in expected])
            custom.save(ignore_permissions=True) if name else custom.insert(ignore_permissions=True)
    frappe.clear_cache()
    if before != _permission_state():
        audit_install_change("ensure_roles", "Internal Ledger Settings", "Internal Ledger Settings")


def ensure_workspace() -> None:
    require_ledger_admin()
    from frappe.modules.import_file import import_file_by_path

    path = Path(__file__).parent / "internal_ledger/workspace/selfledger/selfledger.json"
    expected = json.loads(path.read_text(encoding="utf-8"))
    if frappe.db.exists("Workspace", expected["name"]):
        current = frappe.get_doc("Workspace", expected["name"])
        if not current.standard or current.module != expected["module"]:
            return
        for field, value in expected.items():
            # Native fixture imports preserve site visibility; metadata is not product state.
            if field in {"doctype", "name", "creation", "modified", "modified_by", "is_hidden"}:
                continue
            actual = current.get(field)
            if isinstance(value, list):
                actual = ([{key: row.get(key) for key in template}
                           for row, template in zip(actual, value)] if len(actual) == len(value) else None)
            elif field == "content":
                actual, value = json.loads(actual), json.loads(value)
            elif value == "" and actual is None:
                actual = ""
            if actual != value:
                break
        else:
            return
    # Frappe's reload import keeps Custom Workspace and personal navigation layers intact.
    import_file_by_path(str(path), force=True)


def _permission_state():
    from frappe.permissions import rights
    from internal_ledger.permissions import LEDGER_DOCTYPES

    fields = ["parent", "role", "permlevel", "if_owner", *rights]
    rows = frappe.get_all("Custom DocPerm", filters={"parent": ["in", [*LEDGER_DOCTYPES,
        "Journal Entry", "Account", "GL Entry", "Accounting Dimension"]]}, fields=fields)
    permissions = sorted(tuple(row.get(field) or ("" if field in ("parent", "role") else 0)
                               for field in fields) for row in rows)
    report_roles = tuple((report, tuple(sorted(frappe.get_all(
        "Has Role", filters={"parenttype": "Custom Role", "parent": frappe.db.get_value(
            "Custom Role", {"report": report}, "name")}, pluck="role"
    )))) for report in LEDGER_REPORTS)
    password_field = frappe.db.get_value("Custom Field", {
        "dt": "User", "fieldname": "internal_ledger_must_change_password",
    }, ["fieldtype", "label", "hidden", "no_copy", "read_only", "default"])
    login_settings = frappe.db.get_value("System Settings", "System Settings", [
        "allow_login_using_user_name", "allow_login_using_mobile_number",
    ])
    roles = sorted(set(ROLE_PERMISSIONS) | set(ROLE_PRESETS))
    return (tuple(bool(frappe.db.exists("Role", role)) for role in roles),
            permissions, report_roles, password_field, login_settings)


def load_chart_fixture() -> dict:
    path = Path(__file__).parent / "chart_templates" / "small_business_chart_cn.json"
    return json.loads(path.read_text(encoding="utf-8"))


def install_chart(company: str) -> None:
    require_ledger_admin()
    frappe.get_doc("Company", company)
    installed = {}
    roots = {}
    created = False
    for accounts in load_chart_fixture().values():
        for account in accounts:
            values = dict(account)
            parent_number = values.pop("parent_account_number", None)
            root_type = values["root_type"]
            if parent_number:
                parent = installed[parent_number]
            else:
                if root_type not in roots:
                    matches = frappe.get_all("Account", filters={
                        "company": company, "root_type": root_type, "is_group": 1,
                        "parent_account": ["is", "not set"],
                    }, pluck="name")
                    if len(matches) != 1:
                        frappe.throw(f"Company {company} must have exactly one {root_type} root group.")
                    roots[root_type] = matches[0]
                parent = roots[root_type]
            values.update(company=company, parent_account=parent)
            values.setdefault("is_group", 0)
            matches = frappe.get_all("Account", filters={
                "company": company, "account_number": values["account_number"],
            }, fields=["*"])
            if matches:
                if len(matches) != 1 or any(matches[0].get(field) != value for field, value in values.items()):
                    frappe.throw(f"Account {values['account_number']} conflicts with the small business chart.")
                name = matches[0].name
            else:
                name = frappe.get_doc({"doctype": "Account", **values}).insert().name
                created = True
            installed[values["account_number"]] = name
    if created:
        audit_install_change("install_chart", "Company", company)


def install_default_rules(company: str) -> None:
    """Install a small, reviewable starter set without overwriting user rules."""
    require_ledger_admin()
    accounts = {
        number: frappe.db.get_value("Account", {"company": company, "account_number": number}, "name")
        for number in ("560201", "560202", "3001")
    }
    if not all(accounts.values()):
        frappe.throw("请先安装小企业中文会计科目")
    rules = [
        ("打车", "expense", "expense", "560202"),
        ("交通", "expense", "expense", "560202"),
        ("出行", "expense", "expense", "560202"),
        ("采购", "expense", "expense", "560201"),
        ("货款", "expense", "expense", "560201"),
        ("办公", "expense", "expense", "560201"),
        ("硬盘", "expense", "expense", "560201"),
        ("记账", "expense", "expense", "560201"),
        ("投资款", "income", "capital", "3001"),
    ]
    for value, direction, transaction_type, number in rules:
        filters = {"company": company, "kind": "keyword", "value": value, "direction": direction}
        if frappe.db.exists("Classification Rule", filters):
            continue
        fields = {
            "doctype": "Classification Rule", **filters, "transaction_type": transaction_type,
            "enabled": 1,
        }
        if direction == "expense":
            fields["debit_account"] = accounts[number]
        else:
            fields["credit_account"] = accounts[number]
        frappe.get_doc(fields).insert()


def ensure_erpnext_setup_fixtures() -> None:
    """Install ERPNext's required country fixtures before creating a Company."""
    from erpnext.setup.setup_wizard.operations import install_fixtures

    install_fixtures.install("China")


def initialize_company(company_name: str, company_abbr: str) -> dict:
    """Create the one company for a newly provisioned Site and install ledger defaults."""
    require_ledger_admin()
    company_name = (company_name or "").strip()
    company_abbr = (company_abbr or "").strip()
    if not company_name or not company_abbr or len(company_abbr) > 5:
        frappe.throw("公司名称和 1 至 5 位公司简称为必填项")
    companies = frappe.get_all("Company", pluck="name")
    if companies and companies != [company_name]:
        frappe.throw("此独立 Site 已配置其他公司")
    if frappe.db.exists("Company", company_name):
        company = frappe.get_doc("Company", company_name)
        if company.company_name != company_name or company.abbr != company_abbr:
            frappe.throw("现有公司与开通参数不一致")
    else:
        ensure_erpnext_setup_fixtures()
        company = frappe.get_doc({
            "doctype": "Company",
            "company_name": company_name,
            "abbr": company_abbr,
            "default_currency": "CNY",
            "country": "China",
            "create_chart_of_accounts_based_on": "Standard Template",
            "chart_of_accounts": "Standard",
        }).insert()
    install_chart(company.name)
    settings = frappe.get_single("Internal Ledger Settings")
    settings.company = company.name
    settings.base_currency = "CNY"
    if not settings.fiscal_year_start:
        settings.fiscal_year_start = f"{frappe.utils.today()[:4]}-01-01"
    settings.save()
    install_default_rules(company.name)
    return {"company": company.name, "abbr": company.abbr}
