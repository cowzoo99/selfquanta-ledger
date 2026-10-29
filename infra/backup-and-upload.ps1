[CmdletBinding()]
param(
    [string]$Destination = $env:INTERNAL_LEDGER_BACKUP_DESTINATION,
    [string]$CosUri = $env:COS_BACKUP_URI,
    [string]$CosConfig = $env:COSCLI_CONFIG_FILE,
    [string]$CosExecutable = '/usr/local/bin/coscli'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Destination) -or -not [IO.Path]::IsPathFullyQualified($Destination)) {
    throw 'Backup destination must be an explicit absolute directory.'
}
if ([string]::IsNullOrWhiteSpace($CosConfig) -or -not [IO.Path]::IsPathFullyQualified($CosConfig)) {
    throw 'COS config must be an explicit absolute file path.'
}
if (-not (Test-Path -LiteralPath $CosConfig -PathType Leaf)) { throw 'COS config does not exist.' }
if ($CosUri -notmatch '^cos://[A-Za-z0-9][A-Za-z0-9.-]*/[^\s]*$') { throw 'COS destination must be a cos:// URI with a backup prefix.' }
if (-not [IO.Path]::IsPathFullyQualified($CosExecutable) -or -not (Test-Path -LiteralPath $CosExecutable -PathType Leaf)) {
    throw 'COSCLI executable must exist at an absolute path.'
}
if ((Test-Path -LiteralPath $Destination) -and ((Get-Item -LiteralPath $Destination -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Backup root must not be a symbolic link.'
}

# backup.ps1 exclusively owns encryption, manifests, cleanup and retention.
& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'backup.ps1') -Destination $Destination *> $null
if ($LASTEXITCODE -ne 0) { throw 'Backup failed; COS upload was not attempted.' }

# A previous interrupted backup can leave plaintext staging files behind.
foreach ($item in (Get-ChildItem -LiteralPath $Destination -Recurse -Force)) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        (-not $item.PSIsContainer -and $item.Name -notmatch '\.(zip\.enc|manifest\.json)$')) {
        throw 'Backup root contains an unsafe upload entry; COS upload was not attempted.'
    }
}

# Suppress external output: COS config contents and key material must never enter logs.
& $CosExecutable sync $Destination $CosUri --recursive -c $CosConfig *> $null
$cosExit = $LASTEXITCODE
if ($cosExit -ne 0) {
    Write-Error 'COS upload failed.' -ErrorAction Continue
    exit $cosExit
}
Write-Host 'Encrypted backup uploaded to COS.'
