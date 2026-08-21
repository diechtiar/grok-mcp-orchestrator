# Roadmap

Tracked on the [GitHub Project](https://github.com/users/diechtiar/projects/4).

## Done

- [x] CLI + zero-dep stdio MCP (`list_agents`, `send_message`, `receive_messages`, `ack_message`, `whoami`, `heartbeat`) — **v0.4.0**
- [x] Env-agnostic bus root (`XDG` / `~/.local/share/peer-bus`)
- [x] Path containment, symlink refusal, session-bound keys
- [x] Medium-risk hardenings (session override gate, live-only send, MCP trust refusal, untrusted recv wrappers)
- [x] Documented Grok + Claude MCP setup ([README](README.md))
- [x] [#1](https://github.com/diechtiar/grok-mcp-orchestrator/issues/1) Claude Code MCP settings recipe
- [x] [#2](https://github.com/diechtiar/grok-mcp-orchestrator/issues/2) Agent skill stub — [`skills/peer-bus/SKILL.md`](skills/peer-bus/SKILL.md) (**v0.5.0**)
- [x] [#4](https://github.com/diechtiar/grok-mcp-orchestrator/issues/4) Inbox monitor — `peer-bus watch` with idle backoff (**v0.5.0**)

## Open

1. **P2 — Wake bridge** — optional dual-write to Claude native `SendMessage` when available ([#3](https://github.com/diechtiar/grok-mcp-orchestrator/issues/3)).
2. **P3 — Playbook doc** — worked end-to-end example beyond README ([#5](https://github.com/diechtiar/grok-mcp-orchestrator/issues/5)).
3. **P3 — Native Grok dashboard inject** — product-side wake if APIs land.

Issues remain open until the operator closes them; roadmap checkboxes track shipped code/docs only.
