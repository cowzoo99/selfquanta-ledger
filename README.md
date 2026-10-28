# 协同内账 SaaS

面向单一中国公司、单账套的自托管内账系统。管理员可导入银行卡、微信支付和支付宝脱敏账单，按可解释规则生成借贷平衡草稿，审核后通过 ERPNext 标准接口过账；受邀协作者只读查看流水、凭证和报表。

本项目用于内部经营管理，不替代法定财务软件、税务申报或专业会计判断。

## 当前状态

本地应用与基础设施静态验证已通过；生产发布尚未完成。当前仓库没有所有者提供的 Git `origin`，验收主机也没有 Docker，因此没有构建生产镜像、启动 Compose、执行容器重启、真实加密备份或隔离恢复，也没有创建 `v0.1.0` 标签。完整状态见 [MVP 验收清单](docs/acceptance/2026-10-28-mvp-checklist.md)。

## 首版范围

- 单公司、单账套、人民币本位币。
- 银行卡、微信支付、支付宝 CSV 导入、预览、错误清单和去重。
- 可解释分类规则、待分类队列、借贷平衡草稿、人工复核与拆分分录。
- ERPNext 标准 Journal Entry / GL Entry 过账、幂等控制和冲销更正。
- 流水、草稿、正式凭证、批次与只读原始文件的来源追溯。
- 已自动化验证的总账与余额单公司读取边界；明细账、科目余额表、资产负债表和利润表仍待实际验收。
- 成功敏感操作审计、加密备份脚本和隔离恢复检查脚本。

首版不包含多公司/多租户、自动报税、银企直连、自动付款、发票 OCR、AI 自主入账，以及工资、生产、库存或 CRM 定制。

## 角色与安全边界

- `账套管理员`：导入、分类、编辑草稿、审核、过账、冲销、维护规则与账套设置。
- `账务查看者`：只读查看本账套流水、正式凭证和报表；不能写入，也不能列出或下载原始账单。

权限同时由界面和服务端执行。当前账套的导入对象和正式凭证只能通过受控服务写入；通用文档 API 不能绕过来源链。原始账单保存为私有文件并挂在管理员专属载体上。审计记录不保存账单正文、密码或完整敏感流水数据。

运维人员若拥有服务器 Python、数据库或文件系统权限，仍属于受信任的高权限主体；应用内审计不等同于外部不可篡改审计存储。

## 固定版本

- Frappe `v16.52.0`
- ERPNext `v16.52.1`
- MariaDB `11.8`
- Redis `8.2-alpine`
- frappe_docker `v4.1.1`，commit `d84154ae23ab7ae756dfe942e3f24dcd70d1fbda`

## 本地开发与验证

应用名为 `internal_ledger`，需要安装到已包含上述固定 Frappe/ERPNext 版本的 Bench 站点：

```bash
bench --site test.localhost install-app internal_ledger
bench --site test.localhost migrate
bench --site test.localhost run-tests --app internal_ledger
```

生产配置与脚本的本地安全测试：

```powershell
pwsh -NoProfile -File infra/test-config.ps1
```

测试使用 `internal_ledger/tests/fixtures/` 下的三份脱敏样本：`bank.csv`、`wechat.csv`、`alipay.csv`。不要把真实账单、密码、私钥或备份密钥提交到仓库。

## 部署、备份与恢复

- 部署说明：[docs/operations/deploy.md](docs/operations/deploy.md)
- 备份与恢复说明：[docs/operations/backup-restore.md](docs/operations/backup-restore.md)

生产构建要求干净且已推送的真实 HTTPS `origin`、固定应用 ref、Docker Engine/Compose v2、BuildKit、外置运行密钥和真实 TLS 证书。Compose 只发布 80/443；MariaDB、Redis、backend、worker、scheduler 和 WebSocket 不发布宿主机端口。

上线前必须在目标 Docker 主机完成镜像构建、Compose 启动、重启持久性、数据库与公有/私有附件联合备份，以及独立项目恢复演练；任何一项未通过都不能创建发布标签。
