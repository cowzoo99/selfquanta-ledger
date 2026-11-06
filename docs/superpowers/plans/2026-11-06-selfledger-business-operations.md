# SelfLedger Business Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 ERPNext/HRMS 标准单据交付 CRM、销售、采购、库存、费用、项目和人事工资能力，并在同一 SelfLedger 中文任务层中完成业财联动。

**Architecture:** 不新增平行业务 DocType。SelfLedger 提供初始化模板、角色工作台、中文任务向导和跨模块指标 API；所有业务写入 ERPNext/HRMS 标准 DocType，由标准提交/取消逻辑生成库存和会计影响。

**Tech Stack:** ERPNext v16、HRMS v16、Frappe Server Scripts-free Python app、Vue 3、Frappe Reports。

**Spec:** `docs/superpowers/specs/2026-11-06-selfledger-public-erp-platform-design.md`

## Global Constraints

- 销售、采购、库存、费用、项目、人事工资使用标准 ERPNext/HRMS 数据模型和状态机。
- 不通过 `frappe.db.set_value` 绕过标准单据校验、提交、取消或总账/库存钩子。
- 首版不包含生产制造、MRP、工单、个税社保直接申报和银行实时直连。
- 用户只看与角色和 Company User Permission 匹配的数据；薪资明细只允许本人和授权 HR/财务查看。
- Dashboard 指标必须可追溯到标准报表或明确查询，不能缓存成第二份事实数据。

## File Map

- `internal_ledger/business/setup.py`：小微企业标准业务初始化。
- `internal_ledger/api/business_api.py`：统一首页的只读指标和待办。
- `internal_ledger/api/setup_api.py`：管理员执行的幂等初始化向导。
- `internal_ledger/public/frontend/src/pages/ModuleHub.vue`：模块任务入口。
- `internal_ledger/public/frontend/src/pages/BusinessSetup.vue`：基础资料向导。
- `internal_ledger/public/frontend/src/pages/Dashboard.vue`：跨模块指标和待办。
- `internal_ledger/install.py`：角色与标准报告可见性。
- `internal_ledger/tests/test_business_setup.py`：初始化幂等和权限。
- `internal_ledger/tests/test_business_workflows.py`：标准单据业财联动。
- `internal_ledger/tests/test_hr_payroll_permissions.py`：员工与工资隐私。

---

### Task 1: Create an Idempotent Business Setup Service

**Files:**
- Create: `internal_ledger/business/__init__.py`
- Create: `internal_ledger/business/setup.py`
- Create: `internal_ledger/api/setup_api.py`
- Test: `internal_ledger/tests/test_business_setup.py`

**Interfaces:**
- `get_setup_status(company: str) -> dict`
- `apply_business_setup(company: str, business_type: str, options: dict) -> dict`
- Supported `business_type`: `trade`, `service`.
- Setup creates only missing defaults: selling/buying price lists, customer/supplier groups, item groups, warehouses, cost centers, modes of payment, expense claim types and project type.

- [ ] Write failing tests for exact default records, rerun idempotence, conflict refusal, company isolation and administrator-only mutation.
- [ ] Implement constants for the approved Chinese names and standard DocTypes; do not create custom mirrors.
- [ ] For an existing same-name record with conflicting company/type, return a conflict item and make no partial mutation.
- [ ] Wrap one setup run in a database transaction and emit a single audit event with created/skipped/conflict counts.
- [ ] Return actionable next steps: customer, supplier, item/service, warehouse and opening balance setup.
- [ ] Run `test_business_setup`, `test_install` and `test_permissions`.
- [ ] Commit:

```bash
git add internal_ledger/business internal_ledger/api/setup_api.py internal_ledger/tests/test_business_setup.py
git commit -m "feat: add small business setup service"
```

---

### Task 2: Expose Role-Aware Business Metrics and Tasks

**Files:**
- Create: `internal_ledger/api/business_api.py`
- Modify: `internal_ledger/public/frontend/src/pages/Dashboard.vue`
- Modify: `internal_ledger/public/frontend/src/api.js`
- Test: `internal_ledger/tests/test_business_api.py`
- Modify: `internal_ledger/tests/frontend_review.cjs`

**Interfaces:**
- `get_business_dashboard(company: str, from_date: str, to_date: str) -> dict`
- Sections: sales, receivables, purchases, payables, cash, low_stock, expenses, projects, hr, month_end.
- Each card returns `label`, `value`, `unit`, `severity`, `target_route`, `as_of`; routes come from a server allowlist.

- [ ] Write failing API tests for period/company filtering, role field suppression, standard-report reconciliation and query-count ceiling.
- [ ] Implement one query/report adapter per section; unavailable modules return `available=false` rather than an exception.
- [ ] Exclude payroll values from users without HR/payroll or finance privilege; allow employees to see only their own claim/leave tasks.
- [ ] Add dashboard loading, empty, partial and error states; one failed section must not blank the whole page.
- [ ] Add plain-Chinese tooltips explaining metric source and update time.
- [ ] Run business API tests, front-end contract and build.
- [ ] Commit:

```bash
git add internal_ledger/api/business_api.py internal_ledger/public/frontend/src/pages/Dashboard.vue internal_ledger/public/frontend/src/api.js internal_ledger/tests/test_business_api.py internal_ledger/tests/frontend_review.cjs
git commit -m "feat: add role aware business dashboard"
```

---

### Task 3: Validate CRM, Sales, Purchase and Stock Workflows

**Files:**
- Create: `internal_ledger/tests/test_business_workflows.py`
- Modify: `internal_ledger/public/frontend/src/pages/ModuleHub.vue`
- Modify: `internal_ledger/tests/frontend_review.cjs`

**Interfaces:**
- CRM route set: Lead, Opportunity, Customer.
- Sales route set: Quotation, Sales Order, Delivery Note, Sales Invoice, Payment Entry.
- Purchase route set: Supplier, Request for Quotation, Purchase Order, Purchase Receipt, Purchase Invoice, Payment Entry.
- Stock route set: Item, Warehouse, Stock Entry, Stock Ledger, reorder report.

- [ ] Build a trade-company fixture with one customer, supplier, stock item, service item, warehouse and opening stock.
- [ ] Test Lead → Opportunity → Customer conversion and permission boundaries.
- [ ] Test Sales Order → Delivery Note → Sales Invoice → Payment Entry; assert stock and GL effects against standard reports.
- [ ] Test Purchase Order → Purchase Receipt → Purchase Invoice → Payment Entry; assert stock valuation, payable and bank effects.
- [ ] Test insufficient/negative stock behavior follows company configuration and surfaces a Chinese actionable error.
- [ ] Update ModuleHub task cards to deep-link to standard list/new/report routes; do not duplicate standard forms.
- [ ] Run the new workflow suite plus permissions and unified navigation once.
- [ ] Commit:

```bash
git add internal_ledger/tests/test_business_workflows.py internal_ledger/public/frontend/src/pages/ModuleHub.vue internal_ledger/tests/frontend_review.cjs
git commit -m "test: cover trade business workflows"
```

---

### Task 4: Validate Expenses and Project Accounting

**Files:**
- Modify: `internal_ledger/tests/test_business_workflows.py`
- Modify: `internal_ledger/public/frontend/src/pages/ModuleHub.vue`
- Modify: `internal_ledger/api/business_api.py`

**Interfaces:**
- Expense route set: Expense Claim, Employee Advance, Purchase Invoice.
- Project route set: Project, Task, Timesheet, Project Profitability.

- [ ] Add a service-company fixture with one employee, customer project, task and cost center.
- [ ] Test employee advance → expense claim → approval → payment and assert GL/cost-center effects.
- [ ] Test project/task/timesheet and project-linked sales/expense documents; reconcile project profitability to standard data.
- [ ] Verify ordinary employees can create/read only their own claims and timesheets; project managers see assigned projects; finance sees accounting outputs.
- [ ] Add dashboard cards for pending expense approvals, project progress and overdue tasks using server-authorized routes.
- [ ] Run business workflow and business API suites.
- [ ] Commit:

```bash
git add internal_ledger/tests/test_business_workflows.py internal_ledger/public/frontend/src/pages/ModuleHub.vue internal_ledger/api/business_api.py
git commit -m "feat: connect expenses and projects to finance"
```

---

### Task 5: Deliver Employee, Attendance, Leave and Payroll Drafts

**Files:**
- Modify: `internal_ledger/install.py`
- Modify: `internal_ledger/public/frontend/src/pages/ModuleHub.vue`
- Modify: `internal_ledger/api/business_api.py`
- Create: `internal_ledger/tests/test_hr_payroll_permissions.py`

**Interfaces:**
- HR route set: Employee, Department, Designation, Attendance, Leave Application, Salary Structure, Payroll Entry, Salary Slip.
- Payroll accounting uses HRMS standard Payroll Entry and Salary Slip; only submitted payroll can generate accounting entries.

- [ ] Write failing tests for HR roles, employee self-service, payroll confidentiality, company isolation and standard accounting effects.
- [ ] Add minimal product-role mappings for HR user, HR manager, payroll manager and employee self-service without granting System Manager.
- [ ] Verify employee, department, attendance and leave workflows using standard HRMS validation.
- [ ] Verify salary structure assignment → payroll entry draft → review → submit → accounting entry; no automatic tax/social-security submission.
- [ ] Add Chinese task cards and warnings that payroll export requires accountant/HR review.
- [ ] Ensure backups and customer export include HR attachments while retaining private-file permissions.
- [ ] Run HR permission tests, install/permission tests and front-end contracts.
- [ ] Commit:

```bash
git add internal_ledger/install.py internal_ledger/public/frontend/src/pages/ModuleHub.vue internal_ledger/api/business_api.py internal_ledger/tests/test_hr_payroll_permissions.py
git commit -m "feat: enable secure HR and payroll workflows"
```

---

### Task 6: Integrated Business-to-Finance Acceptance

**Files:**
- Modify: `internal_ledger/tests/test_end_to_end.py`
- Create: `docs/acceptance/2026-11-06-business-operations-g3.md`

**Interfaces:**
- Uses one fictional trade Site and one fictional service Site.
- Compares standard Stock Ledger, General Ledger, AR/AP, project and payroll reports.

- [ ] Run the complete trade and service scenarios using named role accounts, not Administrator for every action.
- [ ] Assert each submitted business document has expected stock/GL links and each cancellation follows standard reversal behavior.
- [ ] Import the resulting bank statement through the intelligent ledger and reconcile payments without duplicate accounting.
- [ ] Verify viewer and auditor accounts can inspect permitted results but cannot mutate source documents.
- [ ] Capture SHA, app versions, test counts and anonymized report totals in G3 evidence.
- [ ] Commit:

```bash
git add internal_ledger/tests/test_end_to_end.py docs/acceptance/2026-11-06-business-operations-g3.md
git commit -m "test: prove business to finance integration"
```

## Phase Exit Criteria

- Trade and service setup is idempotent and conflict-safe.
- CRM, sales, purchase, stock, expense, project and HR/payroll use standard ERPNext/HRMS records.
- Standard business submissions produce correct stock and accounting effects without duplicate books.
- Role-specific dashboards and routes disclose only permitted information.
- Trade and service end-to-end scenarios pass and G3 evidence is committed.

