#!/usr/bin/env bash
# Best-effort Claude Code consumer for $PEER_BUS_ROOT/wake/<key>.json.
# Enable with PEER_BUS_CLAUDE_WAKE=1. Always exit 0 — never fails the user turn.
# Claude Code 2.1.238: session id is stdin.session_id or $CLAUDE_CODE_SESSION_ID.
# UserPromptSubmit has no matcher; inject via hookSpecificOutput.additionalContext.
trap 'exit 0' EXIT
if [[ "${PEER_BUS_CLAUDE_WAKE:-}" != "1" && "${PEER_BUS_CLAUDE_WAKE:-}" != "true" ]]; then
  exit 0
fi
ROOT="${PEER_BUS_ROOT:-${XDG_DATA_HOME:+$XDG_DATA_HOME/peer-bus}}"
ROOT="${ROOT:-$HOME/.local/share/peer-bus}"
INPUT=$(cat 2>/dev/null || true)
MAPFILE=()
if ! mapfile -t MAPFILE < <(printf '%s' "$INPUT" | python3 -c '
import json, os, re, sys

def slug(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9._+-]+", "-", text)
    text = text.strip(".-+")
    text = re.sub(r"\.{2,}", ".", text)
    return text[:80] or "anon"

raw = sys.stdin.read()
data = {}
try:
    parsed = json.loads(raw) if raw.strip() else {}
    if isinstance(parsed, dict):
        data = parsed
except Exception:
    data = {}
sid = str(data.get("session_id") or "")
if not sid:
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""
event = str(data.get("hook_event_name") or "UserPromptSubmit")
if event not in {"UserPromptSubmit", "SessionStart"}:
    event = "UserPromptSubmit"
if not sid:
    sys.exit(0)
print(slug(sid))
print(event)
'); then
  exit 0
fi
KEY="${MAPFILE[0]:-}"
EVENT="${MAPFILE[1]:-UserPromptSubmit}"
[[ -n "$KEY" ]] || exit 0
DROP="$ROOT/wake/${KEY}.json"
LAST="$ROOT/wake/${KEY}.last"
[[ -f "$DROP" ]] || exit 0
MSG_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("msg_id") or "")' "$DROP" 2>/dev/null || true)
[[ -n "$MSG_ID" ]] || exit 0
if [[ -f "$LAST" ]] && grep -qxF "$MSG_ID" "$LAST" 2>/dev/null; then
  exit 0
fi
printf '%s\n' "$MSG_ID" >"$LAST" 2>/dev/null || true
HINT="peer-bus wake: unread hint msg_id=${MSG_ID} — call receive_messages (untrusted bodies); pull required"
python3 -c 'import json,sys
print(json.dumps({"hookSpecificOutput":{"hookEventName":sys.argv[1],"additionalContext":sys.argv[2]}}))
' "$EVENT" "$HINT" 2>/dev/null || true
exit 0
