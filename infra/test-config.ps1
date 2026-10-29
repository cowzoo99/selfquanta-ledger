[CmdletBinding()]
param(
    [string]$InfraRoot = $PSScriptRoot,
    [switch]$StaticOnly,
    [switch]$PrivateAccessOnly,
    [switch]$BackupUploadOnly
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
    'runtime.env.example',
    'host/bootstrap-ubuntu.sh',
    'host/configure-private-access.sh',
    'systemd/selfquanta-ledger.service',
    'host/install-systemd-units.sh',
    'compose.yaml',
    'nginx.conf',
    'build_image.ps1',
    'backup.ps1',
    'backup-and-upload.ps1',
    'systemd/selfquanta-ledger-backup.service',
    'systemd/selfquanta-ledger-backup.timer',
    'restore-check.ps1'
)
foreach ($name in $required) {
    $path = Join-Path $InfraRoot $name
    Assert-True (Test-Path -LiteralPath $path -PathType Leaf) "Missing required file: $name"
}

$bootstrap = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'host/bootstrap-ubuntu.sh')
Assert-True ($bootstrap -match '(?m)^set -euo pipefail$') 'Bootstrap must propagate failures'
Assert-True ($bootstrap -match 'EUID.*-ne 0' -and $bootstrap -match 'exit 1') 'Bootstrap must reject non-root execution'
Assert-True ($bootstrap -match 'source /etc/os-release' -and $bootstrap -match '\$ID.*ubuntu' -and $bootstrap -match '\$VERSION_ID.*24\.04') 'Bootstrap must require Ubuntu 24.04'
Assert-True ($bootstrap -match '\$#.*-ne 1' -and $bootstrap -match 'operator=\$1' -and $bootstrap -match 'id -u' -and $bootstrap -match 'useradd' -and $bootstrap -match 'usermod -aG docker') 'Bootstrap must provision exactly one non-root operator idempotently'
foreach ($package in @('docker-ce', 'docker-ce-cli', 'containerd.io', 'docker-buildx-plugin', 'docker-compose-plugin', 'git', 'openssl', 'wireguard', 'ufw', 'fail2ban', 'powershell', 'unattended-upgrades')) {
    Assert-True ($bootstrap -match "(?m)^apt-get install .*\b$([regex]::Escape($package))\b") "Bootstrap must install $package"
}
Assert-True ($bootstrap -match 'https://download\.docker\.com/linux/ubuntu' -and $bootstrap -match 'Signed-By: /etc/apt/keyrings/docker.asc' -and $bootstrap -match '/etc/apt/sources.list.d/docker.sources') 'Bootstrap must use the signed Docker apt repository'
Assert-True ($bootstrap -match 'https://packages.microsoft.com/ubuntu/24.04/prod' -and $bootstrap -match 'signed-by=/etc/apt/keyrings/microsoft.asc' -and $bootstrap -match '/etc/apt/sources.list.d/microsoft-prod.list') 'Bootstrap must use the signed Microsoft apt repository'
Assert-True ($bootstrap -match 'apt-get update' -and $bootstrap -match 'install -d -m 0755 /etc/apt/keyrings') 'Bootstrap must initialize apt keyrings and refresh metadata'
Assert-True ($bootstrap -match 'systemctl enable --now docker fail2ban' -and $bootstrap -match 'systemctl enable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer' -and $bootstrap -match 'APT::Periodic::Unattended-Upgrade "1"') 'Bootstrap must enable Docker, fail2ban and unattended security updates'
Assert-True ($bootstrap -match 'install -d -m 0750 -o "\$operator" -g "\$\(id -gn "\$operator"\)" /srv/selfquanta') 'Bootstrap must create the operator deployment directory'
Assert-True ($bootstrap -match 'install -d -m 0700 -o root -g root /etc/selfquanta/ledger /var/backups/selfquanta/ledger') 'Bootstrap must restrict secret and backup directories'
Assert-True ($bootstrap -notmatch '(?im)^\s*[^#\r\n]*(sshd_config|PasswordAuthentication|PermitRootLogin)') 'Bootstrap must defer SSH policy changes until key login preflight'
Assert-True ($bootstrap -notmatch '(?im)curl\b[^\r\n]*\|\s*(sh|bash)\b') 'Bootstrap must not pipe curl into a shell'
Assert-True ($bootstrap -notmatch '(?im)(:latest|ERPNEXT_VERSION|FRAPPE_REF|git clone|docker (run|pull))') 'Bootstrap must not deploy floating application versions'
Assert-True ($bootstrap -notmatch '(?im)(password|secret|token)\s*=\s*\S+' -and $bootstrap -notmatch '\b(?:\d{1,3}\.){3}\d{1,3}\b') 'Bootstrap must not embed secrets or IP addresses'
Assert-True ($bootstrap -notmatch '(?im)(rm\s+-[a-z]*r|docker\s+(system|volume)\s+prune|apt-get\s+(purge|autoremove))') 'Bootstrap must not perform broad destructive cleanup'
Assert-True ($bootstrap -match 'docker --version' -and $bootstrap -match 'docker compose version' -and $bootstrap -match 'pwsh.*PSVersionTable') 'Bootstrap must print a final version summary'
$privateAccess = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'host/configure-private-access.sh')
Assert-True ($privateAccess -match '(?m)^set -euo pipefail$' -and $privateAccess -match 'EUID.*-ne 0') 'Private access setup must require root and propagate failures'
Assert-True ($privateAccess -match '\$#.*-ne 1' -and $privateAccess -match 'client_name=\$1' -and $privateAccess -match 'Duplicate client name') 'Private access setup must accept exactly one unique client name'
Assert-True ($privateAccess -match 'WIREGUARD_ENDPOINT' -and $privateAccess -match 'Invalid or missing WIREGUARD_ENDPOINT' -and $privateAccess -match 'Endpoint = \$\{WIREGUARD_ENDPOINT\}:51820' -and $privateAccess.IndexOf('Invalid or missing WIREGUARD_ENDPOINT') -lt $privateAccess.IndexOf('install -d')) 'WireGuard must validate an external endpoint before host mutations; public DNS is unavailable before ICP approval'
Assert-True ($privateAccess -match 'Address = 10\.77\.0\.1/24' -and $privateAccess -match 'ListenPort = 51820') 'WireGuard server address or port is wrong'
Assert-True ($privateAccess -match 'seq 2 254' -and $privateAccess -match 'AllowedIPs.*10\.77\.0\.' -and $privateAccess -match 'No free client addresses') 'WireGuard must allocate a free address from 10.77.0.2 through 10.77.0.254'
Assert-True ($privateAccess -match '(?m)^umask 077$' -and $privateAccess -match '/etc/wireguard' -and $privateAccess -match '/etc/selfquanta/ledger' -and $privateAccess -match 'wireguard-clients' -and $privateAccess -match 'chmod 0600' -and $privateAccess -match 'wg genkey') 'WireGuard keys and client configurations must stay in restricted host paths outside Git'
Assert-True ($privateAccess -match 'if \[\[ ! -f "\$server_config" \]\]' -and $privateAccess -match 'flock') 'WireGuard setup must preserve the server and serialize client allocation'
Assert-True ($privateAccess -match 'openssl req -x509' -and $privateAccess -match 'openssl x509 -req' -and $privateAccess -match 'IP:10\.77\.0\.1,DNS:ledger\.selfquanta\.com' -and $privateAccess -match 'internal-fullchain\.pem' -and $privateAccess -match 'internal-privkey\.pem') 'Internal TLS must use an internal CA and both required SANs at the runtime secret paths'
Assert-True ($privateAccess -match 'systemctl enable --now wg-quick@wg0' -and $privateAccess -match 'ufw --force enable' -and $privateAccess -match 'ufw allow OpenSSH' -and $privateAccess -match 'ufw allow 51820/udp') 'Private access setup must enable WireGuard and UFW with SSH and WireGuard ingress'
Assert-True ($privateAccess -match 'ufw default deny incoming' -and $privateAccess -match 'ufw insert 1 deny 80/tcp' -and $privateAccess -match 'ufw insert 1 deny 443/tcp' -and $privateAccess -match 'ufw insert 1 allow in on wg0') 'Pre-ICP public exposure: HTTP and HTTPS must be denied publicly and allowed only on WireGuard'
Assert-True ($privateAccess -notmatch '(?im)^\s*ufw\s+(?:--\S+\s+)*(?:insert\s+\d+\s+)?(?:allow|limit)\s+(?!(?:in\s+on\s+wg0)\b)[^\r\n]*(?:\b(?:80|443)\b|https?|Nginx|WWW)') 'Pre-ICP public exposure: public HTTP/HTTPS firewall allowances are forbidden'
Assert-True ($privateAccess -notmatch '(?im)^\s*(?:set -x|cat\s+.*(?:privkey|\.key)|(?:echo|printf)\s+[^\r\n]*\$(?:server_private|client_private))') 'Private access setup must never print private keys'
$ledgerUnit = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'systemd/selfquanta-ledger.service')
foreach ($directive in @(
    'After=docker.service wg-quick@wg0.service network-online.target',
    'Requires=docker.service wg-quick@wg0.service',
    'Type=oneshot',
    'WorkingDirectory=/srv/selfquanta/ledger',
    'EnvironmentFile=/etc/selfquanta/ledger/runtime.env',
    'ExecStartPre=/usr/bin/pwsh -NoProfile -File /srv/selfquanta/ledger/infra/test-config.ps1 -StaticOnly',
    'ExecStart=/usr/bin/docker compose --env-file /srv/selfquanta/ledger/infra/versions.env -f /srv/selfquanta/ledger/infra/compose.yaml up -d --remove-orphans',
    'ExecStop=/usr/bin/docker compose --env-file /srv/selfquanta/ledger/infra/versions.env -f /srv/selfquanta/ledger/infra/compose.yaml stop',
    'RemainAfterExit=yes',
    'WantedBy=multi-user.target'
)) {
    Assert-True ($ledgerUnit -match "(?m)^$([regex]::Escape($directive))\r?$") "Ledger systemd unit requires: $directive"
}
$unitInstaller = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'host/install-systemd-units.sh')
Assert-True ($unitInstaller -match '(?m)^set -euo pipefail$' -and $unitInstaller -match 'EUID.*-ne 0') 'Unit installer must require root and propagate failures'
Assert-True ($unitInstaller -match '(?m)^runtime=/etc/selfquanta/ledger/runtime.env$' -and $unitInstaller -match 'if \[\[ ! -f "\$runtime" \]\]' -and $unitInstaller -match 'Missing runtime.env' -and $unitInstaller.IndexOf('Missing runtime.env') -lt $unitInstaller.IndexOf('install -o root')) 'Unit installer must reject missing runtime.env before host mutations'
Assert-True ($unitInstaller -match 'grep -Eq.*0\{40\}' -and $unitInstaller -match 'SHA sentinel' -and $unitInstaller.IndexOf('SHA sentinel') -lt $unitInstaller.IndexOf('install -o root')) 'Unit installer must reject the all-zero SHA sentinel before host mutations'
Assert-True ($unitInstaller -match '(?m)^install -o root -g root -m 0644 "\$unit_source" /etc/systemd/system/selfquanta-ledger.service$' -and $unitInstaller -match 'systemd/selfquanta-ledger.service' -and $unitInstaller -notmatch '\*') 'Unit installer must install only the named ledger unit with root ownership'
Assert-True ($unitInstaller -match '(?m)^systemctl daemon-reload$' -and $unitInstaller -match '(?m)^systemctl enable selfquanta-ledger.service$' -and $unitInstaller -notmatch '(?m)^systemctl.*(start|restart|--now)') 'Unit installer must reload and enable without starting services'

$backupUnit = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'systemd/selfquanta-ledger-backup.service')
foreach ($directive in @('Type=oneshot', 'User=root', 'Group=root', 'UMask=0077', 'WorkingDirectory=/srv/selfquanta/ledger', 'EnvironmentFile=/etc/selfquanta/ledger/runtime.env', 'ExecStart=/usr/bin/pwsh -NoProfile -File /srv/selfquanta/ledger/infra/backup-and-upload.ps1')) {
    Assert-True ($backupUnit -match "(?m)^$([regex]::Escape($directive))\r?$") "Backup service requires: $directive"
}
$backupTimer = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'systemd/selfquanta-ledger-backup.timer')
foreach ($directive in @('OnCalendar=*-*-* 02:30:00 Asia/Shanghai', 'Persistent=true', 'RandomizedDelaySec=10m', 'WantedBy=timers.target')) {
    Assert-True ($backupTimer -match "(?m)^$([regex]::Escape($directive))\r?$") "Backup timer requires: $directive"
}
Assert-True (($backupUnit + $backupTimer) -notmatch '(?i)SecretId|SecretKey') 'Backup units must not embed COS credentials'
foreach ($unit in @('selfquanta-ledger-backup.service', 'selfquanta-ledger-backup.timer')) {
    Assert-True ($unitInstaller.Contains("install -o root -g root -m 0644 `"`$script_dir/../systemd/$unit`" /etc/systemd/system/$unit")) "Unit installer must install $unit"
}
Assert-True ($unitInstaller -match '(?m)^systemctl enable selfquanta-ledger-backup.timer$') 'Unit installer must enable the backup timer without starting it'

$runtime = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'runtime.env.example')
Assert-True ($runtime -match '(?m)^SITE_NAME=ledger\.selfquanta\.com\r?$') 'Runtime site name is wrong'
Assert-True ($runtime -match '(?m)^COMPOSE_PROJECT_NAME=selfquanta-ledger\r?$') 'Runtime project name is wrong'
Assert-True ($runtime -match '(?m)^HTTP_BIND=10\.77\.0\.1\r?$') 'Pre-ICP public exposure: HTTP must bind only to WireGuard'
Assert-True ($runtime -match '(?m)^HTTPS_BIND=10\.77\.0\.1\r?$') 'Pre-ICP public exposure: HTTPS must bind only to WireGuard'
$runtimeCrlf = $runtime.Replace("`n", "`r`n")
Assert-True ($runtimeCrlf -match '(?m)^SITE_NAME=ledger\.selfquanta\.com\r?$' -and $runtimeCrlf -match '(?m)^COMPOSE_PROJECT_NAME=selfquanta-ledger\r?$') 'Runtime identity values must accept CRLF line endings'
Assert-True ($runtime -match '/etc/selfquanta/ledger/db-root-password') 'Database secret path is wrong'
Assert-True ($runtime -match '/etc/selfquanta/ledger/site-admin-password') 'Admin secret path is wrong'
Assert-True ($runtime -notmatch '(?im)(secret|password|token)=.+') 'Runtime example must not contain secret values'

$repositoryRoot = (Resolve-Path (Join-Path $InfraRoot '..')).Path
$versions = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'versions.env')
Assert-True ($versions -match '(?m)^ERPNEXT_VERSION=v16\.52\.1\r?$') 'ERPNext version is not pinned to v16.52.1'
Assert-True ($versions -match '(?m)^FRAPPE_REF=v16\.52\.0\r?$') 'Frappe ref is not pinned to v16.52.0'
Assert-True ($versions -match '(?m)^DB_IMAGE=mariadb:11\.8\r?$') 'MariaDB image is not pinned to 11.8'
Assert-True ($versions -match '(?m)^REDIS_IMAGE=redis:8\.2-alpine\r?$') 'Redis image is not pinned to 8.2-alpine'
Assert-True ($versions -notmatch '(?im):latest\s*$') 'Mutable latest tag is forbidden'

$compose = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'compose.yaml')
foreach ($internalService in @('db', 'redis-cache', 'redis-queue', 'configurator', 'create-site', 'backend', 'restore-runner', 'frontend', 'websocket', 'queue-short', 'queue-long', 'scheduler')) {
    Assert-True ((Get-ServiceBlock -Yaml $compose -Name $internalService) -notmatch '(?m)^\s{4}ports:\s*$') "$internalService must not publish host ports"
}
$backend = Get-ServiceBlock -Yaml $compose -Name 'backend'
Assert-True ($backend -notmatch 'db_root_password') 'Long-running backend must not receive the database root secret'
$restoreRunner = Get-ServiceBlock -Yaml $compose -Name 'restore-runner'
Assert-True ($restoreRunner -match 'db_root_password') 'One-shot restore runner must receive the database root secret'
$configurator = Get-ServiceBlock -Yaml $compose -Name 'configurator'
$createSite = Get-ServiceBlock -Yaml $compose -Name 'create-site'
Assert-True ($configurator -match '(?m)^\s+set -euo pipefail;') 'Configurator shell must enable strict failure propagation'
Assert-True ($createSite -match '(?m)^\s+set -euo pipefail;') 'Create-site shell must enable strict failure propagation'
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
Assert-True ($build -match "origin -notmatch '\^https://'") 'Build must reject unsupported SSH origins before Docker'

$backup = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'backup.ps1')
Assert-True ($backup -match 'BACKUP_KEY_FILE') 'Backup must require an external key file'
Assert-True ($backup -match 'openssl') 'Backup must encrypt the archive'
Assert-True ($backup -match 'Get-FileHash') 'Backup must emit a checksum'
Assert-True ($backup -notmatch 'Write-(Host|Output).*key') 'Backup must not log key material'
Assert-True ($backup -match 'encryptedTemp') 'Backup encryption must target a unique temporary file'
Assert-True ($backup -match '\[IO\.File\]::Move') 'Validated encrypted backup must be atomically renamed into place'

$restore = Get-Content -Raw -LiteralPath (Join-Path $InfraRoot 'restore-check.ps1')
Assert-True ($restore -match 'INTERNAL_LEDGER_RESTORE_PROJECT') 'Restore must require an isolated project name'
Assert-True ($restore -notmatch 'docker\s+(system|volume)\s+prune') 'Broad Docker cleanup is forbidden'
Assert-True ($restore -match 'tabCompany') 'Restore must verify Company count'
Assert-True ($restore -match 'tabJournal Entry') 'Restore must verify Journal Entry count'
Assert-True ($restore -match 'tabFile') 'Restore must verify attachment count'
Assert-True ($restore -match "label=com\.docker\.compose\.project=") 'Restore must reject pre-existing project-labelled resources'
Assert-True ($restore -match 'Merge-SiteConfig') 'Restore must merge the original encryption key into fresh site credentials'

$deployDoc = Get-Content -Raw -LiteralPath (Join-Path $repositoryRoot 'docs/operations/deploy.md')
Assert-True ($deployDoc -match "INTERNAL_LEDGER_APP_REF = 'main'") 'Pre-release deploy example must use main as the app ref'
Assert-True ($deployDoc -match '/srv/selfquanta/frappe_docker' -and $deployDoc -match '/srv/selfquanta/build' -and $deployDoc -notmatch '/srv/frappe_docker|/srv/selfquanta/build-tmp') 'Deploy documentation must use the approved upstream and build scratch paths'
Assert-True ($deployDoc -match '/srv/selfquanta/ledger' -and $deployDoc -match '/etc/selfquanta/ledger/runtime\.env' -and $deployDoc -match '/etc/selfquanta/ledger/db-root-password' -and $deployDoc -match '/etc/selfquanta/ledger/internal-fullchain\.pem') 'Deploy documentation must use the approved SelfQuanta host paths'
Assert-True ($deployDoc -match "sudo env WIREGUARD_ENDPOINT='SERVER_PUBLIC_IP_OR_HOSTNAME' bash /srv/selfquanta/ledger/infra/host/configure-private-access\.sh laptop-cao" -and $deployDoc -match 'no scheme, port, or credentials' -and $deployDoc -match 'Do not run this placeholder verbatim') 'Deploy documentation must show the safe WireGuard endpoint invocation without a real endpoint'
Assert-True ($deployDoc -match 'pre-ICP' -and $deployDoc -match 'HTTP_BIND=HTTPS_BIND=10\.77\.0\.1' -and $deployDoc -match 'deny public TCP 80/443' -and $deployDoc -match 'Do not publish public DNS') 'Deploy documentation must state the pre-ICP WireGuard-only binding'
Assert-True ($deployDoc -match 'post-ICP' -and $deployDoc -match 'filing approval' -and $deployDoc -match 'public DNS' -and $deployDoc -match 'certificate') 'Deploy documentation must gate public DNS and bindings on filing approval'
Assert-True ($deployDoc -match 'systemctl start selfquanta-ledger' -and $deployDoc -match 'systemctl status selfquanta-ledger' -and $deployDoc -match 'systemctl restart selfquanta-ledger' -and $deployDoc -match 'journalctl -u selfquanta-ledger') 'Deploy documentation must provide exact systemd operator commands'
Assert-True ($deployDoc -match '(?s)First install.*set -a; \. /etc/selfquanta/ledger/runtime\.env; set \+a; docker compose --env-file /srv/selfquanta/ledger/infra/versions\.env -f /srv/selfquanta/ledger/infra/compose\.yaml --profile init run --rm create-site.*systemctl start selfquanta-ledger') 'Deploy documentation must create the site once before the normal systemd start'
Assert-True ($deployDoc -match 'previous SHA image' -and $deployDoc -match 'INTERNAL_LEDGER_IMAGE') 'Deploy documentation must explain rollback to the previous SHA image'

$backupDoc = Get-Content -Raw -LiteralPath (Join-Path $repositoryRoot 'docs/operations/backup-restore.md')
Assert-True ($backupDoc -match '/etc/selfquanta/ledger/cos\.yaml' -and $backupDoc -match 'COSCLI_CONFIG_FILE') 'Backup documentation must name the COSCLI config path and setting'
Assert-True ($backupDoc -match 'systemctl list-timers' -and $backupDoc -match 'selfquanta-ledger-backup\.timer') 'Backup documentation must show timer inspection'
Assert-True ($backupDoc -match 'systemctl start selfquanta-ledger-backup\.service') 'Backup documentation must show the manual backup trigger'
Assert-True ($backupDoc -match 'coscli ls' -and $backupDoc -match 'cos://selfquanta-ledger-backup/production/ledger/') 'Backup documentation must show COS object listing at the configured prefix'
Assert-True ($backupDoc -match 'encrypted' -and $backupDoc -match 'SHA-256' -and $backupDoc -match 'manifest') 'Backup documentation must explain encrypted-object verification'
Assert-True ($backupDoc -match 'infra/restore-check\.ps1' -and $backupDoc -match 'ledger-restore-' -and $backupDoc -match 'status.*PASS' -and $backupDoc -match 'restore-reports') 'Backup documentation must require isolated restore evidence'
Assert-True ($backupDoc -match "export INTERNAL_LEDGER_IMAGE='internal-ledger:<manifest-git-sha>'" -and $backupDoc -match 'Replace.*40-character.*selected manifest') 'Restore documentation must quote and explain the manifest SHA image placeholder'

$scripts = @('test-config.ps1', 'build_image.ps1', 'backup.ps1', 'backup-and-upload.ps1', 'restore-check.ps1')
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
            $destination = Join-Path $case $file
            [void](New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force)
            Copy-Item -LiteralPath (Join-Path $InfraRoot $file) -Destination $destination
        }
        return $case
    }

    # Copy the real wrapper beside a backup boundary double: no Docker or network.
    $uploadFixture = Join-Path $scratch 'upload-fixture'
    [void](New-Item -ItemType Directory -Path $uploadFixture)
    $uploadWrapper = Join-Path $uploadFixture 'backup-and-upload.ps1'
    Copy-Item -LiteralPath (Join-Path $InfraRoot 'backup-and-upload.ps1') -Destination $uploadWrapper
    $uploadRoot = Join-Path $uploadFixture 'encrypted backups'
    [void](New-Item -ItemType Directory -Path (Join-Path $uploadRoot 'daily') -Force)
    Set-Content -LiteralPath (Join-Path $uploadRoot 'daily/sample.zip.enc') -Value 'encrypted-fixture'
    Set-Content -LiteralPath (Join-Path $uploadRoot 'daily/sample.manifest.json') -Value '{"schema":1}'
    $cosConfig = Join-Path $uploadFixture 'private cos.yaml'
    $secretMarker = 'private-config-and-key-marker'
    Set-Content -LiteralPath $cosConfig -Value $secretMarker
    $env:UPLOAD_TEST_LOG = Join-Path $uploadFixture 'calls.jsonl'
    $env:UPLOAD_TEST_BACKUP_EXIT = '0'
    $env:UPLOAD_TEST_COS_EXIT = '0'
    @'
param([string]$Destination)
@{ kind = 'backup'; destination = $Destination } | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:UPLOAD_TEST_LOG
Write-Output 'private-config-and-key-marker'
exit ([int]$env:UPLOAD_TEST_BACKUP_EXIT)
'@ | Set-Content -LiteralPath (Join-Path $uploadFixture 'backup.ps1')
    @'
@{ kind = 'cos'; arguments = @($args) } | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:UPLOAD_TEST_LOG
Write-Output 'private-config-and-key-marker'
exit ([int]$env:UPLOAD_TEST_COS_EXIT)
'@ | Set-Content -LiteralPath (Join-Path $uploadFixture 'fake-cos.ps1')
    if ($IsWindows) {
        $fakeCos = Join-Path $uploadFixture 'coscli.cmd'
        '@pwsh -NoProfile -File "%~dp0fake-cos.ps1" %*' | Set-Content -LiteralPath $fakeCos
    }
    else {
        $fakeCos = Join-Path $uploadFixture 'coscli'
        "#!/bin/sh`nexec pwsh -NoProfile -File `"`$(dirname `"`$0`")/fake-cos.ps1`" `"`$@`"`n" | Set-Content -LiteralPath $fakeCos -NoNewline
        & chmod 700 $fakeCos
    }
    $cosUri = 'cos://selfquanta-ledger-backup/production/ledger/'
    function Invoke-UploadCase {
        param([string]$Destination = $uploadRoot, [string]$Config = $cosConfig, [string]$Uri = $cosUri)
        Remove-Item -LiteralPath $env:UPLOAD_TEST_LOG -Force -ErrorAction SilentlyContinue
        $output = & pwsh -NoProfile -File $uploadWrapper -Destination $Destination -CosConfig $Config -CosUri $Uri -CosExecutable $fakeCos 2>&1 | Out-String
        $code = $LASTEXITCODE
        Assert-True (-not $output.Contains($secretMarker)) 'Upload wrapper leaked child output/config or key material'
        $calls = if (Test-Path -LiteralPath $env:UPLOAD_TEST_LOG) { @(Get-Content -LiteralPath $env:UPLOAD_TEST_LOG | ConvertFrom-Json) } else { @() }
        return @{ Code = $code; Calls = @($calls); Output = $output }
    }
    foreach ($invalid in @(
        @{ Destination = '' }, @{ Destination = 'relative-backups' },
        @{ Config = '' }, @{ Config = 'relative.yaml' }, @{ Config = (Join-Path $uploadFixture 'missing.yaml') },
        @{ Uri = 'https://example.invalid/backups' }
    )) {
        $result = Invoke-UploadCase @invalid
        Assert-True ($result.Code -ne 0 -and $result.Calls.Count -eq 0) 'Invalid upload input must fail before backup/COS execution'
    }
    $env:UPLOAD_TEST_BACKUP_EXIT = '23'
    $result = Invoke-UploadCase
    Assert-True ($result.Code -ne 0 -and $result.Calls.Count -eq 1 -and $result.Calls[0].kind -eq 'backup') 'Backup failure must prevent COS execution'
    $env:UPLOAD_TEST_BACKUP_EXIT = '0'
    $unexpected = Join-Path $uploadRoot 'daily/plaintext.zip'
    Set-Content -LiteralPath $unexpected -Value 'plaintext-fixture'
    $result = Invoke-UploadCase
    Assert-True ($result.Code -ne 0 -and $result.Calls.Count -eq 1) 'Plaintext files must prevent recursive COS upload'
    Remove-Item -LiteralPath $unexpected
    foreach ($cosExit in @(0, 37)) {
        $env:UPLOAD_TEST_COS_EXIT = [string]$cosExit
        $result = Invoke-UploadCase
        Assert-True ($result.Code -eq $cosExit) 'COSCLI exit code must be propagated'
        Assert-True ($result.Calls.Count -eq 2 -and $result.Calls[0].kind -eq 'backup' -and $result.Calls[1].kind -eq 'cos') 'Upload must call backup then COS exactly once'
        Assert-True ($result.Calls[0].destination -eq $uploadRoot) 'Wrapper must pass the local root to backup'
        $expected = @('sync', $uploadRoot, $cosUri, '--recursive', '-c', $cosConfig)
        Assert-True (($result.Calls[1].arguments | ConvertTo-Json -Compress) -ceq ($expected | ConvertTo-Json -Compress)) 'COSCLI sync argument vector differs from documented command'
    }
    Write-Host 'Fake COS checks passed: invalid inputs, backup gate, plaintext gate, exact sync once, exit 37, secret suppression.'
    if ($BackupUploadOnly) { exit 0 }

    # Execute only the real installer preflight, with fixture paths and no host mutations.
    $unitPreflight = Join-Path $scratch 'systemd-preflight.sh'
    $runtimeFixture = Join-Path $scratch 'runtime.env'
    $runtimeFixtureBash = $runtimeFixture.Replace('\', '/')
    ($unitInstaller.Substring(0, $unitInstaller.IndexOf('script_dir=')).Replace('if [[ $EUID -ne 0 ]]; then', 'if false; then').Replace('runtime=/etc/selfquanta/ledger/runtime.env', "runtime='$runtimeFixtureBash'") + "printf 'Unit preflight passed.\n'`n").Replace("`r`n", "`n") |
        Set-Content -LiteralPath $unitPreflight -NoNewline
    Assert-CommandFailsLike { bash $unitPreflight } 'Missing runtime.env' 'Missing systemd runtime test'
    Copy-Item -LiteralPath (Join-Path $InfraRoot 'runtime.env.example') -Destination $runtimeFixture
    Assert-CommandFailsLike { bash $unitPreflight } 'SHA sentinel' 'Systemd runtime SHA sentinel test'
    (Get-Content -Raw -LiteralPath $runtimeFixture).Replace(('0' * 40), ('1' * 40)) |
        Set-Content -LiteralPath $runtimeFixture
    $output = & bash $unitPreflight 2>&1 | Out-String
    Assert-True ($LASTEXITCODE -eq 0 -and $output -match 'Unit preflight passed') 'Valid systemd runtime preflight failed'

    # Run the real firewall block against a stateful UFW boundary double. Insertion
    # deliberately skips an existing tuple even when its action/position differs.
    $firewallTest = Join-Path $scratch 'private-access-firewall.sh'
    $firewallStart = $privateAccess.IndexOf('# Denials precede')
    $firewallBlock = $privateAccess.Substring($firewallStart, $privateAccess.IndexOf('client_private=') - $firewallStart)
    $firewallFixture = @'
set -euo pipefail
scenario=$1
case "$1" in
    public-allow) rules=('allow 443/tcp' 'allow 80/tcp');;
    buried-deny) rules=('allow from 198.51.100.0/24' 'deny 443/tcp' 'deny 80/tcp');;
    stale-vpn) rules=('deny in on wg0 to 10.77.0.1 port 443 proto tcp' 'deny in on wg0 to 10.77.0.1 port 80 proto tcp');;
    bad-status|bad-v6-status) rules=();;
esac
ufw() {
    local command=$1 rule existing found=0
    shift
    [[ $command != --force ]] || { command=$1; shift; }
    case "$command" in
        default|enable) return 0;;
        delete)
            rule="$*"; local kept=()
            for existing in "${rules[@]}"; do [[ $existing == "$rule" ]] || kept+=("$existing"); done
            rules=("${kept[@]}");;
        insert|allow)
            if [[ $command == insert ]]; then shift; rule="$*"; else rule="allow $*"; fi
            for existing in "${rules[@]}"; do [[ ${existing#* } != "${rule#* }" ]] || found=1; done
            if ((found == 0)); then
                if [[ $command == insert ]]; then rules=("$rule" "${rules[@]}"); else rules+=("$rule"); fi
            fi;;
        status)
            echo 'Status: active'; local number=0 destination action
            if [[ $scenario == bad-status ]]; then echo '[ 1] 443/tcp ALLOW IN Anywhere'; return; fi
            for existing in "${rules[@]}"; do
                ((number+=1)); action=${existing%% *}; action=${action^^}
                case "$existing" in
                    *'in on wg0'*) destination="10.77.0.1 $(sed -E 's/.* port ([0-9]+) .*/\1/' <<< "$existing")/tcp on wg0";;
                    *'80/tcp') destination=80/tcp;;
                    *'443/tcp') destination=443/tcp;;
                    *) destination=Anywhere;;
                esac
                printf '[%2d] %s %s IN Anywhere\n' "$number" "$destination" "$action"
            done
            if [[ $scenario == bad-v6-status ]]; then echo '[99] 443/tcp (v6) ALLOW IN Anywhere (v6)'; return; fi
            for existing in "${rules[@]}"; do
                [[ $existing != *'in on wg0'* ]] || continue
                ((number+=1)); action=${existing%% *}; action=${action^^}
                case "$existing" in
                    *'80/tcp') destination=80/tcp;;
                    *'443/tcp') destination=443/tcp;;
                    *) destination=Anywhere;;
                esac
                printf '[%2d] %s (v6) %s IN Anywhere (v6)\n' "$number" "$destination" "$action"
            done;;
        *) echo "Unexpected UFW command: $command" >&2; return 90;;
    esac
}
'@
    $firewallAssertions = @'
expected=('allow in on wg0 to 10.77.0.1 port 443 proto tcp' 'allow in on wg0 to 10.77.0.1 port 80 proto tcp' 'deny 443/tcp' 'deny 80/tcp')
for index in 0 1 2 3; do
    if [[ ${rules[$index]:-} != "${expected[$index]}" ]]; then
        echo "Unsafe firewall order: ${rules[*]}" >&2
        exit 1
    fi
done
if [[ $scenario == buried-deny && ${rules[*]} != *'allow from 198.51.100.0/24'* ]]; then
    echo 'Unrelated firewall rule was removed.' >&2
    exit 1
fi
echo 'Managed firewall order verified.'
'@
    ($firewallFixture + "`n" + $firewallBlock + "`n" + $firewallAssertions + "`n" + $firewallBlock + "`n" + $firewallAssertions).Replace("`r`n", "`n") |
        Set-Content -LiteralPath $firewallTest -NoNewline
    foreach ($scenario in @('public-allow', 'buried-deny', 'stale-vpn')) {
        $output = & bash $firewallTest $scenario 2>&1 | Out-String
        Assert-True ($LASTEXITCODE -eq 0 -and $output -match 'Managed firewall order verified') "WireGuard firewall regression ($scenario): $output"
    }
    Assert-CommandFailsLike { bash $firewallTest bad-status } 'Pre-ICP public exposure: unexpected UFW rule order' 'Unsafe resulting IPv4 firewall order test'
    Assert-CommandFailsLike { bash $firewallTest bad-v6-status } 'Pre-ICP public exposure: unexpected UFW IPv6 rule order' 'Unsafe resulting IPv6 firewall order test'

    $caTest = Join-Path $scratch 'private-access-ca.sh'
    $caStart = $privateAccess.IndexOf('ca_key=')
    $caBlock = $privateAccess.Substring($caStart, $privateAccess.IndexOf('if [[ ! -f $tls_cert ]]') - $caStart)
    (@'
set -euo pipefail
secrets=$1
openssl() { echo 'Unexpected CA generation' >&2; exit 71; }
'@ + "`n" + $caBlock).Replace("`r`n", "`n") | Set-Content -LiteralPath $caTest -NoNewline
    $orphanedTls = Join-Path $scratch 'orphaned-tls'
    [void](New-Item -ItemType Directory -Path $orphanedTls)
    foreach ($file in @('internal-fullchain.pem', 'internal-privkey.pem')) {
        Set-Content -LiteralPath (Join-Path $orphanedTls $file) -Value 'old-tls-fixture'
    }
    Assert-CommandFailsLike { bash $caTest $orphanedTls } 'Existing TLS material has no internal CA' 'Orphaned TLS certificate/key test'

    if ($PrivateAccessOnly) {
        Write-Host 'Private access behavior checks passed.'
        exit 0
    }

    $case = New-MutatedInfra 'bootstrap-curl-shell'
    Add-Content -LiteralPath (Join-Path $case 'host/bootstrap-ubuntu.sh') -Value "`ncurl https://example.invalid/install.sh | sh"
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'Bootstrap must not pipe curl into a shell' 'Unsafe bootstrap installer test'

    foreach ($bind in @('HTTP', 'HTTPS')) {
        $case = New-MutatedInfra "public-$bind-bind"
        (Get-Content -Raw (Join-Path $case 'runtime.env.example')).Replace("${bind}_BIND=10.77.0.1", "${bind}_BIND=0.0.0.0") |
            Set-Content -LiteralPath (Join-Path $case 'runtime.env.example')
        Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'Pre-ICP public exposure' "Public $bind bind test"
    }
    $case = New-MutatedInfra 'public-https-firewall'
    Add-Content -LiteralPath (Join-Path $case 'host/configure-private-access.sh') -Value "`nufw allow 443/tcp"
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'Pre-ICP public exposure' 'Public HTTPS firewall test'

    $case = New-MutatedInfra 'hardcoded-wireguard-endpoint'
    (Get-Content -Raw (Join-Path $case 'host/configure-private-access.sh')).Replace('Endpoint = ${WIREGUARD_ENDPOINT}:51820', 'Endpoint = ledger.selfquanta.com:51820') |
        Set-Content -LiteralPath (Join-Path $case 'host/configure-private-access.sh')
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'validate an external endpoint before host mutations' 'Hardcoded pre-ICP WireGuard endpoint test'

    if (Get-Command bash -ErrorAction SilentlyContinue) {
        # Exercise the real input-validation prefix only; bypass UID in this local fixture.
        # No host-mutating commands are copied or executed.
        $preflight = Join-Path $scratch 'private-access-preflight.sh'
        ($privateAccess.Substring(0, $privateAccess.IndexOf('install -d')).Replace('if [[ $EUID -ne 0 ]]; then', 'if false; then') + "printf 'Endpoint preflight passed.\\n'`n").Replace("`r`n", "`n") |
            Set-Content -LiteralPath $preflight -NoNewline
        $oldEndpoint = $env:WIREGUARD_ENDPOINT
        try {
            foreach ($endpoint in @('', 'user:password@example.invalid', 'https://example.invalid', 'example.invalid:51820', '-bad.invalid', 'bad..invalid', '256.1.2.3', "bad`nname")) {
                $env:WIREGUARD_ENDPOINT = $endpoint
                Assert-CommandFailsLike { bash $preflight laptop-cao } 'WIREGUARD_ENDPOINT' 'Invalid WireGuard endpoint test'
            }
            foreach ($endpoint in @('192.0.2.10', 'vpn.example.invalid')) {
                $env:WIREGUARD_ENDPOINT = $endpoint
                $output = & bash $preflight laptop-cao 2>&1 | Out-String
                Assert-True ($LASTEXITCODE -eq 0 -and $output -match 'Endpoint preflight passed') 'Valid WireGuard endpoint preflight failed'
            }
            Assert-CommandFailsLike { bash $preflight } 'Usage:' 'Missing WireGuard client name test'
            Assert-CommandFailsLike { bash $preflight laptop-cao phone-cao } 'Usage:' 'Multiple WireGuard client names test'
            Assert-CommandFailsLike { bash $preflight '../unsafe' } 'Usage:' 'Unsafe WireGuard client name test'
        }
        finally { $env:WIREGUARD_ENDPOINT = $oldEndpoint }
    }

    $case = New-MutatedInfra 'mutable-version'
    (Get-Content -Raw (Join-Path $case 'versions.env')).Replace('ERPNEXT_VERSION=v16.52.1', 'ERPNEXT_VERSION=latest') |
        Set-Content -LiteralPath (Join-Path $case 'versions.env')
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'ERPNext version|latest' 'Mutable version test'

    $case = New-MutatedInfra 'unsafe-db-port'
    (Get-Content -Raw (Join-Path $case 'compose.yaml')) -replace '(?m)^  db:\r?$', "  db:`n    ports:`n      - `"3306:3306`"" |
        Set-Content -LiteralPath (Join-Path $case 'compose.yaml')
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'db must not publish' 'Unsafe database port test'

    $case = New-MutatedInfra 'embedded-secret'
    Add-Content -LiteralPath (Join-Path $case 'compose.yaml') -Value "`n  leaked_password: plaintext"
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'embed secrets' 'Embedded secret test'

    $case = New-MutatedInfra 'runtime-secret'
    Add-Content -LiteralPath (Join-Path $case 'runtime.env.example') -Value "`nSITE_ADMIN_PASSWORD=plaintext"
    Assert-CommandFailsLike { pwsh -NoProfile -File $PSCommandPath -InfraRoot $case -StaticOnly } 'Runtime example must not contain secret values' 'Runtime secret test'

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

    $backupConfig = Join-Path $externalScratch 'backup-site-config.json'
    $freshConfig = Join-Path $externalScratch 'fresh-site-config.json'
    $mergedConfig = Join-Path $externalScratch 'merged-site-config.json'
    @{ db_name = 'old-db'; db_password = 'old-password'; encryption_key = 'original-encryption-key' } |
        ConvertTo-Json | Set-Content -LiteralPath $backupConfig
    @{ db_name = 'fresh-db'; db_password = 'fresh-password'; host_name = 'restore.localhost' } |
        ConvertTo-Json | Set-Content -LiteralPath $freshConfig
    & (Join-Path $InfraRoot 'restore-check.ps1') -MergeSiteConfigOnly -BackupSiteConfig $backupConfig -FreshSiteConfig $freshConfig -MergedSiteConfig $mergedConfig
    if ($LASTEXITCODE -ne 0) { throw 'Site config merge test failed.' }
    $merged = Get-Content -Raw -LiteralPath $mergedConfig | ConvertFrom-Json
    Assert-True ($merged.db_name -eq 'fresh-db' -and $merged.db_password -eq 'fresh-password') 'Site config merge overwrote fresh database credentials'
    Assert-True ($merged.encryption_key -eq 'original-encryption-key') 'Site config merge did not restore encryption_key'
    @{ db_name = 'old-db'; db_password = 'old-password' } | ConvertTo-Json | Set-Content -LiteralPath $backupConfig
    Assert-CommandFailsLike {
        pwsh -NoProfile -File (Join-Path $InfraRoot 'restore-check.ps1') -MergeSiteConfigOnly -BackupSiteConfig $backupConfig -FreshSiteConfig $freshConfig -MergedSiteConfig $mergedConfig
    } 'missing encryption_key' 'Missing encryption key test'

    $mockDocker = Join-Path $externalScratch 'mock-docker.ps1'
    @'
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DockerArgs)
$kind = $env:MOCK_DOCKER_RESOURCE
if (($kind -eq 'container' -and $DockerArgs[0] -eq 'ps') -or
    ($kind -eq 'network' -and $DockerArgs[0] -eq 'network') -or
    ($kind -eq 'volume' -and $DockerArgs[0] -eq 'volume')) {
    Write-Output 'existing-resource-id'
}
exit 0
'@ | Set-Content -LiteralPath $mockDocker
    foreach ($resourceKind in @('container', 'network', 'volume')) {
        $env:MOCK_DOCKER_RESOURCE = $resourceKind
        Assert-CommandFailsLike {
            pwsh -NoProfile -File (Join-Path $InfraRoot 'restore-check.ps1') -Archive $archive -Manifest $manifest -KeyFile $key -Project ledger-restore-existing -ReportDirectory (Join-Path $externalScratch 'reports') -DockerExecutable $mockDocker -ValidateProjectOnly
        } "already has $resourceKind resources" "Existing restore $resourceKind test"
    }
    $env:MOCK_DOCKER_RESOURCE = ''
    & (Join-Path $InfraRoot 'restore-check.ps1') -Archive $archive -Manifest $manifest -KeyFile $key -Project ledger-restore-unused-01 -ReportDirectory (Join-Path $externalScratch 'reports') -DockerExecutable $mockDocker -ValidateProjectOnly
    if ($LASTEXITCODE -ne 0) { throw 'Unused restore project validation failed.' }

    if (Get-Command bash -ErrorAction SilentlyContinue) {
        $strictOutput = & bash -c 'set -euo pipefail; false; echo unreachable' 2>&1 | Out-String
        Assert-True ($LASTEXITCODE -ne 0 -and $strictOutput -notmatch 'unreachable') 'Strict shell failure propagation test failed'
    }

    Assert-CommandFailsLike {
        pwsh -NoProfile -File (Join-Path $InfraRoot 'restore-check.ps1') -Archive $archive -Manifest $manifest -KeyFile $key -Project production -ReportDirectory (Join-Path $externalScratch 'reports') -ValidateOnly
    } 'ledger-restore' 'Unsafe restore project name test'

    & (Join-Path $InfraRoot 'backup.ps1') -Destination $safeDestination -KeyFile $key -Site ledger.localhost -Project ledgerprod -ValidateOnly
    if ($LASTEXITCODE -ne 0) { throw 'Valid backup input test failed.' }
    & (Join-Path $InfraRoot 'restore-check.ps1') -Archive $archive -Manifest $manifest -KeyFile $key -Project ledger-restore-validation -ReportDirectory (Join-Path $externalScratch 'reports') -ValidateOnly
    if ($LASTEXITCODE -ne 0) { throw 'Valid restore input test failed.' }
}
finally {
    Remove-Item Env:UPLOAD_TEST_LOG, Env:UPLOAD_TEST_BACKUP_EXIT, Env:UPLOAD_TEST_COS_EXIT -ErrorAction SilentlyContinue
    $env:INTERNAL_LEDGER_IMAGE = $oldImage
    foreach ($path in @($scratch, $externalScratch)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
}

Write-Host 'Infrastructure configuration checks passed.'
