"""
QEMU Guest Agent (QGA) service layer.

Wraps libvirt_qemu.qemuAgentCommand() to send QGA JSON commands to a domain
and parse the JSON replies. Works on local and remote libvirt connections
(libvirt forwards the command to the hypervisor's libvirtd).

All functions here are blocking (they wait for the agent up to the given
timeout) and must only be called from worker threads, never from the UI
thread.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shlex
import threading
import time
from collections.abc import Callable
from typing import Any

import libvirt

from .constants import ErrorMessages
from .utils import format_uptime, snake_case_keys
from .vm_queries import _get_domain_root, get_vm_devices_info

try:
    import libvirt_qemu

    _HAS_LIBVIRT_QEMU = True
except ImportError:  # pragma: no cover - depends on the libvirt build
    libvirt_qemu = None
    _HAS_LIBVIRT_QEMU = False

# Default per-command timeouts (seconds)
PING_TIMEOUT = 5
QUERY_TIMEOUT = 10
EXEC_STATUS_POLL_INTERVAL = 0.5
# Chunk size for guest file uploads (256 KiB per guest-file-write)
UPLOAD_CHUNK_SIZE = 256 * 1024


class GuestAgentError(Exception):
    """Raised when a QGA command cannot be completed.

    Attributes:
        reason: one of "not_configured", "not_connected", "timeout",
                "vm_not_running", "command_failed", "unsupported", "unknown"
        command: the QGA command name that failed (or None)
    """

    def __init__(self, message: str, reason: str = "unknown", command: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.command = command


class UploadCancelled(Exception):
    """Raised when a file upload is cancelled between chunks.

    Attributes:
        bytes_written: bytes written to the guest before the cancel
        total_bytes: total size of the local file
    """

    def __init__(self, bytes_written: int, total_bytes: int) -> None:
        super().__init__(f"Upload cancelled after {bytes_written} of {total_bytes} bytes")
        self.bytes_written = bytes_written
        self.total_bytes = total_bytes


def _classify_libvirt_error(error: libvirt.libvirtError, command: str | None) -> GuestAgentError:
    """Map a libvirt error from qemuAgentCommand to a GuestAgentError with a reason."""
    message = str(error)
    if "not configured" in message:
        return GuestAgentError(message, reason="not_configured", command=command)
    if "not connected" in message or "not available due to an error" in message:
        return GuestAgentError(message, reason="not_connected", command=command)
    if "didn't respond" in message or "disappeared" in message:
        return GuestAgentError(message, reason="timeout", command=command)
    if "domain is not running" in message:
        return GuestAgentError(message, reason="vm_not_running", command=command)
    if "unable to execute QEMU agent command" in message:
        if (
            "CommandNotFound" in message
            or "CommandDisabled" in message
            or "has not been found" in message
        ):
            reason = "unsupported"
        else:
            reason = "command_failed"
        return GuestAgentError(message, reason=reason, command=command)
    return GuestAgentError(message, reason="unknown", command=command)


def is_available() -> bool:
    """Returns True if the libvirt build exposes qemuAgentCommand."""
    return _HAS_LIBVIRT_QEMU


def ga_command(
    domain: libvirt.virDomain,
    name: str,
    arguments: dict | None = None,
    timeout: int = QUERY_TIMEOUT,
) -> Any:
    """Send a QGA command to the domain and return the reply's 'return' value.

    Args:
        domain: a running libvirt domain
        name: QGA command name, e.g. "guest-ping"
        arguments: optional dict of command arguments
        timeout: timeout in seconds (must be > 0)

    Raises:
        GuestAgentError: if the agent is unreachable or the command fails
    """
    if not _HAS_LIBVIRT_QEMU:
        raise GuestAgentError(
            "libvirt_qemu bindings are not available in this libvirt build",
            reason="unknown",
            command=name,
        )

    cmd: dict[str, Any] = {"execute": name}
    if arguments is not None:
        cmd["arguments"] = arguments

    try:
        reply_str = libvirt_qemu.qemuAgentCommand(domain, json.dumps(cmd), timeout, 0)
    except libvirt.libvirtError as e:
        raise _classify_libvirt_error(e, name) from e

    try:
        reply = json.loads(reply_str)
    except (json.JSONDecodeError, TypeError) as e:
        raise GuestAgentError(
            f"Invalid JSON reply from guest agent: {reply_str!r}", command=name
        ) from e

    if not isinstance(reply, dict):
        raise GuestAgentError(f"Unexpected guest agent reply: {reply_str!r}", command=name)

    # Defensive: libvirt normally converts JSON errors into libvirt errors,
    # but some paths may pass the raw error object through.
    if "error" in reply:
        err = reply["error"]
        if isinstance(err, dict):
            klass = err.get("class", "Error")
            desc = err.get("desc", str(err))
            if klass in ("CommandNotFound", "CommandDisabled"):
                reason = "unsupported"
            else:
                reason = "command_failed"
            raise GuestAgentError(f"{klass}: {desc}", reason=reason, command=name)
        raise GuestAgentError(f"Guest agent error: {err}", command=name)

    if "return" not in reply:
        raise GuestAgentError(
            f"Guest agent reply has neither 'return' nor 'error': {reply_str!r}", command=name
        )

    return reply["return"]


def ga_ping(domain: libvirt.virDomain, timeout: int = PING_TIMEOUT) -> bool:
    """Returns True if the guest agent answers guest-ping."""
    try:
        ga_command(domain, "guest-ping", timeout=timeout)
        return True
    except GuestAgentError as e:
        logging.debug("guest-ping failed for %s: %s (reason=%s)", domain.name(), e, e.reason)
        return False


def agent_status(domain: libvirt.virDomain) -> str:
    """Returns the guest agent status for a domain.

    Returns one of:
        "not_configured" - VM has no guest agent channel
        "not_running"    - VM is not active
        "down"           - channel configured but the agent is not responding
        "running"        - the agent answered guest-ping
    """
    if domain is None:
        return "not_configured"

    try:
        if domain.isActive() == 0:
            return "not_running"
    except libvirt.libvirtError:
        return "not_running"

    # Channel presence check (cheap, from the XML)
    try:
        _, root = _get_domain_root(domain)
        if root is not None and not get_vm_devices_info(root).get("qemu_guest_agent"):
            return "not_configured"
    except Exception:
        logging.debug("Failed to check agent channel in XML", exc_info=True)

    return "running" if ga_ping(domain) else "down"


def ga_supported(domain: libvirt.virDomain, timeout: int = PING_TIMEOUT) -> dict:
    """Returns the agent version and the list of supported commands (from 'guest-info')."""
    result = ga_command(domain, "guest-info", timeout=timeout)
    if not isinstance(result, dict):
        return {}
    commands = [
        c.get("name", "")
        for c in result.get("supported_commands", [])
        if isinstance(c, dict) and c.get("enabled", True)
    ]
    return {
        "version": result.get("version", "unknown"),
        "name": "qemu-ga",
        "commands": [c for c in commands if c],
    }


def ga_info(domain: libvirt.virDomain, timeout: int = QUERY_TIMEOUT) -> dict:
    """Returns guest info: hostname, OS, uptime and load.

    Uptime is read from /proc/uptime via the file commands (the protocol
    has no uptime command). Commands missing on the agent (e.g.
    guest-get-load before QEMU 10) are skipped; other errors propagate.
    """
    info: dict[str, Any] = {
        "hostname": "",
        "os": {},
        "uptime": 0,
        "uptime_format": "",
        "load": [],
    }

    def _fetch(command: str) -> Any:
        try:
            return ga_command(domain, command, timeout=timeout)
        except GuestAgentError as e:
            if e.reason == "unsupported":
                return None
            raise

    result = _fetch("guest-get-host-name")
    if isinstance(result, dict):
        info["hostname"] = result.get("host-name", "")

    result = _fetch("guest-get-osinfo")
    if isinstance(result, dict):
        info["os"] = snake_case_keys(result)

    result = _fetch("guest-get-load")
    if isinstance(result, dict):
        info["load"] = [
            result.get("load1m", 0),
            result.get("load5m", 0),
            result.get("load15m", 0),
        ]

    uptime = ga_uptime(domain, timeout)
    if uptime is not None:
        info["uptime"] = int(uptime)
        info["uptime_format"] = format_uptime(uptime)

    return info


def ga_uptime(domain: libvirt.virDomain, timeout: int = QUERY_TIMEOUT) -> float | None:
    """Returns the guest uptime in seconds, read from /proc/uptime.

    Returns None if it cannot be read (non-Linux guest, agent error...).
    """
    try:
        handle = ga_command(
            domain, "guest-file-open", {"path": "/proc/uptime", "mode": "r"}, timeout=timeout
        )
    except GuestAgentError:
        return None
    if not isinstance(handle, int):
        return None
    try:
        result = ga_command(
            domain, "guest-file-read", {"handle": handle, "count": 256}, timeout=timeout
        )
        if not isinstance(result, dict):
            return None
        data = base64.b64decode(result.get("buf-b64", "")).decode("utf-8", errors="replace")
        return float(data.split()[0])
    except (GuestAgentError, ValueError, IndexError):
        return None
    finally:
        try:
            ga_command(domain, "guest-file-close", {"handle": handle}, timeout=timeout)
        except GuestAgentError:
            pass


def ga_upload_file(
    domain: libvirt.virDomain,
    local_path: str,
    remote_path: str,
    mode: str = "w",
    timeout: int = QUERY_TIMEOUT,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    """Upload a local file to the guest via the QGA file commands.

    The agent runs as root inside the guest: the destination file is
    created root-owned and its parent directory must already exist.

    Returns the number of bytes written. Raises UploadCancelled if
    cancel_event is set between chunks (the guest handle is closed).
    """
    if mode not in ("w", "a"):
        raise ValueError(f"invalid upload mode: {mode}")

    try:
        total_size = os.path.getsize(local_path)
    except OSError as e:
        raise GuestAgentError(f"Cannot read local file '{local_path}': {e}") from e

    handle = ga_command(
        domain, "guest-file-open", {"path": remote_path, "mode": mode}, timeout=timeout
    )
    if not isinstance(handle, int):
        raise GuestAgentError(
            f"Unexpected guest-file-open reply: {handle!r}", command="guest-file-open"
        )

    bytes_written = 0
    try:
        with open(local_path, "rb") as local_file:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise UploadCancelled(bytes_written, total_size)
                chunk = local_file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                ga_command(
                    domain,
                    "guest-file-write",
                    {"handle": handle, "buf-b64": base64.b64encode(chunk).decode("ascii")},
                    timeout=timeout,
                )
                bytes_written += len(chunk)
                if progress_callback is not None:
                    progress_callback(bytes_written, total_size)
        ga_command(domain, "guest-file-flush", {"handle": handle}, timeout=timeout)
    finally:
        try:
            ga_command(domain, "guest-file-close", {"handle": handle}, timeout=timeout)
        except GuestAgentError:
            pass
    return bytes_written


def ga_fsinfo(domain: libvirt.virDomain, timeout: int = QUERY_TIMEOUT) -> list[dict]:
    """Returns the list of mounted filesystems with usage stats."""
    result = ga_command(domain, "guest-get-fsinfo", timeout=timeout)
    if not isinstance(result, list):
        return []
    return snake_case_keys(result)


def ga_fstrim(
    domain: libvirt.virDomain,
    minimum: int | None = None,
    maximum: int | None = None,
    timeout: int = QUERY_TIMEOUT,
) -> int:
    """Run fstrim on the guest's writable filesystems. Returns the number of bytes trimmed."""
    arguments: dict[str, Any] = {}
    if minimum is not None:
        arguments["minimum"] = minimum
    if maximum is not None:
        arguments["maximum"] = maximum
    result = ga_command(domain, "guest-fstrim", arguments or None, timeout=timeout)
    if not isinstance(result, dict):
        return 0
    return result.get("trimmed", 0)


def ga_set_time(domain: libvirt.virDomain, timeout: int = QUERY_TIMEOUT) -> None:
    """Sync the guest clock to the host clock (guest-set-time, nanoseconds)."""
    ga_command(domain, "guest-set-time", {"time": time.time_ns()}, timeout=timeout)


def ga_set_user_password(
    domain: libvirt.virDomain,
    username: str,
    password: str,
    crypted: bool = False,
    timeout: int = QUERY_TIMEOUT,
) -> None:
    """Set a user's password inside the guest (base64-encoded for the agent)."""
    encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
    ga_command(
        domain,
        "guest-set-user-password",
        {"username": username, "password": encoded, "crypted": crypted},
        timeout=timeout,
    )


def ga_fsfreeze_freeze(domain: libvirt.virDomain, timeout: int = QUERY_TIMEOUT) -> int:
    """Freeze all guest filesystems. Returns the number of frozen filesystems."""
    result = ga_command(domain, "guest-fsfreeze-freeze", timeout=timeout)
    if not isinstance(result, dict):
        return 0
    return result.get("frozen", 0)


def ga_fsfreeze_thaw(domain: libvirt.virDomain, timeout: int = QUERY_TIMEOUT) -> int:
    """Thaw all guest filesystems. Returns the number of thawed filesystems."""
    result = ga_command(domain, "guest-fsfreeze-thaw", timeout=timeout)
    if not isinstance(result, dict):
        return 0
    return result.get("thawed", 0)


def ga_fsfreeze_status(domain: libvirt.virDomain, timeout: int = PING_TIMEOUT) -> str:
    """Return the guest filesystem freeze state.

    Returns:
        One of 'frozen', 'thawed', 'frozen-error', 'none'
    """
    result = ga_command(domain, "guest-fsfreeze-status", timeout=timeout)
    if not isinstance(result, dict):
        return "none"
    return result.get("state", "none")


# ---------------------------------------------------------------------------
# Power operations
# ---------------------------------------------------------------------------


def ga_shutdown(domain: libvirt.virDomain, mode: str = "powerdown", timeout: int = 60) -> None:
    """Ask the guest to shut itself down gracefully (worker-only).

    mode: "powerdown" (default, the guest powers off), "halt" (the guest
    halts) or "reboot" (the guest reboots).

    The guest powers off before the agent can send its reply, so libvirt
    may report a timeout / "agent disappeared" error even when the
    shutdown succeeded; in that case the domain state is verified instead.
    """
    if mode not in ("powerdown", "halt", "reboot"):
        raise ValueError(f"invalid shutdown mode: {mode}")
    try:
        ga_command(domain, "guest-shutdown", {"mode": mode}, timeout=timeout)
    except GuestAgentError as exc:
        if _agent_lost(exc):
            _wait_domain_state(domain, _shutdown_acceptable_states(mode), 60, exc)
        else:
            raise


def ga_suspend(domain: libvirt.virDomain, mode: str = "ram", timeout: int = 30) -> None:
    """Suspend the guest from inside via the agent (worker-only).

    mode: "ram" (PM-suspend to RAM) or "disk" (PM-suspend to disk).

    The guest suspends before the agent can send its reply, so libvirt may
    report a timeout / "agent disappeared" error even when the suspend
    succeeded; in that case the domain state is verified instead.
    """
    if mode not in ("ram", "disk"):
        raise ValueError(f"invalid suspend mode: {mode}")
    try:
        ga_command(domain, f"guest-suspend-{mode}", None, timeout=timeout)
    except GuestAgentError as exc:
        if _agent_lost(exc):
            _wait_domain_state(domain, _suspend_acceptable_states(mode), 30, exc)
        else:
            raise


def _agent_lost(exc: GuestAgentError) -> bool:
    """True if the error looks like the agent vanishing mid-command (see ga_shutdown)."""
    return exc.reason == "timeout"


def _wait_domain_state(
    domain: libvirt.virDomain,
    acceptable: set[int],
    deadline_seconds: int,
    exc: GuestAgentError,
) -> None:
    """Re-raise exc unless the domain reaches one of the acceptable states."""
    deadline = time.time() + deadline_seconds
    while True:
        try:
            state = domain.info()[0]
        except libvirt.libvirtError:
            state = None
        if state in acceptable:
            return
        if time.time() >= deadline:
            raise exc
        time.sleep(1)


def _suspend_acceptable_states(mode: str) -> set[int]:
    states = {libvirt.VIR_DOMAIN_PMSUSPENDED}
    if mode == "disk":
        # The guest powers off once the hibernation image is written.
        states.add(libvirt.VIR_DOMAIN_SHUTOFF)
    return states


def _shutdown_acceptable_states(mode: str) -> set[int]:
    if mode == "powerdown":
        return {libvirt.VIR_DOMAIN_SHUTOFF}
    if mode == "halt":
        return {libvirt.VIR_DOMAIN_PAUSED, libvirt.VIR_DOMAIN_SHUTOFF}
    # reboot: the domain stays running; best-effort acceptance
    return {libvirt.VIR_DOMAIN_RUNNING}


def ga_exec(
    domain: libvirt.virDomain,
    command: str,
    env: list[str] | None = None,
) -> int:
    """Start a command in the guest (shlex-split into path + args) and return its PID.

    The protocol has no guest-side timeout; to bound a run, use ga_exec_wait.
    """
    tokens = shlex.split(command)
    if not tokens:
        raise GuestAgentError("Empty command", command="guest-exec")

    arguments: dict[str, Any] = {"path": tokens[0], "capture-output": True}
    if len(tokens) > 1:
        arguments["arg"] = tokens[1:]
    if env:
        arguments["env"] = env

    reply = ga_command(domain, "guest-exec", arguments=arguments, timeout=QUERY_TIMEOUT)
    pid = reply.get("pid") if isinstance(reply, dict) else reply
    if not isinstance(pid, int):
        raise GuestAgentError(f"Unexpected guest-exec reply: {reply!r}", command="guest-exec")
    return pid


def ga_exec_status(domain: libvirt.virDomain, pid: int, timeout: int = QUERY_TIMEOUT) -> dict:
    """Returns the status of a guest-exec process, normalized to:

    {"exited": bool, "exitcode": int | None, "signal": int | None,
     "stdout": str, "stderr": str, "truncated": bool}

    'exitcode' is set on normal exit, 'signal' when killed by a signal.
    """
    result = ga_command(domain, "guest-exec-status", arguments={"pid": pid}, timeout=timeout)
    if not isinstance(result, dict):
        raise GuestAgentError(
            f"Unexpected guest-exec-status reply: {result!r}", command="guest-exec-status"
        )
    # QGA replies use kebab-case keys (out-data, out-truncated, ...)
    result = snake_case_keys(result)

    def _decode(key: str) -> str:
        data = result.get(key)
        if not data:
            return ""
        try:
            return base64.b64decode(data).decode("utf-8", errors="replace")
        except Exception:
            return f"<undecodable {key}>"

    return {
        "exited": bool(result.get("exited", False)),
        "exitcode": result.get("exitcode"),
        "signal": result.get("signal"),
        "stdout": _decode("out_data"),
        "stderr": _decode("err_data"),
        "truncated": bool(result.get("out_truncated") or result.get("err_truncated")),
    }


def ga_exec_wait(
    domain: libvirt.virDomain,
    command: str,
    poll_interval: float = EXEC_STATUS_POLL_INTERVAL,
    overall_timeout: float = 300.0,
    env: list[str] | None = None,
) -> dict:
    """Run a command in the guest and wait for it to finish (worker-only, it sleeps).

    overall_timeout only bounds the wait here: the process keeps running
    in the guest if the deadline is exceeded. Returns the normalized
    status dict from ga_exec_status.
    """
    pid = ga_exec(domain, command, env=env)
    deadline = time.monotonic() + overall_timeout
    while True:
        status = ga_exec_status(domain, pid)
        if status["exited"]:
            return status
        if time.monotonic() > deadline:
            raise GuestAgentError(
                f"Timed out waiting for guest process {pid} to finish", command="guest-exec-status"
            )
        time.sleep(poll_interval)


def ga_ssh_add_authorized_keys(
    domain: libvirt.virDomain, user: str, keys: list[str], timeout: int = QUERY_TIMEOUT
) -> None:
    """Add SSH authorized keys for a user inside the guest."""
    try:
        ga_command(
            domain,
            "guest-ssh-add-authorized-keys",
            arguments={"username": user, "keys": keys},
            timeout=timeout,
        )
    except GuestAgentError as e:
        if _is_ssh_dir_exists_error(e):
            match = re.search(r"failed to create directory '([^']+)'", str(e))
            path = match.group(1) if match else "the user's .ssh directory"
            raise GuestAgentError(
                ErrorMessages.GUEST_SSH_DIR_NOT_A_DIRECTORY_TEMPLATE.format(path=path),
                reason="command_failed",
                command="guest-ssh-add-authorized-keys",
            ) from e
        raise


def _is_ssh_dir_exists_error(exc: GuestAgentError) -> bool:
    """True for the QGA 'failed to create directory ...: File exists' error."""
    message = str(exc)
    return "failed to create directory" in message and "File exists" in message


def ga_ssh_get_authorized_keys(domain: libvirt.virDomain, user: str) -> list[str]:
    """Returns the SSH authorized keys for a user inside the guest.

    A missing authorized_keys file (user has no SSH keys yet) is reported
    as an empty list; other agent errors are raised.
    """
    try:
        result = ga_command(
            domain,
            "guest-ssh-get-authorized-keys",
            arguments={"username": user},
            timeout=QUERY_TIMEOUT,
        )
    except GuestAgentError as e:
        if "No such file or directory" in str(e):
            return []
        raise
    # The QMP reply is a GuestAuthorizedKeys object: {"keys": [str, ...]}
    if isinstance(result, dict):
        result = result.get("keys")
    if not isinstance(result, list):
        return []
    return [k for k in result if isinstance(k, str)]


def ga_ssh_remove_authorized_keys(domain: libvirt.virDomain, user: str, keys: list[str]) -> None:
    """Remove SSH authorized keys for a user inside the guest."""
    ga_command(
        domain,
        "guest-ssh-remove-authorized-keys",
        arguments={"username": user, "keys": keys},
        timeout=QUERY_TIMEOUT,
    )
