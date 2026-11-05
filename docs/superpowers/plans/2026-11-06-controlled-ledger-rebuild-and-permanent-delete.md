# Controlled Ledger Rebuild and Permanent Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make posted income/expense rows and debit/credit entries fully editable and permanently deletable through one synchronized service that keeps imported transactions, drafts, formal vouchers and the general ledger consistent.

**Architecture:** Add one focused posted-entry maintenance service beside the existing posting service. Reads return a versioned aggregate rooted at `Imported Transaction`; writes lock the aggregate and either rebuild the formal posting atomically or permanently remove the aggregate and its private files. Both Accounting Center views use one Vue editor and the same API, while native Journal Entry and GL Entry writes remain blocked.

**Tech Stack:** Frappe 16.52.0, ERPNext 16.52.1, Python 3.14, MariaDB row locks/savepoints, Vue 3 SFC, Node-based Vue renderer tests, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-06-controlled-ledger-rebuild-and-permanent-delete-design.md`

## Global Constraints

- Single company and single ledger; every read and write is scoped from `Internal Ledger Settings.company`.
- Only `Administrator` and `账套管理员` may rebuild or permanently delete; `账务查看者` remains read-only.
- Do not directly edit submitted `Journal Entry` or `GL Entry`; use cancel/delete/repost under existing service markers.
- Rebuild and deletion are atomic: a failed request leaves the entire old aggregate intact.
- Debit total, credit total and imported transaction amount must be equal and positive.
- Shared source files remain while any transaction in their import batch exists; the empty batch and source file are deleted with the last transaction.
- Do not change unrelated import recognition, duplicate-review, member-management or ERP navigation behavior.
- Follow test-first development and run focused tests by module; run the concentrated regression gate only after all implementation tasks are complete.

---

## File Structure

- Create `internal_ledger/accounting/posted_entry_maintenance.py`: aggregate loading, versioning, rebuild, permanent deletion and source-file cleanup.
- Modify `internal_ledger/accounting/posting_maintenance.py`: extract the existing cancel/delete mechanics into a private reusable helper without changing public behavior.
- Modify `internal_ledger/internal_ledger/doctype/voucher_draft/voucher_draft.py`: allow only the new service marker to replace a posted draft snapshot during rebuild.
- Modify `internal_ledger/internal_ledger/doctype/internal_ledger_source_file/internal_ledger_source_file.py`: allow only the new source-delete marker to remove an empty batch's source carrier.
- Modify `internal_ledger/permissions.py`: define source-delete and posted-rebuild markers, authorize controlled protected-file deletion, and add a deletion-audit helper that works after the referenced row is gone.
- Modify `internal_ledger/api/posting_api.py`: expose aggregate read, rebuild and permanent-delete POST endpoints.
- Create `internal_ledger/public/frontend/src/components/PostedEntryEditor.vue`: one editor for transaction fields, classification, lines, evidence and delete confirmation.
- Modify `internal_ledger/public/frontend/src/pages/AccountingCenter.vue`: use the editor from both views and replace evidence-only editing and soft posting deletion.
- Create `internal_ledger/tests/test_posted_entry_maintenance.py`: backend service, permissions, rollback, dedupe and file-lifecycle tests.
- Modify `internal_ledger/tests/frontend_review.cjs`: editor and both-view interaction tests.
- Modify `internal_ledger/tests/test_unified_navigation.py`: static UI/API contract assertions.

---

### Task 1: Versioned Posted-Entry Read Contract

**Files:**
- Create: `internal_ledger/accounting/posted_entry_maintenance.py`
- Modify: `internal_ledger/api/posting_api.py`
- Create: `internal_ledger/tests/test_posted_entry_maintenance.py`

**Interfaces:**
- Produces: `get_posted_entry(transaction: str) -> dict`
- Produces: `posted_entry_version(tx, draft, link) -> str`
- Response keys: `transaction`, `draft`, `posting`, `evidence`, `version`

- [ ] **Step 1: Write failing aggregate-read and permission tests**

```python
def test_get_posted_entry_returns_one_versioned_editable_aggregate():
    aggregate = posted_fixture()
    result = posting_api.get_posted_entry(aggregate.transaction)
    assert result["transaction"]["amount"] == aggregate.amount
    assert result["draft"]["name"] == aggregate.draft
    assert result["posting"]["journal_entry"] == aggregate.journal
    assert len(result["draft"]["lines"]) == 2
    assert len(result["version"]) == 64


def test_viewer_can_read_posted_entry_aggregate():
    aggregate = posted_fixture()
    frappe.set_user(user("账务查看者"))
    assert posting_api.get_posted_entry(aggregate.transaction)["draft"]["status"] == "Posted"
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_posted_entry_maintenance --failfast
```

Expected: FAIL because `get_posted_entry` does not exist.

- [ ] **Step 3: Implement the aggregate loader and stable version**

Create the service with these public signatures and stable hash inputs:

```python
def posted_entry_version(tx, draft, link) -> str:
    values = {
        "transaction": tx.modified,
        "draft": draft.modified,
        "posting": link.modified,
        "journal": link.journal_entry,
        "lines": [(row.name, row.account, str(row.debit or 0), str(row.credit or 0))
                  for row in draft.lines],
    }
    return sha256(frappe.as_json(values, sort_keys=True).encode()).hexdigest()


def get_posted_entry(transaction: str, *, for_update: bool = False) -> dict:
    tx = frappe.get_doc("Imported Transaction", transaction, for_update=for_update)
    tx.check_permission("read")
    draft_name = frappe.db.get_value(
        "Voucher Draft", {"imported_transaction": tx.name}, "name", for_update=for_update
    )
    if not draft_name:
        frappe.throw("该流水尚未生成凭证")
    draft = frappe.get_doc("Voucher Draft", draft_name, for_update=for_update)
    link_name = frappe.db.get_value(
        "Posting Link", {"voucher_draft": draft.name}, "name", for_update=for_update
    )
    if draft.status != "Posted" or not link_name:
        frappe.throw("只能维护已过账账目")
    link = frappe.get_doc("Posting Link", link_name, for_update=for_update)
    if link.status != "Posted" or link.reversal_journal_entry:
        frappe.throw("已冲销凭证不能编辑或删除")
    return serialize_posted_entry(tx, draft, link, posted_entry_version(tx, draft, link))
```

The serializer must return only company-scoped business fields, account names/numbers needed by the editor, evidence title/note metadata, and the version; it must not return dedupe keys to viewers.

- [ ] **Step 4: Expose only the completed read endpoint**

```python
@frappe.whitelist(methods=["GET", "POST"])
def get_posted_entry(transaction: str) -> dict:
    return posted_entry_maintenance.get_posted_entry(transaction)
```

Do not add rebuild or permanent-delete endpoints in this task; each write endpoint is added only after its service has a failing test and a complete implementation.

- [ ] **Step 5: Run the focused module and verify the read tests pass**

Run the same module command. Expected: aggregate-read and viewer-read tests PASS with no skipped tests.

- [ ] **Step 6: Commit the read contract**

```bash
git add internal_ledger/accounting/posted_entry_maintenance.py \
  internal_ledger/api/posting_api.py internal_ledger/tests/test_posted_entry_maintenance.py
git commit -m "feat: add versioned posted entry aggregate"
```

---

### Task 2: Atomic Controlled Rebuild

**Files:**
- Modify: `internal_ledger/accounting/posted_entry_maintenance.py`
- Modify: `internal_ledger/accounting/posting_maintenance.py`
- Modify: `internal_ledger/api/posting_api.py`
- Modify: `internal_ledger/internal_ledger/doctype/voucher_draft/voucher_draft.py`
- Modify: `internal_ledger/permissions.py`
- Modify: `internal_ledger/tests/test_posted_entry_maintenance.py`

**Interfaces:**
- Consumes: `get_posted_entry(transaction, for_update=True)` and `posted_entry_version(...)`
- Produces: `rebuild_posted_entry(transaction: str, values: dict, expected_version: str) -> dict`
- Produces: `_remove_formal_posting(link, draft, *, resulting_status: str | None) -> str`
- Input `values`: `occurred_at`, `posting_date`, `counterparty`, `summary`, `direction`, `transaction_type`, `amount`, `payment_account`, `lines`

- [ ] **Step 1: Write failing rebuild and write-permission tests**

```python
def test_rebuild_updates_transaction_draft_new_journal_and_general_ledger():
    old = posted_fixture()
    before = posting_api.get_posted_entry(old.transaction)
    result = posting_api.rebuild_posted_entry(old.transaction, {
        "occurred_at": "2026-08-16 09:30:00",
        "posting_date": "2026-08-16",
        "counterparty": "新交易对象",
        "summary": "调整后的办公采购",
        "direction": "expense",
        "transaction_type": "费用支出",
        "amount": "125.00",
        "payment_account": account("1001"),
        "lines": [
            {"account": account("560201"), "debit": "125.00", "credit": "0"},
            {"account": account("1001"), "debit": "0", "credit": "125.00"},
        ],
    }, before["version"])
    assert result["posting"]["journal_entry"] != old.journal
    assert not frappe.db.exists("Journal Entry", old.journal)
    assert frappe.db.get_value("Imported Transaction", old.transaction, "amount") == Decimal("125.00")
    assert frappe.db.get_value("Voucher Draft", old.draft, "summary") == "调整后的办公采购"
    assert frappe.db.count("GL Entry", {"voucher_no": result["posting"]["journal_entry"]}) == 2


def test_rebuild_rolls_back_every_layer_when_reposting_fails():
    old = posted_fixture()
    before = posting_api.get_posted_entry(old.transaction)
    with patch("internal_ledger.accounting.posted_entry_maintenance.post_draft",
               side_effect=frappe.ValidationError("模拟重新过账失败")):
        with unittest.TestCase().assertRaises(frappe.ValidationError):
            posting_api.rebuild_posted_entry(old.transaction, changed_values(old), before["version"])
    assert frappe.db.exists("Journal Entry", old.journal)
    assert frappe.db.get_value("Imported Transaction", old.transaction, "amount") == old.amount
    assert posting_api.get_posted_entry(old.transaction)["version"] == before["version"]


def test_viewer_cannot_rebuild_posted_entry():
    old = posted_fixture()
    before = posting_api.get_posted_entry(old.transaction)
    frappe.set_user(user("账务查看者"))
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        posting_api.rebuild_posted_entry(old.transaction, changed_values(old), before["version"])
```

Add separate tests for stale version, unbalanced lines, amount mismatch, frozen account, closed period and dedupe collision. Each must assert the old journal and old transaction remain unchanged.

- [ ] **Step 2: Run the focused module and verify RED**

Expected: FAIL because the rebuild endpoint and service do not exist; the fixture transaction remains unchanged.

- [ ] **Step 3: Extract reusable formal-posting removal**

Move the cancel/GL delete/Posting Link delete/Journal Entry delete sequence from `delete_posting` into:

```python
def _remove_formal_posting(link, draft, *, resulting_status=None) -> str:
    journal = frappe.get_doc("Journal Entry", link.journal_entry, for_update=True)
    if journal.docstatus != 1 or journal.reversal_of:
        frappe.throw("正式凭证状态不允许重建")
    journal.flags.ledger_journal_write = _JOURNAL_WRITE
    journal.cancel()
    frappe.db.delete("GL Entry", {"voucher_type": "Journal Entry", "voucher_no": journal.name})
    frappe.delete_doc("Posting Link", link.name, ignore_permissions=True)
    previous = frappe.flags.get("ledger_journal_delete")
    frappe.flags.ledger_journal_delete = _JOURNAL_DELETE
    try:
        frappe.delete_doc("Journal Entry", journal.name, ignore_permissions=True)
    finally:
        frappe.flags.ledger_journal_delete = previous
    if resulting_status:
        frappe.db.set_value("Voucher Draft", draft.name, "status", resulting_status,
                            update_modified=False)
    return journal.name
```

Keep existing `delete_posting` behavior by calling this helper with `resulting_status="Rejected"` and retaining its rejection reason and audit event.

- [ ] **Step 4: Add a service-only posted-draft rebuild marker**

Define `_POSTED_ENTRY_REBUILD = object()` in `permissions.py`. In `VoucherDraft.validate`, permit replacement of posted fields only when `self.flags.posted_entry_rebuild is _POSTED_ENTRY_REBUILD`; still run child ownership, balance, transaction-account, currency and period validation. No normal document save receives this marker.

- [ ] **Step 5: Implement exact input parsing and validation**

```python
def _validated_values(values, tx, company):
    required = {"occurred_at", "posting_date", "counterparty", "summary", "direction",
                "transaction_type", "amount", "payment_account", "lines"}
    if not isinstance(values, dict) or set(values) != required:
        frappe.throw("账目编辑字段不完整")
    amount = Decimal(str(values["amount"]))
    if amount <= 0:
        frappe.throw("金额必须大于零")
    if values["direction"] not in {"income", "expense"}:
        frappe.throw("收支方向无效")
    lines = parse_lines(values["lines"])
    validate_balanced(lines)
    if sum((line.debit for line in lines), Decimal("0")) != amount:
        frappe.throw("借贷金额必须等于流水金额")
    validate_transaction_accounts_for_all(lines, values["payment_account"],
                                          values["transaction_type"], values["direction"], company)
    validate_period(company, getdate(values["posting_date"]))
    return NormalizedPostedEntry(
        occurred_at=get_datetime(values["occurred_at"]),
        posting_date=getdate(values["posting_date"]),
        counterparty=values["counterparty"].strip(),
        summary=values["summary"].strip(),
        direction=values["direction"],
        transaction_type=values["transaction_type"].strip(),
        amount=amount,
        payment_account=values["payment_account"],
        lines=tuple(lines),
    )
```

Define frozen `NormalizedPostedEntry` and `NormalizedPostedLine` dataclasses with explicitly typed fields before `_validated_values`; do not pass the browser dictionary deeper into the service.

- [ ] **Step 6: Implement the atomic rebuild with one savepoint**

```python
def rebuild_posted_entry(transaction, values, expected_version):
    require_ledger_admin()
    point = "posted_rebuild_" + uuid4().hex
    frappe.db.savepoint(point)
    try:
        tx, draft, link = _locked_documents(transaction)
        if posted_entry_version(tx, draft, link) != expected_version:
            frappe.throw("账目已被其他人修改，请刷新后重试")
        parsed = _validated_values(values, tx, draft.company)
        _assert_unique_dedupe_key(tx, parsed)
        old_journal = _remove_formal_posting(link, draft, resulting_status="Draft")
        _update_imported_transaction(tx, parsed)
        _replace_draft_snapshot(draft, parsed)
        approve_draft(draft.name)
        new_journal = post_draft(draft.name)
        _sync_existing_evidence(tx.name)
        record_audit("rebuild_posted_entry", "Voucher Draft", draft.name,
                     f"old:{old_journal};new:{new_journal}")
        result = get_posted_entry(tx.name)
    except frappe.QueryDeadlockError:
        raise
    except Exception:
        frappe.db.rollback(save_point=point)
        frappe.db.release_savepoint(point)
        raise
    frappe.db.release_savepoint(point)
    return result
```

`_update_imported_transaction` must set `_IMPORT_WRITE`, recompute the dedupe key from `NormalizedTransaction`, exclude the current name when checking uniqueness, clear obsolete duplicate-review fields, and call `save()`. `_replace_draft_snapshot` must set the service marker, manual override fields, provided business type and lines, then save and validate before approval.

- [ ] **Step 7: Expose the completed rebuild endpoint**

```python
@frappe.whitelist(methods=["POST"])
def rebuild_posted_entry(transaction: str, values, expected_version: str) -> dict:
    require_ledger_admin()
    parsed = frappe.parse_json(values) if isinstance(values, str) else values
    return posted_entry_maintenance.rebuild_posted_entry(
        transaction, parsed, expected_version
    )
```

- [ ] **Step 8: Run the focused backend module and existing posting module**

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_posted_entry_maintenance --failfast
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_posting --failfast
```

Expected: all rebuild, rollback and existing posting tests PASS.

- [ ] **Step 9: Commit controlled rebuild**

```bash
git add internal_ledger/accounting/posted_entry_maintenance.py \
  internal_ledger/accounting/posting_maintenance.py internal_ledger/permissions.py \
  internal_ledger/internal_ledger/doctype/voucher_draft/voucher_draft.py \
  internal_ledger/tests/test_posted_entry_maintenance.py
git commit -m "feat: atomically rebuild posted ledger entries"
```

---

### Task 3: Permanent Aggregate and File Deletion

**Files:**
- Modify: `internal_ledger/accounting/posted_entry_maintenance.py`
- Modify: `internal_ledger/internal_ledger/doctype/internal_ledger_source_file/internal_ledger_source_file.py`
- Modify: `internal_ledger/permissions.py`
- Modify: `internal_ledger/api/posting_api.py`
- Modify: `internal_ledger/tests/test_posted_entry_maintenance.py`

**Interfaces:**
- Produces: `permanently_delete_transaction(transaction: str, reason: str, expected_version: str) -> dict`
- Produces: `permanently_delete_transactions(transactions: list[str], reason: str, expected_versions: dict[str, str]) -> list[dict]`
- Produces: `record_deleted_audit(action, reference, company, result) -> None`

- [ ] **Step 1: Write failing deletion and write-permission tests**

```python
def test_permanent_delete_removes_all_transaction_specific_layers():
    item = posted_fixture(with_evidence_file=True)
    current = posting_api.get_posted_entry(item.transaction)
    result = posting_api.permanently_delete_transactions(
        [item.transaction], "录入错误", {item.transaction: current["version"]}
    )[0]
    assert result["status"] == "deleted"
    for doctype, name in (("Imported Transaction", item.transaction),
                          ("Voucher Draft", item.draft),
                          ("Journal Entry", item.journal),
                          ("Ledger Evidence", item.evidence)):
        assert not frappe.db.exists(doctype, name)
    assert not frappe.db.exists("GL Entry", {"voucher_no": item.journal})
    assert not frappe.db.exists("File", item.evidence_file)


def test_source_file_is_kept_until_the_last_batch_transaction_is_deleted():
    first, second, source = two_posted_entries_from_one_source_batch()
    delete(first)
    assert frappe.db.exists("Internal Ledger Source File", source.carrier)
    assert frappe.db.exists("File", source.file)
    delete(second)
    assert not frappe.db.exists("Internal Ledger Source File", source.carrier)
    assert not frappe.db.exists("File", source.file)
    assert not frappe.db.exists("Import Batch", source.batch)


def test_viewer_cannot_permanently_delete_transaction():
    item = posted_fixture()
    current = posting_api.get_posted_entry(item.transaction)
    frappe.set_user(user("账务查看者"))
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        posting_api.permanently_delete_transactions(
            [item.transaction], "删除原因", {item.transaction: current["version"]}
        )
```

Add tests for missing reason, stale version, viewer denial, a simulated file-deletion failure rolling back database rows, and duplicate candidates formerly pointing at the deleted canonical transaction.

- [ ] **Step 2: Run the focused module and verify RED**

Expected: FAIL because the permanent-delete endpoint and service do not exist.

- [ ] **Step 3: Add controlled evidence/source deletion markers**

Define `_SOURCE_DELETE = object()` in `permissions.py`. Update `protect_source_file` and `InternalLedgerSourceFile.on_trash` so only `doc.flags.source_delete is _SOURCE_DELETE` authorizes deletion. Evidence deletion continues to require `_EVIDENCE_WRITE`; evidence `File` rows require `_EVIDENCE_FILE_WRITE`. Add:

```python
def record_deleted_audit(action, reference, company, result):
    require_ledger_admin()
    event = frappe.get_doc({"doctype": "Internal Ledger Audit Event", "action": action,
        "reference_doctype": reference.doctype, "reference_name": reference.name,
        "result": result, "actor": frappe.session.user, "timestamp": now_datetime(),
        "company": company})
    event.flags.audit_insert = _AUDIT_INSERT
    event.insert(ignore_permissions=True, ignore_links=True)
```

- [ ] **Step 4: Implement permanent deletion in deterministic lock order**

```python
def permanently_delete_transaction(transaction, reason, expected_version):
    require_ledger_admin()
    if not isinstance(reason, str) or not reason.strip():
        frappe.throw("永久删除必须填写原因")
    tx, draft, link = _locked_documents(transaction)
    if posted_entry_version(tx, draft, link) != expected_version:
        frappe.throw("账目已被其他人修改，请刷新后重试")
    batch = frappe.get_doc("Import Batch", tx.import_batch, for_update=True)
    reference = tx.as_dict()
    _remove_formal_posting(link, draft)
    _delete_evidence_and_files(tx.name)
    frappe.delete_doc("Voucher Draft", draft.name, ignore_permissions=True)
    _release_duplicate_candidates(tx.name)
    frappe.delete_doc("Imported Transaction", tx.name, ignore_permissions=True)
    if not frappe.db.exists("Imported Transaction", {"import_batch": batch.name}):
        _delete_empty_batch_source(batch)
    record_deleted_audit("permanently_delete_transaction",
                         frappe._dict(doctype="Imported Transaction", name=tx.name),
                         batch.company, "success")
    return {"transaction": tx.name, "status": "deleted"}
```

`_release_duplicate_candidates` must lock rows with `duplicate_of=tx.name`, set them to `status="Imported"`, clear duplicate-review fields and assign unique `released:<name>` dedupe keys under `_IMPORT_WRITE`, so no dangling Dynamic Link remains. `_delete_empty_batch_source` deletes attached source `File` rows under `_SOURCE_DELETE`, then force-deletes the circular source carrier and empty batch under the same marker.

- [ ] **Step 5: Wrap batch deletion in one savepoint per selected transaction**

Return one result per transaction. Validation failures return `{"transaction": name, "status": "failed", "message": chinese_message}` after rolling back that transaction's savepoint; deadlocks propagate so the request can be safely retried. Deduplicate input names while preserving order.

- [ ] **Step 6: Expose the completed permanent-delete endpoint**

```python
@frappe.whitelist(methods=["POST"])
def permanently_delete_transactions(transactions, reason: str, expected_versions) -> list[dict]:
    require_ledger_admin()
    names = frappe.parse_json(transactions) if isinstance(transactions, str) else transactions
    versions = frappe.parse_json(expected_versions) if isinstance(expected_versions, str) else expected_versions
    return posted_entry_maintenance.permanently_delete_transactions(
        names, reason, versions
    )
```

- [ ] **Step 7: Run deletion, evidence and permission modules**

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_posted_entry_maintenance --failfast
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_evidence_api --failfast
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_permissions --failfast
```

Expected: all deletion lifecycle, protected-file, audit and role tests PASS.

- [ ] **Step 8: Commit permanent deletion**

```bash
git add internal_ledger/accounting/posted_entry_maintenance.py \
  internal_ledger/api/posting_api.py internal_ledger/permissions.py \
  internal_ledger/internal_ledger/doctype/internal_ledger_source_file/internal_ledger_source_file.py \
  internal_ledger/tests/test_posted_entry_maintenance.py
git commit -m "feat: permanently delete synchronized ledger entries"
```

---

### Task 4: One Full-Entry Editor in Both Accounting Views

**Files:**
- Create: `internal_ledger/public/frontend/src/components/PostedEntryEditor.vue`
- Modify: `internal_ledger/public/frontend/src/pages/AccountingCenter.vue`
- Modify: `internal_ledger/tests/frontend_review.cjs`
- Modify: `internal_ledger/tests/test_unified_navigation.py`

**Interfaces:**
- Consumes: `internal_ledger.api.posting_api.get_posted_entry`
- Consumes: `internal_ledger.api.posting_api.rebuild_posted_entry`
- Consumes: `internal_ledger.api.posting_api.permanently_delete_transactions`
- Emits: `saved`, `deleted`, `closed`

- [ ] **Step 1: Write failing Vue renderer tests**

Add a `posted_entry_editor` case that mounts the real editor, returns one aggregate from the read API, edits date/counterparty/amount/accounts, and asserts the rebuild call contains all fields and the original version. Assert a viewer sees no inputs, save or delete button.

Extend `accounting_center`:

```javascript
for (const view of ['entries', 'movements']) {
  switchView(view);
  const edit = findButton('编辑整笔账目');
  const remove = findButton('永久删除');
  assert.ok(edit && remove);
  edit.props.onClick(); await settle();
  assert.match(text(page.root), /交易日期.*业务分类.*借方分录.*贷方分录/s);
}
assert.doesNotMatch(text(page.root), /编辑标题、备注与附件/);
```

- [ ] **Step 2: Run frontend cases and verify RED**

```bash
node internal_ledger/tests/frontend_review.cjs posted_entry_editor
node internal_ledger/tests/frontend_review.cjs accounting_center
```

Expected: FAIL because `PostedEntryEditor.vue` and the full-entry buttons do not exist.

- [ ] **Step 3: Implement the full-entry editor**

The component loads the aggregate on transaction change and keeps a separate editable form. Use native datetime/date/number inputs, the existing Chinese business-type catalog, and `classification_api.list_leaf_accounts` for account datalists. Render at least one debit and one credit line; allow add/remove lines but never allow saving zero lines on either side.

Submit exactly:

```javascript
await frappe.call({
  method: 'internal_ledger.api.posting_api.rebuild_posted_entry',
  type: 'POST',
  args: {transaction: props.transaction, values: JSON.stringify(form),
         expected_version: aggregate.version},
});
emit('saved');
```

The delete action requires two confirmations: a typed reason and `window.confirm('此操作将永久删除流水、附件、凭证和总账，且不可恢复。确认继续？')`. It passes `{[transaction]: aggregate.version}` to the permanent-delete endpoint and emits `deleted` only when the returned status is `deleted`.

Embed the existing `TransactionEvidence` component below the accounting fields so title, note and attachments remain editable in the same panel.

- [ ] **Step 4: Replace evidence-only and soft-delete actions in Accounting Center**

Both `ledger-overview-voucher` and `ledger-movement-row` call `openEditor(entry.transaction)`. Replace “编辑标题和备注” with “编辑整笔账目” and add “永久删除”. Replace the bulk toolbar label with “全选可永久删除账目” and call the same permanent-delete endpoint with transaction names and versions loaded before confirmation. Remove the obsolete direct `TransactionEvidence` panel from `AccountingCenter.vue`.

- [ ] **Step 5: Refresh all views after save or delete**

On `saved` or `deleted`, close the editor, clear selected rows, show one Chinese status message and emit `refresh`. `App.vue` already reloads workflow items and counts from that event; do not add a second refresh mechanism.

- [ ] **Step 6: Run focused frontend and static navigation tests**

```bash
node internal_ledger/tests/frontend_review.cjs posted_entry_editor
node internal_ledger/tests/frontend_review.cjs accounting_center
node internal_ledger/tests/frontend_review.cjs transaction_evidence_admin
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_unified_navigation --failfast
```

Expected: both views expose the same editor/delete behavior, viewer controls stay hidden, evidence editing remains available inside the new panel, and navigation tests PASS.

- [ ] **Step 7: Commit the unified editor**

```bash
git add internal_ledger/public/frontend/src/components/PostedEntryEditor.vue \
  internal_ledger/public/frontend/src/pages/AccountingCenter.vue \
  internal_ledger/tests/frontend_review.cjs internal_ledger/tests/test_unified_navigation.py
git commit -m "feat: edit and delete full posted entries"
```

---

### Task 5: Concentrated Regression, Release and Production Verification

**Files:**
- Modify only if verification finds a requirement-specific defect; do not refactor adjacent code.

**Interfaces:**
- Consumes all prior task interfaces.
- Produces a reviewed immutable image tagged with the exact Git commit.

- [ ] **Step 1: Run the concentrated backend gate once**

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_posted_entry_maintenance --failfast
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_posting --failfast
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_evidence_api --failfast
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_permissions --failfast
```

Expected: zero failures. Fix only failures caused by the new aggregate service, rerun the failed module, then rerun this four-module gate once.

- [ ] **Step 2: Run the concentrated frontend/build gate once**

```bash
node internal_ledger/tests/frontend_review.cjs posted_entry_editor
node internal_ledger/tests/frontend_review.cjs accounting_center
node internal_ledger/tests/frontend_review.cjs workflow_navigation
bench build --app internal_ledger
pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
git diff --check
```

Expected: every command exits 0 and the bundle contains the full-entry editor.

- [ ] **Step 3: Commit only verification fixes, if any**

Inspect `git status --short`, stage only the exact requirement files already listed in Tasks 1-4 that were changed to correct a failed verification, then commit with `git commit -m "fix: complete posted entry maintenance verification"`. Skip this step when the worktree is already clean; do not create an empty commit.

- [ ] **Step 4: Push the exact reviewed commit**

```bash
git status --short
git rev-parse HEAD
git push origin HEAD:feature/classification-evidence
```

Require a clean status and record the full 40-character commit before building.

- [ ] **Step 5: Build and inspect the immutable candidate image**

On the server, fetch and detach at the recorded commit. Build `internal-ledger:<full-sha>` from the currently verified production image with `infra/Dockerfile.app-layer`. Verify the revision label, at least 57 asset entries, `desk.bundle.js`, `ledger_workbench.bundle.js`, and zero missing asset files.

- [ ] **Step 6: Back up, migrate and switch using the existing maintenance sequence**

Enable maintenance, disable scheduler, stop workers, create and checksum an encrypted backup, run candidate `bench --site ledger.selfquanta.com migrate`, save the old runtime file, change only `INTERNAL_LEDGER_IMAGE`, and restart `selfquanta-ledger`. If COS configuration is still absent, report that cloud upload is unavailable and proceed only under the user's existing approval for a verified local encrypted backup.

- [ ] **Step 7: Production read-only and synthetic isolated verification**

With maintenance enabled, verify container health, candidate image IDs, database connectivity, runtime assets and role permissions. Run rebuild/permanent-delete smoke only on the isolated test site, never on real production transactions. Disable maintenance, enable scheduler, restart, then verify `/api/method/ping`, the hashed ledger bundle and `internal_ledger_desk.js` return HTTP 200 through the WireGuard endpoint.

- [ ] **Step 8: Report the exact user-visible workflow**

Tell the user to connect WireGuard, open `https://10.77.0.1`, hard refresh, enter “会计中心”, and use “编辑整笔账目” or “永久删除” in either view. State the deployed commit, backup identifier, test totals, and any unresolved external backup configuration without claiming it succeeded.

---

## Plan Self-Review

- Spec coverage: edit fields, both views, atomic rebuild, rollback, permanent deletion, evidence files, empty source batch cleanup, duplicate links, concurrency, permissions, audit and deployment are each assigned to a task.
- Placeholder scan: no deferred implementation markers, skipped tests or incomplete write endpoints remain in the task sequence.
- Type consistency: frontend and API use transaction name as the aggregate key, `expected_version` as a 64-character string, `lines` as dictionaries with `account/debit/credit`, and batch deletion uses `expected_versions` keyed by transaction name.
