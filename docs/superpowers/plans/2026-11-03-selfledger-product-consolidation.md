# SelfLedger Product Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有导入、去重、分类、制证、审核过账和 ERPNext 经营模块收敛为一个中文、低操作量的 SelfLedger 产品闭环。

**Architecture:** 复用 Frappe/ERPNext、现有 Vue 工作台和确定性分类器；在服务层增加一个幂等的流水自动处理入口，由导入确认和重复释放共同调用。前端继续使用现有组件，但把用户流程改为“导入结果汇总 + 单一待办中心 + 中文快捷分类”，自动化止于凭证草稿。

**Tech Stack:** Frappe/ERPNext v16、Python 3.14、MariaDB、Vue 3、Frappe esbuild、Docker Compose、Edge/Playwright 验收。

**Spec:** `docs/superpowers/specs/2026-11-03-selfledger-product-consolidation-design.md`

## Global Constraints

- 只有一个登录入口、一个 SelfLedger 工作台和一套导航。
- 自动化止于凭证草稿；正式审核与过账必须由管理员确认。
- ERPNext 英文默认科目保留在数据库中，但不得出现在内账默认分类选择器。
- 现有 WireGuard 私有访问方式保持不变，不开放公网。
- 不增加多租户、计费、移动 App、外部付费 AI 或第二套前端框架。
- 每个行为变更严格执行 RED → GREEN；每个任务独立提交。
- 不在生产账套运行会写数据的自动化测试；生产只运行只读健康检查和真实浏览器验收。

## File Map

- `internal_ledger/accounting/batch_workflow.py`：唯一的批量/单笔自动分类与制证服务。
- `internal_ledger/api/import_api.py`：导入确认后调用自动处理并返回汇总。
- `internal_ledger/api/duplicate_api.py`：重复释放后调用同一自动处理服务。
- `internal_ledger/api/classification_api.py`：向内账前端提供受控中文科目目录。
- `internal_ledger/chart_templates/small_business_chart_cn.json`：中文科目白名单的唯一来源。
- `internal_ledger/public/frontend/src/labels.js`：数据库稳定枚举到中文显示文本的纯映射。
- `internal_ledger/public/frontend/src/pages/ImportPreview.vue`：导入结果汇总与下一步入口。
- `internal_ledger/public/frontend/src/pages/DuplicateReview.vue`：重复释放后的自动续跑结果。
- `internal_ledger/public/frontend/src/components/ManualClassificationForm.vue`：中文快捷分类与更多科目。
- `internal_ledger/public/frontend/src/pages/ReviewCenter.vue`：统一待办中心和安全批量审核。
- `internal_ledger/public/frontend/src/pages/Dashboard.vue`、`App.vue`：统一首页、导航和任务入口。
- `internal_ledger/internal_ledger/workspace/selfledger/selfledger.json`：Frappe Workspace 快捷入口。
- `internal_ledger/tests/`：服务、权限、接口、前端契约和端到端回归测试。

---

### Task 1: 幂等自动分类与制证服务

**Files:**
- Modify: `internal_ledger/accounting/batch_workflow.py`
- Test: `internal_ledger/tests/test_batch_workflow.py`

**Interfaces:**
- Consumes: `classify(tx_name: str) -> ClassificationResult`、`create_draft(tx_name, result) -> str`。
- Produces: `process_transactions(tx_names: list[str]) -> list[dict]`，每项固定包含 `name`、`status`、`draft`、`message`；`status` 为 `created|manual|duplicate|conflict|skipped|failed`。
- Keeps: `prepare_matched_transactions(tx_names)` 作为兼容包装，直接调用 `process_transactions`。

- [ ] **Step 1: 写失败测试，定义稳定结果与幂等行为**

在 `test_batch_workflow.py` 增加：

```python
def test_process_transactions_reports_actionable_states_and_is_idempotent():
    from internal_ledger.accounting.batch_workflow import process_transactions
    add_rule()
    matched = transaction()
    unmatched = transaction(counterparty="无匹配商户")
    conflict = transaction(counterparty="冲突商户")
    add_rule(value="冲突商户")
    add_rule(value="冲突商户", debit_account=account("560202"))
    _, duplicate = duplicate_pair()

    outcomes = process_transactions([matched, unmatched, conflict, duplicate.name])
    assert [row["status"] for row in outcomes] == ["created", "manual", "conflict", "duplicate"]
    assert outcomes[0]["draft"]
    assert all(set(row) == {"name", "status", "draft", "message"} for row in outcomes)

    repeated = process_transactions([matched])
    assert repeated[0]["status"] == "skipped"
    assert repeated[0]["draft"] == outcomes[0]["draft"]
    assert frappe.db.count("Voucher Draft", {"imported_transaction": matched}) == 1
```

- [ ] **Step 2: 运行测试并确认因接口/状态尚不存在而失败**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_batch_workflow
```

Expected: FAIL，提示无法导入 `process_transactions` 或现有状态仍为 `skipped`。

- [ ] **Step 3: 最小实现统一处理入口**

在 `batch_workflow.py` 中保留现有权限检查、逐行 savepoint、异常脱敏和审计，并用以下分支替换当前从 `if tx.status != "Imported"` 开始的决策块：

```python
if tx.status == "Duplicate" or tx.duplicate_review_status in {"Pending", "Confirmed"}:
    status, message = "duplicate", "疑似或已确认重复，未进入分类制证"
elif tx.status != "Imported":
    status, message = "skipped", "流水已处理，无需重复生成"
else:
    draft = frappe.db.get_value(
        "Voucher Draft", {"imported_transaction": name}, "name", for_update=True
    )
    if draft:
        status, message = "skipped", "已有凭证草稿，无需重复生成"
    else:
        result = classify(name)
        if result.status == "matched":
            draft = create_draft(name, result)
            status, message = "created", "已自动分类并生成待审核凭证草稿"
        elif result.status == "conflict":
            status, message = "conflict", "分类规则冲突，请人工确认"
        else:
            status, message = "manual", "未匹配分类规则，请人工分类"
outcomes.append({
    "name": outcome_name, "status": status, "draft": draft, "message": message,
})


def prepare_matched_transactions(tx_names: list[str]) -> list[dict]:
    return process_transactions(tx_names)
```

不得复制 `classify` 或 `create_draft` 的业务规则，不得把异常文本返回给客户端。

- [ ] **Step 4: 运行批处理、分类、权限回归测试**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_batch_workflow \
  --module internal_ledger.tests.test_classifier \
  --module internal_ledger.tests.test_permissions
```

Expected: PASS，且既有逐行回滚、死锁传播、查看者禁止写入测试保持通过。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger/accounting/batch_workflow.py internal_ledger/tests/test_batch_workflow.py
git commit -m "feat: automate transaction classification drafts"
```

---

### Task 2: 导入确认与重复释放自动续跑

**Files:**
- Modify: `internal_ledger/api/import_api.py`
- Modify: `internal_ledger/api/duplicate_api.py`
- Test: `internal_ledger/tests/test_importers.py`
- Test: `internal_ledger/tests/test_duplicate_review.py`
- Test: `internal_ledger/tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `process_transactions(tx_names) -> list[dict]` from Task 1。
- Produces: `summarize_processing(outcomes: list[dict]) -> dict`，包含 `total`、`created`、`duplicate`、`manual`、`conflict`、`failed`。
- Changes: `confirm_import(source, content_base64, payment_account, expected_file_hash, filename=None, corrections_json=None)` 返回 `batch_name`、`detected_source`、`processing`、`summary`；`resolve_duplicate(name, decision, note)` 返回 `transaction`、`processing`。

- [ ] **Step 1: 写失败测试，锁定导入自动制证和重复释放续跑**

在相应测试中加入：

```python
def test_confirm_import_auto_processes_normal_rows_and_returns_summary():
    add_rule(kind="keyword", value="办公")
    preview = preview_import("bank", encoded, account("1001"))
    result = confirm_import("bank", encoded, account("1001"), preview["file_hash"])
    assert result["summary"]["total"] == len(result["processing"])
    assert result["summary"]["created"] >= 1
    created = [row for row in result["processing"] if row["status"] == "created"]
    assert all(frappe.db.exists("Voucher Draft", row["draft"]) for row in created)


def test_release_duplicate_immediately_reprocesses_it():
    from internal_ledger.api.duplicate_api import resolve_duplicate

    add_rule()
    _, duplicate = duplicate_pair()
    result = resolve_duplicate(duplicate.name, "release", "两笔真实交易")
    assert result["transaction"]["duplicate_review_status"] == "Released"
    assert result["processing"]["status"] == "created"
    assert result["processing"]["draft"]
```

- [ ] **Step 2: 运行测试并确认旧接口只返回批次/流水而失败**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_importers \
  --module internal_ledger.tests.test_duplicate_review
```

Expected: FAIL，缺少 `summary`、`processing` 或释放后没有草稿。

- [ ] **Step 3: 在 API 边界调用统一服务并汇总**

实现纯汇总函数：

```python
PROCESSING_STATUSES = ("created", "duplicate", "manual", "conflict", "failed")


def summarize_processing(outcomes):
    summary = {"total": len(outcomes), **{status: 0 for status in PROCESSING_STATUSES}}
    for row in outcomes:
        if row["status"] in PROCESSING_STATUSES:
            summary[row["status"]] += 1
    return summary
```

`confirm_import` 在附件和批次保存完成后查询该批次流水编号并调用 `process_transactions`。`resolve_duplicate` 仅在 `decision == "release"` 时续跑；确认重复返回 `duplicate` 结果。保留 `duplicate_review.resolve` 的单一职责，不把分类逻辑塞入模型函数。

- [ ] **Step 4: 验证局部失败、幂等、权限和端到端行为**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_importers \
  --module internal_ledger.tests.test_duplicate_review \
  --module internal_ledger.tests.test_end_to_end \
  --module internal_ledger.tests.test_permissions
```

Expected: PASS；重复候选不生成草稿，释放后生成或进入人工待办，查看者仍被拒绝。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger/api/import_api.py internal_ledger/api/duplicate_api.py \
  internal_ledger/tests/test_importers.py \
  internal_ledger/tests/test_duplicate_review.py internal_ledger/tests/test_end_to_end.py
git commit -m "feat: continue ledger workflow after imports"
```

---

### Task 3: 中文内账科目目录与快捷分类

**Files:**
- Modify: `internal_ledger/api/classification_api.py`
- Modify: `internal_ledger/public/frontend/src/components/ManualClassificationForm.vue`
- Create: `internal_ledger/public/frontend/src/labels.js`
- Test: `internal_ledger/tests/test_manual_classification.py`
- Test: `internal_ledger/tests/test_unified_navigation.py`

**Interfaces:**
- Consumes: `load_chart_fixture() -> dict` from `internal_ledger.install`。
- Produces: `list_leaf_accounts()` rows with exactly `name`、`account_number`、`account_name`、`root_type`、`root_label`。
- Produces: `ACCOUNT_PRESETS` keyed by `office|travel|purchase|income|fee|capital|transfer|other`，前端按科目编码解析实际 DocName。

- [ ] **Step 1: 写失败测试，证明英文 ERP 科目被排除**

替换旧“返回所有末级科目”断言：

```python
def test_leaf_accounts_only_expose_numbered_chinese_chart_accounts():
    from internal_ledger.api.classification_api import list_leaf_accounts
    english = frappe.get_doc({"doctype": "Account", "account_name": "Travel Expenses",
        "company": frappe.flags.classification_company, "root_type": "Expense",
        "report_type": "Profit and Loss", "is_group": 0}).insert()
    rows = list_leaf_accounts()
    allowed_numbers = {"1001", "100201", "560201", "560202", "5001", "5603", "3001"}
    assert english.name not in {row["name"] for row in rows}
    assert all(row["account_number"] and row["account_name"] for row in rows)
    assert allowed_numbers <= {row["account_number"] for row in rows}
    assert {row["root_label"] for row in rows} <= {"资产", "负债", "所有者权益", "收入", "成本费用"}
```

前端契约测试必须断言快捷分类中文标签和 `labels.js` 中的方向/来源/状态映射存在。

- [ ] **Step 2: 运行定向测试并确认英文科目仍被返回而失败**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_manual_classification \
  --module internal_ledger.tests.test_unified_navigation
```

Expected: FAIL，`Travel Expenses` 仍在结果或缺少 `root_label`。

- [ ] **Step 3: 使用中文模板建立白名单，不修改 ERPNext 原科目**

从 `load_chart_fixture()` 提取 `is_group != 1` 的科目编码；仅查询当前公司、启用、末级、编码在白名单内的 Account。映射根类型：

```python
ROOT_LABELS = {
    "Asset": "资产", "Liability": "负债", "Equity": "所有者权益",
    "Income": "收入", "Expense": "成本费用",
}
```

禁止重命名或删除生产中的英文科目。

- [ ] **Step 4: 实现中文快捷分类和“更多中文科目”**

在 `labels.js` 导出稳定映射：

```javascript
export const directions = {expense: '支出', income: '收入'};
export const sources = {bank: '银行卡', wechat: '微信支付', alipay: '支付宝'};
export const transactionTypes = {
  expense: '费用支出', income: '经营收入', transfer: '账户转账',
  refund: '退款', fee: '手续费', capital: '股东投入',
};
```

`ManualClassificationForm.vue` 提供八个中文快捷分类；点击后按编码填充建议科目，资金科目按交易方向填入另一侧。只有选择“其他”或主动展开时显示完整中文科目选择器。选项格式固定为 `编码 · 名称`。

- [ ] **Step 5: 运行测试和前端构建**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_manual_classification \
  --module internal_ledger.tests.test_unified_navigation
bench build --app internal_ledger
```

Expected: PASS；构建产出 `ledger_workbench.bundle.*.js`，无 Vue 编译错误。

- [ ] **Step 6: 提交**

```bash
git add internal_ledger/api/classification_api.py internal_ledger/public/frontend/src/labels.js \
  internal_ledger/public/frontend/src/components/ManualClassificationForm.vue \
  internal_ledger/tests/test_manual_classification.py internal_ledger/tests/test_unified_navigation.py
git commit -m "feat: add Chinese ledger classification catalog"
```

---

### Task 4: 导入结果、重复续跑和统一待办中心

**Files:**
- Modify: `internal_ledger/public/frontend/src/pages/ImportPreview.vue`
- Modify: `internal_ledger/public/frontend/src/pages/DuplicateReview.vue`
- Modify: `internal_ledger/public/frontend/src/pages/ReviewCenter.vue`
- Modify: `internal_ledger/public/frontend/src/components/DraftLines.vue`
- Modify: `internal_ledger/public/frontend/src/App.vue`
- Test: `internal_ledger/tests/test_unified_navigation.py`

**Interfaces:**
- Consumes: Task 2 的 `confirm_import.message.summary`、`confirm_import.message.processing` 和 `resolve_duplicate.message.processing`。
- Consumes: Task 3 的 `directions`、`sources`、`transactionTypes` 中文映射。
- Produces: 页面契约文本“自动生成凭证草稿”“处理剩余事项”“待办中心”“确认审核并过账”。

- [ ] **Step 1: 写失败的前端契约测试**

在 `test_unified_navigation.py` 增加：

```python
def test_import_duplicate_and_task_center_expose_one_continuous_workflow():
    root = ROOT / "public/frontend/src"
    imported = (root / "pages/ImportPreview.vue").read_text()
    duplicate = (root / "pages/DuplicateReview.vue").read_text()
    review = (root / "pages/ReviewCenter.vue").read_text()
    app = (root / "App.vue").read_text()
    for text in ("自动生成凭证草稿", "疑似重复", "待人工分类", "处理剩余事项"):
        assert text in imported
    assert "processing" in duplicate and "已自动分类并生成凭证草稿" in duplicate
    assert "待办中心" in app
    assert "directions" in review and "sources" in review
    assert "确认审核并过账" in review
```

- [ ] **Step 2: 运行测试并确认页面尚未消费新接口而失败**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_unified_navigation
```

Expected: FAIL，缺少汇总文案、续跑结果或待办中心标签。

- [ ] **Step 3: 改造导入结果卡和重复复核结果**

`ImportPreview.vue` 保存完整返回值并显示五项汇总；按钮链接到：

```text
查看自动生成的凭证 -> /app/ledger-workbench/review/待审核
处理剩余事项 -> 优先疑似重复，其次待分类
```

`DuplicateReview.vue` 根据 `processing.status` 显示唯一下一步：`created` 查看草稿，`manual|conflict` 前往待人工分类，`failed` 显示可重试说明，`duplicate` 表示已忽略。

- [ ] **Step 4: 收敛为统一待办中心并全部中文显示**

`App.vue` 顶部导航保留“业务总览、文件智能导入、待办中心、企业成员”；待办中心继续复用 `ReviewCenter` 的五个状态筛选。重复详情仍使用现有 `DuplicateReview`，但只能由待办项进入，不再要求用户理解第二套工作流。

`ReviewCenter.vue` 和 `DraftLines.vue` 使用 `labels.js` 显示中文方向、来源、业务类型和分类原因。待审核筛选激活时，自动选中 `postEligible` 且无异常的草稿，但仍必须经过现有确认摘要与“确认审核并过账”。切换筛选、刷新或数据变化时清除不再有效的选择。

- [ ] **Step 5: 运行前端契约、权限和批量过账测试并构建**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_unified_navigation \
  --module internal_ledger.tests.test_review_api \
  --module internal_ledger.tests.test_batch_workflow
bench build --app internal_ledger
```

Expected: PASS；自动选择不绕过确认，查看者页面不出现写操作。

- [ ] **Step 6: 提交**

```bash
git add internal_ledger/public/frontend/src/pages/ImportPreview.vue \
  internal_ledger/public/frontend/src/pages/DuplicateReview.vue \
  internal_ledger/public/frontend/src/pages/ReviewCenter.vue \
  internal_ledger/public/frontend/src/components/DraftLines.vue \
  internal_ledger/public/frontend/src/App.vue internal_ledger/tests/test_unified_navigation.py
git commit -m "feat: simplify the continuous ledger workflow"
```

---

### Task 5: 统一首页、Workspace 与完整回归

**Files:**
- Modify: `internal_ledger/public/frontend/src/pages/Dashboard.vue`
- Modify: `internal_ledger/public/frontend/src/App.vue`
- Modify: `internal_ledger/api/review_api.py`
- Modify: `internal_ledger/internal_ledger/workspace/selfledger/selfledger.json`
- Modify: `internal_ledger/tests/test_unified_navigation.py`
- Modify: `internal_ledger/tests/test_review_api.py`
- Modify: `internal_ledger/tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `get_dashboard_counts()` 和 Task 2 的处理状态。
- Produces: 唯一首页入口；智能内账待办卡；销售、采购、库存、制造 ERP 分支。

- [ ] **Step 1: 写失败测试覆盖统一入口和完整业务闭环**

更新 Workspace 期望为：业务总览、文件智能导入、待办中心、账簿与报表、企业成员、销售、采购、库存、制造。`get_dashboard_counts()` 新增 `latest_batch`，根据最近一个 Import Batch 的流水状态与草稿状态返回批次编号、总数、自动草稿数、疑似重复数和待人工分类数。端到端测试新增：规则命中导入自动生成草稿，人工学习规则后下一笔自动生成草稿，重复释放后自动续跑，最终仍通过 `approve_and_post` 才生成已提交 Journal Entry。

核心断言：

```python
from internal_ledger.api.posting_api import approve_and_post

assert frappe.db.count("Voucher Draft", {"imported_transaction": learned_tx}) == 1
assert frappe.db.count("Voucher Draft", {"imported_transaction": duplicate.name}) == 1
assert not frappe.db.exists("Journal Entry", {"user_remark": ["like", "%自动过账%"]})
result = approve_and_post(frappe.as_json([draft_name]))
assert result[0]["journal_entry"]
assert frappe.get_doc("Journal Entry", result[0]["journal_entry"]).docstatus == 1
```

- [ ] **Step 2: 运行测试并确认旧 Workspace/首页结构失败**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger \
  --module internal_ledger.tests.test_unified_navigation \
  --module internal_ledger.tests.test_review_api \
  --module internal_ledger.tests.test_end_to_end
```

Expected: FAIL，Workspace 快捷项仍为旧结构或端到端导入后缺少自动草稿。

- [ ] **Step 3: 最小更新首页与 Workspace**

首页顺序固定为：主导入按钮、本月收支、四类待办、最近处理提示、ERP 经营入口。删除重复解释相同流程的卡片和技术性文案。Workspace 只保留对应九个入口，不创建第二个 Workspace。

- [ ] **Step 4: 运行完整应用测试和静态部署检查**

Run:

```bash
bench --site test.localhost run-tests --app internal_ledger
bench build --app internal_ledger
pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
git diff --check
```

Expected: 全部 PASS；前端构建无错误；基础设施静态检查通过；无空白或行尾错误。

- [ ] **Step 5: 提交**

```bash
git add internal_ledger/public/frontend/src/pages/Dashboard.vue \
  internal_ledger/public/frontend/src/App.vue \
  internal_ledger/api/review_api.py \
  internal_ledger/internal_ledger/workspace/selfledger/selfledger.json \
  internal_ledger/tests/test_unified_navigation.py internal_ledger/tests/test_review_api.py \
  internal_ledger/tests/test_end_to_end.py
git commit -m "feat: finish the unified SelfLedger experience"
```

---

### Task 6: 真实浏览器验收与生产部署

**Files:**
- Create temporarily, then remove: `.browser-verify/selfledger-e2e.js`
- Use: `infra/Dockerfile.app-layer`
- Use: `docs/operations/deploy.md`
- Use: `docs/operations/backup-restore.md`

**Interfaces:**
- Consumes: Task 1–5 的完整提交和不可变 Git SHA。
- Produces: 浏览器验收截图、生产镜像、健康检查证据和可回滚运行配置。

- [ ] **Step 1: 在隔离测试站点准备不含真实财务数据的验收夹具**

创建一个测试账单，覆盖：规则命中、未匹配、疑似重复各一笔。不得把真实客户流水提交到 Git。确认夹具仅存在于测试站点或临时目录。

- [ ] **Step 2: 编写失败的 Edge/Playwright 验收脚本并在旧版本运行**

脚本从环境变量读取 `SELFLEDGER_BASE_URL`、`SELFLEDGER_USER`、`SELFLEDGER_PASSWORD` 和 `SELFLEDGER_FIXTURE`，不得打印密码。英语科目检查和主断言如下：

```javascript
async function visibleEnglishAccountOptions(page) {
  const labels = await page.locator('#manual-debit option, #manual-credit option').allTextContents();
  return labels.filter(label => /\b(Expenses|Income|Cash|Sales|Stock|Capital)\b/i.test(label));
}

await page.goto(`${process.env.SELFLEDGER_BASE_URL}/desk`);
await page.waitForURL('**/desk/ledger-workbench/dashboard');
await page.getByText('开始导入并自动识别', {exact: true}).click();
await page.locator('#ledger-file').setInputFiles(process.env.SELFLEDGER_FIXTURE);
await page.getByRole('button', {name: '识别并预览'}).click();
await page.getByRole('button', {name: '确认导入'}).click();
await page.getByText('自动生成凭证草稿', {exact: false}).waitFor();
await page.getByText('处理剩余事项', {exact: true}).click();
await page.getByText('待办中心', {exact: true}).waitFor();
if ((await visibleEnglishAccountOptions(page)).length) {
  throw new Error('人工分类仍显示英文 ERPNext 科目');
}
```

Expected on old production image: FAIL，旧界面没有新汇总、待办中心或英文科目过滤。

- [ ] **Step 3: 在候选镜像和隔离测试站点运行完整浏览器流程**

完成“登录 → 导入 → 自动汇总 → 重复释放 → 人工分类 → 查看借贷凭证 → 批量确认过账 → 打开正式凭证”，并验证销售、采购、库存、制造入口。保存全页截图到受控验收目录；截图不得包含密码、密钥或真实客户流水。

- [ ] **Step 4: 创建并验证生产备份**

按 `docs/operations/backup-restore.md` 执行现有备份命令，确认数据库、站点文件和私有文件均存在且加密上传成功。记录备份标识，不在聊天或日志中输出密钥。

- [ ] **Step 5: 构建并部署不可变候选镜像**

使用最终 40 位提交构建 `internal-ledger:<sha>`；OCR 未变化时只为已验证 OCR 镜像创建同 SHA 标签。验证镜像 revision label、Desk bundle 和 `ledger_workbench.bundle.js` 后，更新 `/etc/selfquanta/ledger/runtime.env` 的两个不可变镜像标签并重启 systemd 服务。

- [ ] **Step 6: 生产只读健康检查和真实浏览器验收**

必须确认：

```text
Git HEAD == runtime image SHA == image revision label
HTTPS /api/method/ping == 200
backend/frontend/gateway/ocr/db/redis == healthy
selfquanta-ledger-backup.timer == active
/desk 自动进入统一首页
中文科目下拉不出现英文 ERPNext 科目
导入、重复复核、自动分类、凭证草稿和人工过账闭环可见
```

生产浏览器验收只使用专用测试数据，并在验收后按审计要求清理或冲销；不得对真实流水重复过账。

- [ ] **Step 7: 推送最终提交并记录验收证据**

```bash
git push origin main
git status --short
git rev-parse HEAD
```

Expected: 推送成功，工作树干净，生产运行 SHA 与最终提交一致。
