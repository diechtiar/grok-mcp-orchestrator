#!/usr/bin/env python3
"""peer-bus — cross-harness ListAgents / SendMessage.

Env-agnostic filesystem bus. Default root:
  $PEER_BUS_ROOT, else $XDG_DATA_HOME/peer-bus, else ~/.local/share/peer-bus

Optional discovery (skip if unset / missing):
  $PEER_BUS_USAGE_DIR or $USAGE_DIR  — Claude-style statusline snapshots
  $GROK_HOME (default ~/.grok)       — Grok active_sessions.json + summaries

Security (v0.4):
  - Inbox keys are session-bound when a harness session id is available (not spoofable via --as).
  - Session id from GROK_SESSION_ID / CLAUDE_SESSION_ID only; PEER_BUS_SESSION_ID needs
    PEER_BUS_ALLOW_SESSION_OVERRIDE=1 (off by default).
  - All inbox/registry paths are re-slugged and must resolve under the bus root (no traversal).
  - Symlink inbox directories are refused.
  - send() targets live agents by default (PEER_BUS_ALLOW_STALE_SEND=1 to include stale).
  - send() ok proves ACCEPTANCE only, never that a peer read the message.
  - --as / display_name only affects the human-readable from.name unless
    PEER_BUS_TRUST_NAME_KEYS=1 (dev/smoke only; MCP refuses to run with it set).

CLI:
  peer-bus whoami [--as DISPLAY]
  peer-bus list [--json] [--all]
  peer-bus send --to NAME|ID --body TEXT [--summary TEXT] [--as DISPLAY]
  peer-bus recv [--json] [--all]
  peer-bus ack MSG_ID
  peer-bus heartbeat [--as DISPLAY]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _default_root() -> Path:
    if os.environ.get("PEER_BUS_ROOT"):
        return Path(os.environ["PEER_BUS_ROOT"]).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "peer-bus"
    return Path.home() / ".local" / "share" / "peer-bus"


def _optional_dir(*env_names: str) -> Path | None:
    for name in env_names:
        raw = os.environ.get(name)
        if raw:
            return Path(raw).expanduser()
    return None


ROOT = _default_root().resolve()
INBOX = ROOT / "inbox"
REGISTRY = ROOT / "registry"
# Claude / other harness snapshots — only if explicitly configured (no host-specific default)
USAGE_DIR = _optional_dir("PEER_BUS_USAGE_DIR", "USAGE_DIR")
GROK_HOME = Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok"))).expanduser()
ACTIVE = GROK_HOME / "active_sessions.json"
SESSIONS = GROK_HOME / "sessions"

# Soft default + hard ceiling (env cannot raise above HARD_MAX_BODY)
HARD_MAX_BODY = 64_000
MAX_BODY = min(int(os.environ.get("PEER_BUS_MAX_BODY", "48000")), HARD_MAX_BODY)
MAX_INBOX_FILES = int(os.environ.get("PEER_BUS_MAX_INBOX_FILES", "200"))
TRUST_NAME_KEYS = os.environ.get("PEER_BUS_TRUST_NAME_KEYS", "").lower() in {"1", "true", "yes"}
ALLOW_SESSION_OVERRIDE = os.environ.get("PEER_BUS_ALLOW_SESSION_OVERRIDE", "").lower() in {
    "1",
    "true",
    "yes",
}
ALLOW_STALE_SEND = os.environ.get("PEER_BUS_ALLOW_STALE_SEND", "").lower() in {"1", "true", "yes"}
MAX_DISPLAY_NAME = 64


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ensure_dirs() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    REGISTRY.mkdir(parents=True, exist_ok=True)
    # Best-effort tighten bus dirs (some mounts ignore mode)
    for path in (ROOT, INBOX, REGISTRY):
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def _slug(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9._+-]+", "-", text)
    text = text.strip(".-+")
    # Collapse residual dot-dot style after substitution
    text = re.sub(r"\.{2,}", ".", text)
    return text[:80] or "anon"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_symlink():
            return None
        return json.loads(path.read_text())
    except Exception:
        return None


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _refuse_symlink(path: Path, label: str) -> None:
    if path.exists() and path.is_symlink():
        raise ValueError(f"refusing symlink {label}: {path}")
    # Also refuse if any parent under ROOT is a symlink escape — resolve already flattens,
    # but check the final path is still under ROOT.
    if path.exists() and not _is_under(path, ROOT):
        raise ValueError(f"path escapes peer-bus root: {path}")


def _safe_key(raw: str) -> str:
    key = _slug(raw)
    if "/" in key or "\\" in key or key in {".", ".."}:
        raise ValueError(f"illegal key after slug: {raw!r} -> {key!r}")
    return key


def _safe_display_name(raw: str | None) -> str | None:
    """Display-only name: no fake address suffixes, bounded length."""
    if raw is None:
        return None
    name = " ".join(str(raw).split())
    if not name:
        return None
    # Prevent forging "Name [abcdef]" in from.name / address presentation
    name = re.sub(r"\s*\[[0-9a-fA-F]{4,}\]\s*$", "", name).strip()
    if len(name) > MAX_DISPLAY_NAME:
        name = name[:MAX_DISPLAY_NAME].rstrip()
    return name or None


def _inbox_dir(key_raw: str, *, create: bool = True) -> Path:
    """Return a directory path guaranteed under INBOX (no traversal / symlink)."""
    key = _safe_key(key_raw)
    _ensure_dirs()
    dest = INBOX / key
    if dest.exists():
        _refuse_symlink(dest, "inbox dir")
        if not dest.is_dir():
            raise ValueError(f"inbox path is not a directory: {dest}")
    elif create:
        dest.mkdir(parents=True, exist_ok=True)
        _refuse_symlink(dest, "inbox dir")
    resolved = dest.resolve()
    if not _is_under(resolved, INBOX):
        raise ValueError(f"inbox path escapes INBOX: {resolved}")
    return dest


def _registry_path(key_raw: str) -> Path:
    key = _safe_key(key_raw)
    _ensure_dirs()
    path = REGISTRY / f"{key}.json"
    if path.exists():
        _refuse_symlink(path, "registry file")
    if not _is_under(path if not path.exists() else path.resolve(), REGISTRY):
        # non-existent: check parent
        if not _is_under(REGISTRY / key, REGISTRY):
            raise ValueError("registry path escape")
    return path


def _session_id() -> str | None:
    """Harness-injected ids only, unless PEER_BUS_ALLOW_SESSION_OVERRIDE=1."""
    sid = os.environ.get("GROK_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if sid:
        return sid
    if ALLOW_SESSION_OVERRIDE:
        return os.environ.get("PEER_BUS_SESSION_ID") or None
    return None


def detect_self(display_name: str | None = None) -> dict[str, Any]:
    """Resolve this agent's identity.

    Inbox key is bound to session id when present. display_name / --as only changes
    the human-readable name (and from.name on send), unless PEER_BUS_TRUST_NAME_KEYS=1.
    """
    sid = _session_id()
    harness = os.environ.get("PEER_BUS_HARNESS")
    title = None
    env_name = os.environ.get("PEER_BUS_SELF") or os.environ.get("PEER_BUS_NAME")

    if sid and (SESSIONS.is_dir()):
        harness = harness or ("grok" if os.environ.get("GROK_SESSION_ID") else None)
        for group in SESSIONS.glob("*"):
            summary = group / sid / "summary.json"
            data = _read_json(summary)
            if data:
                title = data.get("generated_title") or data.get("session_summary") or None
                if data.get("title_is_manual") and data.get("generated_title"):
                    title = data["generated_title"]
                harness = harness or "grok"
                break

    if os.environ.get("CLAUDE_SESSION_ID") and not harness:
        harness = "claude"

    name = _safe_display_name(display_name) or _safe_display_name(env_name) or _safe_display_name(title)
    if not name:
        name = f"grok-{sid[:8]}" if sid else f"anon-{uuid.uuid4().hex[:8]}"

    if sid and not TRUST_NAME_KEYS:
        key = _safe_key(sid)
    elif TRUST_NAME_KEYS and (display_name or env_name):
        key = _safe_key(display_name or env_name or name)
    elif sid:
        key = _safe_key(sid)
    else:
        key = _safe_key(name)

    return {
        "key": key,
        "name": name,
        "harness": harness or "unknown",
        "session_id": sid,
        "cwd": os.getcwd(),
        "title": title,
        "name_keys": TRUST_NAME_KEYS,
        "session_id_source": (
            "grok"
            if os.environ.get("GROK_SESSION_ID")
            else "claude"
            if os.environ.get("CLAUDE_SESSION_ID")
            else "override"
            if sid and ALLOW_SESSION_OVERRIDE
            else None
        ),
    }


def heartbeat(self_info: dict[str, Any] | None = None) -> dict[str, Any]:
    me = self_info or detect_self()
    path = _registry_path(me["key"])
    payload = {
        "key": me["key"],  # always our computed key, never caller-controlled alternate
        "name": me["name"],
        "harness": me.get("harness"),
        "session_id": me.get("session_id"),
        "cwd": me.get("cwd"),
        "ts": _now(),
        "epoch": time.time(),
        "pid": os.getpid(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return payload


def _grok_agents() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    active = _read_json(ACTIVE) or []
    if not isinstance(active, list):
        return out
    by_id = {a.get("session_id"): a for a in active if isinstance(a, dict)}
    for sid, meta in by_id.items():
        if not sid:
            continue
        title = None
        updated = None
        for group in SESSIONS.glob("*"):
            summary = group / sid / "summary.json"
            data = _read_json(summary)
            if data:
                title = data.get("generated_title") or data.get("session_summary") or None
                updated = data.get("last_active_at") or data.get("updated_at")
                break
        name = title or f"grok-{sid[:8]}"
        key = _safe_key(sid)
        out.append(
            {
                "key": key,
                "name": name,
                "ref": sid[:6],
                "session_id": sid,
                "harness": "grok",
                "state": "live",
                "cwd": meta.get("cwd"),
                "pid": meta.get("pid"),
                "opened_at": meta.get("opened_at"),
                "last_active_at": updated,
                "address": f"{name} [{sid[:6]}]",
            }
        )
    return out


def _usage_agents(stale_min: float = 30.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    usage_dir = USAGE_DIR
    if usage_dir is None or not usage_dir.is_dir():
        return out
    now = time.time()
    for path in sorted(usage_dir.glob("*.json")):
        if path.name.startswith(".") or path.name == "guard-state.json":
            continue
        if path.is_symlink():
            continue
        data = _read_json(path)
        if not data:
            continue
        age_min = (now - path.stat().st_mtime) / 60
        name = data.get("name") or data.get("session_name") or path.stem[:12]
        sid = data.get("session") or data.get("session_id") or path.stem
        key = _safe_key(str(sid))
        out.append(
            {
                "key": key,
                "name": str(name),
                "ref": str(sid)[:6],
                "session_id": str(sid),
                "harness": "claude",
                "state": "stale" if age_min > stale_min else "live",
                "cwd": data.get("cwd") or data.get("workspace"),
                "model": data.get("model"),
                "five_hour": data.get("five_hour"),
                "age_min": round(age_min, 1),
                "address": f"{name} [{str(sid)[:6]}]",
                "source": "usage",
            }
        )
    return out


def _registry_agents(stale_min: float = 15.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    now = time.time()
    if not REGISTRY.is_dir():
        return out
    for path in REGISTRY.glob("*.json"):
        if path.is_symlink():
            continue
        data = _read_json(path)
        if not data:
            continue
        # NEVER trust data["key"] from disk — filename stem is authoritative
        key = _safe_key(path.stem)
        age_min = (now - float(data.get("epoch") or path.stat().st_mtime)) / 60
        name = data.get("name") or key
        sid = data.get("session_id") or key
        out.append(
            {
                "key": key,
                "name": name,
                "ref": str(sid)[:6] if sid else key[:6],
                "session_id": sid,
                "harness": data.get("harness") or "unknown",
                "state": "stale" if age_min > stale_min else "live",
                "cwd": data.get("cwd"),
                "age_min": round(age_min, 1),
                "address": f"{name} [{str(sid)[:6]}]" if sid else name,
                "source": "registry",
            }
        )
    return out


def list_agents(include_stale: bool = False) -> list[dict[str, Any]]:
    _ensure_dirs()
    rows = _grok_agents() + _usage_agents() + _registry_agents()
    if not include_stale:
        rows = [r for r in rows if r.get("state") != "stale"]

    by_sid: dict[str, dict[str, Any]] = {}
    merged: list[dict[str, Any]] = []
    for row in rows:
        sid = row.get("session_id")
        if sid and sid in by_sid:
            prev = by_sid[sid]
            if prev.get("harness") == "grok" and row.get("harness") != "grok":
                continue
            if prev.get("source") == "usage" and row.get("harness") == "grok":
                merged.remove(prev)
            else:
                continue
        if sid:
            by_sid[sid] = row
        # Force safe key on every row
        row["key"] = _safe_key(str(row.get("key") or row.get("session_id") or row.get("name")))
        merged.append(row)

    names: dict[str, int] = {}
    for row in merged:
        names[row["name"]] = names.get(row["name"], 0) + 1
    for row in merged:
        if names.get(row["name"], 0) > 1:
            row["name_collision"] = True
    return merged


def resolve_recipient(
    to: str,
    agents: list[dict[str, Any]] | None = None,
    *,
    allow_stale: bool | None = None,
) -> dict[str, Any]:
    stale_ok = ALLOW_STALE_SEND if allow_stale is None else allow_stale
    agents = agents if agents is not None else list_agents(include_stale=stale_ok)
    raw = to.strip()
    m = re.match(r"^(.*?)\s*\[([0-9a-fA-F]{4,})\]\s*$", raw)
    name_part, ref_part = (m.group(1).strip(), m.group(2).lower()) if m else (raw, None)

    matches: list[dict[str, Any]] = []
    for a in agents:
        if not stale_ok and a.get("state") == "stale":
            continue
        sid = str(a.get("session_id") or "")
        key = str(a.get("key") or "")
        if raw == sid or raw == key or raw.lower() == sid.lower():
            matches = [a]
            break
        if ref_part and sid.lower().startswith(ref_part):
            if _slug(name_part) == key or name_part == a.get("name") or not name_part:
                matches.append(a)
            continue
        if name_part == a.get("name") or _slug(name_part) == key:
            matches.append(a)

    if not matches:
        if not stale_ok:
            # Retry once including stale to give a clearer error
            stale_agents = list_agents(include_stale=True)
            stale_hit = False
            for a in stale_agents:
                if a.get("state") != "stale":
                    continue
                sid = str(a.get("session_id") or "")
                if raw == sid or name_part == a.get("name") or (ref_part and sid.lower().startswith(ref_part)):
                    stale_hit = True
                    break
            if stale_hit:
                raise ValueError(
                    f"recipient {raw!r} is stale/offline; set PEER_BUS_ALLOW_STALE_SEND=1 to send anyway"
                )
        if not TRUST_NAME_KEYS:
            raise ValueError(
                f"unknown recipient {raw!r}; list_agents first, or set "
                "PEER_BUS_TRUST_NAME_KEYS=1 for name-keyed smoke tests"
            )
        return {
            "key": _safe_key(name_part or raw),
            "name": name_part or raw,
            "session_id": None,
            "harness": "unknown",
            "state": "unlisted",
            "address": raw,
            "warning": "unlisted name-key recipient (TRUST_NAME_KEYS)",
        }

    if len(matches) > 1 and not ref_part:
        opts = ", ".join(a.get("address") or a["name"] for a in matches)
        raise ValueError(f"ambiguous name {name_part!r}; disambiguate with ref: {opts}")
    if ref_part:
        refined = [a for a in matches if str(a.get("session_id") or "").lower().startswith(ref_part)]
        if len(refined) == 1:
            chosen = refined[0]
        elif not refined:
            raise ValueError(f"no agent matching {raw!r}")
        else:
            chosen = refined[0]
    else:
        chosen = matches[0]

    chosen = dict(chosen)
    chosen["key"] = _safe_key(str(chosen.get("session_id") or chosen.get("key")))
    return chosen


def send_message(
    to: str,
    body: str,
    *,
    summary: str | None = None,
    self_info: dict[str, Any] | None = None,
    msg_type: str = "message",
) -> dict[str, Any]:
    me = self_info or detect_self()
    heartbeat(me)
    if not isinstance(body, str):
        raise ValueError("body must be a string")
    if len(body) > MAX_BODY:
        raise ValueError(f"body too large ({len(body)} > {MAX_BODY})")

    recipient = resolve_recipient(to)
    dest_dir = _inbox_dir(recipient["key"], create=True)

    existing = list(dest_dir.glob("*.json"))
    if len(existing) >= MAX_INBOX_FILES:
        raise ValueError(f"inbox full for {recipient['key']} ({MAX_INBOX_FILES} files)")

    msg_id = uuid.uuid4().hex
    path = dest_dir / f"{int(time.time())}-{msg_id}.json"
    if path.exists() or path.is_symlink():
        raise ValueError("refusing to overwrite existing/symlink message path")

    envelope = {
        "msg_id": msg_id,
        "ts": _now(),
        "type": msg_type,
        "summary": (summary or (body.splitlines()[0] if body else ""))[:120],
        "body": body,
        "from": {
            "key": me["key"],
            "name": me["name"],
            "session_id": me.get("session_id"),
            "session_id_source": me.get("session_id_source"),
            "harness": me.get("harness"),
            "address": f"{me['name']} [{(me.get('session_id') or me['key'])[:6]}]",
            # Claim only: harness env provided the session id (not PEER_BUS_SESSION_OVERRIDE)
            "session_bound": me.get("session_id_source") in {"grok", "claude"},
        },
        "to": {
            "key": recipient["key"],
            "name": recipient.get("name"),
            "session_id": recipient.get("session_id"),
            "harness": recipient.get("harness"),
            "address": recipient.get("address") or to,
        },
        "read": False,
    }
    # Write without following symlinks: open with O_NOFOLLOW|O_CREAT|O_EXCL when possible
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(envelope, indent=2) + "\n")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    if not _is_under(path.resolve(), INBOX):
        path.unlink(missing_ok=True)
        raise ValueError("message path escaped INBOX after write")

    return {
        "ok": True,
        "accepted": True,
        "delivered_to_reader": False,
        "msg_id": msg_id,
        "path": str(path),
        "to": envelope["to"],
        "from": envelope["from"],
        "warning": recipient.get("warning"),
        "note": "acceptance only — peer must recv/drain; success≠read",
    }


def receive_messages(
    self_info: dict[str, Any] | None = None,
    *,
    unread_only: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    me = self_info or detect_self()
    heartbeat(me)
    try:
        dest = _inbox_dir(me["key"], create=False)
    except ValueError:
        return []
    if not dest.is_dir():
        return []
    files = sorted(p for p in dest.glob("*.json") if not p.is_symlink())
    out: list[dict[str, Any]] = []
    for path in files:
        data = _read_json(path)
        if not data:
            continue
        if unread_only and data.get("read"):
            continue
        data = dict(data)
        data["_path"] = str(path)
        data["untrusted"] = True
        # Safe view for model context (CLI/MCP can prefer these)
        body = data.get("body") if isinstance(data.get("body"), str) else ""
        data["body_for_model"] = (
            "<<<UNTRUSTED_PEER_MESSAGE>>>\n" + body + "\n<<<END_UNTRUSTED_PEER_MESSAGE>>>"
        )
        out.append(data)
        if len(out) >= max(1, min(limit, 100)):
            break
    return out


def ack_message(msg_id: str, self_info: dict[str, Any] | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F]{16,64}", msg_id or ""):
        return {"ok": False, "error": "invalid msg_id"}
    me = self_info or detect_self()
    try:
        dest = _inbox_dir(me["key"], create=False)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    for path in dest.glob("*.json"):
        if path.is_symlink():
            continue
        data = _read_json(path)
        if not data or data.get("msg_id") != msg_id:
            continue
        data["read"] = True
        data["acked_at"] = _now()
        read_dir = dest / "read"
        read_dir.mkdir(exist_ok=True)
        _refuse_symlink(read_dir, "read dir")
        target = read_dir / path.name
        path.write_text(json.dumps(data, indent=2) + "\n")
        path.rename(target)
        if not _is_under(target.resolve(), INBOX):
            return {"ok": False, "error": "ack target escaped INBOX"}
        return {"ok": True, "msg_id": msg_id, "moved_to": str(target)}
    return {"ok": False, "error": f"msg_id not found in inbox for {me['key']}: {msg_id}"}


# ---------- CLI ----------


def _cmd_whoami(args: argparse.Namespace) -> int:
    me = detect_self(args.as_name)
    heartbeat(me)
    print(json.dumps(me, indent=2))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    agents = list_agents(include_stale=args.all)
    if args.json:
        print(json.dumps(agents, indent=2))
        return 0
    if not agents:
        print("no agents found")
        return 0
    print(f"{'NAME':<32} {'REF':<8} {'HARNESS':<8} {'STATE':<6} ADDRESS")
    for a in agents:
        flag = " *" if a.get("name_collision") else ""
        print(
            f"{(a.get('name') or '')[:32]:<32} {(a.get('ref') or ''):<8} "
            f"{(a.get('harness') or ''):<8} {(a.get('state') or ''):<6} "
            f"{a.get('address')}{flag}"
        )
    if any(a.get("name_collision") for a in agents):
        print("\n* name collision — address with [ref] when sending")
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    me = detect_self(args.as_name)
    body = args.body
    if args.body_file:
        # CLI-only: read a local file the operator chose
        body = Path(args.body_file).read_text()
    if body is None:
        body = sys.stdin.read()
    try:
        result = send_message(args.to, body, summary=args.summary, self_info=me)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def _cmd_recv(args: argparse.Namespace) -> int:
    # recv ignores --as for inbox selection unless TRUST_NAME_KEYS
    me = detect_self(args.as_name if TRUST_NAME_KEYS else None)
    if args.as_name and not TRUST_NAME_KEYS:
        me["name"] = args.as_name  # display only; key unchanged
    msgs = receive_messages(me, unread_only=not args.all)
    if args.json:
        print(json.dumps(msgs, indent=2))
        return 0
    if not msgs:
        print("inbox empty")
        return 0
    for m in msgs:
        fr = m.get("from") or {}
        print(f"--- {m.get('msg_id')} from={fr.get('address') or fr.get('name')} ts={m.get('ts')}")
        print("<<<UNTRUSTED_PEER_MESSAGE>>>")
        print(m.get("summary") or "")
        print(m.get("body") or "")
        print("<<<END_UNTRUSTED_PEER_MESSAGE>>>")
    return 0


def _cmd_ack(args: argparse.Namespace) -> int:
    me = detect_self(args.as_name if TRUST_NAME_KEYS else None)
    print(json.dumps(ack_message(args.msg_id, me), indent=2))
    return 0


def _cmd_heartbeat(args: argparse.Namespace) -> int:
    print(json.dumps(heartbeat(detect_self(args.as_name)), indent=2))
    return 0


def _add_as(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--as",
        dest="as_name",
        default=None,
        help="display name only (inbox key stays session-bound unless PEER_BUS_TRUST_NAME_KEYS=1)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="peer-bus", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("whoami")
    _add_as(p)
    p.set_defaults(func=_cmd_whoami)

    p = sub.add_parser("list")
    _add_as(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true", help="include stale")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("send")
    _add_as(p)
    p.add_argument("--to", required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--summary")
    p.set_defaults(func=_cmd_send)

    p = sub.add_parser("recv")
    _add_as(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true", help="include already-acked")
    p.set_defaults(func=_cmd_recv)

    p = sub.add_parser("ack")
    _add_as(p)
    p.add_argument("msg_id")
    p.set_defaults(func=_cmd_ack)

    p = sub.add_parser("heartbeat")
    _add_as(p)
    p.set_defaults(func=_cmd_heartbeat)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
