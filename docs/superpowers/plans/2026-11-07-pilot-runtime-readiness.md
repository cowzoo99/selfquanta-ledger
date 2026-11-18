# SelfLedger Pilot Runtime Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不修改现有 WireGuard 生产系统的前提下，为同一个候选 Git SHA 生成不可变 App/OCR 镜像，完成真实 Nginx 容器校验、加密备份与隔离恢复、单 Site 升级及镜像回滚演练，并形成可审计的 `GO/NO-GO` 证据。

**Architecture:** 继续使用现有 Frappe/ERPNext/HRMS、`internal_ledger`、Docker Compose 与 Nginx。所有演练只使用 `acceptance-a.localhost`、独立 Compose 项目、独立卷、独立密钥和回环地址端口；不得读取或挂载现有生产 Site、生产卷、生产运行时文件或生产备份密钥。现有脚本已经覆盖构建、备份、恢复和配置门禁，本阶段优先执行并留证，只在真实失败暴露脚本缺陷时写最小回归测试和修复。

**Tech Stack:** Frappe `v16.52.0`、ERPNext `v16.52.1`、HRMS `v16.52.0`、`frappe_docker` `v4.1.1` / `d84154ae23ab7ae756dfe942e3f24dcd70d1fbda`、Docker Engine、Compose v2、PowerShell 7、OpenSSL、Nginx `1.28.0-alpine`。

**Spec:** `docs/superpowers/specs/2026-11-06-selfledger-public-erp-platform-design.md`

## Assumptions and hard boundaries

- 当前 `main` 基线和标签 `public-erp-mvp-baseline-2026-11-07` 已推送；本计划只在 `release/pilot-readiness` 执行。
- 现有 WireGuard 地址、生产 Compose 项目、生产数据库、生产卷、生产证书和生产 systemd 服务一律不读取、不停止、不重启、不迁移。
- 本机 E 盘当前可用空间约 2.3 GB，不满足镜像构建和恢复演练要求；不得把 Docker 构建缓存、镜像层或恢复卷放到 E 盘。
- 候选构建主机的 `/srv/selfquanta/pilot-readiness` 所在文件系统必须至少有 30 GB 可用空间，并在构建后按真实镜像、缓存和恢复卷大小再次校验；不满足时停止，不自动清理其他项目或 Docker 全局资源。
- 本阶段关闭的是内部可执行门禁。真实 ICP、DNS、公网 TLS、云防火墙、真实客户授权和签字仍是外部 `PENDING`，不得伪造或用合成证据替代。
- 如命令输出可能含密码、密钥、银行内容、完整客户数据或 Site 配置，证据只记录退出码、SHA、镜像 ID、计数和脱敏结论。

## Success criteria

1. App 与 OCR 镜像都使用同一个非零 40 位候选 SHA，镜像存在且 App revision label、运行时资产和版本检查通过。
2. `nginx:1.28.0-alpine` 实际容器完成模板渲染和 `nginx -t`；未知 Host 返回 `421`，批准 Host 的 HTTP 只重定向到 HTTPS。
3. `acceptance-a.localhost` 产生真实 AES-256 加密归档和 schema 2 清单；全新 `ledger-restore-*` 项目恢复后生成 `status: PASS` 报告并匹配关键计数。
4. 候选 Site 完成 maintenance → candidate migrate → 两阶段只读 smoke → previous image rollback → smoke；过程中不接纳普通用户、不上传真实流水。
5. `docs/acceptance/2026-11-06-public-erp-integrated-checklist.md` 记录实际命令、SHA、结果和仍阻塞的外部条件；无证据的项目保持 `BLOCKED/PENDING`。
6. 现有生产系统的容器、卷、运行时配置和 WireGuard 服务在前后清单中一致。

### Task 1: Freeze the candidate and prove the isolated baseline

**Files:**
- Create: `docs/acceptance/2026-11-07-pilot-runtime-readiness.md`
- Verify: `internal_ledger/tests/frontend_review.cjs`
- Verify: `infra/test-config.ps1`

**Interfaces:**
- 候选标签固定为 `selfledger-pilot-rc1-20261118`。
- 验收记录只保存 Git SHA、版本、测试计数、镜像 ID/摘要、项目名、时间和状态，不保存密钥或业务正文。

- [ ] 在 `release/pilot-readiness` 提交本计划后，运行一次集中基线：

```powershell
$repo = 'E:\杭州逆熵云\记账\.worktrees\pilot-readiness'
git -C $repo status --short --branch
git -C $repo rev-parse HEAD
pwsh -NoProfile -File "$repo\infra\test-config.ps1" -StaticOnly
wsl -d Ubuntu-24.04 -- bash -lc 'cd /opt/internal-ledger-bench && node apps/internal_ledger/internal_ledger/tests/frontend_review.cjs'
```

Expected: 工作树干净；两项检查退出 `0`。不重复运行已经在同一 SHA 通过的 11 分钟数据库全套。

- [ ] 创建验收记录，写明 `release/pilot-readiness`、候选 SHA、当前 `NO-GO` 三个内部阻塞和两个外部阻塞。
- [ ] 运行 Git 历史高风险文件名与内容扫描；只记录候选文件数量，不输出匹配内容：

```powershell
$repo = 'E:\杭州逆熵云\记账\.worktrees\pilot-readiness'
git -C $repo ls-files
git -C $repo log --all --format=%H
rg -l -i "BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|AKID[A-Za-z0-9]{13,}|Secret(Id|Key)\s*[:=]|password\s*[:=]" $repo -g '!site-admin-password.txt' -g '!*.conf'
```

Expected: 无被 Git 跟踪的私钥、云密钥或真实密码；如有候选，先人工判定并清除，再继续。

- [ ] 提交验收记录并推送分支，然后创建并原子推送候选标签：

```powershell
$repo = 'E:\杭州逆熵云\记账\.worktrees\pilot-readiness'
git -C $repo add docs/acceptance/2026-11-07-pilot-runtime-readiness.md
git -C $repo commit -m "docs: start pilot runtime readiness evidence"
git -C $repo push -u origin release/pilot-readiness
git -C $repo tag -a selfledger-pilot-rc1-20261118 -m "SelfLedger pilot runtime candidate"
git -C $repo push --atomic origin release/pilot-readiness selfledger-pilot-rc1-20261118
```

Expected: 远端标签解析出的 peeled SHA 与本地 `HEAD` 完全相同。

### Task 2: Build and inspect immutable App/OCR images

**Files:**
- Verify: `infra/build_image.ps1`
- Verify: `infra/versions.env`
- Verify: `ocr_service/Dockerfile`
- Modify only on reproduced defect: `infra/test-config.ps1`, the failing script, and its focused documentation

**Interfaces:**
- 候选输出镜像名由 `internal-ledger:` / `internal-ledger-ocr:` 加候选 40 位 SHA 组成；上一版镜像用同样规则绑定 baseline 40 位 SHA。
- 构建目录为 `/srv/selfquanta/pilot-readiness`；生产目录 `/srv/selfquanta/ledger` 禁止作为本任务工作目录。

- [ ] 在获批的隔离 Linux 构建主机执行容量、Docker 与网络前置检查：

```bash
set -euo pipefail
test "$(df --output=avail -B1 /srv | tail -1)" -ge 32212254720
docker version
docker compose version
git ls-remote https://github.com/cowzoo99/selfquanta-ledger.git refs/tags/selfledger-pilot-rc1-20261118
git ls-remote https://github.com/frappe/frappe_docker.git refs/tags/v4.1.1
```

Expected: `/srv` 可用空间至少 30 GB，Docker/Compose 可用，两个远端标签可读取；构建后必须按真实占用再次判断是否足以执行恢复演练。失败即停止；不得运行 `docker system prune` 或删除其他项目。

- [ ] 获取固定源码；已有目录必须是干净、同源目录，否则停止，不覆盖：

```bash
set -euo pipefail
sudo install -d -m 0750 /srv/selfquanta/pilot-readiness
sudo chown "$(id -u):$(id -g)" /srv/selfquanta/pilot-readiness
git clone https://github.com/cowzoo99/selfquanta-ledger.git /srv/selfquanta/pilot-readiness/ledger
git clone --branch v4.1.1 --single-branch https://github.com/frappe/frappe_docker.git /srv/selfquanta/pilot-readiness/frappe_docker
git -C /srv/selfquanta/pilot-readiness/frappe_docker checkout --detach d84154ae23ab7ae756dfe942e3f24dcd70d1fbda
git -C /srv/selfquanta/pilot-readiness/ledger fetch --tags origin
git -C /srv/selfquanta/pilot-readiness/ledger checkout --detach selfledger-pilot-rc1-20261118
test -z "$(git -C /srv/selfquanta/pilot-readiness/ledger status --porcelain)"
test -z "$(git -C /srv/selfquanta/pilot-readiness/frappe_docker status --porcelain)"
```

- [ ] 先构建 baseline 的 App/OCR 镜像，作为真实 previous-image 回滚目标：

```bash
set -euo pipefail
cd /srv/selfquanta/pilot-readiness/ledger
git checkout --detach public-erp-mvp-baseline-2026-11-07
export RELEASE_COMMIT="$(git rev-parse HEAD)"
export INTERNAL_LEDGER_APP_REF=public-erp-mvp-baseline-2026-11-07
export FRAPPE_DOCKER_DIR=/srv/selfquanta/pilot-readiness/frappe_docker
export INTERNAL_LEDGER_BUILD_TEMP=/srv/selfquanta/pilot-readiness/build
pwsh -NoProfile -File infra/build_image.ps1 -ValidateOnly
pwsh -NoProfile -File infra/build_image.ps1
docker build --tag "internal-ledger-ocr:$RELEASE_COMMIT" ocr_service
docker image inspect "internal-ledger:$RELEASE_COMMIT" "internal-ledger-ocr:$RELEASE_COMMIT" >/dev/null
```

- [ ] 再构建候选 App/OCR 镜像并运行真实运行时资产门禁：

```bash
set -euo pipefail
cd /srv/selfquanta/pilot-readiness/ledger
git checkout --detach selfledger-pilot-rc1-20261118
export RELEASE_COMMIT="$(git rev-parse HEAD)"
export INTERNAL_LEDGER_APP_REF=selfledger-pilot-rc1-20261118
export FRAPPE_DOCKER_DIR=/srv/selfquanta/pilot-readiness/frappe_docker
export INTERNAL_LEDGER_BUILD_TEMP=/srv/selfquanta/pilot-readiness/build
pwsh -NoProfile -File infra/build_image.ps1 -ValidateOnly
pwsh -NoProfile -File infra/build_image.ps1
docker build --tag "internal-ledger-ocr:$RELEASE_COMMIT" ocr_service
docker image inspect "internal-ledger:$RELEASE_COMMIT" --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker image inspect "internal-ledger-ocr:$RELEASE_COMMIT" --format '{{.Id}}'
docker run --rm --entrypoint python "internal-ledger:$RELEASE_COMMIT" -c 'import json; from pathlib import Path; root=Path("/home/frappe/frappe-bench"); entries=json.loads((root/"assets/assets.json").read_text()); assert len(entries)>=57; assert entries["desk.bundle.js"].endswith(".js"); assert entries["ledger_workbench.bundle.js"].endswith(".js"); assert all((root/v.lstrip("/")).resolve().is_relative_to(root) and (root/v.lstrip("/")).is_file() for v in entries.values()); print("PASS", len(entries))'
```

Expected: 两个镜像标签使用同一 SHA；App label 等于该 SHA；Desk 和 SelfLedger bundle 均存在。镜像 ID 和资产条数写入验收记录。

- [ ] 若失败，先用最小测试在 `infra/test-config.ps1` 或对应脚本复现，再修复唯一根因；重跑本任务，不顺带改业务代码。
- [ ] 修复提交格式：`fix(ops): repair immutable candidate build gate`。

### Task 3: Validate the real Nginx container and Host boundary

**Files:**
- Verify: `infra/nginx.conf`
- Verify: `infra/compose.yaml`
- Modify only on reproduced defect: `infra/nginx.conf`, `infra/test-config.ps1`

**Interfaces:**
- 使用真实 `nginx:1.28.0-alpine`，不是正则夹具。
- 只绑定回环地址的临时端口 `18080/18443`，不修改 UFW、云防火墙、DNS 或 WireGuard。

- [ ] 拉取固定 Nginx 镜像，并在独立 acceptance Compose 项目启动完整候选栈前运行 `docker compose config --quiet`：

```bash
set -euo pipefail
cd /srv/selfquanta/pilot-readiness/ledger
docker pull nginx:1.28.0-alpine
sudo install -d -m 0700 /etc/selfquanta/pilot-readiness /var/backups/selfquanta/pilot-readiness /var/lib/selfquanta/pilot-restore-reports
sudo openssl rand -out /etc/selfquanta/pilot-readiness/db-root-password 48
sudo openssl rand -out /etc/selfquanta/pilot-readiness/site-admin-password 48
sudo openssl rand -out /etc/selfquanta/pilot-readiness/backup.key 64
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj '/CN=acceptance-a.localhost' -addext 'subjectAltName=DNS:acceptance-a.localhost' -keyout /etc/selfquanta/pilot-readiness/tls-private-key.pem -out /etc/selfquanta/pilot-readiness/tls-certificate.pem >/dev/null 2>&1
sudo chmod 0600 /etc/selfquanta/pilot-readiness/*
export RELEASE_COMMIT="$(git rev-parse HEAD)"
export INTERNAL_LEDGER_IMAGE="internal-ledger:$RELEASE_COMMIT"
export OCR_IMAGE="internal-ledger-ocr:$RELEASE_COMMIT"
export SITE_NAME=acceptance-a.localhost
export COMPOSE_PROJECT_NAME=selfledger_pilot_acceptance
export HTTP_BIND=127.0.0.1 HTTPS_BIND=127.0.0.1 HTTP_PORT=18080 HTTPS_PORT=18443
export DB_ROOT_PASSWORD_FILE=/etc/selfquanta/pilot-readiness/db-root-password
export SITE_ADMIN_PASSWORD_FILE=/etc/selfquanta/pilot-readiness/site-admin-password
export TLS_CERTIFICATE_FILE=/etc/selfquanta/pilot-readiness/tls-certificate.pem
export TLS_PRIVATE_KEY_FILE=/etc/selfquanta/pilot-readiness/tls-private-key.pem
docker compose --env-file infra/versions.env -f infra/compose.yaml config --quiet
```

Expected: 配置成功，只有 gateway 发布 `127.0.0.1:18080` 和 `127.0.0.1:18443`；OCR、数据库和 Redis 无宿主端口。

- [ ] 启动候选栈后检查真实渲染配置和 Host 行为：

```bash
docker compose --env-file infra/versions.env -f infra/compose.yaml up -d
docker compose --env-file infra/versions.env -f infra/compose.yaml exec -T gateway nginx -t
test "$(curl -sk -o /dev/null -w '%{http_code}' -H 'Host: unknown.invalid' https://127.0.0.1:18443/)" = 421
test "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: acceptance-a.localhost' http://127.0.0.1:18080/)" = 308
```

Expected: `nginx -t` 成功，未知 Host `421`，合法 Host HTTP `308`。结果写入验收记录。

### Task 4: Produce a real encrypted backup and isolated restore PASS

**Files:**
- Verify: `infra/backup.ps1`
- Verify: `infra/restore-check.ps1`
- Verify: `docs/operations/backup-restore.md`
- Modify only on reproduced defect: the failing script, `infra/test-config.ps1`, and runbook

**Interfaces:**
- acceptance 项目：`selfledger_pilot_acceptance`。
- restore 项目：`ledger-restore-pilot-20261107`。
- 密钥、归档和报告均位于 `/etc/selfquanta/pilot-readiness` 或 `/var/backups/selfquanta/pilot-readiness`，不进入 Git。

- [ ] 确认 Task 3 的 root-only 密钥和测试证书存在且权限为 `0600`，然后首次创建 acceptance Site，并只写入合成验收数据：

```bash
cd /srv/selfquanta/pilot-readiness/ledger
docker compose --env-file infra/versions.env -f infra/compose.yaml --profile init run --rm create-site
docker compose --env-file infra/versions.env -f infra/compose.yaml up -d
docker compose --env-file infra/versions.env -f infra/compose.yaml exec -T backend bench --site acceptance-a.localhost execute 'frappe.db.sql("select 1")'
docker compose --env-file infra/versions.env -f infra/compose.yaml exec -T backend bench --site acceptance-a.localhost execute 'frappe.db.exists("Workspace", "SelfLedger 企业经营与智能内账")'
```

Expected: Site 创建一次且只创建一次；数据库与统一 Workspace 只读检查成功。

- [ ] 生成加密备份并从清单自动选择本轮归档：

```bash
export BACKUP_KEY_FILE=/etc/selfquanta/pilot-readiness/backup.key
pwsh -NoProfile -File infra/backup.ps1 -Destination /var/backups/selfquanta/pilot-readiness -KeyFile "$BACKUP_KEY_FILE" -Site acceptance-a.localhost -Project selfledger_pilot_acceptance
export INTERNAL_LEDGER_BACKUP_MANIFEST="$(find /var/backups/selfquanta/pilot-readiness -type f -name '*.manifest.json' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
export INTERNAL_LEDGER_BACKUP_ARCHIVE="${INTERNAL_LEDGER_BACKUP_MANIFEST%.manifest.json}.zip.enc"
test -s "$INTERNAL_LEDGER_BACKUP_ARCHIVE"
test -s "$INTERNAL_LEDGER_BACKUP_MANIFEST"
```

Expected: 归档为 `.zip.enc`，同名 manifest 为 schema 2，SHA-256 校验可重复。

- [ ] 用全新项目实际恢复，不以 `-ValidateOnly` 代替：

```bash
export INTERNAL_LEDGER_RESTORE_PROJECT=ledger-restore-pilot-20261107
export INTERNAL_LEDGER_RESTORE_REPORT_DIR=/var/lib/selfquanta/pilot-restore-reports
pwsh -NoProfile -File infra/restore-check.ps1 -Archive "$INTERNAL_LEDGER_BACKUP_ARCHIVE" -Manifest "$INTERNAL_LEDGER_BACKUP_MANIFEST" -KeyFile "$BACKUP_KEY_FILE" -Project "$INTERNAL_LEDGER_RESTORE_PROJECT" -ReportDirectory "$INTERNAL_LEDGER_RESTORE_REPORT_DIR" -ValidateOnly
pwsh -NoProfile -File infra/restore-check.ps1 -Archive "$INTERNAL_LEDGER_BACKUP_ARCHIVE" -Manifest "$INTERNAL_LEDGER_BACKUP_MANIFEST" -KeyFile "$BACKUP_KEY_FILE" -Project "$INTERNAL_LEDGER_RESTORE_PROJECT" -ReportDirectory "$INTERNAL_LEDGER_RESTORE_REPORT_DIR"
jq -e '.status == "PASS"' "$(find "$INTERNAL_LEDGER_RESTORE_REPORT_DIR" -type f -name '*.json' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
```

Expected: `PASS` 报告包含 source manifest、候选 SHA 和 schema 2 八类 expected/actual 计数；报告路径和脱敏计数写入验收记录。

### Task 5: Exercise one-Site candidate upgrade and previous-image rollback

**Files:**
- Verify: `docs/operations/deploy.md`
- Verify: `infra/compose.yaml`
- Modify only on reproduced defect: focused deployment script/test/runbook

**Interfaces:**
- 只操作 `selfledger_pilot_acceptance`。
- previous image 必须是演练前实际记录的不可变镜像 ID/SHA，不允许 `latest`。
- 数据恢复只在迁移不兼容或只读 smoke 证明数据损坏时使用；普通代码回滚不自动覆盖数据。

- [ ] 在演练记录中先写入 previous SHA、candidate SHA、备份 manifest、项目名和操作者，再进入维护模式。
- [ ] 执行现有 runbook 的顺序：停止调度器 → 加密备份成功 → 停止 Web 服务 → candidate 一次性 migrate → 切换候选镜像 → maintenance 下容器/资产/数据库只读检查 → 关闭 maintenance → 私有 HTTP 只读 smoke。
- [ ] 只读 smoke 至少验证：`/api/method/ping`、命名用户登录、统一 Workspace、Company、启用叶子科目、工作台计数、总账只读报告和审计历史；不上传文件、不批准过账、不写合成会计记录。
- [ ] 恢复 previous SHA 镜像，重启同一 acceptance 项目并重复上述只读 smoke。
- [ ] 任一步失败：立即重新开启 maintenance；如果 backend 无法执行，则先停止 acceptance gateway；保留项目和日志供诊断，不触碰生产项目。

Expected: 候选升级和 previous-image 回滚两次 smoke 都通过；候选与 previous 镜像 ID、迁移退出码、smoke 结果和是否发生数据恢复写入验收记录。

### Task 6: Close the internal gates and make an honest release decision

**Files:**
- Modify: `docs/acceptance/2026-11-07-pilot-runtime-readiness.md`
- Modify: `docs/acceptance/2026-11-06-public-erp-integrated-checklist.md`
- Modify if command drift was found: `docs/operations/deploy.md`, `docs/operations/backup-restore.md`

**Interfaces:**
- 内部门禁只能由本轮真实证据从 `BLOCKED/PENDING` 改为 `PASS`。
- ICP/DNS/TLS、公网外部 smoke 和客户签字保持 `PENDING`，直到真实完成。

- [ ] 更新综合清单：不可变镜像、真实 Nginx、真实加密恢复、单 Site 升级/回滚分别引用本轮证据。
- [ ] 记录前后生产资源指纹清单，确认现有 WireGuard 生产容器、卷、运行时配置和服务未改变；不得记录秘密值。
- [ ] 运行本阶段集中回归：

```powershell
$repo = 'E:\杭州逆熵云\记账\.worktrees\pilot-readiness'
pwsh -NoProfile -File "$repo\infra\test-config.ps1" -StaticOnly
wsl -d Ubuntu-24.04 -- bash -lc 'cd /opt/internal-ledger-bench && node apps/internal_ledger/internal_ledger/tests/frontend_review.cjs'
git -C $repo diff --check
git -C $repo status --short
```

Expected: 两项门禁退出 `0`，`diff --check` 无输出，只包含本阶段计划内文件。

- [ ] 提交并推送证据：

```powershell
$repo = 'E:\杭州逆熵云\记账\.worktrees\pilot-readiness'
git -C $repo add docs/acceptance/2026-11-07-pilot-runtime-readiness.md docs/acceptance/2026-11-06-public-erp-integrated-checklist.md docs/operations/deploy.md docs/operations/backup-restore.md infra
git -C $repo diff --cached --name-only
git -C $repo commit -m "ops: prove pilot runtime readiness"
git -C $repo push origin release/pilot-readiness
```

Expected: staged file列表必须逐项可追溯到本计划；没有脚本缺陷时不得为了“有代码”而修改 `infra` 或 runbook。

## Final decision rule

- 内部四项全部 `PASS` 且 P0/P1 为零：结论可写为“运行时试点候选已准备，公网与客户门禁仍待完成”，不能写“已公网发布”。
- 任一镜像、Nginx、恢复或回滚项失败：保持 `NO-GO`，先修复该主链路，禁止转去做 AI、支付、自助注册或 UI 美化。
- 真实公网发布仍需另一个经批准的切换计划，必须包含 ICP、DNS、TLS、外部网络验证、云防火墙、实名账号与 2FA、3–5 家客户授权和逐 Site 签字。
