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


def load_tests(loader, tests, pattern):
    def set_up():
        frappe.set_user("Administrator")
        frappe.local.form_dict = frappe._dict()

    def tear_down():
        frappe.set_user("Administrator")
        # Custom Field updates run schema DDL, which commits and invalidates savepoints.
        frappe.db.rollback()
        frappe.local.form_dict = frappe._dict()
        frappe.local.request = None
        frappe.clear_cache()

    return unittest.TestSuite(unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
                              for name, test in globals().items() if name.startswith("test_"))
