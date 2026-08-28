"""
Auto HTTP Server

Simple HTTP server for serving Auto configuration files during VM installation.
The server runs in a background thread and automatically stops after the VM installation completes.
"""

import atexit
import base64
import errno
import http.server
import logging
import socketserver
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

# Keep track of active servers to clean up on exit
_ACTIVE_SERVERS = []


def _cleanup_servers():
    """Stop all active servers on application exit."""
    if _ACTIVE_SERVERS:
        logging.getLogger(__name__).info(f"Cleaning up {len(_ACTIVE_SERVERS)} active Auto servers...")
        # Create a copy of the list to iterate over, as stop() modifies the original list
        for server in _ACTIVE_SERVERS[:]:
            try:
                server.stop()
            except Exception as e:
                logging.getLogger(__name__).error(f"Error stopping server during cleanup: {e}")


atexit.register(_cleanup_servers)


class AutoHTTPServer:
    """
    HTTP server for serving Auto configuration files.

    The server serves files from a specified directory and runs in a background thread.
    """

    def __init__(self, serve_dir: Path, port: int = 0):
        """
        Initialize the Auto HTTP server.

        Args:
            serve_dir: Directory containing files to serve
            port: Port to listen on (0 = auto-select available port)
        """
        self.serve_dir = serve_dir
        self.port = port
        self.server: Optional[socketserver.TCPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.logger = logging.getLogger(__name__)
        self.actual_port: Optional[int] = None

    def start(self) -> int:
        """
        Start the HTTP server in a background thread.

        Returns:
            The actual port the server is listening on

        Raises:
            Exception: If server fails to start
        """
        try:
            # Create a custom handler that serves from our directory
            class CustomHandler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *args, directory=None, **kwargs):
                    super().__init__(*args, directory=str(directory), **kwargs)

                def log_message(self, format, *args):
                    # Log to our logger instead of stderr
                    logging.getLogger(__name__).info(
                        f"HTTP Request: {self.address_string()} - {format % args}"
                    )

                def log_error(self, format, *args):
                    # Suppress "Bad request version" or "Bad request syntax" errors 
                    # which happen when client tries HTTPS on HTTP port (common with Agama probing)
                    if any(msg in format for msg in ["Bad request version", "Bad request syntax"]):
                        return
                    super().log_error(format, *args)

            # Create handler with our serve directory
            handler = lambda *args, **kwargs: CustomHandler(
                *args, directory=self.serve_dir, **kwargs
            )

            # Create TCP server (allows port reuse)
            self.server = socketserver.TCPServer(("", self.port), handler, bind_and_activate=False)
            self.server.allow_reuse_address = True
            self.server.server_bind()
            self.server.server_activate()

            # Get the actual port (important if port=0 was specified)
            self.actual_port = self.server.server_address[1]

            # Start server in background thread
            self.thread = threading.Thread(
                target=self.server.serve_forever, daemon=True, name="AutoHTTPServer"
            )
            self.thread.start()

            # Register with global list for cleanup
            if self not in _ACTIVE_SERVERS:
                _ACTIVE_SERVERS.append(self)

            self.logger.info(
                f"Auto HTTP server started on port {self.actual_port}, serving {self.serve_dir}"
            )

            return self.actual_port

        except Exception as e:
            self.logger.error(f"Failed to start Auto HTTP server: {e}")
            raise

    def stop(self):
        """Stop the HTTP server and clean up resources."""
        # Remove from global list first to prevent re-entry during cleanup
        if self in _ACTIVE_SERVERS:
            _ACTIVE_SERVERS.remove(self)

        if self.server:
            self.logger.info(f"Stopping Auto HTTP server on port {self.actual_port}")
            self.server.shutdown()
            self.server.server_close()
            self.server = None

        if self.thread:
            self.thread.join(timeout=5)
            self.thread = None

        self.actual_port = None

    def get_url(self, filename: str, host: str = "localhost") -> str:
        """
        Get the HTTP URL for a file being served.

        Args:
            filename: Name of the file to get URL for
            host: Hostname or IP address to use in URL

        Returns:
            Full HTTP URL for the file
        """
        if not self.actual_port:
            raise RuntimeError("Server not started")

        return f"http://{host}:{self.actual_port}/{filename}"

    def __enter__(self):
        """Context manager entry - start server."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - stop server."""
        self.stop()
        return False


class RemoteAutoHTTPServer:
    """
    HTTP server for serving Auto configuration files on a remote host via SSH.

    Mirrors the AutoHTTPServer interface (start/stop/get_url) but runs
    `python3 -m http.server` on the remote host so the VM being installed
    can reach the automation files on the hypervisor itself.
    """

    def __init__(self, serve_dir: Path, port: int = 0, remote_host: Optional[str] = None):
        """
        Initialize the remote Auto HTTP server.

        Args:
            serve_dir: Local directory containing files to push and serve
            port: Port to listen on (0 = auto-select available port)
            remote_host: SSH host string (user@host)
        """
        self.serve_dir = serve_dir
        self.port = port
        self.remote_host = remote_host
        self.logger = logging.getLogger(__name__)
        self.actual_port: Optional[int] = None
        self.remote_dir: Optional[str] = None
        self._pid_file: Optional[str] = None
        self._ssh_master: Optional[str] = None

    def _ssh_control_args(self) -> list:
        """SSH ControlMaster args for connection reuse."""
        if not self._ssh_master:
            self._ssh_master = f"/tmp/virtui_ssh_{uuid.uuid4().hex[:8]}"
        return ["-o", f"ControlMaster=auto", "-o", f"ControlPath={self._ssh_master}",
                "-o", "ControlPersist=60"]

    def _run_remote(self, cmd: str, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess:
        """Run a command on the remote host via SSH."""
        return subprocess.run(
            ["ssh", *self._ssh_control_args(), self.remote_host, cmd],
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )

    def _kill_existing_servers(self):
        """Kill any previous virtui automation servers and clean up their files.

        Orphaned servers from earlier runs (e.g. when the app crashed or the VM
        was manually destroyed) would otherwise keep holding the configured
        port and serving stale files.
        """
        try:
            self._run_remote(
                "pkill -f 'python3 -u -m http.server' 2>/dev/null; "
                "rm -f /tmp/virtui_auto_http_*.pid /tmp/virtui_auto_http_*.log; "
                "rm -rf /tmp/virtui_automation_remote_*",
                check=False,
                timeout=15,
            )
            # Give the kernel time to release the port
            time.sleep(1)
        except Exception as e:
            self.logger.warning(f"Failed to clean up existing remote servers: {e}")

    def start(self) -> int:
        """
        Push files to the remote host and start http.server there.

        Returns:
            The actual port the server is listening on

        Raises:
            OSError: If the port is already in use (errno 98)
            Exception: If server fails to start
        """
        if not self.remote_host:
            raise ValueError("remote_host is required for RemoteAutoHTTPServer")

        unique_id = uuid.uuid4().hex[:8]
        self.remote_dir = f"/tmp/virtui_automation_remote_{unique_id}"
        self._pid_file = f"/tmp/virtui_auto_http_{unique_id}.pid"
        log_file = f"/tmp/virtui_auto_http_{unique_id}.log"

        try:
            # Verify python3 exists on remote host
            check = self._run_remote("which python3", check=False, timeout=10)
            if check.returncode != 0:
                raise Exception(
                    f"python3 not found on remote host {self.remote_host}. "
                    "Remote auto-install requires python3 on the hypervisor."
                )

            # Kill any orphaned servers from previous runs so the configured
            # port is free and no stale files are served
            self._kill_existing_servers()

            # Create remote directory
            self._run_remote(f"mkdir -p {self.remote_dir}", timeout=10)

            # Push all files in one SSH call via tar+base64
            files = [item for item in sorted(self.serve_dir.iterdir()) if item.is_file()]
            if files:
                import tarfile
                import io
                tar_buffer = io.BytesIO()
                with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                    for item in files:
                        tar.add(item, arcname=item.name)
                tar_b64 = base64.b64encode(tar_buffer.getvalue()).decode("ascii")
                self._run_remote(
                    f"echo '{tar_b64}' | base64 -d | tar x -C {self.remote_dir}",
                    timeout=60,
                )

            # Start http.server on remote host.
            # The subshell `( ... & )` is required: it detaches the background
            # process from the SSH session so the channel closes immediately.
            # Without it, the background process holds the channel open and
            # subprocess.run blocks until the server exits.
            port_arg = self.port if self.port else 0
            start_cmd = (
                f"cd {self.remote_dir} && "
                f"(nohup python3 -u -m http.server {port_arg} --bind 0.0.0.0 "
                f"</dev/null > {log_file} 2>&1 & echo $! > {self._pid_file})"
            )
            self._run_remote(start_cmd, timeout=15)

            # Determine actual port
            if port_arg == 0:
                # Parse chosen port from http.server log output
                self.actual_port = None
                for _ in range(10):
                    result = self._run_remote(
                        f"grep -o 'port [0-9]*' {log_file} | head -1 | awk '{{print $2}}'",
                        check=False,
                        timeout=10,
                    )
                    port_str = result.stdout.strip()
                    if port_str.isdigit():
                        self.actual_port = int(port_str)
                        break
                    time.sleep(1)
                if self.actual_port is None:
                    raise Exception(
                        f"Could not determine port of remote HTTP server on {self.remote_host}"
                    )
            else:
                self.actual_port = port_arg

            # Verify our server process is alive and the port is listening.
            # Checking only the port would match an unrelated process (e.g. an
            # orphaned server from a previous run) holding the same port.
            for _ in range(10):
                check = self._run_remote(
                    f"kill -0 $(cat {self._pid_file}) 2>/dev/null && "
                    f"ss -tln 2>/dev/null | grep -q :{self.actual_port}",
                    check=False,
                    timeout=10,
                )
                if check.returncode == 0:
                    break
                time.sleep(1)
            else:
                # Process died or port not listening - check if it was a port-in-use error
                log_check = self._run_remote(
                    f"grep -q 'Address already in use' {log_file}",
                    check=False,
                    timeout=10,
                )
                if log_check.returncode == 0:
                    err = OSError(errno.EADDRINUSE, "Address already in use")
                    err.filename = self.remote_host
                    raise err
                raise Exception(
                    f"Remote HTTP server failed to start on port {self.actual_port} "
                    f"on {self.remote_host}"
                )

            self.logger.info(
                f"Remote Auto HTTP server started on {self.remote_host} "
                f"port {self.actual_port}, serving {self.remote_dir}"
            )

            return self.actual_port

        except Exception:
            self._cleanup_remote()
            raise

    def _cleanup_remote(self):
        """Best-effort cleanup of remote files."""
        if not self.remote_host:
            return
        try:
            if self._pid_file:
                self._run_remote(
                    f"kill $(cat {self._pid_file}) 2>/dev/null; rm -f {self._pid_file}",
                    check=False,
                    timeout=10,
                )
            if self.remote_dir:
                # Safety check: only allow paths under /tmp/
                if not self.remote_dir.startswith("/tmp/virtui_automation_remote_"):
                    self.logger.error(f"Refusing to delete suspicious path: {self.remote_dir}")
                    return
                self._run_remote(f"rm -rf {self.remote_dir}", check=False, timeout=10)
        except Exception as e:
            self.logger.warning(f"Failed to cleanup remote HTTP server files: {e}")

    def stop(self):
        """Stop the remote HTTP server and clean up."""
        if not self.remote_host:
            return
        try:
            self._run_remote(
                f"kill $(cat {self._pid_file}) 2>/dev/null; rm -f {self._pid_file}",
                check=False,
                timeout=10,
            )
            if self.remote_dir:
                # Safety check: only allow paths under /tmp/
                if not self.remote_dir.startswith("/tmp/virtui_automation_remote_"):
                    self.logger.error(f"Refusing to delete suspicious path: {self.remote_dir}")
                    return
                self._run_remote(f"rm -rf {self.remote_dir}", check=False, timeout=10)
            self.logger.info(f"Stopped remote Auto HTTP server on {self.remote_host}")
        except Exception as e:
            self.logger.error(f"Error stopping remote HTTP server: {e}")
        finally:
            self.actual_port = None
            self.remote_dir = None
            self._pid_file = None
            if self._ssh_master:
                subprocess.run(["ssh", "-o", f"ControlPath={self._ssh_master}",
                                "-O", "exit", self.remote_host],
                               capture_output=True, check=False, timeout=10)
                self._ssh_master = None

    def get_url(self, filename: str, host: str = "localhost") -> str:
        """
        Get the HTTP URL for a file being served.

        Args:
            filename: Name of the file to get URL for
            host: Hostname or IP address to use in URL

        Returns:
            Full HTTP URL for the file
        """
        if not self.actual_port:
            raise RuntimeError("Server not started")

        return f"http://{host}:{self.actual_port}/{filename}"

    def __enter__(self):
        """Context manager entry - start server."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - stop server."""
        self.stop()
        return False
