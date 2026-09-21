# Roadmap

Tracked on the [GitHub Project](https://github.com/users/diechtiar/projects/4).

## Current (v0.10.0)

Stdlib CLI + stdio MCP. Live roster (Herdr, optional tmux, Grok pids, usage overlay).
Send/recv/ack with session-bound keys. `watch` (inotify; `--max-runtime`). `mail`. `prune`.
`bus_version` on whoami/flock/envelopes; `pool.schema`; `doctor`; optional ack receipts.
Acceptance is not delivery. Same-UID cooperative bus — see [SECURITY.md](SECURITY.md).

## Next

1. Native Grok dashboard inject — **blocked** on product APIs. Listen model stays pull (`recv` / `watch`).
2. Publish tags when cutting a GitHub release.

## Non-goals

- Multi-tenant / cross-UID security
- Treating `ok: true` as the peer having read the mail
- Relaying user authority or secrets on the bus
- Replacing Claude↔Claude native SendMessage

## Done

- [x] CLI + zero-dep stdio MCP — **v0.4.0**
- [x] Env-agnostic bus root (`XDG` / `~/.local/share/peer-bus`)
- [x] Path containment, symlink refusal, session-bound keys
- [x] Medium-risk hardenings (session override gate, live-only send, MCP trust refusal, untrusted recv wrappers)
- [x] Documented Grok + Claude MCP setup ([README](README.md))
- [x] [#1](https://github.com/diechtiar/grok-mcp-orchestrator/issues/1) Claude Code MCP settings recipe
- [x] [#2](https://github.com/diechtiar/grok-mcp-orchestrator/issues/2) Agent skill stub
- [x] [#4](https://github.com/diechtiar/grok-mcp-orchestrator/issues/4) Inbox monitor — `peer-bus watch`
- [x] [#3](https://github.com/diechtiar/grok-mcp-orchestrator/issues/3) Wake bridge
- [x] [#5](https://github.com/diechtiar/grok-mcp-orchestrator/issues/5) Playbook
- [x] Live roster (tmux, then Herdr-first) — **v0.7–0.9**
- [x] Evented watch + unread on statuslines — **v0.8.0**
- [x] Hyphenated refs, prune, ancestor session id, PATH herdr — **v0.9.1–0.9.6**
- [x] Ambiguous-ref refuse, single roster fetch, watch max-runtime, generic docs — **v0.9.7**
- [x] Multiplexer JSON schema pin + `self-test`; Claude inbox-socket dual-write — **v0.9.8**
- [x] Independent 5h+7d pool windows, `pool.state` live|stale — **v0.9.9**
- [x] `bus_version` on payloads, `pool.schema`, `doctor`, optional ack receipts — **v0.10.0**
