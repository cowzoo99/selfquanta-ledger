#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

fail() { printf '%s\n' "$1" >&2; exit 1; }
if [[ $EUID -ne 0 ]]; then
    fail 'Run this script as root.'
fi
[[ $# -eq 1 ]] || fail 'Usage: configure-public-access.sh <DNS hostname>|--private'
hostname=$1
private=0
[[ $hostname != --private ]] || private=1
if ((private == 0)); then
    hostname_pattern='^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$'
    [[ ${#hostname} -le 253 && $hostname =~ $hostname_pattern ]] || fail 'Supply a DNS hostname, never an IP address or URL.'
fi
secrets=/etc/selfquanta/ledger
ufw_dir=/etc/ufw
ufw_defaults=/etc/default/ufw
runtime=$secrets/runtime.env
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -r $runtime && -d $ufw_dir && -f $ufw_defaults ]] || fail 'Missing runtime/UFW configuration.'
exec 9>"$secrets/.access-mode.lock"
flock -n 9 || fail 'Another access-mode change is running.'
# runtime.env is a root-owned trusted shell environment, as in the systemd service.
set -a
source "$runtime"
set +a
dc() { docker compose --env-file "$script_dir/../versions.env" -f "$script_dir/../compose.yaml" "$@"; }
systemctl is-active --quiet wg-quick@wg0 || fail 'WireGuard management must remain active.'
if ((private == 0)); then
    [[ $(sed -n 's/^ICP_APPROVED=//p' "$runtime") == 1 ]] || fail 'Set literal ICP_APPROVED=1 in runtime.env after operator approval.'
    [[ ${PRIVILEGED_2FA_CONFIRMED:-0} == 1 && -s ${PUBLIC_ACCESS_EVIDENCE_FILE:-} && -r ${PUBLIC_ACCESS_EVIDENCE_FILE:-} ]] || fail 'Require operator evidence and PRIVILEGED_2FA_CONFIRMED=1.'
    [[ ${PUBLIC_ACCESS_EVIDENCE_FILE:-} == "$secrets/"* ]] || fail 'Keep operator evidence inside the protected secrets directory.'
    [[ -r ${TLS_CERTIFICATE_FILE:-} && -s ${TLS_CERTIFICATE_FILE:-} && -r ${TLS_PRIVATE_KEY_FILE:-} && -s ${TLS_PRIVATE_KEY_FILE:-} ]] || fail 'Missing readable certificate/key files.'
    # Tencent metadata supplies this host address; an operator-supplied IP cannot override it.
    server_ipv4=$(curl --noproxy '*' --fail --silent --show-error --max-time 5 http://metadata.tencentyun.com/latest/meta-data/public-ipv4)
    python3 -c 'import ipaddress,sys; sys.exit(0 if ipaddress.IPv4Address(sys.argv[1]).is_global else 1)' "$server_ipv4" || fail 'Invalid server public IPv4 from metadata.'
    mapfile -t dns_addresses < <(getent ahostsv4 "$hostname" | awk '{print $1}' | sort -u)
    [[ ${#dns_addresses[@]} -eq 1 && ${dns_addresses[0]} == "$server_ipv4" ]] || fail 'DNS A records must resolve exclusively to this server public IPv4.'
    openssl x509 -in "$TLS_CERTIFICATE_FILE" -noout -ext subjectAltName 2>/dev/null | grep -q 'DNS:' || fail 'Public certificate requires a DNS SAN.'
    openssl x509 -in "$TLS_CERTIFICATE_FILE" -noout -checkhost "$hostname" 2>/dev/null | grep -Fq "Hostname $hostname does match certificate" || fail 'Public certificate does not cover the hostname.'
    openssl x509 -in "$TLS_CERTIFICATE_FILE" -noout -checkend 86400 >/dev/null 2>&1 || fail 'Public certificate expires within one day.'
    openssl verify -purpose sslserver -verify_hostname "$hostname" -untrusted "$TLS_CERTIFICATE_FILE" "$TLS_CERTIFICATE_FILE" >/dev/null 2>&1 || fail 'Public certificate is not trusted by the host CA store.'
    cert_public=$(openssl x509 -in "$TLS_CERTIFICATE_FILE" -pubkey -noout | openssl pkey -pubin -outform DER | openssl dgst -sha256)
    key_public=$(openssl pkey -in "$TLS_PRIVATE_KEY_FILE" -passin pass: -pubout -outform DER 2>/dev/null | openssl dgst -sha256) || fail 'Cannot read TLS private key.'
    [[ $cert_public == "$key_public" ]] || fail 'TLS certificate/key do not match.'
    # Read-only checks use the pinned Frappe v16 mechanisms. Never read OTP secrets:
    # get_otpsecret_for_ creates one when absent and is not an enrollment proof.
    if ! dc exec -T backend python - "$SITE_NAME" <<'PY'
import math
import sys
import frappe

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

frappe.init(site=sys.argv[1])
frappe.connect()
try:
    require(frappe.db.get_single_value("Website Settings", "disable_signup"), "registration must be disabled")
    for field in ("allow_consecutive_login_attempts", "allow_login_after_fail", "enable_two_factor_auth"):
        require(float(frappe.db.get_single_value("System Settings", field) or 0) > 0, field)
    for field in ("bypass_2fa_for_retricted_ip_users", "bypass_restrict_ip_check_if_2fa_enabled"):
        require(not frappe.db.get_single_value("System Settings", field), "global authentication bypass must be disabled")
    rate = frappe.conf.get("rate_limit") or {}
    for field in ("limit", "window"):
        value = rate.get(field)
        require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0, "rate_limit")
    privileged = {"Administrator"}
    privileged.update(frappe.get_all("Has Role", filters={"parenttype": "User", "role": ["in", ["System Manager", "账套管理员"]]}, pluck="parent"))
    for user in privileged:
        if user == "Administrator" or frappe.db.get_value("User", user, "enabled"):
            require(not frappe.db.get_value("User", user, "bypass_restrict_ip_check_if_2fa_enabled"), "privileged IP bypass must be disabled")
            roles = [row.role for row in frappe.get_doc("User", user).roles] + ["All"]
            require(frappe.get_all("Role", filters={"name": ["in", roles], "two_factor_auth": 1}, pluck="name"), "privileged role must require 2FA")
finally:
    frappe.destroy()
PY
    then
        fail 'Frappe public security preflight failed; keep access private.'
    fi
else
    TLS_CERTIFICATE_FILE=$secrets/internal-fullchain.pem
    TLS_PRIVATE_KEY_FILE=$secrets/internal-privkey.pem
    [[ -r $TLS_CERTIFICATE_FILE && -r $TLS_PRIVATE_KEY_FILE ]] || fail 'Missing internal certificate/key for private rollback.'
fi

# Keep a restricted snapshot for operator review/retry. Restore on any failed step.
backup=$(mktemp -d "$secrets/.access-mode-backup.XXXXXX")
cp -p "$runtime" "$backup/runtime.env"
cp -a "$ufw_dir" "$backup/ufw"
cp -p "$ufw_defaults" "$backup/ufw-defaults"
committed=0
gateway_touched=0
recover() {
    status=$?
    trap - EXIT
    if ((committed == 0)); then
        set +e
        rollback_ok=1
        cp -p "$backup/runtime.env" "$runtime" || rollback_ok=0
        cp -a "$backup/ufw/." "$ufw_dir/" || rollback_ok=0
        cp -p "$backup/ufw-defaults" "$ufw_defaults" || rollback_ok=0
        if grep -q '^ENABLED=yes$' "$ufw_dir/ufw.conf"; then
            ufw reload >/dev/null || rollback_ok=0
        else
            ufw --force disable >/dev/null || rollback_ok=0
        fi
        set -a; source "$runtime"; set +a
        if ((gateway_touched && rollback_ok)); then
            dc up -d --no-deps --force-recreate gateway >/dev/null 2>&1 || rollback_ok=0
        fi
        if ((rollback_ok == 0)); then
            dc stop gateway >/dev/null 2>&1
            printf '%s\n' 'ROLLBACK FAILED: gateway stop requested; restore the retained snapshot and verify management access manually.' >&2
        else
            printf '%s\n' 'Access change failed; restored runtime/UFW snapshot. Inspect the gateway and retained backup before retrying.' >&2
        fi
    fi
    exit "$status"
}
trap recover EXIT
export PUBLIC_ACCESS_ENABLED=$((1 - private))
export PUBLIC_HOSTNAME=$hostname
export HTTP_BIND=10.77.0.1 HTTPS_BIND=0.0.0.0 HTTP_PORT=80 HTTPS_PORT=443
if ((private)); then
    export PUBLIC_HOSTNAME= HTTPS_BIND=10.77.0.1 ICP_APPROVED=0
fi
export TLS_CERTIFICATE_FILE TLS_PRIVATE_KEY_FILE
next=$backup/next.env
awk '!/^(PUBLIC_ACCESS_ENABLED|PUBLIC_HOSTNAME|ICP_APPROVED|HTTP_BIND|HTTPS_BIND|HTTP_PORT|HTTPS_PORT|TLS_CERTIFICATE_FILE|TLS_PRIVATE_KEY_FILE)=/' "$runtime" > "$next"
for name in PUBLIC_ACCESS_ENABLED PUBLIC_HOSTNAME ICP_APPROVED HTTP_BIND HTTPS_BIND HTTP_PORT HTTPS_PORT TLS_CERTIFICATE_FILE TLS_PRIVATE_KEY_FILE; do
    printf '%s=%s\n' "$name" "${!name}" >> "$next"
done
install -m 0600 "$next" "$runtime"
pwsh -NoProfile -File "$script_dir/../test-config.ps1" -StaticOnly
dc config --quiet
# Preserve every SSH/WireGuard rule. Replace only our managed web tuples.
delete_web_rule() {
    local output line
    if output=$(LC_ALL=C ufw --force delete "$@" 2>&1); then return; fi
    [[ -n $output ]] || fail 'UFW rule deletion failed without a diagnostic.'
    while IFS= read -r line; do
        [[ $line =~ ^Could\ not\ delete\ non-existent\ rule(\ \(v6\))?$ ]] || fail 'UFW rule deletion failed; restoring access snapshot.'
    done <<< "$output"
}
for action in allow deny reject limit; do
    delete_web_rule "$action" 80/tcp
    delete_web_rule "$action" 443/tcp
    delete_web_rule "$action" in on wg0 to 10.77.0.1 port 80 proto tcp
    delete_web_rule "$action" in on wg0 to 10.77.0.1 port 443 proto tcp
done
ufw insert 1 deny 80/tcp >/dev/null
if ((private)); then ufw insert 1 deny 443/tcp >/dev/null; else ufw insert 1 allow 443/tcp >/dev/null; fi
ufw insert 1 allow in on wg0 to 10.77.0.1 port 80 proto tcp >/dev/null
ufw insert 1 allow in on wg0 to 10.77.0.1 port 443 proto tcp >/dev/null
ufw reload >/dev/null
gateway_touched=1
dc up -d --no-deps --force-recreate gateway >/dev/null
dc exec -T gateway nginx -t >/dev/null 2>&1
committed=1
printf 'Access mode configured. Restricted snapshot: %s\n' "$backup"
