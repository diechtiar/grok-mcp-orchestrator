#!/usr/bin/env python3
"""peer-bus — cross-harness ListAgents / SendMessage for vida-dev.

Filesystem bus under PEER_BUS_ROOT (default /workspace/_shared/peer-bus).
Works for Grok and Claude without Claude's native SendMessage.

Semantics (aligned with peer-dispatch / peer-message-loss-is-measurable):
  - send() returning ok proves ACCEPTANCE (file written), never that a peer read it.
  - Prefer addressing by session id / ref; bare names can collide.
  - Reply to the `from` of the most recent received message when possible.
  - Delivery is poll-based: peers call receive / drain_inbox (no harness wake in v0).

CLI:
  peer-bus whoami [--as NAME]
  peer-bus list [--json]
  peer-bus send --to NAME|ID --body TEXT [--summary TEXT] [--as NAME]
  peer-bus recv [--as NAME] [--all] [--json]
  peer-bus ack MSG_ID [--as NAME]
  peer-bus heartbeat [--as NAME]   # refresh registry presence
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

ROOT = Path(os.environ.get("PEER_BUS_ROOT", "/workspace/_shared/peer-bus")).resolve()
INBOX = ROOT / "inbox"
REGISTRY = ROOT / "registry"
USAGE_DIR = Path(os.environ.get("USAGE_DIR", "/workspace/.usage"))
GROK_HOME = Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok")))
ACTIVE = GROK_HOME / "active_sessions.json"
SESSIONS = GROK_HOME / "sessions"

MAX_BODY = int(os.environ.get("PEER_BUS_MAX_BODY", "48000"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ensure_dirs() -> None:
    INBOX.mkdir(parents=True, exist_ok=True)
    REGISTRY.mkdir(parents=True, exist_ok=True)


def _slug(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9._+-]+", "-", text)
    return text.strip("-")[:80] or "anon"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def detect_self(explicit: str | None = None) -> dict[str, Any]:
    """Resolve this agent's identity."""
    if explicit:
        return {
            "key": _slug(explicit),
            "name": explicit,
            "harness": os.environ.get("PEER_BUS_HARNESS", "unknown"),
            "session_id": os.environ.get("GROK_SESSION_ID")
            or os.environ.get("CLAUDE_SESSION_ID")
            or os.environ.get("PEER_BUS_SESSION_ID"),
            "cwd": os.getcwd(),
        }

    env_name = os.environ.get("PEER_BUS_SELF") or os.environ.get("PEER_BUS_NAME")
    grok_sid = os.environ.get("GROK_SESSION_ID")
    name = env_name
    harness = os.environ.get("PEER_BUS_HARNESS")
    title = None
    if grok_sid:
        harness = harness or "grok"
        # Find summary across cwd groups
        for group in SESSIONS.glob("*"):
            summary = group / grok_sid / "summary.json"
            data = _read_json(summary)
            if data:
                title = data.get("generated_title") or data.get("session_summary") or None
                if data.get("title_is_manual") and data.get("generated_title"):
                    title = data["generated_title"]
                break
        name = name or title or f"grok-{grok_sid[:8]}"
    elif os.environ.get("CLAUDE_SESSION_ID"):
        harness = harness or "claude"
        name = name or os.environ.get("CLAUDE_SESSION_NAME") or f"claude-{os.environ['CLAUDE_SESSION_ID'][:8]}"
    else:
        harness = harness or "unknown"
        name = name or f"agent-{uuid.uuid4().hex[:8]}"

    return {
        "key": _slug(name),
        "name": name,
        "harness": harness,
        "session_id": grok_sid
        or os.environ.get("CLAUDE_SESSION_ID")
        or os.environ.get("PEER_BUS_SESSION_ID"),
        "cwd": os.getcwd(),
        "title": title,
    }


def heartbeat(self_info: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_dirs()
    me = self_info or detect_self()
    path = REGISTRY / f"{me['key']}.json"
    payload = {
        **me,
        "ts": _now(),
        "epoch": time.time(),
        "pid": os.getpid(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def _grok_agents() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    active = _read_json(ACTIVE) or []
    if not isinstance(active, list):
        return out
    by_id = {a.get("session_id"): a for a in active if isinstance(a, dict)}
    for sid, meta in by_id.items():
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
        out.append(
            {
                "key": _slug(name),
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
    if not USAGE_DIR.is_dir():
        return out
    now = time.time()
    for path in sorted(USAGE_DIR.glob("*.json")):
        if path.name.startswith(".") or path.name == "guard-state.json":
            continue
        data = _read_json(path)
        if not data:
            continue
        age_min = (now - path.stat().st_mtime) / 60
        name = data.get("name") or data.get("session_name") or path.stem[:12]
        sid = data.get("session") or data.get("session_id") or path.stem
        out.append(
            {
                "key": _slug(str(name)),
                "name": str(name),
                "ref": str(sid)[:6],
                "session_id": str(sid),
                "harness": "claude" if "claude" in str(data.get("model", "")).lower() or True else "unknown",
                # usage snapshots are Claude statusline in this env
                "state": "stale" if age_min > stale_min else "live",
                "cwd": data.get("cwd") or data.get("workspace"),
                "model": data.get("model"),
                "five_hour": data.get("five_hour"),
                "age_min": round(age_min, 1),
                "address": f"{name} [{str(sid)[:6]}]",
                "source": "usage",
            }
        )
    # Prefer labelling usage as claude — that's how this container writes them
    for row in out:
        if row.get("source") == "usage":
            row["harness"] = "claude"
    return out


def _registry_agents(stale_min: float = 15.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    now = time.time()
    if not REGISTRY.is_dir():
        return out
    for path in REGISTRY.glob("*.json"):
        data = _read_json(path)
        if not data:
            continue
        age_min = (now - float(data.get("epoch") or path.stat().st_mtime)) / 60
        name = data.get("name") or path.stem
        sid = data.get("session_id") or path.stem
        out.append(
            {
                "key": data.get("key") or _slug(str(name)),
                "name": name,
                "ref": str(sid)[:6] if sid else path.stem[:6],
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
    """Merge discovery sources. Prefer live; de-dupe by session_id then key."""
    _ensure_dirs()
    rows = _grok_agents() + _usage_agents() + _registry_agents()
    if not include_stale:
        rows = [r for r in rows if r.get("state") != "stale"]

    by_sid: dict[str, dict[str, Any]] = {}
    by_key: dict[str, dict[str, Any]] = {}
    merged: list[dict[str, Any]] = []
    for row in rows:
        sid = row.get("session_id")
        if sid and sid in by_sid:
            # Prefer grok active over usage/registry for same id
            prev = by_sid[sid]
            if prev.get("harness") == "grok" and row.get("harness") != "grok":
                continue
            if prev.get("source") == "usage" and row.get("harness") == "grok":
                merged.remove(prev)
            else:
                continue
        if sid:
            by_sid[sid] = row
        key = row.get("key")
        if key and key in by_key and by_key[key] is not row:
            # name collision — keep both but flag
            row["name_collision"] = True
            by_key[key]["name_collision"] = True
        if key:
            by_key[key] = row
        merged.append(row)

    # Count name collisions for the list summary
    names: dict[str, int] = {}
    for row in merged:
        names[row["name"]] = names.get(row["name"], 0) + 1
    for row in merged:
        if names.get(row["name"], 0) > 1:
            row["name_collision"] = True
    return merged


def resolve_recipient(to: str, agents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Resolve to= name | name [ref] | session_id | key."""
    agents = agents if agents is not None else list_agents(include_stale=True)
    raw = to.strip()
    m = re.match(r"^(.*?)\s*\[([0-9a-fA-F]{4,})\]\s*$", raw)
    name_part, ref_part = (m.group(1).strip(), m.group(2).lower()) if m else (raw, None)

    matches: list[dict[str, Any]] = []
    for a in agents:
        sid = str(a.get("session_id") or "")
        if raw == sid or raw == a.get("key") or raw.lower() == sid.lower():
            matches = [a]
            break
        if ref_part and sid.lower().startswith(ref_part):
            if _slug(name_part) == a.get("key") or name_part == a.get("name") or not name_part:
                matches.append(a)
            continue
        if name_part == a.get("name") or _slug(name_part) == a.get("key"):
            matches.append(a)

    if not matches:
        # Allow send to a key that only has an inbox / will be created
        return {
            "key": _slug(name_part or raw),
            "name": name_part or raw,
            "session_id": None,
            "harness": "unknown",
            "state": "unlisted",
            "address": raw,
            "warning": "recipient not in list_agents; inbox will still be created",
        }
    if len(matches) > 1 and not ref_part:
        opts = ", ".join(a.get("address") or a["name"] for a in matches)
        raise ValueError(f"ambiguous name {name_part!r}; disambiguate with ref: {opts}")
    if ref_part:
        refined = [a for a in matches if str(a.get("session_id") or "").lower().startswith(ref_part)]
        if len(refined) == 1:
            return refined[0]
        if not refined:
            raise ValueError(f"no agent matching {raw!r}")
        matches = refined
    return matches[0]


def send_message(
    to: str,
    body: str,
    *,
    summary: str | None = None,
    self_info: dict[str, Any] | None = None,
    msg_type: str = "message",
) -> dict[str, Any]:
    _ensure_dirs()
    me = self_info or detect_self()
    heartbeat(me)
    if len(body) > MAX_BODY:
        raise ValueError(f"body too large ({len(body)} > {MAX_BODY})")

    recipient = resolve_recipient(to)
    msg_id = uuid.uuid4().hex
    dest_dir = INBOX / recipient["key"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{int(time.time())}-{msg_id}.json"
    envelope = {
        "msg_id": msg_id,
        "ts": _now(),
        "type": msg_type,
        "summary": (summary or body.splitlines()[0])[:120],
        "body": body,
        "from": {
            "key": me["key"],
            "name": me["name"],
            "session_id": me.get("session_id"),
            "harness": me.get("harness"),
            "address": f"{me['name']} [{(me.get('session_id') or me['key'])[:6]}]",
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
    path.write_text(json.dumps(envelope, indent=2) + "\n")
    return {
        "ok": True,
        "accepted": True,
        "delivered_to_reader": False,  # never claim this
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
    _ensure_dirs()
    me = self_info or detect_self()
    heartbeat(me)
    dest = INBOX / me["key"]
    if not dest.is_dir():
        return []
    files = sorted(dest.glob("*.json"))
    out: list[dict[str, Any]] = []
    for path in files:
        data = _read_json(path)
        if not data:
            continue
        if unread_only and data.get("read"):
            continue
        data["_path"] = str(path)
        out.append(data)
        if len(out) >= limit:
            break
    return out


def ack_message(msg_id: str, self_info: dict[str, Any] | None = None) -> dict[str, Any]:
    me = self_info or detect_self()
    dest = INBOX / me["key"]
    for path in dest.glob("*.json"):
        data = _read_json(path)
        if not data or data.get("msg_id") != msg_id:
            continue
        data["read"] = True
        data["acked_at"] = _now()
        path.write_text(json.dumps(data, indent=2) + "\n")
        read_dir = dest / "read"
        read_dir.mkdir(exist_ok=True)
        path.rename(read_dir / path.name)
        return {"ok": True, "msg_id": msg_id, "moved_to": str(read_dir / path.name)}
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
    me = detect_self(args.as_name)
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
        print(m.get("summary") or "")
        print(m.get("body") or "")
    return 0


def _cmd_ack(args: argparse.Namespace) -> int:
    me = detect_self(args.as_name)
    print(json.dumps(ack_message(args.msg_id, me), indent=2))
    return 0


def _cmd_heartbeat(args: argparse.Namespace) -> int:
    print(json.dumps(heartbeat(detect_self(args.as_name)), indent=2))
    return 0


def _add_as(p: argparse.ArgumentParser) -> None:
    p.add_argument("--as", dest="as_name", default=None, help="override identity name")


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
