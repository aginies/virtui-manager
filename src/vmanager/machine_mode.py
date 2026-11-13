"""Machine-readable output for the vmc command line.

With ``--machine`` one JSON object goes to stdout, prose to stderr, and the exit
status says what happened.
"""

import json
import shlex
import socket
import sys

from .constants import AppInfo
from .utils import sanitize_sensitive_data

PROTOCOL_VERSION = 1

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_CONNECTION = 4
EXIT_NOT_FOUND = 5
EXIT_EXISTS = 6
EXIT_OPERATION = 7
EXIT_TIMEOUT = 8
EXIT_UNSUPPORTED = 9

TIMEOUT_ERRORS = (TimeoutError, socket.timeout)

# Answer words — not localised, not in constants.py.  vmc --machine
# answers a program, not a person, and the code field beside these is the
# stable key a caller branches on.

EMPTY_COMMAND = "Empty command."
CANNOT_PARSE_COMMAND_TEMPLATE = "Cannot parse command: {error}"
UNKNOWN_COMMAND_TEMPLATE = "Unknown command '{command}'."
NO_MACHINE_RESULT_TEMPLATE = "The command '{command}' has no machine-readable result."
UNKNOWN_SUBCOMMAND_TEMPLATE = "Unknown machine subcommand '{subcommand}'."
NO_SUCH_FLAG_TEMPLATE = "machine info has no {flag} flag."


class MachineError(Exception):
    """A failure with the exit status and code the caller is told about."""

    def __init__(self, message, status=EXIT_OPERATION, code="operation_failed"):
        super().__init__(message)
        self.status = status
        self.code = code


def status_for_exception(error):
    """Map an exception onto an exit status and a code."""
    if isinstance(error, MachineError):
        return error.status, error.code
    if isinstance(error, TIMEOUT_ERRORS):
        return EXIT_TIMEOUT, "timeout"
    if type(error).__name__ == "libvirtError":
        return EXIT_OPERATION, "libvirt_error"
    return EXIT_OPERATION, "unexpected_error"


class MachineSession:
    """Writes the machine contract: progress events, then one result object."""

    def __init__(self, command, stdout=None):
        self.command = command
        self._stream = stdout if stdout is not None else sys.__stdout__

    def _write(self, payload):
        self._stream.write(json.dumps(_sanitize(payload)) + "\n")
        self._stream.flush()

    def progress(self, stage, message, percent=None):
        event = {"event": "progress", "stage": stage, "message": message}
        if percent is not None:
            event["percent"] = percent
        self._write(event)

    def succeed(self, data=None):
        self._write({"ok": True, "command": self.command, "data": data or {}})
        return EXIT_OK

    def fail(self, error):
        status, code = status_for_exception(error)
        self._write({
            "ok": False, "command": self.command,
            "code": code, "message": str(error),
        })
        return status


MACHINE_COMMANDS = {}


def machine_command(name):
    """Register a handler as the machine form of ``name``."""
    def register(handler):
        MACHINE_COMMANDS[name] = handler
        return handler
    return register


@machine_command("machine")
def _machine_info(_app, args, _session):
    """``machine info``: what this vmc is, and what it answers to."""
    if args and args[0] != "info":
        raise MachineError(
            UNKNOWN_SUBCOMMAND_TEMPLATE.format(subcommand=args[0]),
            EXIT_USAGE, "usage",
        )
    for word in args[1:]:
        if word.startswith("--"):
            raise MachineError(
                NO_SUCH_FLAG_TEMPLATE.format(flag=word),
                EXIT_USAGE, "usage",
            )
    return {
        "protocol": PROTOCOL_VERSION,
        "name": AppInfo.name,
        "version": AppInfo.version,
        "commands": sorted(MACHINE_COMMANDS),
    }


def _sanitize(payload):
    if isinstance(payload, str):
        return sanitize_sensitive_data(payload)
    if isinstance(payload, dict):
        return {key: _sanitize(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_sanitize(value) for value in payload]
    return payload


def run_command(app, line, session):
    """Run one command for a machine caller and return its exit status."""
    try:
        parts = shlex.split(line)
    except ValueError as e:
        raise MachineError(
            CANNOT_PARSE_COMMAND_TEMPLATE.format(error=e),
            EXIT_USAGE, "usage",
        ) from e
    if not parts:
        raise MachineError(EMPTY_COMMAND, EXIT_USAGE, "usage")

    handler = MACHINE_COMMANDS.get(parts[0])
    if handler is None:
        if hasattr(app, f"do_{parts[0]}"):
            raise MachineError(
                NO_MACHINE_RESULT_TEMPLATE.format(command=parts[0]),
                EXIT_UNSUPPORTED, "unsupported",
            )
        raise MachineError(
            UNKNOWN_COMMAND_TEMPLATE.format(command=parts[0]),
            EXIT_USAGE, "usage",
        )

    data = handler(app, parts[1:], session)
    return session.succeed(data)
