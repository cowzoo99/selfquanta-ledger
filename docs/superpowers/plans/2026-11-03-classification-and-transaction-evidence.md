# SelfLedger 自动分类与逐笔佐证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让已匹配流水自动进入专业借贷凭证草稿，并为每笔流水提供受权限控制、可审计的备注与私有附件能力。

**Architecture:** 复用现有 `classify()`、`process_transactions()` 和 `Voucher Draft`，增加管理员专属、幂等的待分类清理入口，不创建第二套分类逻辑。逐笔资料使用新的 `Ledger Evidence` 单据保存清洗后的备注，附件继续使用 Frappe `File` 私有存储；统一工作台按需读取同一份资料。

**Tech Stack:** Python 3.14、Frappe 16.52、ERPNext 16.52、MariaDB、Vue 3 单文件组件、Node 前端组件测试、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-11-03-classification-and-transaction-evidence-design.md`

## Global Constraints

- 单公司、单账套；不增加多租户层或第二个网站。
- 继续使用现有 WireGuard 内网和账号密码，不开放公网。
- 自动化只生成 `Voucher Draft`；不得自动审核或自动过账。
- 疑似重复且未完成复核的流水不得分类或制证。
- 原始导入文件继续仅账套管理员可读；逐笔佐证允许所属公司的账务查看者读取和下载。
- 逐笔附件全部 `is_private = 1`；允许 PNG、JPG、JPEG、WEBP、PDF、DOC、DOCX、TXT、XLS、XLSX、CSV。
- 单个附件最大 10 MiB，每笔流水最多 10 个附件。
- 附件只作为佐证存储、查看和下载，不参与 OCR、内容识别或自动分类。
- 图片可预览；其他允许文件只下载，不增加在线文档预览器。
- 管理员可编辑和删除；账务查看者只能查看和下载。
- 过账后可补充资料，但不得修改已过账凭证的科目、金额或会计期间。
- 新增界面文案使用简体中文；内部随机 ID 只放在折叠的“技术信息”中。
- 必须保留列表分页以及固定的流程“上一步/下一步”。
- 不引入新的第三方 Python 或前端依赖。

## File Structure

- `internal_ledger/accounting/batch_workflow.py`：增加全账套幂等分类清理和汇总。
- `internal_ledger/api/review_api.py`：暴露管理员专属自动分类入口。
- `internal_ledger/internal_ledger/doctype/ledger_evidence/`：定义一笔流水唯一一条的备注载体。
- `internal_ledger/api/evidence_api.py`：读取、保存备注以及上传、删除私有附件的唯一服务入口。
- `internal_ledger/permissions.py`、`internal_ledger/hooks.py`：公司范围、角色、受控写入和私有文件保护。
- `internal_ledger/public/frontend/src/components/TransactionEvidence.vue`：备注、简单表格、图片预览和附件下载。
- `internal_ledger/public/frontend/src/pages/ReviewCenter.vue`：自动分类结果、中文分类去向、逐笔佐证入口和技术信息折叠区。
- `internal_ledger/public/frontend/src/App.vue`：进入或刷新自动分类步骤时先执行管理员清理，再刷新统一数据。
- `internal_ledger/tests/`：后端、权限、前端和端到端回归。
- `docs/acceptance/2026-11-03-classification-evidence-checklist.md`：测试与生产验收证据。

---

### Task 1: 幂等自动分类清理服务

**Files:**
- Modify: `internal_ledger/accounting/batch_workflow.py`
- Modify: `internal_ledger/api/review_api.py`
- Modify: `internal_ledger/tests/test_batch_workflow.py`
- Modify: `internal_ledger/tests/test_review_api.py`

**Interfaces:**
- Consumes: `process_transactions(tx_names: list[str]) -> list[dict]`。
- Produces: `reconcile_unprepared_transactions() -> dict`；`review_api.auto_classify_pending() -> dict`。
- Return: `{"outcomes": list[dict], "summary": {"created": int, "manual": int, "conflict": int, "duplicate": int, "skipped": int, "failed": int}}`。

- [ ] **Step 1: 写自动清理的失败测试**

在 `test_batch_workflow.py` 增加：

```python
def test_reconcile_unprepared_transactions_creates_only_matched_drafts_and_is_idempotent():
    from internal_ledger.accounting.batch_workflow import reconcile_unprepared_transactions

    add_rule()
    matched = transaction()
    unmatched = transaction(counterparty="未匹配商户")
    conflict = transaction(counterparty="冲突商户")
    add_rule(value="冲突商户")
    add_rule(value="冲突商户", debit_account=account("560202"))

    first = reconcile_unprepared_transactions()
    assert first["summary"]["created"] == 1
    assert first["summary"]["manual"] == 1
    assert first["summary"]["conflict"] == 1
    assert frappe.db.count("Voucher Draft", {"imported_transaction": matched}) == 1
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": unmatched})
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": conflict})

    second = reconcile_unprepared_transactions()
    assert second["summary"]["created"] == 0
    assert frappe.db.count("Voucher Draft", {"imported_transaction": matched}) == 1
    assert frappe.db.count("Journal Entry") == frappe.flags.classification_journals
```

在 `test_review_api.py` 增加查看者拒绝测试：

```python
def test_viewer_cannot_trigger_auto_classification():
    from internal_ledger.api.review_api import auto_classify_pending
    from internal_ledger.tests.test_permissions import user

    try:
        frappe.set_user(user("账务查看者"))
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            auto_classify_pending()
    finally:
        frappe.set_user("Administrator")
```

- [ ] **Step 2: 运行定向测试并确认失败**

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_batch_workflow \
  --module internal_ledger.tests.test_review_api
```

Expected: FAIL，报告两个新增函数尚不存在。

- [ ] **Step 3: 实现最小清理服务和 API**

在 `batch_workflow.py` 增加：

```python
PROCESSING_STATUSES = ("created", "manual", "conflict", "duplicate", "skipped", "failed")


def reconcile_unprepared_transactions() -> dict:
    require_ledger_admin()
    rows = frappe.get_all("Imported Transaction", filters={"status": "Imported"},
                          fields=["name", "duplicate_review_status"], order_by="creation")
    drafted = set(frappe.get_all("Voucher Draft", pluck="imported_transaction"))
    names = [row.name for row in rows if row.name not in drafted
             and row.duplicate_review_status not in {"Pending", "Confirmed"}]
    outcomes = process_transactions(names)
    summary = {status: 0 for status in PROCESSING_STATUSES}
    for row in outcomes:
        summary[row["status"]] += 1
    if outcomes:
        result = ";".join(f"{key}:{summary[key]}" for key in PROCESSING_STATUSES)
        record_audit("auto_classification_sweep", "Internal Ledger Settings",
                     "Internal Ledger Settings", result)
    return {"outcomes": outcomes, "summary": summary}
```

在 `review_api.py` 增加：

```python
@frappe.whitelist(methods=["POST"])
def auto_classify_pending():
    from internal_ledger.accounting.batch_workflow import reconcile_unprepared_transactions
    return reconcile_unprepared_transactions()
```

不得改写 `classify()`、`create_draft()` 或现有借贷校验。

- [ ] **Step 4: 运行测试确认通过**

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_batch_workflow \
  --module internal_ledger.tests.test_review_api
```

Expected: PASS；第二次清理不生成草稿；查看者收到 `PermissionError`；无自动 Journal Entry。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger/accounting/batch_workflow.py internal_ledger/api/review_api.py \
  internal_ledger/tests/test_batch_workflow.py internal_ledger/tests/test_review_api.py
git commit -m "feat: reconcile matched transactions automatically"
```

---

### Task 2: Ledger Evidence 数据模型与权限边界

**Files:**
- Create: `internal_ledger/internal_ledger/doctype/ledger_evidence/__init__.py`
- Create: `internal_ledger/internal_ledger/doctype/ledger_evidence/ledger_evidence.json`
- Create: `internal_ledger/internal_ledger/doctype/ledger_evidence/ledger_evidence.py`
- Modify: `internal_ledger/permissions.py`
- Modify: `internal_ledger/hooks.py`
- Create: `internal_ledger/tests/test_evidence_api.py`

**Interfaces:**
- Produces: DocType `Ledger Evidence`，字段 `imported_transaction`、`company`、`note_html`。
- Produces: 受控写入标记 `_EVIDENCE_WRITE` 和 `_EVIDENCE_FILE_WRITE`。

- [ ] **Step 1: 写模型和权限失败测试**

新建 `test_evidence_api.py`：

```python
import unittest
import frappe
from internal_ledger.tests.test_classifier import set_up, tear_down, transaction
from internal_ledger.tests.test_permissions import user


def test_direct_evidence_insert_is_blocked():
    name = transaction()
    doc = frappe.get_doc({"doctype": "Ledger Evidence", "imported_transaction": name,
                          "note_html": "核对说明"})
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        doc.insert()


def test_viewer_reads_evidence_metadata_but_cannot_write_it():
    actor = user("账务查看者")
    assert frappe.has_permission("Ledger Evidence", "read", user=actor)
    assert not frappe.has_permission("Ledger Evidence", "write", user=actor)
```

- [ ] **Step 2: 运行测试确认 DocType 尚不存在**

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_evidence_api
```

Expected: FAIL，`Ledger Evidence` 元数据尚不存在。

- [ ] **Step 3: 创建最小 DocType**

`ledger_evidence.json` 固定为一笔流水唯一一条、公司只读、备注为 Text Editor，权限如下：

```json
{
  "doctype": "DocType", "name": "Ledger Evidence", "module": "Internal Ledger",
  "autoname": "hash", "engine": "InnoDB",
  "field_order": ["imported_transaction", "company", "note_html"],
  "fields": [
    {"fieldname": "imported_transaction", "label": "Imported Transaction", "fieldtype": "Link", "options": "Imported Transaction", "reqd": 1, "unique": 1, "read_only": 1},
    {"fieldname": "company", "label": "Company", "fieldtype": "Link", "options": "Company", "reqd": 1, "read_only": 1},
    {"fieldname": "note_html", "label": "Note", "fieldtype": "Text Editor"}
  ],
  "permissions": [
    {"role": "账套管理员", "read": 1, "write": 1, "create": 1, "delete": 1, "report": 1},
    {"role": "账务查看者", "read": 1, "report": 1}
  ]
}
```

`ledger_evidence.py`：

```python
import frappe
from frappe.model.document import Document
from frappe.utils import sanitize_html
from internal_ledger.permissions import document_company


class LedgerEvidence(Document):
    def validate(self):
        tx = frappe.get_doc("Imported Transaction", self.imported_transaction)
        tx.check_permission("read")
        self.company = document_company(tx)
        self.note_html = sanitize_html(self.note_html or "")
```

- [ ] **Step 4: 接入受控写入和公司权限**

把 `Ledger Evidence` 加入 `LEDGER_DOCTYPES` 和 hooks；增加：

```python
_EVIDENCE_WRITE = object()
_EVIDENCE_FILE_WRITE = object()


def protect_evidence_write(doc, method=None):
    require_ledger_admin()
    if doc.flags.ledger_evidence_write is not _EVIDENCE_WRITE:
        frappe.throw("流水备注与附件只能由佐证服务写入", frappe.PermissionError)
```

扩展现有 `protect_source_file()`：原始文件继续使用 `_SOURCE_INSERT`；新旧关联任一为 `Ledger Evidence` 时，要求管理员、`_EVIDENCE_FILE_WRITE` 且 `is_private == 1`。

- [ ] **Step 5: 迁移并运行模型权限测试**

```bash
bench --site test.localhost migrate
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_evidence_api \
  --module internal_ledger.tests.test_permissions
```

Expected: PASS；管理员不能绕过服务直接写；查看者只读；原始文件保护回归通过。

- [ ] **Step 6: 提交**

```bash
git add internal_ledger/internal_ledger/doctype/ledger_evidence internal_ledger/permissions.py \
  internal_ledger/hooks.py internal_ledger/tests/test_evidence_api.py
git commit -m "feat: add protected transaction evidence model"
```

---

### Task 3: 备注与私有附件服务

**Files:**
- Create: `internal_ledger/api/evidence_api.py`
- Modify: `internal_ledger/tests/test_evidence_api.py`
- Modify: `internal_ledger/tests/test_permissions.py`

**Interfaces:**
- Produces: `get_evidence(tx_name: str) -> dict`。
- Produces: `save_evidence(tx_name: str, note_html: str) -> dict`。
- Produces: `upload_evidence(tx_name: str, filename: str, content_base64: str, mime_type: str) -> dict`。
- Produces: `delete_evidence_file(tx_name: str, attachment_name: str) -> dict`。
- Attachment shape: `{"name": str, "file_name": str, "file_url": str, "file_size": int, "extension": str, "is_image": bool}`。

- [ ] **Step 1: 写 API、限制、权限和审计失败测试**

在 `test_evidence_api.py` 增加：

```python
import base64
from unittest.mock import patch


def encoded(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def test_admin_saves_sanitized_note_and_private_image():
    from internal_ledger.api.evidence_api import get_evidence, save_evidence, upload_evidence
    tx_name = transaction()
    saved = save_evidence(tx_name,
        '<p>业务说明</p><script>alert(1)</script><table><tr><td>合同</td></tr></table>')
    assert "业务说明" in saved["note_html"] and "<table" in saved["note_html"]
    assert "script" not in saved["note_html"].lower()
    result = upload_evidence(tx_name, "回单.png",
        encoded(b"\x89PNG\r\n\x1a\n" + b"0" * 32), "image/png")
    attachment = frappe.get_doc("File", result["attachments"][0]["name"])
    assert attachment.is_private == 1
    assert attachment.attached_to_doctype == "Ledger Evidence"
    assert get_evidence(tx_name)["attachments"][0]["is_image"] is True


def test_file_limits_and_signature_are_enforced_server_side():
    from internal_ledger.api.evidence_api import MAX_EVIDENCE_FILE_BYTES, upload_evidence
    tx_name = transaction()
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "10 MiB"):
        upload_evidence(tx_name, "过大.pdf",
            encoded(b"%PDF-" + b"0" * MAX_EVIDENCE_FILE_BYTES), "application/pdf")
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "文件内容"):
        upload_evidence(tx_name, "伪装.pdf", encoded(b"not-a-pdf"), "application/pdf")
    with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, "不支持"):
        upload_evidence(tx_name, "脚本.html", encoded(b"<script></script>"), "text/html")


def test_viewer_reads_but_cannot_change_or_delete_evidence():
    from internal_ledger.api.evidence_api import delete_evidence_file, get_evidence, save_evidence, upload_evidence
    tx_name = transaction()
    attachment = upload_evidence(tx_name, "说明.txt",
        encoded("说明".encode()), "text/plain")["attachments"][0]
    try:
        frappe.set_user(user("账务查看者"))
        assert get_evidence(tx_name)["attachments"][0]["name"] == attachment["name"]
        assert frappe.has_permission("File", "read", attachment["name"])
        for operation in (lambda: save_evidence(tx_name, "越权"),
                          lambda: delete_evidence_file(tx_name, attachment["name"])):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()
    finally:
        frappe.set_user("Administrator")


def test_audit_failure_rolls_back_note():
    from internal_ledger.api import evidence_api
    tx_name = transaction()
    with patch.object(evidence_api, "record_audit", side_effect=RuntimeError("audit unavailable")):
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "audit unavailable"):
            evidence_api.save_evidence(tx_name, "不能无审计保存")
    assert not frappe.db.exists("Ledger Evidence", {"imported_transaction": tx_name})
```

同一模块再覆盖：第 11 个附件被拒绝；错误 MIME 被拒绝；附件不属于当前流水时删除被拒绝；查看者仍不能读取 `Internal Ledger Source File`。

- [ ] **Step 2: 运行测试确认 API 缺失**

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_evidence_api \
  --module internal_ledger.tests.test_permissions
```

Expected: FAIL，`internal_ledger.api.evidence_api` 尚不存在。

- [ ] **Step 3: 实现文件校验和序列化**

在 `evidence_api.py` 定义：

```python
MAX_EVIDENCE_FILE_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_FILES = 10
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".doc", ".docx",
                      ".txt", ".xls", ".xlsx", ".csv"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
```

`_decode_upload()` 拒绝非法 Base64、超限内容和非白名单扩展名；检查 PNG、JPEG、WEBP、PDF、OLE DOC/XLS、ZIP DOCX/XLSX 文件头；TXT/CSV 拒绝 NUL 字节。MIME 只允许与扩展名对应的值，不能作为唯一判断。附件响应只返回接口声明的六个字段，不返回服务器路径或正文。

- [ ] **Step 4: 实现受控 CRUD**

读取入口：

```python
def _transaction(tx_name, write=False):
    doc = frappe.get_doc("Imported Transaction", tx_name, for_update=write)
    doc.check_permission("write" if write else "read")
    return doc


@frappe.whitelist(methods=["GET", "POST"])
def get_evidence(tx_name: str):
    tx = _transaction(tx_name)
    name = frappe.db.get_value("Ledger Evidence", {"imported_transaction": tx.name}, "name")
    if not name:
        return {"name": None, "transaction": tx.name, "note_html": "", "attachments": []}
    evidence = frappe.get_doc("Ledger Evidence", name)
    evidence.check_permission("read")
    return _serialize_evidence(evidence)
```

写接口先 `require_ledger_admin()` 并锁定流水。`get_evidence` 在没有记录时只返回空结构；`save_evidence` 和 `upload_evidence` 才按需创建唯一 evidence，并在计数或修改前锁定它。所有写入设置 Task 2 的受控标记。上传使用：

```python
attachment = frappe.get_doc({
    "doctype": "File", "file_name": safe_name, "content": content,
    "attached_to_doctype": "Ledger Evidence", "attached_to_name": evidence.name,
    "is_private": 1,
})
attachment.flags.evidence_file_write = _EVIDENCE_FILE_WRITE
attachment.insert()
```

保存备注、上传、删除分别记录 `update_evidence_note`、`upload_evidence`、`delete_evidence`，审计引用统一为 `Imported Transaction`。删除前核对 File 的 doctype、attached name 和当前流水。

- [ ] **Step 5: 运行定向测试确认通过**

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_evidence_api \
  --module internal_ledger.tests.test_permissions
```

Expected: PASS；服务端限制、角色边界、原始文件隔离和审计均生效。

- [ ] **Step 6: 提交**

```bash
git add internal_ledger/api/evidence_api.py internal_ledger/tests/test_evidence_api.py \
  internal_ledger/tests/test_permissions.py
git commit -m "feat: add audited private evidence service"
```

---

### Task 4: 自动分类中文闭环与专业借贷展示

**Files:**
- Modify: `internal_ledger/public/frontend/src/App.vue`
- Modify: `internal_ledger/public/frontend/src/pages/ReviewCenter.vue`
- Modify: `internal_ledger/public/frontend/src/components/DraftLines.vue`
- Modify: `internal_ledger/tests/frontend_review.cjs`
- Modify: `internal_ledger/tests/test_unified_navigation.py`

**Interfaces:**
- Consumes: `review_api.auto_classify_pending()` 和现有 `list_review_items()`。
- Produces: `classificationRun`，包含 `idle|loading|success|error`、summary 和中文错误。
- Keeps: `WorkflowNavigation.vue` 现有五步、分页和固定页脚。

- [ ] **Step 1: 写前端失败测试**

在 `frontend_review.cjs` 增加 `auto_classification`：管理员进入 classification 时先调用 `auto_classify_pending`，完成后才读列表；返回 summary 时显示“已自动分类并生成草稿、需要人工分类、规则冲突、失败”。查看者不得发出写请求。

增加 `classification_detail`：

```javascript
for (const label of ['业务分类', '借方科目', '贷方科目', '技术信息']) {
  assert.match(text(page.root), new RegExp(label));
}
assert.doesNotMatch(primaryListText, /fdtn|fdsc|tx-[a-z0-9]{6}/);
assert.match(text(page.root), /上一页/);
assert.match(text(page.root), /下一页/);
```

在 `test_unified_navigation.py` 增加：

```python
def test_auto_classification_frontend():
    run_frontend_case("auto_classification")


def test_classification_detail_frontend():
    run_frontend_case("classification_detail")
```

- [ ] **Step 2: 运行前端测试确认失败**

```bash
node internal_ledger/tests/frontend_review.cjs auto_classification
node internal_ledger/tests/frontend_review.cjs classification_detail
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_unified_navigation
```

Expected: FAIL，自动处理和新信息层级尚未接入。

- [ ] **Step 3: 在 App 层按顺序清理和读取**

在 `App.vue` 增加：

```javascript
const classificationRun = reactive({status: 'idle', summary: null, error: ''});

async function refreshPage() {
  if (workflowStage.value === 3 && canWrite) {
    Object.assign(classificationRun, {status: 'loading', summary: null, error: ''});
    try {
      const result = await call('auto_classify_pending');
      Object.assign(classificationRun, {status: 'success', summary: result.summary, error: ''});
    } catch (e) {
      Object.assign(classificationRun, {status: 'error', summary: null, error: errorMessage(e)});
    }
  }
  return refresh();
}
```

使用 `onMounted(refreshPage)`；页头刷新调用 `refreshPage`；子组件单笔修改仍只触发普通 `refresh`。向 `ReviewCenter` 传 `classificationRun`。

- [ ] **Step 4: 调整自动分类主信息**

在 `ReviewCenter.vue` 移除“批量生成凭证”选择和按钮；显示四类中文汇总；第 3 步数据稳定后自动打开当前分页第一笔未匹配/冲突项；列表按钮改为“查看明细”；主标题显示交易对象、金额和日期。草稿详情固定显示业务类型、分类依据以及 `DraftLines` 的借贷科目和金额。流水 ID、批次 ID、草稿 ID、规则 ID 放入 `<details><summary>技术信息</summary>`。在 `DraftLines.vue` 同样把技术 ID 移出主凭证区。

- [ ] **Step 5: 运行测试和构建**

```bash
node internal_ledger/tests/frontend_review.cjs auto_classification
node internal_ledger/tests/frontend_review.cjs classification_detail
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_unified_navigation
bench build --app internal_ledger
```

Expected: PASS；管理员自动处理，查看者只读；随机 ID 不在主列表；Vue 构建成功。

- [ ] **Step 6: 提交**

```bash
git add internal_ledger/public/frontend/src/App.vue \
  internal_ledger/public/frontend/src/pages/ReviewCenter.vue \
  internal_ledger/public/frontend/src/components/DraftLines.vue \
  internal_ledger/tests/frontend_review.cjs internal_ledger/tests/test_unified_navigation.py
git commit -m "feat: complete automatic classification workflow"
```

---

### Task 5: 逐笔备注、表格与附件界面

**Files:**
- Create: `internal_ledger/public/frontend/src/components/TransactionEvidence.vue`
- Modify: `internal_ledger/public/frontend/src/pages/ReviewCenter.vue`
- Modify: `internal_ledger/public/frontend/src/api.js`
- Modify: `internal_ledger/public/frontend/src/App.vue`
- Modify: `internal_ledger/tests/frontend_review.cjs`
- Modify: `internal_ledger/tests/test_unified_navigation.py`

**Interfaces:**
- Consumes: Task 3 的四个 evidence API。
- Produces: `TransactionEvidence` props `{transaction: String, canWrite: Boolean}`。
- State rule: transaction 变化时自行加载；过期异步响应不得覆盖当前流水。

- [ ] **Step 1: 写附件组件失败测试**

在 `frontend_review.cjs` 增加管理员和查看者场景：

```javascript
for (const label of ['备注与附件', '加粗', '项目列表', '插入 2×2 表格',
                     '保存备注', '上传附件', '图片预览', '下载']) {
  assert.match(text(admin.root), new RegExp(label));
}
assert.equal(calls.some(row => row.method.endsWith('save_evidence')), true);
assert.equal(calls.some(row => row.method.endsWith('upload_evidence')), true);
assert.match(text(viewer.root), /备注与附件/);
assert.match(text(viewer.root), /下载/);
assert.doesNotMatch(text(viewer.root), /保存备注|上传附件|删除附件/);
```

再断言选择另一笔流水后重新调用 `get_evidence`，旧流水的延迟响应不会覆盖当前流水。

- [ ] **Step 2: 运行新场景确认组件缺失**

```bash
node internal_ledger/tests/frontend_review.cjs transaction_evidence_admin
node internal_ledger/tests/frontend_review.cjs transaction_evidence_viewer
```

Expected: FAIL，`TransactionEvidence.vue` 尚不存在。

- [ ] **Step 3: 增加独立 API 调用前缀**

在 `api.js` 增加：

```javascript
export async function evidenceCall(method, args = {}) {
  const response = await frappe.call({
    method: `internal_ledger.api.evidence_api.${method}`,
    args,
    type: method === 'get_evidence' ? 'GET' : 'POST',
  });
  return response.message;
}
```

- [ ] **Step 4: 实现最小备注与附件组件**

`TransactionEvidence.vue` 使用受控 `contenteditable` 编辑区，工具栏只提供加粗、项目列表和插入 2×2 表格；输入事件读取 `editor.innerHTML`，服务端仍负责最终清洗。插表使用 DOM Range API 创建 `table > tbody > tr > td`，不执行用户 HTML。

上传时先给出浏览器端扩展名、大小、数量提示，再用 `FileReader.readAsDataURL()`，截取 `base64,` 后的内容调用服务端。浏览器校验不能代替服务端校验。

附件渲染：

```vue
<img v-if="attachment.is_image" :src="attachment.file_url"
     :alt="attachment.file_name" class="ledger-evidence-preview">
<a :href="attachment.file_url" download>{{ attachment.file_name }} · 下载</a>
<button v-if="canWrite" type="button" @click="remove(attachment)">删除附件</button>
```

组件用递增请求编号丢弃过期响应；切换 transaction 时清空编辑状态。保存、上传、删除错误均通过 `errorMessage()` 显示。

- [ ] **Step 5: 接入统一流水详情并补最小样式**

`ReviewCenter.vue` 详情末尾加入：

```vue
<TransactionEvidence v-if="active" :transaction="active.transaction.name" :can-write="canWrite" />
```

`App.vue` 只增加图片最大尺寸、附件换行、编辑区焦点和移动端单列样式；不增加路由或独立网站。

- [ ] **Step 6: 运行测试、构建和权限回归**

```bash
node internal_ledger/tests/frontend_review.cjs transaction_evidence_admin
node internal_ledger/tests/frontend_review.cjs transaction_evidence_viewer
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_unified_navigation \
  --module internal_ledger.tests.test_permissions
bench build --app internal_ledger
```

Expected: PASS；管理员可编辑；查看者只读下载；统一工作台构建无 Vue 错误。

- [ ] **Step 7: 提交**

```bash
git add internal_ledger/public/frontend/src/components/TransactionEvidence.vue \
  internal_ledger/public/frontend/src/pages/ReviewCenter.vue internal_ledger/public/frontend/src/api.js \
  internal_ledger/public/frontend/src/App.vue internal_ledger/tests/frontend_review.cjs \
  internal_ledger/tests/test_unified_navigation.py
git commit -m "feat: add transaction notes and evidence UI"
```

---

### Task 6: 端到端验收与完整回归

**Files:**
- Modify: `internal_ledger/tests/test_end_to_end.py`
- Create: `docs/acceptance/2026-11-03-classification-evidence-checklist.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 1–5 的后端 API、DocType 和统一工作台。
- Produces: 可重复的完整验收用例和发布门禁记录。

- [ ] **Step 1: 写完整链路测试**

在 `test_end_to_end.py` 增加 `test_import_reconcile_evidence_review_and_post_end_to_end`，使用 `tests/fixtures/bank.csv` 和现有导入帮助函数完成：

```python
def test_import_reconcile_evidence_review_and_post_end_to_end():
    from internal_ledger.api.evidence_api import get_evidence, save_evidence, upload_evidence
    from internal_ledger.api.posting_api import approve_and_post
    from internal_ledger.api.review_api import auto_classify_pending

    original, candidate, unmatched = import_duplicate_acceptance_batch()
    add_rule(value=original.counterparty)
    before_journals = frappe.db.count("Journal Entry")
    result = auto_classify_pending()
    assert result["summary"]["created"] == 1
    assert candidate.duplicate_review_status == "Pending"
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": candidate.name})

    draft = frappe.get_doc("Voucher Draft", {"imported_transaction": original.name})
    assert sum(Decimal(str(row.debit)) for row in draft.lines) == sum(
        Decimal(str(row.credit)) for row in draft.lines)
    assert frappe.db.count("Journal Entry") == before_journals

    saved = save_evidence(original.name,
        "<p>合同已核对</p><table><tr><td>编号</td></tr></table>")
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode("ascii")
    uploaded = upload_evidence(original.name, "回单.png", png, "image/png")
    assert saved["note_html"] and uploaded["attachments"]
    assert get_evidence(original.name)["attachments"][0]["is_image"] is True

    journal = approve_and_post([draft.name])[0]["journal_entry"]
    snapshot = frappe.get_doc("Journal Entry", journal).as_dict()
    save_evidence(original.name, "<p>过账后补充说明</p>")
    current = frappe.get_doc("Journal Entry", journal).as_dict()
    assert current["total_debit"] == snapshot["total_debit"]
    assert current["accounts"] == snapshot["accounts"]
    assert not frappe.db.exists("Voucher Draft", {"imported_transaction": unmatched.name})
```

同一测试还必须导入相同内容验证重复候选停在第 2 步；使用查看者账号验证可读佐证、不可写佐证、不可读取原始文件。测试数据只能使用仓库脱敏 fixture。

- [ ] **Step 2: 运行端到端测试**

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_end_to_end
```

Expected: PASS；如出现集成失败，只修改对应的已规划模块，不顺带重构其他业务。

- [ ] **Step 3: 完成验收清单和 README**

验收清单记录运行命令、测试站点、提交 SHA、构建结果、管理员与查看者场景、文件限制、原始文件隔离、无自动过账、备份文件、部署镜像和回滚镜像。README 增加“自动分类闭环”和“逐笔私有佐证”，明确“附件不参与 OCR”。

- [ ] **Step 4: 运行完整应用门禁**

```bash
bench --site test.localhost migrate
bench --site test.localhost run-tests --app internal_ledger
bench build --app internal_ledger
pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
git diff --check
```

Expected: 全部 PASS；生成 ledger bundle；静态部署检查通过；无空白错误。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger/tests/test_end_to_end.py \
  docs/acceptance/2026-11-03-classification-evidence-checklist.md README.md
git commit -m "test: verify classification and evidence workflow"
```

---

### Task 7: 分支复核、生产部署与上线验证

**Files:**
- Modify after verification: `docs/acceptance/2026-11-03-classification-evidence-checklist.md`

**Interfaces:**
- Consumes: 干净且已推送的功能分支、`infra/build_image.ps1`、`infra/backup.ps1`、`docs/operations/deploy.md`。
- Produces: 不可变 SHA 镜像、部署前加密备份、生产验证记录和回滚目标。

- [ ] **Step 1: 最终代码复核和分支门禁**

```bash
git status --short
git diff origin/main..HEAD --check
git log --oneline origin/main..HEAD
bench --site test.localhost run-tests --app internal_ledger
```

Expected: 工作树干净；只有本规格相关提交；测试通过；不含真实账单、密码、私钥或备份密钥。

- [ ] **Step 2: 推送分支并构建不可变镜像**

```powershell
git push -u origin feature/classification-evidence
$env:INTERNAL_LEDGER_APP_REF = 'feature/classification-evidence'
pwsh -NoProfile -File infra/build_image.ps1 -ValidateOnly
pwsh -NoProfile -File infra/build_image.ps1
```

Expected: 远端分支指向 HEAD；镜像标签为 `internal-ledger:<40位HEAD>`；revision label 完全一致。

- [ ] **Step 3: 生产变更前生成加密备份**

在服务器 `/opt/selfquanta-ledger` 使用 `/etc/selfquanta/ledger/runtime.env` 的外部密钥和目的目录：

```bash
sudo systemctl stop selfquanta-ledger-backup.timer
set -a
. /etc/selfquanta/ledger/runtime.env
set +a
pwsh -NoProfile -File infra/backup.ps1
```

Expected: 退出 0；备份含数据库、公有文件和私有文件；输出 `.zip.enc` 路径和校验结果，不输出密钥。

- [ ] **Step 4: 维护模式迁移并切换镜像**

按 `docs/operations/deploy.md` 两阶段执行：启用维护模式、停调度器、运行 `bench --site ledger.selfquanta.com migrate`、验证新 DocType 与资源清单；把 `/etc/selfquanta/ledger/runtime.env` 的 `INTERNAL_LEDGER_IMAGE` 更新为本次 40 位 SHA 并重启 `selfquanta-ledger`。OCR 未变化，不重建 OCR。

Expected: 容器健康；迁移成功；Desk 与 ledger bundle 返回 JavaScript；只有 WireGuard 私网入口可访问。

- [ ] **Step 5: 生产烟测和受控业务验收**

通过 `https://10.77.0.1` 完成：管理员确认统一首页；用脱敏小样本验证重复复核、自动分类、草稿、分页和固定导航；添加文字、2×2 表格、图片和 TXT；查看者验证可读下载、不可写、不可读原始文件；不在生产公司过账合成数据；检查 ping、现有正式凭证和报表；重启后再次确认数据和私有附件。

任何一步失败：立即启用维护模式并切回验收清单中的上一镜像；如迁移产生不兼容数据，使用本次备份按 `docs/operations/backup-restore.md` 恢复。

- [ ] **Step 6: 写入上线记录并提交**

将实际提交 SHA、镜像 ID、备份文件名、部署时间、烟测结果和回滚镜像写入验收清单：

```bash
git add docs/acceptance/2026-11-03-classification-evidence-checklist.md
git commit -m "docs: record classification evidence release"
git push origin feature/classification-evidence
```

Expected: 记录不含密钥、密码或真实财务数据；生产镜像与记录 SHA 一致；备份定时器恢复运行。
