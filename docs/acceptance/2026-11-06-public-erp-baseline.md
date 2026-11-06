# SelfLedger 公网 ERP MVP 基线验收记录

## 基线范围

- 日期：2026-11-06
- 分支：`feature/public-erp-mvp`
- 起点提交：`bf00016049fd68bf49e5be94490c26dfb59dee34`
- 隔离工作区：`E:\杭州逆熵云\记账\.worktrees\public-erp-mvp`
- 测试站点：WSL `Ubuntu-24.04` 中的 `test.localhost`
- 生产环境：本轮未连接、未修改、未部署

## 已确认版本

| 组件 | 版本 |
| --- | --- |
| ERPNext | `16.52.1` (`ade850b`) |
| Frappe | `16.52.0` (`4515014`) |
| internal_ledger | `0.1.0` |
| Docker | `29.9.2` |
| MariaDB 镜像基线 | `mariadb:11.8` |
| Redis 镜像基线 | `redis:8.2-alpine` |
| Nginx 镜像基线 | `nginx:1.28.0-alpine` |
| frappe_docker | `v4.1.1` / `d84154ae23ab7ae756dfe942e3f24dcd70d1fbda` |

## 集中验收结果

| 模块 | 命令/范围 | 结果 |
| --- | --- | --- |
| 核心端到端 | `bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_end_to_end` | 6/6 通过 |
| 权限边界 | `internal_ledger.tests.test_permissions` | 17/17 通过 |
| 已过账维护 | `internal_ledger.tests.test_posted_entry_maintenance` | 57/57 通过 |
| 前端工作台 | `frontend_review.cjs` 全部 42 个命名场景 | 42/42 通过 |
| 公网安全脚本 | Linux 容器内 `python -m unittest internal_ledger.tests.test_public_access_security` | 18 个通过，1 个按环境条件跳过 |
| 基础设施静态检查 | `pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly` | 通过 |
| 代码差异格式 | `git diff --check` | 通过 |

所有 Frappe 测试均在隔离测试站点以合成数据执行并由测试事务回滚，不使用生产数据。

## 基线纠偏

本轮没有修改业务实现，只纠正两类与现行已批准规则冲突的旧测试前提：

1. 重复识别规则已要求同日、同交易对象、同金额时进入复核，因此分类学习与三来源解析用例改用不同日期，继续验证其原目标而不制造有意重复。
2. 已过账凭证补充备注会同步更新 Journal Entry 与 GL Entry，因此端到端断言改为验证同步后的备注。
3. 前端浅渲染器会在条件节点中插入布尔文本，分类选择断言改为分别验证字段标签、选中值和深色标签，不再依赖相邻文本拼接。

## 安全与待办边界

- 文件名级敏感信息扫描只发现部署文档、示例配置和测试中的规则引用；未读取、未输出任何密钥或口令内容。
- `infra/test-config.ps1` 的完整运行依赖 Linux/systemd 构建主机；本轮已完成静态检查和对应 Linux 容器安全测试，完整镜像构建与运行时检查留在 G1 可复现镜像任务完成后集中执行。
- WSL 每次启动会输出本机 NAT/localhost 代理提示，不影响本轮测试结果。
- 测试虚拟环境存在一个历史 pip 残留目录警告，不影响应用导入路径和测试执行；应用实际从当前隔离工作区加载。

## 结论

现有智能内账、重复复核、自动分类、凭证草稿、审核过账、证据同步、已过账维护和权限边界可作为公网 ERP MVP 的冻结基线。后续开发须保持这些主链路持续通过，并按大模块集中验收。
