[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]+$')]
    [string]$Site,
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$CompanyName,
    [Parameter(Mandatory)]
    [ValidateLength(1, 5)]
    [string]$CompanyAbbr,
    [Parameter(Mandatory)]
    [string]$AdminPasswordFile,
    [ValidatePattern('^[a-z0-9][a-z0-9_-]+$')]
    [string]$Project = $env:COMPOSE_PROJECT_NAME,
    [string]$ReceiptDirectory = (Join-Path $PSScriptRoot 'provisioning-receipts'),
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-EnvMap {
    param([string]$Path)
    $map = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^([^#=]+)=(.+)$') { $map[$Matches[1]] = $Matches[2] }
    }
    return $map
}

function Invoke-Compose {
    param([string[]]$Arguments)
    & docker compose --project-name $Project --env-file (Join-Path $PSScriptRoot 'versions.env') `
        -f (Join-Path $PSScriptRoot 'compose.yaml') @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed: $($Arguments -join ' ')" }
}

function Write-ProvisioningReceipt {
    param([hashtable]$Versions, [string]$ImageSha)
    [void](New-Item -ItemType Directory -Path $ReceiptDirectory -Force)
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $path = Join-Path $ReceiptDirectory "$Site-$stamp.json"
    [ordered]@{
        site = $Site
        company = [ordered]@{ name = $CompanyName; abbr = $CompanyAbbr }
        image_sha = $ImageSha
        app_versions = [ordered]@{
            frappe = $Versions.FRAPPE_REF
            erpnext = $Versions.ERPNEXT_VERSION
            hrms = $Versions.HRMS_VERSION
        }
        created_at = [DateTime]::UtcNow.ToString('o')
        operator = [Environment]::UserName
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $path -Encoding utf8NoBOM
    return $path
}

if ($Site.Length -gt 253 -or $Site.StartsWith('.') -or $Site.EndsWith('.') -or $Site.Contains('..')) {
    throw 'Site must be a valid DNS hostname.'
}
foreach ($label in $Site.Split('.')) {
    if ($label.Length -gt 63 -or $label.StartsWith('-') -or $label.EndsWith('-')) {
        throw 'Site must be a valid DNS hostname.'
    }
}
if ([string]::IsNullOrWhiteSpace($CompanyName) -or [string]::IsNullOrWhiteSpace($CompanyAbbr)) {
    throw 'CompanyName and CompanyAbbr are required.'
}
if ([string]::IsNullOrWhiteSpace($Project)) { throw 'COMPOSE_PROJECT_NAME or -Project is required.' }
$passwordPath = (Resolve-Path -LiteralPath $AdminPasswordFile -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $passwordPath -PathType Leaf) -or (Get-Item -LiteralPath $passwordPath).Length -eq 0) {
    throw 'AdminPasswordFile must be a non-empty file.'
}
if ($ValidateOnly) {
    Write-Host "Provisioning input validated for $Site."
    exit 0
}

$versions = Get-EnvMap (Join-Path $PSScriptRoot 'versions.env')
$image = [string]$env:INTERNAL_LEDGER_IMAGE
if ($image -notmatch '(?:[:@])(?<sha>[0-9a-f]{40}|sha256:[0-9a-f]{64})$') {
    throw 'INTERNAL_LEDGER_IMAGE must contain an immutable commit tag or digest.'
}
$imageSha = $Matches.sha
$savedSite = $env:SITE_NAME
$savedPasswordFile = $env:SITE_ADMIN_PASSWORD_FILE
try {
    $env:SITE_NAME = $Site
    $env:SITE_ADMIN_PASSWORD_FILE = $passwordPath
    Invoke-Compose @('--profile', 'init', 'run', '--rm', '--no-deps', '--entrypoint', 'bash',
        'create-site', '-c', "test ! -d sites/$Site")
    Invoke-Compose @('--profile', 'init', 'run', '--rm', 'create-site')
    Invoke-Compose @('run', '--rm', '--no-deps', 'backend', 'bench', '--site', $Site, 'migrate')
    $kwargs = @{ company_name = $CompanyName; company_abbr = $CompanyAbbr } | ConvertTo-Json -Compress
    Invoke-Compose @('run', '--rm', '--no-deps', 'backend', 'bench', '--site', $Site, 'execute',
        'internal_ledger.install.initialize_company', '--kwargs', $kwargs)
    $receipt = Write-ProvisioningReceipt -Versions $versions -ImageSha $imageSha
    Write-Host "Provisioned independent Site $Site. Receipt: $receipt"
}
finally {
    $env:SITE_NAME = $savedSite
    $env:SITE_ADMIN_PASSWORD_FILE = $savedPasswordFile
}
