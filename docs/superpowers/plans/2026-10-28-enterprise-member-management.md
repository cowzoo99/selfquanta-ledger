# Enterprise Member Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal administrator-only member page that creates, lists, disables, re-enables, and resets temporary passwords for read-only users of the deployment's single company.

**Architecture:** Keep `Company`, `User`, `Has Role`, and `User Permission` as the only membership data. Add focused member APIs plus one hidden custom `User` flag for temporary-password enforcement; enforce that flag with Frappe login, request, and password-reset hooks. Extend the existing Vue workbench with one administrator-only page and retain the existing server-side company permission boundary.

**Tech Stack:** Python 3.14, Frappe v16.52.0, ERPNext v16.52.1, MariaDB 11.8, Vue 3, Bench test runner, native Chrome DevTools Protocol.

**Spec:** `docs/superpowers/specs/2026-10-28-enterprise-member-management-design.md`

## Global Constraints

- Each deployment serves exactly one enterprise and one ledger; do not add shared-site multi-tenancy.
- A managed member is a standard non-special `User` with role `账务查看者` and a `User Permission` for the configured `Company`.
- Do not create an Enterprise Member DocType or duplicate names, email addresses, roles, passwords, or enabled state.
- Member APIs may create only `账务查看者`; they must never accept a client-supplied role list.
- `Administrator` and `账套管理员` may manage members; viewers and all other users may not call any member API.
- Never delete a member, create an administrator, or change administrator privileges through this feature.
- Temporary passwords, password hashes, reset keys, session IDs, and cookies must not appear in audit events, ordinary logs, API responses, fixtures, screenshots, or Git.
- Password creation and reset must use the configured Frappe password policy.
- Source-file denial and all existing single-company permission filters must remain unchanged.
- Use transactions and database uniqueness for concurrent duplicate-email arbitration; do not add an application mutex.
- Use no new runtime dependency.

## File Map

- Create `internal_ledger/api/member_api.py`: member queries and administrator-only lifecycle endpoints.
- Create `internal_ledger/member_security.py`: temporary-password login redirect, request guard, flag clearing, password validation, and transactional session revocation.
- Create `internal_ledger/tests/test_member_api.py`: membership, security, rollback, concurrency, and light-capacity tests.
- Create `internal_ledger/public/frontend/src/pages/EnterpriseMembers.vue`: member list and small create/reset dialogs.
- Modify `internal_ledger/install.py`: install the hidden temporary-password custom field idempotently.
- Modify `internal_ledger/hooks.py`: register User password, login, and request hooks.
- Modify `internal_ledger/permissions.py`: record member lifecycle audit events with the configured company.
- Modify `internal_ledger/public/frontend/src/api.js`: allow calls to the member API module without changing existing review calls.
- Modify `internal_ledger/public/frontend/src/App.vue`: administrator-only navigation and page mounting.
- Modify `internal_ledger/tests/test_end_to_end.py`: prove a created member retains the complete viewer read boundary.
- Modify `README.md` and `docs/acceptance/2026-10-28-mvp-checklist.md`: document the feature and fresh evidence.
- Add only sanitized PNGs under `docs/acceptance/screenshots/` if browser acceptance produces new member-page evidence.

---

### Task 1: Install temporary-password metadata and request enforcement

**Files:**
- Create: `internal_ledger/member_security.py`
- Modify: `internal_ledger/install.py`
- Modify: `internal_ledger/hooks.py`
- Test: `internal_ledger/tests/test_member_api.py`

**Interfaces:**
- Produces: `validate_temporary_password(password: str, confirm_password: str, user_data: tuple | None = None) -> None`
- Produces: `revoke_sessions(user: str) -> None`
- Produces: `redirect_temporary_password(login_manager) -> None`
- Produces: `block_temporary_password_session() -> None`
- Produces: `clear_temporary_password_flag(doc, method=None) -> None`
- Produces: custom User field `internal_ledger_must_change_password: Check`

- [ ] **Step 1: Write failing installation and security tests**

Create `internal_ledger/tests/test_member_api.py` with a Frappe function-test suite. The first tests must assert the field is installed once, password validation delegates to Frappe policy, session deletion does not commit, the login hook creates a reset redirect without email, the request guard permits only the reset/logout paths, and a successful core password update clears the flag.

```python
import unittest
from unittest.mock import patch

import frappe


def test_temporary_password_field_is_installed_idempotently():
    from internal_ledger.install import ensure_roles

    ensure_roles()
    ensure_roles()
    assert frappe.db.count("Custom Field", {
        "dt": "User", "fieldname": "internal_ledger_must_change_password",
    }) == 1
    field = frappe.get_meta("User").get_field("internal_ledger_must_change_password")
    assert field.fieldtype == "Check" and field.hidden and field.no_copy


def test_password_validation_rejects_mismatch_and_policy_failure():
    from internal_ledger.member_security import validate_temporary_password

    with unittest.TestCase().assertRaises(frappe.ValidationError):
        validate_temporary_password("Valid-looking-123!", "different")
    with patch("internal_ledger.member_security.test_password_strength", return_value={
        "feedback": {"password_policy_validation_passed": False},
    }):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            validate_temporary_password("weak", "weak", ("Member", "", "", "member@example.invalid", None))


def load_tests(loader, tests, pattern):
    names = [value for name, value in globals().items() if name.startswith("test_")]
    return unittest.TestSuite(unittest.FunctionTestCase(test) for test in names)
```

- [ ] **Step 2: Run the new module and verify the imports/field fail**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_member_api
```

Expected: FAIL because `internal_ledger.member_security` and the custom field do not exist.

- [ ] **Step 3: Install one hidden custom User flag**

In `internal_ledger/install.py`, call Frappe's standard `create_custom_fields` from `ensure_roles()`:

```python
create_custom_fields({"User": [{
    "fieldname": "internal_ledger_must_change_password",
    "label": "Internal Ledger Must Change Password",
    "fieldtype": "Check",
    "hidden": 1,
    "no_copy": 1,
    "read_only": 1,
    "default": "0",
}]}, update=True)
```

Include the custom field's existence and properties in `_permission_state()` so a real metadata change is audited once while an idempotent call is not.

- [ ] **Step 4: Implement password and transactional session helpers**

Create `internal_ledger/member_security.py`. Use `frappe.core.doctype.user.user.test_password_strength`; reject mismatch and failed feedback. Revoke sessions without calling Frappe's `clear_sessions()` because that function commits internally: collect `Sessions.sid`, remove each `session` cache entry first, then execute `frappe.db.delete("Sessions", {"user": user})`. Let cache or database exceptions propagate so the caller transaction can roll back.

```python
def revoke_sessions(user: str) -> None:
    sessions = frappe.get_all("Sessions", filters={"user": user}, pluck="sid")
    for sid in sessions:
        frappe.cache.hdel("session", sid)
    frappe.db.delete("Sessions", {"user": user})
```

- [ ] **Step 5: Implement first-login redirect and request guard**

`redirect_temporary_password(login_manager)` must check the hidden flag for `login_manager.user`, call `User._reset_password(send_email=False)`, and place the returned one-time URL in `frappe.cache.hset("redirect_after_login", user, url)`. It must not return the URL through a member API or log it.

`block_temporary_password_session()` must do nothing for `Guest`, `Administrator`, or a user without the flag. For a flagged user, allow only:

```python
ALLOWED_RESET_COMMANDS = {
    "frappe.core.doctype.user.user.test_password_strength",
    "frappe.core.doctype.user.user.update_password",
    "logout",
    "frappe.auth.logout",
}
ALLOWED_RESET_PATHS = {"/update-password"}
```

All other dynamic requests raise `frappe.AuthenticationError("请先修改临时密码")`. `clear_temporary_password_flag()` must set the flag to `0` only after Frappe's successful `validate_reset_password` hook.

- [ ] **Step 6: Register hooks without replacing existing mappings**

In `internal_ledger/hooks.py` add:

```python
on_login = "internal_ledger.member_security.redirect_temporary_password"
before_request = ["internal_ledger.member_security.block_temporary_password_session"]
doc_events["User"] = {
    "validate_reset_password": "internal_ledger.member_security.clear_temporary_password_flag",
}
```

Do not alter existing ledger DocType, File, or Journal Entry hooks.

- [ ] **Step 7: Run migration and the focused tests**

Run:

```bash
bench --site test.localhost migrate
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_member_api
```

Expected: PASS for the Task 1 tests; migration creates exactly one hidden custom field.

- [ ] **Step 8: Commit Task 1**

```bash
git add internal_ledger/install.py internal_ledger/hooks.py internal_ledger/member_security.py internal_ledger/tests/test_member_api.py
git commit -m "feat: enforce temporary member passwords"
```

---

### Task 2: Add read-only member creation and listing APIs

**Files:**
- Create: `internal_ledger/api/member_api.py`
- Modify: `internal_ledger/permissions.py`
- Modify: `internal_ledger/tests/test_member_api.py`

**Interfaces:**
- Consumes: `validate_temporary_password(...)` from Task 1
- Produces: `list_enterprise_members() -> dict`
- Produces: `create_enterprise_member(first_name: str, email: str, temporary_password: str, confirm_password: str) -> dict`
- Produces: `record_member_audit(action: str, user: str, company: str) -> None`
- Produces internal helper: `_managed_member(user: str, company: str, for_update: bool = False) -> frappe.model.document.Document`

- [ ] **Step 1: Add failing API permission and creation tests**

Add tests that set the existing fixture administrator, viewer, `System Manager`, and `Guest` in turn. Assert only `Administrator` and `账套管理员` can list/create. For a successful create, assert:

```python
created = create_enterprise_member(
    "只读成员", "member.one@example.invalid", "Temp-Only-938!x", "Temp-Only-938!x"
)
assert created == {
    "email": "member.one@example.invalid",
    "full_name": "只读成员",
    "enabled": 1,
    "must_change_password": 1,
}
assert frappe.db.exists("Has Role", {
    "parenttype": "User", "parent": created["email"], "role": "账务查看者",
})
assert frappe.db.exists("User Permission", {
    "user": created["email"], "allow": "Company", "for_value": frappe.flags.classification_company,
})
assert frappe.db.count("Has Role", {"parent": created["email"]}) == 1
```

Also assert the API response and audit row contain none of the temporary-password text.

- [ ] **Step 2: Run the focused module and verify the API import fails**

Run the Task 1 module command. Expected: FAIL because `member_api` and `record_member_audit` do not exist.

- [ ] **Step 3: Implement exact membership lookup**

In `member_api.py`, normalize email with `strip().lower()` and validate it with `frappe.utils.validate_email_address(..., throw=True)`. Obtain the configured company from `Internal Ledger Settings`, then lock its `Company` document on writes. `_managed_member()` must reject special users and require both the viewer role and the exact company `User Permission`; it must not infer membership from email domain or global role alone.

- [ ] **Step 4: Implement member listing**

Expose a GET-only whitelisted endpoint. Query `User Permission` for the configured company, intersect with `Has Role(role="账务查看者", parenttype="User")`, and return only these fields:

```python
{
    "company": company,
    "enabled_count": sum(row.enabled for row in members),
    "disabled_count": sum(not row.enabled for row in members),
    "members": [{
        "email": row.name,
        "full_name": row.full_name,
        "enabled": int(row.enabled),
        "creation": row.creation,
        "last_login": row.last_login,
    } for row in members],
}
```

Sort by `enabled desc, full_name asc, name asc`. Never return roles, reset keys, password metadata, API keys, or session data.

- [ ] **Step 5: Implement transactional member creation**

Expose a POST-only endpoint. After administrator validation and company locking, validate name/email/password before inserts. Create the standard user with `send_welcome_email=0`, `user_type="System User"`, exactly one viewer role, `new_password=temporary_password`, and the hidden flag set. Insert one `User Permission` for the configured Company. Catch the database duplicate-user race and return a stable `frappe.ValidationError("该登录邮箱已存在")` without revealing any other account fields.

- [ ] **Step 6: Add company-scoped member audit**

Extend `internal_ledger/permissions.py` with `record_member_audit()`. Reuse `_AUDIT_INSERT` and insert an `Internal Ledger Audit Event` whose `reference_doctype` is `User`, `reference_name` is the member email, and `company` is the configured company. Do not call `record_audit()` because `User` has no company field.

- [ ] **Step 7: Verify rollback and concurrent duplicate creation**

Add savepoint-based failure injection around user insert, user-permission insert, and audit insert. After each raised exception, assert no User, Has Role, User Permission, or audit row remains. Add two independent MariaDB connections using `ThreadPoolExecutor(max_workers=2)`; both call `create_enterprise_member()` for the same normalized email, and assert exactly one User and one User Permission exist after one success and one stable conflict.

- [ ] **Step 8: Run focused member and existing permission tests**

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_member_api
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_permissions
```

Expected: both modules PASS; existing viewer write denial remains intact.

- [ ] **Step 9: Commit Task 2**

```bash
git add internal_ledger/api/member_api.py internal_ledger/permissions.py internal_ledger/tests/test_member_api.py
git commit -m "feat: create and list enterprise members"
```

---

### Task 3: Add enable, disable, and temporary-password reset lifecycle

**Files:**
- Modify: `internal_ledger/api/member_api.py`
- Modify: `internal_ledger/member_security.py`
- Modify: `internal_ledger/tests/test_member_api.py`

**Interfaces:**
- Consumes: `_managed_member(...)`, `record_member_audit(...)`, `validate_temporary_password(...)`, `revoke_sessions(...)`
- Produces: `set_enterprise_member_enabled(user: str, enabled: int) -> dict`
- Produces: `reset_enterprise_member_password(user: str, temporary_password: str, confirm_password: str) -> dict`

- [ ] **Step 1: Write failing lifecycle and session tests**

Create a managed viewer through the public creation API, insert two test `Sessions` rows for that user, and assert:

```python
disabled = set_enterprise_member_enabled(member, 0)
assert disabled == {"email": member, "enabled": 0}
assert frappe.db.get_value("User", member, "enabled") == 0
assert frappe.db.count("Sessions", {"user": member}) == 0

enabled = set_enterprise_member_enabled(member, 1)
assert enabled == {"email": member, "enabled": 1}
assert frappe.db.exists("Has Role", {"parent": member, "role": "账务查看者"})
```

Reset tests must prove the old password no longer authenticates, the new temporary password does, the hidden flag is `1`, sessions are gone, and neither response nor audit contains the password.

- [ ] **Step 2: Run the module and verify lifecycle endpoints fail to import**

Run the focused member module. Expected: FAIL because both lifecycle endpoints are absent.

- [ ] **Step 3: Implement enable/disable with exact-target validation**

Require POST, administrator permission, configured-company lock, and `_managed_member(..., for_update=True)`. Accept only integer/JSON values equivalent to `0` or `1`; reject other values. Update the standard `User.enabled`, revoke all target sessions on disable, preserve viewer role and company permission, and record `disable_enterprise_member` or `enable_enterprise_member` in the same transaction.

- [ ] **Step 4: Implement password reset without Frappe's committing session helper**

Validate both password fields before writes. Load the managed member with a row lock, then call `frappe.utils.password.update_password(user, temporary_password, logout_all_sessions=False)` directly so no welcome or password-change email is attempted. Set `internal_ledger_must_change_password=1` with a database write in the same transaction, call the Task 1 `revoke_sessions()`, and record `reset_enterprise_member_password`. The lower-level password call is permitted only inside this already-authorized service and must not be exposed as an API. Return only:

```python
{"email": user, "must_change_password": 1}
```

- [ ] **Step 5: Test rollback at session and audit failures**

Patch `frappe.cache.hdel` to raise before session deletion and patch `record_member_audit` to raise after User mutation. Roll back to a savepoint and assert enabled state or old password, sessions, flag, and audit rows all remain as before. No test output may print either password.

- [ ] **Step 6: Test first-login forced reset end to end**

Authenticate with the temporary password through `frappe.auth.LoginManager.login()`. Assert the response redirect points to `/update-password?key=` without logging the key; attempt a normal internal-ledger API and expect `AuthenticationError`; call Frappe's standard `update_password` with the reset key; assert the flag clears and the viewer APIs work afterward.

- [ ] **Step 7: Run lifecycle, permission, and end-to-end tests**

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_member_api
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_permissions
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_end_to_end
```

Expected: all three modules PASS.

- [ ] **Step 8: Commit Task 3**

```bash
git add internal_ledger/api/member_api.py internal_ledger/member_security.py internal_ledger/tests/test_member_api.py
git commit -m "feat: manage enterprise member access"
```

---

### Task 4: Add the enterprise member page to the Vue workbench

**Files:**
- Create: `internal_ledger/public/frontend/src/pages/EnterpriseMembers.vue`
- Modify: `internal_ledger/public/frontend/src/api.js`
- Modify: `internal_ledger/public/frontend/src/App.vue`
- Modify: `internal_ledger/tests/test_app_boot.py`

**Interfaces:**
- Consumes: all Task 2 and Task 3 member API endpoints
- Produces: `memberCall(method: str, args: object = {}) -> Promise<any>`
- Produces: Vue page prop `active: Boolean`; the component reloads when `active` changes from false to true

- [ ] **Step 1: Add a failing static boot test for the page boundary**

Extend `test_app_boot.py` to read the source files and assert `EnterpriseMembers.vue` exists, `App.vue` renders the members navigation only under `canWrite`, and `api.js` targets `internal_ledger.api.member_api` through a separate helper. This is a wiring test, not a replacement for browser acceptance.

```python
def test_member_page_is_admin_only_in_the_workbench_source():
    root = Path(__file__).parents[1] / "public" / "frontend" / "src"
    app = (root / "App.vue").read_text(encoding="utf-8")
    api = (root / "api.js").read_text(encoding="utf-8")
    assert (root / "pages" / "EnterpriseMembers.vue").exists()
    assert "v-if=\"canWrite\"" in app and "企业成员" in app
    assert "internal_ledger.api.member_api" in api
```

- [ ] **Step 2: Run the boot module and verify it fails**

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_app_boot
```

Expected: FAIL because the component and wiring do not exist.

- [ ] **Step 3: Add a dedicated member API caller**

Keep the existing `call()` behavior unchanged and add:

```javascript
export async function memberCall(method, args = {}) {
  const response = await frappe.call({method: `internal_ledger.api.member_api.${method}`, args});
  return response.message;
}
```

- [ ] **Step 4: Implement the focused member component**

`EnterpriseMembers.vue` must load only while active, display company and enabled/disabled counts, and render the five approved member columns. Use native `<dialog>` elements or the existing component-local conditional pattern; do not add a UI library. The create form contains exactly `first_name`, `email`, `temporary_password`, and `confirm_password`. The reset form contains target email, temporary password, and confirmation. Clear password refs in `finally` after every request.

Buttons must use these exact API operations:

```javascript
await memberCall('create_enterprise_member', form.value)
await memberCall('set_enterprise_member_enabled', {user: row.email, enabled: row.enabled ? 0 : 1})
await memberCall('reset_enterprise_member_password', reset.value)
```

Show sanitized `errorMessage()` output in `role="alert"`; disable the active action while saving; require a confirmation prompt before disabling a member.

- [ ] **Step 5: Wire administrator-only navigation**

Import the component in `App.vue`. Add `['members', '企业成员']` only when `canWrite` is true; if a viewer somehow sets `page='members'`, render the dashboard instead of the member component. Keep classification-rule maintenance administrator-only and preserve all current dashboard/import/review behavior.

- [ ] **Step 6: Add responsive styles without global redesign**

Reuse `.ledger-table` for horizontal containment. At `max-width: 800px`, hide only the creation and last-login columns with a component-scoped class, keep name/email/status/actions visible, and ensure buttons remain at least 40px high. Do not change colors or typography outside the member component.

- [ ] **Step 7: Build assets and run boot/member tests**

```bash
bench build --app internal_ledger
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_app_boot
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_member_api
```

Expected: required `internal_ledger` bundle builds; both modules PASS. An unrelated optional ERPNext banking Vite package must not be installed or treated as part of this app build.

- [ ] **Step 8: Commit Task 4**

```bash
git add internal_ledger/public/frontend/src/api.js internal_ledger/public/frontend/src/App.vue internal_ledger/public/frontend/src/pages/EnterpriseMembers.vue internal_ledger/tests/test_app_boot.py
git commit -m "feat: add enterprise member page"
```

---

### Task 5: Prove viewer behavior, light concurrency, and acceptance evidence

**Files:**
- Modify: `internal_ledger/tests/test_end_to_end.py`
- Modify: `internal_ledger/tests/test_member_api.py`
- Modify: `README.md`
- Modify: `docs/acceptance/2026-10-28-mvp-checklist.md`
- Create as evidence if produced: `docs/acceptance/screenshots/admin-desktop-members.png`
- Create as evidence if produced: `docs/acceptance/screenshots/admin-mobile-members.png`
- Create as evidence if produced: `docs/acceptance/screenshots/viewer-desktop-no-members.png`

**Interfaces:**
- Consumes: completed backend and frontend feature
- Produces: fresh full-suite, browser, permission, and small-capacity acceptance evidence

- [ ] **Step 1: Extend the real workflow to use an API-created member**

Replace the manually provisioned viewer in `test_end_to_end.py` only where doing so does not disturb existing permission fixtures. Create the member with `create_enterprise_member()`, clear the temporary-password flag through the tested successful password-reset path, switch to that user, and retain assertions for dashboard, review item, General Ledger, Trial Balance, Balance Sheet, and Profit and Loss Statement. Add explicit assertions that member creation, disable/enable, and reset audit actions contain no password material.

- [ ] **Step 2: Add a five-reader plus one-admin capacity smoke test**

In `test_member_api.py`, provision five managed viewers. Use six independent Frappe/MariaDB connections synchronized with `threading.Barrier(6)`: five workers set a distinct viewer and call `get_dashboard_counts()`, while the administrator calls `preview_import()` with the sanitized bank fixture. Assert all six calls finish within 30 seconds, all viewers receive the same company-scoped count shape, preview does not persist a batch, and no deadlock/error occurs. Always close each independent connection in `finally`.

- [ ] **Step 3: Run focused and complete automated verification**

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_member_api
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_end_to_end
bench --site test.localhost run-tests --app internal_ledger
pwsh -NoProfile -File infra/test-config.ps1
git diff --check
```

Expected: every command exits 0; record the actual test count and duration rather than copying an old value.

- [ ] **Step 4: Perform desktop and mobile Chrome acceptance**

Start the local Bench server without reload, use a dedicated temporary Chrome profile and native DevTools Protocol, and exercise:

- administrator desktop 1440px: create member, list/count refresh, disable/enable, reset dialog;
- administrator mobile 390px: readable member rows, no horizontal page overflow, usable actions;
- viewer desktop/mobile: no “企业成员” navigation, direct member API calls return permission errors;
- temporary member: login redirects to `/update-password`, normal ledger APIs are blocked until successful change, then viewer reads work.

Use only synthetic `example.invalid` identities and random temporary secrets stored outside Git. Save only sanitized screenshots listed in this task. Delete the synthetic user, temporary profile, logs, scripts, and secrets; stop only the dedicated Chrome and Bench processes.

- [ ] **Step 5: Update product and acceptance documentation**

Update README scope to say each customer receives an independent deployment and administrators can manage read-only enterprise members. Update the acceptance checklist with actual command output, screenshot names, the five-reader/one-admin boundary, and the remaining production blockers. Do not claim a public paid launch, SLA, CloudBase production readiness, or `v0.1.0` release.

- [ ] **Step 6: Scan for secrets and inspect the complete diff**

```bash
rg -n -i "temporary_password|reset_password_key|sid=|example\.invalid" README.md docs internal_ledger infra --glob '!internal_ledger/tests/**'
git status --short
git diff --check
git diff --stat
git diff
```

Expected: only intentional field/function names appear; no secret values, reset URLs, sessions, or unexpected files are present.

- [ ] **Step 7: Commit acceptance evidence and documentation**

```bash
git add README.md docs/acceptance internal_ledger/tests/test_end_to_end.py internal_ledger/tests/test_member_api.py
git commit -m "test: verify enterprise member workflow"
```

- [ ] **Step 8: Final post-commit verification**

```bash
git status --short
git show --stat --oneline HEAD
git tag --list v0.1.0
```

Expected: clean worktree; the final commit contains only intended tests/docs/evidence; `v0.1.0` remains absent until real production deployment, restart, encrypted backup, isolated restore, HTTPS, and commercial-readiness gates pass.
