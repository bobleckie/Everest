#!/usr/bin/env bash
# Outer proxy entrypoint: ensure a TLS cert exists, then exec nginx.
set -euo pipefail

/usr/local/bin/generate-cert.sh

exec "$@"
