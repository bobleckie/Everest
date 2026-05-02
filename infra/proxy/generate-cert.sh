#!/usr/bin/env bash
# Generates a self-signed cert for the local-prod stack on first boot.
# Idempotent: never overwrites an existing cert. Subject CN/SAN list
# is overridable via TLS_HOSTS env var (comma-separated).
#
# WARNING: self-signed certs are for LOCAL USE ONLY. When you migrate
# to a real host, replace /etc/nginx/tls/server.{crt,key} with files
# from your CA — this script will leave them alone if they exist.

set -euo pipefail

CERT_DIR="${CERT_DIR:-/etc/nginx/tls}"
mkdir -p "$CERT_DIR"

if [[ -f "$CERT_DIR/server.crt" && -f "$CERT_DIR/server.key" ]]; then
    echo "[tls] cert already exists at $CERT_DIR — leaving it alone."
    exit 0
fi

HOSTS="${TLS_HOSTS:-localhost,127.0.0.1,everest.local}"
echo "[tls] generating self-signed cert for: $HOSTS"

# Build the SAN entry list.
SAN_LINES=""
IFS=',' read -ra HOST_ARR <<< "$HOSTS"
i=1
for h in "${HOST_ARR[@]}"; do
    h_trimmed="$(echo "$h" | xargs)"
    if [[ "$h_trimmed" =~ ^[0-9.]+$ ]]; then
        SAN_LINES+="IP.$i = $h_trimmed"$'\n'
    else
        SAN_LINES+="DNS.$i = $h_trimmed"$'\n'
    fi
    i=$((i+1))
done

CONF="$(mktemp)"
cat > "$CONF" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions    = v3_req
prompt             = no

[req_distinguished_name]
C  = US
ST = Local
L  = Local
O  = Everest Local Dev
CN = localhost

[v3_req]
keyUsage         = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName   = @alt_names

[alt_names]
$SAN_LINES
EOF

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$CERT_DIR/server.key" \
    -out    "$CERT_DIR/server.crt" \
    -config "$CONF"

rm -f "$CONF"
chmod 600 "$CERT_DIR/server.key"
chmod 644 "$CERT_DIR/server.crt"

echo "[tls] cert written to $CERT_DIR/"
