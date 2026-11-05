"""
Unit tests for the QEMU guest agent service layer (vmanager.guest_agent).
"""

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import libvirt

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager import guest_agent
from vmanager.guest_agent import (
    GuestAgentError,
    UploadCancelled,
    agent_status,
    ga_command,
    ga_exec,
    ga_exec_status,
    ga_exec_wait,
    ga_fsfreeze_freeze,
    ga_fsfreeze_status,
    ga_fsfreeze_thaw,
    ga_fsinfo,
    ga_fstrim,
    ga_info,
    ga_ping,
    ga_set_time,
    ga_set_user_password,
    ga_shutdown,
    ga_ssh_add_authorized_keys,
    ga_ssh_get_authorized_keys,
    ga_ssh_remove_authorized_keys,
    ga_supported,
    ga_suspend,
    ga_upload_file,
    ga_uptime,
)


def _make_domain(name="test-vm", active=1):
    domain = MagicMock(spec=libvirt.virDomain)
    domain.name.return_value = name
    domain.isActive.return_value = active
    return domain


def _reply_json(reply):
    return json.dumps(reply)


class TestGaCommand(unittest.TestCase):
    @patch.object(guest_agent, "libvirt_qemu")
    def test_success_returns_return_value(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": "pong"})
        result = ga_command(_make_domain(), "guest-ping")
        self.assertEqual(result, "pong")
        args = mock_lq.qemuAgentCommand.call_args[0]
        self.assertEqual(json.loads(args[1])["execute"], "guest-ping")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_arguments_are_forwarded(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": 42})
        ga_command(_make_domain(), "guest-exec", arguments={"path": "/bin/true"})
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["arguments"], {"path": "/bin/true"})

    def _assert_libvirt_error_mapping(self, message, expected_reason):
        with patch.object(guest_agent, "libvirt_qemu") as mock_lq:
            mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(message)
            with self.assertRaises(GuestAgentError) as ctx:
                ga_command(_make_domain(), "guest-ping")
            self.assertEqual(ctx.exception.reason, expected_reason)

    def test_error_not_configured(self):
        self._assert_libvirt_error_mapping("QEMU guest agent is not configured", "not_configured")

    def test_error_not_connected(self):
        self._assert_libvirt_error_mapping("QEMU guest agent is not connected", "not_connected")

    def test_error_agent_error_state(self):
        self._assert_libvirt_error_mapping(
            "QEMU guest agent is not available due to an error", "not_connected"
        )

    def test_error_sync_timeout(self):
        self._assert_libvirt_error_mapping(
            "guest agent didn't respond to synchronization within '5' seconds", "timeout"
        )

    def test_error_command_timeout(self):
        self._assert_libvirt_error_mapping(
            "guest agent didn't respond to command within '10' seconds", "timeout"
        )

    def test_error_disappeared(self):
        self._assert_libvirt_error_mapping(
            "Guest agent disappeared while executing command", "timeout"
        )

    def test_error_vm_not_running(self):
        self._assert_libvirt_error_mapping("domain is not running", "vm_not_running")

    def test_error_command_failed(self):
        self._assert_libvirt_error_mapping(
            "unable to execute QEMU agent command 'guest-exec': GuestExecvError: boom",
            "command_failed",
        )

    def test_error_command_not_found(self):
        self._assert_libvirt_error_mapping(
            "unable to execute QEMU agent command 'guest-ssh-add-authorized-keys': "
            "CommandNotFound: command not found",
            "unsupported",
        )

    def test_error_command_not_found_libvirt_wording(self):
        # QEMU >= 10 agents report removed commands this way
        self._assert_libvirt_error_mapping(
            "unable to execute QEMU agent command 'guest-supported': "
            "The command guest-supported has not been found",
            "unsupported",
        )

    @patch.object(guest_agent, "libvirt_qemu")
    def test_json_error_reply(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json(
            {"error": {"class": "GenericError", "desc": "boom"}}
        )
        with self.assertRaises(GuestAgentError) as ctx:
            ga_command(_make_domain(), "guest-ping")
        self.assertEqual(ctx.exception.reason, "command_failed")
        self.assertIn("boom", str(ctx.exception))

    @patch.object(guest_agent, "libvirt_qemu")
    def test_json_error_reply_unsupported(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json(
            {"error": {"class": "CommandNotFound", "desc": "nope"}}
        )
        with self.assertRaises(GuestAgentError) as ctx:
            ga_command(_make_domain(), "guest-ping")
        self.assertEqual(ctx.exception.reason, "unsupported")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_invalid_json_reply(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = "not-json"
        with self.assertRaises(GuestAgentError):
            ga_command(_make_domain(), "guest-ping")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_reply_without_return_or_error(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"foo": "bar"})
        with self.assertRaises(GuestAgentError):
            ga_command(_make_domain(), "guest-ping")


class TestPingAndStatus(unittest.TestCase):
    @patch.object(guest_agent, "libvirt_qemu")
    def test_ping_true(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        self.assertTrue(ga_ping(_make_domain()))

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ping_false(self, mock_lq):
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "QEMU guest agent is not connected"
        )
        self.assertFalse(ga_ping(_make_domain()))

    def test_status_not_running(self):
        self.assertEqual(agent_status(_make_domain(active=0)), "not_running")

    @patch.object(guest_agent, "get_vm_devices_info")
    @patch.object(guest_agent, "_get_domain_root")
    def test_status_not_configured(self, mock_root, mock_devices):
        mock_root.return_value = ("xml", MagicMock())
        mock_devices.return_value = {"qemu_guest_agent": False}
        self.assertEqual(agent_status(_make_domain()), "not_configured")

    @patch.object(guest_agent, "get_vm_devices_info")
    @patch.object(guest_agent, "_get_domain_root")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_status_running(self, mock_lq, mock_root, mock_devices):
        mock_root.return_value = ("xml", MagicMock())
        mock_devices.return_value = {"qemu_guest_agent": True}
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        self.assertEqual(agent_status(_make_domain()), "running")

    @patch.object(guest_agent, "get_vm_devices_info")
    @patch.object(guest_agent, "_get_domain_root")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_status_down(self, mock_lq, mock_root, mock_devices):
        mock_root.return_value = ("xml", MagicMock())
        mock_devices.return_value = {"qemu_guest_agent": True}
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "QEMU guest agent is not connected"
        )
        self.assertEqual(agent_status(_make_domain()), "down")


class TestGuestInfo(unittest.TestCase):
    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_info(self, mock_lq):
        # guest-info only carries agent metadata; the guest data comes
        # from the dedicated guest-get-* commands
        replies = {
            "guest-get-host-name": {"host-name": "guest"},
            "guest-get-osinfo": {
                "name": "NixOS",
                "pretty-name": "NixOS 25.05",
                "kernel-release": "6.12",
            },
            "guest-get-load": {"load1m": 0.1, "load5m": 0.2, "load15m": 0.3},
            "guest-file-open": 1000,
            "guest-file-read": {
                "count": 16,
                "buf-b64": base64.b64encode(b"3600.00 7200.00\n").decode(),
                "eof": True,
            },
            "guest-file-close": {},
        }

        def fake_qemu_agent_command(domain, cmd, timeout, flags):
            name = json.loads(cmd)["execute"]
            return _reply_json({"return": replies[name]})

        mock_lq.qemuAgentCommand.side_effect = fake_qemu_agent_command
        info = ga_info(_make_domain())
        self.assertEqual(info["hostname"], "guest")
        self.assertEqual(info["os"]["pretty_name"], "NixOS 25.05")
        self.assertEqual(info["load"], [0.1, 0.2, 0.3])
        self.assertEqual(info["uptime"], 3600)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_supported(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json(
            {
                "return": {
                    "version": "9.2.0",
                    "supported_commands": [
                        {"enabled": True, "name": "guest-ping", "success-response": True},
                        {"enabled": True, "name": "guest-exec", "success-response": True},
                    ],
                }
            }
        )
        supported = ga_supported(_make_domain())
        self.assertEqual(supported["version"], "9.2.0")
        self.assertIn("guest-exec", supported["commands"])
        # The command sent is guest-info (the only agent-info command)
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-info")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_fsinfo(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json(
            {
                "return": [
                    {
                        "name": "/dev/sda1",
                        "mountpoint": "/",
                        "type": "ext4",
                        "total_bytes": 1073741824,
                        "used_bytes": 536870912,
                    }
                ]
            }
        )
        fs = ga_fsinfo(_make_domain())
        self.assertEqual(fs[0]["mountpoint"], "/")
        self.assertEqual(fs[0]["total_bytes"], 1073741824)


class TestGuestAgentCompat(unittest.TestCase):
    """The service layer must only use commands that exist in the QGA
    protocol (guest-info for agent metadata, split guest-get-* for guest
    info) and skip commands that are missing on older agents."""

    def _not_found(self, name):
        return libvirt.libvirtError(
            f"unable to execute QEMU agent command '{name}': " "CommandNotFound: command not found"
        )

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_supported_calls_guest_info_directly(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json(
            {
                "return": {
                    "version": "10.1.0",
                    "supported_commands": [
                        {"enabled": True, "name": "guest-ping", "success-response": True},
                        {"enabled": True, "name": "guest-exec", "success-response": True},
                        {
                            "enabled": False,
                            "name": "guest-file-open",
                            "success-response": True,
                        },
                    ],
                }
            }
        )
        supported = ga_supported(_make_domain())
        self.assertEqual(supported["version"], "10.1.0")
        self.assertEqual(supported["name"], "qemu-ga")
        # disabled commands are not listed
        self.assertEqual(supported["commands"], ["guest-ping", "guest-exec"])
        # A single round-trip: no probe of a nonexistent command first
        self.assertEqual(mock_lq.qemuAgentCommand.call_count, 1)
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-info")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_supported_other_error_not_swallowed(self, mock_lq):
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "QEMU guest agent is not connected"
        )
        with self.assertRaises(GuestAgentError) as ctx:
            ga_supported(_make_domain())
        self.assertEqual(ctx.exception.reason, "not_connected")
        self.assertEqual(mock_lq.qemuAgentCommand.call_count, 1)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_fsinfo_kebab_keys_converted(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json(
            {
                "return": [
                    {
                        "name": "vda3",
                        "mountpoint": "/",
                        "type": "btrfs",
                        "total-bytes": 28961636352,
                        "used-bytes": 1074139136,
                    }
                ]
            }
        )
        fs = ga_fsinfo(_make_domain())
        self.assertEqual(fs[0]["total_bytes"], 28961636352)
        self.assertEqual(fs[0]["used_bytes"], 1074139136)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_info_uses_split_commands(self, mock_lq):
        replies = {
            "guest-get-host-name": {"host-name": "fedora"},
            "guest-get-osinfo": {
                "name": "Fedora Linux",
                "pretty-name": "Fedora Linux 43 (Forty Three)",
                "kernel-release": "6.17.1",
                "id": "fedora",
            },
            "guest-get-load": {"load1m": 0.1, "load5m": 0.2, "load15m": 0.3},
            "guest-file-open": 1000,
            "guest-file-read": {
                "count": 19,
                "buf-b64": base64.b64encode(b"77002.76 147870.67\n").decode(),
                "eof": True,
            },
            "guest-file-close": {},
        }

        def fake_qemu_agent_command(domain, cmd, timeout, flags):
            name = json.loads(cmd)["execute"]
            return _reply_json({"return": replies[name]})

        mock_lq.qemuAgentCommand.side_effect = fake_qemu_agent_command
        info = ga_info(_make_domain())
        self.assertEqual(info["hostname"], "fedora")
        self.assertEqual(info["os"]["pretty_name"], "Fedora Linux 43 (Forty Three)")
        self.assertEqual(info["os"]["kernel_release"], "6.17.1")
        self.assertEqual(info["load"], [0.1, 0.2, 0.3])
        # The protocol has no uptime command: read from /proc/uptime
        self.assertEqual(info["uptime"], 77002)
        self.assertEqual(info["uptime_format"], "21:23:22")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_uptime_reads_proc_uptime(self, mock_lq):
        def fake_qemu_agent_command(domain, cmd, timeout, flags):
            name = json.loads(cmd)["execute"]
            if name == "guest-file-open":
                return _reply_json({"return": 1000})
            if name == "guest-file-read":
                data = base64.b64encode(b"77002.76 147870.67\n").decode()
                return _reply_json({"return": {"count": 19, "buf-b64": data, "eof": True}})
            if name == "guest-file-close":
                return _reply_json({"return": {}})
            raise AssertionError(f"unexpected command {name}")

        mock_lq.qemuAgentCommand.side_effect = fake_qemu_agent_command
        self.assertAlmostEqual(ga_uptime(_make_domain()), 77002.76)
        # handle must be closed
        last_cmd = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(last_cmd["execute"], "guest-file-close")
        self.assertEqual(last_cmd["arguments"]["handle"], 1000)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_uptime_open_fails_returns_none(self, mock_lq):
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "unable to execute QEMU agent command 'guest-file-open': "
            "GenericError: No such file or directory"
        )
        self.assertIsNone(ga_uptime(_make_domain()))

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_uptime_closes_handle_on_read_error(self, mock_lq):
        calls = []

        def fake_qemu_agent_command(domain, cmd, timeout, flags):
            name = json.loads(cmd)["execute"]
            calls.append(name)
            if name == "guest-file-open":
                return _reply_json({"return": 42})
            if name == "guest-file-read":
                raise libvirt.libvirtError(
                    "unable to execute QEMU agent command 'guest-file-read': GenericError: boom"
                )
            if name == "guest-file-close":
                return _reply_json({"return": {}})
            raise AssertionError(f"unexpected command {name}")

        mock_lq.qemuAgentCommand.side_effect = fake_qemu_agent_command
        self.assertIsNone(ga_uptime(_make_domain()))
        self.assertEqual(calls, ["guest-file-open", "guest-file-read", "guest-file-close"])

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_info_split_command_missing_is_skipped(self, mock_lq):
        def fake_qemu_agent_command(domain, cmd, timeout, flags):
            name = json.loads(cmd)["execute"]
            if name == "guest-get-host-name":
                raise self._not_found(name)
            if name == "guest-get-osinfo":
                return _reply_json({"return": {"name": "Fedora", "id": "fedora"}})
            if name == "guest-get-load":
                return _reply_json({"return": {"load1m": 0, "load5m": 0, "load15m": 0}})
            if name == "guest-file-open":
                raise self._not_found(name)
            raise AssertionError(f"unexpected command {name}")

        mock_lq.qemuAgentCommand.side_effect = fake_qemu_agent_command
        info = ga_info(_make_domain())
        self.assertEqual(info["hostname"], "")
        self.assertEqual(info["os"]["name"], "Fedora")
        self.assertEqual(info["load"], [0, 0, 0])

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_info_split_other_error_propagates(self, mock_lq):
        def fake_qemu_agent_command(domain, cmd, timeout, flags):
            name = json.loads(cmd)["execute"]
            if name == "guest-get-host-name":
                return _reply_json({"return": {"host-name": "fedora"}})
            if name == "guest-get-osinfo":
                raise libvirt.libvirtError(
                    "unable to execute QEMU agent command 'guest-get-osinfo': " "GenericError: boom"
                )
            if name == "guest-file-open":
                raise self._not_found(name)
            raise AssertionError(f"unexpected command {name}")

        mock_lq.qemuAgentCommand.side_effect = fake_qemu_agent_command
        with self.assertRaises(GuestAgentError) as ctx:
            ga_info(_make_domain())
        self.assertEqual(ctx.exception.reason, "command_failed")


class TestGuestExec(unittest.TestCase):
    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_exec_builds_arguments(self, mock_lq):
        # Real QGA schema (stable since 2.5): path/arg/env/capture-output,
        # {'pid': N} reply, no timeout or cwd in the protocol
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": {"pid": 1234}})
        pid = ga_exec(_make_domain(), "ls -la /tmp")
        self.assertEqual(pid, 1234)
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-exec")
        self.assertEqual(sent["arguments"]["path"], "ls")
        self.assertEqual(sent["arguments"]["arg"], ["-la", "/tmp"])
        self.assertTrue(sent["arguments"]["capture-output"])
        self.assertNotIn("timeout", sent["arguments"])
        self.assertNotIn("cwd", sent["arguments"])

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_exec_empty_command(self, mock_lq):
        with self.assertRaises(GuestAgentError):
            ga_exec(_make_domain(), "   ")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_exec_error_not_swallowed(self, mock_lq):
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "unable to execute QEMU agent command 'guest-exec': "
            "GuestExecvError: executable not found"
        )
        with self.assertRaises(GuestAgentError) as ctx:
            ga_exec(_make_domain(), "/nonexistent")
        self.assertEqual(ctx.exception.reason, "command_failed")
        # single round-trip, no retry
        self.assertEqual(mock_lq.qemuAgentCommand.call_count, 1)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_exec_status_kebab_keys_decoded(self, mock_lq):
        # Real QGA GuestExecStatus fields: out-data/err-data base64,
        # out-truncated/err-truncated booleans, no timed-out field
        mock_lq.qemuAgentCommand.return_value = _reply_json(
            {
                "return": {
                    "exited": True,
                    "exitcode": 0,
                    "out-data": base64.b64encode(b"hello").decode(),
                    "err-data": base64.b64encode(b"boom").decode(),
                    "out-truncated": False,
                    "err-truncated": False,
                }
            }
        )
        status = ga_exec_status(_make_domain(), 77)
        self.assertEqual(status["stdout"], "hello")
        self.assertEqual(status["stderr"], "boom")
        self.assertFalse(status["truncated"])
        self.assertIsNone(status["signal"])

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_exec_status_truncated_flag(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json(
            {
                "return": {
                    "exited": True,
                    "exitcode": 0,
                    "out-data": base64.b64encode(b"hello").decode(),
                    "out-truncated": True,
                }
            }
        )
        status = ga_exec_status(_make_domain(), 77)
        self.assertTrue(status["truncated"])

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_exec_status_signal(self, mock_lq):
        # Signal-killed process: no exitcode, signal number reported
        mock_lq.qemuAgentCommand.return_value = _reply_json(
            {"return": {"exited": True, "signal": 9, "out-data": "", "err-data": ""}}
        )
        status = ga_exec_status(_make_domain(), 77)
        self.assertIsNone(status["exitcode"])
        self.assertEqual(status["signal"], 9)

    @patch.object(guest_agent, "ga_exec_status")
    @patch.object(guest_agent, "ga_exec")
    def test_ga_exec_wait_polls_until_exited(self, mock_exec, mock_status):
        mock_exec.return_value = 77
        mock_status.side_effect = [
            {
                "exited": False,
                "exitcode": None,
                "signal": None,
                "stdout": "",
                "stderr": "",
                "truncated": False,
            },
            {
                "exited": True,
                "exitcode": 0,
                "signal": None,
                "stdout": "hello",
                "stderr": "",
                "truncated": False,
            },
        ]
        status = ga_exec_wait(_make_domain(), "echo hello", poll_interval=0.01)
        self.assertTrue(status["exited"])
        self.assertEqual(status["exitcode"], 0)
        self.assertEqual(status["stdout"], "hello")
        self.assertEqual(mock_status.call_count, 2)

    @patch.object(guest_agent, "ga_exec_status")
    @patch.object(guest_agent, "ga_exec")
    def test_ga_exec_wait_overall_timeout(self, mock_exec, mock_status):
        mock_exec.return_value = 77
        mock_status.return_value = {
            "exited": False,
            "exitcode": None,
            "signal": None,
            "stdout": "",
            "stderr": "",
            "truncated": False,
        }
        with self.assertRaises(GuestAgentError) as ctx:
            ga_exec_wait(_make_domain(), "sleep 100", poll_interval=0.01, overall_timeout=0.05)
        self.assertEqual(ctx.exception.reason, "unknown")


class TestGuestSSHKeys(unittest.TestCase):
    @patch.object(guest_agent, "libvirt_qemu")
    def test_get_keys(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json(
            {"return": {"keys": ["ssh-ed25519 AAAA user@host", "ssh-rsa BBBB other@host"]}}
        )
        keys = ga_ssh_get_authorized_keys(_make_domain(), "root")
        self.assertEqual(len(keys), 2)
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-ssh-get-authorized-keys")
        self.assertEqual(sent["arguments"]["username"], "root")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_get_keys_missing_file_returns_empty(self, mock_lq):
        # No authorized_keys file yet: agent reports ENOENT, we report no keys
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "guest agent command failed: unable to execute QEMU agent command "
            "'guest-ssh-get-authorized-keys': failed to read "
            "'/root/.ssh/authorized_keys': Failed to open file \"/root/.ssh/authorized_keys\": "
            "No such file or directory"
        )
        keys = ga_ssh_get_authorized_keys(_make_domain(), "root")
        self.assertEqual(keys, [])

    @patch.object(guest_agent, "libvirt_qemu")
    def test_get_keys_other_errors_still_raise(self, mock_lq):
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "guest agent command failed: unable to execute QEMU agent command "
            "'guest-ssh-get-authorized-keys': failed to read "
            "'/root/.ssh/authorized_keys': Failed to open file \"/root/.ssh/authorized_keys\": "
            "Permission denied"
        )
        with self.assertRaises(guest_agent.GuestAgentError):
            ga_ssh_get_authorized_keys(_make_domain(), "root")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_add_keys(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        ga_ssh_add_authorized_keys(_make_domain(), "alice", ["ssh-ed25519 AAAA a@b"])
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-ssh-add-authorized-keys")
        self.assertEqual(sent["arguments"]["username"], "alice")
        self.assertEqual(sent["arguments"]["keys"], ["ssh-ed25519 AAAA a@b"])

    @patch.object(guest_agent, "libvirt_qemu")
    def test_remove_keys(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        ga_ssh_remove_authorized_keys(_make_domain(), "alice", ["ssh-ed25519 AAAA a@b"])
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-ssh-remove-authorized-keys")
        self.assertEqual(sent["arguments"]["username"], "alice")
        self.assertEqual(sent["arguments"]["keys"], ["ssh-ed25519 AAAA a@b"])

    @patch.object(guest_agent, "libvirt_qemu")
    def test_add_keys_dir_not_a_directory_raises_clear_error(self, mock_lq):
        # The .ssh path exists but is not a directory: the agent cannot fix
        # that, so a clear, actionable error must be raised (no extra
        # commands are sent).
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "guest agent command failed: unable to execute QEMU agent command "
            "'guest-ssh-add-authorized-keys': failed to create directory "
            "'/root/.ssh': File exists"
        )
        with self.assertRaises(GuestAgentError) as ctx:
            ga_ssh_add_authorized_keys(_make_domain(), "root", ["ssh-ed25519 AAAA a@b"])
        self.assertIn("/root/.ssh", str(ctx.exception))
        self.assertEqual(mock_lq.qemuAgentCommand.call_count, 1)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_add_keys_other_errors_still_raise(self, mock_lq):
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "guest agent command failed: unable to execute QEMU agent command "
            "'guest-ssh-add-authorized-keys': failed to lookup user 'nosuch': No such user"
        )
        with self.assertRaises(GuestAgentError):
            ga_ssh_add_authorized_keys(_make_domain(), "nosuch", ["ssh-ed25519 AAAA a@b"])
        # Only the original command was sent
        self.assertEqual(mock_lq.qemuAgentCommand.call_count, 1)


class TestGuestUpload(unittest.TestCase):
    """ga_upload_file: chunked guest-file-write with flush/close and
    handle cleanup on error or cancel."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write_local_file(self, name: str, data: bytes) -> str:
        path = os.path.join(self.tmpdir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _fake_upload(self, written_chunks: list[bytes]):
        """ga_command fake that records the base64-decoded write payloads."""

        def fake(domain, name, arguments=None, timeout=10):
            if name == "guest-file-open":
                return 7
            if name == "guest-file-write":
                written_chunks.append(base64.b64decode(arguments["buf-b64"]))
                return {"count": len(arguments["buf-b64"]), "eof": False}
            if name in ("guest-file-flush", "guest-file-close"):
                return None
            raise AssertionError(f"unexpected command {name}")

        return fake

    @patch.object(guest_agent, "ga_command")
    def test_upload_chunked_writes_round_trip(self, mock_ga):
        # 600 KiB > 256 KiB chunk size: three writes, content intact
        data = os.urandom(600 * 1024)
        path = self._write_local_file("big.bin", data)
        written: list[bytes] = []
        mock_ga.side_effect = self._fake_upload(written)
        total = ga_upload_file(_make_domain(), path, "/tmp/big.bin")
        self.assertEqual(total, len(data))
        self.assertEqual(len(written), 3)
        self.assertEqual(b"".join(written), data)

    @patch.object(guest_agent, "ga_command")
    def test_upload_flush_before_close(self, mock_ga):
        path = self._write_local_file("f.bin", b"hello")
        mock_ga.side_effect = self._fake_upload([])
        ga_upload_file(_make_domain(), path, "/tmp/f.bin")
        names = [c.args[1] for c in mock_ga.call_args_list]
        self.assertEqual(
            names, ["guest-file-open", "guest-file-write", "guest-file-flush", "guest-file-close"]
        )
        # open arguments carry the destination path and the mode
        self.assertEqual(mock_ga.call_args_list[0].args[2], {"path": "/tmp/f.bin", "mode": "w"})
        self.assertEqual(mock_ga.call_args_list[-1].args[2], {"handle": 7})

    @patch.object(guest_agent, "ga_command")
    def test_upload_closes_handle_on_write_error(self, mock_ga):
        path = self._write_local_file("f.bin", b"hello")
        calls: list[str] = []

        def fake(domain, name, arguments=None, timeout=10):
            calls.append(name)
            if name == "guest-file-open":
                return 9
            if name == "guest-file-write":
                raise GuestAgentError("boom", reason="command_failed", command=name)
            if name == "guest-file-close":
                return None
            raise AssertionError(f"unexpected command {name}")

        mock_ga.side_effect = fake
        with self.assertRaises(GuestAgentError):
            ga_upload_file(_make_domain(), path, "/tmp/f.bin")
        self.assertEqual(calls, ["guest-file-open", "guest-file-write", "guest-file-close"])

    @patch.object(guest_agent, "ga_command")
    def test_upload_append_mode(self, mock_ga):
        path = self._write_local_file("f.bin", b"more")
        mock_ga.side_effect = self._fake_upload([])
        ga_upload_file(_make_domain(), path, "/var/log/app.log", mode="a")
        self.assertEqual(
            mock_ga.call_args_list[0].args[2], {"path": "/var/log/app.log", "mode": "a"}
        )

    @patch.object(guest_agent, "ga_command")
    def test_upload_empty_file(self, mock_ga):
        path = self._write_local_file("empty.bin", b"")
        written: list[bytes] = []
        mock_ga.side_effect = self._fake_upload(written)
        total = ga_upload_file(_make_domain(), path, "/tmp/empty.bin")
        self.assertEqual(total, 0)
        self.assertEqual(written, [])
        names = [c.args[1] for c in mock_ga.call_args_list]
        self.assertEqual(names, ["guest-file-open", "guest-file-flush", "guest-file-close"])

    @patch.object(guest_agent, "ga_command")
    def test_upload_progress_callback_totals(self, mock_ga):
        data = os.urandom(300 * 1024)
        path = self._write_local_file("p.bin", data)
        mock_ga.side_effect = self._fake_upload([])
        progress: list[tuple[int, int]] = []
        ga_upload_file(
            _make_domain(),
            path,
            "/tmp/p.bin",
            progress_callback=lambda w, t: progress.append((w, t)),
        )
        # cumulative totals, ending at the full file size
        self.assertEqual(progress[-1], (len(data), len(data)))
        self.assertEqual(progress[0][0], guest_agent.UPLOAD_CHUNK_SIZE)
        totals = [w for w, _ in progress]
        self.assertEqual(totals, sorted(totals))

    @patch.object(guest_agent, "ga_command")
    def test_upload_cancel_between_chunks(self, mock_ga):
        data = os.urandom(600 * 1024)
        path = self._write_local_file("c.bin", data)
        cancel = threading.Event()
        calls: list[str] = []
        written: list[bytes] = []

        def fake(domain, name, arguments=None, timeout=10):
            calls.append(name)
            if name == "guest-file-open":
                return 5
            if name == "guest-file-write":
                written.append(base64.b64decode(arguments["buf-b64"]))
                cancel.set()  # cancel after the first chunk
                return {"count": 1, "eof": False}
            if name == "guest-file-close":
                return None
            raise AssertionError(f"unexpected command {name}")

        mock_ga.side_effect = fake
        with self.assertRaises(UploadCancelled) as ctx:
            ga_upload_file(_make_domain(), path, "/tmp/c.bin", cancel_event=cancel)
        # one chunk written, then the handle is closed and no more writes
        self.assertEqual(len(written), 1)
        self.assertEqual(calls, ["guest-file-open", "guest-file-write", "guest-file-close"])
        self.assertEqual(ctx.exception.bytes_written, guest_agent.UPLOAD_CHUNK_SIZE)
        self.assertEqual(ctx.exception.total_bytes, len(data))

    @patch.object(guest_agent, "ga_command")
    def test_upload_open_failure_no_close(self, mock_ga):
        path = self._write_local_file("o.bin", b"x")
        mock_ga.side_effect = GuestAgentError(
            "failed to open file '/tmp/o.bin': No such file or directory",
            reason="command_failed",
            command="guest-file-open",
        )
        with self.assertRaises(GuestAgentError):
            ga_upload_file(_make_domain(), path, "/tmp/o.bin")
        # no handle was opened, so no close is sent
        mock_ga.assert_called_once()

    def test_upload_rejects_bad_mode(self):
        path = self._write_local_file("m.bin", b"x")
        with self.assertRaises(ValueError):
            ga_upload_file(_make_domain(), path, "/tmp/m.bin", mode="r")


class TestGuestOperations(unittest.TestCase):
    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_fstrim_returns_trimmed_bytes(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": {"trimmed": 1048576}})
        trimmed = ga_fstrim(_make_domain())
        self.assertEqual(trimmed, 1048576)
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-fstrim")
        self.assertNotIn("arguments", sent)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_fstrim_passes_min_max(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": {"trimmed": 0}})
        ga_fstrim(_make_domain(), minimum=1024, maximum=1 << 20)
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["arguments"], {"minimum": 1024, "maximum": 1 << 20})

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_fstrim_non_dict_reply(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        self.assertEqual(ga_fstrim(_make_domain()), 0)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_set_time_sends_nanoseconds(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        before = time.time_ns()
        ga_set_time(_make_domain())
        after = time.time_ns()
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-set-time")
        self.assertGreaterEqual(sent["arguments"]["time"], before)
        self.assertLessEqual(sent["arguments"]["time"], after)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_set_user_password_base64_always(self, mock_lq):
        # The agent requires a base64 encoded password (since QEMU 2.5);
        # it must be encoded unconditionally, with no version lookup
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        ga_set_user_password(_make_domain(), "alice", "s3cret")
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-set-user-password")
        self.assertEqual(sent["arguments"]["username"], "alice")
        self.assertEqual(base64.b64decode(sent["arguments"]["password"]).decode(), "s3cret")
        self.assertFalse(sent["arguments"]["crypted"])
        # No version lookup: exactly one QGA command was sent
        self.assertEqual(mock_lq.qemuAgentCommand.call_count, 1)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_set_user_password_unicode_base64(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        ga_set_user_password(_make_domain(), "alice", "p@ss wörd")
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(
            base64.b64decode(sent["arguments"]["password"]).decode("utf-8"), "p@ss wörd"
        )

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_set_user_password_crypted(self, mock_lq):
        # A crypted hash is base64 encoded as well (the agent always decodes)
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        ga_set_user_password(_make_domain(), "alice", "$6$hash", crypted=True)
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertTrue(sent["arguments"]["crypted"])
        self.assertEqual(base64.b64decode(sent["arguments"]["password"]).decode(), "$6$hash")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_fsfreeze_freeze(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": {"frozen": 3}})
        self.assertEqual(ga_fsfreeze_freeze(_make_domain()), 3)
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-fsfreeze-freeze")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_fsfreeze_thaw(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": {"thawed": 3}})
        self.assertEqual(ga_fsfreeze_thaw(_make_domain()), 3)
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-fsfreeze-thaw")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_fsfreeze_status(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": {"state": "frozen"}})
        self.assertEqual(ga_fsfreeze_status(_make_domain()), "frozen")
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-fsfreeze-status")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_fsfreeze_status_non_dict_reply(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        self.assertEqual(ga_fsfreeze_status(_make_domain()), "none")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_fsfreeze_error_propagates(self, mock_lq):
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "unable to execute QEMU agent command 'guest-fsfreeze-freeze': "
            "GuestFSError: fsfreeze failed"
        )
        with self.assertRaises(GuestAgentError) as ctx:
            ga_fsfreeze_freeze(_make_domain())
        self.assertEqual(ctx.exception.reason, "command_failed")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_shutdown_default_mode(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        ga_shutdown(_make_domain())
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-shutdown")
        self.assertEqual(sent["arguments"], {"mode": "powerdown"})

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_shutdown_reboot_mode(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        ga_shutdown(_make_domain(), mode="reboot")
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["arguments"], {"mode": "reboot"})

    def test_ga_shutdown_rejects_bad_mode(self):
        with self.assertRaises(ValueError):
            ga_shutdown(_make_domain(), mode="bogus")

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_suspend_ram(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        ga_suspend(_make_domain(), "ram")
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-suspend-ram")
        self.assertNotIn("arguments", sent)

    @patch.object(guest_agent, "libvirt_qemu")
    def test_ga_suspend_disk(self, mock_lq):
        mock_lq.qemuAgentCommand.return_value = _reply_json({"return": None})
        ga_suspend(_make_domain(), "disk")
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-suspend-disk")

    def test_ga_suspend_rejects_bad_mode(self):
        with self.assertRaises(ValueError):
            ga_suspend(_make_domain(), "bogus")


class _FakeTime:
    """Minimal fake of the time module for ga_suspend state polling."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class TestGuestSuspendVerification(unittest.TestCase):
    """The guest suspends before the agent can reply: libvirt reports an
    error while the operation succeeded. ga_suspend must verify the
    domain state in that case."""

    DISAPPEARED = libvirt.libvirtError(
        "guest agent command timed out: Guest agent disappeared while executing command"
    )

    def _domain_with_state(self, state):
        domain = _make_domain()
        domain.info.return_value = (state, 0, 0, 0, 0)
        return domain

    @patch.object(guest_agent, "time")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_suspend_ram_disappeared_but_suspended(self, mock_lq, mock_time):
        fake = _FakeTime()
        mock_time.time.side_effect = fake.time
        mock_time.sleep.side_effect = fake.sleep
        mock_lq.qemuAgentCommand.side_effect = self.DISAPPEARED
        domain = self._domain_with_state(libvirt.VIR_DOMAIN_PMSUSPENDED)
        ga_suspend(domain, "ram")  # must not raise
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-suspend-ram")
        self.assertEqual(fake.sleeps, [])  # matched on the first check

    @patch.object(guest_agent, "time")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_suspend_ram_disappeared_still_running(self, mock_lq, mock_time):
        fake = _FakeTime()
        mock_time.time.side_effect = fake.time
        mock_time.sleep.side_effect = fake.sleep
        mock_lq.qemuAgentCommand.side_effect = self.DISAPPEARED
        domain = self._domain_with_state(libvirt.VIR_DOMAIN_RUNNING)
        with self.assertRaises(GuestAgentError):
            ga_suspend(domain, "ram")
        self.assertTrue(fake.sleeps)  # it polled before giving up

    @patch.object(guest_agent, "time")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_suspend_ram_no_response_timeout_but_suspended(self, mock_lq, mock_time):
        # Real libvirt wording when the guest suspends before the agent can
        # reply (QGA blocks in the suspend): neither "disappeared" nor
        # "timed out" appears in the message.
        fake = _FakeTime()
        mock_time.time.side_effect = fake.time
        mock_time.sleep.side_effect = fake.sleep
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "guest agent didn't respond to command within '30' seconds"
        )
        domain = self._domain_with_state(libvirt.VIR_DOMAIN_PMSUSPENDED)
        ga_suspend(domain, "ram")  # must not raise
        self.assertEqual(fake.sleeps, [])  # matched on the first check

    @patch.object(guest_agent, "time")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_suspend_disk_disappeared_shut_off(self, mock_lq, mock_time):
        fake = _FakeTime()
        mock_time.time.side_effect = fake.time
        mock_time.sleep.side_effect = fake.sleep
        mock_lq.qemuAgentCommand.side_effect = self.DISAPPEARED
        domain = self._domain_with_state(libvirt.VIR_DOMAIN_SHUTOFF)
        ga_suspend(domain, "disk")  # must not raise

    @patch.object(guest_agent, "time")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_suspend_other_error_not_swallowed(self, mock_lq, mock_time):
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "guest agent command failed: GuestAgentError: hibernate not supported"
        )
        domain = self._domain_with_state(libvirt.VIR_DOMAIN_RUNNING)
        with self.assertRaises(GuestAgentError):
            ga_suspend(domain, "disk")
        mock_time.sleep.assert_not_called()


class TestGuestShutdownVerification(unittest.TestCase):
    """The guest powers off before the agent can reply: libvirt reports an
    error while the shutdown succeeded. ga_shutdown must verify the
    domain state in that case."""

    DISAPPEARED = libvirt.libvirtError(
        "guest agent command timed out: Guest agent disappeared while executing command"
    )

    def _domain_with_state(self, state):
        domain = _make_domain()
        domain.info.return_value = (state, 0, 0, 0, 0)
        return domain

    @patch.object(guest_agent, "time")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_shutdown_disappeared_but_shut_off(self, mock_lq, mock_time):
        fake = _FakeTime()
        mock_time.time.side_effect = fake.time
        mock_time.sleep.side_effect = fake.sleep
        mock_lq.qemuAgentCommand.side_effect = self.DISAPPEARED
        domain = self._domain_with_state(libvirt.VIR_DOMAIN_SHUTOFF)
        ga_shutdown(domain)  # must not raise
        sent = json.loads(mock_lq.qemuAgentCommand.call_args[0][1])
        self.assertEqual(sent["execute"], "guest-shutdown")
        self.assertEqual(fake.sleeps, [])  # matched on the first check

    @patch.object(guest_agent, "time")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_shutdown_disappeared_still_running(self, mock_lq, mock_time):
        fake = _FakeTime()
        mock_time.time.side_effect = fake.time
        mock_time.sleep.side_effect = fake.sleep
        mock_lq.qemuAgentCommand.side_effect = self.DISAPPEARED
        domain = self._domain_with_state(libvirt.VIR_DOMAIN_RUNNING)
        with self.assertRaises(GuestAgentError):
            ga_shutdown(domain)
        self.assertTrue(fake.sleeps)  # it polled before giving up

    @patch.object(guest_agent, "time")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_shutdown_halt_accepts_paused(self, mock_lq, mock_time):
        fake = _FakeTime()
        mock_time.time.side_effect = fake.time
        mock_time.sleep.side_effect = fake.sleep
        mock_lq.qemuAgentCommand.side_effect = self.DISAPPEARED
        domain = self._domain_with_state(libvirt.VIR_DOMAIN_PAUSED)
        ga_shutdown(domain, mode="halt")  # must not raise

    @patch.object(guest_agent, "time")
    @patch.object(guest_agent, "libvirt_qemu")
    def test_shutdown_other_error_not_swallowed(self, mock_lq, mock_time):
        mock_lq.qemuAgentCommand.side_effect = libvirt.libvirtError(
            "guest agent command failed: unable to execute QEMU agent "
            "command 'guest-shutdown': mode is invalid"
        )
        domain = self._domain_with_state(libvirt.VIR_DOMAIN_RUNNING)
        with self.assertRaises(GuestAgentError):
            ga_shutdown(domain)
        mock_time.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
