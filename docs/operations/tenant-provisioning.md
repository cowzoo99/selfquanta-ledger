# 独立客户 Site 开通与生命周期

每个客户使用独立 Frappe Site、数据库、站点目录、加密密钥、管理员和备份前缀。禁止在同一 Site 中用多个 Company 模拟多个客户。

## 开通

前提：已加载服务器的 `runtime.env`，`INTERNAL_LEDGER_IMAGE` 是精确提交或镜像摘要，数据库根密码和站点管理员临时密码均存放在仅 root 可读的文件中。

```powershell
pwsh -NoProfile -File infra/provision-site.ps1 `
  -Site customer.example.com `
  -CompanyName '示例商贸有限公司' `
  -CompanyAbbr '示例' `
  -AdminPasswordFile C:\secure\customer-admin.txt `
  -Project selfledger
```

脚本会在任何修改前验证域名和密码文件，拒绝已存在的 Site，依次安装 ERPNext、HRMS、Internal Ledger，执行迁移并初始化一家公司、小企业中文科目和默认分类规则。密码只通过 Docker Secret 文件传入，不写入命令行、Git、日志或回执。

成功回执位于 `infra/provisioning-receipts/`，只包含 Site、公司、镜像 SHA、应用版本、UTC 时间和操作人。将回执复制到受控运维记录；它不能代替备份。

## 验证与重新执行

```powershell
pwsh -NoProfile -File infra/provision-site.ps1 `
  -Site customer.example.com `
  -CompanyName '示例商贸有限公司' `
  -CompanyAbbr '示例' `
  -AdminPasswordFile C:\secure\customer-admin.txt `
  -Project selfledger `
  -ValidateOnly
```

真实开通命令对已存在的 Site 直接失败，不迁移、不重置密码、不覆盖公司。失败后先保留现场和日志；禁止删除 Site 后盲目重试。

## 暂停与恢复

在维护窗口中暂停单个客户，不停止其他 Site：

```bash
docker compose --project-name selfledger --env-file infra/versions.env -f infra/compose.yaml \
  run --rm --no-deps backend bench --site customer.example.com set-maintenance-mode on
docker compose --project-name selfledger --env-file infra/versions.env -f infra/compose.yaml \
  run --rm --no-deps backend bench --site customer.example.com disable-scheduler
```

恢复前先完成健康检查，再执行 `enable-scheduler` 和 `set-maintenance-mode off`。客户停用不等于删除，数据和备份应继续保留到合同约定期限。

## 加密导出

使用现有 `infra/backup.ps1` 或 `infra/backup-and-upload.ps1`，为目标 Site 生成数据库、私有文件和站点配置备份。只有同时满足以下条件才算可恢复导出：

- 加密归档存在且 SHA-256 与 manifest 一致；
- manifest 的 Site 与目标完全一致；
- 独立恢复演练报告状态为 `PASS`；
- 归档和密钥不存放在同一位置。

## 销毁

销毁必须在单独维护窗口执行，并使用两个分别输入的参数 `-Site` 和 `-ConfirmSite`；两者必须区分大小写完全相等。还必须提供最近一次已验证备份 manifest。任何一项不满足都停止操作。

```powershell
param(
  [Parameter(Mandatory)][string]$Site,
  [Parameter(Mandatory)][string]$ConfirmSite,
  [Parameter(Mandatory)][string]$VerifiedBackupManifest
)
if (-not [string]::Equals($Site, $ConfirmSite, [StringComparison]::Ordinal)) {
  throw 'ConfirmSite must exactly match Site.'
}
if (-not (Test-Path -LiteralPath $VerifiedBackupManifest -PathType Leaf)) {
  throw 'A verified backup manifest is required.'
}
```

确认备份、回执、合同保留期和审批单后，先开启维护模式并停用调度，再在服务器交互终端执行 `bench drop-site <Site>`。数据库根密码只在交互提示中输入，不放进命令行或脚本。完成后保留加密备份、恢复报告、开通回执和销毁审计记录；不得运行 `docker volume prune` 或删除共享 `sites` 卷。
