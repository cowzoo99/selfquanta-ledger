# SelfLedger AI Operations and Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增加可关闭、可审计的国内大模型助手，并完成监控、加密备份、恢复、升级回滚、安全公网接入和 3–5 家邀请制试点。

**Architecture:** AI 通过一个 OpenAI-compatible HTTPS adapter 接入可配置国内模型，所有工具调用经过服务端白名单和现有权限；核心 ERP 不依赖模型可用性。运维继续使用不可变镜像、每 Site 加密备份和单 Site 渐进迁移，Nginx 按 Host 路由到独立 Site。

**Tech Stack:** Python standard library HTTP/JSON、Frappe Password fields、Docker Compose、Nginx、PowerShell、systemd、加密备份、外部可用性监控。

**Spec:** `docs/superpowers/specs/2026-11-06-selfledger-public-erp-platform-design.md`

## Global Constraints

- 真实模型调用、购买额度或提交真实客户数据前必须获得单独批准。
- AI 默认不读取原始附件、员工工资、身份证号、银行账号或联系人隐私；只有明确授权的最小字段可进入提示词。
- AI 返回值一律视为建议或草稿，必须通过确定性校验和既有权限后才能保存。
- 模型故障、超时、余额不足或关闭时，ERP 核心业务和会计流程必须完整可用。
- 公网发布前必须启用 HTTPS、2FA、登录限流、命名账户、审计和恢复演练。
- 试点反馈和案例不默认公开；公开需要企业另行书面授权。

## File Map

- `internal_ledger/ai/provider.py`：OpenAI-compatible 模型适配器。
- `internal_ledger/ai/service.py`：权限、脱敏、工具白名单和输出校验。
- `internal_ledger/api/ai_api.py`：聊天、解释和建议接口。
- `internal_ledger/internal_ledger/doctype/ai_provider_settings/`：单 Site 模型设置和 Password 字段。
- `internal_ledger/internal_ledger/doctype/ai_interaction_audit/`：不含敏感正文的审计元数据。
- `internal_ledger/public/frontend/src/pages/AIAssistant.vue`：统一壳内 AI 页面。
- `infra/health-check.ps1`：逐 Site 健康和证书检查。
- `infra/backup-all-sites.ps1`：逐 Site 调用现有加密备份。
- `infra/restore-check.ps1`：隔离恢复和关键计数验证。
- `docs/operations/`：运行手册、事故、升级、试点开通和数据退出。

---

### Task 1: Implement a Disabled-by-Default AI Provider Boundary

**Files:**
- Create: `internal_ledger/ai/__init__.py`
- Create: `internal_ledger/ai/provider.py`
- Create: `internal_ledger/ai/service.py`
- Create: `internal_ledger/internal_ledger/doctype/ai_provider_settings/ai_provider_settings.json`
- Create: `internal_ledger/internal_ledger/doctype/ai_provider_settings/ai_provider_settings.py`
- Test: `internal_ledger/tests/test_ai_provider.py`

**Interfaces:**
- `AIProvider.complete(messages: list[dict], response_schema: dict, timeout_seconds: int) -> dict`
- Settings: enabled, base_url, model, api_key (Password), timeout_seconds, monthly_request_limit, allowed_features.
- Features: `explain_report`, `suggest_classification`, `draft_summary`, `answer_help`.

- [ ] Write failing unit tests with an in-process fake HTTPS response for disabled state, timeout, non-JSON response, schema mismatch, rate limit and secret redaction.
- [ ] Implement the adapter with `urllib.request`, explicit TLS verification, fixed path suffix, bounded body size and no automatic retry on non-idempotent calls.
- [ ] Read API key via Frappe Password API only at call time; never return or log it.
- [ ] Reject non-HTTPS base URLs outside a test-only dependency-injected transport.
- [ ] Enforce per-Site monthly request counter and allowed feature list before network access.
- [ ] Run `test_ai_provider`; no real provider call is permitted in this task.
- [ ] Commit:

```bash
git add internal_ledger/ai internal_ledger/internal_ledger/doctype/ai_provider_settings internal_ledger/tests/test_ai_provider.py
git commit -m "feat: add disabled by default AI provider boundary"
```

---

### Task 2: Add Permissioned AI Tools and Audit

**Files:**
- Create: `internal_ledger/api/ai_api.py`
- Create: `internal_ledger/internal_ledger/doctype/ai_interaction_audit/ai_interaction_audit.json`
- Create: `internal_ledger/internal_ledger/doctype/ai_interaction_audit/ai_interaction_audit.py`
- Modify: `internal_ledger/ai/service.py`
- Test: `internal_ledger/tests/test_ai_service.py`

**Interfaces:**
- `ask_assistant(feature: str, context_type: str, context_name: str, question: str) -> dict`
- Response: answer, citations (document type/name/route), warnings, request_id.
- Audit stores feature, actor, company, context references, provider/model, latency, token counts if returned, result status and content hashes; it does not store raw secret or private prompt text.

- [ ] Write failing tests for role/company isolation, private-field stripping, prompt injection strings, forbidden mutation requests, citation existence and audit metadata.
- [ ] Build a fixed tool registry of read-only functions for standard reports, approved document summaries and help text. No arbitrary method name, SQL, Python or route comes from model output.
- [ ] Apply field allowlists by context; redact bank account, ID, phone, email, salary and attachment content unless the feature explicitly permits the current actor.
- [ ] Validate model JSON against feature-specific schemas; return a Chinese safe error on invalid output.
- [ ] Classification suggestions create no rule or voucher automatically; user must explicitly accept through the existing deterministic classification API.
- [ ] Run AI service and permissions tests.
- [ ] Commit:

```bash
git add internal_ledger/api/ai_api.py internal_ledger/ai/service.py internal_ledger/internal_ledger/doctype/ai_interaction_audit internal_ledger/tests/test_ai_service.py
git commit -m "feat: add permissioned auditable AI tools"
```

---

### Task 3: Add AI to the Unified UI Without Blocking ERP

**Files:**
- Create: `internal_ledger/public/frontend/src/pages/AIAssistant.vue`
- Modify: `internal_ledger/public/frontend/src/App.vue`
- Modify: `internal_ledger/public/frontend/src/pages/Dashboard.vue`
- Modify: `internal_ledger/public/frontend/src/api.js`
- Modify: `internal_ledger/tests/frontend_review.cjs`

**Interfaces:**
- Route: `assistant`.
- The page shows disabled, unavailable, loading, answer, warning and error states.
- Supported actions are fixed buttons; free text cannot select a server method.

- [ ] Add failing front-end tests proving the assistant is hidden/disabled when settings disable it and ERP navigation remains usable after AI errors.
- [ ] Add Chinese feature cards for report explanation, classification suggestion, business summary and usage help.
- [ ] Display citations and “AI 建议，需人工复核”; never present advice as posted accounting or tax submission.
- [ ] Keep requests cancellable and prevent double submission; do not persist conversation history in browser local storage.
- [ ] Run front-end contract, API tests and application build.
- [ ] Commit:

```bash
git add internal_ledger/public/frontend/src internal_ledger/tests/frontend_review.cjs
git commit -m "feat: add optional AI assistant to SelfLedger"
```

---

### Task 4: Operate and Restore Every Site

**Files:**
- Create: `infra/health-check.ps1`
- Create: `infra/backup-all-sites.ps1`
- Modify: `infra/backup.ps1`
- Modify: `infra/restore-check.ps1`
- Modify: `infra/test-config.ps1`
- Modify: `docs/operations/backup-restore.md`
- Create: `docs/operations/incident-response.md`

**Interfaces:**
- `health-check.ps1 -SitesFile <path>` reads a non-secret JSON list and checks HTTPS, certificate expiry, ping, scheduler, queue and last backup.
- `backup-all-sites.ps1` invokes the existing encryption-safe `backup.ps1` once per Site and returns a per-Site summary without stopping after the first failure.

- [ ] Add failing config tests for unknown-host fallback, missing backup, certificate expiry, unencrypted output, shared backup key path and missing Site name.
- [ ] Implement health checks with bounded timeouts and nonzero exit when any Site has a blocking condition; output no cookies or response bodies.
- [ ] Implement all-Site backup orchestration with one explicit Site list, per-Site destination/key file and retention policy.
- [ ] Extend isolated restore check to select one package, restore to a disposable Site and compare Company, User, File, Item, Journal Entry, GL Entry, Employee and Salary Slip counts.
- [ ] Document RPO 24 hours, trial RTO 4 hours, severity levels, customer notification and credential rotation.
- [ ] Run config tests, one encrypted backup per acceptance Site and one isolated restore from each schema version in the candidate release.
- [ ] Commit:

```bash
git add infra/health-check.ps1 infra/backup-all-sites.ps1 infra/backup.ps1 infra/restore-check.ps1 infra/test-config.ps1 docs/operations/backup-restore.md docs/operations/incident-response.md
git commit -m "ops: add multisite health backup and restore"
```

---

### Task 5: Harden Public Access and Progressive Deployment

**Files:**
- Modify: `infra/nginx.conf`
- Modify: `infra/compose.yaml`
- Modify: `infra/host/configure-public-access.sh`
- Modify: `internal_ledger/tests/test_public_access_security.py`
- Modify: `docs/operations/deploy.md`

**Interfaces:**
- Nginx preserves the real Host for Frappe Site routing and rejects unrecognized hosts.
- Deployment migrates one Site at a time under maintenance mode and records success before proceeding.

- [ ] Add failing tests for HTTPS-only, secure cookies, HSTS, login rate limit, unknown Host rejection, no global forced Site header and 2FA gates for privileged roles.
- [ ] Replace fixed `FRAPPE_SITE_NAME_HEADER` behavior with validated Host-based site selection; keep an explicit health host only for internal probes.
- [ ] Require DNS/TLS readiness and Site existence before adding a host to the public allowlist.
- [ ] Update deployment runbook: backup → maintenance → migrate one Site → read-only smoke → enable scheduler → traffic → next Site.
- [ ] Add rollback: maintenance, previous image SHA, database/files restore only when schema compatibility requires it, then smoke.
- [ ] Run public-access security tests and `infra/test-config.ps1`.
- [ ] Commit:

```bash
git add infra/nginx.conf infra/compose.yaml infra/host/configure-public-access.sh internal_ledger/tests/test_public_access_security.py docs/operations/deploy.md
git commit -m "security: harden multisite public access"
```

---

### Task 6: Run the 3–5 Company Invitation Pilot

**Files:**
- Create: `docs/operations/pilot-onboarding.md`
- Create: `docs/acceptance/2026-11-06-pilot-company-template.md`
- Create: `docs/acceptance/2026-11-06-ai-operations-g4.md`
- Create: `docs/acceptance/2026-11-06-public-erp-g5.md`

**Interfaces:**
- One checklist copy per customer, stored outside Git if it contains identifying information; Git stores only the anonymized template and aggregate status.
- Pilot status: invited, provisioned, trained, active, blocked, completed, exited.

- [ ] Select 3–5 companies and obtain explicit consent for data processing, backup location, support channel and free-pilot terms; case publication remains opt-in.
- [ ] Provision one independent Site per company, create named users, enforce password change and privileged 2FA, and deliver a one-page login/use guide.
- [ ] Import opening masters/balances and one representative month using customer-approved files; retain original files privately.
- [ ] Complete role, finance, business, mobile, backup/export and customer-exit acceptance for each Site.
- [ ] Enable the real domestic model only after separate approval and credentials; first run uses synthetic data, then one approved low-risk feature. Record cost and provider result without logging content.
- [ ] Fix P0/P1 before continuing admissions. Batch P2/P3 into planned releases; do not hot-edit containers or production databases.
- [ ] Run one restore drill, one image rollback and one provider-offline drill during pilot.
- [ ] Record aggregate availability, backup success, import success, unresolved blockers and monthly infrastructure cost in G4/G5 documents.
- [ ] Commit only anonymized procedures/evidence:

```bash
git add docs/operations/pilot-onboarding.md docs/acceptance/2026-11-06-pilot-company-template.md docs/acceptance/2026-11-06-ai-operations-g4.md docs/acceptance/2026-11-06-public-erp-g5.md
git commit -m "docs: complete SelfLedger invitation pilot gates"
```

## Phase Exit Criteria

- AI can be disabled globally or per feature without affecting ERP.
- AI requests are permissioned, minimized, schema-validated and auditable; no real provider evidence is claimed until actually run.
- Every Site has current encrypted backup, tested restore, certificate/health monitoring and documented rollback.
- Unknown hosts are rejected; privileged users use 2FA; public login is rate-limited and HTTPS-only.
- 3–5 companies complete independent acceptance with no P0/P1 defect.
- Actual monthly infrastructure cost remains within the approved pilot budget or is re-approved before expansion.

