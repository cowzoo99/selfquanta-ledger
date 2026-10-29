#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

if [[ $EUID -ne 0 ]]; then
    echo 'Run this script as root.' >&2
    exit 1
fi
if [[ $# -ne 1 || ! $1 =~ ^[a-z][a-z0-9_-]{0,31}$ ]]; then
    echo 'Usage: sudo bash infra/host/configure-private-access.sh <client-name>' >&2
    exit 1
fi
client_name=$1
WIREGUARD_ENDPOINT=${WIREGUARD_ENDPOINT:-}
hostname_pattern='^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$'
if [[ ${#WIREGUARD_ENDPOINT} -gt 253 || ! $WIREGUARD_ENDPOINT =~ $hostname_pattern ]]; then
    echo 'Invalid or missing WIREGUARD_ENDPOINT; supply a reachable IPv4 address or hostname without a port.' >&2
    exit 1
fi
if [[ $WIREGUARD_ENDPOINT =~ ^[0-9.]+$ ]]; then
    IFS=. read -r -a octets <<< "$WIREGUARD_ENDPOINT"
    if [[ ${#octets[@]} -ne 4 ]]; then
        echo 'WIREGUARD_ENDPOINT must be a valid IPv4 address.' >&2
        exit 1
    fi
    for octet in "${octets[@]}"; do
        if [[ ${#octet} -gt 3 ]] || ((10#$octet > 255)); then
            echo 'WIREGUARD_ENDPOINT must be a valid IPv4 address.' >&2
            exit 1
        fi
    done
fi
secrets=/etc/selfquanta/ledger
clients=$secrets/wireguard-clients
server_config=/etc/wireguard/wg0.conf
client_config=$clients/$client_name.conf
install -d -m 0700 -o root -g root /etc/wireguard "$secrets" "$clients"
exec 9>/etc/wireguard/.private-access.lock
flock -n 9
if [[ -e $client_config ]] || { [[ -f $server_config ]] && grep -Fxq "# Client: $client_name" "$server_config"; }; then
    echo 'Duplicate client name; choose a new name.' >&2
    exit 1
fi

if [[ ! -f "$server_config" ]]; then
    server_private=$(wg genkey)
    cat > "$server_config" <<EOF
[Interface]
Address = 10.77.0.1/24
ListenPort = 51820
PrivateKey = $server_private
EOF
    unset server_private
fi
chmod 0600 "$server_config"
client_ip=
for octet in $(seq 2 254); do
    if ! grep -Eq "^[[:space:]]*AllowedIPs[[:space:]]*=[[:space:]]*10\.77\.0\.$octet/32([[:space:]]|$)" "$server_config"; then
        client_ip=10.77.0.$octet
        break
    fi
done
if [[ -z $client_ip ]]; then
    echo 'No free client addresses in 10.77.0.2 through 10.77.0.254.' >&2
    exit 1
fi

ca_key=$secrets/internal-ca.key
ca_cert=$secrets/internal-ca.pem
tls_key=$secrets/internal-privkey.pem
tls_cert=$secrets/internal-fullchain.pem
if [[ ! -f $ca_key && ! -f $ca_cert ]]; then
    openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -sha256 \
        -subj '/CN=SelfQuanta Ledger Internal CA' \
        -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' \
        -keyout "$ca_key" -out "$ca_cert" >/dev/null 2>&1
fi
if [[ ! -f $ca_key || ! -f $ca_cert ]]; then
    echo 'Incomplete internal CA; restore its matching key and certificate before retrying.' >&2
    exit 1
fi
if [[ ! -f $tls_cert ]]; then
    if [[ ! -f $tls_key ]]; then
        openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$tls_key" >/dev/null 2>&1
    fi
    openssl req -new -key "$tls_key" -subj '/CN=ledger.selfquanta.com' \
        -out "$secrets/internal-server.csr" >/dev/null 2>&1
    openssl x509 -req -in "$secrets/internal-server.csr" -CA "$ca_cert" -CAkey "$ca_key" \
        -CAcreateserial -days 397 -sha256 -out "$secrets/internal-server.pem" \
        -extfile <(printf '%s\n' 'basicConstraints=critical,CA:FALSE' 'keyUsage=critical,digitalSignature,keyEncipherment' 'extendedKeyUsage=serverAuth' 'subjectAltName=IP:10.77.0.1,DNS:ledger.selfquanta.com') >/dev/null 2>&1
    cat "$secrets/internal-server.pem" "$ca_cert" > "$tls_cert"
fi
if [[ ! -f $tls_key ]]; then
    echo 'Missing TLS private key; restore the matching key before retrying.' >&2
    exit 1
fi
chmod 0600 "$ca_key" "$ca_cert" "$tls_key" "$tls_cert"

# Denials precede any existing broad allowances; only wg0 gets web ingress.
ufw default deny incoming >/dev/null
ufw insert 1 deny 80/tcp >/dev/null
ufw insert 1 deny 443/tcp >/dev/null
ufw insert 1 allow in on wg0 to 10.77.0.1 port 80 proto tcp >/dev/null
ufw insert 1 allow in on wg0 to 10.77.0.1 port 443 proto tcp >/dev/null
ufw allow OpenSSH >/dev/null
ufw allow 51820/udp >/dev/null
ufw --force enable >/dev/null

client_private=$(wg genkey)
client_public=$(printf '%s' "$client_private" | wg pubkey)
server_public=$(sed -n 's/^PrivateKey = //p' "$server_config" | wg pubkey)
cat > "$client_config" <<EOF
[Interface]
PrivateKey = $client_private
Address = $client_ip/32

[Peer]
PublicKey = $server_public
Endpoint = ${WIREGUARD_ENDPOINT}:51820
AllowedIPs = 10.77.0.1/32
PersistentKeepalive = 25
EOF
unset client_private
chmod 0600 "$client_config"
cat >> "$server_config" <<EOF

# Client: $client_name
[Peer]
PublicKey = $client_public
AllowedIPs = $client_ip/32
EOF
systemctl enable --now wg-quick@wg0 >/dev/null 2>&1
# Apply newly added peers even when the interface was already running.
wg syncconf wg0 <(wg-quick strip wg0)
printf '%s\n' "$client_config" "$ca_cert"
