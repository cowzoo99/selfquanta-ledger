# SelfQuanta Production Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前单公司、单账套协同内账系统发布到腾讯云上海轻量应用服务器，先通过 WireGuard 私网供少量人员使用，再在 ICP 备案通过后切换到 `https://ledger.selfquanta.com`，并完成重启、加密备份和隔离恢复验收。

**Architecture:** 保留现有 Frappe/ERPNext Docker Compose 架构和固定版本镜像构建链。生产主机使用 `/srv/selfquanta/ledger` 运行一个独立 Compose 项目，秘密保存在 `/etc/selfquanta/ledger`，备份落到 `/var/backups/selfquanta/ledger` 后同步到 COS。备案前网关只绑定 WireGuard 地址 `10.77.0.1`；备案后才绑定公网端口并启用公开证书。

**Tech Stack:** Ubuntu 24.04 LTS、Docker Engine/Compose v2、PowerShell 7、WireGuard、systemd、OpenSSL、Tencent COSCLI、Frappe v16.52.0、ERPNext v16.52.1、MariaDB 11.8、Redis 8.2-alpine。

**Spec:** `docs/superpowers/specs/2026-10-29-selfquanta-production-deployment-design.md`

## Global Constraints

- 本计划只针对首台上海主节点和当前内账；不增加 Kubernetes、CloudBase 重构、多租户共享数据库或复杂 CI/CD。
- GitHub 公共仓库固定为 `https://github.com/cowzoo99/selfquanta-ledger.git`；仓库中不得出现服务器 IP、真实账单、客户数据、密码、密钥、证书私钥或营业执照敏感字段。
- 本地项目继续位于 `E:\杭州逆熵云\记账`；SSH 私钥、WireGuard 客户端配置和云凭据放在 `E:\杭州逆熵云\运维密钥\selfquanta`，不得写入 C 盘或项目仓库。
- 生产目录固定为 `/srv/selfquanta/ledger`，固定依赖目录为 `/srv/selfquanta/frappe_docker`，构建临时目录为 `/srv/selfquanta/build`。
- 生产秘密目录固定为 `/etc/selfquanta/ledger`，备份目录固定为 `/var/backups/selfquanta/ledger`；秘密文件权限为 `0600`，目录权限为 `0700`。
- 镜像标签必须是完整 40 位 Git SHA；不得使用 `latest`。只有全部生产验收通过后才能创建 `v0.1.0`。
- 备案前不得开放公网 80/443，也不得用未备案域名对公网提供真实账务服务。
- 每个任务只修改列出的文件；发现无关问题只记录，不顺手重构。

---

## Task 1: Complete Owner-Controlled Cloud and Repository Prerequisites

**Files:**
- Verify: `docs/superpowers/specs/2026-10-29-selfquanta-production-deployment-design.md`
- No repository changes

- [ ] **Step 1: Complete Tencent Cloud enterprise identity verification**

  由所有者在腾讯云控制台使用已确认的企业主体完成实名认证，开启登录保护和费用告警。实施人员不得复制营业执照编号、负责人证件或控制台口令到聊天、终端日志或仓库。

- [ ] **Step 2: Purchase the domain and server**

  在购买页再次确认 `selfquanta.com` 可注册后，以同一企业主体购买域名；购买上海地域 Ubuntu 24.04 LTS 轻量应用服务器，规格为 4 核、8 GB、120 GB SSD、10 Mbps。记录公网 IP 到密码管理器，不写入仓库。

- [ ] **Step 3: Create the empty public GitHub repository**

  在 GitHub 账号 `cowzoo99` 下创建公共仓库 `selfquanta-ledger`，不要勾选自动生成 README、License 或 `.gitignore`，以免形成冲突历史。

- [ ] **Step 4: Create local key storage off C drive**

  在 PowerShell 中执行：

  ```powershell
  New-Item -ItemType Directory -Force -Path 'E:\杭州逆熵云\运维密钥\selfquanta' | Out-Null
  ssh-keygen -t ed25519 -a 64 -f 'E:\杭州逆熵云\运维密钥\selfquanta\selfquanta-prod-ed25519' -C 'selfquanta-production'
  ```

  将公钥加入服务器的非 root 运维账号；私钥不得通过聊天发送。

- [ ] **Step 5: Verify the external gate**

  成功标准：企业账号可登录、域名订单完成、服务器状态为运行中、SSH 公钥登录成功、GitHub 空仓库 URL 可访问。任一条件未满足时停在此任务，不编造 IP、凭据或购买状态。

---

## Task 2: Publish the Existing History to the Exact GitHub Origin

**Files:**
- Verify: `.git/config` (Git metadata only; never commit it)
- Verify: `infra/build_image.ps1`

- [ ] **Step 1: Confirm the local tree is clean and the design commit is present**

  Run:

  ```powershell
  Set-Location 'E:\杭州逆熵云\记账'
  git status --short
  git log -1 --oneline
  ```

  Expected: no status output; HEAD contains the approved deployment design and this implementation plan.

- [ ] **Step 2: Configure the exact credential-free HTTPS origin**

  Run:

  ```powershell
  git remote add origin https://github.com/cowzoo99/selfquanta-ledger.git
  git remote get-url origin
  git push -u origin main
  ```

  If `origin` already exists, first verify it. Only when it is not the exact URL above, run `git remote set-url origin https://github.com/cowzoo99/selfquanta-ledger.git` and recheck it. Do not embed a token in the URL.

- [ ] **Step 3: Verify local and remote SHAs are identical**

  Run:

  ```powershell
  $localSha = git rev-parse HEAD
  $remoteSha = (git ls-remote origin refs/heads/main).Split("`t")[0]
  if ($localSha -ne $remoteSha) { throw 'origin/main does not match local HEAD' }
  ```

  Expected: no exception and both values are 40 hexadecimal characters.

- [ ] **Step 4: Commit**

  No new commit is expected for remote configuration. Preserve the exact local history and record the verified SHA in the password manager/deployment record, not in a source file.

---

## Task 3: Add Production Runtime Contracts and Static Tests

**Files:**
- Create: `infra/runtime.env.example`
- Modify: `infra/test-config.ps1`
- Test: `infra/test-config.ps1`

- [ ] **Step 1: Write failing static assertions first**

  Add `runtime.env.example` to `$required` and assert these exact non-secret contracts:

  ```powershell
  $runtime = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'runtime.env.example')
  Assert-True ($runtime -match '(?m)^SITE_NAME=ledger\.selfquanta\.com$') 'Runtime site name is wrong'
  Assert-True ($runtime -match '(?m)^COMPOSE_PROJECT_NAME=selfquanta-ledger$') 'Runtime project name is wrong'
  Assert-True ($runtime -match '(?m)^HTTP_BIND=10\.77\.0\.1$') 'Private HTTP bind is wrong'
  Assert-True ($runtime -match '(?m)^HTTPS_BIND=10\.77\.0\.1$') 'Private HTTPS bind is wrong'
  Assert-True ($runtime -match '/etc/selfquanta/ledger/db-root-password') 'Database secret path is wrong'
  Assert-True ($runtime -match '/etc/selfquanta/ledger/site-admin-password') 'Admin secret path is wrong'
  Assert-True ($runtime -notmatch '(?im)(secret|password|token)=.+') 'Runtime example must not contain secret values'
  ```

  Run: `pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly`

  Expected: FAIL with `Missing required file: runtime.env.example`.

- [ ] **Step 2: Create the minimal non-secret runtime template**

  The file must contain only these deployment settings and paths:

  ```dotenv
  INTERNAL_LEDGER_IMAGE=internal-ledger:0000000000000000000000000000000000000000
  SITE_NAME=ledger.selfquanta.com
  COMPOSE_PROJECT_NAME=selfquanta-ledger
  DB_ROOT_PASSWORD_FILE=/etc/selfquanta/ledger/db-root-password
  SITE_ADMIN_PASSWORD_FILE=/etc/selfquanta/ledger/site-admin-password
  TLS_CERTIFICATE_FILE=/etc/selfquanta/ledger/internal-fullchain.pem
  TLS_PRIVATE_KEY_FILE=/etc/selfquanta/ledger/internal-privkey.pem
  HTTP_BIND=10.77.0.1
  HTTPS_BIND=10.77.0.1
  HTTP_PORT=80
  HTTPS_PORT=443
  INTERNAL_LEDGER_BACKUP_DESTINATION=/var/backups/selfquanta/ledger
  BACKUP_KEY_FILE=/etc/selfquanta/ledger/backup.key
  COSCLI_CONFIG_FILE=/etc/selfquanta/ledger/cos.yaml
  COS_BACKUP_URI=cos://selfquanta-ledger-backup/production/ledger/
  ```

  The all-zero SHA is a deliberately invalid example sentinel; installation must replace it with `git rev-parse HEAD` and validation must reject the all-zero value in the real runtime file.

- [ ] **Step 3: Add a mutation test for accidental secret values**

  Extend `New-MutatedInfra` coverage so a copied runtime template containing `SITE_ADMIN_PASSWORD=plaintext` fails with `Runtime example must not contain secret values`.

- [ ] **Step 4: Run tests**

  Run:

  ```powershell
  pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
  pwsh -NoProfile -File infra/test-config.ps1
  ```

  Expected: both commands end with the existing success messages and no scratch data remains in the repository.

- [ ] **Step 5: Commit**

  ```powershell
  git add infra/runtime.env.example infra/test-config.ps1
  git commit -m "test: define SelfQuanta production runtime contract"
  ```

---

## Task 4: Add an Idempotent Ubuntu Host Bootstrap Script

**Files:**
- Create: `infra/host/bootstrap-ubuntu.sh`
- Modify: `infra/test-config.ps1`
- Test: `infra/test-config.ps1`

- [ ] **Step 1: Add failing contract tests**

  Require `host/bootstrap-ubuntu.sh` and assert that it:

  - exits unless run as root on Ubuntu 24.04;
  - installs Docker from Docker's signed apt repository, Compose plugin, Git, OpenSSL, WireGuard, UFW, fail2ban and PowerShell 7;
  - enables `docker`, `fail2ban` and unattended security updates;
  - creates `/srv/selfquanta`, `/etc/selfquanta/ledger`, and `/var/backups/selfquanta/ledger` with the required permissions;
  - creates a named non-root operator supplied as the sole positional argument and adds it to the `docker` group;
  - never changes SSH password/root-login policy until a key login preflight succeeds;
  - contains no `curl | sh`, floating application version, secret, IP or destructive broad cleanup.

  Because `$required` now contains nested paths, update `New-MutatedInfra` to create each destination parent directory before `Copy-Item`; preserve the relative path instead of flattening files into the scratch root.

  Run: `pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly`

  Expected: FAIL because the script is missing.

- [ ] **Step 2: Implement the smallest idempotent bootstrap**

  Interface:

  ```bash
  sudo bash infra/host/bootstrap-ubuntu.sh selfquantaops
  ```

  Use `set -euo pipefail`, verify `/etc/os-release`, create apt keyrings with explicit repository files, call `apt-get update`, install only the listed packages, create directories with `install -d`, and print a final version summary. Re-running the command must not create duplicate repository entries or fail because the user/directories already exist.

- [ ] **Step 3: Add syntax and negative checks**

  On Linux/WSL run `bash -n infra/host/bootstrap-ubuntu.sh`. In PowerShell static tests, mutate the script to add `curl https://example.invalid/install.sh | sh` and assert rejection.

- [ ] **Step 4: Run tests and commit**

  ```powershell
  pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
  git add infra/host/bootstrap-ubuntu.sh infra/test-config.ps1
  git commit -m "feat: add Ubuntu production host bootstrap"
  ```

---

## Task 5: Add WireGuard-Only Access and Internal TLS Setup

**Files:**
- Create: `infra/host/configure-private-access.sh`
- Modify: `infra/test-config.ps1`
- Test: `infra/test-config.ps1`

- [ ] **Step 1: Add failing security assertions**

  Require the script and assert these invariants: WireGuard server address `10.77.0.1/24`, UDP port `51820`, one client per invocation, keys generated outside Git, UFW allows SSH and 51820/UDP but not public 80/443, and the certificate SAN contains both `IP:10.77.0.1` and `DNS:ledger.selfquanta.com`.

  Run the static test and expect a missing-file failure.

- [ ] **Step 2: Implement the exact interface**

  ```bash
  sudo bash infra/host/configure-private-access.sh laptop-cao
  ```

  The script must:

  1. create `/etc/wireguard/wg0.conf` with `0600` permissions if absent;
  2. allocate the next free client address from `10.77.0.2` through `10.77.0.254` and refuse duplicate client names;
  3. write the client config to `/etc/selfquanta/ledger/wireguard-clients/laptop-cao.conf` with `0600` permissions;
  4. generate an internal CA and a server certificate under `/etc/selfquanta/ledger` without exposing private keys in output;
  5. enable `wg-quick@wg0`, enable UFW, and leave 80/443 closed on the public interface;
  6. print only the client config path and CA certificate path, never private key content.

- [ ] **Step 3: Add mutation tests**

  Mutate a copy to `HTTP_BIND=0.0.0.0` or `ufw allow 443/tcp`; the static suite must fail with a pre-ICP public exposure error.

- [ ] **Step 4: Verify and commit**

  ```powershell
  pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
  git add infra/host/configure-private-access.sh infra/test-config.ps1
  git commit -m "feat: add private production access setup"
  ```

---

## Task 6: Add systemd Lifecycle Units

**Files:**
- Create: `infra/systemd/selfquanta-ledger.service`
- Create: `infra/host/install-systemd-units.sh`
- Modify: `infra/test-config.ps1`
- Test: `infra/test-config.ps1`

- [ ] **Step 1: Add failing unit-contract tests**

  Require both files and assert that the service uses:

  ```ini
  WorkingDirectory=/srv/selfquanta/ledger
  EnvironmentFile=/etc/selfquanta/ledger/runtime.env
  ExecStartPre=/usr/bin/pwsh -NoProfile -File /srv/selfquanta/ledger/infra/test-config.ps1 -StaticOnly
  ExecStart=/usr/bin/docker compose --env-file /srv/selfquanta/ledger/infra/versions.env -f /srv/selfquanta/ledger/infra/compose.yaml up -d --remove-orphans
  ExecStop=/usr/bin/docker compose --env-file /srv/selfquanta/ledger/infra/versions.env -f /srv/selfquanta/ledger/infra/compose.yaml stop
  RemainAfterExit=yes
  ```

  Also assert `After=docker.service wg-quick@wg0.service network-online.target` and `Requires=docker.service wg-quick@wg0.service`.

- [ ] **Step 2: Implement the unit and installer**

  The installer must copy only the named units into `/etc/systemd/system`, set root ownership, run `systemctl daemon-reload`, and enable but not start the ledger service. It must reject execution when `/etc/selfquanta/ledger/runtime.env` is missing or still contains the SHA sentinel.

- [ ] **Step 3: Verify locally and on Ubuntu**

  Run the PowerShell static suite. On the server run:

  ```bash
  sudo systemd-analyze verify /etc/systemd/system/selfquanta-ledger.service
  ```

  Expected: exit code 0 and no unknown directive/error.

- [ ] **Step 4: Commit**

  ```powershell
  git add infra/systemd/selfquanta-ledger.service infra/host/install-systemd-units.sh infra/test-config.ps1
  git commit -m "feat: manage ledger lifecycle with systemd"
  ```

---

## Task 7: Add COS Upload and Daily Backup Scheduling

**Files:**
- Create: `infra/backup-and-upload.ps1`
- Create: `infra/systemd/selfquanta-ledger-backup.service`
- Create: `infra/systemd/selfquanta-ledger-backup.timer`
- Modify: `infra/host/install-systemd-units.sh`
- Modify: `infra/test-config.ps1`
- Test: `infra/test-config.ps1`

- [ ] **Step 1: Write failing tests using a fake COSCLI executable**

  Add static assertions and executable tests for this interface:

  ```powershell
  pwsh -NoProfile -File infra/backup-and-upload.ps1 `
    -Destination /var/backups/selfquanta/ledger `
    -CosUri cos://selfquanta-ledger-backup/production/ledger/ `
    -CosConfig /etc/selfquanta/ledger/cos.yaml
  ```

  Test these cases without network access:

  - backup failure prevents COSCLI from running;
  - missing/non-absolute destination or config path is rejected;
  - non-`cos://` destination is rejected;
  - COSCLI nonzero exit is propagated;
  - successful path calls `coscli sync <local-root> <cos-uri> --recursive -c <config>` exactly once;
  - output never contains key material or config contents.

  Run the full infrastructure test and expect failure because the wrapper is missing.

- [ ] **Step 2: Implement a thin wrapper**

  The wrapper must call existing `backup.ps1` first and only then execute Tencent COSCLI `sync`. It must not duplicate encryption, retention or manifest logic. Sync the encrypted `.zip.enc` archives and sanitized `.manifest.json` sidecars; the archive itself remains the authoritative restore payload. Use `/usr/local/bin/coscli` by default and allow an explicit executable path only for tests.

- [ ] **Step 3: Add timer units**

  Use `OnCalendar=*-*-* 02:30:00 Asia/Shanghai`, `Persistent=true`, and `RandomizedDelaySec=10m`. The service must load `/etc/selfquanta/ledger/runtime.env`, run as root so it can read the key/config, set a restrictive umask, and use the wrapper. Do not place SecretId/SecretKey in the unit or environment file.

- [ ] **Step 4: Configure COS outside Git**

  On the server, install the official Linux amd64 COSCLI binary as `/usr/local/bin/coscli` and verify its published SHA-256 before installation. Create a private COS bucket in the Shanghai region and a dedicated CAM sub-user restricted to the backup prefix. Initialize `/etc/selfquanta/ledger/cos.yaml` interactively with the bucket alias `selfquanta-ledger-backup`, then set ownership `root:root` and mode `0600`.

  The expected upload command follows Tencent's documented form:

  ```bash
  sudo /usr/local/bin/coscli sync /var/backups/selfquanta/ledger cos://selfquanta-ledger-backup/production/ledger/ --recursive -c /etc/selfquanta/ledger/cos.yaml
  ```

- [ ] **Step 5: Verify and commit**

  ```powershell
  pwsh -NoProfile -File infra/test-config.ps1
  git add infra/backup-and-upload.ps1 infra/systemd infra/host/install-systemd-units.sh infra/test-config.ps1
  git commit -m "feat: schedule encrypted backups to COS"
  ```

  On Ubuntu additionally run `systemd-analyze verify` for both backup units.

---

## Task 8: Update Operations Documentation Before Touching Production

**Files:**
- Modify: `docs/operations/deploy.md`
- Modify: `docs/operations/backup-restore.md`
- Modify: `infra/test-config.ps1`

- [ ] **Step 1: Add failing documentation assertions**

  Require deployment documentation to name the exact paths, `main` as the pre-release app ref, WireGuard-only pre-ICP binding, systemd commands, and the public-cutover gate. Require backup documentation to name COSCLI config, timer inspection, encrypted-object verification and isolated restore evidence.

- [ ] **Step 2: Rewrite only obsolete deployment sections**

  Preserve existing security rationale. Replace the generic `/etc/internal-ledger` and `ledger.example.com` examples with the approved SelfQuanta paths/domain. Document two states explicitly:

  - pre-ICP: `HTTP_BIND=HTTPS_BIND=10.77.0.1`, no public 80/443;
  - post-ICP: public DNS/certificate and public bindings only after filing approval.

- [ ] **Step 3: Add exact operator commands**

  Include `systemctl start/status/restart selfquanta-ledger`, `journalctl -u`, timer inspection, manual backup trigger, COS object listing, restore validation, and rollback to the previous SHA image.

- [ ] **Step 4: Verify and commit**

  ```powershell
  pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
  rg -n "ledger\.example\.com|/etc/internal-ledger|TODO|TBD" docs/operations infra
  git add docs/operations infra/test-config.ps1
  git commit -m "docs: add SelfQuanta production runbook"
  ```

  Expected: static tests pass; the search finds no obsolete example or unfinished marker in the changed production material.

---

## Task 9: Bootstrap the Live Server and Build the Immutable Image

**Files:**
- Execute: committed scripts under `infra/host/`
- Create outside Git: `/etc/selfquanta/ledger/runtime.env` and secret files
- No source change unless a reproducible defect is first captured by a failing test

- [ ] **Step 1: Clone exact repositories and verify pins**

  ```bash
  sudo install -d -o selfquantaops -g selfquantaops /srv/selfquanta/ledger /srv/selfquanta/frappe_docker /srv/selfquanta/build
  git clone https://github.com/cowzoo99/selfquanta-ledger.git /srv/selfquanta/ledger
  git clone https://github.com/frappe/frappe_docker.git /srv/selfquanta/frappe_docker
  git -C /srv/selfquanta/frappe_docker checkout v4.1.1
  test "$(git -C /srv/selfquanta/frappe_docker rev-parse HEAD)" = d84154ae23ab7ae756dfe942e3f24dcd70d1fbda
  ```

- [ ] **Step 2: Run host bootstrap and private-access setup**

  Execute the committed scripts as root, transfer each generated client config and the internal CA certificate to `E:\杭州逆熵云\运维密钥\selfquanta`, then remove any transient transfer copy. Import the client config on each approved device and verify `ping 10.77.0.1` over WireGuard.

- [ ] **Step 3: Create production secrets outside Git**

  ```bash
  sudo install -d -m 0700 -o root -g root /etc/selfquanta/ledger
  sudo sh -c 'openssl rand -base64 48 > /etc/selfquanta/ledger/db-root-password'
  sudo sh -c 'openssl rand -base64 48 > /etc/selfquanta/ledger/site-admin-password'
  sudo sh -c 'openssl rand -out /etc/selfquanta/ledger/backup.key 64'
  sudo chmod 0600 /etc/selfquanta/ledger/db-root-password /etc/selfquanta/ledger/site-admin-password /etc/selfquanta/ledger/backup.key
  ```

  Store a separately protected copy of the backup key. Never display these files.

- [ ] **Step 4: Build the exact image from `main`**

  ```bash
  cd /srv/selfquanta/ledger
  export FRAPPE_DOCKER_DIR=/srv/selfquanta/frappe_docker
  export INTERNAL_LEDGER_APP_REF=main
  export INTERNAL_LEDGER_BUILD_TEMP=/srv/selfquanta/build
  pwsh -NoProfile -File infra/test-config.ps1
  pwsh -NoProfile -File infra/build_image.ps1
  ```

  Expected: `internal-ledger:<40-character-HEAD-SHA>` exists and `bench version` succeeds.

- [ ] **Step 5: Materialize and validate runtime configuration**

  Copy `runtime.env.example` to `/etc/selfquanta/ledger/runtime.env`, replace only the image sentinel with the exact HEAD SHA, set mode `0600`, and run `docker compose ... config --quiet`. Confirm both published sockets bind `10.77.0.1`, not `0.0.0.0`.

---

## Task 10: Initialize, Start, and Accept the Private Production System

**Files:**
- Create: `docs/acceptance/2026-10-29-production-launch.md`
- Modify: `docs/acceptance/2026-10-28-mvp-checklist.md`

- [ ] **Step 1: Initialize the site once**

  Run the `create-site` profile exactly once with the real runtime environment. Confirm a second invocation refuses to overwrite the existing site.

- [ ] **Step 2: Install and start systemd units**

  Run the installer, then:

  ```bash
  sudo systemctl start selfquanta-ledger
  sudo systemctl status --no-pager selfquanta-ledger
  docker compose --env-file infra/versions.env -f infra/compose.yaml ps
  ```

  Expected: all long-running services are healthy; MariaDB/Redis/backend ports are not published.

- [ ] **Step 3: Perform functional and permission acceptance over WireGuard**

  Visit `https://10.77.0.1`, trust only the transferred internal CA, and verify:

  - administrator can log in, change the temporary password, import a sanitized statement, review and post;
  - viewer can read allowed reports but cannot import, edit, review, post, reverse, manage members or download original statements;
  - public registration is disabled;
  - audit evidence records login, member creation, import, review and posting.

- [ ] **Step 4: Verify restart persistence**

  Record a sanitized test voucher and attachment, run `sudo systemctl restart selfquanta-ledger`, then reboot the server once. Verify the same voucher/attachment remains accessible and all services return healthy without manual container commands.

- [ ] **Step 5: Verify backup, COS upload and isolated restore**

  Start the backup service manually. Verify an encrypted archive and sanitized manifest exist locally and in COS, and confirm no plaintext database/files archive exists in the COS prefix. Run `infra/restore-check.ps1` with a unique restore project, verify PASS counts, then log into the isolated restored site without exposing its gateway publicly and open one restored attachment.

- [ ] **Step 6: Record sanitized evidence**

  In `docs/acceptance/2026-10-29-production-launch.md`, record only:

  - deployed 40-character Git SHA;
  - UTC/Asia-Shanghai test timestamps;
  - PASS/FAIL for service health, permission boundary, reboot, backup and restore;
  - restore report filename and object prefix, excluding bucket credentials, IPs, usernames and statement data.

  Update the existing MVP checklist only where live evidence changes a pending item.

- [ ] **Step 7: Tag the first accepted release**

  Only when every acceptance item is PASS:

  ```bash
  git tag -a v0.1.0 -m "SelfQuanta ledger production acceptance"
  git push origin v0.1.0
  ```

  Commit the sanitized acceptance evidence first:

  ```powershell
  git add docs/acceptance
  git commit -m "docs: record SelfQuanta production acceptance"
  git push origin main
  ```

---

## Task 11: Submit ICP Filing and Perform the Public HTTPS Cutover

**Files:**
- Modify: `docs/acceptance/2026-10-29-production-launch.md`
- Modify outside Git: `/etc/selfquanta/ledger/runtime.env`, public certificate files and firewall rules

- [ ] **Step 1: Submit the filing without delaying private use**

  In Tencent Cloud, submit the enterprise ICP filing for `selfquanta.com` using the actual company materials. Keep the system WireGuard-only while review is pending. Store filing documents in the enterprise document system, not Git.

- [ ] **Step 2: Create DNS only after approval**

  After approval, create an A record for `ledger.selfquanta.com` pointing to the server public IP. Verify public DNS from two resolvers before requesting a certificate.

- [ ] **Step 3: Install a publicly trusted certificate**

  Use an ACME client with automatic renewal. Point `TLS_CERTIFICATE_FILE` and `TLS_PRIVATE_KEY_FILE` to the issued files, preserve root-only private-key access, and test renewal with the client's dry-run command.

- [ ] **Step 4: Open the approved public ports and change bindings**

  Change only `HTTP_BIND` and `HTTPS_BIND` in the production runtime file from `10.77.0.1` to `0.0.0.0`, allow TCP 80/443 in both Tencent firewall and UFW, then restart `selfquanta-ledger`. Keep database, Redis, backend and worker ports private.

- [ ] **Step 5: Perform public acceptance and rollback check**

  Verify valid HTTPS, HTTP-to-HTTPS redirect, login, viewer read-only behavior, no public registration, and no mixed-content warnings at `https://ledger.selfquanta.com`. From a non-WireGuard network confirm only approved public ports respond. Rollback procedure is to close 80/443, restore both bind values to `10.77.0.1`, and restart the service.

- [ ] **Step 6: Record the cutover result**

  Append filing approval date, certificate expiry/renewal check date, DNS/HTTPS PASS, and rollback test PASS to the sanitized acceptance report. Do not record the filing number if it exposes unnecessary identifying information.

---

## Final Verification Gate

- [ ] Local and remote `main` resolve to the same SHA, and production runs that SHA.
- [ ] `pwsh -NoProfile -File infra/test-config.ps1` passes from a clean worktree.
- [ ] `bash -n` passes for every shell script; `systemd-analyze verify` passes for every unit.
- [ ] Before ICP approval, public 80/443 are closed and the service binds only `10.77.0.1`.
- [ ] After ICP cutover, only SSH, WireGuard, HTTP and HTTPS are externally reachable.
- [ ] Administrator/viewer permission boundaries pass against production server APIs, not only hidden UI controls.
- [ ] Reboot persistence, daily encrypted backup, COS upload and isolated restore all have sanitized PASS evidence.
- [ ] No secret, real financial data, server IP, private key or business-license sensitive field is present in Git history.
- [ ] `v0.1.0` exists only after all pre-public production acceptance checks pass.
