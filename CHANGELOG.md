# Changelog

## 0.10.0 — 2026-09-21

- `bus_version` is on `whoami`, `flock`/`list_agents`, and each message envelope.
  Compare MCP `whoami.bus_version` to CLI `peer-bus version` — a mismatch means
  the stdio process predates the tree on disk.
- `pool.schema` is an integer (`2` = `five_hour` + `seven_day` + `state`/`age_min`).
- `peer-bus doctor` reports CLI version, on-disk MCP `SERVER_INFO`, multiplexer
  schema, usage dir, and pool schema. It does not inspect a live MCP pid.
- Ack writes an optional sender-side receipt under `$PEER_BUS_ROOT/receipts/<from-key>/`.
  Opt out with `PEER_BUS_ACK_RECEIPTS=0`. A receipt is not proof of understanding.

## 0.9.9 — 2026-09-20

- `pool` includes `seven_day` / `seven_day_resets_at` beside `five_hour`. Each
  window is omitted when its own reset is in the past; an expired 5h sample no
  longer hides a live 7d figure.
- `pool.state` is `live` or `stale` from snapshot age (same 5 min threshold as
  per-seat `context~`), plus `age_min`. CLI prints both windows and the state.

## 0.9.8 — 2026-09-20

- Pinned JSON schema for multiplexer `agent list`, `pane list`, and `process-info`.
  `peer-bus self-test` checks a live binary when present (skip if missing).
- After accept, Claude recipients also get a native inbox-socket post (same channel
  as SendMessage). Opt out with `PEER_BUS_CLAUDE_UDS=0`. Failure never undoes accept.

## 0.9.7 — 2026-09-19

- Docs and tests are host-agnostic: no machine, user, or seat names in the tree.
- Ambiguous flock `[ref]` prefixes raise instead of picking the first match.
- `resolve_recipient` loads the roster once (stale rows used only for the error).
- `receive_messages` no longer writes a registry heartbeat.
- `watch --max-runtime SEC` / `PEER_BUS_WATCH_MAX_RUNTIME` exits 0 when the budget elapses.

## 0.9.6 — 2026-09-10

- `resolve_recipient` accepts hyphenated flock refs. Two session ids sharing the
  first 8 hex chars print `Name [01a08225-0]`; a hex-only character class treated
  that as an unknown name. Copy-paste from the address column failed; the bare
  name still resolved.

## 0.9.5 — 2026-09-09

- `detect_self` / `watch` inherit session id from an ancestor `grok --resume <sid>`
  when `GROK_SESSION_ID` is unset. Without that walk, `watch --as DISPLAY` bound a
  name-key and never saw the session inbox.

## 0.9.4 — 2026-09-09

- `_herdr_cmd` finds `herdr` in `$PATH`, then `~/.local/bin/herdr`. Claude MCP
  stdio often has `PATH=/usr/bin:/bin`, so `list_agents` dropped Herdr Claude
  seats and showed grok+usage only. Override: `PEER_BUS_HERDR_BIN`. Attach-client
  panes still resolve via process-info (`attach <short-id>` → `claude agents --json`);
  a test pins that shape.

## 0.9.3 — 2026-09-08

- Live roster prefers **Herdr** over tmux. Default on; `PEER_BUS_TMUX=1` still
  scans tmux when that flock is the one running. `PEER_BUS_TMUX=0` skips tmux.

## 0.9.2 — 2026-09-08

- Herdr `claude attach` panes keep the shell window title; flock uses the pane
  **label** when the title is still `user@host:cwd`. Process-info also reads
  `attach <short-id>` the same way as `--resume`.

- MCP `send_message` schema is `to` + `body` only. Extra fields (`display_name`,
  `summary`) made some hosts drop `to`. Aliases `recipient`/`address` and a
  first-line `TO Name [ref]` prefix recover a dropped `to`. Error text lists
  the keys that arrived.
- `peer-bus prune` (dry-run) / `peer-bus prune --apply` removes dead registry
  rows, usage snaps whose sid is not live, and empty inboxes. Live flock sids,
  still-alive pids, and inboxes with unread files stay.

## 0.9.1 — 2026-09-08

- Live roster includes **Herdr** session `agents` (`herdr --session agents agent list`),
  same authority as tmux: a visible agent pane is live even when usage snapshots
  are older than 30 minutes. `PEER_BUS_HERDR=0` disables; `PEER_BUS_HERDR_SESSION`
  selects the session (default `agents`). Attach panes with no `agent_session` fall
  back to `pane process-info` (`--resume` / `--session-id` argv, then pid environ).
- Usage snaps overlay model/context only; they do not mark tmux/herdr rows stale
  or rename a titled seat.
- `peer-bus mail --flock` sums unread across live seats. `scripts/herdr-mail-status.sh`
  prints a compact `mail N` for a multiplexer status bar.
- MCP `send_message` returns `missing to` / `missing body` instead of a KeyError.

## 0.9.0 — 2026-09-08

- Roster `five_hour` is one account-wide **pool**, not a per-seat column. CLI prints
  `POOL 5h N%` once; MCP `list_agents` / `flock` return `{pool, agents}`. Expired
  windows (`five_hour_resets_at` in the past) are dropped — same predicate as
  `pool_samples()`. Per-seat discriminator is `context` (`~` when the snapshot is
  older than 5 minutes).
- `receive_messages` returns **newest first**; `limit` caps the newest N. It still
  does not consume — `ack_message` moves the file. (Watch peeks through the same
  function.)
- `watch` without `--as` re-execs with `--as <detect_self name>` so a launched
  watcher shows the seat display name in `ps`, not an untitled python.

## 0.8.0 — 2026-09-04

- `watch` uses Linux inotify on `inbox/<key>/` (CREATE / MOVED_TO / CLOSE_WRITE);
  `PEER_BUS_WATCH=poll` keeps the sleep backoff. Same one-line output.
- `peer-bus mail` / MCP `mail_count`: unread file count, no bodies.
- Claude statusline shows `mail N` when N>0. Grok statusline script +
  `[ui.status_line]` (`mail N`, cached `flock N`).

## 0.7.0 — 2026-09-04

- Live roster (P1): `list_agents` / `peer-bus flock` prefer tmux pane title + pid tree
  (`CLAUDE_CODE_SESSION_ID` / `GROK_SESSION_ID`), then grok `active_sessions` (pid must
  be alive), then fresh usage snaps. Registry heartbeats and stale usage are not live
  on their own. A work tracker is not presence.
- `detect_self` names Claude seats from tmux/usage; never `grok-<sid>` for a Claude harness.
- Skip usage `throttle.json` / `guard-state.json`. Ghost registry names (inbox-loop
  subagents, `anon-*`) stay out of the default list.
- MCP tool `flock` aliases `list_agents`. `PEER_BUS_TMUX=0` disables the tmux scan.
- Collapse wrapper Grok sids that share a pid with a named seat. Grow `[ref]` past 6 chars when prefixes collide.
- Registry heartbeats (including live MCP stdio pids) are never a seat.

## 0.6.6 — 2026-08-21

- Document accepted operator model: user instructs each session to listen; no Grok push-wake

## 0.6.5 — 2026-08-21

- `peer-bus version` CLI
- stdlib `tests/test_peer_bus.py` (safe key, Claude session id, send/recv/ack/wake, symlink refuse)
- CI runs unittest before smoke
- Tests: MCP TRUST_NAME_KEYS refusal; body size cap; stale-send refusal
- CONTRIBUTING.md
- CI: `actions/checkout@v5`, `actions/setup-python@v6` (Node 24 runtime)
- Tests: GROK vs Claude session-id order; watch --once empty stdout; CLI TRUST_NAME_KEYS send/recv + wake drop

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
