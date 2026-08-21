#!/usr/bin/env python3
"""Minimal stdio MCP server for peer-bus (no external deps).

Security: inbox key is session-bound. `display_name` only changes the human-readable
from.name — it cannot switch whose inbox is read or written.
"""
from __future__ import annotations

import json
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import peer_bus  # noqa: E402

SERVER_INFO = {"name": "peer-bus", "version": "0.2.0"}
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "list_agents",
        "description": "List live peer agents. Name collisions are flagged — send with name [ref].",
        "inputSchema": {
            "type": "object",
            "properties": {"include_stale": {"type": "boolean", "default": False}},
        },
    },
    {
        "name": "send_message",
        "description": "Send to a peer inbox (acceptance only). display_name sets from.name only; inbox keys are session-bound.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "body": {"type": "string"},
                "summary": {"type": "string"},
                "display_name": {
                    "type": "string",
                    "description": "Optional from.name override; does not change sender inbox key",
                },
            },
            "required": ["to", "body"],
        },
    },
    {
        "name": "receive_messages",
        "description": "Drain THIS session's unread inbox (session-bound; no impersonation).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_read": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "ack_message",
        "description": "Ack a message in THIS session's inbox only.",
        "inputSchema": {
            "type": "object",
            "properties": {"msg_id": {"type": "string"}},
            "required": ["msg_id"],
        },
    },
    {
        "name": "whoami",
        "description": "Show this session's peer-bus identity (session-bound key) and heartbeat.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string"},
            },
        },
    },
    {
        "name": "heartbeat",
        "description": "Publish presence for THIS session.",
        "inputSchema": {
            "type": "object",
            "properties": {"display_name": {"type": "string"}},
        },
    },
]


def _result(obj: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(obj, indent=2)}]}


def _error(code: int, message: str, req_id: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    display = args.get("display_name")
    # Never accept legacy as_name for inbox switching
    if args.get("as_name"):
        return _result(
            {
                "ok": False,
                "error": "as_name is removed; use display_name for from.name only (inbox is session-bound)",
            }
        )

    me = peer_bus.detect_self(display)

    try:
        if name == "list_agents":
            return _result(peer_bus.list_agents(include_stale=bool(args.get("include_stale"))))
        if name == "send_message":
            return _result(
                peer_bus.send_message(
                    args["to"],
                    args["body"],
                    summary=args.get("summary"),
                    self_info=me,
                )
            )
        if name == "receive_messages":
            return _result(
                peer_bus.receive_messages(
                    me,
                    unread_only=not bool(args.get("include_read")),
                    limit=int(args.get("limit") or 50),
                )
            )
        if name == "ack_message":
            return _result(peer_bus.ack_message(args["msg_id"], me))
        if name == "whoami":
            peer_bus.heartbeat(me)
            return _result(me)
        if name == "heartbeat":
            return _result(peer_bus.heartbeat(me))
    except (KeyError, ValueError, OSError) as exc:
        return _result({"ok": False, "error": str(exc)})
    return _result({"ok": False, "error": f"unknown tool {name}"})


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _ok(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})
    if method == "tools/call":
        return _ok(req_id, call_tool(params.get("name"), params.get("arguments") or {}))
    if method == "ping":
        return _ok(req_id, {})
    if req_id is not None:
        return _error(-32601, f"method not found: {method}", req_id)
    return None


def main() -> None:
    peer_bus.heartbeat()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
