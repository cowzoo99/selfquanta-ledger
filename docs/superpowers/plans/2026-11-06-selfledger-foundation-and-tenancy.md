# SelfLedger Foundation and Tenancy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可重复构建、可安全开通多个独立客户 Site、具有清晰角色边界和统一中文入口的 SelfLedger 公测平台底座。

**Architecture:** 一个固定版本应用镜像承载 Frappe、ERPNext、HRMS 和 `internal_ledger`；同一 Compose 项目允许多个 Frappe Site 共享计算进程，但每个 Site 使用独立数据库、站点目录、加密密钥、域名和备份。站点开通由显式脚本执行，客户信息和密码不进入 Git。

**Tech Stack:** Frappe/ERPNext/HRMS v16、Docker Compose、PowerShell、MariaDB、Redis、Nginx、Vue 3、Frappe Workspace。

**Spec:** `docs/superpowers/specs/2026-11-06-selfledger-public-erp-platform-design.md`

## Global Constraints

- 不把多个客户放进同一 Company 或同一 Site 来模拟多租户。
- 不新增中央客户数据库作为 8 周试点前置条件；试点开通使用受控运维脚本和不含秘密的清单。
- 站点密码、数据库密码、TLS 私钥、备份密钥通过文件或 Docker Secret 注入，禁止命令行明文和 Git 跟踪。
- 角色权限以服务端 DocPerm、User Permission 和 API 检查为准，前端隐藏按钮不能代替权限。
- 统一入口只负责导航和任务编排，标准 ERPNext DocType 仍是唯一业务数据源。

## File Map

- `infra/versions.env`：Frappe、ERPNext、HRMS、数据库和 Redis 固定版本。
- `infra/build_image.ps1`：生成固定应用列表并构建不可变镜像。
- `infra/compose.yaml`：运行共享服务和按参数创建 Site。
- `infra/provision-site.ps1`：新建客户 Site、安装应用和初始化企业的唯一入口。
- `infra/test-config.ps1`：静态安全、版本和多 Site 配置检查。
- `internal_ledger/install.py`：幂等安装角色、权限、工作区和中文默认值。
- `internal_ledger/permissions.py`：公司和文件的服务端权限边界。
- `internal_ledger/internal_ledger/workspace/selfledger/selfledger.json`：统一 Workspace。
- `internal_ledger/public/frontend/src/App.vue`：统一 SelfLedger 产品导航。
- `internal_ledger/public/frontend/src/pages/Dashboard.vue`：面向任务的中文首页。
- `internal_ledger/tests/test_install.py`、`test_permissions.py`、`test_unified_navigation.py`：平台回归。
- `docs/operations/tenant-provisioning.md`：开通、停用、导出和销毁操作规程。

---

### Task 1: Pin HRMS and Make the Image Reproducible

**Files:**
- Modify: `infra/versions.env`
- Modify: `infra/build_image.ps1`
- Modify: `infra/compose.yaml`
- Modify: `infra/test-config.ps1`
- Test: `internal_ledger/tests/test_public_access_security.py`

**Interfaces:**
- `versions.env` adds `HRMS_VERSION` as an exact immutable v16 tag.
- `build_image.ps1` writes apps in order: ERPNext, HRMS, internal_ledger.
- `create-site` installs `erpnext`, `hrms`, then `internal_ledger`.

- [ ] Add failing assertions in `infra/test-config.ps1` that `HRMS_VERSION` exists, apps JSON includes HRMS exactly once, and `compose.yaml` installs HRMS before `internal_ledger`.
- [ ] Run:

```powershell
pwsh -NoProfile -File infra/test-config.ps1
```

Expected: FAIL because HRMS is not pinned or installed.

- [ ] Add the HRMS v16 release tag verified compatible with the pinned Frappe/ERPNext versions to `versions.env`; do not use `develop`, `main` or an unpinned branch.
- [ ] Extend the apps JSON array in `build_image.ps1` with `https://github.com/frappe/hrms` and `$versions.HRMS_VERSION`.
- [ ] Add `--install-app hrms` to `create-site`; keep application order deterministic.
- [ ] Run `pwsh -NoProfile -File infra/test-config.ps1` and `pwsh -NoProfile -File infra/build_image.ps1 -ValidateOnly`.
- [ ] On the Linux build host, build the image and run `docker run --rm <image> bench version`; expected output lists Frappe, ERPNext, HRMS and Internal Ledger at the pinned versions.
- [ ] Commit:

```bash
git add infra/versions.env infra/build_image.ps1 infra/compose.yaml infra/test-config.ps1 internal_ledger/tests/test_public_access_security.py
git commit -m "build: pin HRMS in the SelfLedger image"
```

---

### Task 2: Provision Independent Customer Sites Safely

**Files:**
- Create: `infra/provision-site.ps1`
- Modify: `infra/compose.yaml`
- Modify: `infra/test-config.ps1`
- Create: `docs/operations/tenant-provisioning.md`

**Interfaces:**
- Command:

```powershell
pwsh -NoProfile -File infra/provision-site.ps1 `
  -Site customer.example.com `
  -CompanyName "示例商贸有限公司" `
  -CompanyAbbr "示例" `
  -AdminPasswordFile C:\secure\customer-admin.txt
```

- The script validates names, refuses an existing Site, passes secrets by mounted files, installs all apps, runs migrate, creates Company, installs the small-business chart and writes a non-secret receipt.
- Receipt path: `infra/provisioning-receipts/<site>-<UTC timestamp>.json`; fields are Site, company, image SHA, app versions, created_at and operator only.

- [ ] Add static failing tests to `infra/test-config.ps1` for rejected invalid hosts, missing password file, existing Site, command-line password values and receipt secret leakage.
- [ ] Implement `provision-site.ps1` with `[ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]+$')]`, `Resolve-Path` for the secret file, exact Compose project selection and `try/finally` cleanup.
- [ ] Add a `site-init` profile/service if necessary; it must accept `SITE_NAME` per invocation and must not rewrite the global default Site.
- [ ] Initialize the company through a whitelisted-free bench execute function that accepts non-secret company parameters and calls existing `install_chart`/`install_default_rules` idempotently.
- [ ] Test with two disposable Sites:

```powershell
pwsh -NoProfile -File infra/provision-site.ps1 -Site alpha.test -CompanyName '甲公司' -CompanyAbbr '甲' -AdminPasswordFile C:\secure\alpha.txt
pwsh -NoProfile -File infra/provision-site.ps1 -Site beta.test -CompanyName '乙公司' -CompanyAbbr '乙' -AdminPasswordFile C:\secure\beta.txt
```

Expected: each Site has exactly one configured Company; user/file/journal counts can differ; running the same command again refuses without mutation.

- [ ] Document open, suspend, export and destroy steps. Destruction must require a separately typed `-ConfirmSite` equal to `-Site`, a verified backup and an explicit maintenance window.
- [ ] Commit:

```bash
git add infra/provision-site.ps1 infra/compose.yaml infra/test-config.ps1 infra/provisioning-receipts/.gitkeep docs/operations/tenant-provisioning.md
git commit -m "feat: add safe independent site provisioning"
```

---

### Task 3: Add Role Presets Without Weakening Existing Ledger Roles

**Files:**
- Modify: `internal_ledger/install.py`
- Modify: `internal_ledger/permissions.py`
- Modify: `internal_ledger/member_security.py`
- Modify: `internal_ledger/api/member_api.py`
- Test: `internal_ledger/tests/test_install.py`
- Test: `internal_ledger/tests/test_permissions.py`
- Test: `internal_ledger/tests/test_member_api.py`

**Interfaces:**
- New SelfLedger roles: `企业负责人`, `财务主管`, `会计`, `出纳`, `销售`, `采购`, `库存`, `项目`, `人事工资`, `只读审计`.
- Existing `账套管理员` and `账务查看者` remain compatible.
- `create_enterprise_member` accepts one server-side role preset name, never arbitrary DocPerms or client-supplied role arrays.

- [ ] Write table-driven failing tests asserting the exact role matrix from the approved specification. Include negative tests for arbitrary roles, cross-company User Permission and a viewer granting privileges.
- [ ] Extend `ensure_roles()` with a constant role preset map; use ERPNext/HRMS standard roles where they already express the permission and add only product-facing SelfLedger roles needed for navigation.
- [ ] Map each product role to the minimum standard roles and a Company User Permission. Do not grant System Manager.
- [ ] Keep original raw source files restricted to financial administrators even when a user can read resulting vouchers/reports.
- [ ] Update member APIs to accept `role_preset` from a fixed allowlist; return the effective Chinese role name and never return password metadata or API keys.
- [ ] Run:

```powershell
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_install
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_permissions
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_member_api
```

Expected: PASS; each write API returns PermissionError for roles outside its matrix.

- [ ] Commit:

```bash
git add internal_ledger/install.py internal_ledger/permissions.py internal_ledger/member_security.py internal_ledger/api/member_api.py internal_ledger/tests/test_install.py internal_ledger/tests/test_permissions.py internal_ledger/tests/test_member_api.py
git commit -m "feat: add least privilege ERP role presets"
```

---

### Task 4: Build One Unified Chinese Product Entry

**Files:**
- Modify: `internal_ledger/internal_ledger/workspace/selfledger/selfledger.json`
- Modify: `internal_ledger/public/js/internal_ledger_desk.js`
- Modify: `internal_ledger/public/frontend/src/App.vue`
- Modify: `internal_ledger/public/frontend/src/pages/Dashboard.vue`
- Create: `internal_ledger/public/frontend/src/pages/ModuleHub.vue`
- Modify: `internal_ledger/tests/frontend_review.cjs`
- Modify: `internal_ledger/tests/test_unified_navigation.py`

**Interfaces:**
- Primary routes: `dashboard`, `import`, `review`, `accounting`, `sales`, `purchase`, `stock`, `expense`, `crm`, `projects`, `hr`, `members`, `account`.
- ModuleHub links to standard ERPNext/HRMS routes using a fixed server-reviewed map; it does not embed another application or create a second shell.
- Navigation items are role-filtered but server permissions remain authoritative.

- [ ] Add failing front-end contract tests for one SelfLedger shell, Chinese labels, mobile wrapping, accessible current state, role-filtered modules and no duplicate ERPNext app launcher as the primary path.
- [ ] Add a `MODULES` constant containing label, description, icon, route and allowed product roles. Do not fetch executable routes from user input.
- [ ] Extend `App.vue` navigation and render `ModuleHub` for standard modules; preserve the existing import/review/accounting components unchanged.
- [ ] Make Dashboard show “今日待办、资金、销售、采购、库存预警、项目、人事工资” cards using existing APIs or safe zero states; card clicks use the unified shell.
- [ ] Update the Workspace shortcuts to the same routes and both existing ledger roles plus new permitted roles.
- [ ] Run:

```powershell
node internal_ledger/tests/frontend_review.cjs
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_unified_navigation
bench build --app internal_ledger
```

Expected: PASS and a valid `ledger_workbench.bundle.js` entry in the asset manifest.

- [ ] Commit:

```bash
git add internal_ledger/internal_ledger/workspace/selfledger/selfledger.json internal_ledger/public/js/internal_ledger_desk.js internal_ledger/public/frontend/src internal_ledger/tests/frontend_review.cjs internal_ledger/tests/test_unified_navigation.py
git commit -m "feat: unify SelfLedger ERP navigation"
```

---

### Task 5: Prove Cross-Site and Access Isolation

**Files:**
- Create: `internal_ledger/tests/test_multisite_isolation.py`
- Modify: `infra/test-config.ps1`
- Create: `docs/acceptance/2026-11-06-foundation-g1.md`

**Interfaces:**
- Test runner accepts `SELFLEDGER_TEST_SITE_A` and `SELFLEDGER_TEST_SITE_B`.
- Read-only isolation test records counts/hashes, never customer values.

- [ ] Create two test Sites from Task 2 and insert distinct fixture customers, files and journal entries.
- [ ] Assert authenticated requests to Site A cannot resolve Site B document names, files, backups or database names.
- [ ] Assert Host header routing selects the requested existing Site and rejects an unknown host instead of falling back to a default customer.
- [ ] Assert each Site has distinct `encryption_key`, database name, admin account and backup directory; output only equality booleans and hashes.
- [ ] Run all Phase 1 focused tests plus `infra/test-config.ps1`; record commands, SHA, pass counts and two-site evidence in the G1 document.
- [ ] Commit:

```bash
git add internal_ledger/tests/test_multisite_isolation.py infra/test-config.ps1 docs/acceptance/2026-11-06-foundation-g1.md
git commit -m "test: prove SelfLedger tenant isolation"
```

## Phase Exit Criteria

- Immutable image contains exact compatible Frappe, ERPNext, HRMS and Internal Ledger versions.
- An operator can provision a customer Site without exposing secrets or editing Compose source.
- Two Sites have distinct databases, files, keys and backups and pass hostile cross-Site checks.
- Role presets are least-privilege and existing ledger permissions still pass.
- Every permitted user enters through one Chinese SelfLedger shell on desktop and mobile.
- G1 evidence is committed before Finance and Tax implementation begins.

