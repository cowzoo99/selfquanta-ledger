import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import frappe


PASSWORD_COMMAND = "frappe.core.doctype.user.user.update_password"


def member():
    return frappe.get_doc({
        "doctype": "User", "email": f"member-{uuid4().hex}@example.com",
        "first_name": "Member", "send_welcome_email": 0,
        "internal_ledger_must_change_password": 1,
    }).insert(ignore_permissions=True)


def test_temporary_password_field_is_installed_idempotently():
    from internal_ledger.install import ensure_roles

    ensure_roles()
    ensure_roles()
    assert frappe.db.count("Custom Field", {
        "dt": "User", "fieldname": "internal_ledger_must_change_password",
    }) == 1
    field = frappe.get_meta("User").get_field("internal_ledger_must_change_password")
    assert field.fieldtype == "Check" and field.hidden and field.no_copy and field.read_only
    assert field.default == "0"


def test_metadata_changes_are_audited_once():
    from internal_ledger.install import ensure_roles

    frappe.db.set_value("Custom Field", "User-internal_ledger_must_change_password", "hidden", 0)
    with patch("internal_ledger.install.audit_install_change") as audit:
        ensure_roles()
        assert audit.call_count == 1
        ensure_roles()
        assert audit.call_count == 1


def test_password_validation_rejects_mismatch_and_policy_failure():
    from internal_ledger.member_security import validate_temporary_password

    with unittest.TestCase().assertRaises(frappe.ValidationError):
        validate_temporary_password("Valid-looking-123!", "different")
    with patch("internal_ledger.member_security.test_password_strength", return_value={
        "feedback": {"password_policy_validation_passed": False},
    }):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            validate_temporary_password("weak", "weak", ("Member", "", "", "member@example.invalid", None))


def test_password_validation_uses_frappe_policy_and_user_data():
    from internal_ledger.member_security import validate_temporary_password

    data = ("Member", "", "", "member@example.invalid", None)
    for result in ({}, {"feedback": {"password_policy_validation_passed": True}}):
        with patch("internal_ledger.member_security.test_password_strength", return_value=result) as policy:
            validate_temporary_password("Valid-looking-123!", "Valid-looking-123!", data)
            policy.assert_called_once_with("Valid-looking-123!", user_data=data)


def test_revoke_sessions_removes_cache_and_database_without_commit():
    from internal_ledger.member_security import revoke_sessions

    user = member().name
    sid = uuid4().hex
    frappe.db.sql("insert into tabSessions (user, sid) values (%s, %s)", (user, sid))
    frappe.cache.hset("session", sid, {"user": user})
    with patch.object(frappe.db, "commit", side_effect=AssertionError("Unexpected commit")):
        revoke_sessions(user)
    assert frappe.db.sql("select sid from tabSessions where user=%s", (user,)) == ()
    assert not frappe.cache.hget("session", sid)


def test_session_revocation_failures_propagate():
    from internal_ledger.member_security import revoke_sessions

    user = member().name
    sid = uuid4().hex
    frappe.db.sql("insert into tabSessions (user, sid) values (%s, %s)", (user, sid))
    with patch.object(frappe.cache, "hdel", side_effect=RuntimeError("Cache unavailable")):
        with unittest.TestCase().assertRaises(RuntimeError):
            revoke_sessions(user)
    assert frappe.db.sql("select sid from tabSessions where user=%s", (user,))
    with patch.object(frappe.db, "delete", side_effect=RuntimeError("Database unavailable")):
        with unittest.TestCase().assertRaises(RuntimeError):
            revoke_sessions(user)


def test_login_generates_reset_redirect_without_email():
    from internal_ledger.member_security import redirect_temporary_password

    doc = member()
    try:
        with patch.object(type(doc), "password_reset_mail", side_effect=AssertionError("Unexpected email")):
            redirect_temporary_password(SimpleNamespace(user=doc.name))
        url = frappe.cache.hget("redirect_after_login", doc.name)
        assert "/update-password?key=" in url
        assert frappe.db.get_value("User", doc.name, "reset_password_key")
    finally:
        frappe.cache.hdel("redirect_after_login", doc.name)
    frappe.db.set_value("User", doc.name, "internal_ledger_must_change_password", 0)
    with patch.object(type(doc), "_reset_password", side_effect=AssertionError("Unexpected reset")):
        redirect_temporary_password(SimpleNamespace(user=doc.name))


def test_flagged_session_allows_only_reset_and_logout():
    from internal_ledger.member_security import block_temporary_password_session

    frappe.set_user(member().name)
    commands = ("frappe.core.doctype.user.user.test_password_strength", PASSWORD_COMMAND,
                "logout", "frappe.auth.logout")
    for command in commands:
        frappe.local.form_dict = frappe._dict(cmd=command)
        frappe.local.request = SimpleNamespace(path="/")
        block_temporary_password_session()
        frappe.local.form_dict = frappe._dict()
        frappe.local.request = SimpleNamespace(path=f"/api/method/{command}")
        block_temporary_password_session()
    frappe.local.request = SimpleNamespace(path="/update-password")
    block_temporary_password_session()
    for path, command in (("/desk", None), ("/api/resource/User", None),
                          ("/api/method/frappe.client.get_list", None),
                          ("/update-password", "frappe.client.get_list")):
        frappe.local.request = SimpleNamespace(path=path)
        frappe.local.form_dict = frappe._dict(cmd=command)
        with unittest.TestCase().assertRaisesRegex(frappe.AuthenticationError, "请先修改临时密码"):
            block_temporary_password_session()


def test_temporary_password_guard_runs_before_request_and_after_token_authentication():
    guard = "internal_ledger.member_security.block_temporary_password_session"
    assert guard in frappe.get_hooks("before_request")
    assert guard in frappe.get_hooks("auth_hooks")


def test_login_home_page_redirect_is_limited_to_flagged_login_responses():
    from internal_ledger.member_security import block_temporary_password_session

    doc = member()
    frappe.set_user(doc.name)
    try:
        for command in ("login", "frappe.auth.login"):
            frappe.local.form_dict = frappe._dict(cmd=command)
            frappe.local.request = SimpleNamespace(path="/")
            frappe.local.response.update(home_page="original", redirect_to="reset-target")
            block_temporary_password_session()
            assert frappe.local.response.home_page == "reset-target"
        frappe.local.response.pop("redirect_to")
        frappe.local.response.home_page = "original"
        block_temporary_password_session()
        assert frappe.local.response.home_page == "original"
        frappe.db.set_value("User", doc.name, "internal_ledger_must_change_password", 0)
        frappe.local.response.redirect_to = "reset-target"
        block_temporary_password_session()
        assert frappe.local.response.home_page == "original"
    finally:
        frappe.local.response.pop("redirect_to", None)
        frappe.local.response.pop("home_page", None)


def test_flagged_api_token_cannot_bypass_reset_requirement():
    from frappe.auth import validate_auth
    from werkzeug.wrappers import Request

    from internal_ledger.member_security import block_temporary_password_session

    doc = member()
    doc.api_key = uuid4().hex
    doc.api_secret = uuid4().hex
    secret = doc.api_secret
    doc.save(ignore_permissions=True)
    for path in ("/api/resource/User", "/api/method/frappe.client.get_list"):
        frappe.set_user("Guest")
        frappe.local.request = Request.from_values(path=path, headers={
            "Authorization": f"token {doc.api_key}:{secret}",
        })
        block_temporary_password_session()
        with patch.object(frappe.local, "login_manager", SimpleNamespace(user="Guest"), create=True):
            with unittest.TestCase().assertRaisesRegex(frappe.AuthenticationError, "请先修改临时密码"):
                validate_auth()
        assert frappe.session.user == doc.name
    for path in ("/update-password", f"/api/method/{PASSWORD_COMMAND}",
                 "/api/method/frappe.core.doctype.user.user.test_password_strength",
                 "/api/method/logout", "/api/method/frappe.auth.logout"):
        frappe.set_user("Guest")
        frappe.local.request = Request.from_values(path=path, headers={
            "Authorization": f"token {doc.api_key}:{secret}",
        })
        with patch.object(frappe.local, "login_manager", SimpleNamespace(user="Guest"), create=True):
            validate_auth()
        assert frappe.session.user == doc.name


def test_post_login_flagged_session_can_deliver_reset_redirect():
    from internal_ledger.member_security import block_temporary_password_session, redirect_temporary_password

    doc = member()
    frappe.set_user(doc.name)
    try:
        for command in ("login", "frappe.auth.login"):
            frappe.local.form_dict = frappe._dict(cmd=command)
            frappe.local.request = None
            redirect_temporary_password(SimpleNamespace(user=doc.name))
            frappe.local.request = SimpleNamespace(path="/")
            block_temporary_password_session()
            assert "/update-password?key=" in frappe.cache.hget("redirect_after_login", doc.name)
    finally:
        frappe.cache.hdel("redirect_after_login", doc.name)


def test_unflagged_guest_and_administrator_requests_are_unchanged():
    from internal_ledger.member_security import block_temporary_password_session

    doc = member()
    frappe.db.set_value("User", doc.name, "internal_ledger_must_change_password", 0)
    frappe.local.request = SimpleNamespace(path="/desk")
    for user in ("Guest", "Administrator", doc.name):
        frappe.set_user(user)
        block_temporary_password_session()


def test_successful_core_password_update_clears_flag():
    from frappe.core.doctype.user.user import update_password

    doc = member()
    url = doc._reset_password(send_email=False)
    frappe.local.form_dict = frappe._dict(cmd=PASSWORD_COMMAND)
    with patch("frappe.core.doctype.user.user.test_password_strength", return_value={}):
        with patch("frappe.core.doctype.user.user._update_password", side_effect=RuntimeError("Password failure")):
            with unittest.TestCase().assertRaises(RuntimeError):
                update_password("New-member-password-123!", key=url.split("key=")[1])
        assert frappe.db.get_value("User", doc.name, "internal_ledger_must_change_password") == 1
        with patch.object(frappe.local, "login_manager", SimpleNamespace(login_as=Mock()), create=True):
            update_password("New-member-password-123!", key=url.split("key=")[1])
    assert frappe.db.get_value("User", doc.name, "internal_ledger_must_change_password") == 0


def test_forgot_password_validation_does_not_clear_flag():
    doc = member()
    frappe.local.form_dict = frappe._dict(cmd="frappe.core.doctype.user.user.reset_password")
    doc.validate_reset_password()
    assert frappe.db.get_value("User", doc.name, "internal_ledger_must_change_password") == 1
    doc._reset_password(send_email=False)
    doc.reload().validate_reset_password()
    assert frappe.db.get_value("User", doc.name, "internal_ledger_must_change_password") == 1



MEMBER_PASSWORD = "Temp-Only-938!x"


def create_member(email=None, first_name="只读成员"):
    from internal_ledger.api.member_api import create_enterprise_member

    return create_enterprise_member(first_name, email or f"member-{uuid4().hex}@example.invalid",
                                    MEMBER_PASSWORD, MEMBER_PASSWORD)


def company_permission(user, company):
    return frappe.get_doc({"doctype": "User Permission", "user": user, "allow": "Company",
                           "for_value": company, "apply_to_all_doctypes": 1}).insert(ignore_permissions=True)


def member_company_reference():
    # Only the Company link/row lock is needed; bypass chart/bootstrap side effects in this fixture.
    name = f"_Test Member Company {uuid4().hex}"
    frappe.get_doc({"doctype": "Company", "name": name, "company_name": name,
                    "abbr": uuid4().hex[:5], "default_currency": "CNY", "country": "China"}).db_insert()
    return name


def assert_no_member(email):
    assert not frappe.db.exists("User", email)
    assert not frappe.db.count("Has Role", {"parenttype": "User", "parent": email})
    assert not frappe.db.count("User Permission", {"user": email})
    assert not frappe.db.count("Internal Ledger Audit Event", {"reference_doctype": "User",
                                                               "reference_name": email})


def test_member_only_ledger_administrators_can_list_and_create():
    from internal_ledger.api.member_api import list_enterprise_members
    from internal_ledger.permissions import record_member_audit
    from internal_ledger.tests.test_permissions import user

    admin = user("账套管理员")
    actors = (user("账务查看者"), user("System Manager"), "Guest")
    company = frappe.flags.classification_company
    for actor in actors:
        frappe.set_user(actor)
        for operation in (list_enterprise_members, create_member,
                          lambda: record_member_audit("create_enterprise_member", actor, company)):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()
    for actor in ("Administrator", admin):
        frappe.set_user(actor)
        created = create_member()
        assert created["email"] in {row["email"] for row in list_enterprise_members()["members"]}


def test_member_creation_is_normalized_read_only_and_secret_free():
    from internal_ledger.api.member_api import create_enterprise_member

    created = create_enterprise_member("  只读成员  ", " Member.One@Example.Invalid ",
                                       MEMBER_PASSWORD, MEMBER_PASSWORD)
    assert created == {"email": "member.one@example.invalid", "full_name": "只读成员",
                       "enabled": 1, "must_change_password": 1}
    email = created["email"]
    assert frappe.db.exists("Has Role", {"parenttype": "User", "parent": email, "role": "账务查看者"})
    assert frappe.db.count("Has Role", {"parent": email}) == 1
    assert frappe.db.exists("User Permission", {"user": email, "allow": "Company",
                                                "for_value": frappe.flags.classification_company})
    doc = frappe.get_doc("User", email)
    assert doc.user_type == "System User" and not doc.send_welcome_email
    assert doc.internal_ledger_must_change_password == 1
    events = frappe.get_all("Internal Ledger Audit Event", filters={"reference_name": email}, fields=["*"])
    assert len(events) == 1
    event = events[0]
    assert event.action == "create_enterprise_member" and event.reference_doctype == "User"
    assert event.company == frappe.flags.classification_company and event.result == "success"
    assert event.actor == "Administrator" and event.timestamp
    assert MEMBER_PASSWORD not in frappe.as_json({"response": created, "audit": events})


def test_member_listing_intersects_exact_company_and_viewer_role():
    from internal_ledger.api.member_api import _managed_member, list_enterprise_members
    from internal_ledger.tests.test_permissions import user

    company = frappe.flags.classification_company
    enabled = [create_member(first_name=name) for name in ("Bravo", "Alpha", "Alpha")]
    disabled = create_member(first_name="Aaron")
    frappe.db.set_value("User", disabled["email"], "enabled", 0)
    role_only, foreign = user("账务查看者"), user("账务查看者")
    company_only = user("System Manager")
    company_permission(company_only, company)
    company_permission(foreign, member_company_reference())
    for special in ("Administrator", "Guest"):
        frappe.get_doc({"doctype": "Has Role", "parent": special, "parenttype": "User",
                        "parentfield": "roles", "role": "账务查看者"}).db_insert()
        company_permission(special, company)
    result = list_enterprise_members()
    assert result["company"] == company
    assert (result["enabled_count"], result["disabled_count"]) == (3, 1)
    expected = sorted(enabled, key=lambda row: (row["full_name"], row["email"])) + [disabled]
    assert [row["email"] for row in result["members"]] == [row["email"] for row in expected]
    for row in result["members"]:
        assert set(row) == {"email", "full_name", "enabled", "creation", "last_login"}
        assert isinstance(row["enabled"], int) and row["creation"]
    assert _managed_member(disabled["email"], company, for_update=True).name == disabled["email"]
    for target in (role_only, company_only, foreign, "Administrator", "Guest", "missing@example.invalid"):
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            _managed_member(target, company)


def test_member_invalid_input_and_duplicates_leave_no_partial_accounts():
    from internal_ledger.api.member_api import create_enterprise_member, list_enterprise_members

    for first_name, email, password, confirmation in (
        (" ", "blank-name@example.invalid", MEMBER_PASSWORD, MEMBER_PASSWORD),
        ("Member", "not-an-email", MEMBER_PASSWORD, MEMBER_PASSWORD),
        ("Member", "multiple@example.invalid,other@example.invalid", MEMBER_PASSWORD, MEMBER_PASSWORD),
        ("Member", "mismatch@example.invalid", MEMBER_PASSWORD, "different"),
        ("Member", "empty-password@example.invalid", "", ""),
    ):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            create_enterprise_member(first_name, email, password, confirmation)
        assert_no_member(email)
    with patch("internal_ledger.member_security.test_password_strength", return_value={
        "feedback": {"password_policy_validation_passed": False},
    }):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            create_enterprise_member("Member", "weak@example.invalid", "weak", "weak")
    assert_no_member("weak@example.invalid")
    existing = create_member()
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "^该登录邮箱已存在$"):
        create_member(existing["email"].upper())
    assert frappe.db.count("User Permission", {"user": existing["email"]}) == 1
    with unittest.TestCase().assertRaises(TypeError):
        create_enterprise_member("Member", "roles@example.invalid", MEMBER_PASSWORD, MEMBER_PASSWORD,
                                 roles=["System Manager"])
    assert_no_member("roles@example.invalid")
    assert frappe.allowed_http_methods_for_whitelisted_func[create_enterprise_member] == ["POST"]
    assert frappe.allowed_http_methods_for_whitelisted_func[list_enterprise_members] == ["GET"]
    frappe.db.set_single_value("Internal Ledger Settings", "company", None)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        create_member("unconfigured@example.invalid")
    assert_no_member("unconfigured@example.invalid")



def test_member_audit_cannot_be_written_for_another_company():
    from internal_ledger.permissions import record_member_audit

    created = create_member()
    before = frappe.db.count("Internal Ledger Audit Event", {"reference_name": created["email"]})
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        record_member_audit("create_enterprise_member", created["email"], member_company_reference())
    assert frappe.db.count("Internal Ledger Audit Event", {"reference_name": created["email"]}) == before

def test_member_each_insert_failure_rolls_back_all_member_rows():
    from frappe.model.document import Document

    original = Document.db_insert
    for stage in ("User", "Has Role", "User Permission", "Internal Ledger Audit Event"):
        email = f"rollback-{uuid4().hex}@example.invalid"
        frappe.db.savepoint("member_failure_test")

        def fail_after_insert(doc, *args, **kwargs):
            result = original(doc, *args, **kwargs)
            if doc.doctype == stage:
                raise RuntimeError("injected insert failure")
            return result

        with patch.object(Document, "db_insert", fail_after_insert):
            with unittest.TestCase().assertRaisesRegex(RuntimeError, "injected insert failure"):
                create_member(email)
        assert_no_member(email)
        frappe.db.rollback(save_point="member_failure_test")


def test_member_concurrent_duplicate_uses_independent_database_connections():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from internal_ledger.api.member_api import create_enterprise_member

    email = f"concurrent-{uuid4().hex}@example.invalid"
    site, sites_path = frappe.local.site, frappe.local.sites_path
    barrier = Barrier(2)
    # Discard the chart fixture before committing an isolated Company link for both workers.
    frappe.db.rollback()
    frappe.clear_cache()
    original_company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    company = member_company_reference()
    frappe.db.set_single_value("Internal Ledger Settings", "company", company)
    frappe.db.commit()

    def worker(address):
        frappe.init(site=site, sites_path=sites_path)
        frappe.connect()
        frappe.flags.in_test = True
        try:
            connection = frappe.db.sql("select connection_id()")[0][0]
            barrier.wait(timeout=30)
            try:
                result = create_enterprise_member("Concurrent", address, MEMBER_PASSWORD, MEMBER_PASSWORD)
                frappe.db.commit()
                return connection, "success", result["email"]
            except frappe.ValidationError as exc:
                frappe.db.rollback()
                return connection, "conflict", str(exc)
        finally:
            frappe.destroy()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, address) for address in (email, f" {email.upper()} ")]
            results = [future.result(timeout=60) for future in futures]
        frappe.db.rollback()
        assert len({row[0] for row in results}) == 2
        assert sorted(row[1] for row in results) == ["conflict", "success"]
        assert next(row[2] for row in results if row[1] == "conflict") == "该登录邮箱已存在"
        assert frappe.db.count("User", {"name": email}) == 1
        assert frappe.db.count("User Permission", {"user": email}) == 1
        assert frappe.db.count("Has Role", {"parenttype": "User", "parent": email}) == 1
        assert frappe.db.count("Internal Ledger Audit Event", {"reference_doctype": "User",
                                                              "reference_name": email}) == 1
    finally:
        frappe.db.delete("Internal Ledger Audit Event", {"reference_doctype": "User", "reference_name": email})
        frappe.db.delete("User Permission", {"user": email})
        frappe.delete_doc("User", email, force=True, ignore_permissions=True, ignore_missing=True)
        frappe.db.set_single_value("Internal Ledger Settings", "company", original_company)
        frappe.db.delete("Company", {"name": company})
        frappe.db.commit()



def test_member_five_viewers_and_admin_preview_capacity_smoke():
    import base64
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from time import monotonic

    from internal_ledger.api.import_api import preview_import
    from internal_ledger.api.review_api import get_dashboard_counts
    from internal_ledger.tests.test_importers import fixture

    site, sites_path = frappe.local.site, frappe.local.sites_path
    frappe.db.rollback()
    frappe.clear_cache()
    original_company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    company = member_company_reference()
    frappe.db.set_single_value("Internal Ledger Settings", "company", company)
    viewers = []
    try:
        for _ in range(5):
            email = create_member()["email"]
            viewers.append(email)
            frappe.db.set_value("User", email, "internal_ledger_must_change_password", 0)
        frappe.db.commit()
        baseline = frappe.db.count("Import Batch")
        encoded = base64.b64encode(fixture("bank")).decode()
        barrier = Barrier(6, timeout=30)

        def worker(actor):
            frappe.init(site=site, sites_path=sites_path)
            frappe.connect()
            try:
                frappe.flags.in_test = True
                frappe.set_user(actor)
                connection = frappe.db.sql("select connection_id()")[0][0]
                frappe.db.sql("set session innodb_lock_wait_timeout = 20")
                barrier.wait()
                if actor == "Administrator":
                    before = frappe.db.count("Import Batch")
                    result = preview_import("bank", encoded, "test payment account")
                    assert frappe.db.count("Import Batch") == before
                else:
                    result = get_dashboard_counts("2026-10")
                return connection, result
            finally:
                frappe.destroy()

        started = monotonic()
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(worker, actor) for actor in viewers + ["Administrator"]]
            results = [future.result(timeout=30) for future in futures]
        assert monotonic() - started < 30
        assert len({connection for connection, _ in results}) == 6
        counts = [result for _, result in results[:5]]
        assert all(row == counts[0] for row in counts)
        assert counts[0] == {"month": "2026-10", "income": "0", "expense": "0",
                             "pending_classification": 0, "pending_review": 0, "posted": 0}
        assert len(results[-1][1]["rows"]) == 3 and not results[-1][1]["errors"]
        frappe.db.rollback()
        assert frappe.db.count("Import Batch") == baseline
    finally:
        frappe.set_user("Administrator")
        for email in viewers:
            frappe.db.delete("Internal Ledger Audit Event", {"reference_doctype": "User", "reference_name": email})
            frappe.db.delete("User Permission", {"user": email})
            frappe.delete_doc("User", email, force=True, ignore_permissions=True, ignore_missing=True)
        frappe.db.set_single_value("Internal Ledger Settings", "company", original_company)
        frappe.db.delete("Company", {"name": company})
        frappe.db.commit()
        frappe.clear_cache()


def member_sessions(email):
    sessions = [uuid4().hex, uuid4().hex]
    frappe.flags.member_test_sessions = frappe.flags.get("member_test_sessions", []) + sessions
    for sid in sessions:
        frappe.db.sql("insert into tabSessions (user, sid) values (%s, %s)", (email, sid))
        frappe.cache.hset("session", sid, {"user": email})
    return sessions


def member_audits(email):
    return frappe.get_all("Internal Ledger Audit Event", filters={"reference_name": email},
                          fields=["*"], order_by="creation asc")


def test_member_enable_disable_preserves_membership_and_revokes_sessions():
    from internal_ledger.api.member_api import set_enterprise_member_enabled

    email = create_member()["email"]
    sessions = member_sessions(email)
    roles = frappe.get_all("Has Role", filters={"parent": email}, fields=["name", "role"])
    permissions = frappe.get_all("User Permission", filters={"user": email}, fields=["name", "for_value"])
    with patch.object(frappe.db, "commit", side_effect=AssertionError("Unexpected commit")):
        assert set_enterprise_member_enabled(email, 0) == {"email": email, "enabled": 0}
        assert frappe.db.get_value("User", email, "enabled") == 0
        assert frappe.db.count("Sessions", {"user": email}) == 0
        assert all(not frappe.cache.hget("session", sid) for sid in sessions)
        assert set_enterprise_member_enabled(email, 1) == {"email": email, "enabled": 1}
    assert frappe.db.get_value("User", email, "enabled") == 1
    assert frappe.get_all("Has Role", filters={"parent": email}, fields=["name", "role"]) == roles
    assert frappe.get_all("User Permission", filters={"user": email}, fields=["name", "for_value"]) == permissions
    assert [event.action for event in member_audits(email)] == [
        "create_enterprise_member", "disable_enterprise_member", "enable_enterprise_member"]
    # Enabling an already enabled member must not log out an active session.
    member_sessions(email)
    assert set_enterprise_member_enabled(email, True)["enabled"] == 1
    assert frappe.db.count("Sessions", {"user": email}) == 2
    assert frappe.allowed_http_methods_for_whitelisted_func[set_enterprise_member_enabled] == ["POST"]


def test_member_reset_replaces_password_without_email_commit_or_secret_output():
    from frappe.utils.password import check_password
    from internal_ledger.api.member_api import reset_enterprise_member_password

    email = create_member()["email"]
    frappe.db.set_value("User", email, "internal_ledger_must_change_password", 0)
    sessions = member_sessions(email)
    password = uuid4().hex + "!aB7"
    with patch.object(frappe.db, "commit", side_effect=AssertionError("Unexpected commit")), \
            patch("frappe.sendmail", side_effect=AssertionError("Unexpected email")):
        result = reset_enterprise_member_password(email, password, password)
    assert result == {"email": email, "must_change_password": 1}
    assert check_password(email, password) == email
    with unittest.TestCase().assertRaises(frappe.AuthenticationError):
        check_password(email, MEMBER_PASSWORD)
    assert frappe.db.get_value("User", email, "internal_ledger_must_change_password") == 1
    assert not frappe.db.count("Sessions", {"user": email})
    assert all(not frappe.cache.hget("session", sid) for sid in sessions)
    audits = member_audits(email)
    assert audits[-1].action == "reset_enterprise_member_password"
    assert audits[-1].actor == "Administrator" and audits[-1].company == frappe.flags.classification_company
    assert password not in frappe.as_json({"response": result, "audit": audits})
    assert MEMBER_PASSWORD not in frappe.as_json({"response": result, "audit": audits})
    assert frappe.allowed_http_methods_for_whitelisted_func[reset_enterprise_member_password] == ["POST"]


def test_member_lifecycle_denies_unauthorized_actors_and_nonmembers():
    from internal_ledger.api.member_api import reset_enterprise_member_password, set_enterprise_member_enabled
    from internal_ledger.tests.test_permissions import user

    email = create_member()["email"]
    company = frappe.flags.classification_company
    role_only, foreign, company_only = user("账务查看者"), user("账务查看者"), user("System Manager")
    company_permission(foreign, member_company_reference())
    company_permission(company_only, company)
    password = uuid4().hex + "!aB7"
    operations = (lambda target: set_enterprise_member_enabled(target, 0),
                  lambda target: reset_enterprise_member_password(target, password, password))
    for actor in (email, company_only, "Guest"):
        frappe.set_user(actor)
        for operation in operations:
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation(email)
    frappe.set_user("Administrator")
    for target in ("Administrator", "Guest", role_only, foreign, company_only, "missing@example.invalid"):
        for operation in operations:
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation(target)
    assert frappe.db.get_value("User", email, "enabled") == 1
    assert len(member_audits(email)) == 1
    admin = user("账套管理员")
    frappe.set_user(admin)
    assert set_enterprise_member_enabled(email, 0)["enabled"] == 0
    assert reset_enterprise_member_password(email, password, password)["must_change_password"] == 1
    frappe.set_user("Administrator")
    frappe.db.set_single_value("Internal Ledger Settings", "company", None)
    for operation in operations:
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            operation(email)


def test_member_lifecycle_validates_inputs_before_mutation():
    from frappe.utils.password import check_password
    from internal_ledger.api.member_api import reset_enterprise_member_password, set_enterprise_member_enabled

    email = create_member()["email"]
    for value in (-1, 2, "false", "", None, [], {}, 0.5):
        with unittest.TestCase().assertRaises((frappe.ValidationError, TypeError)):
            set_enterprise_member_enabled(email, value)
    password = uuid4().hex + "!aB7"
    for temporary, confirmation in (("", ""), (password, "different"), (None, None)):
        with unittest.TestCase().assertRaises((frappe.ValidationError, TypeError)):
            reset_enterprise_member_password(email, temporary, confirmation)
    with patch("internal_ledger.member_security.test_password_strength", return_value={
        "feedback": {"password_policy_validation_passed": False},
    }):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            reset_enterprise_member_password(email, password, password)
    assert frappe.db.get_value("User", email, "enabled") == 1
    assert check_password(email, MEMBER_PASSWORD) == email
    assert len(member_audits(email)) == 1
    # Frappe normalizes annotated integer arguments before invoking the service.
    for value, expected in ((False, 0), (0.0, 0), ("0", 0), (True, 1), (1.0, 1), ("1", 1)):
        assert set_enterprise_member_enabled(email, value) == {"email": email, "enabled": expected}


def test_member_lifecycle_failures_roll_back_password_sessions_state_and_audit():
    from frappe.utils.password import check_password
    from internal_ledger.api.member_api import reset_enterprise_member_password, set_enterprise_member_enabled
    from internal_ledger.permissions import record_member_audit

    email = create_member()["email"]
    frappe.db.set_value("User", email, "internal_ledger_must_change_password", 0)
    sessions = member_sessions(email)
    before = member_audits(email)
    password = uuid4().hex + "!aB7"

    def fail_after_audit(*args, **kwargs):
        record_member_audit(*args, **kwargs)
        raise RuntimeError("injected lifecycle failure")

    for operation in (lambda: set_enterprise_member_enabled(email, 0),
                      lambda: reset_enterprise_member_password(email, password, password)):
        for failure in (patch.object(frappe.cache, "hdel", side_effect=RuntimeError("injected lifecycle failure")),
                        patch("internal_ledger.api.member_api.record_member_audit", side_effect=fail_after_audit)):
            frappe.db.savepoint("lifecycle_failure_test")
            with failure, patch.object(frappe.db, "commit", side_effect=AssertionError("Unexpected commit")):
                with unittest.TestCase().assertRaisesRegex(RuntimeError, "injected lifecycle failure"):
                    operation()
            # The service itself must restore DB state before the caller's rollback.
            for rollback in (False, True):
                if rollback:
                    frappe.db.rollback(save_point="lifecycle_failure_test")
                assert frappe.db.get_value("User", email, "enabled") == 1
                assert frappe.db.get_value("User", email, "internal_ledger_must_change_password") == 0
                assert check_password(email, MEMBER_PASSWORD) == email
                with unittest.TestCase().assertRaises(frappe.AuthenticationError):
                    check_password(email, password)
                assert frappe.db.count("Sessions", {"user": email}) == 2
                assert member_audits(email) == before
    for sid in sessions:
        frappe.cache.hdel("session", sid)


def test_member_temporary_login_requires_core_password_reset_before_viewer_api():
    from frappe.auth import CookieManager, LoginManager
    from frappe.core.doctype.user.user import update_password
    from werkzeug.wrappers import Request
    from internal_ledger.api.member_api import reset_enterprise_member_password
    from internal_ledger.api.review_api import get_dashboard_counts
    from internal_ledger.member_security import block_temporary_password_session

    email = create_member()["email"]
    password, final_password = uuid4().hex + "!aB7", uuid4().hex + "!aB7"
    reset_enterprise_member_password(email, password, password)
    frappe.set_user("Guest")
    frappe.local.request = Request.from_values(path="/api/method/login", method="POST")
    frappe.local.request_ip = "127.0.0.1"
    frappe.local.cookie_manager = CookieManager()
    frappe.local.form_dict = frappe._dict(cmd="login", usr=email, pwd=password)
    login_manager = LoginManager.__new__(LoginManager)
    login_manager.user = login_manager.info = login_manager.full_name = login_manager.user_type = None
    # Core login/session code commits; keep its actual writes isolated in this test transaction.
    with patch.object(frappe.db, "commit"), \
            patch.object(frappe.local, "login_manager", login_manager, create=True), \
            patch("frappe.sendmail", side_effect=AssertionError("Unexpected email")):
        login_manager.login()
        assert frappe.session.user == email
        redirect = frappe.local.response.get("redirect_to", "")
        assert "/update-password?key=" in redirect
        block_temporary_password_session()
        assert frappe.local.response.get("home_page") == redirect
        frappe.local.form_dict = frappe._dict(cmd="internal_ledger.api.review_api.get_dashboard_counts")
        frappe.local.request = Request.from_values(path="/api/method/internal_ledger.api.review_api.get_dashboard_counts")
        with unittest.TestCase().assertRaises(frappe.AuthenticationError):
            block_temporary_password_session()
            get_dashboard_counts("2026-10")
        frappe.local.form_dict = frappe._dict(cmd=PASSWORD_COMMAND)
        update_password(final_password, key=redirect.split("key=", 1)[1])
        assert frappe.db.get_value("User", email, "internal_ledger_must_change_password") == 0
        frappe.local.form_dict = frappe._dict(cmd="internal_ledger.api.review_api.get_dashboard_counts")
        block_temporary_password_session()
        assert get_dashboard_counts("2026-10")["posted"] == 0
    for sid in frappe.db.sql("select sid from tabSessions where user=%s", (email,), pluck=True):
        frappe.cache.hdel("session", sid)
    frappe.local.response.pop("redirect_to", None)


class PasswordSafeFunctionTestCase(unittest.FunctionTestCase):
    def run(self, result=None):
        self._test_result = result
        return super().run(result)

    def setUp(self):
        # Frappe enables traceback locals; security tests must never print credentials or reset keys.
        self._test_result.tb_locals = False
        super().setUp()


def load_tests(loader, tests, pattern):
    def set_up(test):
        frappe.set_user("Administrator")
        frappe.local.form_dict = frappe._dict()
        if test.__name__.startswith("test_member_"):
            from internal_ledger.tests.test_classifier import set_up as company_set_up

            company_set_up()

    def tear_down():
        frappe.set_user("Administrator")
        for sid in frappe.flags.pop("member_test_sessions", []):
            frappe.cache.hdel("session", sid)
        # Custom Field updates run schema DDL, which commits and invalidates savepoints.
        frappe.db.rollback()
        frappe.local.form_dict = frappe._dict()
        frappe.local.request = None
        frappe.clear_cache()

    return unittest.TestSuite(PasswordSafeFunctionTestCase(test, setUp=lambda test=test: set_up(test), tearDown=tear_down)
                              for name, test in globals().items() if name.startswith("test_"))
