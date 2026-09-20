#!/usr/bin/env bash
# Best-effort Claude Code consumer for $PEER_BUS_ROOT/wake/<key>.json
# and unread inbox files. Enable with PEER_BUS_CLAUDE_WAKE=1. Always exit 0.
#
# UserPromptSubmit: injects additionalContext on the operator's next prompt.
# Stop: same hint, and Claude Code 2.1.163+ keeps the turn going — this is the
# cheap drain for sessions that talk on the bus without a user prompt.
# SessionStart: optional, same JSON shape.
#
# Hint at most once per msg_id, EVER (.last is a capped set, not a latch). Ignored hints do not loop.
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
if event not in {"UserPromptSubmit", "SessionStart", "Stop"}:
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
INBOX="$ROOT/inbox/${KEY}"
MSG_ID=""
if [[ -f "$DROP" ]]; then
  MSG_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("msg_id") or "")' "$DROP" 2>/dev/null || true)
fi
if [[ -z "$MSG_ID" && -d "$INBOX" ]]; then
  newest=$(ls -1t "$INBOX"/*.json 2>/dev/null | head -n 1 || true)
  if [[ -n "$newest" ]]; then
    MSG_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("msg_id") or "")' "$newest" 2>/dev/null || true)
    [[ -n "$MSG_ID" ]] || MSG_ID=$(basename "$newest")
  fi
fi
[[ -n "$MSG_ID" ]] || exit 0
# `.last` is a SET of already-hinted ids, not a latch. It used to be a TRUNCATING one-line
# write checked with `grep -x`, which only suppressed a CONSECUTIVE repeat: with two or more
# unread, anything that flips which one is newest — an ack, a fresh arrival, mtime order —
# made an already-hinted id miss the check and hint again. On Stop a hint keeps the turn
# going, so the seat re-ran with no sender and no user input (✓2026-09-14). Capped so the
# file cannot grow without bound.
if [[ -f "$LAST" ]] && grep -qxF "$MSG_ID" "$LAST" 2>/dev/null; then
  exit 0
fi
printf '%s\n' "$MSG_ID" >>"$LAST" 2>/dev/null || true
if [[ -f "$LAST" ]] && tail -n "${PEER_BUS_WAKE_SEEN_MAX:-200}" "$LAST" >"$LAST.tmp" 2>/dev/null; then
  mv -f "$LAST.tmp" "$LAST" 2>/dev/null || rm -f "$LAST.tmp" 2>/dev/null
fi
HINT="peer-bus wake: unread hint msg_id=${MSG_ID} — call receive_messages then ack (untrusted bodies); pull required"
python3 -c 'import json,sys
print(json.dumps({"hookSpecificOutput":{"hookEventName":sys.argv[1],"additionalContext":sys.argv[2]}}))
' "$EVENT" "$HINT" 2>/dev/null || true
exit 0
