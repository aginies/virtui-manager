"""
Manages multiple libvirt connections.
"""
import logging
import threading
import libvirt
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from .constants import AppCacheTimeout

class ConnectionManager:
    """A class to manage opening, closing, and storing multiple libvirt connections."""

    class BaseWrapper:
        """Base class for proxying libvirt objects to track statistics."""
        def __init__(self, obj, uri, manager):
            self._obj = obj
            self._uri = uri
            self._manager = manager
        def __getattr__(self, name):
            attr = getattr(self._obj, name)
            if callable(attr):
                def wrapped(*args, **kwargs):
                    self._manager._record_call(self._uri, name)
                    res = attr(*args, **kwargs)

                    # Wrap returned domain objects to track their calls too
                    if isinstance(res, libvirt.virDomain):
                        return ConnectionManager.DomainWrapper(res, self._uri, self._manager)
                    if isinstance(res, list) and res and isinstance(res[0], libvirt.virDomain):
                        return [ConnectionManager.DomainWrapper(d, self._uri, self._manager) for d in res]
                    return res
                return wrapped
            return attr

        def __dir__(self):
            return dir(self._obj)

    class ConnectionWrapper(BaseWrapper):
        """Proxies libvirt connection calls."""
        pass

    class DomainWrapper(BaseWrapper):
        """Proxies libvirt domain calls."""
        pass

    def __init__(self):
        """Initializes the ConnectionManager."""
        self.connections: dict[str, libvirt.virConnect] = {}  # uri -> virConnect object
        self.connection_errors: dict[str, str] = {}           # uri -> error message
        self.call_stats: dict[str, dict[str, int]] = {}       # uri -> {method -> count}
        self._lock = threading.RLock()
        self._alive_cache = {}  # Cache liveness (defaut 30s)
        self._alive_lock = threading.RLock()
        self._last_check = {}   # Per-URI last check time
        self._failed_attempts: dict[str, int] = {} # uri -> count


    def _record_call(self, uri: str, method_name: str):
        """Increments the call counter for a URI and method."""
        with self._lock:
            if uri not in self.call_stats:
                self.call_stats[uri] = {}
            stats = self.call_stats[uri]
            stats[method_name] = stats.get(method_name, 0) + 1

    def _is_alive_fast(self, uri: str, conn) -> bool:
        """Cheap liveness check using cached timestamp."""
        import time
        now = time.time()
        
        with self._alive_lock:
            last = self._last_check.get(uri, 0)
            if now - last < AppCacheTimeout.INFO_CACHE_TTL:
                return self._alive_cache.get(uri, True)

        # Perform libvirt call outside lock
        alive = False
        try:
            # Cheaper than getLibVersion(): listDefinedDomains(0) no-op if cached
            conn.listDefinedDomains()
            alive = True
        except libvirt.libvirtError:
            alive = False

        with self._alive_lock:
            self._alive_cache[uri] = alive
            self._last_check[uri] = now
            return alive

    def get_stats(self) -> dict[str, dict[str, int]]:
        """Returns the call statistics."""
        with self._lock:
            # Return a deep copy
            return {uri: methods.copy() for uri, methods in self.call_stats.items()}

    def reset_stats(self):
        """Resets all call statistics."""
        with self._lock:
            self.call_stats.clear()

    def connect(self, uri: str, force_retry: bool = False) -> libvirt.virConnect | None:
        """
        Connects to a given URI. If already connected, returns the existing connection.
        If the existing connection is dead, it will attempt to reconnect.
        Args:
            uri: The URI to connect to.
            force_retry: If True, resets the failure counter and attempts connection even if previously failed.
        """
        if force_retry:
            self.reset_failure_count(uri)

        # Check for max retries
        if self._failed_attempts.get(uri, 0) >= 2:
             logging.debug(f"Connection to {uri} failed 2 times. Skipping retry.")
             return None

        conn = None
        with self._lock:
            conn = self.connections.get(uri)

        if conn:
            # Check if the connection is still alive and try to reconnect if not
            try:
                # Test the connection by calling a simple libvirt function
                # Note: This call itself will be recorded if we use the wrapper, 
                # but here we use the raw conn to test.
                if not self._is_alive_fast(uri, conn):
                    logging.warning(f"Connection to {uri} is dead, reconnecting...")
                return self.ConnectionWrapper(conn, uri, self)
            except libvirt.libvirtError:
                # Connection is dead, remove it and create a new one
                logging.warning(f"Connection to {uri} is dead, reconnecting...")
                self.disconnect(uri)
                new_conn = self._create_connection(uri)
                return self.ConnectionWrapper(new_conn, uri, self) if new_conn else None

        new_conn = self._create_connection(uri)
        return self.ConnectionWrapper(new_conn, uri, self) if new_conn else None

    def reset_failure_count(self, uri: str):
        """Resets the failure count for a URI."""
        with self._lock:
            self._failed_attempts[uri] = 0

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
                    conn = future.result(timeout=15)
                    executor.shutdown(wait=True)
                except TimeoutError:
                    # If it times out, we raise a libvirtError to be caught by the existing error handling.
                    executor.shutdown(wait=False)
                    msg = "Connection timed out after 15 seconds."
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

            # Enable keepalive for remote connections
            # Local connections (qemu:///system, etc.) usually don't support it and raise an error
            is_remote = 'ssh' in uri.lower() or 'tcp' in uri.lower() or 'tls' in uri.lower()
            if is_remote:
                try:
                    conn.setKeepAlive(5, 3)
                except libvirt.libvirtError as e:
                    logging.warning(f"Failed to set keepalive for {uri}: {e}")

            with self._lock:
                self.connections[uri] = conn
                self._failed_attempts[uri] = 0 # Reset failure count on success
                if uri in self.connection_errors:
                    del self.connection_errors[uri]  # Clear previous error on successful connect
            return conn
        except libvirt.libvirtError as e:
            # Increment failure count
            with self._lock:
                self._failed_attempts[uri] = self._failed_attempts.get(uri, 0) + 1
                count = self._failed_attempts[uri]
            
            error_message = f"Failed to connect to '{uri}' (Attempt {count}/2): {e}"
            if count >= 2 and count < 3:
                error_message += " - Max retries reached. Will not try again."
            
            logging.error(error_message)
            
            with self._lock:
                self.connection_errors[uri] = error_message
                if uri in self.connections:
                    del self.connections[uri]  # Clean up failed connection attempt
            return None

    def disconnect(self, uri: str) -> bool:
        """
        Closes and removes a specific connection from the manager.
        """
        conn_to_close = None
        with self._lock:
            if uri in self.connections:
                conn_to_close = self.connections[uri]
                del self.connections[uri]

        if conn_to_close:
            try:
                conn_to_close.close()
                logging.info(f"Closed connection to {uri}")
            except libvirt.libvirtError as e:
                logging.error(f"Error closing connection to {uri}: {e}")
            except Exception as e:
                logging.error(f"Unexpected error closing connection to {uri}: {e}")
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
            conn = self.connections.get(uri)
            if conn:
                return self.ConnectionWrapper(conn, uri, self)
            return None

    def get_uri_for_connection(self, conn: libvirt.virConnect) -> str | None:
        """
        Returns the URI string associated with a given connection object.
        """
        # Handle wrapped connection
        if isinstance(conn, self.ConnectionWrapper):
            return conn._uri

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

    def get_failed_attempts(self, uri: str) -> int:
        """
        Returns the number of failed connection attempts for a given URI.
        """
        with self._lock:
            return self._failed_attempts.get(uri, 0)

    def is_max_retries_reached(self, uri: str) -> bool:
        """
        Checks if the maximum number of connection retries has been reached for a URI.
        """
        with self._lock:
            return self._failed_attempts.get(uri, 0) >= 3

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
