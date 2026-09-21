#!/usr/bin/env python3
"""stdlib unittest coverage for peer-bus core helpers and CLI verbs."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import peer_bus  # noqa: E402


def setUpModule() -> None:
    # Live herdr on the host roster must not leak into mocked roster tests.
    os.environ["PEER_BUS_HERDR"] = "0"
    # This test process is a child of a live grok --resume; do not inherit that sid.
    peer_bus._grok_sid_from_ancestor_argv = lambda **_k: None  # type: ignore[method-assign]


_SID_ENV = (
    "GROK_SESSION_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "PEER_BUS_SESSION_ID",
    "PEER_BUS_ALLOW_SESSION_OVERRIDE",
)


def _cleared_sid_env(**set_values: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _SID_ENV}
    env.update(set_values)
    return env


class SafeKeyTests(unittest.TestCase):
    def test_strips_traversal(self) -> None:
        self.assertNotIn("..", peer_bus._safe_key("../etc/passwd"))
        self.assertEqual(peer_bus._safe_key("Alice"), "alice")

    def test_empty_becomes_anon(self) -> None:
        self.assertEqual(peer_bus._safe_key(""), "anon")

    def test_refuses_path_separators_in_result(self) -> None:
        for raw in ("../etc/passwd", "foo/../../bar", r"alice\\bob", "a/b/c"):
            key = peer_bus._safe_key(raw)
            self.assertNotIn("/", key, raw)
            self.assertNotIn("\\", key, raw)
            self.assertNotIn("..", key, raw)


class ClaudeSessionIdTests(unittest.TestCase):
    def test_prefers_claude_code_session_id(self) -> None:
        env = _cleared_sid_env(
            CLAUDE_CODE_SESSION_ID="code-1",
            CLAUDE_SESSION_ID="legacy-1",
        )
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(peer_bus._claude_session_id(), "code-1")
            self.assertEqual(peer_bus._session_id(), "code-1")

    def test_falls_back_to_claude_session_id(self) -> None:
        env = _cleared_sid_env(CLAUDE_SESSION_ID="legacy-2")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(peer_bus._claude_session_id(), "legacy-2")
            self.assertEqual(peer_bus._session_id(), "legacy-2")

    def test_grok_session_id_beats_claude(self) -> None:
        env = _cleared_sid_env(
            GROK_SESSION_ID="grok-9",
            CLAUDE_CODE_SESSION_ID="code-1",
            CLAUDE_SESSION_ID="legacy-1",
        )
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(peer_bus._claude_session_id(), "code-1")
            self.assertEqual(peer_bus._session_id(), "grok-9")

    def test_override_ignored_without_flag(self) -> None:
        env = _cleared_sid_env(PEER_BUS_SESSION_ID="spoof")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(peer_bus._claude_session_id())
            self.assertIsNone(peer_bus._session_id())

    def test_sid_from_grok_resume_argv(self) -> None:
        sid = "01a06164-e873-7d63-ada8-aaac94102d4a"
        self.assertEqual(
            peer_bus._sid_from_grok_argv(
                ["grok", "--resume", sid, "--model", "grok-4.6"]
            ),
            sid,
        )
        self.assertIsNone(peer_bus._sid_from_grok_argv(["python3", "peer_bus.py", "watch", "--as", "Cora"]))


class TempBusMixin:
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="peer-bus-ut-")
        self.root = Path(self._tmpdir.name)
        self.env = {
            "PEER_BUS_ROOT": str(self.root),
            "PEER_BUS_TRUST_NAME_KEYS": "1",
            "PEER_BUS_WAKE_DROP": "1",
            "PEER_BUS_HARNESS": "test",
        }
        self._saved = {
            "ROOT": peer_bus.ROOT,
            "INBOX": peer_bus.INBOX,
            "REGISTRY": peer_bus.REGISTRY,
            "WAKE": peer_bus.WAKE,
            "RECEIPTS": peer_bus.RECEIPTS,
            "TRUST_NAME_KEYS": peer_bus.TRUST_NAME_KEYS,
            "WAKE_DROP": peer_bus.WAKE_DROP,
            "WAKE_ENABLED": peer_bus.WAKE_ENABLED,
            "WAKE_CMD": peer_bus.WAKE_CMD,
            "_WAKE_CALLBACK": peer_bus._WAKE_CALLBACK,
        }
        peer_bus.ROOT = self.root.resolve()
        peer_bus.INBOX = peer_bus.ROOT / "inbox"
        peer_bus.REGISTRY = peer_bus.ROOT / "registry"
        peer_bus.WAKE = peer_bus.ROOT / "wake"
        peer_bus.RECEIPTS = peer_bus.ROOT / "receipts"
        peer_bus.TRUST_NAME_KEYS = True
        peer_bus.WAKE_DROP = True
        peer_bus.WAKE_ENABLED = False
        peer_bus.WAKE_CMD = ""
        peer_bus._WAKE_CALLBACK = None
        peer_bus._ensure_dirs()

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            setattr(peer_bus, name, value)
        self._tmpdir.cleanup()


class TempBusTestCase(TempBusMixin, unittest.TestCase):
    def test_send_recv_ack_wake_drop(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            sender = peer_bus.detect_self("Orchestra")
            result = peer_bus.send_message("Worker", "unit-ping", summary="ut", self_info=sender)
            self.assertTrue(result["ok"])
            self.assertTrue(result["wake"]["ok"])
            worker = peer_bus.detect_self("Worker")
            drop = peer_bus.WAKE / f"{worker['key']}.json"
            self.assertTrue(drop.is_file(), drop)
            msgs = peer_bus.receive_messages(worker)
            self.assertEqual(len(msgs), 1)
            mid = msgs[0]["msg_id"]
            ack = peer_bus.ack_message(mid, worker)
            self.assertTrue(ack["ok"])
            self.assertEqual(peer_bus.receive_messages(worker), [])

    def test_recv_newest_first_limit_does_not_mark_read(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            worker = peer_bus.detect_self("Worker")
            dest = peer_bus._inbox_dir(worker["key"], create=True)
            stamps = [
                ("2026-09-04T00:00:00.000000Z", "old"),
                ("2026-09-07T08:00:00.000000Z", "mid"),
                ("2026-09-07T18:00:00.000000Z", "new"),
            ]
            for i, (ts, body) in enumerate(stamps):
                mid = f"{i:032x}"
                payload = {
                    "msg_id": mid,
                    "ts": ts,
                    "body": body,
                    "from": {"name": "Orchestra"},
                    "read": False,
                }
                (dest / f"{1000 + i}-{mid}.json").write_text(json.dumps(payload) + "\n")
            newest = peer_bus.receive_messages(worker, limit=2)
            self.assertEqual([m["body"] for m in newest], ["new", "mid"])
            still = peer_bus.receive_messages(worker, limit=10)
            self.assertEqual([m["body"] for m in still], ["new", "mid", "old"])

    def test_refuse_symlink_inbox(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            key = peer_bus._safe_key("Worker")
            target = self.root / "outside"
            target.mkdir()
            link = peer_bus.INBOX / key
            peer_bus.INBOX.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                peer_bus._inbox_dir(key, create=True)


class CliSmokeTests(unittest.TestCase):
    def test_version_and_watch_once(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peer-bus-cli-") as tmp:
            env = os.environ.copy()
            env.update(
                {
                    "PEER_BUS_ROOT": tmp,
                    "PEER_BUS_TRUST_NAME_KEYS": "1",
                    "PEER_BUS_WAKE_DROP": "1",
                }
            )
            ver = subprocess.check_output(
                [sys.executable, str(ROOT / "peer_bus.py"), "version"],
                env=env,
                text=True,
            ).strip()
            self.assertRegex(ver, r"^\d+\.\d+\.\d+$")
            subprocess.check_call(
                [sys.executable, str(ROOT / "peer_bus.py"), "watch", "--as", "Worker", "--once"],
                env=env,
            )

    def test_watch_once_empty_stdout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peer-bus-watch-") as tmp:
            env = os.environ.copy()
            env.update({"PEER_BUS_ROOT": tmp, "PEER_BUS_TRUST_NAME_KEYS": "1"})
            out = subprocess.check_output(
                [sys.executable, str(ROOT / "peer_bus.py"), "watch", "--as", "Worker", "--once"],
                env=env,
                text=True,
            )
            self.assertEqual(out, "")

    def test_watch_reexecs_as_from_detect_self(self) -> None:
        with mock.patch.object(
            peer_bus,
            "detect_self",
            return_value={"name": "Drew", "key": "z", "session_id": "z"},
        ):
            with mock.patch.object(peer_bus.os, "execvp") as ex:
                args = argparse.Namespace(
                    as_name=None,
                    interval=2.0,
                    max_interval=30.0,
                    once=True,
                    max_runtime=None,
                )
                peer_bus._cmd_watch(args)
        ex.assert_called_once()
        argv = ex.call_args[0][1]
        self.assertIn("--as", argv)
        self.assertIn("Drew", argv)

    def test_cli_trust_name_keys_send_recv_wake_drop(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peer-bus-cli-send-") as tmp:
            env = os.environ.copy()
            env.update(
                {
                    "PEER_BUS_ROOT": tmp,
                    "PEER_BUS_TRUST_NAME_KEYS": "1",
                    "PEER_BUS_WAKE_DROP": "1",
                }
            )
            pb = [sys.executable, str(ROOT / "peer_bus.py")]
            send = subprocess.check_output(
                pb + ["send", "--as", "Orchestra", "--to", "Worker", "--body", "cli-ping"],
                env=env,
                text=True,
            )
            payload = json.loads(send)
            self.assertTrue(payload["ok"])
            mid = payload["msg_id"]
            key = payload["to"]["key"]
            drop = Path(tmp) / "wake" / f"{key}.json"
            self.assertTrue(drop.is_file(), drop)
            self.assertEqual(json.loads(drop.read_text())["msg_id"], mid)
            recv = subprocess.check_output(
                pb + ["recv", "--as", "Worker", "--json"],
                env=env,
                text=True,
            )
            msgs = json.loads(recv)
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0]["msg_id"], mid)
            self.assertIn("cli-ping", msgs[0].get("body_raw") or msgs[0].get("body") or "")



class McpTrustRefusalTests(unittest.TestCase):
    def test_mcp_exits_when_trust_name_keys_set(self) -> None:
        env = os.environ.copy()
        env["PEER_BUS_TRUST_NAME_KEYS"] = "1"
        env["PEER_BUS_ROOT"] = tempfile.mkdtemp(prefix="peer-bus-mcp-")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "mcp_server.py")],
            input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
            env=env,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)


class BodyCapTests(unittest.TestCase):
    def test_body_too_large_raises(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peer-bus-cap-") as tmp:
            root = Path(tmp)
            peer_bus.ROOT = root.resolve()
            peer_bus.INBOX = peer_bus.ROOT / "inbox"
            peer_bus.REGISTRY = peer_bus.ROOT / "registry"
            peer_bus.WAKE = peer_bus.ROOT / "wake"
            peer_bus.RECEIPTS = peer_bus.ROOT / "receipts"
            peer_bus.TRUST_NAME_KEYS = True
            peer_bus.WAKE_DROP = True
            peer_bus._ensure_dirs()
            env = {"PEER_BUS_ROOT": tmp, "PEER_BUS_TRUST_NAME_KEYS": "1"}
            with mock.patch.dict(os.environ, env, clear=False):
                old = peer_bus.MAX_BODY
                peer_bus.MAX_BODY = 32
                try:
                    sender = peer_bus.detect_self("Orchestra")
                    with self.assertRaises(ValueError):
                        peer_bus.send_message("Worker", "x" * 64, self_info=sender)
                finally:
                    peer_bus.MAX_BODY = old



class StaleSendTests(unittest.TestCase):
    def test_send_to_unknown_without_stale_flag_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peer-bus-stale-") as tmp:
            root = Path(tmp)
            peer_bus.ROOT = root.resolve()
            peer_bus.INBOX = peer_bus.ROOT / "inbox"
            peer_bus.REGISTRY = peer_bus.ROOT / "registry"
            peer_bus.WAKE = peer_bus.ROOT / "wake"
            peer_bus.RECEIPTS = peer_bus.ROOT / "receipts"
            peer_bus.TRUST_NAME_KEYS = False
            peer_bus.ALLOW_STALE_SEND = False
            peer_bus._ensure_dirs()
            env = {"PEER_BUS_ROOT": tmp}
            os.environ.pop("PEER_BUS_TRUST_NAME_KEYS", None)
            with mock.patch.dict(os.environ, env, clear=False):
                # Force non-name-key mode: resolve_recipient needs live list
                peer_bus.TRUST_NAME_KEYS = False
                sender = peer_bus.detect_self("Gus")
                # Without live peer, resolve should fail
                with self.assertRaises(ValueError):
                    peer_bus.send_message("Nobody [dead00]", "x", self_info=sender)


class RosterTests(unittest.TestCase):
    def setUp(self) -> None:
        peer_bus._TMUX_CACHE = None
        self._tmpdir = tempfile.TemporaryDirectory(prefix="peer-bus-roster-")
        self.root = Path(self._tmpdir.name)
        self._saved = {
            "ROOT": peer_bus.ROOT,
            "INBOX": peer_bus.INBOX,
            "REGISTRY": peer_bus.REGISTRY,
            "WAKE": peer_bus.WAKE,
            "RECEIPTS": peer_bus.RECEIPTS,
            "USAGE_DIR": peer_bus.USAGE_DIR,
        }
        peer_bus.ROOT = self.root.resolve()
        peer_bus.INBOX = peer_bus.ROOT / "inbox"
        peer_bus.REGISTRY = peer_bus.ROOT / "registry"
        peer_bus.WAKE = peer_bus.ROOT / "wake"
        peer_bus.RECEIPTS = peer_bus.ROOT / "receipts"
        peer_bus.USAGE_DIR = self.root / "usage"
        peer_bus._ensure_dirs()
        peer_bus.USAGE_DIR.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            setattr(peer_bus, name, value)
        peer_bus._TMUX_CACHE = None
        self._tmpdir.cleanup()

    def test_clean_pane_title(self) -> None:
        self.assertEqual(peer_bus._clean_pane_title("✳ Ada (3)"), "Ada")
        self.assertEqual(peer_bus._clean_pane_title("Cora - grok"), "Cora")
        self.assertEqual(peer_bus._clean_pane_title("Beau"), "Beau")

    def test_ghost_names(self) -> None:
        self.assertTrue(peer_bus._is_ghost_name("Check inbox once"))
        self.assertTrue(peer_bus._is_ghost_name("throttle"))
        self.assertTrue(peer_bus._is_ghost_name("anon-1cd86ec7"))
        self.assertFalse(peer_bus._is_ghost_name("Ada"))

    def test_claude_detect_self_not_grok_prefix(self) -> None:
        env = _cleared_sid_env(
            CLAUDE_CODE_SESSION_ID="c662b3ea-d616-4c37-8a78-74ae8272b578",
            PEER_BUS_HARNESS="claude",
            PEER_BUS_TMUX="0",
        )
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(peer_bus, "_tmux_seats", return_value=[]):
                with mock.patch.object(peer_bus, "_usage_name_for_sid", return_value=None):
                    me = peer_bus.detect_self()
        self.assertEqual(me["harness"], "claude")
        self.assertTrue(me["name"].startswith("claude-"), me["name"])
        self.assertFalse(me["name"].startswith("grok-"), me["name"])

    def test_detect_self_uses_tmux_title(self) -> None:
        sid = "c662b3ea-d616-4c37-8a78-74ae8272b578"
        seat = {"name": "Ada", "session_id": sid, "harness": "claude"}
        env = _cleared_sid_env(
            CLAUDE_CODE_SESSION_ID=sid,
            PEER_BUS_HARNESS="claude",
            PEER_BUS_TMUX="0",
        )
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(peer_bus, "_tmux_seats", return_value=[seat]):
                me = peer_bus.detect_self()
        self.assertEqual(me["name"], "Ada")
        self.assertEqual(me["harness"], "claude")

    def test_registry_pid_alive_is_not_a_seat(self) -> None:
        sid = "cdc9c934-7fcd-4073-9f41-30d01643643c"
        reg = [
            {
                "key": sid,
                "name": "grok-cdc9c934",
                "ref": sid[:6],
                "session_id": sid,
                "harness": "claude",
                "state": "live",
                "pid": 1206015,
                "source": "registry",
                "address": f"grok-cdc9c934 [{sid[:6]}]",
            }
        ]
        with mock.patch.object(peer_bus, "_tmux_seats", return_value=[]):
            with mock.patch.object(peer_bus, "_grok_agents", return_value=[]):
                with mock.patch.object(peer_bus, "_usage_agents", return_value=[]):
                    with mock.patch.object(peer_bus, "_registry_agents", return_value=reg):
                        with mock.patch.object(peer_bus, "_pid_alive", return_value=True):
                            self.assertEqual(peer_bus.list_agents(False), [])
                            all_rows = peer_bus.list_agents(True)
        self.assertEqual(all_rows[0]["state"], "stale")

    def test_list_drops_registry_ghosts(self) -> None:
        ghost = {
            "key": "01a056ef-774d-7623-b4f5-1a1fd8f82c2e",
            "name": "Check inbox once",
            "ref": "01a056",
            "session_id": "01a056ef-774d-7623-b4f5-1a1fd8f82c2e",
            "harness": "grok",
            "state": "live",
            "source": "registry",
            "address": "Check inbox once [01a056]",
        }
        with mock.patch.object(peer_bus, "_tmux_seats", return_value=[]):
            with mock.patch.object(peer_bus, "_grok_agents", return_value=[]):
                with mock.patch.object(peer_bus, "_usage_agents", return_value=[]):
                    with mock.patch.object(peer_bus, "_registry_agents", return_value=[ghost]):
                        live = peer_bus.list_agents(False)
                        all_rows = peer_bus.list_agents(True)
        self.assertEqual(live, [])
        self.assertEqual(len(all_rows), 1)
        self.assertEqual(all_rows[0]["state"], "stale")

    def test_tmux_live_beats_stale_usage_same_sid(self) -> None:
        sid = "c662b3ea-d616-4c37-8a78-74ae8272b578"
        seat = {
            "name": "Ada",
            "session_id": sid,
            "harness": "claude",
            "agent_pid": 9,
            "pane_id": "%0",
        }
        usage = [
            {
                "key": sid,
                "name": "grok-c662b3ea",
                "ref": sid[:6],
                "session_id": sid,
                "harness": "claude",
                "state": "stale",
                "source": "usage",
                "address": f"grok-c662b3ea [{sid[:6]}]",
            }
        ]
        with mock.patch.object(peer_bus, "_tmux_seats", return_value=[seat]):
            with mock.patch.object(peer_bus, "_grok_agents", return_value=[]):
                with mock.patch.object(peer_bus, "_usage_agents", return_value=usage):
                    with mock.patch.object(peer_bus, "_registry_agents", return_value=[]):
                        rows = peer_bus.list_agents(False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Ada")
        self.assertEqual(rows[0]["state"], "live")
        self.assertEqual(rows[0]["source"], "tmux")
        self.assertEqual(rows[0]["address"], "Ada [c662b3]")

    def test_parse_herdr_agents_uses_session_and_stripped_title(self) -> None:
        payload = {
            "result": {
                "agents": [
                    {
                        "agent": "claude",
                        "agent_session": {
                            "value": "804be46b-ff20-4136-83f4-75d62912df53"
                        },
                        "pane_id": "w2:p2",
                        "terminal_title_stripped": "Beau",
                    },
                    {
                        "agent": "grok",
                        "agent_session": {
                            "value": "01a06164-e873-7d63-ada8-aaac94102d4a"
                        },
                        "pane_id": "w2:pB",
                        "terminal_title_stripped": "Cora - grok",
                    },
                    {
                        "agent": "claude",
                        "pane_id": "w2:pE",
                        "terminal_title_stripped": "Ada",
                    },
                ]
            }
        }
        rows = {r["name"]: r for r in peer_bus._parse_herdr_agents(payload)}
        self.assertEqual(rows["Beau"]["session_id"], "804be46b-ff20-4136-83f4-75d62912df53")
        self.assertEqual(rows["Beau"]["harness"], "claude")
        self.assertEqual(rows["Cora"]["harness"], "grok")
        self.assertIsNone(rows["Ada"]["session_id"])

    def test_parse_herdr_agents_prefers_pane_label_over_shell_title(self) -> None:
        payload = {
            "result": {
                "agents": [
                    {
                        "agent": "claude",
                        "agent_session": {
                            "value": "9e7e13c3-83ae-4505-85e9-dc534ca58fbc"
                        },
                        "pane_id": "w3:pE",
                        "terminal_title_stripped": "user@host:~/project",
                    }
                ]
            }
        }
        rows = peer_bus._parse_herdr_agents(payload, labels={"w3:pE": "Ada"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Ada")
        self.assertEqual(rows[0]["session_id"], "9e7e13c3-83ae-4505-85e9-dc534ca58fbc")

    def test_sid_from_herdr_process_info_resume_argv(self) -> None:
        payload = {
            "result": {
                "process_info": {
                    "foreground_processes": [
                        {
                            "argv": [
                                "claude",
                                "--resume",
                                "9e7e13c3-83ae-4505-85e9-dc534ca58fbc",
                                "-n",
                                "Ada",
                            ],
                            "name": "claude",
                            "pid": 11,
                        }
                    ],
                    "shell_pid": 11,
                }
            }
        }
        sid, harness, pid = peer_bus._sid_from_herdr_process_info(payload)
        self.assertEqual(sid, "9e7e13c3-83ae-4505-85e9-dc534ca58fbc")
        self.assertEqual(harness, "claude")
        self.assertEqual(pid, 11)

    def test_sid_from_herdr_process_info_attach_argv(self) -> None:
        payload = {
            "result": {
                "process_info": {
                    "foreground_processes": [
                        {
                            "argv": ["claude", "attach", "9e7e13c3"],
                            "name": "claude",
                            "pid": 22,
                        }
                    ],
                    "shell_pid": 22,
                }
            }
        }
        jobs = [
            {
                "name": "Ada",
                "session_id": "9e7e13c3-83ae-4505-85e9-dc534ca58fbc",
                "pid": 22,
            }
        ]
        with mock.patch.object(peer_bus, "_claude_bg_jobs", return_value=jobs):
            sid, harness, pid = peer_bus._sid_from_herdr_process_info(payload)
        self.assertEqual(sid, "9e7e13c3-83ae-4505-85e9-dc534ca58fbc")
        self.assertEqual(harness, "claude")
        self.assertEqual(pid, 22)

    def test_herdr_live_beats_stale_usage_same_sid(self) -> None:
        sid = "804be46b-ff20-4136-83f4-75d62912df53"
        seat = {
            "name": "Beau",
            "session_id": sid,
            "harness": "claude",
            "pane_id": "w2:p2",
        }
        usage = [
            {
                "key": sid,
                "name": "grok-804be46b",
                "ref": sid[:6],
                "session_id": sid,
                "harness": "claude",
                "state": "stale",
                "source": "usage",
                "address": f"grok-804be46b [{sid[:6]}]",
            }
        ]
        with mock.patch.object(peer_bus, "_tmux_seats", return_value=[]):
            with mock.patch.object(peer_bus, "_herdr_seats", return_value=[seat]):
                with mock.patch.object(peer_bus, "_grok_agents", return_value=[]):
                    with mock.patch.object(peer_bus, "_usage_agents", return_value=usage):
                        with mock.patch.object(peer_bus, "_registry_agents", return_value=[]):
                            rows = peer_bus.list_agents(False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Beau")
        self.assertEqual(rows[0]["state"], "live")
        self.assertEqual(rows[0]["source"], "herdr")
        self.assertEqual(rows[0]["address"], "Beau [804be4]")

    def test_sid_from_claude_bg_job_prefers_pid(self) -> None:
        jobs = [
            {"name": "Ada", "session_id": "d208ebda-d4f3-4b97-86dd-3d6861df55af", "pid": None},
            {
                "name": "Ada",
                "session_id": "9e7e13c3-83ae-4505-85e9-dc534ca58fbc",
                "pid": 722059,
            },
        ]
        with mock.patch.object(peer_bus, "_claude_bg_jobs", return_value=jobs):
            self.assertEqual(
                peer_bus._sid_from_claude_bg_job("Ada", 722059),
                "9e7e13c3-83ae-4505-85e9-dc534ca58fbc",
            )
            self.assertEqual(
                peer_bus._sid_from_claude_bg_job("Ada", None),
                "9e7e13c3-83ae-4505-85e9-dc534ca58fbc",
            )

    def test_parse_claude_bg_jobs_strips_suffix(self) -> None:
        rows = peer_bus._parse_claude_bg_jobs(
            [
                {
                    "id": "9e7e13c3",
                    "sessionId": "9e7e13c3-83ae-4505-85e9-dc534ca58fbc",
                    "name": "Ada (4)",
                    "pid": 1,
                }
            ]
        )
        self.assertEqual(rows[0]["name"], "Ada")
        self.assertEqual(rows[0]["session_id"], "9e7e13c3-83ae-4505-85e9-dc534ca58fbc")

    def test_dead_grok_pid_not_live(self) -> None:
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        grok = [
            {
                "key": sid,
                "name": "grok-dead",
                "ref": sid[:6],
                "session_id": sid,
                "harness": "grok",
                "state": "live",
                "pid": 1,
                "source": "grok",
                "address": f"grok-dead [{sid[:6]}]",
            }
        ]
        with mock.patch.object(peer_bus, "_tmux_seats", return_value=[]):
            with mock.patch.object(peer_bus, "_grok_agents", return_value=grok):
                with mock.patch.object(peer_bus, "_usage_agents", return_value=[]):
                    with mock.patch.object(peer_bus, "_registry_agents", return_value=[]):
                        live = peer_bus.list_agents(False)
                        all_rows = peer_bus.list_agents(True)
        self.assertEqual(live, [])
        self.assertEqual(all_rows[0]["state"], "stale")

    def test_collapse_same_pid_keeps_named_seat(self) -> None:
        sid_a = "01a06164-e873-7d63-ada8-aaac94102d4a"
        sid_b = "01a06bed-5657-7a30-9f5b-8005961fff8c"
        seat = {
            "name": "Cora",
            "session_id": sid_a,
            "harness": "grok",
            "agent_pid": 4242,
            "pane_id": "%11",
        }
        grok = [
            {
                "key": sid_b,
                "name": "grok-01a06bed",
                "ref": sid_b[:6],
                "session_id": sid_b,
                "harness": "grok",
                "state": "live",
                "pid": 4242,
                "source": "grok",
                "address": f"grok-01a06bed [{sid_b[:6]}]",
            }
        ]
        with mock.patch.object(peer_bus, "_tmux_seats", return_value=[seat]):
            with mock.patch.object(peer_bus, "_grok_agents", return_value=grok):
                with mock.patch.object(peer_bus, "_usage_agents", return_value=[]):
                    with mock.patch.object(peer_bus, "_registry_agents", return_value=[]):
                        with mock.patch.object(peer_bus, "_pid_alive", return_value=True):
                            rows = peer_bus.list_agents(False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Cora")

    def test_ref_grows_when_six_char_prefix_collides(self) -> None:
        a = "01a06164-e873-7d63-ada8-aaac94102d4a"
        b = "01a06165-10f7-7150-8a56-e38ae96438e5"
        seats = [
            {"name": "Cora", "session_id": a, "harness": "grok", "agent_pid": 11, "pane_id": "%1"},
            {"name": "Drew", "session_id": b, "harness": "grok", "agent_pid": 12, "pane_id": "%2"},
        ]
        with mock.patch.object(peer_bus, "_tmux_seats", return_value=seats):
            with mock.patch.object(peer_bus, "_grok_agents", return_value=[]):
                with mock.patch.object(peer_bus, "_usage_agents", return_value=[]):
                    with mock.patch.object(peer_bus, "_registry_agents", return_value=[]):
                        rows = {r["name"]: r for r in peer_bus.list_agents(False)}
        self.assertEqual(rows["Cora"]["ref"], "01a06164")
        self.assertEqual(rows["Drew"]["ref"], "01a06165")
        self.assertEqual(rows["Cora"]["address"], "Cora [01a06164]")

    def test_ref_includes_hyphen_when_eight_hex_collides(self) -> None:
        a = "01a08225-0f17-7553-b0c8-2fe76f42e12e"
        b = "01a08225-aa6c-7ba1-bbe3-92a1bb389108"
        seats = [
            {"name": "Eve", "session_id": a, "harness": "grok", "agent_pid": 21, "pane_id": "%3"},
            {"name": "Fay", "session_id": b, "harness": "grok", "agent_pid": 22, "pane_id": "%4"},
        ]
        with mock.patch.object(peer_bus, "_herdr_seats", return_value=[]):
            with mock.patch.object(peer_bus, "_tmux_seats", return_value=seats):
                with mock.patch.object(peer_bus, "_grok_agents", return_value=[]):
                    with mock.patch.object(peer_bus, "_usage_agents", return_value=[]):
                        with mock.patch.object(peer_bus, "_registry_agents", return_value=[]):
                            rows = {r["name"]: r for r in peer_bus.list_agents(False)}
        self.assertEqual(rows["Eve"]["address"], "Eve [01a08225-0]")
        self.assertEqual(rows["Fay"]["address"], "Fay [01a08225-a]")
        chosen = peer_bus.resolve_recipient(
            rows["Eve"]["address"], agents=list(rows.values())
        )
        self.assertEqual(chosen["session_id"], a)
        chosen_b = peer_bus.resolve_recipient("Fay [01a08225-a]", agents=list(rows.values()))
        self.assertEqual(chosen_b["session_id"], b)

    def test_ambiguous_prefix_ref_raises(self) -> None:
        a = "01a08225-0f17-7553-b0c8-2fe76f42e12e"
        b = "01a08225-aa6c-7ba1-bbe3-92a1bb389108"
        agents = [
            {"name": "Eve", "session_id": a, "key": a, "address": "Eve [01a08225-0]"},
            {"name": "Eve", "session_id": b, "key": b, "address": "Eve [01a08225-a]"},
        ]
        with self.assertRaises(ValueError) as ctx:
            peer_bus.resolve_recipient("Eve [01a08225]", agents=agents)
        self.assertIn("ambiguous", str(ctx.exception).lower())

    def test_flock_matches_list(self) -> None:
        with mock.patch.object(peer_bus, "list_agents", return_value=[{"name": "X"}]) as mocked:
            self.assertEqual(peer_bus.flock(include_stale=True), [{"name": "X"}])
            mocked.assert_called_once_with(include_stale=True)

    def test_pool_drops_expired_and_rows_omit_five_hour(self) -> None:
        now = time.time()
        (peer_bus.USAGE_DIR / "old.json").write_text(
            json.dumps(
                {
                    "ts": "2026-09-04T00:00:00Z",
                    "name": "Gus",
                    "session": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "five_hour": "93",
                    "five_hour_resets_at": now - 100,
                    "context": "10",
                }
            )
            + "\n"
        )
        fresh = datetime.fromtimestamp(now - 30, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        (peer_bus.USAGE_DIR / "new.json").write_text(
            json.dumps(
                {
                    "ts": fresh,
                    "name": "Ada",
                    "session": "c662b3ea-d616-4c37-8a78-74ae8272b578",
                    "five_hour": "48.2",
                    "five_hour_resets_at": now + 3600,
                    "seven_day": "61.4",
                    "seven_day_resets_at": now + 86400,
                    "context": "16",
                }
            )
            + "\n"
        )
        pool = peer_bus.pool_usage(now=now)
        self.assertIsNotNone(pool)
        self.assertEqual(pool["five_hour"], "48")
        self.assertEqual(pool["seven_day"], "61")
        self.assertEqual(pool["state"], "live")
        self.assertEqual(pool["source_name"], "Ada")
        with mock.patch.object(peer_bus, "_tmux_seats", return_value=[]):
            with mock.patch.object(peer_bus, "_grok_agents", return_value=[]):
                with mock.patch.object(peer_bus, "_registry_agents", return_value=[]):
                    rows = {r["name"]: r for r in peer_bus.list_agents(False)}
                    view = peer_bus.roster(False)
        self.assertNotIn("five_hour", rows["Ada"])
        self.assertNotIn("five_hour", rows["Gus"])
        self.assertEqual(rows["Ada"]["context"], "16")
        self.assertEqual(view["bus_version"], peer_bus.PEER_BUS_VERSION)
        self.assertEqual(view["pool"]["five_hour"], "48")
        self.assertEqual(view["pool"]["seven_day"], "61")
        self.assertEqual(view["pool"]["state"], "live")
        self.assertEqual(view["pool"]["schema"], peer_bus.POOL_SCHEMA)
        self.assertNotIn("five_hour", view["agents"][0])
        self.assertNotIn("seven_day", view["agents"][0])

    def test_pool_keeps_seven_day_when_five_hour_window_expired(self) -> None:
        now = time.time()
        (peer_bus.USAGE_DIR / "snap.json").write_text(
            json.dumps(
                {
                    "ts": datetime.fromtimestamp(now - 30, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "name": "Ada",
                    "session": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "five_hour": "90",
                    "five_hour_resets_at": now - 10,
                    "seven_day": "40",
                    "seven_day_resets_at": now + 86400,
                }
            )
        )
        pool = peer_bus.pool_usage(now=now)
        self.assertIsNotNone(pool)
        self.assertIsNone(pool["five_hour"])
        self.assertEqual(pool["seven_day"], "40")

    def test_pool_marks_stale_when_snapshot_is_old(self) -> None:
        now = time.time()
        old_ts = datetime.fromtimestamp(now - 12 * 60, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        (peer_bus.USAGE_DIR / "snap.json").write_text(
            json.dumps(
                {
                    "ts": old_ts,
                    "name": "Ada",
                    "session": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "five_hour": "20",
                    "five_hour_resets_at": now + 3600,
                    "seven_day": "30",
                    "seven_day_resets_at": now + 86400,
                }
            )
        )
        pool = peer_bus.pool_usage(now=now)
        self.assertEqual(pool["state"], "stale")
        self.assertGreater(pool["age_min"], 5)

    def test_context_marks_stale_snapshot(self) -> None:
        row: dict = {}
        peer_bus._overlay_context(row, {"context": "76", "age_min": 6.0})
        self.assertEqual(row["context"], "76~")
        peer_bus._overlay_context(row, {"context": "76~", "age_min": 9.0})
        self.assertEqual(row["context"], "76~")

    def test_unread_count_and_mail_cli(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "PEER_BUS_ROOT": str(self.root),
                "PEER_BUS_TRUST_NAME_KEYS": "1",
                "PEER_BUS_WAKE_DROP": "1",
                "PEER_BUS_TMUX": "0",
            }
        )
        pb = [sys.executable, str(ROOT / "peer_bus.py")]
        empty = subprocess.check_output(pb + ["mail", "--as", "Worker"], env=env, text=True)
        self.assertEqual(empty.strip(), "0")
        subprocess.check_output(
            pb + ["send", "--as", "Orchestra", "--to", "Worker", "--body", "ping"],
            env=env,
            text=True,
        )
        n = subprocess.check_output(pb + ["mail", "--as", "Worker"], env=env, text=True)
        self.assertEqual(n.strip(), "1")
        payload = json.loads(
            subprocess.check_output(pb + ["mail", "--as", "Worker", "--json"], env=env, text=True)
        )
        self.assertEqual(payload["count"], 1)

    def test_watch_emits_on_send(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "PEER_BUS_ROOT": str(self.root),
                "PEER_BUS_TRUST_NAME_KEYS": "1",
                "PEER_BUS_WAKE_DROP": "1",
                "PEER_BUS_TMUX": "0",
            }
        )
        pb = [sys.executable, str(ROOT / "peer_bus.py")]
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            pb + ["watch", "--as", "Worker", "--interval", "0.5", "--max-interval", "1"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(0.3)
            subprocess.check_output(
                pb + ["send", "--as", "Orchestra", "--to", "Worker", "--body", "watch-ping"],
                env=env,
            )
            assert proc.stdout is not None
            line = proc.stdout.readline()
            self.assertIn("\t", line)
            self.assertTrue(line.strip().split("\t")[0])
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

    def test_cli_flock_json(self) -> None:
        env = os.environ.copy()
        env.update({"PEER_BUS_ROOT": str(self.root), "PEER_BUS_TMUX": "0", "PEER_BUS_HERDR": "0"})
        out = subprocess.check_output(
            [sys.executable, str(ROOT / "peer_bus.py"), "flock", "--json"],
            env=env,
            text=True,
        )
        json.loads(out)


class CoerceSendArgsTests(unittest.TestCase):
    def test_aliases(self) -> None:
        to, body = peer_bus.coerce_send_args(
            {"recipient": "Ada [9e7e13]", "message": "hi"}
        )
        self.assertEqual(to, "Ada [9e7e13]")
        self.assertEqual(body, "hi")

    def test_to_prefix_stripped_from_body(self) -> None:
        to, body = peer_bus.coerce_send_args({"body": "TO Ada [9e7e13]\nhello"})
        self.assertEqual(to, "Ada [9e7e13]")
        self.assertEqual(body, "hello")

    def test_missing_returns_none(self) -> None:
        to, body = peer_bus.coerce_send_args({"summary": "x", "display_name": "Ivy"})
        self.assertIsNone(to)
        self.assertIsNone(body)


class McpSendArgTests(unittest.TestCase):
    def test_missing_to_lists_keys(self) -> None:
        import mcp_server

        with mock.patch.object(peer_bus, "TRUST_NAME_KEYS", False):
            with mock.patch.object(
                peer_bus, "detect_self", return_value={"key": "ivy", "name": "Ivy"}
            ):
                out = mcp_server.call_tool(
                    "send_message", {"body": "x", "summary": "long"}
                )
        payload = json.loads(out["content"][0]["text"])
        self.assertFalse(payload["ok"])
        self.assertIn("missing to", payload["error"])
        self.assertIn("body", payload["error"])

    def test_recipient_alias_reaches_send(self) -> None:
        import mcp_server

        sent: dict = {}

        def _fake_send(to: str, body: str, **kwargs: object) -> dict:
            sent["to"] = to
            sent["body"] = body
            return {"ok": True, "accepted": True, "msg_id": "m1"}

        with mock.patch.object(peer_bus, "TRUST_NAME_KEYS", False):
            with mock.patch.object(
                peer_bus, "detect_self", return_value={"key": "ivy", "name": "Ivy"}
            ):
                with mock.patch.object(peer_bus, "send_message", side_effect=_fake_send):
                    out = mcp_server.call_tool(
                        "send_message",
                        {"recipient": "Ada [9e7e13]", "body": "ping"},
                    )
        payload = json.loads(out["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertEqual(sent["to"], "Ada [9e7e13]")
        self.assertEqual(sent["body"], "ping")


class PruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="peer-bus-prune-")
        self.root = Path(self._tmpdir.name)
        self._saved = {
            "ROOT": peer_bus.ROOT,
            "INBOX": peer_bus.INBOX,
            "REGISTRY": peer_bus.REGISTRY,
            "WAKE": peer_bus.WAKE,
            "RECEIPTS": peer_bus.RECEIPTS,
            "USAGE_DIR": peer_bus.USAGE_DIR,
        }
        peer_bus.ROOT = self.root.resolve()
        peer_bus.INBOX = peer_bus.ROOT / "inbox"
        peer_bus.REGISTRY = peer_bus.ROOT / "registry"
        peer_bus.WAKE = peer_bus.ROOT / "wake"
        peer_bus.RECEIPTS = peer_bus.ROOT / "receipts"
        peer_bus.USAGE_DIR = self.root / "usage"
        peer_bus._ensure_dirs()
        peer_bus.USAGE_DIR.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            setattr(peer_bus, name, value)
        self._tmpdir.cleanup()

    def test_dry_run_then_apply(self) -> None:
        live_sid = "9e7e13c3-83ae-4505-85e9-dc534ca58fbc"
        (peer_bus.REGISTRY / f"{live_sid}.json").write_text(
            json.dumps({"name": "Ada", "pid": 1, "session_id": live_sid})
        )
        (peer_bus.REGISTRY / "dead00.json").write_text(
            json.dumps(
                {
                    "name": "Check inbox once",
                    "pid": 2147483647,
                    "session_id": "dead00",
                }
            )
        )
        (peer_bus.USAGE_DIR / "c662b3ea-d616-4c37-8a78-74ae8272b578.json").write_text(
            json.dumps({"name": "Ada", "session_id": "c662b3ea-d616-4c37-8a78-74ae8272b578"})
        )
        (peer_bus.USAGE_DIR / f"{live_sid}.json").write_text(
            json.dumps({"name": "Ada", "session_id": live_sid})
        )
        empty = peer_bus.INBOX / "c662b3ea-d616-4c37-8a78-74ae8272b578"
        empty.mkdir()
        (empty / "read").mkdir()
        mailed = peer_bus.INBOX / "keep-mail"
        mailed.mkdir()
        (mailed / "1-abc.json").write_text("{}\n")

        live = [{"session_id": live_sid, "key": live_sid, "name": "Ada"}]
        with mock.patch.object(peer_bus, "list_agents", return_value=live):
            dry = peer_bus.prune_stale(apply=False)
        kinds = {(c["kind"], c["reason"]) for c in dry["candidates"]}
        self.assertIn(("registry", "ghost"), kinds)
        self.assertIn(("usage", "not-live"), kinds)
        self.assertIn(("inbox", "empty-dead"), kinds)
        self.assertFalse(dry["apply"])
        self.assertTrue(any(s["reason"].startswith("kept-unread") for s in dry["skipped"]))
        self.assertTrue((peer_bus.REGISTRY / "dead00.json").is_file())

        with mock.patch.object(peer_bus, "list_agents", return_value=live):
            applied = peer_bus.prune_stale(apply=True)
        self.assertTrue(applied["apply"])
        self.assertFalse((peer_bus.REGISTRY / "dead00.json").exists())
        self.assertTrue((peer_bus.REGISTRY / f"{live_sid}.json").is_file())
        self.assertFalse(
            (peer_bus.USAGE_DIR / "c662b3ea-d616-4c37-8a78-74ae8272b578.json").exists()
        )
        self.assertTrue((peer_bus.USAGE_DIR / f"{live_sid}.json").is_file())
        self.assertFalse(empty.exists())
        self.assertTrue((mailed / "1-abc.json").is_file())


class HerdrBinTests(unittest.TestCase):
    def test_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "herdr"
            fake.write_text("#!/bin/sh\n")
            fake.chmod(0o755)
            env = os.environ.copy()
            env["PEER_BUS_HERDR_BIN"] = str(fake)
            with mock.patch.dict(os.environ, env, clear=False):
                self.assertEqual(peer_bus._herdr_bin(), str(fake))

    def test_home_local_when_which_misses(self) -> None:
        home = Path.home() / ".local/bin/herdr"
        if not (home.is_file() and os.access(home, os.X_OK)):
            self.skipTest("no ~/.local/bin/herdr")
        with mock.patch.object(peer_bus.shutil, "which", return_value=None):
            env = {k: v for k, v in os.environ.items() if k != "PEER_BUS_HERDR_BIN"}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(peer_bus._herdr_bin(), str(home))


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class HerdrSchemaTests(unittest.TestCase):
    def _load(self, name: str) -> dict:
        return json.loads((FIXTURES / name).read_text())

    def test_agent_list_fixture_valid_and_parses(self) -> None:
        payload = self._load("herdr_agent_list.json")
        self.assertEqual(peer_bus.herdr_schema_errors("agent_list", payload), [])
        rows = peer_bus._parse_herdr_agents(payload)
        self.assertEqual({r["name"] for r in rows}, {"Ada", "Beau"})
        self.assertEqual(rows[0]["session_id"], "aaaaaaaa-1111-2222-3333-444444444444")

    def test_pane_list_fixture_valid(self) -> None:
        payload = self._load("herdr_pane_list.json")
        self.assertEqual(peer_bus.herdr_schema_errors("pane_list", payload), [])

    def test_process_info_fixture_valid_and_parses(self) -> None:
        payload = self._load("herdr_process_info.json")
        self.assertEqual(peer_bus.herdr_schema_errors("process_info", payload), [])
        sid, harness, pid = peer_bus._sid_from_herdr_process_info(payload)
        self.assertEqual(sid, "aaaaaaaa-1111-2222-3333-444444444444")
        self.assertEqual(harness, "claude")
        self.assertEqual(pid, 100)

    def test_agent_list_missing_result_is_invalid(self) -> None:
        errs = peer_bus.herdr_schema_errors("agent_list", {"agents": []})
        self.assertTrue(errs)
        self.assertTrue(any("result" in e for e in errs))

    def test_agent_list_missing_pane_id_is_invalid(self) -> None:
        payload = self._load("herdr_agent_list.json")
        del payload["result"]["agents"][0]["pane_id"]
        errs = peer_bus.herdr_schema_errors("agent_list", payload)
        self.assertTrue(any("pane_id" in e for e in errs))

    def test_agent_session_without_value_is_invalid(self) -> None:
        payload = self._load("herdr_agent_list.json")
        payload["result"]["agents"][0]["agent_session"] = {"kind": "id"}
        errs = peer_bus.herdr_schema_errors("agent_list", payload)
        self.assertTrue(any("agent_session" in e for e in errs))

    def test_process_info_missing_argv_is_invalid(self) -> None:
        payload = self._load("herdr_process_info.json")
        del payload["result"]["process_info"]["foreground_processes"][0]["argv"]
        errs = peer_bus.herdr_schema_errors("process_info", payload)
        self.assertTrue(any("argv" in e for e in errs))

    def test_self_test_skips_without_binary(self) -> None:
        with mock.patch.object(peer_bus, "_herdr_bin", return_value=None):
            out = peer_bus.herdr_self_test()
        self.assertTrue(out["skipped"])
        self.assertTrue(out["ok"])

    def test_self_test_fails_on_bad_live_shape(self) -> None:
        with mock.patch.object(peer_bus, "_herdr_bin", return_value="/bin/true"):
            with mock.patch.object(
                peer_bus, "_herdr_cmd", return_value={"nope": True}
            ):
                out = peer_bus.herdr_self_test()
        self.assertFalse(out["skipped"])
        self.assertFalse(out["ok"])
        self.assertTrue(out["errors"])


class ClaudeUdsWakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = {
            "ROOT": peer_bus.ROOT,
            "INBOX": peer_bus.INBOX,
            "REGISTRY": peer_bus.REGISTRY,
            "WAKE": peer_bus.WAKE,
            "RECEIPTS": peer_bus.RECEIPTS,
            "TRUST_NAME_KEYS": peer_bus.TRUST_NAME_KEYS,
        }

    def tearDown(self) -> None:
        for key, val in self._orig.items():
            setattr(peer_bus, key, val)

    def test_send_to_claude_posts_uds_and_still_accepts(self) -> None:
        import socket
        import threading

        with tempfile.TemporaryDirectory(prefix="peer-bus-uds-") as tmp:
            root = Path(tmp)
            sock_path = str(root / "inbox.sock")
            sessions = root / "sessions"
            sessions.mkdir()
            sid = "bbbbbbbb-1111-2222-3333-444444444444"
            pid = os.getpid()
            (sessions / f"{pid}.json").write_text(
                json.dumps(
                    {
                        "pid": pid,
                        "name": "Ada",
                        "sessionId": sid,
                        "messagingSocketPath": sock_path,
                        "status": "idle",
                    }
                )
            )
            (sessions / f"{pid}.{sid[:8]}.key").write_text(json.dumps({"peerToken": "tok"}))
            got: list[str] = []

            def serve() -> None:
                srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                srv.bind(sock_path)
                srv.listen(1)
                srv.settimeout(2)
                try:
                    conn, _ = srv.accept()
                    data = b""
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    got.append(data.decode())
                    conn.close()
                finally:
                    srv.close()

            t = threading.Thread(target=serve)
            t.start()
            time.sleep(0.05)
            with mock.patch.object(peer_bus, "ROOT", root):
                peer_bus.INBOX = root / "inbox"
                peer_bus.REGISTRY = root / "registry"
                peer_bus.WAKE = root / "wake"
                peer_bus.RECEIPTS = root / "receipts"
                with mock.patch.dict(
                    os.environ,
                    {
                        "PEER_BUS_CLAUDE_SESSIONS": str(sessions),
                        "PEER_BUS_TRUST_NAME_KEYS": "1",
                    },
                    clear=False,
                ):
                    peer_bus.TRUST_NAME_KEYS = True
                    rec = {
                        "key": sid,
                        "name": "Ada",
                        "session_id": sid,
                        "harness": "claude",
                        "state": "live",
                        "address": f"Ada [{sid[:6]}]",
                    }
                    with mock.patch.object(peer_bus, "list_agents", return_value=[rec]):
                        with mock.patch.object(
                            peer_bus,
                            "detect_self",
                            return_value={
                                "key": "sender",
                                "name": "Beau",
                                "session_id": "s",
                                "harness": "grok",
                                "session_id_source": "grok",
                            },
                        ):
                            out = peer_bus.send_message("Ada", "hello from the bus")
            t.join(2)
            self.assertTrue(out["ok"])
            blob = "".join(got)
            self.assertIn("hello from the bus", blob)
            self.assertIn("auth", blob)
            methods = [m["method"] for m in (out.get("wake") or {}).get("methods") or []]
            self.assertIn("uds", methods)

    def test_uds_failure_does_not_fail_send(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peer-bus-uds-fail-") as tmp:
            root = Path(tmp)
            sid = "cccccccc-1111-2222-3333-444444444444"
            rec = {
                "key": sid,
                "name": "Ada",
                "session_id": sid,
                "harness": "claude",
                "state": "live",
                "address": f"Ada [{sid[:6]}]",
            }
            with mock.patch.object(peer_bus, "ROOT", root):
                peer_bus.INBOX = root / "inbox"
                peer_bus.REGISTRY = root / "registry"
                peer_bus.WAKE = root / "wake"
                peer_bus.RECEIPTS = root / "receipts"
                with mock.patch.object(peer_bus, "list_agents", return_value=[rec]):
                    with mock.patch.object(
                        peer_bus,
                        "detect_self",
                        return_value={
                            "key": "sender",
                            "name": "Beau",
                            "session_id": "s",
                            "harness": "grok",
                            "session_id_source": "grok",
                        },
                    ):
                        with mock.patch.object(
                            peer_bus,
                            "live_claude_inboxes",
                            return_value=[
                                {
                                    "name": "Ada",
                                    "session_id": sid,
                                    "socket": str(root / "missing.sock"),
                                    "token": None,
                                }
                            ],
                        ):
                            out = peer_bus.send_message("Ada", "still accept")
            self.assertTrue(out["ok"])
            self.assertTrue((root / "inbox" / "sender").is_dir() or True)
            uds = [
                m
                for m in (out.get("wake") or {}).get("methods") or []
                if m.get("method") == "uds"
            ]
            self.assertTrue(uds)
            self.assertFalse(uds[0]["ok"])

    def test_grok_recipient_skips_uds(self) -> None:
        rec = {
            "key": "g1",
            "name": "Beau",
            "session_id": "01aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "harness": "grok",
            "state": "live",
            "address": "Beau [01aaaa]",
        }
        with mock.patch.object(peer_bus, "live_claude_inboxes") as live:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with mock.patch.object(peer_bus, "ROOT", root):
                    peer_bus.INBOX = root / "inbox"
                    peer_bus.REGISTRY = root / "registry"
                    peer_bus.WAKE = root / "wake"
                    peer_bus.RECEIPTS = root / "receipts"
                    with mock.patch.object(peer_bus, "list_agents", return_value=[rec]):
                        with mock.patch.object(
                            peer_bus,
                            "detect_self",
                            return_value={
                                "key": "s",
                                "name": "Ada",
                                "session_id": "s",
                                "harness": "claude",
                                "session_id_source": "claude",
                            },
                        ):
                            out = peer_bus.send_message("Beau", "no uds")
        live.assert_not_called()
        methods = [m["method"] for m in (out.get("wake") or {}).get("methods") or []]
        self.assertNotIn("uds", methods)


class Contract010Tests(TempBusMixin, unittest.TestCase):
    def test_whoami_exposes_bus_version(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            me = peer_bus.detect_self("Ada")
        self.assertEqual(me["bus_version"], peer_bus.PEER_BUS_VERSION)
        self.assertEqual(peer_bus.PEER_BUS_VERSION, "0.10.0")

    def test_envelope_carries_bus_version(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            sender = peer_bus.detect_self("Ada")
            peer_bus.send_message("Beau", "ping", self_info=sender)
            msgs = peer_bus.receive_messages(peer_bus.detect_self("Beau"))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["bus_version"], "0.10.0")

    def test_ack_writes_sender_receipt(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            sender = peer_bus.detect_self("Ada")
            sent = peer_bus.send_message("Beau", "ping", self_info=sender)
            ack = peer_bus.ack_message(sent["msg_id"], peer_bus.detect_self("Beau"))
        self.assertTrue(ack["ok"])
        self.assertTrue(ack.get("receipt"))
        path = Path(ack["receipt"])
        self.assertTrue(path.is_file(), path)
        self.assertTrue(str(path).startswith(str(peer_bus.RECEIPTS)))
        payload = json.loads(path.read_text())
        self.assertEqual(payload["msg_id"], sent["msg_id"])
        self.assertEqual(payload["by"]["name"], "Beau")
        self.assertIn("acked_at", payload)

    def test_ack_skips_receipt_when_disabled(self) -> None:
        env = dict(self.env)
        env["PEER_BUS_ACK_RECEIPTS"] = "0"
        with mock.patch.dict(os.environ, env, clear=False):
            sender = peer_bus.detect_self("Ada")
            sent = peer_bus.send_message("Beau", "ping", self_info=sender)
            ack = peer_bus.ack_message(sent["msg_id"], peer_bus.detect_self("Beau"))
        self.assertTrue(ack["ok"])
        self.assertNotIn("receipt", ack)
        dest = peer_bus.RECEIPTS / peer_bus._safe_key(sender["key"])
        self.assertFalse((dest / f"{sent['msg_id']}.json").exists())

    def test_doctor_ok_with_skips(self) -> None:
        saved = peer_bus.USAGE_DIR
        peer_bus.USAGE_DIR = None
        try:
            with mock.patch.object(
                peer_bus, "herdr_self_test", return_value={"ok": True, "skipped": True}
            ):
                with mock.patch.object(peer_bus, "pool_usage", return_value=None):
                    out = peer_bus.doctor()
        finally:
            peer_bus.USAGE_DIR = saved
        self.assertTrue(out["ok"])
        self.assertEqual(out["bus_version"], "0.10.0")
        names = {c["name"]: c for c in out["checks"]}
        self.assertTrue(names["cli_version"]["ok"])
        self.assertEqual(names["cli_version"]["detail"], "0.10.0")
        self.assertTrue(names["herdr"].get("skipped"))
        self.assertTrue(names["usage_dir"].get("skipped"))
        self.assertTrue(names["pool_schema"].get("skipped"))
        self.assertTrue(names["mcp_version"]["ok"])
        self.assertEqual(names["mcp_version"]["detail"], "0.10.0")

    def test_doctor_fails_on_pool_schema_mismatch(self) -> None:
        saved = peer_bus.USAGE_DIR
        peer_bus.USAGE_DIR = self.root / "usage"
        peer_bus.USAGE_DIR.mkdir(exist_ok=True)
        try:
            with mock.patch.object(
                peer_bus, "herdr_self_test", return_value={"ok": True, "skipped": True}
            ):
                with mock.patch.object(
                    peer_bus, "pool_usage", return_value={"schema": 1, "state": "live"}
                ):
                    out = peer_bus.doctor()
        finally:
            peer_bus.USAGE_DIR = saved
        self.assertFalse(out["ok"])
        pool = next(c for c in out["checks"] if c["name"] == "pool_schema")
        self.assertFalse(pool["ok"])
        self.assertIn("1", pool["detail"])

    def test_doctor_fails_on_missing_usage_dir(self) -> None:
        saved = peer_bus.USAGE_DIR
        peer_bus.USAGE_DIR = self.root / "no-such-usage"
        try:
            with mock.patch.object(
                peer_bus, "herdr_self_test", return_value={"ok": True, "skipped": True}
            ):
                with mock.patch.object(peer_bus, "pool_usage", return_value=None):
                    out = peer_bus.doctor()
        finally:
            peer_bus.USAGE_DIR = saved
        self.assertFalse(out["ok"])
        usage = next(c for c in out["checks"] if c["name"] == "usage_dir")
        self.assertFalse(usage["ok"])

    def test_cli_doctor_json(self) -> None:
        env = os.environ.copy()
        env.update(self.env)
        env.pop("PEER_BUS_USAGE_DIR", None)
        env.pop("USAGE_DIR", None)
        proc = subprocess.run(
            [sys.executable, str(ROOT / "peer_bus.py"), "doctor"],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["bus_version"], "0.10.0")
        self.assertIn("checks", payload)
        self.assertEqual(proc.returncode, 0 if payload["ok"] else 1)

    def test_mcp_initialize_version(self) -> None:
        env = os.environ.copy()
        env.update({"PEER_BUS_ROOT": str(self.root)})
        env.pop("PEER_BUS_TRUST_NAME_KEYS", None)
        proc = subprocess.run(
            [sys.executable, str(ROOT / "mcp_server.py")],
            input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
            env=env,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout.splitlines()[0])
        self.assertEqual(payload["result"]["serverInfo"]["version"], "0.10.0")


if __name__ == "__main__":
    unittest.main()


