"""
Tests for the machine-readable output of vmc (--machine).
"""

import io
import json
import os
import socket
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.machine_mode import (  # noqa: E402
    EXIT_CONFIG,
    EXIT_CONNECTION,
    EXIT_EXISTS,
    EXIT_OPERATION,
    EXIT_TIMEOUT,
    EXIT_UNSUPPORTED,
    EXIT_USAGE,
    MACHINE_COMMANDS,
    PROTOCOL_VERSION,
    TIMEOUT_ERRORS,
    MachineError,
    MachineSession,
    machine_command,
    run_command,
    status_for_exception,
)


@contextmanager
def extra_command(name, handler):
    """Teach machine_mode a command for one test, then forget it."""
    MACHINE_COMMANDS[name] = handler
    try:
        yield
    finally:
        MACHINE_COMMANDS.pop(name, None)


def cli_with(*names):
    """A stand-in command loop: it answers to `do_<name>` for those commands."""
    methods = {f"do_{name}": lambda self, _args=None: None for name in names}
    return type("Cli", (), methods)()


def run(line, app=None):
    """Run one command in machine mode, returning (status, parsed stream)."""
    stream = io.StringIO()
    session = MachineSession(line, stdout=stream)
    try:
        status = run_command(app or MagicMock(), line, session)
    except MachineError as e:
        status = session.fail(e)
    objects = [json.loads(line) for line in stream.getvalue().splitlines()]
    return status, objects


class TestMachineEnvelope(unittest.TestCase):
    """One JSON object per call, and only that, on the session's stream."""

    def test_success_is_one_object(self):
        @machine_command("testing_ok")
        def _ok(_app, _args, _session):
            return {"domain": "sidekick-1"}

        with extra_command("testing_ok", _ok):
            status, objects = run("testing_ok")

        self.assertEqual(status, 0)
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]["ok"], True)
        self.assertEqual(objects[0]["command"], "testing_ok")
        self.assertEqual(objects[0]["data"], {"domain": "sidekick-1"})

    def test_command_without_machine_output_is_not_a_success(self):
        # `start` is a command this vmc has, with no machine form: not a usage
        # error, and not a success either.
        status, objects = run("start sidekick-1", app=cli_with("start"))

        self.assertEqual(status, EXIT_UNSUPPORTED)
        self.assertEqual(objects[0]["ok"], False)
        self.assertEqual(objects[0]["code"], "unsupported")

    def test_a_word_that_is_not_a_command_is_a_usage_error(self):
        status, objects = run("bogus sidekick-1", app=cli_with("start"))

        self.assertEqual(status, EXIT_USAGE)
        self.assertEqual(objects[0]["code"], "usage")

    def test_empty_command_is_a_usage_error(self):
        status, objects = run("   ")

        self.assertEqual(status, EXIT_USAGE)
        self.assertEqual(objects[0]["code"], "usage")

    def test_progress_events_come_before_the_result(self):
        @machine_command("testing_progress")
        def _progress(_app, _args, session):
            session.progress("downloading", "Downloading ISO", 45)
            return {}

        with extra_command("testing_progress", _progress):
            _status, objects = run("testing_progress")

        self.assertEqual(objects[0]["event"], "progress")
        self.assertEqual(objects[0]["stage"], "downloading")
        self.assertEqual(objects[0]["percent"], 45)
        self.assertEqual(objects[-1]["ok"], True)

    def test_arguments_keep_their_spaces(self):
        @machine_command("testing_args")
        def _args(_app, args, _session):
            return {"value": args[0]}

        with extra_command("testing_args", _args):
            _status, objects = run('testing_args "a name with spaces"')

        self.assertEqual(objects[0]["data"]["value"], "a name with spaces")


class TestMachineInfo(unittest.TestCase):
    """``machine info`` is how a caller checks what it is talking to."""

    def test_reports_the_contract_and_the_version(self):
        from vmanager.constants import AppInfo

        status, objects = run("machine info")

        self.assertEqual(status, 0)
        data = objects[0]["data"]
        self.assertEqual(data["protocol"], PROTOCOL_VERSION)
        self.assertEqual(data["version"], AppInfo.version)
        self.assertIn("machine", data["commands"])

    def test_a_flag_it_does_not_take_is_a_usage_error(self):
        status, objects = run("machine info --verbose yes")

        self.assertEqual(status, EXIT_USAGE)
        self.assertIn("--verbose", objects[0]["message"])

    def test_unknown_subcommand_is_a_usage_error(self):
        status, objects = run("machine nonsense")

        self.assertEqual(status, EXIT_USAGE)
        self.assertEqual(objects[0]["code"], "usage")


class TestFailureStatuses(unittest.TestCase):
    """A failure has to leave a status that is not 0, and say which failure."""

    def test_each_kind_of_failure_has_its_status(self):
        for status, code in [
            (EXIT_CONFIG, "config_invalid"),
            (EXIT_CONNECTION, "connection_failed"),
            (EXIT_EXISTS, "already_exists"),
            (EXIT_OPERATION, "provision_failed"),
        ]:
            with self.subTest(status=status):
                stream = io.StringIO()
                session = MachineSession("deploy", stdout=stream)
                error = MachineError("It went wrong", status=status, code=code)

                self.assertEqual(session.fail(error), status)
                answer = json.loads(stream.getvalue())
                self.assertEqual(answer["ok"], False)
                self.assertEqual(answer["code"], code)
                self.assertEqual(answer["message"], "It went wrong")

    def test_a_timeout_is_reported_as_a_timeout(self):
        stream = io.StringIO()
        session = MachineSession("deploy", stdout=stream)

        self.assertEqual(session.fail(TimeoutError("waited too long")), EXIT_TIMEOUT)

    def test_a_stalled_socket_is_a_timeout_on_the_oldest_supported_python(self):
        """3.9 keeps `socket.timeout` apart from `TimeoutError`, so both are caught.

        From 3.10 the two are one object, so behaviour cannot tell a fix that
        names both from one that names only `TimeoutError`; what has to hold on
        the interpreter this project still supports is that `socket.timeout` is
        in the pair the contract answers status 8 for.
        """
        self.assertIn(socket.timeout, TIMEOUT_ERRORS)
        self.assertEqual(status_for_exception(socket.timeout("read timed out"))[0], EXIT_TIMEOUT)

    def test_an_unexpected_error_is_a_failure_not_a_success(self):
        stream = io.StringIO()
        session = MachineSession("deploy", stdout=stream)

        self.assertEqual(session.fail(RuntimeError("boom")), EXIT_OPERATION)
        self.assertEqual(json.loads(stream.getvalue())["code"], "unexpected_error")

    def test_a_libvirt_error_is_a_failure(self):
        class libvirtError(Exception):  # noqa: N801 - named as libvirt names it
            pass

        stream = io.StringIO()
        session = MachineSession("deploy", stdout=stream)

        self.assertEqual(session.fail(libvirtError("refused")), EXIT_OPERATION)
        self.assertEqual(json.loads(stream.getvalue())["code"], "libvirt_error")


class TestMachineOutputIsSafe(unittest.TestCase):
    """The answer must not carry credentials, same rule as the log."""

    def test_a_password_in_a_message_is_redacted(self):
        stream = io.StringIO()
        session = MachineSession("deploy", stdout=stream)

        session.fail(
            MachineError("Connecting to qemu+ssh://root@host/system with password=hunter2")
        )

        answer = stream.getvalue()
        self.assertNotIn("hunter2", answer)
        self.assertNotIn("root@host", answer)
        json.loads(answer)  # still valid JSON

    def test_data_is_sanitized_too(self):
        @machine_command("testing_secret")
        def _secret(_app, _args, _session):
            return {"note": "password=hunter2"}

        with extra_command("testing_secret", _secret):
            _status, objects = run("testing_secret")
        answer = objects[0]

        self.assertNotIn("hunter2", json.dumps(answer))


if __name__ == "__main__":
    unittest.main()
