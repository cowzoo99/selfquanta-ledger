# SelfLedger 财务与报税材料：G2 端到端验收

## 验收边界

- 日期：2026-11-06
- 分支：`feature/public-erp-mvp`
- 验收基线：`c5a4f09`（G2 证据与测试由本文档所在提交继续追溯）
- 隔离工作区：`E:\杭州逆熵云\记账\.worktrees\public-erp-mvp`
- 隔离站点：`test.localhost`
- 生产边界：未连接、未修改、未部署或切换现有 WireGuard 初版系统。
- 数据边界：仅使用 `虚构`、`测试` 标识的合成企业、往来单位、金额和 OCR 文本，不包含真实客户流水。

## G2 主链路

同一自动化验收依次完成：

1. 导入银行、微信、支付宝 CSV 和 OCR 图片识别结果，共 6 条合成流水。
2. 在确认导入前人工修正 1 条交易对象和摘要。
3. 识别并确认 1 条重复流水；该条不生成草稿、不进入过账、总账和材料包。
4. 自动分类并显式审核过账 5 条流水。
5. 对 1 条已过账账目执行受控重建，对 1 条误录执行永久删除。
6. 核对 ERPNext `Journal Entry`、`GL Entry` 与标准 `General Ledger` 报表。
7. 运行 8 项月结检查，全部通过后关闭 2026-10 会计期间。
8. 生成私有、固定清单、可追溯的月度报税准备材料包。

## 集中验收结果

| 模块 | 结果 |
| --- | --- |
| 端到端财务与报税主链路 | 8/8 通过，58.615 秒 |
| 月结检查与关账 | 4/4 通过，20.742 秒 |
| 报税材料包 | 5/5 通过，24.819 秒 |
| 合计 | 17/17 通过 |

主链路关键计数：导入 6 条、确认重复并排除 1 条、过账 5 条、受控重建 1 条、永久删除 1 条；最终有效凭证 4 张、有效总账分录 8 条。

| 对账项 | 结果 |
| --- | --- |
| GL 借方合计 | CNY 3,258.60 |
| GL 贷方合计 | CNY 3,258.60 |
| General Ledger 报表 | 与 GL Entry 借贷合计一致 |
| 材料包总账 | 8 条，与有效 GL Entry 和标准报表一致 |
| 材料清单 | 固定 13 个文件，名称和顺序完全一致 |
| 材料包 SHA-256 | `7314abd2d9f82a7792d21eb0d6ea820d557db9ee551ca54ab0801430d18d1403` |

每个材料包总账凭证号均反向验证以下链路：

`总账.csv → Journal Entry → Posting Link → Voucher Draft → Imported Transaction → Import Batch → Internal Ledger Source File`

## 中文与 Excel 兼容性

- 12 个 CSV 均包含 UTF-8 BOM，使用 CRLF 行结束符，可按 Excel 中文兼容方式打开。
- `总账.csv` 的过账日期全部符合 `YYYY-MM-DD`。
- 借方、贷方字段全部固定为两位小数。
- `说明.txt` 明确标注材料仅供申报准备和人工复核，不代表已向税务机关提交。
- 原始导入载体和材料包均为私有文件。

## 合成夹具哈希

| 文件 | SHA-256 |
| --- | --- |
| `alipay.csv` | `e52b574ff88e1685dee51b04183c7ef973535786f6919f8f34c4118e4e54a69c` |
| `bank.csv` | `7900ec14c87e68792aaf6808c4c5ff66c5c21bee8f290e6d4a5ac7626fae54a7` |
| `ocr_pages.json` | `07dbb8f0e600ace79b6f6673ef62dbbf4e9c2665e19e071e6f1419acee1a601a` |
| `wechat.csv` | `3f26fc292b5b57400c5264203622036c906ac1e4e21da8835268b934d046a38f` |

## 实际命令

```bash
cd /opt/internal-ledger-bench
runuser -u frappe -- env HOME=/home/frappe bench --site test.localhost run-tests \
  --app internal_ledger --module internal_ledger.tests.test_end_to_end
runuser -u frappe -- env HOME=/home/frappe bench --site test.localhost run-tests \
  --app internal_ledger --module internal_ledger.tests.test_month_end
runuser -u frappe -- env HOME=/home/frappe bench --site test.localhost run-tests \
  --app internal_ledger --module internal_ledger.tests.test_tax_materials
```

Windows 工作区最终检查：

```powershell
python -m py_compile internal_ledger/tests/test_end_to_end.py
git -c core.bare=false diff --check
```

## 结论与后续边界

**G2 通过。** 当前候选版本已证明合成财务数据能够从多来源导入、重复排除、分类、审核过账、受控维护、关账，并生成与 ERPNext 标准总账一致且逐行可追溯的中文报税准备材料。

本结论不等同于自动向税务机关申报，也不是生产切换批准。现有 WireGuard 初版继续保持原状；公网生产仍需单独完成生产备份与恢复演练、域名/备案、TLS、监控及用户批准的切换窗口。
