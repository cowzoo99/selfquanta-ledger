[CmdletBinding()]
param(
    [string]$Archive = $env:INTERNAL_LEDGER_BACKUP_ARCHIVE,
    [string]$Manifest = $env:INTERNAL_LEDGER_BACKUP_MANIFEST,
    [string]$KeyFile = $env:BACKUP_KEY_FILE,
    [string]$Project = $env:INTERNAL_LEDGER_RESTORE_PROJECT,
    [string]$ReportDirectory = $env:INTERNAL_LEDGER_RESTORE_REPORT_DIR,
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-InputFile {
    param([string]$Path, [string]$Label)
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) {
        throw "$Label must be an explicit absolute path."
    }
    $full = [IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "$Label does not exist." }
    return $full
}

function Resolve-SafeReportDirectory {
    param([string]$Path, [string]$RepositoryRoot)
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) {
        throw 'INTERNAL_LEDGER_RESTORE_REPORT_DIR must be an explicit absolute path.'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -eq [IO.Path]::GetPathRoot($full)) { throw 'Restore report directory must not be a drive root.' }
    if ($full.StartsWith($RepositoryRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Restore report directory must be outside the repository.'
    }
    return $full
}

function Invoke-Compose {
    param([string[]]$Arguments, [switch]$Quiet)
    if ($Quiet) { & docker @script:ComposePrefix @Arguments *> $null }
    else { & docker @script:ComposePrefix @Arguments }
    if ($LASTEXITCODE -ne 0) { throw 'Isolated restore Docker Compose operation failed.' }
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$archivePath = Resolve-InputFile -Path $Archive -Label 'INTERNAL_LEDGER_BACKUP_ARCHIVE'
$manifestPath = Resolve-InputFile -Path $Manifest -Label 'INTERNAL_LEDGER_BACKUP_MANIFEST'
$keyPath = Resolve-InputFile -Path $KeyFile -Label 'BACKUP_KEY_FILE'
if ((Get-Item -LiteralPath $keyPath).Length -lt 32) { throw 'BACKUP_KEY_FILE must contain at least 32 bytes.' }
if ($Project -notmatch '^ledger-restore-[a-z0-9][a-z0-9-]{7,}$') {
    throw 'INTERNAL_LEDGER_RESTORE_PROJECT must be a unique ledger-restore-* name.'
}
$reportRoot = Resolve-SafeReportDirectory -Path $ReportDirectory -RepositoryRoot $repo
$expected = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
if ($expected.schema -ne 1 -or $expected.git_sha -notmatch '^[0-9a-f]{40}$') {
    throw 'Backup manifest schema or Git SHA is invalid.'
}
if ($expected.encrypted_sha256 -notmatch '^[0-9a-f]{64}$') {
    throw 'Backup manifest encrypted archive checksum is invalid.'
}
foreach ($property in @('company', 'journal_entry', 'file')) {
    if ($expected.counts.$property -notmatch '^\d+$') { throw "Backup manifest count is invalid: $property" }
}
if ($env:INTERNAL_LEDGER_IMAGE -ne "internal-ledger:$($expected.git_sha)") {
    throw 'INTERNAL_LEDGER_IMAGE must exactly match the Git SHA recorded in the manifest.'
}
if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) { throw 'openssl is required.' }
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($archiveHash -ne $expected.encrypted_sha256) { throw 'Encrypted backup checksum does not match the manifest.' }

if ($ValidateOnly) {
    Write-Host 'Restore inputs validated.'
    exit 0
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is required.' }

[void](New-Item -ItemType Directory -Path $reportRoot -Force)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$work = Join-Path $reportRoot ".$Project-$stamp"
$plainArchive = Join-Path $work 'backup.zip'
$payloadRoot = Join-Path $work 'extracted'
$reportPath = Join-Path $reportRoot "$Project-$stamp.json"
$containerDir = "/tmp/$Project-$stamp"
$restoreSite = 'restore.localhost'
$success = $false
$started = $false

try {
    if (Test-Path -LiteralPath $work) { throw 'Restore work directory already exists.' }
    [void](New-Item -ItemType Directory -Path $payloadRoot -Force)
    & openssl enc -d -aes-256-cbc -pbkdf2 -in $archivePath -out $plainArchive -pass "file:$keyPath" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Backup decryption failed.' }
    Expand-Archive -LiteralPath $plainArchive -DestinationPath $payloadRoot

    $payload = Join-Path $payloadRoot 'payload'
    foreach ($name in @('database.sql.gz', 'public-files.tgz', 'private-files.tgz', 'site-config.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $payload $name) -PathType Leaf)) {
            throw "Backup payload is missing $name."
        }
    }

    $secretRoot = Join-Path $work 'secrets'
    [void](New-Item -ItemType Directory -Path $secretRoot -Force)
    $randomRoot = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    $randomAdmin = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    Set-Content -LiteralPath (Join-Path $secretRoot 'db-root') -Value $randomRoot -NoNewline
    Set-Content -LiteralPath (Join-Path $secretRoot 'site-admin') -Value $randomAdmin -NoNewline
    Set-Content -LiteralPath (Join-Path $secretRoot 'unused-tls-cert') -Value 'restore-check-unused' -NoNewline
    Set-Content -LiteralPath (Join-Path $secretRoot 'unused-tls-key') -Value 'restore-check-unused' -NoNewline

    $env:DB_ROOT_PASSWORD_FILE = Join-Path $secretRoot 'db-root'
    $env:SITE_ADMIN_PASSWORD_FILE = Join-Path $secretRoot 'site-admin'
    $env:TLS_CERTIFICATE_FILE = Join-Path $secretRoot 'unused-tls-cert'
    $env:TLS_PRIVATE_KEY_FILE = Join-Path $secretRoot 'unused-tls-key'
    $env:SITE_NAME = $restoreSite
    $env:HTTP_BIND = '127.0.0.1'
    $env:HTTPS_BIND = '127.0.0.1'
    $env:HTTP_PORT = '0'
    $env:HTTPS_PORT = '0'

    $script:ComposePrefix = @(
        'compose',
        '--env-file', (Join-Path $PSScriptRoot 'versions.env'),
        '-f', (Join-Path $PSScriptRoot 'compose.yaml'),
        '--project-name', $Project
    )
    Invoke-Compose -Quiet -Arguments @('up', '-d', 'db', 'redis-cache', 'redis-queue', 'configurator', 'backend')
    $started = $true
    Invoke-Compose -Quiet -Arguments @('--profile', 'init', 'run', '--rm', 'create-site')
    Invoke-Compose -Quiet -Arguments @('exec', '-T', 'backend', 'mkdir', '-p', $containerDir)
    Invoke-Compose -Quiet -Arguments @('cp', "$payload/.", "backend:$containerDir")

    $restoreCommand = @(
        'exec', '-T', 'backend', 'bash', '-c',
        "bench --site '$restoreSite' restore --force --db-root-password `"`$(cat /run/secrets/db_root_password)`" --with-public-files '$containerDir/public-files.tgz' --with-private-files '$containerDir/private-files.tgz' '$containerDir/database.sql.gz'"
    )
    Invoke-Compose -Quiet -Arguments $restoreCommand
    Invoke-Compose -Quiet -Arguments @('exec', '-T', 'backend', 'bench', '--site', $restoreSite, 'migrate')

    $sql = 'SELECT (SELECT COUNT(*) FROM `tabCompany`),(SELECT COUNT(*) FROM `tabJournal Entry`),(SELECT COUNT(*) FROM `tabFile`);'
    $countsText = & docker @script:ComposePrefix exec -T backend bench --site $restoreSite mariadb --skip-column-names --batch -e $sql 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read restored verification counts.' }
    $actualValues = (($countsText | Out-String).Trim() -split '\s+')
    if ($actualValues.Count -ne 3 -or ($actualValues | Where-Object { $_ -notmatch '^\d+$' })) {
        throw 'Unexpected restored verification count output.'
    }
    $actual = [ordered]@{
        company = [int64]$actualValues[0]
        journal_entry = [int64]$actualValues[1]
        file = [int64]$actualValues[2]
    }
    $success =
        $actual.company -eq [int64]$expected.counts.company -and
        $actual.journal_entry -eq [int64]$expected.counts.journal_entry -and
        $actual.file -eq [int64]$expected.counts.file
    if (-not $success) { throw 'Restored verification counts do not match the manifest.' }

    [ordered]@{
        status = 'PASS'
        checked_utc = [DateTime]::UtcNow.ToString('o')
        project = $Project
        source_manifest = $manifestPath
        git_sha = $expected.git_sha
        expected = $expected.counts
        actual = $actual
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding utf8NoBOM
    Write-Host "Restore check passed: $reportPath"
}
catch {
    [ordered]@{
        status = 'FAIL'
        checked_utc = [DateTime]::UtcNow.ToString('o')
        project = $Project
        source_manifest = $manifestPath
        message = $_.Exception.Message
        diagnostic = if ($started) { "Isolated project retained: $Project" } else { 'Containers were not started.' }
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding utf8NoBOM
    throw
}
finally {
    if ($started) {
        try { Invoke-Compose -Quiet -Arguments @('exec', '-T', 'backend', 'rm', '-rf', '--', $containerDir) } catch {}
    }
    if ($success) {
        Invoke-Compose -Quiet -Arguments @('down', '--volumes', '--remove-orphans')
    }
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}
