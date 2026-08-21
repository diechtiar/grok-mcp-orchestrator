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

# MCP stdio: initialize + tools/list (must refuse TRUST_NAME_KEYS)
unset PEER_BUS_TRUST_NAME_KEYS
mcp_out=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | PEER_BUS_ROOT="$PEER_BUS_ROOT" PEER_BUS_HARNESS=grok python3 "$ROOT/mcp_server.py" 2>/dev/null | tail -1)
echo "$mcp_out" | python3 -c 'import json,sys; r=json.load(sys.stdin); assert "result" in r and any(t["name"]=="list_agents" for t in r["result"]["tools"])'
echo "mcp ok"
