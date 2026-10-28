[CmdletBinding()]
param(
    [string]$FrappeDockerPath = $env:FRAPPE_DOCKER_DIR,
    [string]$AppRef = $env:INTERNAL_LEDGER_APP_REF,
    [string]$BuildTemp = $env:INTERNAL_LEDGER_BUILD_TEMP,
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Git {
    param([string[]]$Arguments, [string]$WorkingDirectory = $PWD.Path)
    $output = & git -C $WorkingDirectory @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Git prerequisite failed: git $($Arguments -join ' ')"
    }
    return ($output | Out-String).Trim()
}

function Get-EnvMap {
    param([string]$Path)
    $map = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^([^#=]+)=(.+)$') { $map[$Matches[1]] = $Matches[2] }
    }
    return $map
}

function Assert-SafeScratch {
    param([string]$Path, [string]$RepositoryRoot)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw 'INTERNAL_LEDGER_BUILD_TEMP must be an explicit absolute path outside the repository.'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if (-not [IO.Path]::IsPathRooted($Path) -or $full -eq [IO.Path]::GetPathRoot($full)) {
        throw 'INTERNAL_LEDGER_BUILD_TEMP must not be relative or a drive root.'
    }
    if ($full.StartsWith($RepositoryRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'INTERNAL_LEDGER_BUILD_TEMP must be outside the repository.'
    }
    return $full
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$versions = Get-EnvMap (Join-Path $PSScriptRoot 'versions.env')
$dirty = Invoke-Git -Arguments @('status', '--porcelain', '--untracked-files=normal') -WorkingDirectory $repo
if ($dirty) { throw 'Worktree must be clean before building an image.' }

$sha = Invoke-Git -Arguments @('rev-parse', 'HEAD') -WorkingDirectory $repo
if ($sha -notmatch '^[0-9a-f]{40}$') { throw 'Unable to derive an exact Git SHA.' }

$origin = (& git -C $repo remote get-url origin 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($origin)) {
    throw "Git remote 'origin' is required before an image can be built."
}
if ($origin -notmatch '^(https://|ssh://|git@)') {
    throw "Git remote 'origin' must be an HTTP(S) or SSH remote."
}
if ($origin -match '^https?://[^/]*@') {
    throw "Git remote 'origin' must not embed credentials."
}
if ([string]::IsNullOrWhiteSpace($AppRef)) {
    throw 'INTERNAL_LEDGER_APP_REF must name a remote branch or tag that points to HEAD.'
}
& git check-ref-format --branch $AppRef *> $null
if ($LASTEXITCODE -ne 0 -or $AppRef.StartsWith('refs/')) {
    throw 'INTERNAL_LEDGER_APP_REF must be a short valid Git branch or tag name.'
}
$remoteLines = Invoke-Git -Arguments @(
    'ls-remote', '--exit-code', 'origin',
    "refs/heads/$AppRef", "refs/tags/$AppRef", "refs/tags/$AppRef^{}"
) -WorkingDirectory $repo
$remoteShas = @($remoteLines -split '\r?\n' | ForEach-Object { ($_ -split '\s+')[0] })
if ($sha -notin $remoteShas) {
    throw 'The requested remote app ref does not point to the current HEAD.'
}

if ([string]::IsNullOrWhiteSpace($FrappeDockerPath)) {
    throw 'FRAPPE_DOCKER_DIR must point to the pinned official frappe_docker checkout.'
}
$frappeDocker = (Resolve-Path -LiteralPath $FrappeDockerPath).Path
$frappeDockerHead = Invoke-Git -Arguments @('rev-parse', 'HEAD') -WorkingDirectory $frappeDocker
if ($frappeDockerHead -ne $versions.FRAPPE_DOCKER_COMMIT) {
    throw 'frappe_docker checkout does not match the pinned commit.'
}
if (Invoke-Git -Arguments @('status', '--porcelain', '--untracked-files=normal') -WorkingDirectory $frappeDocker) {
    throw 'frappe_docker checkout must be clean.'
}
$containerfile = Join-Path $frappeDocker 'images/layered/Containerfile'
if (-not (Test-Path -LiteralPath $containerfile -PathType Leaf)) {
    throw 'Pinned frappe_docker checkout lacks images/layered/Containerfile.'
}

$scratchRoot = Assert-SafeScratch -Path $BuildTemp -RepositoryRoot $repo
if ($ValidateOnly) {
    Write-Host "Image prerequisites validated for commit $sha."
    exit 0
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is required.' }
& docker buildx version *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker Buildx with BuildKit secret support is required.' }

$scratch = Join-Path $scratchRoot "internal-ledger-build-$sha"
$appsPath = Join-Path $scratch 'apps.json'
if (Test-Path -LiteralPath $scratch) { throw 'Build scratch directory already exists.' }

try {
    [void](New-Item -ItemType Directory -Path $scratch -Force)
    @(
        [ordered]@{ url = 'https://github.com/frappe/erpnext'; branch = $versions.ERPNEXT_VERSION },
        [ordered]@{ url = $origin; branch = $AppRef }
    ) | ConvertTo-Json | Set-Content -LiteralPath $appsPath -Encoding utf8NoBOM

    $image = "internal-ledger:$sha"
    $arguments = @(
        'build',
        "--build-arg=FRAPPE_PATH=https://github.com/frappe/frappe",
        "--build-arg=FRAPPE_BRANCH=$($versions.FRAPPE_REF)",
        "--build-arg=CACHE_BUST=$sha",
        "--secret=id=apps_json,src=$appsPath",
        "--tag=$image",
        "--file=$containerfile",
        $frappeDocker
    )
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Image build failed.' }

    Write-Host "Built image: $image"
    & docker run --rm $image bench version
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read the built application version list.' }
}
finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force
    }
}
