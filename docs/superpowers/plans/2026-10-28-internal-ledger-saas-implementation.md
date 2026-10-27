# 协同内账 SaaS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ERPNext/Frappe v16 上交付单公司、单账套的协同内账 MVP，完成银行卡、微信、支付宝流水从导入、规则分类、人工审核到正式凭证和报表的闭环。

**Architecture:** 保留 ERPNext 会计内核与 Frappe 权限体系，新建独立应用 `internal_ledger`。来源适配器只负责规范化数据，领域服务负责去重、分类和草稿，过账服务通过 ERPNext `Journal Entry` 标准文档接口提交；任何来源文件都不能直接写总账。

**Tech Stack:** ERPNext v16.52.1、Frappe v16.52.x、Python 3.14、MariaDB、Redis、Vue/Frappe UI、Docker Engine 23+、Docker Compose v2、pytest/FrappeTestCase。

**Spec:** `docs/superpowers/specs/2026-10-28-internal-ledger-saas-design.md`

## Global Constraints

- 项目根目录固定为 `E:\杭州逆熵云\记账`。
- 单公司、单账套、人民币本位币，科目基础为《小企业会计准则》。
- 不修改 ERPNext/Frappe 核心代码；所有业务改动位于 `internal_ledger` 应用。
- 所有自动结果先成为草稿，只有账套管理员人工审核后才能过账。
- 账务查看者只读，服务端接口必须独立执行权限检查。
- 原始文件只读保存；已过账凭证只能冲销，不能静默改写。
- 不实现税务申报、自动付款、银企直连、发票 OCR、多租户或 AI 自主入账。
- 使用真实脱敏样本验收；样本不得提交原始银行卡号、姓名或身份证号。
- 每个任务采用测试先行，验证通过后单独提交。

## Planned File Structure

```text
internal_ledger/
├── hooks.py                         # 应用钩子、权限查询和静态资源声明
├── modules.txt                      # Frappe 模块声明
├── install.py                       # 角色、默认设置和科目模板安装
├── permissions.py                   # 查看者与管理员的服务端权限策略
├── api/
│   ├── import_api.py                # 预览和确认导入白名单接口
│   ├── review_api.py                # 分类、审核和驳回接口
│   └── posting_api.py               # 过账与冲销接口
├── importing/
│   ├── models.py                    # NormalizedTransaction 等纯 Python 类型
│   ├── registry.py                  # 来源适配器选择
│   ├── dedupe.py                    # 来源交易号与回退指纹
│   ├── bank.py                      # 银行通用表格解析
│   ├── wechat.py                    # 微信账单解析
│   └── alipay.py                    # 支付宝账单解析
├── accounting/
│   ├── classifier.py                # 规则优先级与冲突检测
│   ├── drafts.py                    # 借贷草稿生成与平衡验证
│   ├── posting.py                   # Journal Entry 创建与幂等控制
│   └── reversal.py                  # 冲销凭证
├── internal_ledger/doctype/
│   ├── internal_ledger_settings/    # 单例设置
│   ├── import_batch/                # 导入批次
│   ├── imported_transaction/        # 规范化流水
│   ├── classification_rule/         # 分类规则
│   ├── voucher_draft/               # 凭证草稿
│   ├── voucher_draft_line/           # 草稿借贷子表
│   ├── posting_link/                 # 流水与正式凭证唯一关联
│   └── internal_ledger_audit_event/ # 审计事件
├── public/frontend/                 # 工作台与审核中心前端源码
└── tests/
    ├── fixtures/                    # 脱敏银行、微信、支付宝样本
    ├── test_importers.py
    ├── test_dedupe.py
    ├── test_classifier.py
    ├── test_drafts.py
    ├── test_posting.py
    ├── test_permissions.py
    └── test_end_to_end.py
infra/
├── compose.yaml                     # 生产服务编排
├── versions.env                     # 固定镜像与上游版本
├── nginx.conf                       # HTTPS 反向代理
├── build_image.ps1                  # 由当前 Git remote/commit 构建自定义镜像
├── backup.ps1                       # 数据库与站点文件备份
└── restore-check.ps1                # 隔离环境恢复验证
```

---

### Task 1: 建立可安装的 Frappe 应用骨架

**Files:**
- Create: `pyproject.toml`
- Create: `internal_ledger/__init__.py`
- Create: `internal_ledger/hooks.py`
- Create: `internal_ledger/modules.txt`
- Create: `internal_ledger/tests/test_app_boot.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Frappe v16 应用加载约定。
- Produces: 可由 `bench --site test.localhost install-app internal_ledger` 安装的 Python 包；应用版本常量 `internal_ledger.__version__`。

- [ ] **Step 1: 写应用加载失败测试**

```python
def test_app_metadata_is_importable():
    import internal_ledger
    assert internal_ledger.__version__ == "0.1.0"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_app_boot`
Expected: FAIL，提示无法导入 `internal_ledger` 或缺少版本常量。

- [ ] **Step 3: 创建最小应用元数据**

```python
# internal_ledger/__init__.py
__version__ = "0.1.0"
```

`hooks.py` 仅声明 `app_name`、`app_title`、`app_publisher`、`app_description`、`app_email`、`app_license = "AGPL-3.0-only"`；`modules.txt` 只包含 `Internal Ledger`。`pyproject.toml` 声明 Python `>=3.14`、`flit_core >=3.4,<4`、构建后端 `flit_core.buildapi`，并在 `[tool.bench.frappe-dependencies]` 声明 `frappe = ">=16.52.0,<17.0.0"`，不添加业务依赖。

- [ ] **Step 4: 安装并运行启动测试**

Run: `bench --site test.localhost install-app internal_ledger`
Expected: 安装成功且迁移完成。

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_app_boot`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add .gitignore pyproject.toml internal_ledger
git commit -m "chore: scaffold internal ledger app"
```

### Task 2: 创建设置、角色与小企业科目模板

**Files:**
- Create: `internal_ledger/install.py`
- Create: `internal_ledger/fixtures/small_business_chart_cn.json`
- Create: `internal_ledger/internal_ledger/doctype/internal_ledger_settings/internal_ledger_settings.json`
- Create: `internal_ledger/internal_ledger/doctype/internal_ledger_settings/internal_ledger_settings.py`
- Create: `internal_ledger/tests/test_install.py`
- Modify: `internal_ledger/hooks.py`

**Interfaces:**
- Consumes: ERPNext `Company`、`Account` 和 Frappe `Role` DocType。
- Produces: `ensure_roles() -> None`、`install_chart(company: str) -> None`；角色 `账套管理员`、`账务查看者`；单例设置字段 `company`、`base_currency`、`fiscal_year_start`。

- [ ] **Step 1: 写角色与科目安装测试**

```python
def test_install_creates_roles_and_balanced_root_groups():
    ensure_roles()
    assert frappe.db.exists("Role", "账套管理员")
    assert frappe.db.exists("Role", "账务查看者")
    chart = load_chart_fixture()
    assert set(chart) == {"资产", "负债", "所有者权益", "成本", "损益"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_install`
Expected: FAIL，提示 `ensure_roles` 或科目模板不存在。

- [ ] **Step 3: 实现最小安装逻辑**

```python
ROLE_PERMISSIONS = {
    "账套管理员": {"read", "write", "create", "submit", "cancel"},
    "账务查看者": {"read"},
}

def ensure_roles() -> None:
    for role in ROLE_PERMISSIONS:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert()
```

科目 JSON 只放首版必需的一级与常用明细科目；每个叶子节点必须有唯一科目编码、ERPNext `root_type` 与 `report_type`。安装函数遇到已有同编码科目时校验而非覆盖。

- [ ] **Step 4: 运行安装测试与迁移**

Run: `bench --site test.localhost migrate && bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_install`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger
git commit -m "feat: add ledger settings roles and chart"
```

### Task 3: 建立导入批次、统一流水与去重

**Files:**
- Create: `internal_ledger/importing/models.py`
- Create: `internal_ledger/importing/dedupe.py`
- Create: `internal_ledger/internal_ledger/doctype/import_batch/*`
- Create: `internal_ledger/internal_ledger/doctype/imported_transaction/*`
- Create: `internal_ledger/tests/test_dedupe.py`

**Interfaces:**
- Consumes: Task 2 的资金账户设置。
- Produces: `NormalizedTransaction`；`build_dedupe_key(tx: NormalizedTransaction) -> str`；`save_batch(source: str, file_hash: str, rows: list[NormalizedTransaction]) -> str`。

- [ ] **Step 1: 写去重测试**

```python
def test_source_id_wins_and_fallback_is_stable():
    with_id = tx(source_id="WX-1", occurred_at="2026-10-01 10:00:00")
    assert build_dedupe_key(with_id) == "wechat:WX-1"
    no_id = tx(source_id=None, occurred_at="2026-10-01 10:00:00")
    assert build_dedupe_key(no_id) == build_dedupe_key(no_id)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_dedupe`
Expected: FAIL，提示 `NormalizedTransaction` 或 `build_dedupe_key` 不存在。

- [ ] **Step 3: 实现规范化类型与去重键**

```python
@dataclass(frozen=True)
class NormalizedTransaction:
    source: str
    source_id: str | None
    occurred_at: datetime
    amount: Decimal
    direction: Literal["income", "expense"]
    counterparty: str
    summary: str
    payment_account: str
    raw_row: int

def build_dedupe_key(tx: NormalizedTransaction) -> str:
    if tx.source_id:
        return f"{tx.source}:{tx.source_id}"
    raw = "|".join((tx.payment_account, tx.occurred_at.isoformat(), str(tx.amount), tx.counterparty, tx.summary))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

在 `Imported Transaction` 上为 `dedupe_key` 建唯一索引；批次确认在单个数据库事务中写入，新重复项标记为 `Duplicate` 而不创建草稿。

- [ ] **Step 4: 运行去重与 DocType 测试**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_dedupe`
Expected: PASS，包括并发插入时最多一条成功。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger
git commit -m "feat: add import batches and deduplication"
```

### Task 4: 实现银行、微信与支付宝适配器

**Files:**
- Create: `internal_ledger/importing/registry.py`
- Create: `internal_ledger/importing/bank.py`
- Create: `internal_ledger/importing/wechat.py`
- Create: `internal_ledger/importing/alipay.py`
- Create: `internal_ledger/api/import_api.py`
- Create: `internal_ledger/tests/fixtures/bank.csv`
- Create: `internal_ledger/tests/fixtures/wechat.csv`
- Create: `internal_ledger/tests/fixtures/alipay.csv`
- Create: `internal_ledger/tests/test_importers.py`

**Interfaces:**
- Consumes: `NormalizedTransaction`、`save_batch`。
- Produces: `parse_statement(source: str, content: bytes, payment_account: str) -> ParseResult`；`ParseResult(rows, errors, file_hash)`；白名单接口 `preview_import` 和 `confirm_import`。

- [ ] **Step 1: 为三类脱敏样本写契约测试**

```python
@pytest.mark.parametrize("source,file_name", [
    ("bank", "bank.csv"), ("wechat", "wechat.csv"), ("alipay", "alipay.csv")
])
def test_statement_parsers_return_normalized_rows(source, file_name):
    result = parse_statement(source, fixture(file_name), "测试资金账户")
    assert len(result.rows) == 3
    assert all(row.amount > 0 for row in result.rows)
    assert {row.direction for row in result.rows} == {"income", "expense"}
```

- [ ] **Step 2: 运行测试确认三个适配器均失败**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_importers`
Expected: FAIL，提示没有已注册适配器。

- [ ] **Step 3: 实现显式表头识别与金额规范化**

```python
PARSERS = {"bank": parse_bank, "wechat": parse_wechat, "alipay": parse_alipay}

def parse_statement(source: str, content: bytes, payment_account: str) -> ParseResult:
    try:
        parser = PARSERS[source]
    except KeyError as exc:
        raise ValidationError(f"不支持的账单来源: {source}") from exc
    return parser(content, payment_account)
```

每个解析器必须按已知表头显式映射，不按列位置猜测；金额统一为正数并另存方向；错误行记录原始行号和中文原因。预览接口不落库，确认接口重新计算文件哈希并调用 `save_batch`。

- [ ] **Step 4: 运行适配器与恶意文件测试**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_importers`
Expected: PASS，且超限文件、未知表头、公式单元格和非法编码得到明确拒绝或行级错误。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger
git commit -m "feat: import bank wechat and alipay statements"
```

### Task 5: 实现分类规则与借贷草稿

**Files:**
- Create: `internal_ledger/accounting/classifier.py`
- Create: `internal_ledger/accounting/drafts.py`
- Create: `internal_ledger/internal_ledger/doctype/classification_rule/*`
- Create: `internal_ledger/internal_ledger/doctype/voucher_draft/*`
- Create: `internal_ledger/internal_ledger/doctype/voucher_draft_line/*`
- Create: `internal_ledger/tests/test_classifier.py`
- Create: `internal_ledger/tests/test_drafts.py`

**Interfaces:**
- Consumes: `Imported Transaction`、Task 2 科目与资金账户。
- Produces: `classify(tx_name: str) -> ClassificationResult`；`create_draft(tx_name: str, result: ClassificationResult) -> str`；`validate_balanced(lines: Sequence[DraftLine]) -> None`。

- [ ] **Step 1: 写优先级、冲突和借贷平衡测试**

```python
def test_exact_counterparty_beats_keyword_and_conflict_stops():
    add_rule(kind="keyword", value="打车", debit="管理费用-差旅费", priority=10)
    add_rule(kind="counterparty", value="滴滴出行", debit="管理费用-交通费", priority=20)
    result = classify(transaction(counterparty="滴滴出行", summary="打车"))
    assert result.debit_account == "管理费用-交通费"
    assert result.reason == "交易对象精确匹配"

def test_unbalanced_draft_is_rejected():
    with pytest.raises(ValidationError, match="借贷不平"):
        validate_balanced([line(debit="10.00"), line(credit="9.99")])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_classifier`
Expected: FAIL，提示分类服务不存在。

- [ ] **Step 3: 实现确定性分类和草稿生成**

```python
RULE_ORDER = {"counterparty": 3, "keyword": 2, "confirmed_history": 1}

@dataclass(frozen=True)
class ClassificationResult:
    status: Literal["matched", "unmatched", "conflict"]
    debit_account: str | None
    credit_account: str | None
    rule_name: str | None
    reason: str
```

同最高优先级出现不同科目时返回 `conflict`。普通支出、收入、转账、退款和手续费各使用独立的小函数生成行；所有函数最终调用 `validate_balanced`。未匹配或冲突不得创建可审核草稿。

- [ ] **Step 4: 运行分类和草稿测试**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_classifier --module internal_ledger.tests.test_drafts`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger
git commit -m "feat: classify transactions and build balanced drafts"
```

### Task 6: 构建工作台、导入预览与审核中心

**Files:**
- Create: `internal_ledger/public/frontend/src/pages/Dashboard.vue`
- Create: `internal_ledger/public/frontend/src/pages/ImportPreview.vue`
- Create: `internal_ledger/public/frontend/src/pages/ReviewCenter.vue`
- Create: `internal_ledger/public/frontend/src/components/DraftLines.vue`
- Create: `internal_ledger/api/review_api.py`
- Create: `internal_ledger/tests/test_review_api.py`

**Interfaces:**
- Consumes: `preview_import`、`confirm_import`、`classify`、`create_draft`。
- Produces: `get_dashboard_counts()`、`get_review_item(name)`、`update_draft(name, lines, summary, posting_date)`、`approve_draft(name)`、`reject_draft(name, reason)`。

- [ ] **Step 1: 写审核状态转换 API 测试**

```python
def test_only_draft_can_be_approved():
    draft = make_draft(status="Draft")
    assert approve_draft(draft.name).status == "Approved"
    with pytest.raises(ValidationError, match="当前状态不可审核"):
        approve_draft(draft.name)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_review_api`
Expected: FAIL，提示审核 API 不存在。

- [ ] **Step 3: 实现最小审核 API 与三页 UI**

```python
ALLOWED_TRANSITIONS = {
    "Draft": {"Approved", "Rejected"},
    "Rejected": {"Draft"},
    "Approved": {"Posted"},
}

def assert_transition(current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        frappe.throw("当前状态不可执行该操作")
```

页面遵循已批准线框图：左侧导航、顶部月度摘要、异常优先列表、原流水与借贷科目同屏。批量审核只允许处理所有校验均通过的选中项；失败项逐条显示原因。

- [ ] **Step 4: 运行 API 测试并构建前端**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_review_api`
Expected: PASS。

Run: `bench build --app internal_ledger`
Expected: 构建成功，无缺失模块或模板错误。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger
git commit -m "feat: add import and review workbench"
```

### Task 7: 实现正式过账、幂等与冲销

**Files:**
- Create: `internal_ledger/accounting/posting.py`
- Create: `internal_ledger/accounting/reversal.py`
- Create: `internal_ledger/api/posting_api.py`
- Create: `internal_ledger/internal_ledger/doctype/posting_link/*`
- Create: `internal_ledger/tests/test_posting.py`

**Interfaces:**
- Consumes: 状态为 `Approved` 的 `Voucher Draft`。
- Produces: `post_draft(draft_name: str) -> str`，返回 Journal Entry 名称；`reverse_posting(posting_link: str, reason: str) -> str`，返回冲销 Journal Entry 名称。

- [ ] **Step 1: 写幂等、回滚和冲销测试**

```python
def test_posting_is_idempotent_and_reversal_links_original():
    draft = approved_draft()
    journal = post_draft(draft.name)
    assert post_draft(draft.name) == journal
    reversal = reverse_posting(link_for(draft.name), "科目选择错误")
    assert frappe.db.get_value("Journal Entry", reversal, "reversal_of") == journal
```

- [ ] **Step 2: 运行测试确认失败**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_posting`
Expected: FAIL，提示 `post_draft` 不存在。

- [ ] **Step 3: 用 ERPNext 文档接口实现过账**

```python
def post_draft(draft_name: str) -> str:
    existing = frappe.db.get_value("Posting Link", {"voucher_draft": draft_name, "status": "Posted"}, "journal_entry")
    if existing:
        return existing
    draft = load_approved_draft_for_update(draft_name)
    journal = frappe.get_doc(to_journal_entry(draft))
    journal.insert()
    journal.submit()
    create_posting_link(draft, journal)
    draft.db_set("status", "Posted")
    return journal.name
```

整个函数在单一数据库事务内运行；Journal Entry 插入、提交、Posting Link 和状态更新任一步失败都回滚。冲销复制原分录并交换借贷，保存原凭证链接和必填原因。

- [ ] **Step 4: 运行过账测试**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_posting`
Expected: PASS，包括模拟 Journal Entry 提交失败时不存在 Posting Link 且草稿仍为 Approved。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger
git commit -m "feat: post and reverse journal entries safely"
```

### Task 8: 锁定只读权限并补齐审计与端到端测试

**Files:**
- Create: `internal_ledger/permissions.py`
- Create: `internal_ledger/internal_ledger/doctype/internal_ledger_audit_event/*`
- Create: `internal_ledger/tests/test_permissions.py`
- Create: `internal_ledger/tests/test_end_to_end.py`
- Modify: `internal_ledger/hooks.py`
- Modify: `internal_ledger/api/*.py`

**Interfaces:**
- Consumes: 所有白名单 API 与领域服务。
- Produces: `require_ledger_admin() -> None`、`record_audit(action: str, reference_doctype: str, reference_name: str, result: str) -> None`。

- [ ] **Step 1: 写查看者写入拒绝测试**

```python
@pytest.mark.parametrize("operation", [confirm_import, approve_draft, post_draft, reverse_posting])
def test_viewer_cannot_write(operation, viewer_user):
    frappe.set_user(viewer_user)
    with pytest.raises(frappe.PermissionError):
        operation(valid_argument_for(operation))
```

- [ ] **Step 2: 运行测试确认至少一个接口越权失败**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_permissions`
Expected: FAIL，证明尚有写接口未执行管理员校验。

- [ ] **Step 3: 在每个写入口增加服务端权限与审计**

```python
def require_ledger_admin() -> None:
    if "账套管理员" not in frappe.get_roles():
        raise frappe.PermissionError("需要账套管理员权限")

def record_audit(action: str, reference_doctype: str, reference_name: str, result: str) -> None:
    frappe.get_doc({
        "doctype": "Internal Ledger Audit Event",
        "action": action,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "result": result,
        "actor": frappe.session.user,
    }).insert(ignore_permissions=True)
```

DocType 权限中查看者只拥有 `read` 和 `report`；原始附件字段使用更高 permission level，不授予查看者。审计事件只允许系统创建，任何业务用户不能修改或删除。

- [ ] **Step 4: 运行权限和完整链路测试**

Run: `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_permissions --module internal_ledger.tests.test_end_to_end`
Expected: PASS；端到端断言导入三类样本、分类、草稿、审核、过账、报表余额和来源追溯均成功。

- [ ] **Step 5: 运行全部应用测试并提交**

Run: `bench --site test.localhost run-tests --app internal_ledger`
Expected: PASS，无跳过的核心流程测试。

```bash
git add internal_ledger
git commit -m "test: enforce permissions and verify ledger workflow"
```

### Task 9: 生产镜像、备份恢复与上线验收

**Files:**
- Create: `infra/versions.env`
- Create: `infra/compose.yaml`
- Create: `infra/nginx.conf`
- Create: `infra/test-config.ps1`
- Create: `infra/build_image.ps1`
- Create: `infra/backup.ps1`
- Create: `infra/restore-check.ps1`
- Create: `docs/operations/deploy.md`
- Create: `docs/operations/backup-restore.md`

**Interfaces:**
- Consumes: 当前仓库 Git remote、当前提交 SHA、官方 `frappe_docker` layered 构建方式。
- Produces: 固定版本镜像 `internal-ledger:<git-sha>`；`backup.ps1` 输出加密归档；`restore-check.ps1` 在隔离 Compose 项目中验证恢复。

- [ ] **Step 1: 写部署配置静态测试**

```powershell
$compose = Get-Content -Raw infra/compose.yaml
if ($compose -match 'ports:\s*\n\s*-\s*"(3306|6379)') { throw '数据库或 Redis 不得暴露公网端口' }
$versions = Get-Content -Raw infra/versions.env
if ($versions -notmatch 'ERPNEXT_VERSION=v16\.52\.1') { throw 'ERPNext 版本未固定' }
```

- [ ] **Step 2: 运行静态测试确认失败**

Run: `pwsh -File infra/test-config.ps1`
Expected: FAIL，因为生产配置尚不存在。

- [ ] **Step 3: 创建固定版本生产配置**

```dotenv
ERPNEXT_VERSION=v16.52.1
FRAPPE_REF=v16.52.0
DB_IMAGE=mariadb:11.8
REDIS_IMAGE=redis:8.2-alpine
```

`build_image.ps1` 必须运行 `git remote get-url origin` 和 `git rev-parse HEAD`；没有远程仓库或工作区不干净时立即失败。脚本把 `FRAPPE_REF=v16.52.0` 传给官方镜像构建的 `FRAPPE_BRANCH` 参数，并使用 BuildKit secret 生成临时 `apps.json`，其中 ERPNext 固定为 v16.52.1、当前应用固定为当前仓库提交；不得把凭据写入镜像层或日志。`compose.yaml` 只发布 80/443，其他服务使用内部网络和命名卷。

- [ ] **Step 4: 实现备份与隔离恢复检查**

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
docker compose --env-file infra/versions.env exec -T backend bench --site ledger.localhost backup --with-files --compress
if ($LASTEXITCODE -ne 0) { throw '备份失败' }
Write-Host "Backup completed: $stamp"
```

备份脚本将数据库与文件归档加密后复制到独立目标；日志只输出时间戳和归档校验值。恢复脚本使用独立 Compose project name，恢复后验证 `tabCompany`、`tabJournal Entry` 和站点附件数量，再销毁隔离容器但保留验证报告。

- [ ] **Step 5: 构建镜像并运行配置检查**

Run: `pwsh -File infra/test-config.ps1`
Expected: PASS。

Run: `pwsh -File infra/build_image.ps1`
Expected: 输出以当前 Git SHA 标记的镜像，并打印应用版本清单；不得打印 Git 凭据。

- [ ] **Step 6: 在临时站点执行上线验收**

Run: `docker compose --env-file infra/versions.env -f infra/compose.yaml up -d`
Expected: 所有必需服务健康，只有 HTTP/HTTPS 对宿主机发布。

Run: `docker compose --env-file infra/versions.env -f infra/compose.yaml exec backend bench --site ledger.localhost run-tests --app internal_ledger`
Expected: PASS。

Run: `pwsh -File infra/backup.ps1 && pwsh -File infra/restore-check.ps1`
Expected: 备份成功，隔离恢复检查 PASS。

- [ ] **Step 7: 提交**

```bash
git add infra docs/operations
git commit -m "ops: add reproducible deployment and recovery"
```

### Task 10: 最终验收与版本标记

**Files:**
- Create: `docs/acceptance/2026-10-28-mvp-checklist.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 1–9 的代码、测试、镜像和运维文档。
- Produces: 可复核的验收记录与 Git 标签 `v0.1.0`。

- [ ] **Step 1: 按规格逐项记录验收证据**

验收记录必须包含每项命令、退出码、脱敏样本名称、生成的 Import Batch、Journal Entry、报表校验值、查看者权限拒绝结果和恢复报告路径；任何失败项都不得标记完成。

- [ ] **Step 2: 运行最终自动化验证**

Run: `bench --site test.localhost run-tests --app internal_ledger`
Expected: PASS。

Run: `pwsh -File infra/test-config.ps1`
Expected: PASS。

Run: `git status --short`
Expected: 仅验收记录和 README 为预期未提交修改。

- [ ] **Step 3: 人工检查关键页面**

在桌面与手机宽度检查工作台、导入预览、待分类、审核中心、凭证追溯和基础报表；确认没有水平溢出、不可见按钮或查看者写入口。把结果和截图文件名写入验收记录。

- [ ] **Step 4: 提交验收记录并标记版本**

```bash
git add README.md docs/acceptance/2026-10-28-mvp-checklist.md
git commit -m "docs: record internal ledger mvp acceptance"
git tag -a v0.1.0 -m "Internal ledger MVP"
```

- [ ] **Step 5: 验证最终仓库状态**

Run: `git status --short && git log -5 --oneline && git tag --list v0.1.0`
Expected: 工作区为空，显示最终五个提交和标签 `v0.1.0`。
