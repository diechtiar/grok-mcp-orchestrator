# Peer-bus playbook

Worked end-to-end multi-session flow (Grok↔Grok or Grok↔Claude on the same OS user).

## 0. Preconditions

- Both sessions load peer-bus MCP **or** can run `python3 peer_bus.py …`
- Same bus root (default `~/.local/share/peer-bus`, or identical `PEER_BUS_ROOT`)
- Optional: `PEER_BUS_USAGE_DIR` so Grok `list` sees Claude statusline snapshots

## 1. Discover

```bash
python3 peer_bus.py list
# or MCP list_agents
```

Copy an address with ref when names collide: `Luke [01a023]`.

## 2. Send a contract (not an essay)

```bash
python3 peer_bus.py send --as Rick --to "Luke [01a023]" --body $'@v1 topic
CTX|one-line frame
DO|outcome contract
NOT|stop point
PRIOR|claim|d=how|t=HH:MM
RPT|ack,artefact,wrong_priors'
```

`ok: true` means **accepted** (inbox file written). It does **not** mean Luke has read it.

Wake (best-effort, never fails accept):

- Default: drop `$PEER_BUS_ROOT/wake/<recipient-key>.json`
- Optional: `PEER_BUS_WAKE=1` + `PEER_BUS_WAKE_CMD='…'`
- Optional: in-process `peer_bus.set_wake_callback(...)` (e.g. Claude native SendMessage host)

## 3. Recipient pulls

On Luke’s session (turn start, `/loop`, or `peer-bus watch`):

```bash
python3 peer_bus.py recv
# handle UNTRUSTED body
python3 peer_bus.py ack <msg_id>
```

Monitor-friendly (one line per new unread; idle backoff):

```bash
python3 peer_bus.py watch
# → <msg_id>\t<from.address>
```

## 4. Reply

Send to the latest inbound `from.address` (not a guessed display name).

```bash
python3 peer_bus.py send --as Luke --to "Rick [019fc7]" --body $'@v1 topic
ACK|ok
FACT|…|e=…
RPT|ack,artefact,wrong_priors'
```

## 5. Silence

If no reply:

1. Confirm the inbox file exists under `$PEER_BUS_ROOT/inbox/<key>/`
2. Confirm the peer session is live (`list`)
3. Treat silence as a **channel/artefact** gap — check the branch/PR/file yourself
4. Do **not** infer “peer refused” from acceptance alone; do **not** resend stacks of duplicates without checking

## 6. Skill pointers

- Agent stub: [`skills/peer-bus/SKILL.md`](../skills/peer-bus/SKILL.md)
- Contract/priors grammar: peer-dispatch (when installed in the harness)
- Trust model: [SECURITY.md](../SECURITY.md)
