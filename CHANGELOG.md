# Changelog

## 0.6.5 — 2026-08-21

- `peer-bus version` CLI
- stdlib `tests/test_peer_bus.py` (safe key, Claude session id, send/recv/ack/wake, symlink refuse)
- CI runs unittest before smoke
- Tests: MCP TRUST_NAME_KEYS refusal; body size cap

## 0.6.4 — 2026-08-21

- Accept `CLAUDE_CODE_SESSION_ID` as Claude harness session id (MCP/hooks on Code 2.1.x)

## 0.6.3 — 2026-08-21

- Claude wake hook tightened for Claude Code 2.1.238: stdin `session_id` / `$CLAUDE_CODE_SESSION_ID`, JSON `additionalContext`, settings fragment with `timeout` and no `UserPromptSubmit` matcher

## 0.6.2 — 2026-08-21

- Claude wake-drop consumer sketch: `hooks/peer-bus-wake.sh` + `docs/claude-wake-hook.md`
- Skill wording: wake is best-effort hint; pull still required

## 0.6.1 — 2026-08-21

- GitHub Actions smoke workflow
- MCP initialize/tools.list covered in `scripts/smoke.sh`

## 0.6.0 — 2026-08-21

- Wake after accept ([#3](https://github.com/diechtiar/grok-mcp-orchestrator/issues/3)): default `wake/<key>.json` drop; optional `PEER_BUS_WAKE=1` + `PEER_BUS_WAKE_CMD`; `set_wake_callback()` for in-process Claude dual-write; wake failure never fails acceptance
- Playbook ([#5](https://github.com/diechtiar/grok-mcp-orchestrator/issues/5)): [`docs/playbook.md`](docs/playbook.md)
- MCP server version bump to 0.6.0

## 0.5.0 — 2026-08-21

- `peer-bus watch` — unread inbox poller with idle backoff; emits `msg_id` + `from.address` only ([#4](https://github.com/diechtiar/grok-mcp-orchestrator/issues/4))
- Agent skill stub [`skills/peer-bus/SKILL.md`](skills/peer-bus/SKILL.md) ([#2](https://github.com/diechtiar/grok-mcp-orchestrator/issues/2))
- MCP server version bump to 0.5.0

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
