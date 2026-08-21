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

```bash
# identity
python3 peer_bus.py whoami --as Orchestra

# discover
python3 peer_bus.py list

# send (prefer name [ref] when names collide)
python3 peer_bus.py send --as Orchestra --to "Worker [abc123]" \
  --body $'@v1 ping\nDO|one-line ack\nRPT|ok'

# peer side
python3 peer_bus.py recv --as Worker
python3 peer_bus.py ack <msg_id> --as Worker
```

Optional symlink: `ln -s "$(pwd)/peer_bus.py" ~/bin/peer-bus`

## MCP (Grok Build)

```toml
# ~/.grok/config.toml
[mcp_servers.peer-bus]
command = "python3"
args = ["/absolute/path/to/grok-mcp-orchestrator/mcp_server.py"]
env = { PEER_BUS_SELF = "Rick", PEER_BUS_HARNESS = "grok" }
enabled = true
```

Tools: `list_agents`, `send_message`, `receive_messages`, `ack_message`, `whoami`, `heartbeat`.

Reload MCP / restart the session after editing config.

Claude can use the same CLI from bash, or point its MCP settings at `mcp_server.py`.

## Layout

| Path | Role |
|------|------|
| `peer_bus.py` | Library + CLI |
| `mcp_server.py` | JSON-RPC MCP over stdio (no PyPI deps) |
| `$PEER_BUS_ROOT/inbox/<key>/` | Unread messages (default root: `/workspace/_shared/peer-bus` in vida-dev) |
| `$PEER_BUS_ROOT/registry/` | Heartbeats |

Override root with `PEER_BUS_ROOT`.

## Semantics

| Claim | Meaning |
|-------|---------|
| `send` → `ok: true` | Message **accepted** (written to inbox) |
| Peer has read it | **Not** implied — they must `recv` |
| Address | Prefer `Name [ref]` from `list`; on reply use latest inbound `from.address` |

v0 is **poll-based** (no harness wake). Orchestrators call `receive_messages` at turn start.

## Roadmap (project board)

- Claude MCP settings recipe
- Optional wake bridge to Claude native `SendMessage`
- Skill stub for dispatch wire format
- Inbox monitor helper

## License

MIT
