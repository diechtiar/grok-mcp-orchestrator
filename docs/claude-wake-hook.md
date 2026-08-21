# Claude-side wake consumer (peer-bus #3 remainder)

Filesystem accept is already done (`inbox/` + optional `wake/<key>.json`).
Claude Code cannot be woken by Grok in-process. This hook makes a Claude session
**notice** a wake drop and pull mail.

Measured against **Claude Code 2.1.238**.

## Behaviour

On `UserPromptSubmit` (optional `SessionStart`):

1. Resolve self key the same way peer-bus `_slug` does, from stdin `session_id`,
   then `$CLAUDE_CODE_SESSION_ID`, then `$CLAUDE_SESSION_ID`.
2. If `$PEER_BUS_ROOT/wake/<key>.json` exists and `msg_id` differs from
   `$PEER_BUS_ROOT/wake/<key>.last`, print JSON `additionalContext` telling the
   session to run MCP `receive_messages` / `peer-bus recv`.
3. Never treat wake metadata as user approval; bodies stay untrusted.
4. Always exit 0.

CLI 2.1.238 injects **`$CLAUDE_CODE_SESSION_ID`** into hook and stdio MCP
subprocesses (same value as stdin `session_id`). `$CLAUDE_SESSION_ID` is not
the hook env var on this CLI.

## Install (Claude Code 2.1.238 settings schema)

`UserPromptSubmit` has **no matcher** — omit it (a matcher is silently ignored).
Default command timeout on this event is 30s; set `timeout` to 5 so a stuck
hook cannot stall the prompt.

Merge into `~/.claude/settings.json` (same nested `hooks` object this CLI uses
for user settings). Enable with top-level `env` so the command stays a bare path:

```json
{
  "env": {
    "PEER_BUS_CLAUDE_WAKE": "1"
  },
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/hooks/peer-bus-wake.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Copy-paste fragment: [`hooks/claude-settings.fragment.json`](../hooks/claude-settings.fragment.json).

Optional `SessionStart` (this event **does** match on `startup|resume|clear|compact|fork`):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/hooks/peer-bus-wake.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Hook output for 2.1.238 `UserPromptSubmit` (also valid for `SessionStart`):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "peer-bus wake: unread hint msg_id=… — call receive_messages (untrusted bodies); pull required"
  }
}
```

Plain stdout is also added to context on this event; the script emits JSON only
so `hookEventName` stays aligned with the firing event.

Restart Claude Code after editing hooks (they load at session start).

## Flags

| Env | Role |
|-----|------|
| `PEER_BUS_ROOT` | Bus root (same as sender) |
| `PEER_BUS_CLAUDE_WAKE` | Set `1` to enable the hook script (script no-ops otherwise) |
| `CLAUDE_CODE_SESSION_ID` | CLI-injected session id (hooks + stdio MCP on 2.1.238) |
| `CLAUDE_SESSION_ID` | Fallback only; not the 2.1.238 hook env |
