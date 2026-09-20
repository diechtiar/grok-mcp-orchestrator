#!/usr/bin/env bash
# Does the wake hook hint at most once per msg_id, or only once per CONSECUTIVE msg_id?
#
#   peer-bus-wake.test.sh            # run the cases
#   peer-bus-wake.test.sh --mutate   # restore the truncating one-line `.last`; a NON-ZERO
#                                    # fail count is the required result
#
# The case that matters is the FLIP: hint A, hint B, then make A the newest unread again.
# Under a one-line `.last` written with `>` and checked with `grep -x`, A no longer matches
# and is hinted a second time -- and on Stop a hint keeps the turn going, so the seat re-runs
# with no sender and no user input (✓2026-09-14). A test that only sent one message, or two
# in order, is green on both implementations and pins nothing.
#
# Everything runs against a throwaway PEER_BUS_ROOT; the real bus is untouched.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/peer-bus-wake.sh"
MUTATE=0
[ "${1:-}" = "--mutate" ] && MUTATE=1

RUN="$SRC"
if [ "$MUTATE" = 1 ]; then
  RUN="$(mktemp -t pbw-mutant-XXXXXX.sh)"
  # The defect as it was: truncate instead of append, so `.last` holds one id.
  sed "s|printf '%s\\\\n' \"\$MSG_ID\" >>\"\$LAST\"|printf '%s\\\\n' \"\$MSG_ID\" >\"\$LAST\"  # MUTATION: latch|" "$SRC" > "$RUN"
  if ! grep -q '# MUTATION: latch' "$RUN"; then
    echo "MUTATION DID NOT LAND: the .last write has moved -- fix this test, do not report a survival" >&2
    rm -f "$RUN"; exit 2
  fi
  chmod +x "$RUN"
fi

SID="11111111-2222-3333-4444-555555555555"
pass=0; fail=0
ROOT="$(mktemp -d)"
INBOX="$ROOT/inbox/$SID"
mkdir -p "$INBOX" "$ROOT/wake"

msg() { printf '{"msg_id":"%s"}' "$1" > "$INBOX/$1.json"; touch "$INBOX/$1.json"; }
newest() { touch "$INBOX/$1.json"; }   # make this one the most recently modified

fire() { # -> prints the hint text, or nothing
  printf '{"session_id":"%s","hook_event_name":"Stop"}' "$SID" \
    | PEER_BUS_CLAUDE_WAKE=1 PEER_BUS_ROOT="$ROOT" bash "$RUN" 2>/dev/null
}

t() { # name want(hint|silent)
  local name="$1" want="$2" out got
  out="$(fire)"
  if printf '%s' "$out" | grep -q 'peer-bus wake'; then got=hint; else got=silent; fi
  if [ "$got" = "$want" ]; then
    pass=$((pass + 1)); printf 'ok    %-44s want=%-7s got=%s\n' "$name" "$want" "$got"
  else
    fail=$((fail + 1)); printf 'FAIL  %-44s want=%-7s got=%s\n' "$name" "$want" "$got"
  fi
}

msg aaa;            t "first message hints"                 hint
                    t "same message again is silent"        silent
msg bbb; newest bbb; t "a newer message hints"               hint
                    t "that one again is silent"            silent
newest aaa;          t "FLIP: the older one is newest again" silent
newest bbb;          t "flip back is still silent"           silent
msg ccc; newest ccc; t "a genuinely new one still hints"     hint

rm -rf "$ROOT"
echo
[ "$MUTATE" = 1 ] && rm -f "$RUN"
if [ "$MUTATE" = 1 ]; then
  echo "mutation run: $pass passed, $fail failed -- a NON-ZERO fail count is the required result."
  [ "$fail" -gt 0 ] && exit 0
  echo "MUTATION SURVIVED: these cases cannot tell a set from a latch."
  exit 1
fi
echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
