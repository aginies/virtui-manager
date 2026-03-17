"""
Auto HTTP Server

Simple HTTP server for serving Auto configuration files during VM installation.
The server runs in a background thread and automatically stops after the VM installation completes.
"""

import atexit
import http.server
import logging
import socketserver
import threading
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
