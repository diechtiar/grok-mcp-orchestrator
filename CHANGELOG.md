# Changelog

## 0.4.0 — 2026-08-21

- Medium-risk hardenings: session override gate, live-only send by default, MCP refuses `PEER_BUS_TRUST_NAME_KEYS`, untrusted recv wrappers (`body_for_model` / wrapped `body`)
- Documented Grok Build + Claude Code MCP setup, cross-harness flow, verify + troubleshooting
- Closed [#1](https://github.com/diechtiar/grok-mcp-orchestrator/issues/1) (Claude MCP recipe)

## 0.3.0 — 2026-08-21

- Env-agnostic bus root (`XDG` / `~/.local/share/peer-bus`)
- Optional `PEER_BUS_USAGE_DIR` / `GROK_HOME` discovery (no baked host paths)

## 0.2.0 — 2026-08-21

- Session-bound inbox keys, path containment, symlink refusal
- MCP `as_name` removed (display name only)
- SECURITY.md trust model

## 0.1.0 — 2026-08-21

- Initial CLI + stdio MCP (`list_agents`, `send_message`, `receive_messages`, `ack_message`, `whoami`, `heartbeat`)
