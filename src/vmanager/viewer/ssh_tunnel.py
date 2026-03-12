"""
SSH Tunnel Manager

Manages SSH tunnel lifecycle for qemu+ssh:// connections.
Provides non-blocking tunnel verification and robust cleanup.
"""

import re
import socket
import subprocess
import time
from typing import Optional, Tuple, Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .constants import (
    SSH_TUNNEL_VERIFY_TIMEOUT,
    SSH_TUNNEL_GRACEFUL_SHUTDOWN_TIMEOUT,
    SSH_TUNNEL_KILL_TIMEOUT,
    TUNNEL_VERIFY_CHECK_INTERVAL_MS,
)


class SSHTunnelManager:
    """
    Manages SSH tunnel for remote libvirt connections.

    Handles tunnel creation, verification, and cleanup for qemu+ssh:// URIs.
    """

    def __init__(self, log_callback: Optional[Callable[[str], None]] = None,
                 notification_callback: Optional[Callable[[str, Gtk.MessageType], None]] = None):
        """
        Initialize the SSH tunnel manager.

        Args:
            log_callback: Function to call for logging messages
            notification_callback: Function to call for user notifications
        """
        self.log = log_callback if log_callback else lambda msg: None
        self.notify = notification_callback if notification_callback else lambda msg, typ: None

        # Tunnel state
        self.ssh_tunnel_process: Optional[subprocess.Popen] = None
        self.ssh_tunnel_local_port: Optional[int] = None
        self.ssh_tunnel_active: bool = False
        self.ssh_gateway: Optional[str] = None
        self.ssh_gateway_port: Optional[str] = None

    def set_notification_callback(self, callback: Callable[[str, Gtk.MessageType], None]):
        """
        Set the notification callback after initialization.

        Args:
            callback: Function to call for user notifications
        """
        self.notify = callback if callback else lambda msg, typ: None

    def _find_free_port(self) -> int:
        """
        Find a free local port for SSH tunnel.

        Returns:
            Port number that is currently available
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    def _parse_ssh_uri(self, uri: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse qemu+ssh URI to extract SSH gateway and port.

        Examples:
            qemu+ssh://user@host/system -> gateway: user@host, port: 22
            qemu+ssh://user@host:999/system -> gateway: user@host, port: 999

        Args:
            uri: The libvirt URI to parse

        Returns:
            Tuple of (gateway, port) or (None, None) if parsing fails
        """
        if not uri or "qemu+ssh" not in uri:
            return None, None

        # Pattern: qemu+ssh://[user@]host[:port]/path
        match = re.search(r"qemu\+ssh://([^@]+@)?([^/:]+)(?::(\d+))?", uri)
        if not match:
            return None, None

        user_part = match.group(1) if match.group(1) else ""
        host = match.group(2)
        port = match.group(3) if match.group(3) else "22"

        gateway = f"{user_part}{host}"
        return gateway, port

    def setup(self, uri: str, direct_connection: bool = False) -> bool:
        """
        Setup SSH tunnel configuration from URI.

        Parses the URI and allocates a local port, but doesn't start the tunnel yet.

        Args:
            uri: The libvirt URI (e.g., qemu+ssh://user@host/system)
            direct_connection: If True, skip SSH tunnel setup

        Returns:
            True if setup successful, False otherwise
        """
        if not uri or "qemu+ssh" not in uri or direct_connection:
            return False

        try:
            # Parse SSH gateway from URI
            self.ssh_gateway, self.ssh_gateway_port = self._parse_ssh_uri(uri)

            if not self.ssh_gateway:
                self.log("ERROR: Could not parse qemu+ssh URI")
                return False

            self.log(
                f"Detected remote SSH connection via {self.ssh_gateway}:{self.ssh_gateway_port}"
            )

            # Find a free local port for the tunnel
            self.ssh_tunnel_local_port = self._find_free_port()

            self.log(f"SSH tunnel will use local port: {self.ssh_tunnel_local_port}")

            return True

        except Exception as e:
            self.log(f"ERROR: Failed to setup SSH tunnel: {e}")
            return False

    def start(self, remote_host: str, remote_port: int) -> bool:
        """
        Start the actual SSH tunnel process.

        Uses BatchMode to avoid interactive prompts that would hang the UI.
        Verifies the tunnel is established asynchronously.

        Args:
            remote_host: Remote host to tunnel to (e.g., 'localhost' for VM on remote host)
            remote_port: Remote port to tunnel to (e.g., VNC/SPICE port)

        Returns:
            True if tunnel process started, False otherwise
        """
        if not self.ssh_gateway or not self.ssh_tunnel_local_port:
            return False

        # Ensure any previous tunnel is stopped
        self.stop()

        # SSH command: ssh -N -C -L local_port:remote_host:remote_port gateway -p gateway_port
        # -o BatchMode=yes: Fail immediately if password/passphrase is needed or host key unknown
        # -o ConnectTimeout=10: Don't wait forever for connection
        # -o StrictHostKeyChecking=accept-new: Accept new host keys but reject changed ones
        # -o ExitOnForwardFailure=yes: Exit if port forwarding fails
        ssh_cmd = [
            "ssh",
            "-N",
            "-C",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ExitOnForwardFailure=yes",
            "-L",
            f"{self.ssh_tunnel_local_port}:{remote_host}:{remote_port}",
            self.ssh_gateway,
            "-p",
            self.ssh_gateway_port,
        ]

        # Sanitize command for logging (hide potential sensitive info)
        safe_cmd = ' '.join(ssh_cmd).replace(self.ssh_gateway, "***@***")
        self.log(f"Starting SSH tunnel: {safe_cmd}")

        try:
            self.ssh_tunnel_process = subprocess.Popen(
                ssh_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            self.log("ERROR: 'ssh' command not found. Please install OpenSSH client.")
            self.notify("SSH client not found. Cannot establish tunnel.", Gtk.MessageType.ERROR)
            return False
        except Exception as e:
            self.log(f"ERROR: Failed to start SSH process: {e}")
            self.notify(f"Failed to start SSH tunnel: {e}", Gtk.MessageType.ERROR)
            return False

        # Start async verification (non-blocking)
        self.log("SSH tunnel process started, verifying connection...")
        self._verify_tunnel()
        return True

    def _verify_tunnel(self, timeout: Optional[int] = None):
        """
        Verify that the SSH tunnel process started and local port is listening.

        This is non-blocking and uses GLib callbacks instead of blocking waits.
        Returns immediately and verification happens asynchronously.

        Args:
            timeout: Verification timeout in seconds (uses default if None)
        """
        if timeout is None:
            timeout = SSH_TUNNEL_VERIFY_TIMEOUT

        # Store verification start time
        start_time = time.time()

        def check_tunnel_ready() -> bool:
            """Non-blocking callback to check tunnel status."""
            # Check if process died
            if self.ssh_tunnel_process is None:
                return False

            if self.ssh_tunnel_process.poll() is not None:
                # Process exited, get error output
                _, stderr = self.ssh_tunnel_process.communicate()
                error_msg = stderr.decode().strip() if stderr else "Unknown error"
                self.log(f"ERROR: SSH tunnel failed to start: {error_msg}")
                self.notify(f"SSH tunnel failed: {error_msg}", Gtk.MessageType.ERROR)
                self.ssh_tunnel_process = None
                self.ssh_tunnel_active = False
                return False

            # Check if local port is now listening
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.1)
                    result = s.connect_ex(("localhost", self.ssh_tunnel_local_port))
                    if result == 0:
                        # Success - tunnel is ready
                        self.ssh_tunnel_active = True
                        self.log("SSH tunnel established successfully.")
                        return False  # Stop checking
            except Exception:
                pass

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                # Timeout reached, but tunnel might still work
                self.log("WARNING: SSH tunnel verification timed out, proceeding anyway.")
                self.ssh_tunnel_active = True
                return False  # Stop checking

            # Continue checking
            return True

        # Start async verification with configured interval
        GLib.timeout_add(TUNNEL_VERIFY_CHECK_INTERVAL_MS, check_tunnel_ready)

    def stop(self, *args):
        """
        Terminate the SSH tunnel process if active.

        This method is safe to call multiple times and handles cleanup robustly.
        Can be connected as a GTK signal handler (accepts extra args).
        """
        if not self.ssh_tunnel_process:
            return

        try:
            self.log("Terminating SSH tunnel")

            # Check if process is still running before attempting termination
            if self.ssh_tunnel_process.poll() is None:
                # Process is still running, attempt graceful termination
                self.ssh_tunnel_process.terminate()

                try:
                    # Wait for graceful shutdown
                    self.ssh_tunnel_process.wait(timeout=SSH_TUNNEL_GRACEFUL_SHUTDOWN_TIMEOUT)
                    self.log("SSH tunnel terminated gracefully.")
                except subprocess.TimeoutExpired:
                    # Force kill if graceful termination failed
                    self.log("SSH tunnel did not respond to SIGTERM, sending SIGKILL")
                    self.ssh_tunnel_process.kill()
                    try:
                        # Wait for kill to complete
                        self.ssh_tunnel_process.wait(timeout=SSH_TUNNEL_KILL_TIMEOUT)
                        self.log("SSH tunnel forcefully terminated.")
                    except subprocess.TimeoutExpired:
                        self.log("WARNING: SSH tunnel process may still be running")
            else:
                # Process already exited
                self.log("SSH tunnel process already exited.")

        except Exception as e:
            # Log but don't raise - cleanup should be robust
            self.log(f"WARNING: Error while stopping SSH tunnel: {e}")
        finally:
            # Always clean up our references
            self.ssh_tunnel_process = None
            self.ssh_tunnel_active = False

    def is_active(self) -> bool:
        """
        Check if tunnel is active.

        Returns:
            True if tunnel is active and ready
        """
        return self.ssh_tunnel_active

    def get_local_port(self) -> Optional[int]:
        """
        Get the local port number for the tunnel.

        Returns:
            Local port number, or None if not set up
        """
        return self.ssh_tunnel_local_port

    def cleanup(self):
        """
        Clean up tunnel resources.

        Alias for stop() for use in destructors.
        """
        self.stop()


__all__ = ['SSHTunnelManager']
