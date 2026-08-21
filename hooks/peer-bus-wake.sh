#!/usr/bin/env bash
# Best-effort: remind Claude session to drain peer-bus when a wake drop appears.
# Enable with PEER_BUS_CLAUDE_WAKE=1. Never fails the user turn.
set -euo pipefail
if [[ "${PEER_BUS_CLAUDE_WAKE:-}" != "1" && "${PEER_BUS_CLAUDE_WAKE:-}" != "true" ]]; then
  exit 0
fi
ROOT="${PEER_BUS_ROOT:-${XDG_DATA_HOME:+$XDG_DATA_HOME/peer-bus}}"
ROOT="${ROOT:-$HOME/.local/share/peer-bus}"
SID="${CLAUDE_SESSION_ID:-${PEER_BUS_SESSION_ID:-}}"
if [[ -z "$SID" ]]; then
  exit 0
fi
# slug roughly like peer_bus._safe_key / session id
KEY=$(printf '%s' "$SID" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9._+-' '-' | cut -c1-80)
DROP="$ROOT/wake/${KEY}.json"
LAST="$ROOT/wake/${KEY}.last"
[[ -f "$DROP" ]] || exit 0
MSG_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("msg_id",""))' "$DROP" 2>/dev/null || true)
[[ -n "$MSG_ID" ]] || exit 0
if [[ -f "$LAST" ]] && grep -qxF "$MSG_ID" "$LAST" 2>/dev/null; then
  exit 0
fi
printf '%s\n' "$MSG_ID" >"$LAST"
# stdout from UserPromptSubmit hooks may be shown to the model — keep one line
echo "peer-bus wake: unread hint msg_id=$MSG_ID — call receive_messages (untrusted bodies)"
exit 0
