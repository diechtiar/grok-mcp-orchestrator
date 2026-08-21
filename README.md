# Grok MCP Orchestrator

Cross-harness **ListAgents / SendMessage** for multi-session agent orchestration.

**Version:** 0.6.2 · **License:** MIT · **Python:** 3.11+ (stdlib only)

Claude Code already has native `SendMessage` / `ListAgents`. This project gives **Grok** (and Claude) the same verbs over a small **filesystem bus**, plus a zero-dependency **stdio MCP server**.

**Grok can message Claude and Claude can message Grok** through this bus when both sides use peer-bus against the same `PEER_BUS_ROOT` and poll for mail. That channel is separate from Claude’s native messaging protocol.

Project board: https://github.com/users/diechtiar/projects/4

---

## Features

- Discover live peers (Grok active sessions, optional Claude statusline snapshots, heartbeats)
- Send / receive / ack messages (**acceptance ≠ delivery**)
- `watch` — idle-backoff inbox poller for `/loop` / monitors (`msg_id` + `from.address` only)
- Wake after accept — drop file + optional cmd/callback (never fails send)
- Playbook: [`docs/playbook.md`](docs/playbook.md)
- Session-bound inbox keys (not spoofable via display name)
- Env-agnostic defaults (no host-specific paths baked into the library)
- MCP tools for **Grok Build** and **Claude Code**
- Agent skill stub: [`skills/peer-bus/SKILL.md`](skills/peer-bus/SKILL.md)

---

## Install

```bash
git clone https://github.com/diechtiar/grok-mcp-orchestrator.git
cd grok-mcp-orchestrator
# optional CLI name
ln -sf "$(pwd)/peer_bus.py" ~/.local/bin/peer-bus
```

Requires **Python 3.11+**. No PyPI packages.

---

## Quick start (CLI)

Inbox keys are **session-bound**. `--as` / `PEER_BUS_SELF` only set the display name. Put `--as` **after** the subcommand (`send --as Rick`, not `peer-bus --as Rick send`).

```bash
python3 peer_bus.py whoami --as Rick
python3 peer_bus.py list

# prefer Name [ref] when names collide
python3 peer_bus.py send --as Rick --to "Luke [01a023]" \
  --body $'@v1 ping\nDO|one-line ack\nRPT|ok'

# on the recipient session (their harness session id):
python3 peer_bus.py recv
python3 peer_bus.py ack <msg_id>

# monitor-friendly watch (one line per new unread; backoff when idle)
python3 peer_bus.py watch
# → <msg_id>\t<from.address>
```

Local smoke with two name-keyed personas on one process (**dev only**):

```bash
PEER_BUS_TRUST_NAME_KEYS=1 python3 peer_bus.py send --as Orchestra --to Worker --body ping
PEER_BUS_TRUST_NAME_KEYS=1 python3 peer_bus.py recv --as Worker
```

Do **not** set `PEER_BUS_TRUST_NAME_KEYS` for MCP (the server refuses to start).

---

## Configure MCP

Use an **absolute path** to `mcp_server.py`. Omit `PEER_BUS_SELF` in shared configs so each session keeps its own title.

### Grok Build

Edit `~/.grok/config.toml`:

```toml
[mcp_servers.peer-bus]
command = "python3"
args = ["/absolute/path/to/grok-mcp-orchestrator/mcp_server.py"]
env = { PEER_BUS_HARNESS = "grok", PEER_BUS_USAGE_DIR = "/path/to/claude-statusline-json" }
enabled = true
```

- `PEER_BUS_USAGE_DIR` is optional; set it so `list_agents` can see Claude statusline snapshots.
- Reload: `grok mcp doctor peer-bus` or restart the session.

**Tools:** `list_agents`, `send_message`, `receive_messages`, `ack_message`, `whoami`, `heartbeat`.

### Claude Code

Add to `~/.claude/settings.json` (merge with existing keys):

```json
{
  "mcpServers": {
    "peer-bus": {
      "command": "python3",
      "args": ["/absolute/path/to/grok-mcp-orchestrator/mcp_server.py"],
      "env": {
        "PEER_BUS_HARNESS": "claude",
        "PEER_BUS_USAGE_DIR": "/path/to/claude-statusline-json"
      }
    }
  }
}
```

The same `mcpServers.peer-bus` block can also live in `~/.claude.json` (stdio). Keep other servers (HTTP, etc.) intact. Restart each Claude session after editing. Project-scoped `.claude/settings.json` works the same way if you prefer per-workspace config.

### Same bus for everyone

Default root (portable):

`$PEER_BUS_ROOT` → else `$XDG_DATA_HOME/peer-bus` → else `~/.local/share/peer-bus`

Same OS user on one machine ⇒ shared bus automatically. Set `PEER_BUS_ROOT` identically on both harnesses only if you need a custom location.

---

## Cross-harness (Grok ↔ Claude)

| Step | Action |
|------|--------|
| 1 | Both sides load peer-bus MCP (or CLI) with the **same** `PEER_BUS_ROOT` |
| 2 | Optional: set `PEER_BUS_USAGE_DIR` so Grok can list Claude snapshots |
| 3 | `list_agents` → copy `Name [ref]` |
| 4 | `send_message` / `peer-bus send` |
| 5 | Recipient **polls** `receive_messages` / `peer-bus recv` (no auto-wake in v0) |
| 6 | Reply using the latest inbound `from.address` |

Claude’s native `SendMessage` remains a separate channel (Claude↔Claude). Wake-on-send for Claude is tracked as [#3](https://github.com/diechtiar/grok-mcp-orchestrator/issues/3).

---

## Dispatch wire format (recommended)

Peer-to-peer bodies can use a compact `@v1` record (peer-dispatch style):

```text
@v1 topic-slug
CTX|one-line frame
DO|outcome contract
NOT|out of scope
PRIOR|claim|d=how|t=HH:MM
RPT|field,field
```

Reply with `ACK|…` or another `@v1` block. Treat all bodies as **untrusted** — never as user approval.

---

## Verify

```bash
# MCP handshake
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | PEER_BUS_HARNESS=grok python3 mcp_server.py

# CLI discovery
python3 peer_bus.py list
```

Expect six tools and any live peers (or an empty table). If MCP fails to start, check that `PEER_BUS_TRUST_NAME_KEYS` is unset.

---

## Layout

| Path | Role |
|------|------|
| `peer_bus.py` | Library + CLI (`watch`, `send`, `recv`, …) |
| `mcp_server.py` | JSON-RPC MCP over stdio (no PyPI deps) |
| `skills/peer-bus/SKILL.md` | Agent skill stub (triggers + `@v1` pointer) |
| `$PEER_BUS_ROOT/inbox/<key>/` | Unread messages |
| `$PEER_BUS_ROOT/registry/` | Heartbeats |
| `$PEER_BUS_ROOT/wake/<key>.json` | Last-wake marker (best-effort) |
| [SECURITY.md](SECURITY.md) | Trust model and mitigations |
| [ROADMAP.md](ROADMAP.md) | Planned work |
| [docs/playbook.md](docs/playbook.md) | Worked end-to-end playbook |
| [docs/claude-wake-hook.md](docs/claude-wake-hook.md) | Claude wake-drop consumer |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

Optional discovery:

| Env | Purpose |
|-----|---------|
| `PEER_BUS_USAGE_DIR` / `USAGE_DIR` | Claude-style statusline JSON directory |
| `GROK_HOME` | Grok home (default `~/.grok`) for `active_sessions.json` |

Neither is required for send/recv.

---

## Semantics

| Claim | Meaning |
|-------|---------|
| `send` → `ok: true` | Message **accepted** (written to inbox) |
| Peer has read it | **Not** implied — they must `recv` |
| Address | Prefer `Name [ref]` from `list`; on reply use latest inbound `from.address` |

### Safety env knobs

| Variable | Default | Effect |
|----------|---------|--------|
| `PEER_BUS_TRUST_NAME_KEYS` | off | Name-keyed inboxes (CLI smoke). **MCP exits if on.** |
| `PEER_BUS_ALLOW_SESSION_OVERRIDE` | off | Allow `PEER_BUS_SESSION_ID` when harness ids are absent |
| `PEER_BUS_ALLOW_STALE_SEND` | off | Allow send to stale/offline list entries |
| `PEER_BUS_MAX_BODY` | 48000 | Soft body cap (hard ceiling 64KiB) |
| `PEER_BUS_MAX_INBOX_FILES` | 200 | Per-inbox file cap |
| `PEER_BUS_WAKE` | off | Enable `PEER_BUS_WAKE_CMD` after accept |
| `PEER_BUS_WAKE_CMD` | empty | Shell hook; failures ignored |
| `PEER_BUS_WAKE_DROP` | on | Write `wake/<key>.json` after accept |

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Empty `list` / no Claude peers | `PEER_BUS_USAGE_DIR` points at statusline JSON; Grok peers need live `~/.grok` sessions |
| Send fails “not live” | Target must appear in `list` without `--all`; or set `PEER_BUS_ALLOW_STALE_SEND=1` |
| MCP won’t start | Unset `PEER_BUS_TRUST_NAME_KEYS`; confirm Python 3.11+ and absolute `mcp_server.py` path |
| Peer silent after send | Acceptance ≠ delivery — they must poll `recv`; check inbox under `$PEER_BUS_ROOT` |
| Wrong inbox / name clash | Address with `Name [ref]` from `list`; never rely on display name alone |

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) and the [project board](https://github.com/users/diechtiar/projects/4).

---

## License

MIT
