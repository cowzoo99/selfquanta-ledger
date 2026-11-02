import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import frappe

from internal_ledger.tests.test_classifier import set_up, tear_down


TITLE = "SelfLedger 企业经营与智能内账"
ROOT = Path(__file__).resolve().parents[1]
LABELS = ["业务总览", "文件智能导入", "待办中心", "账簿与报表", "企业成员", "销售", "采购", "库存", "制造"]
ROUTES = ["/app/ledger-workbench/" + view for view in
          ("dashboard", "import", "review")] + ["/app/query-report/General%20Ledger", "/app/ledger-workbench/members"] + [
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


def test_workspace_fixture_has_only_the_nine_unified_entries():
    workspace = json.loads((ROOT / "internal_ledger/workspace/selfledger/selfledger.json").read_text(encoding="utf-8"))
    assert [row["label"] for row in workspace["shortcuts"]] == LABELS
    assert [row["url"] for row in workspace["shortcuts"]] == ROUTES
    assert [row["data"]["shortcut_name"] for row in json.loads(workspace["content"])] == LABELS


def test_dashboard_orders_import_totals_four_tasks_latest_batch_and_erp():
    source = (ROOT / "public/frontend/src/pages/Dashboard.vue").read_text(encoding="utf-8")
    template = source.split("<template>", 1)[1]
    positions = [template.index(text) for text in (
        'class="ledger-primary-action"', 'class="ledger-counts"', 'v-for="card in cards"',
        '最近处理', 'v-for="entry in erpLinks"')]
    assert positions == sorted(positions)
    for field in ("batch_name", "total", "created", "duplicate", "manual"):
        assert "counts.latest_batch." + field in template
    assert "ledger-flow" not in source and "异常优先" not in source
    app = (ROOT / "public/frontend/src/App.vue").read_text(encoding="utf-8")
    assert 'href="/app/query-report/General%20Ledger"' in app
    navigation = app.split("const navigation =", 1)[1].split(";", 1)[0]
    assert "duplicates" not in navigation and "classification" not in navigation


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


def test_setup_home_is_redirected_inside_desk_for_ledger_users():
    hooks = (ROOT / "hooks.py").read_text()
    assert 'app_include_js = "/assets/internal_ledger/js/internal_ledger_desk.js"' in hooks
    script = (ROOT / "public/js/internal_ledger_desk.js").read_text()
    assert 'route[0] === "setup" && route[1] === "home"' in script
    assert 'route[0] === "Workspaces" && route[1] === "Home"' in script
    assert 'window.location.pathname === "/desk" && route[0] === ""' in script
    assert 'window.location.assign("/desk/ledger-workbench/dashboard")' in script
    assert '(frappe.user_roles || []).some' in script
    assert 'frappe.set_route("ledger-workbench", "dashboard")' in script
    assert "setInterval" in script and "clearInterval" in script
    assert 'frappe.router.on("change"' in script
    assert "账套管理员" in script and "账务查看者" in script


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


def test_manual_classification_chinese_catalog_contract():
    labels = (ROOT / "public/frontend/src/labels.js").read_text(encoding="utf-8")
    for name in ("directions", "sources", "transactionTypes", "transactionStatuses", "draftStatuses",
                 "processingStatuses", "classificationStatuses", "ACCOUNT_PRESETS"):
        assert f"export const {name} =" in labels
    for text in ("支出", "收入", "银行卡", "微信支付", "支付宝", "费用支出", "经营收入", "账户转账",
                 "退款", "手续费", "股东投入", "办公费", "差旅费", "采购支出", "其他"):
        assert text in labels
    form = (ROOT / "public/frontend/src/components/ManualClassificationForm.vue").read_text(encoding="utf-8")
    for text in ("ACCOUNT_PRESETS", "transactionTypes", "preset.direction !== transaction.direction", "更多中文科目",
                 'v-if="showAccounts"', ':value="account.name"', " · ",
                 "account.account_number === preset.accountNumber", "account.root_type === 'Asset'",
                 "account.name !== props.transaction.payment_account"):
        assert text in form


def test_import_duplicate_and_task_center_expose_one_continuous_workflow():
    root = ROOT / "public/frontend/src"
    imported = (root / "pages/ImportPreview.vue").read_text(encoding="utf-8")
    duplicate = (root / "pages/DuplicateReview.vue").read_text(encoding="utf-8")
    review = (root / "pages/ReviewCenter.vue").read_text(encoding="utf-8")
    app = (root / "App.vue").read_text(encoding="utf-8")
    for text in ("自动生成凭证草稿", "疑似重复", "待人工分类", "处理剩余事项"):
        assert text in imported
    assert "processing" in duplicate and "已自动分类并生成凭证草稿" in duplicate
    assert "待办中心" in app
    assert "directions" in review and "sources" in review
    assert "确认审核并过账" in review


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))
