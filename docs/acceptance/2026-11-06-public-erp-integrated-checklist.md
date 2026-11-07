# SelfLedger 公网 ERP 综合验收清单

## 验收边界

- 日期：2026-11-07
- 分支：`feature/public-erp-mvp`
- 验收提交：以本记录所在提交为准。
- 隔离环境：WSL `Ubuntu-24.04`，本机 Bench `/opt/internal-ledger-bench`。
- 专用 Site：`acceptance-a.localhost`、`acceptance-b.localhost`。
- 数据边界：仅使用合成公司、用户、业务单据、流水和附件；未使用真实客户数据。
- 生产边界：未连接、未修改、未部署或切换现有 WireGuard 初版系统。

## 双 Site 独立性

两个 Site 均通过官方 `bench new-site` 创建，安装 Frappe `16.52.0`、ERPNext `16.52.1`、HRMS `16.52.0` 和 Internal Ledger `0.1.0`，并各自初始化一家公司和独立随机 `encryption_key`。

| 检查 | Site A | Site B | 结果 |
| --- | ---: | ---: | --- |
| Company | 1 | 1 | PASS |
| 本 Site 合成 Customer/File/Journal Entry | 1 / 1 / 1 | 1 / 1 / 1 | PASS |
| 对方合成 Customer/File/Journal Entry 可见数 | 0 / 0 / 0 | 0 / 0 / 0 | PASS |
| 数据库哈希 | 与 B 不同 | 与 A 不同 | PASS |
| 加密密钥哈希 | 与 B 不同 | 与 A 不同 | PASS |
| 私有文件目录哈希 | 与 B 不同 | 与 A 不同 | PASS |
| 备份目录哈希 | 与 B 不同 | 与 A 不同 | PASS |
| 隔离测试 | 2/2 | 2/2 | PASS |

哈希只用于证明不同，不记录数据库名、密钥或路径原文。两个本地管理员口令仅保存在 WSL root-only 文件，未进入 Git、命令参数或测试日志。

## 专用 Site A 集中验收

| 模块 | 结果 | 耗时 |
| --- | --- | ---: |
| 经营业财工作流 | 6/6 PASS | 302.266 秒 |
| HR/薪资权限 | 2/2 PASS | 60.571 秒 |
| 权限与审计边界 | 18/18 PASS | 473.036 秒 |
| 财税业财综合端到端 | 10/10 PASS | 210.029 秒 |

完整端到端模块通过显式 `SELFLEDGER_ACCEPTANCE_SITE=acceptance-a.localhost` 运行；默认仍只允许 `test.localhost`，显式目标也必须匹配 `acceptance-*.localhost`，防止误对生产 Site 运行合成验收。

## 综合链路证据

- 导入 6 条合成流水，确认并排除重复 1 条，正式过账 5 条。
- 受控重建 1 条，永久删除 1 条；最终有效凭证 4 张、总账分录 8 条。
- 总账借方和贷方均为 CNY 3,258.60，13 项月结/报税准备材料可重复生成并追溯。
- 贸易链路覆盖销售、采购、库存、应收应付、收付款、银行核销和标准撤销；智能导入没有重复增加总账。
- 服务链路覆盖项目、工时、费用、销售/采购发票、工资、报表、角色隔离和标准撤销。
- 查看者只能读取获准结果；管理员审计失败会回滚业务操作；私有源文件的列表、读取、API 和直接 URL 均受权限保护。
- 导入、分类、凭证、Journal Entry、GL Entry、源文件、操作人和审计事件链路已由端到端及权限模块验证。

## 发布门禁

| 门禁 | 状态 | 证据或阻断原因 |
| --- | --- | --- |
| 两个专用 Site 独立 | PASS | A/B 共 4/4 隔离测试通过 |
| 财税业财端到端 | PASS | Site A 10/10 通过 |
| 角色与敏感数据边界 | PASS | 经营 6/6、HR 2/2、权限 18/18 通过 |
| 客户完整退出导出 | PENDING | 尚未证明主数据、交易、附件和审计日志的一次性客户导出 |
| 加密备份与隔离恢复 | BLOCKED | 本地缺少候选 App/OCR 不可变镜像；不得用 `-ValidateOnly` 代替真实 `PASS` 报告 |
| Nginx 模板真实容器验证 | BLOCKED | `nginx:1.28.0-alpine` 下载因 Docker Hub 网络超时失败 |
| 单 Site 升级与镜像回滚 | PENDING | 尚未在候选不可变镜像上执行 |
| 真实 DNS/备案/TLS/公网只读 smoke | PENDING | 尚无获批域名、证书、服务器窗口和公网切换授权 |
| 3–5 家客户逐户签字 | PENDING | 当前无真实试点客户授权 |

## 决策

**综合候选代码主链路通过，但 G4/G5 尚未通过，当前结论为 `NO-GO`。** 在客户完整导出、真实加密恢复、候选镜像回滚、Nginx 容器验证、公网安全检查和真实客户签字完成前，不得合并为生产发布结论或开放公网流量。
