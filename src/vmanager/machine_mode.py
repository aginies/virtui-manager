"""Machine-readable output for the vmc command line.

Documented in ``documentation/manual/cli.md`` ("Machine-Readable Output").
The contract exists so that a program can drive ``vmc`` without reading its
prose: with ``--machine``, one JSON object goes to stdout and everything the
commands print goes to stderr, and the exit status says what happened. The
second half is the point: a command that fails has to fail with a status
that is not 0.

``data`` payloads are added command by command, and a command without one
answers with ``EXIT_UNSUPPORTED`` rather than an empty success.

This surface departs from the interactive tool in three places, deliberately, and
each is recorded in ``docs/adr/``: the answers are not localized (0001), state is
read from the hypervisor rather than the cache (0002), and ``volume rename``
answers 9 (0003).
"""

import json
import shlex
import socket
import sys

from .constants import AppInfo, MachineModeText
from .utils import sanitize_sensitive_data

# Version of this contract, reported by ``machine info``.
PROTOCOL_VERSION = 1

# Exit statuses. A caller switches on these; the ``code`` of the JSON object
# says the same thing in words.
EXIT_OK = 0
EXIT_USAGE = 2  # unknown command, bad arguments
EXIT_CONFIG = 3  # deployment file missing, malformed or invalid
EXIT_CONNECTION = 4  # the hypervisor cannot be reached
EXIT_NOT_FOUND = 5  # no such domain, volume, pool or network
EXIT_EXISTS = 6  # the name is taken
EXIT_OPERATION = 7  # the hypervisor refused, or the operation failed
EXIT_TIMEOUT = 8  # a wait ran out
EXIT_UNSUPPORTED = 9  # command has no machine-readable result (older vmc)

# The exceptions that say a wait ran out. ``socket.timeout`` is an alias of
# ``TimeoutError`` only from Python 3.10: on the 3.9 this project still supports
# the two are separate ``OSError`` siblings, so a timeout is named as both,
# otherwise a stalled transfer is mistaken for a generic failure there.
TIMEOUT_ERRORS = (TimeoutError, socket.timeout)

# The stage a progress event names is one of ``MachineProgressStage``, held with
# the rest of the words a machine caller is answered with, and listed by
# documentation/manual/cli.md. A command takes the stage it reports from there, so
# the set cannot grow quietly behind the manual.


class MachineError(Exception):
    """A failure with the exit status and the code the caller is told about."""

    def __init__(self, message, status=EXIT_OPERATION, code="operation_failed"):
        super().__init__(message)
        self.status = status
        self.code = code


def status_for_exception(error):
    """Map an exception onto an exit status and a code.

    Anything that is not a :class:`MachineError` escaped its command, so it
    gets a status that says "failed" without pretending to be specific.
    """
    if isinstance(error, MachineError):
        return error.status, error.code
    if isinstance(error, TIMEOUT_ERRORS):
        return EXIT_TIMEOUT, "timeout"
    if type(error).__name__ == "libvirtError":
        return EXIT_OPERATION, "libvirt_error"
    return EXIT_OPERATION, "unexpected_error"


class MachineSession:
    """Writes the machine contract: progress events, then one result object.

    ``stdout`` is the stream the contract is written to, captured by the
    caller before it sends ``sys.stdout`` to stderr.
    """

    def __init__(self, command, stdout=None):
        self.command = command
        self._stream = stdout if stdout is not None else sys.__stdout__

    def _write(self, payload):
        # Same rule as the log: a message must not carry a password or the
        # credentials in a hypervisor URI. Sanitised per value, before the
        # encoding, so the JSON structure is untouched.
        self._stream.write(json.dumps(_sanitize(payload)) + "\n")
        self._stream.flush()

    def progress(self, stage, message, percent=None):
        """Report what a long command is doing, while it does it.

        ``stage`` says which part of the command is talking — one of
        ``MachineProgressStage.ALL`` — and ``message`` is the detail for whoever is
        reading it.
        """
        event = {"event": "progress", "stage": stage, "message": message}
        if percent is not None:
            event["percent"] = percent
        self._write(event)

    def succeed(self, data=None):
        """Report the result and return the exit status for it."""
        self._write({"ok": True, "command": self.command, "data": data or {}})
        return EXIT_OK

    def fail(self, error):
        """Report a failure and return the exit status for it."""
        status, code = status_for_exception(error)
        self._write(
            {
                "ok": False,
                "command": self.command,
                "code": code,
                "message": str(error),
            }
        )
        return status


# Commands with a machine-readable result, by first word. A handler takes the
# command shell, the words after the command name, and the session (for
# progress), and returns the ``data`` dict. It reports failure by raising
# MachineError with the status the caller needs.
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
            MachineModeText.UNKNOWN_SUBCOMMAND_TEMPLATE.format(subcommand=args[0]),
            EXIT_USAGE,
            "usage",
        )
    for word in args[1:]:
        if word.startswith("--"):
            raise MachineError(
                MachineModeText.NO_SUCH_FLAG_TEMPLATE.format(flag=word), EXIT_USAGE, "usage"
            )
    return {
        "protocol": PROTOCOL_VERSION,
        "name": AppInfo.name,
        "version": AppInfo.version,
        "commands": sorted(MACHINE_COMMANDS),
    }


def _sanitize(payload):
    """Sanitise every string of a payload about to be encoded as JSON."""
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
            MachineModeText.CANNOT_PARSE_COMMAND_TEMPLATE.format(error=e), EXIT_USAGE, "usage"
        ) from e
    if not parts:
        raise MachineError(MachineModeText.EMPTY_COMMAND, EXIT_USAGE, "usage")

    handler = MACHINE_COMMANDS.get(parts[0])
    if handler is None:
        # Two different answers: a command vmc has but has not given a
        # machine-readable form yet is this version's gap (9), a word vmc does
        # not know at all is the caller's mistake (2).
        if hasattr(app, f"do_{parts[0]}"):
            raise MachineError(
                MachineModeText.NO_MACHINE_RESULT_TEMPLATE.format(command=parts[0]),
                EXIT_UNSUPPORTED,
                "unsupported",
            )
        raise MachineError(
            MachineModeText.UNKNOWN_COMMAND_TEMPLATE.format(command=parts[0]), EXIT_USAGE, "usage"
        )

    data = handler(app, parts[1:], session)
    return session.succeed(data)
