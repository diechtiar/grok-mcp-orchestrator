#!/usr/bin/env python3
"""stdlib unittest coverage for peer-bus core helpers and CLI verbs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import peer_bus  # noqa: E402


class SafeKeyTests(unittest.TestCase):
    def test_strips_traversal(self) -> None:
        self.assertNotIn("..", peer_bus._safe_key("../etc/passwd"))
        self.assertEqual(peer_bus._safe_key("Alice"), "alice")

    def test_empty_becomes_anon(self) -> None:
        self.assertEqual(peer_bus._safe_key(""), "anon")


class ClaudeSessionIdTests(unittest.TestCase):
    def test_prefers_claude_code_session_id(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CLAUDE_CODE_SESSION_ID": "code-1", "CLAUDE_SESSION_ID": "legacy-1"},
            clear=False,
        ):
            # clear grok id for this check
            os.environ.pop("GROK_SESSION_ID", None)
            self.assertEqual(peer_bus._claude_session_id(), "code-1")
            self.assertEqual(peer_bus._session_id(), "code-1")

    def test_falls_back_to_claude_session_id(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in {
            "GROK_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "PEER_BUS_SESSION_ID"
        }}
        env["CLAUDE_SESSION_ID"] = "legacy-2"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(peer_bus._session_id(), "legacy-2")


class TempBusTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="peer-bus-ut-")
        self.root = Path(self._tmpdir.name)
        self.env = {
            "PEER_BUS_ROOT": str(self.root),
            "PEER_BUS_TRUST_NAME_KEYS": "1",
            "PEER_BUS_WAKE_DROP": "1",
            "PEER_BUS_HARNESS": "test",
        }
        # Reload module paths bound at import time — re-bind for this process
        peer_bus.ROOT = self.root.resolve()
        peer_bus.INBOX = peer_bus.ROOT / "inbox"
        peer_bus.REGISTRY = peer_bus.ROOT / "registry"
        peer_bus.WAKE = peer_bus.ROOT / "wake"
        peer_bus.TRUST_NAME_KEYS = True
        peer_bus.WAKE_DROP = True
        peer_bus.WAKE_ENABLED = False
        peer_bus.WAKE_CMD = ""
        peer_bus._WAKE_CALLBACK = None
        peer_bus._ensure_dirs()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

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


if __name__ == "__main__":
    unittest.main()
