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
    assert "['accounting', '会计中心']" in app
    accounting = (ROOT / "public/frontend/src/pages/AccountingCenter.vue").read_text(encoding="utf-8")
    assert 'href="#ledger-overview"' in accounting
    assert ':href="generalLedgerHref"' in accounting
    navigation = app.split("const navigation =", 1)[1].split(";", 1)[0]
    assert "duplicates" not in navigation and "classification" not in navigation


def test_accounting_center_supports_posted_metadata_and_safe_reversal():
    accounting = (ROOT / "public/frontend/src/pages/AccountingCenter.vue").read_text(encoding="utf-8")
    evidence = (ROOT / "public/frontend/src/components/TransactionEvidence.vue").read_text(encoding="utf-8")
    app = (ROOT / "public/frontend/src/App.vue").read_text(encoding="utf-8")

    for text in ("作废并冲销", "冲销原因", "internal_ledger.api.posting_api.reverse_posting",
                 "posting.status", "已作废（已生成冲销凭证）"):
        assert text in accounting
    for text in ("display_title", "展示标题", "保存标题和备注"):
        assert text in evidence
    assert '@refresh="refresh"' in app


def test_accounting_center_uses_one_versioned_full_entry_editor():
    accounting = (ROOT / "public/frontend/src/pages/AccountingCenter.vue").read_text(encoding="utf-8")
    editor = (ROOT / "public/frontend/src/components/PostedEntryEditor.vue").read_text(encoding="utf-8")
    assert "TransactionEvidence" not in accounting
    assert "PostedEntryEditor" in accounting
    for text in ("全选可永久删除账目", "永久删除选中项", "编辑整笔账目",
                 "internal_ledger.api.posting_api.permanently_delete_transactions", "expected_versions"):
        assert text in accounting
    assert "posting_api.delete_postings" not in accounting
    assert "TransactionEvidence" in editor
    for text in ("get_posted_entry", "rebuild_posted_entry", "expected_version", "transactionTypes"):
        assert text in editor


def test_posted_entry_editor_frontend():
    _run_review_component_case("posted_entry_editor")



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
                 'v-if="showAccounts"', ':value="accountChoice(account)"', " · ",
                 "account.account_number === preset.accountNumber", "account.root_type === 'Asset'",
                 "account.name !== props.transaction.payment_account"):
        assert text in form


def test_manual_classification_supports_searchable_choices_and_visible_tags():
    form = (ROOT / "public/frontend/src/components/ManualClassificationForm.vue").read_text(encoding="utf-8")
    for text in ('list="manual-business-types"', 'list="manual-accounts"', "chooseKind", "chooseAccount",
                 "ledger-choice-tag", "仅处理本笔（不保存规则）", "前往核对已有分类规则"):
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



def test_desk_startup_waits_for_route_and_sidebar_before_redirect():
    import subprocess

    script = ROOT / "public/js/internal_ledger_desk.js"
    harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
function boot(pathname, route, ready, user = 'Administrator', roles = []) {
  const events = {}, assigned = [], routed = [];
  const context = {document: {}, window: {location: {pathname, assign: value => assigned.push(value)}},
    frappe: {session: {user}, user_roles: roles, app: ready ? {sidebar: {}} : {},
      get_route: () => route, set_route: (...values) => routed.push(values),
      router: {on: (name, fn) => {events[name] = fn;}}},
    $: () => ({on: (name, fn) => {events[name] = fn;}}),
    setInterval: fn => {events.tick = fn; return 1;}, clearInterval: () => {}};
  vm.runInNewContext(source, context);
  return {context, events, assigned, routed, setRoute: value => {route = value;}};
}
const startup = boot('/desk', null, false);
assert.doesNotThrow(() => startup.events.app_ready());
assert.doesNotThrow(() => startup.events.change());
assert.doesNotThrow(() => startup.events.tick());
assert.equal(startup.assigned.length, 0);
startup.setRoute(['']);
startup.events.tick();
assert.equal(startup.assigned.length, 0, 'do not route while native Desk sidebar is uninitialized');
startup.context.frappe.app.sidebar = {};
startup.events.tick();
assert.equal(startup.assigned[0], '/desk/ledger-workbench/dashboard');
const form = boot('/desk/journal-entry/SYNTHETIC', null, true);
assert.doesNotThrow(() => form.events.app_ready());
assert.doesNotThrow(() => form.events.tick());
assert.equal(form.assigned.length + form.routed.length, 0);
const journalList = boot('/app/journal-entry', ['List', 'Journal Entry', 'List'], true,
  '13283112536@163.com', ['账套管理员']);
journalList.events.app_ready();
assert.equal(JSON.stringify(journalList.routed[0]), JSON.stringify(['ledger-workbench', 'accounting']));
const legacy = boot('/desk/setup/home', ['Workspaces', 'Home'], true);
legacy.events.app_ready();
assert.equal(JSON.stringify(legacy.routed[0]), JSON.stringify(['ledger-workbench', 'dashboard']));
console.log('PASS: actual Desk script waits for native sidebar, redirects the unsafe Journal Entry list, preserves forms and strict home redirect');
"""
    result = subprocess.run(["node", "-e", harness, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def _run_review_component_case(name):
    import subprocess

    result = subprocess.run(["node", str(ROOT / "tests/frontend_review.cjs"), name], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_review_batch_scope_matches_list_selection_and_confirmation():
    _run_review_component_case("batch_scope")


def test_auto_classification_frontend():
    _run_review_component_case("auto_classification")


def test_classification_detail_frontend():
    _run_review_component_case("classification_detail")


def test_classification_select_all_and_one_click_retry_frontend():
    _run_review_component_case("classification_batch_selection")


def test_transaction_evidence_admin_frontend():
    _run_review_component_case("transaction_evidence_admin")


def test_transaction_evidence_viewer_frontend():
    _run_review_component_case("transaction_evidence_viewer")


def test_transaction_evidence_failed_read_requires_retry():
    _run_review_component_case("transaction_evidence_retry")


def test_review_refresh_preserves_exclusions_and_invalidates_confirmation():
    _run_review_component_case("refresh_selection")


def test_review_validation_error_opens_existing_manual_form_only_for_writers():
    _run_review_component_case("validation_form")


def test_pending_duplicate_card_and_review_queue_exclude_confirmed_history():
    _run_review_component_case("pending_duplicates")


def test_import_result_carries_batch_and_displays_total():
    _run_review_component_case("import_scope_total")


def test_import_result_displays_total_count():
    _run_review_component_case("import_total")


def test_confirmed_import_automatically_continues_to_current_batch_workflow():
    _run_review_component_case("import_auto_route")


def test_workflow_navigation_has_five_steps_and_bottom_previous_next_actions():
    _run_review_component_case("workflow_navigation")


def test_review_center_keeps_ten_item_pagination_with_previous_next_controls():
    _run_review_component_case("review_pagination")


def test_duplicate_review_is_one_item_per_page_with_previous_next_controls():
    _run_review_component_case("duplicate_pagination")


def test_filter_navigation_and_viewer_classification_results():
    _run_review_component_case("navigation_filter_consistency")


def test_classification_discards_stale_responses_and_refreshes():
    _run_review_component_case("classification_stale_requests")


def test_evidence_entry_points_preserve_transaction_identity():
    _run_review_component_case("evidence_entry_points")


def test_classification_failure_is_shown_without_technical_id():
    _run_review_component_case("classification_failure_display")


def test_accounting_center_exposes_drafts_formal_vouchers_and_general_ledger():
    _run_review_component_case("accounting_center")


def test_workbench_route_passes_batch_through_cached_mount_and_app():
    _run_review_component_case("route_scope")


def test_app_initial_request_initializes_review_selection_after_response():
    _run_review_component_case("async_initial_selection")


def test_pending_review_refresh_disables_both_confirmation_buttons():
    _run_review_component_case("pending_buttons")


def test_pending_review_refresh_handlers_cannot_send_posting_rpc():
    _run_review_component_case("pending_handlers")


def test_review_duplicate_link_preserves_batch_and_selected_transaction():
    _run_review_component_case("duplicate_scope_link")


def test_duplicate_component_enforces_scope_and_preserves_legacy_history():
    _run_review_component_case("duplicate_scoped_component")


def test_duplicate_route_and_app_preserve_selected_transaction():
    _run_review_component_case("duplicate_route_scope")


def test_first_failed_review_request_does_not_consume_successful_initialization():
    _run_review_component_case("retry_after_failure")


def test_obsolete_refresh_cannot_unlock_posting_while_latest_request_is_pending():
    _run_review_component_case("overlap_pending")


def test_late_obsolete_refresh_cannot_overwrite_current_review_items():
    _run_review_component_case("overlap_stale_last")


def test_failed_current_list_freezes_posting_until_revalidated():
    _run_review_component_case("latest_list_failure")


def test_dashboard_only_displays_latest_counts_for_the_selected_month():
    _run_review_component_case("current_month_counts")


def test_viewer_cannot_send_a_posting_rpc_after_successful_list():
    _run_review_component_case("viewer_posting")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in sorted(globals().items()) if name.startswith("test_"))
