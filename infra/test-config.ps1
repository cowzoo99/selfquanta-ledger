[CmdletBinding()]
param(
    [string]$InfraRoot = $PSScriptRoot,
    [switch]$StaticOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Get-ServiceBlock {
    param([string]$Yaml, [string]$Name)
    $pattern = "(?ms)^  $([regex]::Escape($Name)):\r?\n(?<body>.*?)(?=^  [A-Za-z0-9_-]+:\r?$|^(secrets|configs|volumes|networks):\r?$|\z)"
    $match = [regex]::Match($Yaml, $pattern)
    Assert-True $match.Success "Missing Compose service: $Name"
    return $match.Groups['body'].Value
}

$required = @(
    'versions.env',
    'compose.yaml',
    'nginx.conf',
    'build_image.ps1',
    'backup.ps1',
    'restore-check.ps1'
)
foreach ($name in $required) {
    $path = Join-Path $InfraRoot $name
    Assert-True (Test-Path -LiteralPath $path -PathType Leaf) "Missing required file: $name"
}

$versions = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'versions.env')
Assert-True ($versions -match '(?m)^ERPNEXT_VERSION=v16\.52\.1$') 'ERPNext version is not pinned to v16.52.1'
Assert-True ($versions -match '(?m)^FRAPPE_REF=v16\.52\.0$') 'Frappe ref is not pinned to v16.52.0'
Assert-True ($versions -match '(?m)^DB_IMAGE=mariadb:11\.8$') 'MariaDB image is not pinned to 11.8'
Assert-True ($versions -match '(?m)^REDIS_IMAGE=redis:8\.2-alpine$') 'Redis image is not pinned to 8.2-alpine'
Assert-True ($versions -notmatch '(?im):latest\s*$') 'Mutable latest tag is forbidden'

$compose = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'compose.yaml')
foreach ($internalService in @('db', 'redis-cache', 'redis-queue', 'configurator', 'create-site', 'backend', 'frontend', 'websocket', 'queue-short', 'queue-long', 'scheduler')) {
    Assert-True ((Get-ServiceBlock -Yaml $compose -Name $internalService) -notmatch '(?m)^\s{4}ports:\s*$') "$internalService must not publish host ports"
}
$gateway = Get-ServiceBlock -Yaml $compose -Name 'gateway'
Assert-True ($gateway -match '(?m)^\s{4}ports:\s*$') 'Gateway must publish HTTP/HTTPS ports'
Assert-True ($gateway -match 'HTTP_PORT:-80}:8080') 'HTTP port 80 must map to the gateway'
Assert-True ($gateway -match 'HTTPS_PORT:-443}:8443') 'HTTPS port 443 must map to the gateway'
Assert-True ($compose -notmatch '(?im)(password|secret|token)[ \t]*:[ \t]*[^$\r\n{][^\r\n]*') 'Compose must not embed secrets'
Assert-True ($compose -match '(?m)^\s{4}restart:\s+unless-stopped\s*$') 'Long-running services need restart policies'
Assert-True ($compose -match '(?m)^\s{4}healthcheck:\s*$') 'Compose needs health checks'
Assert-True ($compose -match '(?m)^volumes:\s*$') 'Compose needs named volumes'
Assert-True ($compose -match '(?ms)^  internal:\r?\n    internal: true\s*$') 'Application data services need an isolated internal network'
Assert-True ($compose -notmatch '(?im)network_mode:\s*host') 'Host networking is forbidden'

$build = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'build_image.ps1')
Assert-True ($build -match 'remote get-url origin') 'Build must require the origin remote'
Assert-True ($build -match "'rev-parse', 'HEAD'") 'Build must derive the exact Git SHA'
Assert-True ($build -match '--secret=id=apps_json,src=') 'Build must pass apps.json as a BuildKit secret'
Assert-True ($build -match 'images/layered/Containerfile') 'Build must use the official layered Containerfile'
Assert-True ($build -notmatch 'APPS_JSON_BASE64') 'apps.json must not be passed as a build argument'

$backup = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'backup.ps1')
Assert-True ($backup -match 'BACKUP_KEY_FILE') 'Backup must require an external key file'
Assert-True ($backup -match 'openssl') 'Backup must encrypt the archive'
Assert-True ($backup -match 'Get-FileHash') 'Backup must emit a checksum'
Assert-True ($backup -notmatch 'Write-(Host|Output).*key') 'Backup must not log key material'

$restore = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'restore-check.ps1')
Assert-True ($restore -match 'INTERNAL_LEDGER_RESTORE_PROJECT') 'Restore must require an isolated project name'
Assert-True ($restore -notmatch 'docker\s+(system|volume)\s+prune') 'Broad Docker cleanup is forbidden'
Assert-True ($restore -match 'tabCompany') 'Restore must verify Company count'
Assert-True ($restore -match 'tabJournal Entry') 'Restore must verify Journal Entry count'
Assert-True ($restore -match 'tabFile') 'Restore must verify attachment count'

$scripts = @('test-config.ps1', 'build_image.ps1', 'backup.ps1', 'restore-check.ps1')
foreach ($name in $scripts) {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $InfraRoot $name),
        [ref]$tokens,
        [ref]$errors
    )
    Assert-True ($errors.Count -eq 0) "PowerShell parse error in $name"
}

if ($StaticOnly) {
    Write-Host 'Infrastructure static checks passed.'
    exit 0
}

function Assert-CommandFailsLike {
    param([scriptblock]$Command, [string]$Pattern, [string]$Message)
    $output = & $Command 2>&1 | Out-String
    Assert-True ($LASTEXITCODE -ne 0) "$Message did not fail"
    Assert-True ($output -match $Pattern) "$Message failed for an unexpected reason"
}

$repositoryRoot = (Resolve-Path (Join-Path $InfraRoot '..')).Path
$scratch = Join-Path $repositoryRoot '.infra-test-scratch'
$externalScratch = Join-Path (Split-Path $repositoryRoot -Parent) '.internal-ledger-infra-test'
$oldImage = $env:INTERNAL_LEDGER_IMAGE
try {
    foreach ($path in @($scratch, $externalScratch)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
        [void](New-Item -ItemType Directory -Path $path -Force)
    }

    function New-MutatedInfra {
        param([string]$Name)
        $case = Join-Path $scratch $Name
        [void](New-Item -ItemType Directory -Path $case)
        foreach ($file in $required) {
            Copy-Item -LiteralPath (Join-Path $InfraRoot $file) -Destination $case
        }
        return $case
    }

    $case = New-MutatedInfra 'mutable-version'
    (Get-Content -Raw (Join-Path $case 'versions.env')).Replace('ERPNEXT_VERSION=v16.52.1', 'ERPNEXT_VERSION=latest') |
        Set-Content -LiteralPath (Join-Path $case 'versions.env')
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'ERPNext version|latest' 'Mutable version test'

    $case = New-MutatedInfra 'unsafe-db-port'
    (Get-Content -Raw (Join-Path $case 'compose.yaml')).Replace("  db:`n", "  db:`n    ports:`n      - `"3306:3306`"`n") |
        Set-Content -LiteralPath (Join-Path $case 'compose.yaml')
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'db must not publish' 'Unsafe database port test'

    $case = New-MutatedInfra 'embedded-secret'
    Add-Content -LiteralPath (Join-Path $case 'compose.yaml') -Value "`n  leaked_password: plaintext"
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'embed secrets' 'Embedded secret test'

    $case = New-MutatedInfra 'broad-cleanup'
    Add-Content -LiteralPath (Join-Path $case 'restore-check.ps1') -Value "`ndocker system prune"
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'Broad Docker cleanup' 'Broad cleanup test'

    $key = Join-Path $externalScratch 'backup.key'
    Set-Content -LiteralPath $key -Value ('x' * 40) -NoNewline
    $sha = '1111111111111111111111111111111111111111'
    $env:INTERNAL_LEDGER_IMAGE = "internal-ledger:$sha"
    $plain = Join-Path $externalScratch 'plain.txt'
    $cipher = Join-Path $externalScratch 'cipher.bin'
    $roundTrip = Join-Path $externalScratch 'round-trip.txt'
    Set-Content -LiteralPath $plain -Value 'sanitized-backup-fixture' -NoNewline
    & openssl enc -aes-256-cbc -pbkdf2 -salt -in $plain -out $cipher -pass "file:$key" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Local backup encryption test failed.' }
    & openssl enc -d -aes-256-cbc -pbkdf2 -in $cipher -out $roundTrip -pass "file:$key" 2>$null
    if ($LASTEXITCODE -ne 0 -or (Get-Content -Raw $roundTrip) -ne 'sanitized-backup-fixture') {
        throw 'Local backup decryption test failed.'
    }
    $safeDestination = Join-Path $externalScratch 'backups'
    [void](New-Item -ItemType Directory -Path $safeDestination)
    Assert-CommandFailsLike {
        pwsh -NoProfile -File (Join-Path $InfraRoot 'backup.ps1') -Destination ([IO.Path]::GetPathRoot($safeDestination)) -KeyFile $key -Site ledger.localhost -Project ledgerprod -ValidateOnly
    } 'drive root' 'Unsafe backup destination test'
    Assert-CommandFailsLike {
        pwsh -NoProfile -File (Join-Path $InfraRoot 'backup.ps1') -Destination $safeDestination -KeyFile (Join-Path $externalScratch 'missing.key') -Site ledger.localhost -Project ledgerprod -ValidateOnly
    } 'does not exist' 'Missing backup key test'

    $archive = Join-Path $externalScratch 'sample.zip.enc'
    Set-Content -LiteralPath $archive -Value 'validation-only' -NoNewline
    $manifest = Join-Path $externalScratch 'sample.manifest.json'
    [ordered]@{
        schema = 1
        git_sha = $sha
        encrypted_sha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        counts = [ordered]@{ company = 1; journal_entry = 2; file = 3 }
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifest
    Assert-CommandFailsLike {
        pwsh -NoProfile -File (Join-Path $InfraRoot 'restore-check.ps1') -Archive $archive -Manifest $manifest -KeyFile $key -Project production -ReportDirectory (Join-Path $externalScratch 'reports') -ValidateOnly
    } 'ledger-restore' 'Unsafe restore project name test'

    & (Join-Path $InfraRoot 'backup.ps1') -Destination $safeDestination -KeyFile $key -Site ledger.localhost -Project ledgerprod -ValidateOnly
    if ($LASTEXITCODE -ne 0) { throw 'Valid backup input test failed.' }
    & (Join-Path $InfraRoot 'restore-check.ps1') -Archive $archive -Manifest $manifest -KeyFile $key -Project ledger-restore-validation -ReportDirectory (Join-Path $externalScratch 'reports') -ValidateOnly
    if ($LASTEXITCODE -ne 0) { throw 'Valid restore input test failed.' }
}
finally {
    $env:INTERNAL_LEDGER_IMAGE = $oldImage
    foreach ($path in @($scratch, $externalScratch)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
}

Write-Host 'Infrastructure configuration checks passed.'
