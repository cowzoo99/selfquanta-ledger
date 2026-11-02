# Unified ERP and Intelligent Ledger Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one ERPNext product shell that turns imported statements into deduplicated, classified, balanced, reviewed and posted accounting entries.

**Architecture:** Keep Frappe Desk and ERPNext as the single application shell and accounting engine. Extend the existing internal-ledger domain services with explicit duplicate-review and manual-classification state transitions, then expose batch draft/post operations through narrow APIs and Vue pages. Every write remains permission-checked, audited and idempotent.

**Tech Stack:** Frappe/ERPNext, Python 3, MariaDB, Vue 3, Docker Compose, PowerShell infrastructure checks.

**Spec:** `docs/superpowers/specs/2026-11-02-unified-ledger-erp-workflow-design.md`

## Global Constraints

- One domain, one login, one Frappe Desk shell and one permission system.
- Retain all existing ERPNext sales, purchase, stock and manufacturing modules.
- No paid AI service or new external dependency.
- Pending or confirmed duplicates never affect totals, classification, drafts or posting.
- Only `账套管理员` may import, resolve duplicates, classify, approve or post; `账务查看者` is read-only.
- Every batch operation reports success, failure and skipped rows explicitly.
- Journal Entries are created only through `internal_ledger.accounting.posting.post_draft`.
- Use TDD for every behavior change and preserve existing production data.
- Public access requires a real hostname, Tencent Cloud ICP approval, matching public TLS certificate and explicit opt-in; absent prerequisites must leave WireGuard-only bindings unchanged.

---

### Task 1: Duplicate Review Lifecycle

**Files:**
- Modify: `internal_ledger/internal_ledger/doctype/imported_transaction/imported_transaction.json`
- Modify: `internal_ledger/internal_ledger/doctype/imported_transaction/imported_transaction.py`
- Modify: `internal_ledger/importing/models.py`
- Create: `internal_ledger/importing/duplicate_review.py`
- Create: `internal_ledger/api/duplicate_api.py`
- Create: `internal_ledger/tests/test_duplicate_review.py`
- Modify: `internal_ledger/permissions.py`

**Interfaces:**
- Produces: `list_duplicates() -> list[dict]`
- Produces: `resolve_duplicate(name: str, decision: Literal["confirm", "release"], note: str) -> dict`
- Produces: `release_duplicate(name: str, actor: str, note: str) -> ImportedTransaction`
- Consumes: existing `record_audit`, `_IMPORT_WRITE`, `build_dedupe_key` and ledger role checks.

- [ ] **Step 1: Write failing duplicate lifecycle tests**

Add cases proving a duplicate starts in `Pending`, is excluded from classification, can be confirmed without changing accounting totals, and can be released exactly once:

```python
def test_duplicate_is_pending_and_excluded():
    first = import_row(source_id="WX-1", amount="78.00")
    duplicate = import_row(source_id="WX-1", amount="78.00")
    assert duplicate.status == "Duplicate"
    assert duplicate.duplicate_review_status == "Pending"
    assert duplicate.duplicate_of == first.name
    assert duplicate.candidate_dedupe_key == "wechat:WX-1"
    with pytest.raises(frappe.ValidationError, match="重复流水"):
        classify(duplicate.name)


def test_release_duplicate_preserves_audit_link_and_enters_normal_flow():
    original, duplicate = duplicate_pair()
    released = release_duplicate(duplicate.name, "Administrator", "两笔真实交易")
    assert released.status == "Imported"
    assert released.duplicate_review_status == "Released"
    assert released.duplicate_of == original.name
    assert released.dedupe_key == f"released:{duplicate.name}"
    assert released.duplicate_reviewed_by == "Administrator"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_duplicate_review
```

Expected: FAIL because duplicate review fields and `release_duplicate` do not exist.

- [ ] **Step 3: Add duplicate-review fields and validation**

Add fields:

```json
{"fieldname":"duplicate_review_status","label":"Duplicate Review Status","fieldtype":"Select","options":"Pending\nConfirmed\nReleased","read_only":1},
{"fieldname":"candidate_dedupe_key","label":"Candidate Dedupe Key","fieldtype":"Data","read_only":1,"permlevel":1},
{"fieldname":"duplicate_match_reason","label":"Duplicate Match Reason","fieldtype":"Data","read_only":1},
{"fieldname":"duplicate_reviewed_by","label":"Duplicate Reviewed By","fieldtype":"Link","options":"User","read_only":1},
{"fieldname":"duplicate_reviewed_at","label":"Duplicate Reviewed At","fieldtype":"Datetime","read_only":1},
{"fieldname":"duplicate_review_note","label":"Duplicate Review Note","fieldtype":"Small Text","read_only":1}
```

Update validation so `Duplicate` requires `Pending` or `Confirmed`, no active `dedupe_key`, and an original link; `Imported + Released` requires `released:<name>`, preserves the original link, and may enter classification.

- [ ] **Step 4: Implement atomic confirm/release transitions**

Create `duplicate_review.py` with row locking and explicit transitions:

```python
def resolve(name, decision, actor, note):
    tx = frappe.get_doc("Imported Transaction", name, for_update=True)
    if tx.status != "Duplicate" or tx.duplicate_review_status != "Pending":
        frappe.throw("该重复候选已经处理")
    tx.duplicate_reviewed_by = actor
    tx.duplicate_reviewed_at = now_datetime()
    tx.duplicate_review_note = (note or "").strip()
    if decision == "confirm":
        tx.duplicate_review_status = "Confirmed"
    elif decision == "release":
        tx.status = "Imported"
        tx.duplicate_review_status = "Released"
        tx.dedupe_key = f"released:{tx.name}"
    else:
        frappe.throw("重复处理决定无效")
    tx.flags.ledger_import_write = _IMPORT_WRITE
    tx.save()
    record_audit(f"duplicate_{decision}", "Imported Transaction", tx.name, "success")
    return tx
```

Set `candidate_dedupe_key` and a literal match reason during `save_batch` when the unique-key collision is handled.

- [ ] **Step 5: Add permission-checked API and verify GREEN**

`list_duplicates` returns both candidate and original transaction fields. `resolve_duplicate` calls `require_ledger_admin`, checks read/write permission, invokes the service, and returns the updated row.

Run the Task 1 module plus existing dedupe tests; expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add internal_ledger/importing internal_ledger/api/duplicate_api.py internal_ledger/internal_ledger/doctype/imported_transaction internal_ledger/tests/test_duplicate_review.py internal_ledger/permissions.py
git commit -m "feat: add audited duplicate review lifecycle"
```

---

### Task 2: Manual Classification and Exact Rule Learning

**Files:**
- Create: `internal_ledger/accounting/manual_classification.py`
- Create: `internal_ledger/api/classification_api.py`
- Modify: `internal_ledger/accounting/classifier.py`
- Modify: `internal_ledger/api/review_api.py`
- Create: `internal_ledger/tests/test_manual_classification.py`

**Interfaces:**
- Produces: `classify_manually(tx_name, transaction_type, debit_account, credit_account, summary, remember_counterparty) -> VoucherDraft`
- Produces: `list_leaf_accounts() -> list[dict]`
- Consumes: Task 1 released duplicates, existing `validate_transaction_accounts`, `build_lines`, `create_draft` and `Classification Rule`.

- [ ] **Step 1: Write failing manual-classification tests**

```python
def test_manual_classification_creates_balanced_draft_and_exact_rule():
    tx = unmatched_expense(counterparty="重庆八戒软件有限公司", amount="36000")
    draft = classify_manually(
        tx.name, "expense", account("560201"), tx.payment_account,
        "软件开发服务费", True,
    )
    assert [(row.debit, row.credit) for row in draft.lines] == [(36000, 0), (0, 36000)]
    rule = frappe.get_doc("Classification Rule", draft.classification_rule)
    assert (rule.kind, rule.value, rule.direction) == ("counterparty", tx.counterparty, "expense")


def test_manual_classification_rejects_pending_duplicate():
    tx = pending_duplicate()
    with pytest.raises(frappe.ValidationError, match="重复流水"):
        classify_manually(tx.name, "expense", account("560201"), tx.payment_account, "", True)
```

- [ ] **Step 2: Run tests and verify RED**

Expected: FAIL because `manual_classification.py` does not exist.

- [ ] **Step 3: Implement manual classification service**

Lock the transaction and batch, require `status == "Imported"`, validate accounts with the existing service, create or reuse an exact counterparty rule, then call `create_draft(tx_name, classify(tx_name))`. Reject blank counterparties when rule learning is requested. Never create a free-text keyword rule.

- [ ] **Step 4: Add APIs for accounts and manual classification**

Return leaf accounts with `name`, `account_number`, `account_name` and `root_type`, filtered to the configured company, enabled and non-group. The write endpoint accepts only the six explicit fields in the interface and returns the new `Voucher Draft`.

- [ ] **Step 5: Verify classifier, permissions and manual tests**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_manual_classification
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_classifier
```

Expected: all PASS, including conflict detection and released-duplicate classification.

- [ ] **Step 6: Commit**

```bash
git add internal_ledger/accounting/manual_classification.py internal_ledger/api/classification_api.py internal_ledger/accounting/classifier.py internal_ledger/api/review_api.py internal_ledger/tests/test_manual_classification.py
git commit -m "feat: classify unmatched transactions and learn exact rules"
```

---

### Task 3: Batch Draft Generation and Professional Voucher Presentation

**Files:**
- Create: `internal_ledger/accounting/batch_workflow.py`
- Modify: `internal_ledger/api/review_api.py`
- Modify: `internal_ledger/public/frontend/src/pages/ReviewCenter.vue`
- Modify: `internal_ledger/public/frontend/src/components/DraftLines.vue`
- Create: `internal_ledger/public/frontend/src/components/ManualClassificationForm.vue`
- Create: `internal_ledger/tests/test_batch_workflow.py`

**Interfaces:**
- Produces: `prepare_matched_transactions(tx_names: list[str]) -> list[Outcome]`
- `Outcome = {"name": str, "status": "created"|"skipped"|"failed", "draft": str|None, "message": str}`
- Consumes: Tasks 1–2 states and existing `create_draft`.

- [ ] **Step 1: Write failing batch tests**

Cover a matched transaction, unmatched transaction, pending duplicate and already-drafted transaction in one call. Assert only the matched row creates one draft and a second identical call skips it without creating another.

- [ ] **Step 2: Verify RED**

Run the new module; expected FAIL because `prepare_matched_transactions` is missing.

- [ ] **Step 3: Implement per-row idempotent draft preparation**

For each requested name: check permission, skip non-Imported and existing drafts, call `classify`, create only `matched` results, and return a literal outcome for every input. Wrap each row in its own savepoint and roll back only that row on failure.

- [ ] **Step 4: Add professional voucher totals**

Update `DraftLines.vue` to show columns `科目编码`, `科目名称`, `借方金额`, `贷方金额`, plus computed footer totals:

```js
const debitTotal = computed(() => props.lines.reduce((sum, row) => sum + Number(row.debit || 0), 0));
const creditTotal = computed(() => props.lines.reduce((sum, row) => sum + Number(row.credit || 0), 0));
const balanced = computed(() => Math.abs(debitTotal.value - creditTotal.value) < 0.000000001);
```

Display posting date, draft number, source transaction, summary, classification reason, operator and status above the lines.

- [ ] **Step 5: Add batch and manual controls to Review Center**

Add filters for `疑似重复`, `待分类`, `待审核`, `待过账`, `已过账`; add “批量生成凭证” for selected Imported rows; render `ManualClassificationForm` for unmatched rows; keep all duplicate rows read-only with a link to duplicate review.

- [ ] **Step 6: Run backend tests and frontend build**

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_batch_workflow
bench build --app internal_ledger
```

Expected: tests PASS and `ledger_workbench.bundle.js` builds without Vue errors.

- [ ] **Step 7: Commit**

```bash
git add internal_ledger/accounting/batch_workflow.py internal_ledger/api/review_api.py internal_ledger/public/frontend/src internal_ledger/tests/test_batch_workflow.py
git commit -m "feat: batch balanced voucher draft generation"
```

---

### Task 4: Batch Review and Formal Posting

**Files:**
- Modify: `internal_ledger/accounting/batch_workflow.py`
- Modify: `internal_ledger/api/posting_api.py`
- Modify: `internal_ledger/public/frontend/src/pages/ReviewCenter.vue`
- Create: `internal_ledger/tests/test_batch_posting.py`

**Interfaces:**
- Produces: `approve_and_post(draft_names: list[str]) -> list[Outcome]`
- Consumes: existing `approve_draft`, `posting.post_draft`, Task 3 Outcome schema.

- [ ] **Step 1: Write failing partial-success and idempotency tests**

Create two valid drafts and one invalid/closed-period draft. Assert the valid drafts become `Posted` with submitted Journal Entries, the invalid row remains repairable, every input has an outcome, and retrying posted rows returns `skipped` without a second Journal Entry.

- [ ] **Step 2: Verify RED**

Expected: FAIL because batch approval/posting does not exist.

- [ ] **Step 3: Implement per-draft atomic workflow**

For each name, use a savepoint; reload and validate the draft; approve only a `Draft`; post only an `Approved` draft; return an existing Journal Entry for `Posted`; roll back that row and return `failed` on validation errors. Record approve, post and failure audit events.

- [ ] **Step 4: Add confirmation summary and result table**

Before calling the API, show selected count, income total, expense total, debit total, credit total and anomaly count. Require a second click on “确认审核并过账”. Render one result row per draft with Journal Entry link or failure reason.

- [ ] **Step 5: Verify posting and permissions**

Run batch-posting, posting, permissions and end-to-end test modules. Expected: all PASS; a `账务查看者` call returns a permission error; all submitted Journal Entries have equal debit/credit totals.

- [ ] **Step 6: Commit**

```bash
git add internal_ledger/accounting/batch_workflow.py internal_ledger/api/posting_api.py internal_ledger/public/frontend/src/pages/ReviewCenter.vue internal_ledger/tests/test_batch_posting.py
git commit -m "feat: batch approve and post journal entries"
```

---

### Task 5: Unified Product Home, Navigation and Duplicate Review UI

**Files:**
- Modify: `internal_ledger/public/frontend/src/App.vue`
- Modify: `internal_ledger/public/frontend/src/pages/Dashboard.vue`
- Create: `internal_ledger/public/frontend/src/pages/DuplicateReview.vue`
- Modify: `internal_ledger/internal_ledger/page/ledger_workbench/ledger_workbench.js`
- Create: `internal_ledger/internal_ledger/workspace/selfledger/selfledger.json`
- Modify: `internal_ledger/install.py`
- Create: `internal_ledger/tests/test_unified_navigation.py`

**Interfaces:**
- Consumes: Task 1 duplicate APIs, Task 3 review filters and existing ERPNext routes.
- Produces: one Frappe Workspace named `SelfLedger 企业经营与智能内账` and route-aware workbench views.

- [ ] **Step 1: Write failing navigation and dashboard contract tests**

Assert the installed Workspace contains shortcuts for `业务总览`, `文件智能导入`, `重复流水复核`, `智能分类`, `凭证中心`, `销售`, `采购`, `库存`, and `制造`; assert viewer/admin role visibility remains unchanged.

- [ ] **Step 2: Verify RED**

Expected: FAIL because the unified Workspace does not exist.

- [ ] **Step 3: Create unified Workspace and route-aware page**

Create a public Workspace owned by Administrator with Internal Ledger shortcuts first and ERPNext workspace links second. Read `frappe.get_route()` in `ledger_workbench.js` and pass the requested view into `mount_workbench(root, {view})` so Workspace shortcuts open the correct content without a second product shell.

- [ ] **Step 4: Remove the second sidebar and build the overview**

Change `App.vue` from a two-column nested navigation to a single content column with a compact page header and view tabs. Dashboard cards show counts and direct actions. Keep ERPNext links as normal Desk routes; do not duplicate ERPNext pages.

- [ ] **Step 5: Implement duplicate review page**

List candidate/original rows side by side, display source ID/fingerprint reason, and provide only two admin actions: `确认重复，忽略` and `不是重复，保留并入账`. Require a note for release; viewers see the same evidence without buttons.

- [ ] **Step 6: Build, migrate fixture and verify UI contracts**

```bash
bench --site test.localhost migrate
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_unified_navigation
bench build --app internal_ledger
```

Expected: workspace test PASS and bundle build succeeds.

- [ ] **Step 7: Commit**

```bash
git add internal_ledger/public/frontend/src internal_ledger/internal_ledger/page/ledger_workbench internal_ledger/internal_ledger/workspace internal_ledger/install.py internal_ledger/tests/test_unified_navigation.py
git commit -m "feat: unify ERP and intelligent ledger navigation"
```

---

### Task 6: End-to-End Acceptance, Migration and Production Deployment

**Files:**
- Modify: `internal_ledger/tests/test_end_to_end.py`
- Modify: `infra/test-config.ps1`
- Modify: `docs/operations/deploy.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: deployment evidence for import → duplicate gate → classification → balanced draft → approval → Journal Entry posting.

- [ ] **Step 1: Add end-to-end acceptance tests**

Use two identical transactions and one unmatched transaction. Assert the duplicate is excluded, release it manually, classify both normal transactions, generate balanced drafts, approve/post, and verify exactly two submitted Journal Entries and two Posting Links exist. Add a viewer-role variant that can read but cannot mutate.

- [ ] **Step 2: Run focused and full suites**

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_end_to_end
bench --site test.localhost run-tests --app internal_ledger
pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
```

Expected: zero failures.

- [ ] **Step 3: Build production images and verify manifests**

Build the incremental app image and OCR image. Run the asset-manifest gate requiring 57 or more entries, zero missing files, and valid `desk.bundle.js` plus `ledger_workbench.bundle.js`. Run document-parser unit tests inside the new app image.

- [ ] **Step 4: Back up and migrate production**

Run the existing backup service and require exit status 0. Update the server checkout to the exact release commit, run `bench --site ledger.selfquanta.com migrate`, then update `INTERNAL_LEDGER_IMAGE` only after migration succeeds.

- [ ] **Step 5: Verify production without changing user accounting data**

Verify container health, both frontend bundles return HTTP 200, OCR remains private, the unified Workspace exists, and API reads show the configured company and accounts. Use generated test fixtures on a test site or transaction rollback; do not post synthetic entries to the production company.

- [ ] **Step 6: Perform user acceptance with one real file**

The administrator uploads one approved sample through the UI, confirms duplicate behavior, manually classifies one unmatched row, reviews the professional debit/credit table, and explicitly approves posting. Record the resulting real Journal Entry number as acceptance evidence without exposing source IDs or bank details in logs.

- [ ] **Step 7: Commit and merge release**

```bash
git add internal_ledger/tests/test_end_to_end.py infra/test-config.ps1 docs
git commit -m "test: verify unified ledger accounting workflow"
git push origin HEAD
```

Merge only after automated checks and production smoke verification pass.

---

### Task 7: Public HTTPS Access Without User WireGuard

**Files:**
- Modify: `infra/compose.yaml`
- Modify: `infra/nginx.conf`
- Modify: `infra/runtime.env.example`
- Create: `infra/host/configure-public-access.sh`
- Modify: `infra/host/configure-private-access.sh`
- Modify: `infra/test-config.ps1`
- Modify: `docs/operations/deploy.md`
- Create: `internal_ledger/tests/test_public_access_security.py`

**Interfaces:**
- Produces: `configure-public-access.sh <hostname>` with a fail-closed prerequisite check.
- Consumes: existing `HTTP_BIND`, `HTTPS_BIND`, TLS secret files, UFW and systemd deployment.

- [ ] **Step 1: Write failing configuration and security tests**

Add tests proving public mode refuses an IP address, missing certificate, hostname/certificate mismatch or absent explicit ICP confirmation; private mode keeps `10.77.0.1` bindings. Assert Compose publishes only gateway ports and never publishes database, Redis, OCR, backend, workers or WebSocket ports.

- [ ] **Step 2: Run tests and verify RED**

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_public_access_security
pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
```

Expected: FAIL because public-access configuration and prerequisite checks do not exist.

- [ ] **Step 3: Add explicit public runtime mode**

Add required values with safe defaults:

```dotenv
PUBLIC_ACCESS_ENABLED=0
PUBLIC_HOSTNAME=
ICP_APPROVED=0
```

When public mode is disabled, require `HTTP_BIND=HTTPS_BIND=10.77.0.1`. When enabled, require `PUBLIC_HOSTNAME` to be a DNS hostname, `ICP_APPROVED=1`, certificate/key files to exist, and bind gateway HTTPS to `0.0.0.0:443`. Do not add host port mappings to any other service.

- [ ] **Step 4: Implement fail-closed host configuration**

`configure-public-access.sh` validates root execution, hostname syntax, certificate SAN coverage with `openssl x509 -checkhost`, DNS resolution to the server public IPv4, and the literal `ICP_APPROVED=1` runtime flag. It then backs up runtime/UFW state, allows public 443, optionally allows 80 only for a documented redirect/certificate requirement, preserves WireGuard and SSH management rules, runs `infra/test-config.ps1`, and restarts only after checks pass. On failure it restores the previous runtime and UFW state.

- [ ] **Step 5: Harden public login exposure**

Keep registration disabled; require secure cookies and HTTPS proxy headers; configure Frappe rate limiting and login-attempt lockout; require System Manager/账套管理员 users to enable two-factor authentication before public cutover. Document one named account per person and prohibit shared Administrator use.

- [ ] **Step 6: Verify public and rollback modes**

On a staging hostname, verify HTTPS certificate/hostname, login, WebSocket, file upload and logout without WireGuard. From an external network confirm only 443 is reachable. Run the rollback path and confirm the service again binds only to `10.77.0.1`, remains healthy over WireGuard, and preserves all database volumes.

- [ ] **Step 7: Production cutover gate**

Do not execute production public cutover until the owner supplies the exact registered hostname, Tencent Cloud ICP approval is visible, DNS resolves to `42.193.189.93`, and a matching public certificate is installed. Once supplied, run backup, public configuration, external login acceptance with a non-admin account, firewall scan and rollback drill.

- [ ] **Step 8: Commit**

```bash
git add infra internal_ledger/tests/test_public_access_security.py docs/operations/deploy.md
git commit -m "feat: add gated public HTTPS access"
```
