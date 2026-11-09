"""Tests for the --machine answer envelope."""

import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.machine_mode import (
    EXIT_CONNECTION,
    EXIT_OK,
    EXIT_OPERATION,
    EXIT_UNSUPPORTED,
    EXIT_USAGE,
    MachineError,
    MachineSession,
    run_command,
)


class TestExitStatuses(unittest.TestCase):
    def test_ok_is_zero(self):
        self.assertEqual(EXIT_OK, 0)

    def test_usage_is_two(self):
        self.assertEqual(EXIT_USAGE, 2)

    def test_statuses_are_distinct(self):
        statuses = {
            EXIT_OK, EXIT_USAGE, 3, EXIT_CONNECTION, 5, 6,
            EXIT_OPERATION, 8, EXIT_UNSUPPORTED,
        }
        self.assertEqual(len(statuses), 9)


class TestMachineError(unittest.TestCase):
    def test_default_status_is_operation(self):
        e = MachineError("fail")
        self.assertEqual(e.status, EXIT_OPERATION)
        self.assertEqual(e.code, "operation_failed")

    def test_explicit_status(self):
        e = MachineError("not found", 5, "not_found")
        self.assertEqual(e.status, 5)
        self.assertEqual(e.code, "not_found")


class TestMachineSession(unittest.TestCase):
    def test_succeed_writes_ok(self):
        buf = io.StringIO()
        s = MachineSession("test", stdout=buf)
        status = s.succeed({"key": "value"})
        self.assertEqual(status, EXIT_OK)
        obj = json.loads(buf.getvalue())
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["command"], "test")
        self.assertEqual(obj["data"]["key"], "value")

    def test_fail_writes_error(self):
        buf = io.StringIO()
        s = MachineSession("test", stdout=buf)
        status = s.fail(MachineError("bad", EXIT_USAGE, "usage"))
        self.assertEqual(status, EXIT_USAGE)
        obj = json.loads(buf.getvalue())
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["code"], "usage")
        self.assertIn("bad", obj["message"])

    def test_progress_writes_event(self):
        buf = io.StringIO()
        s = MachineSession("test", stdout=buf)
        s.progress("download", "fetching", percent=45)
        obj = json.loads(buf.getvalue())
        self.assertEqual(obj["event"], "progress")
        self.assertEqual(obj["stage"], "download")
        self.assertEqual(obj["percent"], 45)


class TestRunCommand(unittest.TestCase):
    def setUp(self):
        self.app = MagicMock(spec=[])
        self.buf = io.StringIO()

    def test_machine_info(self):
        session = MachineSession("machine info", stdout=self.buf)
        status = run_command(self.app, "machine info", session)
        self.assertEqual(status, EXIT_OK)
        obj = json.loads(self.buf.getvalue())
        self.assertTrue(obj["ok"])
        self.assertIn("machine", obj["data"]["commands"])

    def test_empty_command(self):
        session = MachineSession("", stdout=self.buf)
        with self.assertRaises(MachineError) as ctx:
            run_command(self.app, "", session)
        self.assertEqual(ctx.exception.status, EXIT_USAGE)

    def test_known_command_without_machine_form(self):
        self.app.do_start = lambda _: None
        session = MachineSession("start", stdout=self.buf)
        with self.assertRaises(MachineError) as ctx:
            run_command(self.app, "start", session)
        self.assertEqual(ctx.exception.status, EXIT_UNSUPPORTED)

    def test_unknown_command(self):
        session = MachineSession("nope", stdout=self.buf)
        with self.assertRaises(MachineError) as ctx:
            run_command(self.app, "nope", session)
        self.assertEqual(ctx.exception.status, EXIT_USAGE)


class TestMainIntegration(unittest.TestCase):
    """--machine flag reaches the envelope from the command line."""

    @patch("vmanager.vmanager_cmd.VManagerCMD")
    @patch("vmanager.vmanager_cmd.register_error_handler")
    def test_machine_info(self, _reg, mock_cmd_cls):
        mock_cmd = MagicMock()
        mock_cmd._sanitize_message = lambda x: x
        mock_cmd.active_connections = {"local": MagicMock()}
        mock_cmd_cls.return_value = mock_cmd

        from vmanager.vmanager_cmd import main

        with patch.object(sys, "argv", ["vmc", "--machine", "-c", "machine info"]):
            status = main()
        self.assertEqual(status, EXIT_OK)

    @patch("vmanager.vmanager_cmd.VManagerCMD")
    @patch("vmanager.vmanager_cmd.register_error_handler")
    def test_machine_without_command_is_usage(self, _reg, mock_cmd_cls):
        mock_cmd = MagicMock()
        mock_cmd_cls.return_value = mock_cmd

        from vmanager.vmanager_cmd import main

        with patch.object(sys, "argv", ["vmc", "--machine"]):
            status = main()
        self.assertEqual(status, EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
