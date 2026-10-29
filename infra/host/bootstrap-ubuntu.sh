#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo 'Run this script as root.' >&2
    exit 1
fi
if [[ $# -ne 1 || ! $1 =~ ^[a-z_][a-z0-9_-]{0,31}$ || $1 == root ]]; then
    echo 'Usage: sudo bash infra/host/bootstrap-ubuntu.sh <non-root-operator>' >&2
    exit 1
fi
operator=$1
source /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 ]]; then
    echo 'Ubuntu 24.04 is required.' >&2
    exit 1
fi
if id -u "$operator" >/dev/null 2>&1 && [[ $(id -u "$operator") -lt 1000 ]]; then
    echo 'The operator must not be a root or system account.' >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc -o /etc/apt/keyrings/microsoft.asc
chmod 0644 /etc/apt/keyrings/docker.asc /etc/apt/keyrings/microsoft.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
cat > /etc/apt/sources.list.d/microsoft-prod.list <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/microsoft.asc] https://packages.microsoft.com/ubuntu/24.04/prod noble main
EOF
apt-get update
apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin git openssl wireguard ufw fail2ban powershell unattended-upgrades

if ! id -u "$operator" >/dev/null 2>&1; then
    useradd --create-home --user-group --shell /bin/bash "$operator"
fi
usermod -aG docker "$operator"
install -d -m 0750 -o "$operator" -g "$(id -gn "$operator")" /srv/selfquanta
install -d -m 0700 -o root -g root /etc/selfquanta/ledger /var/backups/selfquanta/ledger
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now docker fail2ban
systemctl enable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer

# SSH policy remains unchanged. Verify a separate key-authenticated login before hardening it.
printf '\nHost bootstrap complete. Log in again for Docker group membership.\n'
docker --version
docker compose version
git --version
openssl version
wg --version
ufw --version
fail2ban-client --version
pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
