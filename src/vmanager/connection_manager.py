"""
Manages multiple libvirt connections.
"""
import logging
import threading
import libvirt
from concurrent.futures import ThreadPoolExecutor, TimeoutError

class ConnectionManager:
    """A class to manage opening, closing, and storing multiple libvirt connections."""

    def __init__(self):
        """Initializes the ConnectionManager."""
        self.connections: dict[str, libvirt.virConnect] = {}  # uri -> virConnect object
        self.connection_errors: dict[str, str] = {}           # uri -> error message
        self._lock = threading.RLock()

    def connect(self, uri: str) -> libvirt.virConnect | None:
        """
        Connects to a given URI. If already connected, returns the existing connection.
        If the existing connection is dead, it will attempt to reconnect.
        """
        conn = None
        with self._lock:
            conn = self.connections.get(uri)

        if conn:
            # Check if the connection is still alive and try to reconnect if not
            try:
                # Test the connection by calling a simple libvirt function
                conn.getLibVersion()
                return conn
            except libvirt.libvirtError:
                # Connection is dead, remove it and create a new one
                logging.warning(f"Connection to {uri} is dead, reconnecting...")
                self.disconnect(uri)
                return self._create_connection(uri)

        return self._create_connection(uri)

    def _create_connection(self, uri: str) -> libvirt.virConnect | None:
        """
        Creates a new connection to the given URI with a timeout.
        """
        try:
            logging.info(f"Opening new libvirt connection to {uri}")

            def open_connection():
                connect_uri = uri
                # Append no_tty=1 to prevent interactive password prompts
                if 'ssh' in uri.lower() and 'no_tty=' not in uri:
                    sep = '&' if '?' in uri else '?'
                    connect_uri += f"{sep}no_tty=1"
                return libvirt.open(connect_uri)

            executor = ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(open_connection)
                try:
                    # Wait for 10 seconds for the connection to establish
                    conn = future.result(timeout=10)
                    executor.shutdown(wait=True)
                except TimeoutError:
                    # If it times out, we raise a libvirtError to be caught by the existing error handling.
                    executor.shutdown(wait=False)
                    msg = "Connection timed out after 10 seconds."
                    # Check if the URI suggests an SSH connection
                    if 'ssh' in uri.lower(): # Use .lower() for robustness
                        msg += " If using SSH, this can happen if a password or SSH key passphrase is required."
                        msg += " Please use an SSH agent or a key without a passphrase, as interactive prompts are not supported."
                    raise libvirt.libvirtError(msg)
            except Exception:
                # Ensure executor is shut down in case of other errors during submission/execution
                executor.shutdown(wait=False)
                raise

            if conn is None:
                # This case can happen if the URI is valid but the hypervisor is not running
                raise libvirt.libvirtError(f"libvirt.open('{uri}') returned None")

            with self._lock:
                self.connections[uri] = conn
                if uri in self.connection_errors:
                    del self.connection_errors[uri]  # Clear previous error on successful connect
            return conn
        except libvirt.libvirtError as e:
            error_message = f"Failed to connect to '{uri}': {e}"
            logging.error(error_message)
            with self._lock:
                self.connection_errors[uri] = str(e)
                if uri in self.connections:
                    del self.connections[uri]  # Clean up failed connection attempt
            return None

    def disconnect(self, uri: str) -> bool:
        """
        Closes and removes a specific connection from the manager.
        """
        with self._lock:
            if uri in self.connections:
                try:
                    self.connections[uri].close()
                    logging.info(f"Closed connection to {uri}")
                except libvirt.libvirtError as e:
                    logging.error(f"Error closing connection to {uri}: {e}")
                finally:
                    if uri in self.connections:
                        del self.connections[uri]
                    return True
        return False

    def disconnect_all(self) -> None:
        """Closes all active connections managed by this instance."""
        logging.info("Closing all active libvirt connections.")
        with self._lock:
            uris = list(self.connections.keys())
        
        for uri in uris:
            self.disconnect(uri)

    def get_connection(self, uri: str) -> libvirt.virConnect | None:
        """
        Retrieves an active connection object for a given URI.
        """
        with self._lock:
            return self.connections.get(uri)

    def get_uri_for_connection(self, conn: libvirt.virConnect) -> str | None:
        """
        Returns the URI string associated with a given connection object.
        """
        with self._lock:
            for uri, stored_conn in self.connections.items():
                if stored_conn == conn:
                    return uri
        return None

    def get_all_connections(self) -> list[libvirt.virConnect]:
        """
        Returns a list of all active libvirt connection objects.
        """
        with self._lock:
            return list(self.connections.values())

    def get_all_uris(self) -> list[str]:
        """
        Returns a list of all URIs with active connections.
        """
        with self._lock:
            return list(self.connections.keys())

    def get_connection_error(self, uri: str) -> str | None:
        """
        Returns the last error message for a given URI, or None if no error.
        """
        with self._lock:
            return self.connection_errors.get(uri)

    def has_connection(self, uri: str) -> bool:
        """
        Checks if a connection to the given URI exists.
        """
        with self._lock:
            return uri in self.connections

    def is_connection_alive(self, uri: str) -> bool:
        """
        Checks if a connection to the given URI is alive.
        """
        with self._lock:
            conn = self.connections.get(uri)

        if not conn:
            return False
        
        try:
            # Test the connection by calling a simple libvirt function
            conn.getLibVersion()
            return True
        except libvirt.libvirtError:
            return False
