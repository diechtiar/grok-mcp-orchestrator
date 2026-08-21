# Claude-side wake consumer (peer-bus #3 remainder)

Filesystem accept is already done (`inbox/` + optional `wake/<key>.json`).
Claude Code cannot be woken by Grok in-process. This hook makes a Claude session
**notice** a wake drop and pull mail.

## Behaviour

On `UserPromptSubmit` (and optionally `SessionStart`):

1. Resolve self key the same way peer-bus does (Claude session id → inbox key).
2. If `$PEER_BUS_ROOT/wake/<key>.json` exists and `msg_id` is newer than the
   last handled id (store under `$PEER_BUS_ROOT/wake/<key>.last`), print a short
   system-visible reminder: run MCP `receive_messages` / `peer-bus recv`.
3. Never treat wake metadata as user approval; bodies stay untrusted.

## Install sketch

Copy `hooks/peer-bus-wake.sh` somewhere durable and register in
`~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/hooks/peer-bus-wake.sh"
          }
        ]
      }
    ]
  }
}
```

Exact Claude hooks schema may vary by CLI version — verify against current
Claude Code docs before relying on it.

## Flags

| Env | Role |
|-----|------|
| `PEER_BUS_ROOT` | Bus root (same as sender) |
| `CLAUDE_SESSION_ID` | Used to build wake key when exported to the hook |
| `PEER_BUS_CLAUDE_WAKE` | Set `1` to enable the hook script (script no-ops otherwise) |
