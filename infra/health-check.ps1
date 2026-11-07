[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SitesFile,
    [ValidateRange(2, 60)][int]$TimeoutSeconds = 10,
    [ValidateRange(1, 90)][int]$CertificateWarningDays = 14,
    [ValidateRange(1, 168)][int]$MaxBackupAgeHours = 26,
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-Sites {
    param([string]$Path)
    if (-not [IO.Path]::IsPathRooted($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'SitesFile must be an existing absolute JSON file.'
    }
    $document = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -AsHashtable
    if (-not $document.ContainsKey('sites') -or @($document.sites).Count -eq 0) {
        throw 'SitesFile must contain one explicit sites list.'
    }
    return @($document.sites)
}

function Get-CertificateExpiry {
    param([string]$HostName)
    $tcp = [Net.Sockets.TcpClient]::new()
    try {
        $tcp.ConnectAsync($HostName, 443).WaitAsync([TimeSpan]::FromSeconds($TimeoutSeconds)).GetAwaiter().GetResult()
        $ssl = [Net.Security.SslStream]::new($tcp.GetStream(), $false, { $true })
        try {
            $ssl.AuthenticateAsClientAsync($HostName).WaitAsync([TimeSpan]::FromSeconds($TimeoutSeconds)).GetAwaiter().GetResult()
            return [DateTime]$ssl.RemoteCertificate.NotAfter
        }
        finally { $ssl.Dispose() }
    }
    finally { $tcp.Dispose() }
}

$sites = Read-Sites -Path $SitesFile
foreach ($field in @('Site', 'HostName', 'Project', 'Destination')) {
    if (@($sites | Where-Object { -not $_.ContainsKey($field) -or [string]::IsNullOrWhiteSpace([string]$_[$field]) }).Count) {
        throw "Every Site entry requires $field."
    }
}
foreach ($field in @('Site', 'HostName', 'Project')) {
    if (@($sites | Group-Object { [string]$_[$field] } | Where-Object Count -gt 1).Count) {
        throw "Every Site requires a unique $field value."
    }
}
if ($ValidateOnly) {
    Write-Host 'Health-check inputs validated.'
    exit 0
}

$results = @()
foreach ($site in $sites) {
    $checks = [ordered]@{}
    $blocking = $false
    try {
        $uri = "https://$($site.HostName)/api/method/ping"
        $response = Invoke-WebRequest -Uri $uri -Method Get -TimeoutSec $TimeoutSeconds -MaximumRedirection 0
        $checks.https = if ([int]$response.StatusCode -eq 200) { 'PASS' } else { "FAIL:$([int]$response.StatusCode)" }
        if ($checks.https -ne 'PASS') { $blocking = $true }
    }
    catch {
        $socket = $_.Exception
        while ($socket.InnerException) { $socket = $socket.InnerException }
        $checks.https = if ($socket -is [Net.Sockets.SocketException] -and $socket.SocketErrorCode -eq 'HostNotFound') { 'FAIL:unknown_host' } else { 'FAIL:unreachable' }
        $blocking = $true
    }
    try {
        $notAfter = Get-CertificateExpiry -HostName ([string]$site.HostName)
        $checks.certificate_not_after = $notAfter.ToUniversalTime().ToString('o')
        if ($notAfter.ToUniversalTime() -le [DateTime]::UtcNow.AddDays($CertificateWarningDays)) {
            $checks.certificate = 'FAIL:expiry'
            $blocking = $true
        } else { $checks.certificate = 'PASS' }
    }
    catch { $checks.certificate = 'FAIL:unavailable'; $blocking = $true }

    $compose = @('compose', '--env-file', (Join-Path $PSScriptRoot 'versions.env'), '-f', (Join-Path $PSScriptRoot 'compose.yaml'), '--project-name', [string]$site.Project)
    try {
        $running = @(& docker @compose ps --status running --services 2>$null)
        if ($LASTEXITCODE -ne 0) { throw 'Docker service probe failed.' }
    }
    catch { $running = @() }
    foreach ($service in @('backend', 'scheduler', 'queue-short', 'queue-long')) {
        $checks[$service] = if ($running -contains $service) { 'PASS' } else { 'FAIL:not_running' }
        if ($checks[$service] -ne 'PASS') { $blocking = $true }
    }

    $daily = Join-Path ([string]$site.Destination) 'daily'
    $latest = if (Test-Path -LiteralPath $daily -PathType Container) {
        Get-ChildItem -LiteralPath $daily -File -Filter '*.zip.enc' | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    }
    if (-not $latest) {
        $checks.backup = 'FAIL:No encrypted backup found'
        $blocking = $true
    } else {
        $age = ([DateTime]::UtcNow - $latest.LastWriteTimeUtc).TotalHours
        $checks.backup_age_hours = [Math]::Round($age, 1)
        $checks.backup = if ($age -le $MaxBackupAgeHours) { 'PASS' } else { 'FAIL:stale' }
        if ($checks.backup -ne 'PASS') { $blocking = $true }
    }
    $results += [ordered]@{ site = [string]$site.Site; status = if ($blocking) { 'FAIL' } else { 'PASS' }; checks = $checks }
}

$results | ConvertTo-Json -Depth 6
if (@($results | Where-Object status -eq 'FAIL').Count) { exit 1 }
