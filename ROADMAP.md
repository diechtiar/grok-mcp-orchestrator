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
- [x] [#3](https://github.com/diechtiar/grok-mcp-orchestrator/issues/3) Wake bridge — drop file + optional `PEER_BUS_WAKE_CMD` / in-process callback (**v0.6.0**)
- [x] [#5](https://github.com/diechtiar/grok-mcp-orchestrator/issues/5) Playbook — [`docs/playbook.md`](docs/playbook.md) (**v0.6.0**)

## Open

1. **P3 — Native Grok dashboard inject** — optional product enhancement if APIs land. **Accepted operator model (2026-08-21):** the user instructs each session to listen (`recv` at turn start, `peer-bus watch` / monitor, or Claude wake hook). Peer-bus does not push-wake Grok sessions.
2. ~~**Hardening** — automated smoke/CI~~ — `scripts/smoke.sh` + unittest + `.github/workflows/smoke.yml` (**v0.6.5**).

Issues remain open until the operator closes them; roadmap checkboxes track shipped code/docs only.
