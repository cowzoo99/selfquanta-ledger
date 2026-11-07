[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SitesFile,
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

$sites = Read-Sites -Path $SitesFile
foreach ($field in @('Site', 'Project', 'Destination', 'KeyFile', 'RetentionDailyDays', 'RetentionMonthlyMonths')) {
    if (@($sites | Where-Object { -not $_.ContainsKey($field) -or [string]::IsNullOrWhiteSpace([string]$_[$field]) }).Count) {
        throw "Every Site entry requires $field."
    }
}
foreach ($field in @('Site', 'Project')) {
    $duplicates = @($sites | Group-Object { [string]$_[$field] } | Where-Object Count -gt 1)
    if ($duplicates.Count) { throw "Every Site requires a unique $field value." }
}
$sharedDestinations = @($sites | Group-Object { [IO.Path]::GetFullPath([string]$_.Destination) } | Where-Object Count -gt 1)
if ($sharedDestinations.Count) { throw 'Every Site requires a separate backup destination.' }
$sharedKeys = @($sites | Group-Object { [IO.Path]::GetFullPath([string]$_.KeyFile) } | Where-Object Count -gt 1)
if ($sharedKeys.Count) { throw 'Every Site requires a separate key file; shared backup key path is forbidden.' }

$results = @()
foreach ($site in $sites) {
    try {
        $arguments = @{
            Destination = [string]$site.Destination
            KeyFile = [string]$site.KeyFile
            Site = [string]$site.Site
            Project = [string]$site.Project
            RetentionDailyDays = [int]$site.RetentionDailyDays
            RetentionMonthlyMonths = [int]$site.RetentionMonthlyMonths
            ValidateOnly = $ValidateOnly
        }
        & (Join-Path $PSScriptRoot 'backup.ps1') @arguments *> $null
        if (-not $ValidateOnly) {
            $plain = @(Get-ChildItem -LiteralPath ([string]$site.Destination) -Recurse -File -Filter '*.zip')
            if ($plain.Count) { throw 'Unencrypted backup output detected.' }
            $latest = Get-ChildItem -LiteralPath (Join-Path ([string]$site.Destination) 'daily') -File -Filter '*.zip.enc' |
                Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            if (-not $latest) { throw 'Encrypted backup output is missing.' }
        }
        $results += [ordered]@{ site = [string]$site.Site; status = 'PASS'; message = if ($ValidateOnly) { 'validated' } else { 'encrypted backup created' } }
    }
    catch {
        $results += [ordered]@{ site = [string]$site.Site; status = 'FAIL'; message = $_.Exception.Message }
        continue
    }
}

$results | ConvertTo-Json -Depth 4
if (@($results | Where-Object status -eq 'FAIL').Count) { exit 1 }
