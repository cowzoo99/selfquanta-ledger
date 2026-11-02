import json
import unittest
from pathlib import Path

import frappe

from internal_ledger.tests.test_classifier import set_up, tear_down


TITLE = "SelfLedger 企业经营与智能内账"
ROOT = Path(__file__).resolve().parents[1]
LABELS = ["业务总览", "文件智能导入", "重复流水复核", "智能分类", "凭证中心", "销售", "采购", "库存", "制造"]
ROUTES = ["/app/ledger-workbench/" + view for view in
          ("dashboard", "import", "duplicates", "classification", "review")] + [
              "/app/selling", "/app/buying", "/app/stock", "/app/manufacturing"]


def test_installed_workspace_shortcuts_and_content():
    assert frappe.db.exists("Workspace", TITLE), "Unified Workspace must be installed"
    workspace = frappe.get_doc("Workspace", TITLE)
    assert workspace.title == TITLE
    assert workspace.public == 1 and workspace.owner == "Administrator"
    assert workspace.module == "Internal Ledger"
    assert [row.label for row in workspace.shortcuts] == LABELS
    assert [row.url for row in workspace.shortcuts] == ROUTES
    assert all(row.type == "URL" for row in workspace.shortcuts)
    blocks = json.loads(workspace.content)
    assert [row["data"]["shortcut_name"] for row in blocks if row["type"] == "shortcut"] == LABELS


def test_workspace_visible_to_both_ledger_roles_without_extra_business_rights():
    from internal_ledger.tests.test_permissions import user
    from frappe.desk.desktop import get_workspaces

    for role in ("账套管理员", "账务查看者"):
        actor = user(role)
        try:
            frappe.set_user(actor)
            visible = get_workspaces.__wrapped__()
            assert TITLE in {row.name for row in visible["pages"]}
            assert frappe.has_permission("Imported Transaction", ptype="read")
            assert bool(frappe.has_permission("Voucher Draft", ptype="write")) == (role == "账套管理员")
        finally:
            frappe.set_user("Administrator")


def test_workspace_install_is_idempotent_and_rejects_viewer():
    from internal_ledger.install import ensure_workspace
    from internal_ledger.tests.test_permissions import user

    ensure_workspace()
    before = frappe.get_doc("Workspace", TITLE).as_dict()
    ensure_workspace()
    assert frappe.get_doc("Workspace", TITLE).as_dict() == before
    try:
        frappe.set_user(user("账务查看者"))
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            ensure_workspace()
    finally:
        frappe.set_user("Administrator")


def test_page_mount_is_route_aware_and_reacts_to_cached_desk_routes():
    script = (ROOT / "internal_ledger/page/ledger_workbench/ledger_workbench.js").read_text()
    assert "frappe.get_route()" in script
    assert "on_page_show" in script, "Desk reuses a loaded page between view routes"
    bundle = (ROOT / "public/frontend/ledger_workbench.bundle.js").read_text()
    assert "createApp(App," in bundle
    app = (ROOT / "public/frontend/src/App.vue").read_text()
    assert "defineProps" in app and "view" in app
    assert "grid-template-columns: 160px" not in app
    assert "<nav" not in app
    assert "DuplicateReview" in app


def test_duplicate_page_contract_and_dedicated_review_link():
    path = ROOT / "public/frontend/src/pages/DuplicateReview.vue"
    assert path.exists(), "Dedicated duplicate-review component is missing"
    source = path.read_text()
    for text in ("candidate", "original", "duplicate_match_reason", "duplicate_review_status",
                 "duplicate_reviewed_by", "duplicate_reviewed_at", "duplicate_review_note",
                 "确认重复，忽略", "不是重复，保留并入账", "note.trim()", "canWrite"):
        assert text in source
    assert "candidate_dedupe_key" not in source
    review = (ROOT / "public/frontend/src/pages/ReviewCenter.vue").read_text()
    assert "/app/ledger-workbench/duplicates" in review
    assert "/app/imported-transaction/" not in review


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))
