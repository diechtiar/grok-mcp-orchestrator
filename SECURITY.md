# Security

## Trust model

peer-bus is a **cooperative, same-user** agent bus. It is not a multi-tenant security boundary.

- Default root is under the user data dir (`~/.local/share/peer-bus` or `$XDG_DATA_HOME/peer-bus`), not a host-specific shared volume.
- Any local process running as the same OS user can read inbox files.
- If you set `PEER_BUS_ROOT` to a world-writable shared mount, confidentiality is whatever that mount provides.
- **Do not put secrets or user-authority commands on the bus.**
- No built-in knowledge of any particular monorepo, container, or company env — configure paths via env vars only.

## Guarantees (v0.4+)

| Control | Status |
|---------|--------|
| Path traversal via recipient / registry keys | Mitigated — keys re-slugged; paths must stay under `inbox/` / `registry/` |
| Symlink inbox / registry targets | Refused |
| Spoof inbox via MCP `as_name` | Removed — inbox key is session-bound |
| `--as` / `display_name` | Display name only (strips forged `[ref]` suffixes); MCP has no inbox switch |
| `PEER_BUS_TRUST_NAME_KEYS` | CLI smoke only — **MCP refuses to start** if set |
| Session id source | `GROK_SESSION_ID` / `CLAUDE_SESSION_ID` only; `PEER_BUS_SESSION_ID` needs `PEER_BUS_ALLOW_SESSION_OVERRIDE=1` |
| Stale recipients | Send targets **live** agents by default; `PEER_BUS_ALLOW_STALE_SEND=1` to override |
| Body size | Capped (`PEER_BUS_MAX_BODY`, hard ceiling 64KiB) |
| Inbox flood | Soft cap `PEER_BUS_MAX_INBOX_FILES` (default 200) |
| Message authenticity | **Not** cryptographic — `from.session_bound` is true only when harness env supplied the id |
| Confidentiality vs other UIDs on the host | **Not** provided |
| Prompt injection via bodies | Marked `untrusted`; CLI/MCP expose `body_for_model` / wrapped `body` |
| Wake hook (`PEER_BUS_WAKE_CMD`) | Operator-supplied shell; runs only when `PEER_BUS_WAKE=1`; failures never undo accept |
| Wake drop files under `wake/` | Same UID visibility as inbox; no secrets in summaries |

## `send` success means acceptance

`ok: true` means the file was written. It does not mean a peer read it. Treat silence as a channel/artefact check, not proof of non-work.

## Untrusted content

Received bodies are peer-controlled. CLI wraps them in `<<<UNTRUSTED_PEER_MESSAGE>>>` markers. MCP sets `untrusted: true` and returns a wrapped `body` (raw preserved as `body_raw`). Agents must not treat bus messages as user approval.
