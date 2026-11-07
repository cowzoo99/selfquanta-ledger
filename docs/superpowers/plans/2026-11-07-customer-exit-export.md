# SelfLedger customer exit export implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an enterprise owner generate, download and delete a complete private customer-exit ZIP containing readable business data, attachments, audit records and verifiable hashes from the current Site only.

**Architecture:** Add one service-owned `Customer Exit Export` request DocType. A background worker builds a deterministic archive from a curated business DocType catalog inside the current Site, attaches it as a protected private File, and records immutable audit events. The unified Vue workbench adds an owner-only Chinese data-management page that calls four narrow APIs.

**Tech Stack:** Frappe v16.52, ERPNext v16.52, HRMS v16.52, Python 3.14 standard library `csv`, `hashlib`, `json`, `tempfile` and `zipfile`, Vue 3, Frappe esbuild, Node frontend contract tests.

**Spec:** `docs/superpowers/specs/2026-11-07-customer-exit-export-design.md`

## Global constraints

- Work only in `.worktrees/public-erp-mvp` on `feature/public-erp-mvp`; do not change or deploy the current WireGuard production checkout.
- The browser never supplies a Site, database, filesystem directory or company filter. The active Frappe Site is the customer boundary.
- Only a User with an explicit `企业负责人` Has Role row may request, list, download or delete a package. `Administrator` receives no implicit exception.
- Platform-support delegation remains disabled in this task: there is no Administrator bypass or support backdoor. A future time-limited customer grant requires a separate approved design.
- Export no password hashes, reset keys, API credentials, OTP secrets, sessions, OAuth tokens, site configuration, encryption keys, database credentials, private keys or server paths.
- The worker must expose no partial ZIP. A failure removes temporary files, marks the request `Failed`, records a safe failure code and preserves the audit record.
- Use one module-level RED/GREEN cycle per task. Do not run the full application suite after each step.
- The frontend must use the installed `heydamir/unslop` Skill and preserve the existing SelfLedger product shell, Chinese labels, routes, behavior and established `#155775` accent.
- Do not introduce a new JavaScript package manager, archive dependency, object store or retention scheduler.
- Do not mark the integrated acceptance row `PASS` until both dedicated acceptance Sites produce and open real packages.

## File map

| File | Responsibility |
|---|---|
| `internal_ledger/internal_ledger/doctype/customer_exit_export/customer_exit_export.json` | Immutable request schema and owner-only read permission |
| `internal_ledger/internal_ledger/doctype/customer_exit_export/customer_exit_export.py` | DocType controller marker |
| `internal_ledger/customer_exit/catalog.py` | Curated data sets and field-denial policy |
| `internal_ledger/customer_exit/archive.py` | CSV, attachment, manifest and ZIP construction |
| `internal_ledger/customer_exit/service.py` | Request state machine, background job, download and deletion orchestration |
| `internal_ledger/api/export_api.py` | Four whitelisted owner operations |
| `internal_ledger/permissions.py` | Explicit owner check and protected export File rules |
| `internal_ledger/hooks.py` | Request and File permission hooks |
| `internal_ledger/public/frontend/src/pages/CustomerExitExport.vue` | Owner-facing data-management page |
| `internal_ledger/public/frontend/src/api.js` | Export API adapter |
| `internal_ledger/public/frontend/src/App.vue` | Owner-only route and page mount |
| `internal_ledger/tests/test_customer_exit_export.py` | Backend state, package, permission and failure tests |
| `internal_ledger/tests/frontend_review.cjs` | Vue behavior and owner-only navigation tests |
| `internal_ledger/tests/test_multisite_isolation.py` | Package-level A/B isolation evidence |
| `docs/operations/pilot-onboarding.md` | Customer-facing export and handoff instructions |
| `docs/acceptance/2026-11-06-public-erp-integrated-checklist.md` | Actual acceptance evidence only |

---

### Task 1: Add the service-owned request and owner permission boundary

**Files:**
- Create: `internal_ledger/internal_ledger/doctype/customer_exit_export/__init__.py`
- Create: `internal_ledger/internal_ledger/doctype/customer_exit_export/customer_exit_export.py`
- Create: `internal_ledger/internal_ledger/doctype/customer_exit_export/customer_exit_export.json`
- Create: `internal_ledger/customer_exit/__init__.py`
- Create: `internal_ledger/customer_exit/service.py`
- Create: `internal_ledger/tests/test_customer_exit_export.py`
- Modify: `internal_ledger/permissions.py`
- Modify: `internal_ledger/hooks.py`

**Interfaces:**
- Produces `require_enterprise_owner(user: str | None = None) -> str`.
- Produces `has_customer_exit_permission(doc, ptype, user=None, **kwargs) -> bool` and `customer_exit_query(user=None, doctype=None) -> str`.
- Produces `Customer Exit Export` statuses `Generating`, `Ready`, `Failed`, `Deleted`.
- The service-only write token is private to `permissions.py`; browser code cannot set request state.

- [ ] **Step 1: Write the failing owner and request-state tests**

Add this setup and the first two tests to `test_customer_exit_export.py`:

```python
import unittest

import frappe

from internal_ledger.tests.test_permissions import user
from internal_ledger.tests.test_review_api import set_up, tear_down


def owner():
    return user("企业负责人")


def clean_up():
    frappe.set_user("Administrator")
    if frappe.db.table_exists("Customer Exit Export"):
        frappe.db.delete("Customer Exit Export")
    tear_down()


def test_only_explicit_enterprise_owner_passes_export_gate():
    from internal_ledger.permissions import require_enterprise_owner

    frappe.set_user(owner())
    assert require_enterprise_owner() == frappe.session.user
    for actor in (user("账套管理员"), user("账务查看者"), "Administrator", "Guest"):
        frappe.set_user(actor)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            require_enterprise_owner()


def test_customer_exit_request_is_owner_read_only_and_service_written():
    from internal_ledger.customer_exit.service import _insert_request

    frappe.set_user(owner())
    request = _insert_request(frappe.session.user)
    assert request.status == "Generating"
    assert request.requested_by == frappe.session.user
    assert frappe.has_permission(request.doctype, "read", request)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        request.status = "Ready"
        request.save()
```

Use the module's existing `load_tests` pattern:

```python
def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=clean_up)
        for name, test in sorted(globals().items())
        if name.startswith("test_")
    )
```

- [ ] **Step 2: Run the focused module and verify RED**

Run:

```powershell
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_customer_exit_export --failfast
```

Expected: FAIL because `Customer Exit Export`, `require_enterprise_owner` and `internal_ledger.customer_exit.service` do not exist.

- [ ] **Step 3: Add the minimal DocType schema**

Create a hash-named, service-owned DocType with this exact field contract:

```json
{
  "doctype": "DocType",
  "name": "Customer Exit Export",
  "module": "Internal Ledger",
  "autoname": "hash",
  "engine": "InnoDB",
  "field_order": [
    "status", "requested_by", "requested_at", "completed_at", "downloaded_at",
    "file", "row_count", "attachment_count", "package_size", "package_sha256",
    "manifest_json", "failure_code"
  ],
  "fields": [
    {"fieldname": "status", "label": "Status", "fieldtype": "Select", "options": "Generating\nReady\nFailed\nDeleted", "reqd": 1, "read_only": 1},
    {"fieldname": "requested_by", "label": "Requested By", "fieldtype": "Link", "options": "User", "reqd": 1, "read_only": 1},
    {"fieldname": "requested_at", "label": "Requested At", "fieldtype": "Datetime", "reqd": 1, "read_only": 1},
    {"fieldname": "completed_at", "label": "Completed At", "fieldtype": "Datetime", "read_only": 1},
    {"fieldname": "downloaded_at", "label": "Downloaded At", "fieldtype": "Datetime", "read_only": 1},
    {"fieldname": "file", "label": "Private Package", "fieldtype": "Attach", "read_only": 1},
    {"fieldname": "row_count", "label": "Row Count", "fieldtype": "Int", "read_only": 1},
    {"fieldname": "attachment_count", "label": "Attachment Count", "fieldtype": "Int", "read_only": 1},
    {"fieldname": "package_size", "label": "Package Size", "fieldtype": "Int", "read_only": 1},
    {"fieldname": "package_sha256", "label": "Package SHA-256", "fieldtype": "Data", "read_only": 1},
    {"fieldname": "manifest_json", "label": "Manifest JSON", "fieldtype": "Long Text", "read_only": 1},
    {"fieldname": "failure_code", "label": "Failure Code", "fieldtype": "Data", "read_only": 1}
  ],
  "permissions": [
    {"role": "企业负责人", "read": 1, "report": 1}
  ]
}
```

The Python controller contains only:

```python
from frappe.model.document import Document


class CustomerExitExport(Document):
    pass
```

- [ ] **Step 4: Add explicit role and write guards**

In `permissions.py`, add a private `_CUSTOMER_EXIT_WRITE` token and these contracts:

```python
def _has_explicit_role(user: str, role: str) -> bool:
    return bool(frappe.db.exists("Has Role", {"parent": user, "parenttype": "User", "role": role}))


def require_enterprise_owner(user: str | None = None) -> str:
    user = user or frappe.session.user
    if user == "Guest" or not _has_explicit_role(user, "企业负责人"):
        frappe.throw("需要企业负责人权限", frappe.PermissionError)
    return user


def has_customer_exit_permission(doc, ptype, user=None, **kwargs):
    user = user or frappe.session.user
    return ptype in ("read", "report", "select") and _has_explicit_role(user, "企业负责人")


def customer_exit_query(user=None, doctype=None):
    return "" if _has_explicit_role(user or frappe.session.user, "企业负责人") else "1=0"


def protect_customer_exit_write(doc, method=None):
    require_enterprise_owner()
    if doc.flags.customer_exit_write is not _CUSTOMER_EXIT_WRITE:
        frappe.throw("企业数据导出记录只能由导出服务写入", frappe.PermissionError)
```

Add the private production insertion boundary. Task 3 reuses it from the public request service; browser code never calls it directly:

```python
def _insert_request(actor: str):
    from internal_ledger.permissions import _CUSTOMER_EXIT_WRITE, require_enterprise_owner
    from frappe.utils import now_datetime

    actor = require_enterprise_owner(actor)
    doc = frappe.get_doc({
        "doctype": "Customer Exit Export",
        "status": "Generating",
        "requested_by": actor,
        "requested_at": now_datetime(),
    })
    doc.flags.customer_exit_write = _CUSTOMER_EXIT_WRITE
    return doc.insert(ignore_permissions=True)
```

Wire `Customer Exit Export` in `hooks.py` with `has_permission`, `permission_query_conditions`, and `before_validate` plus `on_trash` events. Do not put it in `_ledger_doctypes`, because its Site boundary is not a `company` field.

- [ ] **Step 5: Migrate and verify GREEN**

Run:

```powershell
bench --site test.localhost migrate
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_customer_exit_export
```

Expected: both tests PASS. The `Administrator` negative case proves there is no implicit platform bypass.

- [ ] **Step 6: Commit the request boundary**

```powershell
git add internal_ledger/internal_ledger/doctype/customer_exit_export internal_ledger/customer_exit internal_ledger/permissions.py internal_ledger/hooks.py internal_ledger/tests/test_customer_exit_export.py
git commit -m "feat: add owner-only customer exit requests"
```

---

### Task 2: Build a complete safe archive from the current Site

**Files:**
- Create: `internal_ledger/customer_exit/catalog.py`
- Create: `internal_ledger/customer_exit/archive.py`
- Modify: `internal_ledger/tests/test_customer_exit_export.py`

**Interfaces:**
- Produces `build_customer_exit_archive(request_name: str, target: Path) -> dict`.
- Manifest schema is `selfledger.customer-exit.v1`.
- Archive paths are generated from catalog keys and File document IDs, never from user paths.

- [ ] **Step 1: Add a failing archive-content test**

Create a unique customer, attachment and audit event, invoke the wished-for builder, and use literal assertions:

```python
def test_archive_contains_business_rows_attachment_audit_and_valid_hashes(tmp_path=None):
    import hashlib
    import io
    import json
    from pathlib import Path
    from zipfile import ZipFile

    from internal_ledger.customer_exit.archive import build_customer_exit_archive
    from internal_ledger.customer_exit.service import _insert_request

    marker = "EXIT-OWN-FIXTURE"
    customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": marker,
        "customer_type": "Company",
    }).insert(ignore_permissions=True)
    attached = frappe.get_doc({
        "doctype": "File",
        "file_name": "退出凭据.txt",
        "content": marker.encode("utf-8"),
        "is_private": 1,
        "attached_to_doctype": "Customer",
        "attached_to_name": customer.name,
    }).insert(ignore_permissions=True)
    request = _insert_request(frappe.session.user)
    target = Path(frappe.get_site_path("private", f"{request.name}.zip"))
    manifest = build_customer_exit_archive(request.name, target)
    content = target.read_bytes()
    assert hashlib.sha256(content).hexdigest()
    with ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        assert names[:2] == ["manifest.json", "README.txt"]
        assert "master-data/customer.csv" in names
        assert "audit/internal-ledger-audit-event.csv" in names
        assert "attachments/index.csv" in names
        assert marker in archive.read("master-data/customer.csv").decode("utf-8-sig")
        index = archive.read("attachments/index.csv").decode("utf-8-sig")
        assert attached.name in index and "退出凭据.txt" in index
        file_name = next(name for name in names if name.startswith("attachments/files/"))
        assert archive.read(file_name) == marker.encode("utf-8")
        stored = json.loads(archive.read("manifest.json"))
        assert stored["schema"] == "selfledger.customer-exit.v1"
        assert stored["row_count"] == manifest["row_count"]
        assert all(len(item["sha256"]) == 64 for item in stored["members"])
    target.unlink(missing_ok=True)
```

Extend `clean_up()` to delete the fixture Customer, its File row and physical blob.

- [ ] **Step 2: Run the test and verify RED**

Run the focused module. Expected: FAIL because `internal_ledger.customer_exit.archive` does not exist.

- [ ] **Step 3: Define the curated catalog and secret-field policy**

In `catalog.py`, define three explicit groups:

```python
DATASETS = {
    "master-data": (
        "Company", "Account", "Cost Center", "Customer", "Supplier", "Contact", "Address",
        "Item", "Item Group", "Warehouse", "Project", "Employee", "Department", "Designation",
    ),
    "transactions": (
        "Lead", "Opportunity", "Quotation", "Sales Order", "Delivery Note", "Sales Invoice",
        "Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry", "Stock Entry",
        "Expense Claim", "Employee Advance", "Task", "Timesheet", "Attendance", "Leave Application",
        "Payroll Entry", "Salary Slip", "Journal Entry", "GL Entry", "Import Batch",
        "Imported Transaction", "Classification Rule", "Voucher Draft", "Posting Link",
        "Month End Run", "Tax Material Package",
    ),
    "audit": ("Version", "Activity Log", "Internal Ledger Audit Event", "AI Interaction Audit"),
}

DENIED_FIELD_PATTERN = re.compile(
    r"(^|_)(password|passwd|secret|token|otp|private_key|encryption_key|reset_key|api_key)(_|$)",
    re.IGNORECASE,
)
DENIED_FIELDTYPES = {"Password"}
```

Add `safe_fields(meta)`, which returns `name`, standard timestamps and every scalar field that is not a Table field, a Password field or a denied name. Table fields are exported through their child DocType using `parent`, `parenttype`, `parentfield` and `idx`.

Add one special safe identity data set with exactly `name`, `email`, `first_name`, `last_name`, `full_name`, `enabled`, `user_type`, `creation`, and `modified`, plus a separate roles CSV with `parent`, `role`, and `idx`. Do not route `User` through generic field discovery.

- [ ] **Step 4: Implement deterministic CSV, attachment and manifest writing**

In `archive.py`, implement these exact public and private boundaries:

```python
def build_customer_exit_archive(request_name: str, target: Path) -> dict:
    request = frappe.get_doc("Customer Exit Export", request_name)
    if request.status != "Generating":
        frappe.throw("只有生成中的企业数据导出可以构建")
    target.parent.mkdir(parents=True, exist_ok=True)
    members = _collect_csv_members(request)
    members.update(_collect_attachment_members(members))
    manifest = _manifest(request, members)
    _write_zip(target, manifest, members)
    _verify_zip(target, manifest)
    return manifest
```

Implementation rules:

- `_csv_bytes(headers, rows)` writes UTF-8 BOM and CRLF, matching `tax_materials.py`.
- Order every query by `creation, name`; order child rows by `parent, parentfield, idx, name`.
- Convert dates and datetimes to ISO text, Decimal values to plain strings, booleans to `0` or `1`, and remaining scalar values with `str`.
- Include only installed DocTypes. Record missing optional DocTypes in `manifest["unavailable"]`.
- Build `exported_documents` as `(doctype, name)` pairs while writing top-level CSV files. Include only File rows attached to one of those pairs.
- Store attachment bytes under `attachments/files/<first-16-sha256-of-File-name><safe-extension>`. The original filename appears only in `attachments/index.csv`.
- Reject a missing physical attachment with failure code `ATTACHMENT_READ_FAILED`; do not silently omit it.
- Write ZIP members with timestamp `(1980, 1, 1, 0, 0, 0)`, mode `0600`, and `ZIP_DEFLATED` for reproducible member metadata.
- Put `manifest.json` and `README.txt` first. The manifest records every other member's byte length and SHA-256. The package SHA-256 is stored on the request after ZIP closure, not recursively inside the manifest.
- `_verify_zip` reopens the archive, rejects duplicate names, checks the manifest schema and recomputes every member hash.

- [ ] **Step 5: Add failing secret and path-safety tests before each implementation branch**

Add these tests before implementing the corresponding filters:

```python
def test_archive_excludes_authentication_and_server_material():
    from internal_ledger.customer_exit.catalog import safe_fields

    user_meta = frappe.get_meta("User")
    fields = set(safe_fields(user_meta))
    assert "password" not in fields
    assert "api_key" not in fields
    assert "api_secret" not in fields
    assert "reset_password_key" not in fields
    assert "name" in fields


def test_attachment_name_cannot_escape_archive():
    from internal_ledger.customer_exit.archive import attachment_archive_path

    path = attachment_archive_path("FILE-0001", "../../server.key")
    assert path.startswith("attachments/files/")
    assert ".." not in path
    assert "server.key" not in path
```

Run each test after adding it and verify it fails for the missing filter/path function before implementing the minimum code.

- [ ] **Step 6: Run the complete archive module GREEN**

Run:

```powershell
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_customer_exit_export
```

Expected: archive, secret filter and path safety tests PASS, and no generated ZIP remains after cleanup.

- [ ] **Step 7: Commit the archive builder**

```powershell
git add internal_ledger/customer_exit/catalog.py internal_ledger/customer_exit/archive.py internal_ledger/tests/test_customer_exit_export.py
git commit -m "feat: build verifiable customer exit archives"
```

---

### Task 3: Add the asynchronous service, protected download and controlled deletion

**Files:**
- Modify: `internal_ledger/customer_exit/service.py`
- Create: `internal_ledger/api/export_api.py`
- Modify: `internal_ledger/permissions.py`
- Modify: `internal_ledger/hooks.py`
- Modify: `internal_ledger/tests/test_customer_exit_export.py`
- Modify: `internal_ledger/tests/test_permissions.py`

**Interfaces:**
- `request_customer_exit_export() -> dict`
- `list_customer_exit_exports() -> list[dict]`
- `download_customer_exit_export(name: str) -> None`
- `delete_customer_exit_export(name: str) -> dict`
- `run_customer_exit_export(name: str) -> None` is the background worker.

- [ ] **Step 1: Add failing service-state and permission tests**

Add tests with literal expected states:

```python
def test_owner_request_completes_private_package_and_records_audit():
    from internal_ledger.api.export_api import list_customer_exit_exports, request_customer_exit_export

    frappe.set_user(owner())
    result = request_customer_exit_export()
    assert result["status"] == "Ready"
    assert result["requested_by"] == frappe.session.user
    assert result["file"].startswith("/private/files/")
    assert len(result["package_sha256"]) == 64
    assert list_customer_exit_exports()[0]["name"] == result["name"]
    actions = frappe.get_all("Internal Ledger Audit Event", filters={
        "reference_doctype": "Customer Exit Export", "reference_name": result["name"]}, pluck="action")
    assert "request_customer_exit_export" in actions
    assert "complete_customer_exit_export" in actions


def test_nonowners_cannot_request_list_download_or_delete():
    from internal_ledger.api import export_api

    frappe.set_user(owner())
    result = export_api.request_customer_exit_export()
    for actor in (user("账套管理员"), user("账务查看者"), "Administrator", "Guest"):
        frappe.set_user(actor)
        for operation in (
            export_api.request_customer_exit_export,
            export_api.list_customer_exit_exports,
            lambda: export_api.download_customer_exit_export(result["name"]),
            lambda: export_api.delete_customer_exit_export(result["name"]),
        ):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()
```

In tests, `request_customer_exit_export` runs the real Frappe job synchronously by passing `now=True` to `frappe.enqueue` because `frappe.flags.in_test` is true. Do not mock the archive builder.

- [ ] **Step 2: Run the focused module and verify RED**

Expected: FAIL because the API and state service do not exist.

- [ ] **Step 3: Implement the request and worker state machine**

Use this enqueue boundary:

```python
def request_customer_exit_export():
    actor = require_enterprise_owner()
    request = _insert_request(actor)
    record_customer_exit_audit("request_customer_exit_export", request, "success")
    frappe.enqueue(
        "internal_ledger.customer_exit.service.run_customer_exit_export",
        queue="long",
        enqueue_after_commit=not frappe.flags.in_test,
        now=bool(frappe.flags.in_test),
        name=request.name,
        job_id=f"customer-exit-{request.name}",
    )
    return serialize_request(frappe.get_doc(request.doctype, request.name))
```

`run_customer_exit_export` must:

1. Load the request and set the session user to `requested_by`.
2. Create a unique directory with `tempfile.mkdtemp` under `frappe.get_site_path("private", "customer-exit-tmp")`.
3. Call `build_customer_exit_archive`.
4. Insert one private File attached to `Customer Exit Export`.
5. Compute package bytes, size and SHA-256 after ZIP closure.
6. Set `Ready`, counts, manifest JSON, completed time and File URL with the service write token.
7. Record completion audit.
8. In `except`, remove any inserted File and temporary path, set `Failed` with one of `DATA_EXPORT_FAILED`, `ATTACHMENT_READ_FAILED`, `ARCHIVE_VERIFY_FAILED`, and record failure audit.
9. In `finally`, recursively remove only the exact generated temporary directory after resolving it under the fixed Site-private root.

Return no exception content to the user. Log request name, failure code and traceback through Frappe's error log without serializing exported rows.

- [ ] **Step 4: Protect File reads, downloads and controlled deletion**

Add `Customer Exit Export` to `_PROTECTED_FILES`. In `_can_read_protected`, branch before the ledger role rule:

```python
if row.attached_to_doctype == "Customer Exit Export":
    return _has_explicit_role(user, "企业负责人")
```

In `protect_source_file`, require a private File and the service write token on creation. Reject modifications. Allow `on_trash` only through a private `_CUSTOMER_EXIT_FILE_DELETE` token set by `delete_customer_exit_export`.

`download_customer_exit_export` reloads the request, calls `require_enterprise_owner`, requires `Ready`, checks the attached File, records `download_customer_exit_export`, updates `downloaded_at` through the service token, then delegates to `frappe.handler.download_file(request.file)`.

`delete_customer_exit_export` calls `require_enterprise_owner`, deletes only the attached File through the controlled token, clears the URL, changes status to `Deleted`, and records `delete_customer_exit_export`. A second delete returns the same serialized `Deleted` request without raising.

- [ ] **Step 5: Add a real missing-attachment failure test**

Create a private File document whose URL points to a nonexistent blob, attach it to an exported Customer, then request the export:

```python
def test_missing_attachment_fails_without_downloadable_partial_package():
    from internal_ledger.api.export_api import request_customer_exit_export

    customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": "EXIT-MISSING-FILE",
        "customer_type": "Company",
    }).insert(ignore_permissions=True)
    frappe.get_doc({
        "doctype": "File",
        "file_name": "missing.txt",
        "file_url": "/private/files/exit-file-does-not-exist.txt",
        "is_private": 1,
        "attached_to_doctype": "Customer",
        "attached_to_name": customer.name,
    }).insert(ignore_permissions=True)
    frappe.set_user(owner())
    result = request_customer_exit_export()
    assert result["status"] == "Failed"
    assert result["failure_code"] == "ATTACHMENT_READ_FAILED"
    assert not result["file"]
    assert not frappe.get_all("File", filters={
        "attached_to_doctype": "Customer Exit Export", "attached_to_name": result["name"]})
```

This test exercises a real filesystem failure and must not mock `Path.read_bytes`.

- [ ] **Step 6: Verify focused backend and file-permission GREEN**

Run once after the module is complete:

```powershell
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_customer_exit_export
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_permissions
```

Expected: both modules PASS. Confirm failure responses contain no traceback, absolute path or secret field name.

- [ ] **Step 7: Commit the service and API**

```powershell
git add internal_ledger/customer_exit/service.py internal_ledger/api/export_api.py internal_ledger/permissions.py internal_ledger/hooks.py internal_ledger/tests/test_customer_exit_export.py internal_ledger/tests/test_permissions.py
git commit -m "feat: add audited customer exit service"
```

---

### Task 4: Add the owner-only unified frontend using Unslop

**Files:**
- Create: `internal_ledger/public/frontend/src/pages/CustomerExitExport.vue`
- Modify: `internal_ledger/public/frontend/src/api.js`
- Modify: `internal_ledger/public/frontend/src/App.vue`
- Modify: `internal_ledger/tests/frontend_review.cjs`
- Modify: `internal_ledger/tests/test_unified_navigation.py`

**Interfaces:**
- Route: `/app/ledger-workbench/data-management`.
- Navigation label: `数据管理`.
- `exportCall(method: str, args: object = {}) -> Promise<object>`.

- [ ] **Step 1: Load and apply the installed Unslop Skill**

Read the installed `unslop/SKILL.md` and `references/signs.md` before editing. Use this approved direction:

```text
Product: SelfLedger lets a small-company owner take a complete, readable copy of company data.
Character: calm, exact, trustworthy.
World: account books and sealed document packets.
Moments: the package integrity summary and the status trail.
Signature: a short SHA-256 seal shown in tabular figures on completed packages.
```

Audit only the new page and the navigation row it adds. Do not redesign unrelated SelfLedger pages.

- [ ] **Step 2: Add failing Vue behavior tests**

Update the frontend test loader so `api.js` exposes `exportCall`, and add `CustomerExitExport` to the real-child component pattern. Add a `customer_exit_export` case that asserts:

```javascript
frappe.session.user = 'owner@example.invalid';
frappe.user_roles = ['企业负责人'];
frappe.call = async args => {
  calls.push(args);
  if (args.method.endsWith('list_customer_exit_exports')) return {message: []};
  if (args.method.endsWith('request_customer_exit_export')) return {message: {
    name: 'EXIT-1', status: 'Ready', requested_by: frappe.session.user,
    requested_at: '2026-11-07 10:00:00', completed_at: '2026-11-07 10:00:01',
    row_count: 42, attachment_count: 2, package_size: 2048,
    package_sha256: 'a'.repeat(64), file: '/private/files/customer-exit.zip', failure_code: '',
  }};
  return {message: {partial: false, sections: {}}};
};
const page = mount('App.vue', {view: 'data-management'}, true);
await settle();
assert.match(text(page.root), /导出企业全部数据/);
assert.match(text(page.root), /不包含登录密码和系统密钥/);
const request = descendants(page.root).find(el => el.type === 'button' && text(el) === '导出企业全部数据');
request.props.onClick();
await settle();
assert.match(text(page.root), /可下载/);
assert.match(text(page.root), /42 条数据/);
assert.match(text(page.root), /2 个附件/);
assert.match(text(page.root), /aaaaaaaaaaaa/);
assert.ok(descendants(page.root).find(el => el.type === 'a' && text(el) === '下载'));
page.app.unmount();
```

Add another case with `frappe.user_roles = ['账套管理员']` and assert that App contains no `数据管理` link and falls back to the dashboard for `view: 'data-management'`.

- [ ] **Step 3: Run the frontend case and verify RED**

Run:

```powershell
node internal_ledger/tests/frontend_review.cjs customer_exit_export
```

Expected: FAIL because the route, component and API adapter do not exist.

- [ ] **Step 4: Implement the narrow API adapter and route**

Add to `api.js`:

```javascript
export async function exportCall(method, args = {}) {
  const reads = ['list_customer_exit_exports'];
  const response = await frappe.call({
    method: `internal_ledger.api.export_api.${method}`,
    args,
    type: reads.includes(method) ? 'GET' : 'POST',
  });
  return response.message;
}
```

In `App.vue`, import `CustomerExitExport`, define `canExportData` as `userRoles.has('企业负责人')`, append `['data-management', '数据管理']` only for that role, and mount the page only when both the route and role match. Do not use `isAdministrator` as an override.

- [ ] **Step 5: Implement the page states and actions**

`CustomerExitExport.vue` must:

- load `list_customer_exit_exports` on mount;
- poll every 3 seconds only while at least one request is `Generating`, and clear the timer on unmount;
- disable the primary action while requesting;
- render loading, empty, Generating, Ready, Failed and Deleted states in Chinese;
- build download href as `/api/method/internal_ledger.api.export_api.download_customer_exit_export?name=${encodeURIComponent(row.name)}`;
- confirm archive deletion with a plain Chinese browser confirmation;
- call `delete_customer_exit_export` and refresh the list;
- map safe failure codes to Chinese text and never render a raw traceback;
- format bytes and timestamps without inventing package data.

Use a quiet neutral table/list, 4 px spacing increments, one `#155775` primary button, visible focus, 44 px minimum mobile touch targets, `tabular-nums` for counts and SHA, no gradient, emoji, glow, decorative image or looping animation. At widths below 768 px, turn each row into a labeled stacked record without hiding fields.

- [ ] **Step 6: Verify frontend GREEN and run the Unslop checklist**

Run:

```powershell
node internal_ledger/tests/frontend_review.cjs customer_exit_export
node internal_ledger/tests/frontend_review.cjs
bench build --app internal_ledger
```

Then inspect the page at 375 px, 768 px and 1440 px plus 200% zoom on `acceptance-a.localhost`. Verify keyboard focus, accessible labels, no unintended horizontal scroll and no action exposed to a non-owner. Record screenshots outside Git if they contain any customer data; use only synthetic data for committed evidence.

- [ ] **Step 7: Commit the unified page**

```powershell
git add internal_ledger/public/frontend/src/pages/CustomerExitExport.vue internal_ledger/public/frontend/src/api.js internal_ledger/public/frontend/src/App.vue internal_ledger/tests/frontend_review.cjs internal_ledger/tests/test_unified_navigation.py
git commit -m "feat: add customer data export page"
```

---

### Task 5: Prove two-Site isolation and close the acceptance row

**Files:**
- Modify: `internal_ledger/tests/test_multisite_isolation.py`
- Modify: `docs/operations/pilot-onboarding.md`
- Modify: `docs/acceptance/2026-11-06-public-erp-integrated-checklist.md`

**Interfaces:**
- `customer_exit_evidence() -> dict` returns only package hashes, counts and own/peer marker booleans.
- Acceptance evidence contains no Site name, database name, key, password, file content or private path.

- [ ] **Step 1: Add the package isolation test before changing evidence code**

Add to `test_multisite_isolation.py`:

```python
def test_customer_exit_package_contains_own_fixture_and_not_peer_fixture():
    evidence = customer_exit_evidence()
    assert evidence["package_sha256"]
    assert evidence["own_marker_present"]
    assert not evidence["peer_marker_present"]
    assert evidence["attachment_count"] >= 1
    assert evidence["audit_count"] >= 1
```

Add this test to `load_tests`. Run on one acceptance Site and verify RED because `customer_exit_evidence` does not exist.

- [ ] **Step 2: Implement non-secret package evidence**

`customer_exit_evidence` must set the current synthetic enterprise-owner user, call the real request API synchronously, open the resulting private ZIP, concatenate only uncompressed CSV, text and attachment members into `searchable`, and compare byte markers from `_marker(_site())` and `_marker(_peer(_site()))`:

```python
searchable = b"\n".join(
    archive.read(member)
    for member in archive.namelist()
    if member.endswith((".csv", ".txt")) or member.startswith("attachments/files/")
)
own_marker = _marker(_site()).encode("utf-8")
peer_marker = _marker(_peer(_site())).encode("utf-8")

{
    "package_sha256": request.package_sha256,
    "row_count": int(request.row_count),
    "attachment_count": int(request.attachment_count),
    "audit_count": audit_count,
    "own_marker_present": own_marker in searchable,
    "peer_marker_present": peer_marker in searchable,
}
```

Create or reuse an explicit `企业负责人` synthetic user inside each acceptance Site. Do not run as `Administrator`.

- [ ] **Step 3: Run the focused tests on both dedicated Sites**

Run with the already approved Site names:

```powershell
$env:SELFLEDGER_TEST_SITE_A='acceptance-a.localhost'
$env:SELFLEDGER_TEST_SITE_B='acceptance-b.localhost'
bench --site acceptance-a.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_multisite_isolation
bench --site acceptance-b.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_multisite_isolation
```

Expected on both Sites: own marker present, peer marker absent, package hash length 64, at least one attachment and at least one audit record.

- [ ] **Step 4: Open and inspect both real packages**

For each Site, inspect `manifest.json`, one master CSV, one transaction CSV, `attachments/index.csv`, one attachment and one audit CSV. Compare the literal manifest row and attachment counts to the Site queries. Do not copy package contents or Site secrets into Git.

- [ ] **Step 5: Document the customer handoff**

Add a concise section to `pilot-onboarding.md` that tells an enterprise owner:

1. Open `企业设置 → 数据管理`.
2. Select `导出企业全部数据`.
3. Wait for `可下载`.
4. Download the ZIP and keep it encrypted at rest.
5. Compare the displayed short SHA with the manifest/package acceptance record.
6. Delete the server copy only after confirming the local archive opens.

State that the package contains business and personal data, does not contain login passwords or system keys, and is not a database restore backup.

- [ ] **Step 6: Update acceptance evidence only from actual results**

Change the integrated checklist row `客户完整退出导出` from `PENDING` to `PASS` only after Steps 3 and 4 pass. Record the two package hashes only as first 12 characters, actual row/attachment/audit counts, test commands and pass counts. If either Site fails, retain `PENDING` and record the safe failure code.

- [ ] **Step 7: Run the module-level final gate once**

Run:

```powershell
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_customer_exit_export
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_permissions
node internal_ledger/tests/frontend_review.cjs
pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
git diff --check
```

Do not rerun unrelated full application suites unless a focused failure shows shared behavior was changed.

- [ ] **Step 8: Commit acceptance and operations evidence**

```powershell
git add internal_ledger/tests/test_multisite_isolation.py docs/operations/pilot-onboarding.md docs/acceptance/2026-11-06-public-erp-integrated-checklist.md
git commit -m "test: prove customer exit package isolation"
```

## Definition of done

- An explicit enterprise owner can generate, download and delete a complete private package from the unified Chinese workbench.
- No other role, Guest or implicit Administrator access can request, list, download or delete a package.
- The archive contains readable master data, transactions, child rows, attachments, identities/roles and audit records with verified counts and hashes.
- Authentication material, infrastructure configuration and server paths are absent.
- A real attachment or archive failure produces no downloadable partial package.
- Dedicated Sites A and B each prove own-marker presence and peer-marker absence in an opened package.
- The frontend passes focused behavior, keyboard, responsive and Unslop checks without changing unrelated pages.
- The integrated checklist records actual evidence and changes the customer-exit row to `PASS`; all unrelated G4/G5 blockers retain their prior status.
