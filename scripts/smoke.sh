#!/usr/bin/env bash
# peer-bus smoke — exit nonzero on first failure
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PB=(python3 "$ROOT/peer_bus.py")
export PEER_BUS_ROOT="${PEER_BUS_ROOT:-$(mktemp -d /tmp/peer-bus-smoke.XXXXXX)}"
export PEER_BUS_TRUST_NAME_KEYS=1
export PEER_BUS_WAKE_DROP=1
cleanup() { rm -rf "$PEER_BUS_ROOT"; }
trap cleanup EXIT

"${PB[@]}" whoami --as Orchestra >/dev/null
"${PB[@]}" list >/dev/null
"${PB[@]}" send --as Orchestra --to Worker --body 'smoke-ping' --summary smoke >/dev/null
out="$("${PB[@]}" recv --as Worker --json)"
echo "$out" | python3 -c 'import json,sys; m=json.load(sys.stdin); assert m and m[0]["msg_id"]'
mid=$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["msg_id"])')
"${PB[@]}" ack --as Worker "$mid" >/dev/null
"${PB[@]}" watch --as Worker --once >/dev/null
# wake drop from a fresh send
"${PB[@]}" send --as Orchestra --to Worker --body 'smoke-wake' >/dev/null
key=$("${PB[@]}" whoami --as Worker | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')
test -f "$PEER_BUS_ROOT/wake/${key}.json"
echo "smoke ok ROOT=$PEER_BUS_ROOT"
