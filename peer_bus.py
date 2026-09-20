#!/usr/bin/env python3
"""peer-bus — cross-harness ListAgents / SendMessage.

Env-agnostic filesystem bus. Default root:
  $PEER_BUS_ROOT, else $XDG_DATA_HOME/peer-bus, else ~/.local/share/peer-bus

Optional discovery (skip if unset / missing):
  $PEER_BUS_USAGE_DIR or $USAGE_DIR  — Claude-style statusline snapshots
  $GROK_HOME (default ~/.grok)       — Grok active_sessions.json + summaries

Security (v0.4):
  - Inbox keys are session-bound when a harness session id is available (not spoofable via --as).
  - Session id from GROK_SESSION_ID / CLAUDE_SESSION_ID / CLAUDE_CODE_SESSION_ID only;
    PEER_BUS_SESSION_ID needs PEER_BUS_ALLOW_SESSION_OVERRIDE=1 (off by default).
  - All inbox/registry paths are re-slugged and must resolve under the bus root (no traversal).
  - Symlink inbox directories are refused.
  - send() targets live agents by default (PEER_BUS_ALLOW_STALE_SEND=1 to include stale).
  - send() ok proves ACCEPTANCE only, never that a peer read the message.
  - --as / display_name only affects the human-readable from.name unless
    PEER_BUS_TRUST_NAME_KEYS=1 (dev/smoke only; MCP refuses to run with it set).

CLI:
  peer-bus whoami [--as DISPLAY]
  peer-bus list [--json] [--all]
  peer-bus flock [--json] [--all]   # alias of list (live roster)
  peer-bus mail [--count] [--as DISPLAY]   # unread inbox count (no bodies)
  peer-bus send --to NAME|ID --body TEXT [--summary TEXT] [--as DISPLAY]
  peer-bus recv [--json] [--all]
  peer-bus ack MSG_ID
  peer-bus heartbeat [--as DISPLAY]
  peer-bus watch [--interval SEC] [--max-interval SEC] [--once]
  peer-bus prune [--apply]         # dead registry/usage/empty-inbox (dry-run default)
  peer-bus version
"""
from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PEER_BUS_VERSION = "0.9.7"


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
WAKE = ROOT / "wake"
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
# Optional wake after accept (never fails send). See _try_wake().
WAKE_ENABLED = os.environ.get("PEER_BUS_WAKE", "").lower() in {"1", "true", "yes"}
WAKE_CMD = os.environ.get("PEER_BUS_WAKE_CMD", "").strip()
WAKE_DROP = os.environ.get("PEER_BUS_WAKE_DROP", "1").lower() in {"1", "true", "yes"}
MAX_DISPLAY_NAME = 64
# In-process wake callback (e.g. Claude native SendMessage). Set by host; never required.
_WAKE_CALLBACK: Any = None
# tmux roster cache: (monotonic_ts, rows). Empty when PEER_BUS_TMUX=0.
_TMUX_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_TMUX_CACHE_TTL = 2.0
# herdr roster cache: (monotonic_ts, rows). Empty when PEER_BUS_HERDR=0.
_HERDR_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_HERDR_CACHE_TTL = 2.0
_CLAUDE_BG_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_CLAUDE_BG_CACHE_TTL = 5.0
_AUTHORITY_SOURCES = frozenset({"tmux", "herdr"})
_USAGE_SKIP_STEMS = {"guard-state", "throttle"}
_SID_ENV_KEYS = ("GROK_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
# Same window fleet-roster.sh uses for a quiet seat's context figure.
_CONTEXT_STALE_MIN = 5.0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ensure_dirs() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    REGISTRY.mkdir(parents=True, exist_ok=True)
    WAKE.mkdir(parents=True, exist_ok=True)
    # Best-effort tighten bus dirs (some mounts ignore mode)
    for path in (ROOT, INBOX, REGISTRY, WAKE):
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def set_wake_callback(callback: Any) -> None:
    """Register an in-process wake fn(envelope, recipient) -> None. Failures are swallowed."""
    global _WAKE_CALLBACK
    _WAKE_CALLBACK = callback


def _try_wake(envelope: dict[str, Any], recipient: dict[str, Any]) -> dict[str, Any]:
    """Best-effort peer wake after inbox accept. Never raises; never undoes acceptance."""
    out: dict[str, Any] = {"attempted": False, "ok": None, "methods": [], "error": None}
    if _WAKE_CALLBACK is None and not (WAKE_ENABLED and WAKE_CMD) and not WAKE_DROP:
        return out
    methods_ok = 0
    methods_tried = 0

    def _mark(method: str, ok: bool, err: str | None = None) -> None:
        nonlocal methods_ok, methods_tried
        methods_tried += 1
        out["methods"].append({"method": method, "ok": ok, "error": err})
        if ok:
            methods_ok += 1
        elif err and not out["error"]:
            out["error"] = err

    # 1) In-process callback (Claude native SendMessage when host wired it)
    if _WAKE_CALLBACK is not None:
        try:
            _WAKE_CALLBACK(envelope, recipient)
            _mark("callback", True)
        except Exception as exc:  # noqa: BLE001 — wake must not fail send
            _mark("callback", False, f"{type(exc).__name__}: {exc}")

    # 2) Operator-supplied shell command (PEER_BUS_WAKE=1 + PEER_BUS_WAKE_CMD)
    if WAKE_ENABLED and WAKE_CMD:
        try:
            import subprocess

            env = os.environ.copy()
            env.update(
                {
                    "PEER_BUS_WAKE_MSG_ID": str(envelope.get("msg_id") or ""),
                    "PEER_BUS_WAKE_TO_KEY": str(recipient.get("key") or ""),
                    "PEER_BUS_WAKE_TO_ADDRESS": str(
                        (envelope.get("to") or {}).get("address") or recipient.get("address") or ""
                    ),
                    "PEER_BUS_WAKE_TO_HARNESS": str(recipient.get("harness") or ""),
                    "PEER_BUS_WAKE_FROM_ADDRESS": str(
                        (envelope.get("from") or {}).get("address") or ""
                    ),
                    "PEER_BUS_WAKE_SUMMARY": str(envelope.get("summary") or "")[:200],
                    "PEER_BUS_WAKE_PATH": str(envelope.get("_path") or ""),
                }
            )
            proc = subprocess.run(
                WAKE_CMD,
                shell=True,
                env=env,
                timeout=5,
                capture_output=True,
                text=True,
                check=False,
            )
            _mark(
                "cmd",
                proc.returncode == 0,
                None if proc.returncode == 0 else f"exit {proc.returncode}",
            )
        except Exception as exc:  # noqa: BLE001
            _mark("cmd", False, f"{type(exc).__name__}: {exc}")

    # 3) Drop a wake marker under $PEER_BUS_ROOT/wake/<key>.json (external pollers)
    if WAKE_DROP:
        try:
            _ensure_dirs()
            key = _safe_key(str(recipient.get("key") or "anon"))
            drop = WAKE / f"{key}.json"
            if drop.is_symlink():
                _mark("drop", False, "symlink wake target refused")
            else:
                payload = {
                    "msg_id": envelope.get("msg_id"),
                    "ts": envelope.get("ts"),
                    "to": envelope.get("to"),
                    "from": envelope.get("from"),
                    "summary": envelope.get("summary"),
                }
                tmp = drop.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, indent=2) + "\n")
                os.chmod(tmp, 0o600)
                tmp.replace(drop)
                if not _is_under(drop.resolve(), WAKE):
                    drop.unlink(missing_ok=True)
                    _mark("drop", False, "wake path escaped WAKE")
                else:
                    _mark("drop", True)
        except Exception as exc:  # noqa: BLE001
            _mark("drop", False, f"{type(exc).__name__}: {exc}")

    out["attempted"] = methods_tried > 0
    out["ok"] = methods_ok > 0 if methods_tried else None
    return out


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


def _claude_session_id() -> str | None:
    """Claude Code may inject CLAUDE_CODE_SESSION_ID (2.1.x) or CLAUDE_SESSION_ID."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")


def _sid_from_grok_argv(argv: list[str]) -> str | None:
    if not argv:
        return None
    base = argv[0].rsplit("/", 1)[-1].lower()
    if "grok" not in base:
        return None
    for i, arg in enumerate(argv):
        if arg in ("--resume", "--session-id") and i + 1 < len(argv) and len(argv[i + 1]) >= 8:
            return argv[i + 1]
    return None


def _grok_sid_from_ancestor_argv(*, max_hops: int = 12) -> str | None:
    """Grok --resume <sid> on an ancestor (watch/MCP children often lack GROK_SESSION_ID)."""
    pid = os.getpid()
    for _ in range(max_hops):
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return None
        argv = [x.decode(errors="replace") for x in raw.split(b"\0") if x]
        sid = _sid_from_grok_argv(argv)
        if sid:
            return sid
        try:
            ppid = int(Path(f"/proc/{pid}/stat").read_text().split()[3])
        except (OSError, IndexError, ValueError):
            return None
        if ppid <= 1 or ppid == pid:
            return None
        pid = ppid
    return None


def _session_id() -> str | None:
    """Harness-injected ids only, unless PEER_BUS_ALLOW_SESSION_OVERRIDE=1."""
    sid = os.environ.get("GROK_SESSION_ID") or _claude_session_id()
    if sid:
        return sid
    if ALLOW_SESSION_OVERRIDE:
        return os.environ.get("PEER_BUS_SESSION_ID") or None
    return _grok_sid_from_ancestor_argv()


def _pid_alive(pid: Any) -> bool:
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return False
    if n <= 1:
        return False
    return Path(f"/proc/{n}").exists()


def _is_ghost_name(name: str | None) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return True
    if n in {"throttle", "guard-state"}:
        return True
    if n.startswith("anon-"):
        return True
    if n.startswith("check ") or "inbox once" in n:
        return True
    return False


def _clean_pane_title(title: str) -> str | None:
    t = (title or "").strip()
    t = re.sub(r"^[*✳●👁✋✅·]+\s*", "", t)
    t = re.sub(r"\s+\(\d+\)\s*$", "", t)
    t = re.sub(r"\s+-\s+grok\s*$", "", t, flags=re.I)
    return _safe_display_name(t)


def _read_environ_keys(pid: int, keys: tuple[str, ...]) -> dict[str, str]:
    try:
        data = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for item in data.split(b"\0"):
        if b"=" not in item:
            continue
        k, _, v = item.partition(b"=")
        try:
            ks = k.decode()
        except UnicodeDecodeError:
            continue
        if ks in keys:
            out[ks] = v.decode(errors="replace")
    return out


def _child_pids(pid: int) -> list[int]:
    path = Path(f"/proc/{pid}/task/{pid}/children")
    try:
        return [int(x) for x in path.read_text().split() if x]
    except (OSError, ValueError):
        return []


def _session_from_pid_tree(root_pid: int, *, max_nodes: int = 24) -> tuple[str | None, str | None, int | None]:
    """Return (session_id, harness, pid_that_held_the_env) from a tmux pane tree."""
    try:
        start = int(root_pid)
    except (TypeError, ValueError):
        return None, None, None
    seen: set[int] = set()
    queue = [start]
    while queue and len(seen) < max_nodes:
        pid = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        env = _read_environ_keys(pid, _SID_ENV_KEYS)
        if env.get("GROK_SESSION_ID"):
            return env["GROK_SESSION_ID"], "grok", pid
        if env.get("CLAUDE_CODE_SESSION_ID"):
            return env["CLAUDE_CODE_SESSION_ID"], "claude", pid
        if env.get("CLAUDE_SESSION_ID"):
            return env["CLAUDE_SESSION_ID"], "claude", pid
        queue.extend(_child_pids(pid))
    return None, None, None


def _tmux_enabled() -> bool:
    return os.environ.get("PEER_BUS_TMUX", "1").lower() not in {"0", "false", "no", "off"}


def _tmux_seats(*, force: bool = False) -> list[dict[str, Any]]:
    """Live tmux panes in the agents group, keyed later by session id.

    Grouped tmux sessions share pane ids; we dedupe on pane_id.
    """
    global _TMUX_CACHE
    now = time.monotonic()
    if not force and _TMUX_CACHE is not None and (now - _TMUX_CACHE[0]) < _TMUX_CACHE_TTL:
        return _TMUX_CACHE[1]
    if not _tmux_enabled():
        _TMUX_CACHE = (now, [])
        return []
    target = os.environ.get("PEER_BUS_TMUX_TARGET") or os.environ.get("AGENTS_TMUX_SESSION") or "agents"
    try:
        raw = subprocess.check_output(
            [
                "tmux",
                "list-panes",
                "-t",
                target,
                "-a",
                "-F",
                "#{pane_id}\t#{pane_pid}\t#{pane_title}\t#{pane_current_command}",
            ],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        _TMUX_CACHE = (now, [])
        return []
    by_pane: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pane_id, pid_s, title = parts[0], parts[1], parts[2]
        cmd = parts[3] if len(parts) > 3 else ""
        if pane_id in by_pane:
            continue
        name = _clean_pane_title(title)
        try:
            pane_pid = int(pid_s)
        except ValueError:
            continue
        sid, harness, agent_pid = _session_from_pid_tree(pane_pid)
        if not sid or not name:
            continue
        by_pane[pane_id] = {
            "pane_id": pane_id,
            "pane_pid": pane_pid,
            "agent_pid": agent_pid,
            "name": name,
            "session_id": sid,
            "harness": harness or "unknown",
            "cmd": cmd,
        }
    rows = list(by_pane.values())
    _TMUX_CACHE = (now, rows)
    return rows


def _tmux_seat_for_sid(sid: str | None) -> dict[str, Any] | None:
    if not sid:
        return None
    for row in _tmux_seats():
        if row.get("session_id") == sid:
            return row
    return None


def _herdr_enabled() -> bool:
    return os.environ.get("PEER_BUS_HERDR", "1").lower() not in {"0", "false", "no", "off"}


def _herdr_session_name() -> str:
    return (
        os.environ.get("PEER_BUS_HERDR_SESSION")
        or os.environ.get("AGENTS_HERDR_SESSION")
        or "agents"
    )


def _herdr_bin() -> str | None:
    """Locate herdr. Claude MCP often has PATH=/usr/bin:/bin, so which() misses ~/.local/bin."""
    raw = os.environ.get("PEER_BUS_HERDR_BIN")
    if raw:
        p = Path(raw).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    found = shutil.which("herdr")
    if found:
        return found
    for cand in (Path.home() / ".local/bin/herdr", Path("/usr/local/bin/herdr")):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _herdr_cmd(*args: str) -> dict[str, Any] | None:
    """Run `herdr --session <name> …`; return parsed JSON or None."""
    binary = _herdr_bin()
    if not binary:
        return None
    argv = [binary, "--session", _herdr_session_name(), *args]
    try:
        raw = subprocess.check_output(
            argv,
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_herdr_agents(
    payload: Any, labels: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Turn `herdr agent list` JSON into seat dicts. Isolated for tests.

    `claude attach` keeps the shell window title, so the pane *label* is the
    seat name when the title is still `user@host:cwd`.
    """
    if not isinstance(payload, dict):
        return []
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    agents = result.get("agents") if isinstance(result, dict) else None
    if not isinstance(agents, list):
        return []
    out: list[dict[str, Any]] = []
    labels = labels or {}
    for item in agents:
        if not isinstance(item, dict):
            continue
        pane_id = str(item.get("pane_id") or "")
        raw_name = (
            labels.get(pane_id)
            or item.get("label")
            or item.get("terminal_title_stripped")
            or item.get("terminal_title")
            or ""
        )
        name = _clean_pane_title(str(raw_name))
        if not name:
            continue
        sess = item.get("agent_session") if isinstance(item.get("agent_session"), dict) else {}
        sid = sess.get("value") or sess.get("id")
        kind = str(item.get("agent") or "").lower()
        if kind == "grok":
            harness = "grok"
        elif "claude" in kind or kind in {"", "unknown"}:
            harness = "claude"
        else:
            harness = kind
        out.append(
            {
                "name": name,
                "session_id": str(sid) if sid else None,
                "harness": harness,
                "pane_id": item.get("pane_id"),
                "cwd": item.get("cwd") or item.get("foreground_cwd"),
            }
        )
    return out


def _sid_from_herdr_process_info(payload: Any) -> tuple[str | None, str | None, int | None]:
    """Best-effort session id from `herdr pane process-info` (attach has no agent_session)."""
    if not isinstance(payload, dict):
        return None, None, None
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    info = result.get("process_info") if isinstance(result, dict) else None
    if not isinstance(info, dict):
        return None, None, None
    procs = info.get("foreground_processes")
    if not isinstance(procs, list):
        procs = []
    pids: list[int] = []
    for proc in procs:
        if not isinstance(proc, dict):
            continue
        argv = proc.get("argv") if isinstance(proc.get("argv"), list) else []
        for i, arg in enumerate(argv):
            if arg in ("--resume", "--session-id", "attach") and i + 1 < len(argv):
                cand = str(argv[i + 1])
                if len(cand) >= 8:
                    kind = str(proc.get("name") or "")
                    harness = "grok" if "grok" in kind else "claude"
                    pid = proc.get("pid")
                    try:
                        pid_i = int(pid) if pid is not None else None
                    except (TypeError, ValueError):
                        pid_i = None
                    if arg == "attach":
                        for job in _claude_bg_jobs():
                            js = str(job.get("session_id") or "")
                            if js == cand or js.startswith(cand) or cand.startswith(js[:8]):
                                return js, "claude", pid_i
                    return cand, harness, pid_i
        pid = proc.get("pid")
        try:
            pids.append(int(pid))
        except (TypeError, ValueError):
            pass
    shell = info.get("shell_pid")
    try:
        if shell is not None:
            pids.append(int(shell))
    except (TypeError, ValueError):
        pass
    for pid in pids:
        sid, harness, agent_pid = _session_from_pid_tree(pid)
        if sid:
            return sid, harness, agent_pid
    return None, None, None


def _parse_claude_bg_jobs(payload: Any) -> list[dict[str, Any]]:
    """`claude agents --json` rows we can match to a Herdr pane."""
    if isinstance(payload, dict):
        rows = payload.get("agents") or payload.get("jobs") or payload.get("result")
        if isinstance(rows, dict):
            rows = rows.get("agents")
        payload = rows
    if not isinstance(payload, list):
        return []
    out: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        sid = item.get("sessionId") or item.get("session_id")
        if not sid and item.get("id") and len(str(item.get("id"))) >= 8:
            sid = item.get("id")
        name = _clean_pane_title(str(item.get("name") or ""))
        if not sid or not name:
            continue
        pid = item.get("pid")
        try:
            pid_i = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid_i = None
        out.append({"name": name, "session_id": str(sid), "pid": pid_i, "kind": item.get("kind")})
    return out


def _claude_bg_jobs(*, force: bool = False) -> list[dict[str, Any]]:
    global _CLAUDE_BG_CACHE
    now = time.monotonic()
    if not force and _CLAUDE_BG_CACHE is not None and (now - _CLAUDE_BG_CACHE[0]) < _CLAUDE_BG_CACHE_TTL:
        return _CLAUDE_BG_CACHE[1]
    try:
        raw = subprocess.check_output(
            ["claude", "agents", "--json"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(raw)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        data = []
    rows = _parse_claude_bg_jobs(data)
    _CLAUDE_BG_CACHE = (now, rows)
    return rows


def _sid_from_claude_bg_job(name: str, pid: int | None = None) -> str | None:
    """Match a Herdr pane running `claude agents` / attach to the background job."""
    want = _clean_pane_title(name) or name
    hits = [j for j in _claude_bg_jobs() if j.get("name") == want]
    if not hits:
        return None
    if pid is not None:
        for job in hits:
            if job.get("pid") == pid:
                return str(job["session_id"])
    live = [j for j in hits if j.get("pid")]
    pick = live[-1] if live else hits[-1]
    return str(pick["session_id"]) if pick else None


def _herdr_seats(*, force: bool = False) -> list[dict[str, Any]]:
    """Live Herdr agent panes in PEER_BUS_HERDR_SESSION (default `agents`)."""
    global _HERDR_CACHE
    now = time.monotonic()
    if not force and _HERDR_CACHE is not None and (now - _HERDR_CACHE[0]) < _HERDR_CACHE_TTL:
        return _HERDR_CACHE[1]
    if not _herdr_enabled():
        _HERDR_CACHE = (now, [])
        return []
    payload = _herdr_cmd("agent", "list")
    labels: dict[str, str] = {}
    pane_payload = _herdr_cmd("pane", "list")
    if isinstance(pane_payload, dict):
        result = pane_payload.get("result") if isinstance(pane_payload.get("result"), dict) else {}
        panes = result.get("panes") if isinstance(result, dict) else None
        if isinstance(panes, list):
            for pane in panes:
                if not isinstance(pane, dict):
                    continue
                pid = pane.get("pane_id")
                lab = pane.get("label")
                if pid and lab:
                    labels[str(pid)] = str(lab)
    seats = _parse_herdr_agents(payload, labels=labels) if payload else []
    filled: list[dict[str, Any]] = []
    for seat in seats:
        if seat.get("session_id"):
            filled.append(seat)
            continue
        pane_id = seat.get("pane_id")
        if not pane_id:
            continue
        info = _herdr_cmd("pane", "process-info", "--pane", str(pane_id))
        sid, harness, _pid = _sid_from_herdr_process_info(info)
        fg_pid = None
        if isinstance(info, dict):
            procs = ((info.get("result") or {}).get("process_info") or {}).get("foreground_processes")
            if isinstance(procs, list) and procs and isinstance(procs[0], dict):
                try:
                    fg_pid = int(procs[0].get("pid"))
                except (TypeError, ValueError):
                    fg_pid = None
        if not sid:
            sid = _sid_from_claude_bg_job(str(seat.get("name") or ""), fg_pid)
            if sid:
                harness = harness or "claude"
        if not sid:
            continue
        seat = dict(seat)
        seat["session_id"] = sid
        if harness:
            seat["harness"] = harness
        filled.append(seat)
    _HERDR_CACHE = (now, filled)
    return filled


def _herdr_seat_for_sid(sid: str | None) -> dict[str, Any] | None:
    if not sid:
        return None
    for row in _herdr_seats():
        if row.get("session_id") == sid:
            return row
    return None


def _usage_name_for_sid(sid: str | None) -> str | None:
    if not sid or USAGE_DIR is None or not USAGE_DIR.is_dir():
        return None
    path = USAGE_DIR / f"{sid}.json"
    if path.is_symlink() or not path.is_file():
        return None
    data = _read_json(path)
    if not data:
        return None
    return _safe_display_name(str(data.get("name") or data.get("session_name") or ""))


def detect_self(display_name: str | None = None) -> dict[str, Any]:
    """Resolve this agent's identity.

    Inbox key is bound to session id when present. display_name / --as only changes
    the human-readable name (and from.name on send), unless PEER_BUS_TRUST_NAME_KEYS=1.
    """
    sid = _session_id()
    harness = os.environ.get("PEER_BUS_HARNESS")
    title = None
    env_name = os.environ.get("PEER_BUS_SELF") or os.environ.get("PEER_BUS_NAME")
    grok_sid = os.environ.get("GROK_SESSION_ID")

    # Grok summary titles only when this process is actually a Grok session.
    # Walking ~/.grok/sessions for a Claude uuid used to set harness=grok and
    # name=grok-<sid>.
    if sid and grok_sid and sid == grok_sid and SESSIONS.is_dir():
        harness = harness or "grok"
        for group in SESSIONS.glob("*"):
            summary = group / sid / "summary.json"
            data = _read_json(summary)
            if data:
                title = data.get("generated_title") or data.get("session_summary") or None
                if data.get("title_is_manual") and data.get("generated_title"):
                    title = data["generated_title"]
                break

    if _claude_session_id() and not harness:
        harness = "claude"

    seat = _tmux_seat_for_sid(sid) or _herdr_seat_for_sid(sid)
    if seat:
        harness = harness or seat.get("harness")

    name = (
        _safe_display_name(display_name)
        or _safe_display_name(env_name)
        or _safe_display_name(seat.get("name") if seat else None)
        or _usage_name_for_sid(sid)
        or _safe_display_name(title)
    )
    if not name:
        if sid:
            prefix = "claude" if harness == "claude" else "grok"
            name = f"{prefix}-{sid[:8]}"
        else:
            name = f"anon-{uuid.uuid4().hex[:8]}"

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
            if _claude_session_id()
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
        pid = meta.get("pid")
        out.append(
            {
                "key": key,
                "name": name,
                "ref": sid[:6],
                "session_id": sid,
                "harness": "grok",
                "state": "live" if _pid_alive(pid) else "stale",
                "cwd": meta.get("cwd"),
                "pid": pid,
                "opened_at": meta.get("opened_at"),
                "last_active_at": updated,
                "address": f"{name} [{sid[:6]}]",
                "source": "grok",
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
        if path.name.startswith(".") or path.stem in _USAGE_SKIP_STEMS:
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
                "context": data.get("context"),
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
        pid = data.get("pid")
        alive = _pid_alive(pid)
        ghost = _is_ghost_name(str(name))
        out.append(
            {
                "key": key,
                "name": name,
                "ref": str(sid)[:6] if sid else key[:6],
                "session_id": sid,
                "harness": data.get("harness") or "unknown",
                "state": "live" if alive and not ghost else "stale",
                "cwd": data.get("cwd"),
                "pid": pid,
                "age_min": round(age_min, 1),
                "address": f"{name} [{str(sid)[:6]}]" if sid else name,
                "source": "registry",
            }
        )
    return out


def _row_name_score(row: dict[str, Any]) -> int:
    n = str(row.get("name") or "")
    score = 0
    if row.get("source") == "tmux":
        score += 4
    if n and not n.startswith("grok-") and not n.startswith("claude-"):
        score += 2
    if row.get("harness") == "claude":
        score += 1
    return score


def _collapse_same_pid(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One process → one seat. Grok often registers a wrapper sid on the same pid."""
    by_pid: dict[int, dict[str, Any]] = {}
    no_pid: list[dict[str, Any]] = []
    for row in rows:
        try:
            pid = int(row["pid"]) if row.get("pid") is not None else 0
        except (TypeError, ValueError):
            pid = 0
        if pid <= 1:
            no_pid.append(row)
            continue
        prev = by_pid.get(pid)
        if prev is None or _row_name_score(row) > _row_name_score(prev):
            by_pid[pid] = row
    return list(by_pid.values()) + no_pid


def _overlay_name(row: dict[str, Any], name: str | None) -> None:
    cleaned = _safe_display_name(name)
    if not cleaned:
        return
    sid = str(row.get("session_id") or "")
    row["name"] = cleaned
    row["address"] = f"{cleaned} [{sid[:6]}]" if sid else cleaned


def _fmt_pct(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    try:
        n = float(raw)
    except (TypeError, ValueError):
        s = str(raw).strip()
        return s or None
    if n != n:  # NaN
        return None
    return str(int(round(n)))


def _overlay_context(row: dict[str, Any], usage: dict[str, Any]) -> None:
    """Per-seat discriminator. Mark quiet snapshots the way fleet-roster uses ~."""
    ctx = usage.get("context")
    if ctx is None or ctx == "":
        return
    text = str(ctx)
    age = usage.get("age_min")
    try:
        stale = float(age) > _CONTEXT_STALE_MIN
    except (TypeError, ValueError):
        stale = False
    if stale and not text.endswith("~"):
        text = f"{text}~"
    row["context"] = text


def pool_usage(now: float | None = None) -> dict[str, Any] | None:
    """Account-wide 5h pool from the newest unexpired usage snapshot.

    The 5-hour figure is one shared pool, not a per-seat cost. Samples whose
    `five_hour_resets_at` is in the past are void (same predicate as
    usage-guard `pool_samples()`).
    """
    usage_dir = USAGE_DIR
    if usage_dir is None or not usage_dir.is_dir():
        return None
    now_f = time.time() if now is None else now
    best: dict[str, Any] | None = None
    best_ts = ""
    for path in usage_dir.glob("*.json"):
        if path.name.startswith(".") or path.stem in _USAGE_SKIP_STEMS:
            continue
        if path.is_symlink():
            continue
        data = _read_json(path)
        if not data:
            continue
        reset = data.get("five_hour_resets_at")
        try:
            reset_f = float(reset)
        except (TypeError, ValueError):
            continue
        if reset_f <= now_f:
            continue
        ts = str(data.get("ts") or "")
        if ts < best_ts:
            continue
        best_ts = ts
        best = {
            "five_hour": _fmt_pct(data.get("five_hour")),
            "resets_at": reset_f,
            "ts": ts,
            "source_name": data.get("name") or data.get("session_name"),
            "source_session": data.get("session") or data.get("session_id") or path.stem,
        }
    return best


def roster(include_stale: bool = False) -> dict[str, Any]:
    """CLI/MCP view: pool header once, then agent rows (no per-row five_hour)."""
    return {"pool": pool_usage(), "agents": list_agents(include_stale=include_stale)}


def list_agents(include_stale: bool = False) -> list[dict[str, Any]]:
    """Live roster: herdr agents, then tmux pane, then grok pids, then usage overlay.

    Registry heartbeats and stale usage snaps are not live on their own.
    A work tracker is not presence.
    """
    _ensure_dirs()
    by_sid: dict[str, dict[str, Any]] = {}

    for seat in _herdr_seats():
        sid = str(seat.get("session_id") or "")
        if not sid:
            continue
        by_sid[sid] = {
            "key": _safe_key(sid),
            "name": seat["name"],
            "ref": sid[:6],
            "session_id": sid,
            "harness": seat.get("harness") or "unknown",
            "state": "live",
            "pid": seat.get("agent_pid") or seat.get("pane_pid"),
            "cwd": seat.get("cwd"),
            "address": f"{seat['name']} [{sid[:6]}]",
            "source": "herdr",
            "pane_id": seat.get("pane_id"),
        }

    for seat in _tmux_seats():
        sid = str(seat.get("session_id") or "")
        if not sid:
            continue
        if sid in by_sid:
            row = by_sid[sid]
            row.setdefault("pane_id", seat.get("pane_id"))
            continue
        by_sid[sid] = {
            "key": _safe_key(sid),
            "name": seat["name"],
            "ref": sid[:6],
            "session_id": sid,
            "harness": seat.get("harness") or "unknown",
            "state": "live",
            "pid": seat.get("agent_pid") or seat.get("pane_pid"),
            "cwd": None,
            "address": f"{seat['name']} [{sid[:6]}]",
            "source": "tmux",
            "pane_id": seat.get("pane_id"),
        }

    for g in _grok_agents():
        sid = str(g.get("session_id") or "")
        if not sid:
            continue
        alive = _pid_alive(g.get("pid"))
        if sid in by_sid:
            row = by_sid[sid]
            row["pid"] = g.get("pid") or row.get("pid")
            row["cwd"] = g.get("cwd") or row.get("cwd")
            if g.get("opened_at"):
                row["opened_at"] = g["opened_at"]
            if alive:
                row["state"] = "live"
            continue
        g = dict(g)
        g["state"] = "live" if alive else "stale"
        g["key"] = _safe_key(sid)
        by_sid[sid] = g

    for u in _usage_agents():
        sid = str(u.get("session_id") or "")
        if not sid or sid in _USAGE_SKIP_STEMS or _is_ghost_name(str(u.get("name") or "")):
            continue
        fresh = u.get("state") == "live"
        if sid in by_sid:
            row = by_sid[sid]
            if u.get("model"):
                row["model"] = u["model"]
            _overlay_context(row, u)
            row["age_min"] = u.get("age_min")
            if row.get("source") not in _AUTHORITY_SOURCES:
                if fresh:
                    row["state"] = "live"
                current = str(row.get("name") or "")
                if current.startswith("grok-") or current.startswith("claude-"):
                    _overlay_name(row, str(u.get("name") or ""))
            continue
        u = dict(u)
        u["key"] = _safe_key(sid)
        _overlay_context(u, u)
        by_sid[sid] = u

    for r in _registry_agents():
        sid = str(r.get("session_id") or "")
        if not sid:
            continue
        if sid in by_sid:
            continue
        r = dict(r)
        r["key"] = _safe_key(str(r.get("key") or sid))
        # MCP stdio heartbeats keep a python pid alive; that is not a seat.
        r["state"] = "stale"
        by_sid[sid] = r

    merged = _collapse_same_pid(list(by_sid.values()))
    if not include_stale:
        merged = [
            r
            for r in merged
            if r.get("state") != "stale" and not _is_ghost_name(str(r.get("name") or ""))
        ]

    names: dict[str, int] = {}
    for row in merged:
        names[row["name"]] = names.get(row["name"], 0) + 1
    sids = [str(r.get("session_id") or "") for r in merged]

    def _unique_ref(sid: str) -> str:
        if not sid:
            return ""
        n = 6
        lower = [s.lower() for s in sids]
        while n <= len(sid):
            cand = sid[:n]
            hits = [s for s in lower if s.startswith(cand.lower())]
            if len(hits) <= 1:
                return cand
            n += 2
        return sid

    for row in merged:
        if names.get(row["name"], 0) > 1:
            row["name_collision"] = True
        row["key"] = _safe_key(str(row.get("key") or row.get("session_id") or row.get("name")))
        sid = str(row.get("session_id") or "")
        ref = _unique_ref(sid)
        row["ref"] = ref
        if sid and row.get("name"):
            row["address"] = f"{row['name']} [{ref}]"
    return merged


def flock(include_stale: bool = False) -> list[dict[str, Any]]:
    """Same view as list_agents. Name exists so the CLI/MCP can say flock."""
    return list_agents(include_stale=include_stale)


def resolve_recipient(
    to: str,
    agents: list[dict[str, Any]] | None = None,
    *,
    allow_stale: bool | None = None,
) -> dict[str, Any]:
    stale_ok = ALLOW_STALE_SEND if allow_stale is None else allow_stale
    raw = to.strip()
    # UUID refs may include hyphens once they grow past 8 hex chars
    # (`Name [01a08225-0]`). Hex-only character classes reject that printed address.
    m = re.match(r"^(.*?)\s*\[([0-9a-fA-F-]{4,})\]\s*$", raw)
    name_part, ref_part = (m.group(1).strip(), m.group(2).lower()) if m else (raw, None)

    if agents is not None:
        pool = agents
    else:
        pool = list_agents(include_stale=True)

    def _consider(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for a in rows:
            sid = str(a.get("session_id") or "")
            key = str(a.get("key") or "")
            if raw == sid or raw == key or raw.lower() == sid.lower():
                return [a]
            if ref_part and sid.lower().startswith(ref_part):
                if _slug(name_part) == key or name_part == a.get("name") or not name_part:
                    found.append(a)
                continue
            if name_part == a.get("name") or _slug(name_part) == key:
                found.append(a)
        return found

    live_pool = [a for a in pool if a.get("state") != "stale"] if not stale_ok else pool
    matches = _consider(live_pool)

    if not matches:
        if not stale_ok:
            stale_hit = False
            for a in pool:
                if a.get("state") != "stale":
                    continue
                sid = str(a.get("session_id") or "")
                if raw == sid or name_part == a.get("name") or (
                    ref_part and sid.lower().startswith(ref_part)
                ):
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
            opts = ", ".join(a.get("address") or a["name"] for a in refined)
            raise ValueError(f"ambiguous ref {raw!r}; disambiguate: {opts}")
    else:
        chosen = matches[0]

    chosen = dict(chosen)
    chosen["key"] = _safe_key(str(chosen.get("session_id") or chosen.get("key")))
    return chosen


_TO_ALIASES = ("to", "recipient", "address")
_BODY_ALIASES = ("body", "message", "text")
_TO_LINE = re.compile(r"^(?:TO[:\|]?|@to|to:)\s+(.+)$", re.IGNORECASE)


def coerce_send_args(args: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Recover MCP send fields when the host drops `to` or uses aliases.

    Grok has dropped required `to` when `display_name` / `summary` rode along
    with a long body. Accept recipient/address and a first-line TO prefix.
    """
    args = args or {}
    to: str | None = None
    for key in _TO_ALIASES:
        raw = args.get(key)
        if isinstance(raw, str) and raw.strip():
            to = raw.strip()
            break
    body: str | None = None
    for key in _BODY_ALIASES:
        raw = args.get(key)
        if isinstance(raw, str):
            body = raw
            break
    if not to and isinstance(body, str):
        first, _, rest = body.partition("\n")
        match = _TO_LINE.match(first.rstrip("\r"))
        if match:
            to = match.group(1).strip() or None
            body = rest
    return to, body


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

    envelope["_path"] = str(path)
    wake = _try_wake(envelope, recipient)
    envelope.pop("_path", None)

    return {
        "ok": True,
        "accepted": True,
        "delivered_to_reader": False,
        "msg_id": msg_id,
        "path": str(path),
        "to": envelope["to"],
        "from": envelope["from"],
        "warning": recipient.get("warning"),
        "wake": wake,
        "note": "acceptance only — peer must recv/drain; success≠read",
    }


def receive_messages(
    self_info: dict[str, Any] | None = None,
    *,
    unread_only: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    me = self_info or detect_self()
    try:
        dest = _inbox_dir(me["key"], create=False)
    except ValueError:
        return []
    if not dest.is_dir():
        return []
    ranked: list[tuple[str, str, dict[str, Any]]] = []
    for path in dest.glob("*.json"):
        if path.is_symlink():
            continue
        data = _read_json(path)
        if not data:
            continue
        if unread_only and data.get("read"):
            continue
        data = dict(data)
        data["_path"] = str(path)
        data["untrusted"] = True
        body = data.get("body") if isinstance(data.get("body"), str) else ""
        data["body_for_model"] = (
            "<<<UNTRUSTED_PEER_MESSAGE>>>\n" + body + "\n<<<END_UNTRUSTED_PEER_MESSAGE>>>"
        )
        ranked.append((str(data.get("ts") or ""), path.name, data))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    cap = max(1, min(limit, 100))
    return [item[2] for item in ranked[:cap]]


def _live_keep_ids() -> set[str]:
    keep: set[str] = set()
    for agent in list_agents(include_stale=False):
        sid = str(agent.get("session_id") or "")
        key = str(agent.get("key") or "")
        if sid:
            keep.add(sid)
            keep.add(_safe_key(sid))
        if key:
            keep.add(key)
            keep.add(_safe_key(key))
    return keep


def prune_stale(*, apply: bool = False) -> dict[str, Any]:
    """Remove dead registry/usage/empty-inbox rows. Dry-run unless apply=True.

    Keeps live flock sids, registry rows whose pid is still alive (wrapper
    grok), usage skip stems, and inboxes that still have unread files.
    """
    _ensure_dirs()
    keep = _live_keep_ids()
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    if REGISTRY.is_dir():
        for path in sorted(REGISTRY.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                continue
            data = _read_json(path) or {}
            sid = str(data.get("session_id") or path.stem)
            key = _safe_key(path.stem)
            name = str(data.get("name") or "")
            if sid in keep or key in keep:
                continue
            if _pid_alive(data.get("pid")):
                continue
            reason = "ghost" if _is_ghost_name(name) else "dead-pid"
            candidates.append(
                {"kind": "registry", "path": str(path), "name": name, "reason": reason}
            )

    if USAGE_DIR is not None and USAGE_DIR.is_dir():
        for path in sorted(USAGE_DIR.glob("*.json")):
            if path.name.startswith(".") or path.stem in _USAGE_SKIP_STEMS:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            data = _read_json(path) or {}
            sid = str(data.get("session") or data.get("session_id") or path.stem)
            name = str(data.get("name") or data.get("session_name") or "")
            if sid in keep or path.stem in keep or _safe_key(sid) in keep:
                continue
            candidates.append(
                {
                    "kind": "usage",
                    "path": str(path),
                    "name": name,
                    "reason": "not-live",
                }
            )

    if INBOX.is_dir():
        for dest in sorted(INBOX.iterdir()):
            if dest.is_symlink() or not dest.is_dir():
                continue
            key = dest.name
            if key in keep or _safe_key(key) in keep:
                continue
            unread = unread_count_for_key(key)
            item = {"kind": "inbox", "path": str(dest), "name": key, "reason": "empty-dead"}
            if unread:
                item["reason"] = f"kept-unread:{unread}"
                skipped.append(item)
                continue
            candidates.append(item)

    removed: list[dict[str, Any]] = []
    if apply:
        for item in candidates:
            path = Path(item["path"])
            if item["kind"] == "inbox":
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
            removed.append(item)
    return {
        "ok": True,
        "apply": apply,
        "count": len(candidates if not apply else removed),
        "candidates": candidates,
        "removed": removed,
        "skipped": skipped,
    }


def unread_count_for_key(key: str) -> int:
    """Unread json files in one inbox. Does not heartbeat or read bodies."""
    try:
        dest = _inbox_dir(key, create=False)
    except ValueError:
        return 0
    if not dest.is_dir():
        return 0
    n = 0
    for path in dest.glob("*.json"):
        if path.is_symlink() or not path.is_file():
            continue
        n += 1
    return n


def unread_count(self_info: dict[str, Any] | None = None) -> int:
    """Unread inbox files for this session. Does not heartbeat or read bodies."""
    me = self_info or detect_self()
    return unread_count_for_key(str(me.get("key") or ""))


def flock_unread_total() -> int:
    """Unread files across live seats' inboxes (Herdr tab-bar / operator glance)."""
    n = 0
    seen: set[str] = set()
    for agent in list_agents(False):
        key = str(agent.get("key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        n += unread_count_for_key(key)
    return n


def _watch_deadline(max_runtime: float | None) -> float | None:
    raw = max_runtime
    if raw is None:
        env = os.environ.get("PEER_BUS_WATCH_MAX_RUNTIME")
        if env:
            try:
                raw = float(env)
            except ValueError:
                raw = None
    if raw is None or float(raw) <= 0:
        return None
    return time.monotonic() + float(raw)


def _watch_remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return deadline - time.monotonic()


def _inotify_init(path: Path) -> int | None:
    """Linux inotify fd on path, or None to fall back to poll. PEER_BUS_WATCH=poll forces poll."""
    if os.environ.get("PEER_BUS_WATCH", "").lower() in {"poll", "sleep"}:
        return None
    if not sys.platform.startswith("linux"):
        return None
    try:
        import ctypes
        import ctypes.util
        import fcntl

        libname = ctypes.util.find_library("c")
        if not libname:
            return None
        libc = ctypes.CDLL(libname, use_errno=True)
        libc.inotify_init.restype = ctypes.c_int
        fd = int(libc.inotify_init())
        if fd < 0:
            return None
        flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        libc.inotify_add_watch.restype = ctypes.c_int
        # CREATE | MOVED_TO | CLOSE_WRITE
        mask = ctypes.c_uint32(0x100 | 0x80 | 0x8)
        wd = int(libc.inotify_add_watch(fd, os.fsencode(str(path)), mask))
        if wd < 0:
            os.close(fd)
            return None
        return fd
    except Exception:
        return None


def _inotify_wait(fd: int, timeout: float | None) -> bool:
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return False
    try:
        os.read(fd, 4096)
    except BlockingIOError:
        pass
    except OSError:
        return False
    return True


def _emit_unread(me: dict[str, Any], seen: set[str]) -> int:
    """Print msg_id<TAB>from.address for newly seen unread mail. Returns how many lines."""
    n = 0
    for m in receive_messages(me, unread_only=True, limit=100):
        mid = m.get("msg_id")
        if not isinstance(mid, str) or mid in seen:
            continue
        seen.add(mid)
        fr = m.get("from") if isinstance(m.get("from"), dict) else {}
        addr = fr.get("address") or fr.get("name") or ""
        print(f"{mid}\t{addr}", flush=True)
        n += 1
    return n


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



def _cmd_version(args: argparse.Namespace) -> int:
    print(PEER_BUS_VERSION)
    return 0


def _cmd_whoami(args: argparse.Namespace) -> int:
    me = detect_self(args.as_name)
    heartbeat(me)
    print(json.dumps(me, indent=2))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    view = roster(include_stale=args.all)
    if args.json:
        print(json.dumps(view, indent=2))
        return 0
    pool = view.get("pool") if isinstance(view.get("pool"), dict) else None
    pct = pool.get("five_hour") if pool else None
    if pct:
        print(f"POOL 5h {pct}%  (account-wide; not per seat)")
    else:
        print("POOL 5h —  (no unexpired sample)")
    agents = view.get("agents") or []
    if not agents:
        print("no agents found")
        return 0
    print(f"{'NAME':<32} {'REF':<8} {'HARNESS':<8} {'STATE':<6} {'CTX':<6} ADDRESS")
    for a in agents:
        flag = " *" if a.get("name_collision") else ""
        print(
            f"{(a.get('name') or '')[:32]:<32} {(a.get('ref') or ''):<8} "
            f"{(a.get('harness') or ''):<8} {(a.get('state') or ''):<6} "
            f"{(a.get('context') or '—'):<6} {a.get('address')}{flag}"
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


def _cmd_mail(args: argparse.Namespace) -> int:
    if getattr(args, "flock", False):
        n = flock_unread_total()
        if args.json:
            print(json.dumps({"count": n, "scope": "flock"}))
            return 0
        print(n)
        return 0
    me = detect_self(args.as_name if TRUST_NAME_KEYS else None)
    if args.as_name and not TRUST_NAME_KEYS:
        me["name"] = args.as_name
    n = unread_count(me)
    if args.json:
        print(json.dumps({"count": n, "key": me["key"]}))
        return 0
    print(n)
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    result = prune_stale(apply=bool(args.apply))
    print(json.dumps(result, indent=2))
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    """Emit one line per new unread message: msg_id<TAB>from.address.

    Linux: inotify on the inbox dir (PEER_BUS_WATCH=poll to force sleep backoff).
    """
    # Watches launched without --as: put the detected display name on argv
    # so process listings match the seat, not an untitled python.
    if not args.as_name:
        guessed = str(detect_self().get("name") or "")
        if guessed and not guessed.startswith(("grok-", "claude-", "anon-")):
            os.execvp(sys.argv[0], [*sys.argv, "--as", guessed])
    me = detect_self(args.as_name if TRUST_NAME_KEYS else None)
    if args.as_name and not TRUST_NAME_KEYS:
        me["name"] = args.as_name
    interval = max(0.5, float(args.interval))
    max_interval = max(interval, float(args.max_interval))
    delay = interval
    seen: set[str] = set()
    dest = _inbox_dir(me["key"], create=True)
    deadline = _watch_deadline(getattr(args, "max_runtime", None))
    _emit_unread(me, seen)
    if args.once:
        return 0
    fd = _inotify_init(dest)
    if fd is not None:
        _emit_unread(me, seen)  # race between first drain and add_watch
        try:
            while True:
                timeout = _watch_remaining(deadline)
                if timeout is not None and timeout <= 0:
                    return 0
                if not _inotify_wait(fd, timeout):
                    if deadline is not None and time.monotonic() >= deadline:
                        return 0
                    continue
                _emit_unread(me, seen)
        except KeyboardInterrupt:
            return 0
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        return 0
    try:
        while True:
            n = _emit_unread(me, seen)
            if n:
                delay = interval
            else:
                delay = min(max_interval, delay * 1.5)
            timeout = _watch_remaining(deadline)
            if timeout is not None and timeout <= 0:
                return 0
            time.sleep(delay if timeout is None else min(delay, timeout))
            if deadline is not None and time.monotonic() >= deadline:
                return 0
    except KeyboardInterrupt:
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

    p = sub.add_parser("version", help="print peer-bus version")
    p.set_defaults(func=_cmd_version)

    p = sub.add_parser("whoami")
    _add_as(p)
    p.set_defaults(func=_cmd_whoami)

    p = sub.add_parser("list")
    _add_as(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true", help="include stale")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("flock", help="live roster (same as list)")
    _add_as(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true", help="include stale")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("mail", help="unread inbox count for this session (no bodies)")
    _add_as(p)
    p.add_argument("--count", action="store_true", help="print the integer (default)")
    p.add_argument("--flock", action="store_true", help="sum unread across live seats")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_mail)

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

    p = sub.add_parser(
        "prune",
        help="remove dead registry/usage/empty-inbox rows (dry-run unless --apply)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="delete candidates; default is dry-run JSON",
    )
    p.set_defaults(func=_cmd_prune)

    p = sub.add_parser(
        "watch",
        help="watch unread inbox; print msg_id and from.address per new message (backoff when idle)",
    )
    _add_as(p)
    p.add_argument("--interval", type=float, default=2.0, help="base poll seconds (default 2)")
    p.add_argument(
        "--max-interval",
        type=float,
        default=30.0,
        help="idle backoff cap seconds (default 30)",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="scan once and exit (still prints only new unread lines)",
    )
    p.add_argument(
        "--max-runtime",
        type=float,
        default=None,
        help="exit 0 after this many seconds (also PEER_BUS_WATCH_MAX_RUNTIME)",
    )
    p.set_defaults(func=_cmd_watch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
