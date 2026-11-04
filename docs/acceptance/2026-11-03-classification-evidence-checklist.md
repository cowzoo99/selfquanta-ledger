# 自动分类与逐笔佐证验收记录

执行日期：2026-11-04。范围：Task 6 本地验收及 Task 7 WireGuard 私网生产发布。

## 验收对象与环境

- 代码基线 SHA：`5186270088c3c64a753119a6e196dbfac70d5338`；本轮仅增加端到端测试、README 和本清单，生产模块无改动。
- 验收提交：本记录所在的 `test: verify classification and evidence workflow` 提交，可用 `git log -1 --format=%H -- docs/acceptance/2026-11-03-classification-evidence-checklist.md` 获取完整 SHA。
- 测试站点：WSL 的 `test.localhost`；Bench：`/opt/internal-ledger-bench`。测试入口强制核对站点名，合成账务及文件在测试后回滚。
- 测试数据：仓库 `internal_ledger/tests/fixtures/bank.csv`，原样重复导入；既有端到端测试同时覆盖脱敏微信和支付宝 fixture。未导入真实账单。
- WSL `PYTHONPATH` 指向当前隔离工作树，`NODE_PATH` 指向 Bench 已安装的 Frappe Node 依赖；没有新增依赖或改动生产配置。

## 命令与结果

从 WSL 执行：

```bash
export PYTHONPATH=/mnt/e/杭州逆熵云/记账/.worktrees/import-workflow-navigation
export NODE_PATH=/opt/internal-ledger-bench/apps/frappe/node_modules
cd /opt/internal-ledger-bench
bench --site test.localhost run-tests --app internal_ledger --module internal_ledger.tests.test_end_to_end
bench --site test.localhost migrate
bench --site test.localhost run-tests --app internal_ledger
bench build --app internal_ledger
```

构建结束后，在工作树串行执行：

```powershell
pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly
git diff --check
```

| 门禁 | 本轮结果 |
|---|---|
| 最终定向端到端 | PASS：6 tests，32.338s，0 failures/errors；含有效 PNG 的最终版本 |
| 测试站点迁移 | PASS：exit 0 |
| 全应用回归 | PASS：369 tests，1139.763s，0 failures/errors |
| 前端生产构建 | PASS：exit 0；本地候选 `ledger_workbench.bundle.OIRXH7FX.js`，生产 Linux 镜像 `ledger_workbench.bundle.KPH7GRNA.js` |
| 基础设施静态检查 | PASS：exit 0，`Infrastructure static checks passed.` |
| Git 空白检查 | PASS：`git diff --check` 和提交前暂存差异检查无输出 |

Bench 的 `--module` 是单值选项，重复指定只使用最后一个；本轮定向只指定一个模块，完整回归不指定模块，所有数据库测试串行运行。

## 业务与权限验收

新增用例：`test_import_reconcile_evidence_review_and_post_end_to_end`，通过真实数据库、导入、分类、佐证和审核过账 API：

- [x] 原样导入银行 fixture 三笔流水，再导入相同字节内容，三笔候选均为 `Duplicate / Pending` 并指向原流水。
- [x] 重复候选保持第 2 步只读状态，分类、人工制证和批量处理均不生成草稿；自动清理后仍为 Pending。
- [x] 导入后新增匹配规则，自动清理只生成一份借贷各 12.50 元的 `Draft`；重复清理不增草稿，不创建 Journal Entry 或 Posting Link。两笔未匹配原流水不生成草稿。
- [x] 管理员保存中文文字和简单表格、上传 PNG；内容从真实私有 File 读取，图片元数据可用于预览。
- [x] 只有管理员显式审核过账后才生成一份已提交 Journal Entry 和两条 GL Entry。
- [x] 过账后修改备注、上传和删除补充附件，完整 Journal Entry 字段与 GL Entry 快照逐项不变，正式凭证总数不增。
- [x] 备注更新、上传和删除均留下管理员成功审计；查看者拒绝操作不增任何业务对象或成功审计。
- [x] 查看者可读已过账草稿及同一份备注和附件，通过页面实际使用的私有文件响应下载并核对 PNG 原始字节。
- [x] 查看者保存、上传、删除、自动清理与审核过账均拒绝；原始账单载体列表、读取、下载 API 和私有 URL 均拒绝。

## 格式限制与回归范围

允许 PNG、JPG、JPEG、WEBP、PDF、DOC、DOCX、TXT、XLS、XLSX、CSV；单文件最大 **10 MiB**，每笔最多 **10 个**。后端 `test_evidence_api` 覆盖超限、第 11 个文件、非法 Base64、扩展名/MIME/文件特征不符、文本 NUL、附件归属、公司和角色权限、受控写入口、富文本清洗及审计失败回滚。`test_permissions` 保留原始文件与佐证私有 URL 隔离回归。这些测试纳入全应用门禁，结果以本轮全应用回归为准。

附件不参与 OCR、内容识别或自动分类；图片可预览，其他允许文件只下载。现有统一工作台、分页、上一步/下一步、管理员与查看者组件场景纳入全应用前端测试和构建。

新增测试的有效性另通过临时测试 harness 验证：只把自动清理替换为空结果，真实链路在 `created == 1` 断言失败（1 test，1 failure，0 errors）；恢复真实服务后端到端 6 项通过；PNG 测试字节经标准库校验 IHDR/IDAT/IEND CRC，并在完整门禁后再次运行最终端到端 6 项（32.338s）通过。该 RED 是受控变异检查，不是未修复的产品缺陷。首次测试错误地比较通用下载 API 的文本解码结果与二进制图片，已改为验证页面真实私有下载响应，未修改生产下载逻辑。

## 环境限制与 Task 7 发布记录

本轮完成服务/API、真实文件权限和自动化前端回归；未进行本轮真实浏览器选区编辑及视觉验收，不将 Node 组件测试视作该项人工验收。Frappe 现有弃用提示保留，不影响测试结果。

| 生产证据 | 状态 |
|---|---|
| 生产部署代码 SHA | PASS：`36d3f71db0d365c85a46c7c6731aea40402ec6cb`；发布标签 `release-2026-11-04-classification-evidence` |
| 发布/部署镜像与 digest | PASS：应用 `sha256:60134457bae3e44a3c00752085f603f5a3dd0b9fc9a2e448dc9a603e283b5b55`；OCR 内容未变，仅复用标签，ID `sha256:f6e107ab688b7e1409f60747cc479d9d7dc3ef3020db736991216c68f5d5769d` |
| 部署前数据库与公有/私有附件备份文件 | PASS：`internal-ledger-ledger.selfquanta.com-20261104-134427.zip.enc`，6,253,840 bytes，SHA-256 `448b4d6a091140d7fa56dcfe0ea5b826e9b6e10290dc032b897a9a5c63907fcf`；清单计数 company=1、journal_entry=0、file=5，部署后计数一致 |
| 备份加密、上传及隔离恢复验证 | 本机加密与哈希验证 PASS；COS 配置文件未安装，云端上传未执行；本次备份未另做隔离恢复，保留为后续运维项 |
| 回滚镜像、代码 SHA 和回滚验证 | 回滚应用 SHA `373872c654583cb545915496f6d8d3ef506d4fb1`，镜像 ID `sha256:2e6516e644a3111b4ea42bf989fea1d714eb01a4dcfffeb3ebdd26820e33f991`；运行配置备份保存在服务器 `/etc/selfquanta/ledger/runtime.env.pre-36d3f71db0d365c85a46c7c6731aea40402ec6cb`，未因主链路成功而执行破坏性回滚演练 |
| 内网登录、导入、分类、附件权限和健康检查 | PASS：管理员真实登录、统一工作台、资源包、待办、仪表盘、分类汇总、导入账户读取和 CSRF 退出；全部容器健康；备份定时器 active；无近期服务错误 |
| 私网边界 | PASS：HTTP/HTTPS 仅监听 `10.77.0.1:80/443`，未开放公网 |
| 浏览器选区编辑和视觉验收 | 自动化组件、构建及生产 HTTP 主链路 PASS；真实浏览器富文本选区、2×2 表格和图片视觉效果留给使用者首次操作确认，不阻断本次私网主链路上线 |

生产于 2026-11-04 13:49（Asia/Shanghai）完成迁移和切换。运行时镜像、数据库迁移、`Ledger Evidence` DocType、HTTPS ping、管理员只读主链路烟测、容器健康、备份计时器及 WireGuard 绑定均已现场核对。未在生产创建或过账合成账务数据。
