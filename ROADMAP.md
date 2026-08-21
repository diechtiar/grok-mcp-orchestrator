# Roadmap

Tracked on the [GitHub Project](https://github.com/users/diechtiar/projects/4).

## Done

- [x] CLI + zero-dep stdio MCP (`list_agents`, `send_message`, `receive_messages`, `ack_message`, `whoami`, `heartbeat`) — **v0.4.0**
- [x] Env-agnostic bus root (`XDG` / `~/.local/share/peer-bus`)
- [x] Path containment, symlink refusal, session-bound keys
- [x] Medium-risk hardenings (session override gate, live-only send, MCP trust refusal, untrusted recv wrappers)
- [x] Documented Grok + Claude MCP setup ([README](README.md))
- [x] [#1](https://github.com/diechtiar/grok-mcp-orchestrator/issues/1) Claude Code MCP settings recipe

## Open

1. **P1 — Agent skill stub** — point agents at README + `@v1` dispatch wire format ([#2](https://github.com/diechtiar/grok-mcp-orchestrator/issues/2)).
2. **P2 — Wake bridge** — optional dual-write to Claude native `SendMessage` when available ([#3](https://github.com/diechtiar/grok-mcp-orchestrator/issues/3)).
3. **P2 — Inbox monitor** — watch unread for `/loop` / monitors ([#4](https://github.com/diechtiar/grok-mcp-orchestrator/issues/4)).
4. **P3 — Playbook doc** — worked end-to-end example beyond README ([#5](https://github.com/diechtiar/grok-mcp-orchestrator/issues/5)).
5. **P3 — Native Grok dashboard inject** — product-side wake if APIs land.
