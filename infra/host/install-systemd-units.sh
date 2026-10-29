#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo 'Run this script as root.' >&2
    exit 1
fi
runtime=/etc/selfquanta/ledger/runtime.env
if [[ ! -f "$runtime" ]]; then
    echo 'Missing runtime.env; configure /etc/selfquanta/ledger/runtime.env first.' >&2
    exit 1
fi
if grep -Eq ':0{40}($|[^0-9a-fA-F])' "$runtime"; then
    echo 'Replace the all-zero SHA sentinel in runtime.env before installing units.' >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
unit_source="$script_dir/../systemd/selfquanta-ledger.service"
install -o root -g root -m 0644 "$unit_source" /etc/systemd/system/selfquanta-ledger.service
install -o root -g root -m 0644 "$script_dir/../systemd/selfquanta-ledger-backup.service" /etc/systemd/system/selfquanta-ledger-backup.service
install -o root -g root -m 0644 "$script_dir/../systemd/selfquanta-ledger-backup.timer" /etc/systemd/system/selfquanta-ledger-backup.timer
systemctl daemon-reload
systemctl enable selfquanta-ledger.service
systemctl enable selfquanta-ledger-backup.timer
