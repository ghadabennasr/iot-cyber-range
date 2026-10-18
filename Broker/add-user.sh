#!/usr/bin/env bash
# Usage: ./Broker/add-user.sh <username> [password]
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${1:?usage: add-user.sh <username> [password]}"
PASS="${2:-$(openssl rand -hex 12)}"
FLAGS="-b"; [ -f "$DIR/passwordfile" ] || FLAGS="-c -b"
docker run --rm -v "$DIR:/work" eclipse-mosquitto:2 \
  mosquitto_passwd $FLAGS /work/passwordfile "$USER_NAME" "$PASS"
echo "$USER_NAME : $PASS"
