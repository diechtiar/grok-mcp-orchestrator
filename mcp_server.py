#!/usr/bin/env python3
"""Minimal stdio MCP server for peer-bus (no external deps).

Tools:
  list_agents, send_message, receive_messages, ack_message, whoami, heartbeat

Configure in ~/.grok/config.toml:

  [mcp_servers.peer-bus]
  command = "python3"
  args = ["/workspace/_shared/peer-bus/mcp_server.py"]
  env = { PEER_BUS_SELF = "Rick" }   # optional stable name
  enabled = true
"""
from __future__ import annotations

import json
import sys
from typing import Any

# Allow importing sibling module when launched by path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import peer_bus  # noqa: E402


SERVER_INFO = {"name": "peer-bus", "version": "0.1.0"}
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "list_agents",
        "description": "List live peer agents (Grok active sessions, Claude usage snapshots, peer-bus registry). Name collisions are flagged — send with name [ref].",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_stale": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "send_message",
        "description": "Send a message to a peer inbox. Returns acceptance only (ok≠read). Prefer to= address from list_agents; on reply use from.address of the latest received message.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "name | name [ref] | session_id | key"},
                "body": {"type": "string"},
                "summary": {"type": "string"},
                "as_name": {"type": "string", "description": "override sender identity"},
            },
            "required": ["to", "body"],
        },
    },
    {
        "name": "receive_messages",
        "description": "Drain this agent's unread peer-bus inbox (poll-based; call at turn start when orchestrating).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "as_name": {"type": "string"},
                "include_read": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "ack_message",
        "description": "Mark a received message as read and archive it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "msg_id": {"type": "string"},
                "as_name": {"type": "string"},
            },
            "required": ["msg_id"],
        },
    },
    {
        "name": "whoami",
        "description": "Show this agent's peer-bus identity and refresh heartbeat.",
        "inputSchema": {
            "type": "object",
            "properties": {"as_name": {"type": "string"}},
        },
    },
    {
        "name": "heartbeat",
        "description": "Publish presence to the peer-bus registry.",
        "inputSchema": {
            "type": "object",
            "properties": {"as_name": {"type": "string"}},
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
    as_name = args.get("as_name")
    me = peer_bus.detect_self(as_name) if as_name or True else peer_bus.detect_self()
    if as_name:
        me = peer_bus.detect_self(as_name)

    if name == "list_agents":
        return _result(peer_bus.list_agents(include_stale=bool(args.get("include_stale"))))
    if name == "send_message":
        try:
            return _result(
                peer_bus.send_message(
                    args["to"],
                    args["body"],
                    summary=args.get("summary"),
                    self_info=me,
                )
            )
        except (KeyError, ValueError) as exc:
            return _result({"ok": False, "error": str(exc)})
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
        name = params.get("name")
        arguments = params.get("arguments") or {}
        return _ok(req_id, call_tool(name, arguments))
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
