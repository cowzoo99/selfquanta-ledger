# SelfQuanta One-Month Free-Trial Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不产生未批准云费用的前提下，把 SelfQuanta Ledger 部署到一个月腾讯云企业免费实例，完成私网试用、备份恢复、UI 反馈收集和到期退出决策。

**Architecture:** 复用现有固定版本 Frappe/ERPNext Docker Compose、WireGuard、systemd 和加密备份脚本。实例只通过 WireGuard 提供 Web 访问；公网 Web、域名、ICP 和付费续费全部位于第 21 天决策门之后。

**Tech Stack:** Tencent Cloud Lighthouse、Ubuntu 24.04 LTS、Docker Engine/Compose v2、PowerShell 7、WireGuard、systemd、OpenSSL。

**Spec:** `docs/superpowers/specs/2026-11-01-selfquanta-free-trial-pilot-design.md`

## Global Constraints

- 只领取结算页明确显示 0 元的企业免费实例；不自动接受付费替代方案。
- 关闭自动续费；不购买域名、云硬盘、数据库、安全增值包或 COS 资源包。
- 公网 TCP 80/443 始终关闭，Web 只绑定 `10.77.0.1`。
- 整个免费试运行只使用代表性脱敏数据；真实数据等待附件内容和管理员登录恢复校验获得自动化测试后再批准。
- 所有秘密和云资源标识保存在 `E:\杭州逆熵云\运维密钥\selfquanta` 或密码管理器，不写入仓库。
- 第 21 天做退出决策，最迟第 27 天完成最终备份和迁移、续费或删除准备。

---

## Task 1: Owner Claims the Zero-Cost Instance

**Files:**
- No repository changes

**Interfaces:**
- Consumes: 腾讯云企业实名认证账号、官方免费体验页 `https://cloud.tencent.com/act/pro/free`
- Produces: 一台运行中的上海 Ubuntu 24.04 实例，以及保存在密码管理器中的公网 IPv4

- [ ] **Step 1: Verify enterprise identity**

  所有者登录腾讯云并确认账号实名认证主体与营业执照企业名称逐字一致。不得把证件号码、验证码或控制台密码发送到聊天。

- [ ] **Step 2: Claim only the approved free SKU**

  在免费体验页选择企业轻量应用服务器：上海、Ubuntu 24.04、4 核、8 GB、180 GB SSD、12 Mbps、1 个月。结算页应付金额必须为 0 元；否则立即停止。

- [ ] **Step 3: Disable renewal and paid additions**

  在实例续费设置中关闭自动续费，确认订单没有云硬盘、数据库、安全增值包、域名或付费 COS 套餐，并在费用中心开启余额和异常费用告警。

- [ ] **Step 4: Add the existing SSH public key**

  将 `E:\杭州逆熵云\运维密钥\selfquanta\selfquanta-prod-ed25519.pub` 加入实例。私钥文件不得上传控制台、GitHub 或聊天。

- [ ] **Step 5: Verify the external gate**

  验收：实例显示运行中；系统为 Ubuntu 24.04；自动续费关闭；订单金额为 0 元；使用私钥可以登录。任一项失败时停止，不进入部署。

---

## Task 2: Bootstrap the Private Host

**Files:**
- Execute: `infra/host/bootstrap-ubuntu.sh`
- Execute: `infra/host/configure-private-access.sh`
- No source changes unless a reproducible defect is first captured by a failing test

**Interfaces:**
- Consumes: Task 1 的实例、公网 IPv4、SSH 私钥
- Produces: `selfquantaops` 运维用户、Docker/PowerShell、WireGuard `10.77.0.1/24`、内部 TLS 和一个客户端配置

- [ ] **Step 1: Set local session values without printing secrets**

  在本地 PowerShell 交互输入服务器地址：

  ```powershell
  $serverIp = Read-Host 'Tencent Cloud server IPv4'
  $keyPath = 'E:\杭州逆熵云\运维密钥\selfquanta\selfquanta-prod-ed25519'
  $defaultUser = Read-Host 'Ubuntu image default SSH user'
  ssh -i $keyPath "$defaultUser@$serverIp"
  ```

- [ ] **Step 2: Create the operator and clone the verified main branch**

  使用镜像默认用户通过密钥首次登录后执行：

  ```bash
  sudo apt-get update
  sudo apt-get install --yes git
  if ! id -u selfquantaops >/dev/null 2>&1; then sudo useradd --create-home --shell /bin/bash selfquantaops; fi
  sudo install -d -m 0700 -o selfquantaops -g selfquantaops /home/selfquantaops/.ssh
  sudo cp "$HOME/.ssh/authorized_keys" /home/selfquantaops/.ssh/authorized_keys
  sudo chown selfquantaops:selfquantaops /home/selfquantaops/.ssh/authorized_keys
  sudo chmod 0600 /home/selfquantaops/.ssh/authorized_keys
  sudo install -d -o selfquantaops -g selfquantaops /srv/selfquanta
  sudo -u selfquantaops git clone --branch main https://github.com/cowzoo99/selfquanta-ledger.git /srv/selfquanta/ledger
  sudo -u selfquantaops git -C /srv/selfquanta/ledger rev-parse HEAD
  ```

  记录完整 40 位 SHA，不记录公网 IP。

- [ ] **Step 3: Bootstrap Ubuntu**

  ```bash
  cd /srv/selfquanta/ledger
  sudo bash infra/host/bootstrap-ubuntu.sh selfquantaops
  sudo usermod -aG "$(id -gn selfquantaops)" "$USER"
  exit
  ```

  回到本地 PowerShell 后重新登录默认管理用户，使 `/srv/selfquanta` 目录组成员身份生效：

  ```powershell
  ssh -i $keyPath "$defaultUser@$serverIp"
  ```

  默认管理用户继续处理 `sudo` 操作。`selfquantaops` 不授予 sudo 权限；从此步骤起，所有非 root Git、构建和 Docker 命令必须通过 `sudo -u selfquantaops -H` 执行。

- [ ] **Step 4: Configure WireGuard-only access**

  ```bash
  cd /srv/selfquanta/ledger
  read -r -p 'Server public IPv4: ' WIREGUARD_ENDPOINT
  sudo env WIREGUARD_ENDPOINT="$WIREGUARD_ENDPOINT" bash infra/host/configure-private-access.sh laptop-cao
  unset WIREGUARD_ENDPOINT
  ```

  输入值只能包含服务器公网 IPv4，不包含端口或协议。将生成的客户端配置和内部 CA 安全下载到 `E:\杭州逆熵云\运维密钥\selfquanta`，传输完成后删除中转副本。

- [ ] **Step 5: Verify the private boundary**

  从已导入 WireGuard 配置的电脑验证 `10.77.0.1` 可达；从非 WireGuard 网络确认公网 TCP 80/443 不可达。

---

## Task 3: Deploy and Accept the Ledger

**Files:**
- Execute: committed scripts and units under `infra/`
- Create outside Git: `/etc/selfquanta/ledger/runtime.env` and secret files
- No source changes unless a reproducible defect is first captured by a failing test

**Interfaces:**
- Consumes: Task 2 的私网主机和仓库 SHA
- Produces: 运行中的单账套系统、管理员账号、只读成员和健康服务

- [ ] **Step 1: Run the complete configuration test**

  ```bash
  cd /srv/selfquanta/ledger
  sudo -u selfquantaops -H pwsh -NoProfile -File infra/test-config.ps1
  ```

- [ ] **Step 2: Create secrets and build the exact image**

  ```bash
  sudo install -d -m 0700 -o root -g root /etc/selfquanta/ledger /var/backups/selfquanta/ledger
  sudo sh -c 'openssl rand -base64 48 > /etc/selfquanta/ledger/db-root-password'
  sudo sh -c 'openssl rand -base64 48 > /etc/selfquanta/ledger/site-admin-password'
  sudo sh -c 'openssl rand -out /etc/selfquanta/ledger/backup.key 64'
  sudo chmod 0600 /etc/selfquanta/ledger/db-root-password /etc/selfquanta/ledger/site-admin-password /etc/selfquanta/ledger/backup.key
  sudo -u selfquantaops git clone https://github.com/frappe/frappe_docker.git /srv/selfquanta/frappe_docker
  sudo -u selfquantaops git -C /srv/selfquanta/frappe_docker checkout v4.1.1
  test "$(sudo -u selfquantaops git -C /srv/selfquanta/frappe_docker rev-parse HEAD)" = d84154ae23ab7ae756dfe942e3f24dcd70d1fbda
  sudo install -d -m 0750 -o selfquantaops -g selfquantaops /srv/selfquanta/build
  cd /srv/selfquanta/ledger
  sudo -u selfquantaops -H env \
    FRAPPE_DOCKER_DIR=/srv/selfquanta/frappe_docker \
    INTERNAL_LEDGER_APP_REF=main \
    INTERNAL_LEDGER_BUILD_TEMP=/srv/selfquanta/build \
    pwsh -NoProfile -File infra/build_image.ps1
  ```

  最终镜像标签必须等于 `git rev-parse HEAD` 的 40 位 SHA。

- [ ] **Step 3: Install runtime and systemd units**

  ```bash
  cd /srv/selfquanta/ledger
  image_sha=$(sudo -u selfquantaops git rev-parse HEAD)
  sudo install -o root -g root -m 0600 infra/runtime.env.example /etc/selfquanta/ledger/runtime.env
  sudo sed -i "s/0000000000000000000000000000000000000000/$image_sha/" /etc/selfquanta/ledger/runtime.env
  sudo bash -c 'set -a; . /etc/selfquanta/ledger/runtime.env; set +a; docker compose --env-file /srv/selfquanta/ledger/infra/versions.env -f /srv/selfquanta/ledger/infra/compose.yaml config --quiet'
  sudo bash infra/host/install-systemd-units.sh
  sudo install -d -m 0755 /etc/systemd/system/selfquanta-ledger-backup.service.d
  printf '%s\n' '[Service]' 'ExecStart=' 'ExecStart=/usr/bin/pwsh -NoProfile -File /srv/selfquanta/ledger/infra/backup.ps1' | sudo tee /etc/systemd/system/selfquanta-ledger-backup.service.d/local-only.conf >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl cat selfquanta-ledger-backup.service
  ```

  再读取 `/etc/selfquanta/ledger/runtime.env`，确认 `HTTP_BIND=HTTPS_BIND=10.77.0.1`；`systemctl cat` 的最终 `ExecStart` 必须调用 `backup.ps1`，不能调用 `backup-and-upload.ps1`。

- [ ] **Step 4: Initialize once and start**

  ```bash
  sudo bash -c 'set -a; . /etc/selfquanta/ledger/runtime.env; set +a; docker compose --env-file /srv/selfquanta/ledger/infra/versions.env -f /srv/selfquanta/ledger/infra/compose.yaml --profile init run --rm create-site'
  sudo systemctl start selfquanta-ledger
  sudo systemctl start selfquanta-ledger-backup.timer
  sudo systemctl status --no-pager selfquanta-ledger
  sudo systemctl list-timers --all selfquanta-ledger-backup.timer
  ```

  不重复执行一次性初始化；若误执行第二次，`create-site` 必须拒绝覆盖现有站点。

- [ ] **Step 5: Perform private acceptance**

  管理员通过 WireGuard 登录并修改临时密码；创建一个只读成员。验证管理员可导入、审核和过账，成员只能查看；验证公开注册关闭、数据库和 Redis 端口未发布。

---

## Task 4: Prove Backup Before Real Data

**Files:**
- Execute: `infra/backup.ps1`
- Execute: `infra/restore-check.ps1`
- Create outside Git: encrypted archives and restore reports

**Interfaces:**
- Consumes: Task 3 的脱敏测试站点
- Produces: 服务器外加密副本和一份明确限定验证范围的隔离恢复报告

- [ ] **Step 1: Create an encrypted local backup**

  ```bash
  cd /srv/selfquanta/ledger
  sudo bash -c 'set -a; . /etc/selfquanta/ledger/runtime.env; set +a; pwsh -NoProfile -File /srv/selfquanta/ledger/infra/backup.ps1 -Destination "$INTERNAL_LEDGER_BACKUP_DESTINATION" -KeyFile "$BACKUP_KEY_FILE" -Site "$SITE_NAME" -Project "$COMPOSE_PROJECT_NAME"'
  sudo find /var/backups/selfquanta/ledger -maxdepth 2 -type f -printf '%f\n'
  ```

  输出只允许出现 `.zip.enc` 和脱敏 `.manifest.json`，不得存在明文数据库或文件归档。

- [ ] **Step 2: Copy the encrypted archive off the instance**

  使用 `scp` 将加密归档和 manifest 复制到 `E:\杭州逆熵云\运维密钥\selfquanta\backups`；将恢复密钥单独复制到 `E:\杭州逆熵云\运维密钥\selfquanta\recovery\backup.key` 并限制 ACL。密钥与归档不得存放在同一个未加密共享位置。

- [ ] **Step 3: Verify the local-only daily timer**

  ```bash
  sudo systemctl start selfquanta-ledger-backup.service
  sudo systemctl status --no-pager selfquanta-ledger-backup.service
  sudo systemctl list-timers --all selfquanta-ledger-backup.timer
  ```

  确认 service 成功生成新的本地加密归档，timer 具有下一次运行时间，并在每个使用日结束前将最新归档和 manifest 拉回 E 盘。试运行期不创建 COS 桶或 CAM 凭据。

- [ ] **Step 4: Run isolated restore**

  ```bash
  read -r -p 'Absolute encrypted archive path: ' archive_path
  read -r -p 'Absolute manifest path: ' manifest_path
  image_ref=$(sudo sed -n 's/^INTERNAL_LEDGER_IMAGE=//p' /etc/selfquanta/ledger/runtime.env)
  sudo env INTERNAL_LEDGER_IMAGE="$image_ref" pwsh -NoProfile -File /srv/selfquanta/ledger/infra/restore-check.ps1 \
    -Archive "$archive_path" \
    -Manifest "$manifest_path" \
    -KeyFile /etc/selfquanta/ledger/backup.key \
    -Project "ledger-restore-$(date +%Y%m%d%H%M%S)" \
    -ReportDirectory /var/backups/selfquanta/ledger/restore-reports
  unset archive_path manifest_path image_ref
  ```

  确认加密归档校验和以及 Company、Journal Entry、File 三类记录计数通过。当前脚本不验证附件二进制内容或管理员登录，不得把报告描述为完整业务恢复验收。

- [ ] **Step 5: Keep the real-data gate closed**

  当前免费试运行继续只使用代表性脱敏数据。另行实现并测试附件内容校验和恢复后管理员登录验收之前，不允许导入真实账务数据。

---

## Task 5: Run the Pilot and Capture UI Evidence

**Files:**
- Create after deployment: `docs/acceptance/2026-11-01-free-trial-pilot.md`
- No frontend source changes in this task

**Interfaces:**
- Consumes: 通过备份门的私网系统
- Produces: 脱敏运行证据、优先级明确的 UI 问题清单和退出决定

- [ ] **Step 1: Operate for at least seven days**

  管理员使用代表性脱敏账单执行导入、分类、审核、过账和冲销；一至两个查看者只读使用。记录结果，不记录真实交易方、金额、账号或账单正文。

- [ ] **Step 2: Classify frontend findings**

  每个问题只记录页面、操作、预期、实际和级别：P0 阻塞记账、P1 容易误操作、P2 信息不清、P3 纯视觉偏好。截图必须使用脱敏数据。

- [ ] **Step 3: Create the bounded UI follow-up**

  只把 P0–P2 纳入后续前端设计计划，优先中文化、导航、导入步骤、借贷审核和手机端表格。P3 不阻塞试运行或续费决定。

- [ ] **Step 4: Decide on day 21**

  选择其一并记录理由：续费当前实例、迁移到已批准的低成本实例、或停止使用。任何付费选择必须由所有者在控制台确认。

- [ ] **Step 5: Complete the exit by day 27**

  先生成最终加密备份并通过恢复检查。停止方案随后删除云实例；续费或迁移方案确认新环境健康后再删除旧实例。不得等待到期当天才处理。

---

## Final Verification Gate

- [ ] 免费实例订单为 0 元且自动续费关闭。
- [ ] 远端 `main` SHA 与运行镜像 SHA 一致。
- [ ] 公网 TCP 80/443 关闭，Web 只绑定 `10.77.0.1`。
- [ ] 管理员和查看者权限边界通过生产 API 与真实浏览器验收。
- [ ] 重启后数据库和附件仍存在。
- [ ] 加密备份存在于服务器外，隔离恢复 PASS。
- [ ] 试运行期未创建 COS 桶、CAM 凭据或上传任务。
- [ ] 试运行报告不包含秘密、IP、真实金额、交易方或账号。
- [ ] 第 21 天退出决定和第 27 天完成动作均已记录。
