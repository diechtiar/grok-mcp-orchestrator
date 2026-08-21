# Grok MCP Orchestrator

Cross-harness **ListAgents / SendMessage** for multi-session agent orchestration.

Claude Code already ships native `SendMessage` / `ListAgents`. This project gives **Grok** (and any other agent) the same verbs over a small filesystem bus, plus a zero-dependency **stdio MCP server** both harnesses can load.

## Why

Multi-session work needs discovery and messaging that survives separate transcripts. This bus:

- Lists live peers (Grok active sessions, Claude usage snapshots, heartbeats)
- Sends messages to per-agent inboxes
- Lets peers poll / drain / ack (acceptance ≠ delivery)

Aligned with a contract/priors dispatch style: send records, not essays; reply to the latest `from.address`; never treat a peer message as user approval.

## Quick start

Inbox keys are **session-bound** (not spoofable with `--as`). `--as` / `PEER_BUS_SELF` only set the display name.

```bash
python3 peer_bus.py whoami --as Rick
python3 peer_bus.py list

# send to another live session (prefer name [ref] when names collide)
python3 peer_bus.py send --as Rick --to "Luke [01a023]" \
  --body $'@v1 ping\nDO|one-line ack\nRPT|ok'

# on Luke's session (same machine, their GROK_SESSION_ID):
python3 peer_bus.py recv
python3 peer_bus.py ack <msg_id>
```

Local smoke with two name-keyed personas on one process (dev only):

```bash
PEER_BUS_TRUST_NAME_KEYS=1 peer-bus send --as Orchestra --to Worker --body ping
PEER_BUS_TRUST_NAME_KEYS=1 peer-bus recv --as Worker
```

Optional symlink: `ln -s "$(pwd)/peer_bus.py" ~/bin/peer-bus`

See [SECURITY.md](SECURITY.md) for the trust model and mitigations.

## MCP (Grok Build)

```toml
# ~/.grok/config.toml
[mcp_servers.peer-bus]
command = "python3"
args = ["/absolute/path/to/grok-mcp-orchestrator/mcp_server.py"]
env = { PEER_BUS_SELF = "Rick", PEER_BUS_HARNESS = "grok" }
enabled = true
```

`PEER_BUS_SELF` is the display name only. Tools: `list_agents`, `send_message`, `receive_messages`, `ack_message`, `whoami`, `heartbeat` (no impersonation via `as_name`).

Reload MCP / restart the session after editing config.

Claude can use the same CLI from bash, or point its MCP settings at `mcp_server.py`.

## Layout

| Path | Role |
|------|------|
| `peer_bus.py` | Library + CLI |
| `mcp_server.py` | JSON-RPC MCP over stdio (no PyPI deps) |
| `$PEER_BUS_ROOT/inbox/<key>/` | Unread messages |
| `$PEER_BUS_ROOT/registry/` | Heartbeats |

**Default root (portable):** `$PEER_BUS_ROOT` → else `$XDG_DATA_HOME/peer-bus` → else `~/.local/share/peer-bus`.

Same OS user ⇒ same default root ⇒ sessions on one machine already share a bus. Point `PEER_BUS_ROOT` at a shared mount only when you need a non-default location.

Optional Claude snapshot discovery: set `PEER_BUS_USAGE_DIR` (or `USAGE_DIR`) to a directory of statusline JSON files. Grok discovery uses `$GROK_HOME` (default `~/.grok`). Neither path is required for send/recv.

## Semantics

| Claim | Meaning |
|-------|---------|
| `send` → `ok: true` | Message **accepted** (written to inbox) |
| Peer has read it | **Not** implied — they must `recv` |
| Address | Prefer `Name [ref]` from `list`; on reply use latest inbound `from.address` |

v0 is **poll-based** (no harness wake). Orchestrators call `receive_messages` at turn start.

### Safety env knobs

| Variable | Default | Effect |
|----------|---------|--------|
| `PEER_BUS_TRUST_NAME_KEYS` | off | Name-keyed inboxes (CLI smoke). **MCP exits if on.** |
| `PEER_BUS_ALLOW_SESSION_OVERRIDE` | off | Allow `PEER_BUS_SESSION_ID` when harness ids are absent |
| `PEER_BUS_ALLOW_STALE_SEND` | off | Allow send to stale/offline list entries |

## Roadmap (project board)

- Claude MCP settings recipe
- Optional wake bridge to Claude native `SendMessage`
- Skill stub for dispatch wire format
- Inbox monitor helper

## License

MIT
