import io
from pathlib import Path
import unittest
from zipfile import ZipFile

import frappe

from internal_ledger.tests.test_duplicate_review import duplicate_pair
from internal_ledger.tests.test_permissions import user
from internal_ledger.tests.test_review_api import draft, set_up, tear_down


EXPECTED_FILES = [
    "材料清单.csv", "科目余额表.csv", "总账.csv", "明细账.csv", "利润表.csv", "资产负债表.csv",
    "现金流量表.csv", "销项发票.csv", "进项发票.csv", "工资汇总.csv", "费用汇总.csv", "异常清单.csv", "说明.txt",
]


def clean_up():
    frappe.set_user("Administrator")
    if frappe.db.table_exists("Tax Material Package"):
        files = frappe.get_all("File", filters={"attached_to_doctype": "Tax Material Package"},
                               fields=["name", "file_url"])
        for row in files:
            path = Path(frappe.get_doc("File", row.name).get_full_path())
            frappe.db.delete("File", {"name": row.name})
            path.unlink(missing_ok=True)
        frappe.db.delete("Tax Material Package", {"company": frappe.flags.classification_company})
    tear_down()


def package_bytes(result):
    file = frappe.get_doc("File", {"file_url": result["file"]})
    return file, Path(file.get_full_path()).read_bytes()


def test_exact_manifest_private_file_and_reproducible_source_reuse():
    from internal_ledger.accounting.tax_materials import build_tax_material_package

    company = frappe.flags.classification_company
    first = build_tax_material_package(company, "2026-10", "monthly_standard")
    second = build_tax_material_package(company, "2026-10", "monthly_standard")
    assert first["name"] == second["name"] and first["version"] == second["version"] == 1
    file, content = package_bytes(first)
    assert file.is_private and file.attached_to_name == first["name"]
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        file.delete(ignore_permissions=True)
    assert Path(file.get_full_path()).exists()
    with ZipFile(io.BytesIO(content)) as archive:
        assert archive.namelist() == EXPECTED_FILES
        for name in EXPECTED_FILES[:-1]:
            assert archive.read(name).startswith(b"\xef\xbb\xbf")
        assert "不代表已向税务机关提交" in archive.read("说明.txt").decode("utf-8")


def test_standard_posting_changes_source_hash_and_creates_next_version():
    from internal_ledger.accounting.posting import post_draft
    from internal_ledger.api.review_api import approve_draft
    from internal_ledger.accounting.tax_materials import build_tax_material_package

    company = frappe.flags.classification_company
    first = build_tax_material_package(company, "2026-10", "monthly_standard")
    post_draft(approve_draft(draft().name).name)
    second = build_tax_material_package(company, "2026-10", "monthly_standard")
    assert second["name"] != first["name"]
    assert second["version"] == 2 and second["source_hash"] != first["source_hash"]
    with ZipFile(io.BytesIO(package_bytes(second)[1])) as archive:
        ledger = archive.read("总账.csv").decode("utf-8-sig")
        assert "12.50" in ledger


def test_confirmed_duplicate_does_not_change_package_or_counts():
    from internal_ledger.api.duplicate_api import resolve_duplicate
    from internal_ledger.accounting.tax_materials import build_tax_material_package

    company = frappe.flags.classification_company
    first = build_tax_material_package(company, "2026-10", "monthly_standard")
    _, duplicate = duplicate_pair()
    resolve_duplicate(duplicate.name, "confirm", "材料包排除重复")
    second = build_tax_material_package(company, "2026-10", "monthly_standard")
    assert second["name"] == first["name"]
    assert second["source_hash"] == first["source_hash"]
    assert second["totals"] == first["totals"]


def test_viewer_can_read_authorized_package_but_cannot_generate():
    from internal_ledger.api.tax_api import build_tax_material_package, get_tax_material_package

    company = frappe.flags.classification_company
    result = build_tax_material_package(company, "2026-10", "monthly_standard")
    viewer = user("账务查看者")
    frappe.set_user(viewer)
    assert get_tax_material_package(result["name"])["name"] == result["name"]
    assert frappe.has_permission("File", "read", frappe.db.get_value("File", {"file_url": result["file"]}))
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        build_tax_material_package(company, "2026-10", "monthly_standard")


def test_company_period_and_package_type_are_validated():
    from internal_ledger.accounting.tax_materials import build_tax_material_package

    company = frappe.flags.classification_company
    for operation in (lambda: build_tax_material_package("_Test Company", "2026-10", "monthly_standard"),
                      lambda: build_tax_material_package(company, "2026-13", "monthly_standard"),
                      lambda: build_tax_material_package(company, "2026-10", "unknown")):
        with unittest.TestCase().assertRaises((frappe.PermissionError, frappe.ValidationError)):
            operation()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=clean_up)
        for name, test in sorted(globals().items()) if name.startswith("test_"))
