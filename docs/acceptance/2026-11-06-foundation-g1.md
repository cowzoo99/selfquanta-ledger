# SelfLedger 公网 ERP MVP：G1 平台与租户隔离验收

## 验收边界

- 日期：2026-11-06
- 分支：`feature/public-erp-mvp`
- 候选提交：`b462e50a577956fc1c0eb7b357ce583e1736df93`
- 隔离工作区：`E:\杭州逆熵云\记账\.worktrees\public-erp-mvp`
- 一次性 Docker 项目：`slg1fdd122b`
- 一次性站点：`g1-a.localhost`、`g1-b.localhost`
- 生产边界：未连接、未修改、未切换现有 WireGuard 初版系统；本轮所有数据均为合成验收数据。

## 可复现镜像

| 项目 | 结果 |
| --- | --- |
| 应用镜像 | `internal-ledger:b462e50a577956fc1c0eb7b357ce583e1736df93` |
| 镜像 ID | `sha256:932c884ea157691d122c6f5e9fa49cb1b6bea9872529cee4e7e7594f4e1dbb3f` |
| 镜像 revision 标签 | 与候选提交完全一致 |
| Frappe | `16.52.0` |
| ERPNext | `16.52.1` |
| HRMS | `16.52.0` |
| Internal Ledger | `0.1.0` |
| OCR 镜像 | `internal-ledger-ocr:b462e50a577956fc1c0eb7b357ce583e1736df93`，运行时健康 |

完整应用镜像从已推送提交构建；后续修复只重建 Internal Ledger 应用层，最终镜像仍用完整 40 位提交标签和 revision 标签追溯。

## 本轮发现并关闭的开通缺口

1. 新 Site 创建 Company 前缺少 ERPNext 官方基础资料，导致 `Warehouse Type: Transit` 校验失败。现改为仅在首次创建 Company 时先安装 ERPNext 中国基础资料。
2. 新账套未初始化必填的会计年度开始日。现首次开通默认使用当年 1 月 1 日，已有账套不改写。
3. Frappe 新 Site 默认未生成 `encryption_key`。现开通脚本为每个 Site 生成独立随机密钥，密钥不写入源码、日志或验收文档。

## 集中验收结果

| 模块 | 结果 |
| --- | --- |
| 基础设施静态门禁 | `infra/test-config.ps1 -StaticOnly` 通过 |
| 应用镜像构建 | `bench build --app internal_ledger` 通过，候选镜像构建成功 |
| 新公司开通与科目表 | `internal_ledger.tests.test_install`：9/9 通过 |
| 角色与账套权限 | 本阶段集中测试：18/18 通过 |
| 企业成员接口 | 本阶段集中测试：43/43 通过 |
| 统一中文入口 | 初跑 57/58；修复后原失败用例定向 1/1 通过；前端构建通过 |
| 甲站点隔离测试 | 2/2 通过 |
| 乙站点隔离测试 | 2/2 通过 |
| HTTPS Host 路由 | 甲 200、乙 200、未知 Host 404 |
| 管理员凭据隔离 | 甲自有密码 200、乙自有密码 200；两次交叉密码均 401 |
| 跨站点文档读取 | 甲读取乙虚拟客户 404；乙读取甲虚拟客户 404 |
| 独立备份 | 两站点各生成配置、数据库、公共文件、私有文件共 4 个备份文件 |

没有因局部修复反复运行全应用测试；沿用本阶段先前已通过且未被本轮基础设施改动触及的权限、成员和导航模块结果，并只重跑开通与隔离主链路。

## 双站点非敏感隔离证据

| 证据 | 甲站点 | 乙站点 | 判定 |
| --- | --- | --- | --- |
| Site 哈希 | `930bacccc8d5e447c70b0f1b0124c132a8379e9133fe79829c5131e26848c3c2` | `13eea187c909deccd6f559dce5636a68fe313cd4321cb82551251631ffb9cefe` | 不同 |
| 数据库哈希 | `7bf91828c152bbdee99dcb2d53489cc4bca3f972e586759cd21bca7eaff5c6da` | `242494bd7aa9f6e2f5acca95cc0d276da01e814d52becf91900ad90eeaa68da4` | 不同 |
| 加密密钥哈希 | `0b1e30ee6a710ef3eea5df4b79a40b5b5fa486a085584480a102003ad33daa0c` | `ae579f3a5a175a3290cb48e377857c177c121b23790a7e60b9f6359bde9af94a` | 不同 |
| 私有文件目录哈希 | `933e0a2fed228f7adadb504caeafbbcc4e04683beffa5b27b596b019347797db` | `d218edefa76f6652ac335e3fc28ea5ffd798635645b9a9e87601d5ef3b7a8802` | 不同 |
| 备份目录哈希 | `a4c1273d7e35b1d8d8e3e15681873c9986afdbbdb63212538e630fd024aa4fbe` | `176e8dfd5bac7ce58dffbfed1438d34d4faf2eac85d2d743fe83df1a982af56a` | 不同 |
| 自有虚拟客户/私有文件/凭证 | `1 / 1 / 1` | `1 / 1 / 1` | 完整 |
| 对方虚拟客户/私有文件/凭证 | `0 / 0 / 0` | `0 / 0 / 0` | 不可见 |

证据函数只输出计数、布尔值和 SHA-256；未输出数据库名、数据库密码、管理员密码或加密密钥原文。

## 结论与后续边界

**G1 通过。** 当前候选版本满足可追溯镜像、独立 Site 开通、独立数据库/文件/密钥/备份、最小权限角色、统一中文入口和 Host 路由要求，可以进入 Finance/Tax MVP 开发。

本结论不是生产切换批准。现有 WireGuard 初版继续作为可用生产基线；公网切换前仍需单独完成生产备份、恢复演练、域名/备案、生产 TLS、监控和经用户批准的切换窗口。
