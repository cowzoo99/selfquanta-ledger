import json
import unittest
from pathlib import Path
from types import SimpleNamespace

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


def test_workspace_reconciles_stale_app_document_and_preserves_site_customizations():
    from internal_ledger.install import ensure_workspace
    from internal_ledger.tests.test_permissions import user

    actor = user("账务查看者")
    personal = frappe.get_doc({"doctype": "Workspace", "label": "_Test Personal Ledger",
        "title": "_Test Personal Ledger", "module": "Internal Ledger", "type": "Workspace",
        "standard": 0, "public": 0, "for_user": actor, "content": "[]"}).insert()
    custom = frappe.get_doc({"doctype": "Custom Workspace", "workspace": TITLE,
        "visibility": "Hidden", "icon": "star", "override_sequence": 1, "sequence_id": 42,
        "content": "[]", "widgets": "{}"}).insert()
    frappe.db.set_value("User", actor, "default_workspace", "Selling")
    preserved = [frappe.get_doc(doc.doctype, doc.name).as_dict()
                 for doc in (personal, custom, frappe.get_doc("User", actor))]
    workspace = frappe.get_doc("Workspace", TITLE)
    frappe.db.set_value("Workspace", TITLE, {"title": "Stale app title", "sequence_id": 999,
        "content": "[]", "modified": "2099-01-01 00:00:00"}, update_modified=False)
    frappe.db.set_value("Workspace Shortcut", workspace.shortcuts[0].name, "url", "/app/missing")
    frappe.db.set_value("Has Role", workspace.roles[0].name, "role", "Guest")
    ensure_workspace()
    test_installed_workspace_shortcuts_and_content()
    repaired = frappe.get_doc("Workspace", TITLE).as_dict()
    assert repaired["sequence_id"] == 0.1
    assert [row["role"] for row in repaired["roles"]] == ["账套管理员", "账务查看者", "System Manager"]
    ensure_workspace()
    assert frappe.get_doc("Workspace", TITLE).as_dict() == repaired
    assert [frappe.get_doc(doc.doctype, doc.name).as_dict() for doc in (personal, custom, frappe.get_doc("User", actor))] == preserved


def test_workspace_reconcile_does_not_take_over_nonstandard_same_name():
    from internal_ledger.install import ensure_workspace

    for values in ({"standard": 0, "title": "Site-owned workspace"},
                   {"standard": 1, "module": "Selling", "title": "Other app workspace"}):
        frappe.db.set_value("Workspace", TITLE, values)
        before = frappe.get_doc("Workspace", TITLE).as_dict()
        ensure_workspace()
        assert frappe.get_doc("Workspace", TITLE).as_dict() == before


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


def test_unified_home_makes_import_primary_and_keeps_erp_modules_in_one_site():
    dashboard = (ROOT / "public/frontend/src/pages/Dashboard.vue").read_text()
    assert "开始导入并自动识别" in dashboard
    assert 'class="ledger-primary-action"' in dashboard
    for route in ("/app/ledger-workbench/import", "selling", "buying", "stock", "manufacturing"):
        assert route in dashboard


def test_default_workspace_patch_sets_only_blank_ledger_users():
    from internal_ledger.patches.set_selfledger_default_workspace import execute
    from internal_ledger.tests.test_permissions import user

    viewer = user("账务查看者")
    manager = user("账套管理员")
    frappe.db.set_value("User", "Administrator", "default_workspace", None)
    frappe.db.set_value("User", viewer, "default_workspace", None)
    frappe.db.set_value("User", manager, "default_workspace", "Selling")
    execute()
    assert frappe.db.get_value("User", "Administrator", "default_workspace") == TITLE
    assert frappe.db.get_value("User", viewer, "default_workspace") == TITLE
    assert frappe.db.get_value("User", manager, "default_workspace") == "Selling"
    execute()
    assert frappe.db.get_value("User", viewer, "default_workspace") == TITLE


def test_ledger_login_redirects_to_unified_home_without_overriding_password_reset():
    from internal_ledger.member_security import redirect_temporary_password
    from internal_ledger.tests.test_permissions import user

    actor = user("账务查看者")
    frappe.cache.hset("home_page", actor, "app/setup/home")
    redirect_temporary_password(SimpleNamespace(user=actor))
    assert frappe.local.flags.home_page == "/app/ledger-workbench/dashboard"
    assert not frappe.cache.hget("home_page", actor)

    frappe.db.set_value("User", actor, "internal_ledger_must_change_password", 1)
    redirect_temporary_password(SimpleNamespace(user=actor))
    reset = frappe.cache.hget("redirect_after_login", actor)
    assert "/update-password?key=" in reset
    assert frappe.local.flags.home_page == reset
    frappe.local.flags.pop("home_page", None)


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
