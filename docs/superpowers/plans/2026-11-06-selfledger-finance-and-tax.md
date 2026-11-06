# SelfLedger Finance and Tax Materials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有智能内账主链路上补齐专业会计期间、月结检查、标准账表和可追溯报税格式材料，使小白用户能完成“导入资料到交付会计/税务材料”的完整闭环。

**Architecture:** `Imported Transaction`、`Voucher Draft`、ERPNext `Journal Entry`/`GL Entry` 继续作为唯一流水、草稿和正式账数据；月结服务只读检查标准账并持久化检查批次，报税材料服务从已提交标准单据生成版本化 ZIP/Excel/PDF 索引，不重新计算或复制总账。

**Tech Stack:** Frappe/ERPNext v16、Python、MariaDB、现有 Vue 工作台、Frappe Reports、加密私有 File、OpenPyXL（若 ERPNext 镜像已有）或 CSV/标准库。

**Spec:** `docs/superpowers/specs/2026-11-06-selfledger-public-erp-platform-design.md`

## Global Constraints

- 正式账只能来自 ERPNext 已提交凭证；页面汇总不能成为第二套账。
- 自动流程止于草稿或检查建议，正式审核、过账、关账由授权人员确认。
- 确认重复的流水不进入分类、凭证、总账和报税材料数量。
- 已过账更正使用受控重建/冲销和审计，不直接篡改 GL Entry。
- 报税材料显著标记“供申报准备和复核，不代表已向税务机关提交”。
- 所有导出文件为私有文件，下载必须重新检查当前用户、公司和 Site 权限。

## File Map

- `internal_ledger/accounting/batch_workflow.py`：导入后的幂等自动处理。
- `internal_ledger/accounting/posting.py`、`posted_entry_maintenance.py`：正式过账与更正。
- `internal_ledger/accounting/month_end.py`：月结检查和锁账编排。
- `internal_ledger/accounting/tax_materials.py`：报税材料清单和导出。
- `internal_ledger/api/close_api.py`、`tax_api.py`：受权限控制的页面接口。
- `internal_ledger/internal_ledger/doctype/month_end_run/`：持久化月结检查批次。
- `internal_ledger/internal_ledger/doctype/tax_material_package/`：材料包元数据、状态和文件引用。
- `internal_ledger/public/frontend/src/pages/AccountingCenter.vue`：会计中心主入口。
- `internal_ledger/public/frontend/src/pages/MonthEndCenter.vue`：月结检查。
- `internal_ledger/public/frontend/src/pages/TaxMaterials.vue`：材料中心。
- `internal_ledger/tests/`：分类、过账、月结、材料和端到端测试。

---

### Task 1: Preserve the Existing Intelligent Accounting Contract

**Files:**
- Modify only if tests expose a defect: `internal_ledger/accounting/batch_workflow.py`
- Modify only if tests expose a defect: `internal_ledger/importing/dedupe.py`
- Modify only if tests expose a defect: `internal_ledger/accounting/posting.py`
- Test: `internal_ledger/tests/test_end_to_end.py`
- Test: `internal_ledger/tests/test_duplicate_review.py`
- Test: `internal_ledger/tests/test_posted_entry_maintenance.py`

**Interfaces:**
- Input: bank/WeChat/Alipay CSV, PDF/JPG/PNG OCR and manual corrections.
- Output states: duplicate review, manual classification, draft review, pending posting, posted.
- Invariant: one accepted source transaction maps to at most one active posting link.

- [ ] Add one golden end-to-end test containing normal rows, exact duplicates, same-date/same-counterparty/same-amount suspicious rows, renamed descriptions, unmatched rows and malformed rows.
- [ ] Assert confirmed duplicates never increment draft, posted or tax-package counts; released duplicates resume automatic classification exactly once.
- [ ] Assert every successful row preserves source file, batch, original row/page, corrections, classification reason, draft and Journal Entry links.
- [ ] Assert authorized rebuild and permanent deletion update the workbench, Journal Entry and reports atomically and emit audit records.
- [ ] Run focused modules once after the test is complete. Repair only failures reproduced by this contract; no adjacent refactor.
- [ ] Commit the contract and any minimal fixes:

```bash
git add internal_ledger/accounting internal_ledger/importing/dedupe.py internal_ledger/tests/test_end_to_end.py internal_ledger/tests/test_duplicate_review.py internal_ledger/tests/test_posted_entry_maintenance.py
git commit -m "test: lock intelligent accounting invariants"
```

---

### Task 2: Implement Month-End Check Runs

**Files:**
- Create: `internal_ledger/accounting/month_end.py`
- Create: `internal_ledger/api/close_api.py`
- Create: `internal_ledger/internal_ledger/doctype/month_end_run/month_end_run.json`
- Create: `internal_ledger/internal_ledger/doctype/month_end_run/month_end_run.py`
- Create: `internal_ledger/internal_ledger/doctype/month_end_check/month_end_check.json`
- Create: `internal_ledger/internal_ledger/doctype/month_end_check/month_end_check.py`
- Modify: `internal_ledger/hooks.py`
- Test: `internal_ledger/tests/test_month_end.py`

**Interfaces:**
- `run_month_end_checks(company: str, period: str) -> dict`
- `get_month_end_run(name: str) -> dict`
- `confirm_period_close(run_name: str) -> dict`
- Check codes: `unposted_drafts`, `pending_duplicates`, `unreconciled_bank`, `negative_stock`, `unsubmitted_invoices`, `unbalanced_accounts`, `missing_payroll`, `missing_tax_documents`.

- [ ] Write failing tests for deterministic check codes, company/period isolation, rerun versioning, viewer read-only behavior and close refusal when a blocking check remains.
- [ ] Define `Month End Run` fields: company, period_start, period_end, status, executed_by, executed_at, source_hash and child checks. Define child fields: code, severity, status, count, message, target_route.
- [ ] Implement each check as a small read-only function returning the same typed result; never fix data inside a check.
- [ ] Compute `source_hash` from relevant document counts/max modified timestamps so identical reruns can return the existing completed result.
- [ ] `confirm_period_close` requires `财务主管` or `账套管理员`, zero blocking checks and an open ERPNext accounting period; it creates/updates the standard period-closing record rather than a custom lock table.
- [ ] Add audit events for run and confirm operations.
- [ ] Run:

```powershell
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_month_end
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_permissions
```

- [ ] Commit:

```bash
git add internal_ledger/accounting/month_end.py internal_ledger/api/close_api.py internal_ledger/internal_ledger/doctype/month_end_run internal_ledger/internal_ledger/doctype/month_end_check internal_ledger/hooks.py internal_ledger/tests/test_month_end.py
git commit -m "feat: add auditable month end checks"
```

---

### Task 3: Generate Versioned Tax Preparation Packages

**Files:**
- Create: `internal_ledger/accounting/tax_materials.py`
- Create: `internal_ledger/api/tax_api.py`
- Create: `internal_ledger/internal_ledger/doctype/tax_material_package/tax_material_package.json`
- Create: `internal_ledger/internal_ledger/doctype/tax_material_package/tax_material_package.py`
- Test: `internal_ledger/tests/test_tax_materials.py`

**Interfaces:**
- `build_tax_material_package(company: str, period: str, package_type: str) -> dict`
- Package types: `monthly_standard`, `quarterly_standard`, `annual_standard`.
- ZIP content uses Chinese filenames and contains `材料清单.csv`, `科目余额表.csv`, `总账.csv`, `明细账.csv`, `利润表.csv`, `资产负债表.csv`, `现金流量表.csv`, `销项发票.csv`, `进项发票.csv`, `工资汇总.csv`, `费用汇总.csv`, `异常清单.csv`, and `说明.txt`.

- [ ] Write failing tests for exact manifest names, stable ordering, CNY decimal formatting, company/period filters, private File permissions, excluded confirmed duplicates and package reproducibility.
- [ ] Define package fields: company, period, package_type, status, version, source_hash, generated_by, generated_at, file, totals_json and disclaimer.
- [ ] Read standard ERPNext Reports/DocTypes through server-side report/query APIs; do not recalculate GL balances from Imported Transaction.
- [ ] Generate CSV with UTF-8 BOM for Chinese Excel compatibility. Generate ZIP using Python `zipfile`; avoid a new dependency.
- [ ] Store the archive as private `File` attached to the package and validate downloads with existing protected-file permission hooks.
- [ ] If source hash changes, create the next immutable package version; never overwrite a package already downloaded or approved.
- [ ] Run `test_tax_materials`, `test_evidence_api`, `test_permissions` and a manual unzip/encoding check.
- [ ] Commit:

```bash
git add internal_ledger/accounting/tax_materials.py internal_ledger/api/tax_api.py internal_ledger/internal_ledger/doctype/tax_material_package internal_ledger/tests/test_tax_materials.py
git commit -m "feat: generate tax preparation material packages"
```

---

### Task 4: Add Month-End and Tax Material Pages to Accounting Center

**Files:**
- Modify: `internal_ledger/public/frontend/src/App.vue`
- Modify: `internal_ledger/public/frontend/src/pages/AccountingCenter.vue`
- Create: `internal_ledger/public/frontend/src/pages/MonthEndCenter.vue`
- Create: `internal_ledger/public/frontend/src/pages/TaxMaterials.vue`
- Modify: `internal_ledger/public/frontend/src/api.js`
- Modify: `internal_ledger/tests/frontend_review.cjs`
- Modify: `internal_ledger/tests/test_unified_navigation.py`

**Interfaces:**
- Accounting tabs: `智能记账`, `凭证与总账`, `月结检查`, `报税材料`.
- Monthly close page renders status, severity, count, plain-Chinese explanation and exact action route.
- Package page renders period, version, source hash prefix, creator, generation time, disclaimer and authorized download.

- [ ] Add failing front-end tests for Chinese-only primary labels, empty/loading/error/success states, keyboard navigation and permissions.
- [ ] Add routes `month-end` and `tax-materials` inside the existing shell; do not create a second app launcher or iframe.
- [ ] Add a prominent “查看月结/报税材料” action after audit/posting completion without changing existing five-step accounting workflow semantics.
- [ ] Disable confirm/generate buttons for read-only users; API must still enforce authorization.
- [ ] Show all blocking checks before the confirm button and require a final confirmation dialog containing company and period.
- [ ] Run front-end contracts, unified-navigation tests and `bench build --app internal_ledger` once after the page set is complete.
- [ ] Commit:

```bash
git add internal_ledger/public/frontend/src internal_ledger/tests/frontend_review.cjs internal_ledger/tests/test_unified_navigation.py
git commit -m "feat: add month end and tax material centers"
```

---

### Task 5: Validate Professional Accounts and Tax Deliverables

**Files:**
- Create: `internal_ledger/tests/fixtures/month_end/`
- Modify: `internal_ledger/tests/test_end_to_end.py`
- Create: `docs/acceptance/2026-11-06-finance-tax-g2.md`

**Interfaces:**
- Fixture uses fictional companies and amounts only.
- Acceptance compares standard Journal Entry, GL Entry, reports and package manifests.

- [ ] Import a mixed bank/WeChat/Alipay/OCR fixture with duplicates and corrections into a dedicated acceptance Site.
- [ ] Complete classification, draft review, posting, one authorized rebuild and one permanent deletion.
- [ ] Reconcile cash/bank, run month-end checks, clear blocking items, confirm close and generate monthly package.
- [ ] Assert total debits equal total credits, report totals reconcile to GL, package summary reconciles to reports and every package line has source traceability.
- [ ] Open Chinese CSV files in Excel-compatible mode and verify encoding, dates and two-decimal money formatting.
- [ ] Record actual commands, SHA, pass counts and output hashes in G2 evidence; do not include transaction descriptions from real customers.
- [ ] Commit:

```bash
git add internal_ledger/tests/fixtures/month_end internal_ledger/tests/test_end_to_end.py docs/acceptance/2026-11-06-finance-tax-g2.md
git commit -m "test: prove finance and tax material workflow"
```

## Phase Exit Criteria

- Existing intelligent import and posted-entry maintenance contracts pass without regression.
- Month-end checks are deterministic, auditable and cannot silently mutate data.
- Standard books and statements reconcile to submitted ERPNext GL Entries.
- Tax preparation ZIP is private, reproducible, versioned and clearly not a direct tax submission.
- Confirmed duplicates are excluded from all draft, posting, report and material counts.
- G2 evidence is committed before business-module implementation begins.

