# SelfLedger Public ERP MVP Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 8 周内把现有单公司智能内账升级为可供 3–5 家企业独立使用的邀请制 SelfLedger ERP 公测版，覆盖财务、报税格式材料、销售、采购、库存、费用、CRM、项目、人事工资和可关闭 AI 助手。

**Architecture:** 保留 Frappe/ERPNext 作为唯一业务与会计数据源，保留 `internal_ledger` 作为中文任务层和智能导入层；每客户使用独立 Frappe Site、数据库、文件、密钥和备份，在试点期共享同一计算服务器。所有标准业务单据直接复用 ERPNext/HRMS，SelfLedger 只增加统一导航、任务向导、连接器、月结与报税材料编排、权限模板和运维自动化。

**Tech Stack:** Frappe v16.52、ERPNext v16.52、HRMS 对应 v16、Python 3.14、MariaDB 11.8、Redis 8.2、Vue 3、Frappe esbuild、Docker Compose、PowerShell、Nginx、PaddleOCR 服务。

**Spec:** `docs/superpowers/specs/2026-11-06-selfledger-public-erp-platform-design.md`

## Global Constraints

- 不复制 ERPNext 的 Customer、Supplier、Item、Sales Invoice、Purchase Invoice、Stock Entry、Project、Employee、Payroll Entry 或 Journal Entry 数据模型。
- 每个客户独立 Site、数据库、私有文件、站点密钥和备份；任何查询、文件和后台任务都不得跨 Site。
- 首版只生成报税格式材料和校验清单，不直接登录或提交税务系统。
- AI 只能解释、建议和生成草稿；不能绕过权限、审批、复式平衡、锁账和审计。
- 现有智能内账主链路、已过账维护、重复检测、成员管理和备份恢复不得回归。
- 每个任务执行 RED → GREEN；一个模块完成后再跑该模块回归，不在每个微小步骤跑全套。
- 生产部署只接受不可变 Git SHA 镜像；测试失败、迁移失败或只读冒烟失败时保持维护模式并回滚。
- 不在 Git、日志、命令参数、文档或测试夹具中写入真实密码、API Key、证书私钥或客户数据。
- 试点期月基础设施支出不超过人民币 500 元，不含模型和第三方接口按量费用。

## Delivery Plans

1. `2026-11-06-selfledger-foundation-and-tenancy.md`：可重复构建、多 Site 开通、角色模板、统一入口和安全基线。
2. `2026-11-06-selfledger-finance-and-tax.md`：智能导入、标准会计、月结、报表和报税格式材料。
3. `2026-11-06-selfledger-business-operations.md`：CRM、销售、采购、库存、费用、项目、人事工资业财联动。
4. `2026-11-06-selfledger-ai-operations-and-pilot.md`：可关闭 AI、监控、备份恢复、公开试点和验收。

## Release Gates

| Gate | Required evidence | Blocking condition |
|---|---|---|
| G0 基线 | 当前内账模块测试、前端契约、基础设施配置检查通过 | 现有主链路未稳定 |
| G1 平台 | 两个测试 Site 数据与文件隔离；角色矩阵通过 | 跨 Site 泄漏或权限越权 |
| G2 财务 | 导入→去重→分类→草稿→审核→过账→月结材料完整 | 借贷不平、重复计账、材料不可追溯 |
| G3 业财 | 销售、采购、库存、费用、项目和工资形成标准会计影响 | 自建平行业务数据或库存负数无提示 |
| G4 运营 | 加密备份、异机恢复、监控、升级与回滚演练通过 | 无恢复证据、密钥泄漏或公网安全门禁失败 |
| G5 试点 | 3–5 家企业逐户验收、责任人签字、遗留项分级 | 严重数据错误或无法导出客户数据 |

## Eight-Week Sequence

| Week | Scope | Exit criteria |
|---|---|---|
| 1 | G0 基线、构建可重复性、HRMS 固定版本 | 干净 SHA 可构建，现有内账测试通过 |
| 2 | 多 Site 开通、角色模板、统一首页与导航 | 第二测试 Site 可独立登录且无数据交叉 |
| 3 | 会计期、月结检查、科目/凭证/账簿校验 | 月结检查能指出阻断项且不自动修改正式账 |
| 4 | 报税格式材料中心、导出、追溯 | 材料包可重建、可下载、来源可追溯 |
| 5 | CRM、销售、采购、库存、费用统一入口 | 标准业务单据与总账联动通过 |
| 6 | 项目、人事工资、角色工作台 | 工资草稿可审核入账，员工隐私权限通过 |
| 7 | AI 助手、监控、备份恢复、公开访问安全 | 关闭 AI 时 ERP 完整运行；恢复演练通过 |
| 8 | 3–5 家邀请试点、修复 P0/P1、发布 | 每户完成验收清单并留存版本和备份证据 |

### Task 1: Freeze and Prove the Existing Baseline

**Files:**
- Modify: `docs/acceptance/2026-11-06-public-erp-baseline.md`
- Verify: `internal_ledger/tests/`
- Verify: `infra/test-config.ps1`

**Interfaces:**
- Produces a baseline record containing Git SHA, pinned versions, test commands, result counts and known non-blocking defects.
- Does not mutate production data.

- [ ] Record `git rev-parse HEAD`, `bench version`, Docker image identifiers and the current production Site name without copying secrets.
- [ ] Run focused baseline suites:

```powershell
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_end_to_end
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_permissions
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_posted_entry_maintenance
node internal_ledger/tests/frontend_review.cjs
pwsh -NoProfile -File infra/test-config.ps1
```

- [ ] Expected: all commands exit `0`; record actual pass counts. Any failure becomes a G0 blocker and is repaired before Phase 1.
- [ ] Run `rg -n "site-admin-password|BEGIN .*PRIVATE KEY|api[_-]?key" . -g '!*.conf' -g '!site-admin-password.txt'` and document only filenames/counts, never secret values.
- [ ] Commit only the evidence document:

```bash
git add docs/acceptance/2026-11-06-public-erp-baseline.md
git commit -m "docs: record public ERP baseline"
```

### Task 2: Execute Phase Plans in Order

**Files:**
- Follow: the four plan files listed under Delivery Plans.

**Interfaces:**
- Each phase consumes the previous phase's release gate evidence.
- Each phase produces code, focused tests, a migration-safe deployment artifact and an acceptance record.

- [ ] Execute Foundation and Tenancy until G1 passes.
- [ ] Execute Finance and Tax until G2 passes.
- [ ] Execute Business Operations until G3 passes.
- [ ] Execute AI, Operations and Pilot until G4 passes.
- [ ] Do not start a later phase to hide a blocker in an earlier phase; split non-blocking polish into the phase debt ledger.
- [ ] After each phase, tag the candidate SHA with an annotated pre-release tag such as `selfledger-mvp-g1-YYYYMMDD`.

### Task 3: Final Integrated Acceptance

**Files:**
- Create: `docs/acceptance/2026-11-06-public-erp-integrated-checklist.md`
- Modify: `docs/operations/deploy.md`
- Modify: `docs/operations/backup-restore.md`

**Interfaces:**
- Consumes anonymized fixtures and dedicated acceptance Sites only.
- Produces a signed checklist for G5 and a release/rollback decision.

- [ ] Provision two acceptance Sites and verify Company, User, File, Journal Entry and Item counts differ without cross-Site visibility.
- [ ] Run one end-to-end scenario: opening setup → customer/product → sales order/invoice/payment → purchase/receipt/invoice/payment → stock movement → expense → payroll draft → intelligent bank import → reconciliation → monthly close → tax material export.
- [ ] Verify every accounting-impacting action has source document, actor, timestamp, company, debit and credit traceability.
- [ ] Verify viewer, salesperson, buyer, stock user, HR user, accountant and owner role boundaries using separate named accounts.
- [ ] Verify customer export includes master data, transactions, attachments and audit logs in documented formats.
- [ ] Run encrypted backup and isolated restore; compare critical row/file counts and open restored reports.
- [ ] Deploy candidate SHA under maintenance mode, migrate one acceptance Site at a time, perform read-only smoke, then enable scheduler and traffic.
- [ ] Record rollback command, previous SHA, selected backup and named operator before public access is enabled.
- [ ] Commit acceptance and operating-document updates:

```bash
git add docs/acceptance/2026-11-06-public-erp-integrated-checklist.md docs/operations/deploy.md docs/operations/backup-restore.md
git commit -m "docs: add public ERP release acceptance"
```

## Definition of Done

- 3–5 家试点企业各自使用独立 Site 和独立备份，管理员不能从一个 Site 读取另一个 Site 数据。
- 小白用户可以从统一中文首页完成核心任务，不需要理解 ERPNext Workspace 结构。
- 流水和文件导入正常时自动去重、分类和生成草稿，异常时进入明确待办，正式过账保持人工批准。
- 标准销售、采购、库存、费用、项目、人事工资单据与会计分录使用同一数据源。
- 月结与报税材料包可重复生成、可追溯、可导出，并明确标记“非税务直接申报”。
- AI 关闭或供应商不可用时，除 AI 建议外的所有 ERP 功能可用。
- 加密备份、异机恢复、升级、回滚、监控和安全门禁均有当期真实证据。
- 没有 P0/P1 缺陷；P2/P3 有责任人、影响说明和处理版本。

