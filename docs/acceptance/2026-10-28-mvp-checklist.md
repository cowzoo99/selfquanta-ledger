# 协同内账 MVP 验收记录

日期：2026-10-28

验收基线：`85673107c73535a6fb85015c835bdf7aeb916cdf`

站点：E 盘 WSL Ubuntu 中的 `test.localhost`（测试站点）

## 结论

- **本地自动化：PASS。** 全应用 118 项测试通过，基础设施静态与安全反例测试通过。
- **浏览器视觉验收：NOT RUN。** 指定的 Chrome DevTools MCP 在本代理工具清单中不可用；获批的 `chrome-devtools-cli` 等价路径也未安装。临时 Bench Web 服务虽成功启动，但没有真实 DevTools 交互或截图，因此桌面/窄屏页面不能标记通过。
- **生产上线验收：BLOCKED。** 仓库缺少所有者提供并已推送的真实 `origin`，当前 Windows 主机没有 Docker。镜像、Compose、重启、真实备份、隔离恢复、HTTPS 与云服务器检查均未执行。
- **发布标签：BLOCKED。** 未创建 `v0.1.0`；只有浏览器视觉和全部生产门禁通过后才能标记版本。

## 最新命令证据

### 全应用测试

```text
wsl -d Ubuntu-24.04 -u frappe -- bash -lc "cd /opt/internal-ledger-bench && bench --site test.localhost run-tests --app internal_ledger"
exit 0
Ran 118 tests in 283.961s
OK
```

本轮端到端输出使用 `bank.csv`、`wechat.csv`、`alipay.csv`：

```text
E2E EVIDENCE ['74hva9mlp3', '74qngg396g', '74umcavlhv']
['ACC-JV-2026-00038' ... 'ACC-JV-2026-00046']
18 GL; cash delta=2857.50; 3 duplicates; 31 audit events; viewer reads
```

这些批次和凭证是回滚隔离的测试对象，不是保留业务数据。测试同时输出：来源文件管理员下载成功、查看者列表/读取/API/私有 URL 均拒绝；审核审计插入失败会回滚业务；真实过账、冲销、并发幂等和混合锁顺序均通过。

### 基础设施安全测试

```text
pwsh -NoProfile -File infra/test-config.ps1
exit 0
Site config merge validated.
Restore project name is unused.
Backup inputs validated.
Restore inputs validated.
Infrastructure configuration checks passed.
```

### 生产构建前置门禁

```text
pwsh -NoProfile -File infra/build_image.ps1
exit 1
Git remote 'origin' is required before an image can be built.
```

失败发生在 Docker 调用前，符合既定安全边界。`Get-Command docker` 的最新结果为 `DOCKER_NOT_FOUND`。基线工作树执行 `git status --short` 无输出，`git tag --list v0.1.0` 无输出。

## 规格验收标准

| # | 标准 | 状态 | 证据与边界 |
|---|---|---|---|
| 1 | 银行卡、微信、支付宝各一份脱敏样本成功导入 | PASS | 真实 e2e 使用三份 CSV，生成 3 个批次、9 个已提交标准 JE 和 18 条 GL。 |
| 2 | 重复导入不重复生成流水或凭证 | PASS | 银行再次导入产生 3 条 Duplicate，指向原流水；未新增 JE。 |
| 3 | 规则生成借贷平衡草稿并显示命中原因 | PASS | 分类/草稿/审核测试覆盖精确、关键词、历史规则优先级与平衡校验；e2e 9 张草稿全部经规则生成后审核过账。 |
| 4 | 未识别或冲突流水停留待分类，不自动过账 | PASS | `test_classifier`、`test_drafts`、`test_review_api` 覆盖 unmatched/conflict 拒绝建草稿和待分类后人工分类。 |
| 5 | 审核后生成正式凭证并可追溯到批次和原文件 | PASS | e2e 通过标准 Journal Entry/GL；链路为 JE→Posting Link→草稿→流水→批次→Source File→private File，并核验 SHA256/raw_row。 |
| 6 | 已过账记录只能通过冲销更正 | PASS | `test_posting` 覆盖普通保存不能修改已过账记录、标准反向 JE、重复冲销拒绝和并发一次性。 |
| 7 | 查看者在界面和后端均不能写 | BLOCKED | 服务端 PASS：所有写入口、通用 doc/client 绕过和原始文件直链均拒绝；界面桌面/窄屏未获得真实 DevTools 视觉证据。 |
| 8 | 核心科目与基础财务报表可用 | PASS | 安装测试验证五类中文科目；e2e 余额现金增加 2857.50；权限测试验证绑定公司余额与 General Ledger，外公司不可见。 |
| 9 | 重启后数据完整，数据库与附件备份可恢复 | BLOCKED | 脚本/配置静态测试 PASS；真实 Docker 启动、重启、加密备份和隔离恢复未运行。 |

## 浏览器检查矩阵

| 页面/检查 | 桌面 | 窄屏/手机 | 说明 |
|---|---|---|---|
| 工作台 | NOT RUN | NOT RUN | 无 DevTools MCP/CLI。 |
| 导入预览/流水列表 | NOT RUN | NOT RUN | 未检查水平溢出、上传控件或错误行布局。 |
| 待分类队列 | NOT RUN | NOT RUN | 未检查真实空态/数据态。 |
| 审核中心与借贷同屏 | NOT RUN | NOT RUN | 未检查拆分行、按钮可见性或键盘路径。 |
| 凭证追溯与报表 | NOT RUN | NOT RUN | 服务端追溯/报表通过，但未作视觉检查。 |
| 查看者写入口隐藏 | NOT RUN | NOT RUN | 服务端拒绝已通过，界面 affordance 未视觉验证。 |

截图：无。不得用源码推断或普通 HTTP 响应代替视觉验收。

## 生产门禁

| 门禁 | 状态 | 清除条件 |
|---|---|---|
| 真实 `origin` 与 ref 指向当前提交 | BLOCKED | 所有者创建并推送凭据无关的 HTTPS origin/ref。 |
| `internal-ledger:<git-sha>` 镜像构建 | BLOCKED | 在 Docker/BuildKit 主机执行 `infra/build_image.ps1` 并核对应用版本清单。 |
| Compose 启动与健康检查 | BLOCKED | 真实密钥、域名和证书就绪后启动；仅 80/443 对外。 |
| 容器重启持久性 | BLOCKED | 重启 backend/worker/数据库后核对数据与附件。 |
| 真实加密备份 | BLOCKED | 在独立目标目录执行 `infra/backup.ps1`，留存校验值。 |
| 隔离恢复演练 | BLOCKED | 执行 `infra/restore-check.ps1`，取得 Company/JE/File 计数一致的 PASS 报告。 |
| HTTPS 与云服务器检查 | BLOCKED | 在真实 Ubuntu LTS 主机核对证书、DNS、防火墙、登录与只有 80/443 发布。 |
| 桌面/手机视觉检查 | BLOCKED | 使用真实 Chrome DevTools 在两种视口逐页检查并保存脱敏截图。 |

## 发布决定

当前只可作为“本地自动化通过、待上线验收”的候选版本，不是已发布的 `v0.1.0`。补齐上述门禁、更新本记录并再次运行完整测试之前，不创建标签。
