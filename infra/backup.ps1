[CmdletBinding()]
param(
    [string]$Destination = $env:INTERNAL_LEDGER_BACKUP_DESTINATION,
    [string]$KeyFile = $env:BACKUP_KEY_FILE,
    [string]$Site = $env:SITE_NAME,
    [string]$Project = $env:COMPOSE_PROJECT_NAME,
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { throw "$Name is required." }
}

function Resolve-SafeDirectory {
    param([string]$Path, [string]$RepositoryRoot)
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) {
        throw 'INTERNAL_LEDGER_BACKUP_DESTINATION must be an explicit absolute directory.'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -eq [IO.Path]::GetPathRoot($full)) { throw 'Backup destination must not be a drive root.' }
    if ($full.StartsWith($RepositoryRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Backup destination must be outside the repository and its Compose project.'
    }
    return $full
}

function Invoke-Compose {
    param([string[]]$Arguments, [switch]$Quiet)
    if ($Quiet) {
        & docker @script:ComposePrefix @Arguments *> $null
    }
    else {
        & docker @script:ComposePrefix @Arguments
    }
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose backup operation failed.' }
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$destinationRoot = Resolve-SafeDirectory -Path $Destination -RepositoryRoot $repo
if ([string]::IsNullOrWhiteSpace($KeyFile) -or -not [IO.Path]::IsPathRooted($KeyFile)) {
    throw 'BACKUP_KEY_FILE must be an explicit absolute path.'
}
$keyPath = [IO.Path]::GetFullPath($KeyFile)
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'BACKUP_KEY_FILE does not exist.' }
if ((Get-Item -LiteralPath $keyPath).Length -lt 32) { throw 'BACKUP_KEY_FILE must contain at least 32 bytes.' }
if ($Site -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]+$') { throw 'SITE_NAME is invalid.' }
if ([string]::IsNullOrWhiteSpace($Project) -or $Project -notmatch '^[a-z0-9][a-z0-9_-]+$') {
    throw 'COMPOSE_PROJECT_NAME is invalid.'
}
if ($env:INTERNAL_LEDGER_IMAGE -notmatch '^internal-ledger:([0-9a-f]{40})$') {
    throw 'INTERNAL_LEDGER_IMAGE must be an exact internal-ledger Git SHA tag.'
}
$imageSha = $Matches[1]
Assert-Command openssl

if ($ValidateOnly) {
    Write-Host 'Backup inputs validated.'
    exit 0
}

Assert-Command docker
[void](New-Item -ItemType Directory -Path $destinationRoot -Force)
$daily = Join-Path $destinationRoot 'daily'
$monthly = Join-Path $destinationRoot 'monthly'
[void](New-Item -ItemType Directory -Path $daily -Force)
[void](New-Item -ItemType Directory -Path $monthly -Force)

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$safeSite = $Site -replace '[^A-Za-z0-9.-]', '_'
$baseName = "internal-ledger-$safeSite-$stamp"
$staging = Join-Path $destinationRoot ".$baseName-staging"
$plainArchive = Join-Path $destinationRoot ".$baseName.zip"
$encrypted = Join-Path $daily "$baseName.zip.enc"
$manifestPath = Join-Path $daily "$baseName.manifest.json"
$containerDir = "/tmp/$baseName"
$script:ComposePrefix = @(
    'compose',
    '--env-file', (Join-Path $PSScriptRoot 'versions.env'),
    '-f', (Join-Path $PSScriptRoot 'compose.yaml'),
    '--project-name', $Project
)

try {
    if (Test-Path -LiteralPath $staging) { throw 'Backup staging path already exists.' }
    [void](New-Item -ItemType Directory -Path (Join-Path $staging 'payload') -Force)
    Invoke-Compose -Quiet -Arguments @('exec', '-T', 'backend', 'mkdir', '-p', $containerDir)

    $sql = 'SELECT (SELECT COUNT(*) FROM `tabCompany`),(SELECT COUNT(*) FROM `tabJournal Entry`),(SELECT COUNT(*) FROM `tabFile`);'
    $countsText = & docker @script:ComposePrefix exec -T backend bench --site $Site mariadb --skip-column-names --batch -e $sql 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read pre-backup verification counts.' }
    $counts = (($countsText | Out-String).Trim() -split '\s+')
    if ($counts.Count -ne 3 -or ($counts | Where-Object { $_ -notmatch '^\d+$' })) {
        throw 'Unexpected pre-backup verification count output.'
    }

    Invoke-Compose -Quiet -Arguments @(
        'exec', '-T', 'backend',
        'bench', '--site', $Site, 'backup', '--with-files', '--compress',
        '--backup-path-db', "$containerDir/database.sql.gz",
        '--backup-path-files', "$containerDir/public-files.tgz",
        '--backup-path-private-files', "$containerDir/private-files.tgz",
        '--backup-path-conf', "$containerDir/site-config.json"
    )
    Invoke-Compose -Quiet -Arguments @('cp', "backend:$containerDir/.", (Join-Path $staging 'payload'))

    $manifest = [ordered]@{
        schema = 1
        created_utc = [DateTime]::UtcNow.ToString('o')
        site = $Site
        git_sha = $imageSha
        counts = [ordered]@{
            company = [int64]$counts[0]
            journal_entry = [int64]$counts[1]
            file = [int64]$counts[2]
        }
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $staging 'manifest.json') -Encoding utf8NoBOM
    Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $plainArchive -CompressionLevel Optimal

    & openssl enc -aes-256-cbc -pbkdf2 -salt -in $plainArchive -out $encrypted -pass "file:$keyPath" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Backup encryption failed.' }
    $hash = (Get-FileHash -LiteralPath $encrypted -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifest['encrypted_sha256'] = $hash
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM

    $monthlyArchive = Join-Path $monthly "internal-ledger-$safeSite-$(Get-Date -Format 'yyyyMM').zip.enc"
    $monthlyManifest = Join-Path $monthly "internal-ledger-$safeSite-$(Get-Date -Format 'yyyyMM').manifest.json"
    if (-not (Test-Path -LiteralPath $monthlyArchive)) {
        Copy-Item -LiteralPath $encrypted -Destination $monthlyArchive
        Copy-Item -LiteralPath $manifestPath -Destination $monthlyManifest
    }

    $dailyGroups = @(Get-ChildItem -LiteralPath $daily -Filter "internal-ledger-$safeSite-*.zip.enc" -File |
        Group-Object { $_.Name.Substring(("internal-ledger-$safeSite-").Length, 8) } |
        Sort-Object Name -Descending)
    for ($index = 0; $index -lt $dailyGroups.Count; $index++) {
        $archives = @($dailyGroups[$index].Group | Sort-Object LastWriteTimeUtc -Descending)
        $remove = if ($index -lt 7) { @($archives | Select-Object -Skip 1) } else { $archives }
        foreach ($archive in $remove) {
            $sidecar = [IO.Path]::ChangeExtension([IO.Path]::ChangeExtension($archive.FullName, $null), '.manifest.json')
            Remove-Item -LiteralPath $archive.FullName -Force
            if (Test-Path -LiteralPath $sidecar) { Remove-Item -LiteralPath $sidecar -Force }
        }
    }

    Write-Host "Backup completed: $stamp"
    Write-Host "Archive: $encrypted"
    Write-Host "SHA256: $hash"
}
finally {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        try { Invoke-Compose -Quiet -Arguments @('exec', '-T', 'backend', 'rm', '-rf', '--', $containerDir) } catch {}
    }
    if (Test-Path -LiteralPath $plainArchive) { Remove-Item -LiteralPath $plainArchive -Force }
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
