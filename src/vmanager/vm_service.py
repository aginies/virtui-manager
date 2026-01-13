"""
VM Service Layer
Handles all libvirt interactions and data processing.
"""
import time
import threading
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
import libvirt
from connection_manager import ConnectionManager
from constants import VmStatus, VmAction, AppCacheTimeout
from storage_manager import check_domain_volumes_in_use
from utils import natural_sort_key
from vm_actions import start_vm, stop_vm, force_off_vm, pause_vm, delete_vm
from vm_queries import (
    get_status, get_vm_description, get_vm_machine_info, get_vm_firmware_info,
    get_vm_networks_info, get_vm_network_ip, get_vm_network_dns_gateway_info,
    get_vm_disks_info, get_vm_devices_info, get_vm_shared_memory_info,
    get_boot_info, get_vm_video_model, get_vm_cpu_model
)

# Global event loop management
_event_loop_thread = None
_event_loop_running = False
_event_loop_lock = threading.Lock()

def _event_loop():
    """Event loop for libvirt events."""
    global _event_loop_running
    while _event_loop_running:
        try:
            libvirt.virEventRunDefaultImpl()
        except Exception as e:
            logging.error(f"Error in libvirt event loop: {e}")
            time.sleep(1)

def _start_event_loop():
    """Start the libvirt event loop if not already running."""
    global _event_loop_thread, _event_loop_running
    with _event_loop_lock:
        if not _event_loop_running:
            libvirt.virEventRegisterDefaultImpl()
            # Register a keepalive timer to ensure the loop wakes up periodically
            try:
                libvirt.virEventAddTimeout(1000, lambda t, id: None, None)
            except Exception:
                pass # Ignore if already registered or fails

            _event_loop_running = True
            _event_loop_thread = threading.Thread(target=_event_loop, daemon=True, name="LibvirtEventLoop")
            _event_loop_thread.start()
            logging.info("Started libvirt event loop")

def _stop_event_loop():
    """Stop the libvirt event loop."""
    global _event_loop_running, _event_loop_thread
    with _event_loop_lock:
        _event_loop_running = False
    
    if _event_loop_thread and _event_loop_thread.is_alive():
        try:
            _event_loop_thread.join(timeout=0.2)
        except Exception:
            pass


class VMService:
    """A service class to abstract libvirt operations."""

    def __init__(self):
        _start_event_loop()
        self.connection_manager = ConnectionManager()
        self._cpu_time_cache = {} # Cache for calculating CPU usage {uuid: (last_time, last_timestamp)}
        self._io_stats_cache = {} # Cache for calculating Disk/Net I/O {uuid: {'ts': ts, 'disk_read': bytes, ...}}
        self._domain_cache: dict[str, libvirt.virDomain] = {}
        self._uuid_to_conn_cache: dict[str, libvirt.virConnect] = {}

        self._vm_data_cache: dict[str, dict] = {}  # {uuid: {'info': (data), 'info_ts': ts, 'xml': 'data', 'xml_ts': ts}}
        self._name_to_uuid_cache: dict[str, dict[str, str]] = {} # {uri: {name: uuid}}
        self._uuid_to_name_cache: dict[str, dict[str, str]] = {} # {uri: {uuid: name}}

        self._info_cache_ttl: int = AppCacheTimeout.INFO_CACHE_TTL
        self._xml_cache_ttl: int = AppCacheTimeout.XML_CACHE_TTL
        self._details_cache_ttl: int = AppCacheTimeout.DETAILS_CACHE_TTL
        self._visible_uuids: set[str] = set()
        self._event_callbacks: dict[str, int] = {}  # {uri: callback_id}
        self._events_enabled: bool = True
        self._registration_lock = threading.Lock()

        # Threading support
        self._cache_lock = threading.RLock()
        self._active_uris_lock = threading.RLock()
        self._active_uris: list[str] = []
        self._monitoring_active = False
        self._monitor_thread = None
        self._data_update_callback = None
        self._vm_update_callback = None
        self._message_callback = None
        self._force_update_event = threading.Event()
        self.start_monitoring()

    def set_data_update_callback(self, callback):
        """Sets a callback to be invoked when background data update finishes."""
        self._data_update_callback = callback

    def set_vm_update_callback(self, callback):
        """Sets a callback to be invoked when a specific VM updates (UUID)."""
        self._vm_update_callback = callback

    def set_message_callback(self, callback):
        """Sets a callback to be invoked for user-facing messages."""
        self._message_callback = callback

    def update_visible_uuids(self, uuids: set[str]):
        """Updates the set of UUIDs currently visible in the UI."""
        with self._cache_lock:
            self._visible_uuids = uuids

    def start_monitoring(self):
        """Starts the background monitoring thread."""
        if self._monitoring_active:
            return
        self._monitoring_active = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="VMServiceMonitor")
        self._monitor_thread.start()

    def stop_monitoring(self):
        """Stops the background monitoring thread."""
        self._monitoring_active = False
        self._force_update_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1.0)

    def _invalidate_cache_for_uri(self, uri: str):
        """Invalidates all cache entries associated with a specific URI."""
        with self._cache_lock:
            # Identify keys belonging to this URI
            keys_to_invalidate = []
            suffix = f"@{uri}"

            # Helper to find keys in a dict
            def find_keys(cache_dict):
                return [k for k in cache_dict.keys() if k.endswith(suffix)]

            keys_to_invalidate.extend(find_keys(self._vm_data_cache))

            # Check other caches
            other_caches = [
                self._cpu_time_cache, self._io_stats_cache,
                self._domain_cache, self._uuid_to_conn_cache
            ]
            for cache in other_caches:
                keys_to_invalidate.extend(find_keys(cache))

            # Remove duplicates
            keys_to_invalidate = list(set(keys_to_invalidate))

            for k in keys_to_invalidate:
                if k in self._vm_data_cache: del self._vm_data_cache[k]
                if k in self._cpu_time_cache: del self._cpu_time_cache[k]
                if k in self._io_stats_cache: del self._io_stats_cache[k]
                if k in self._domain_cache: del self._domain_cache[k]
                if k in self._uuid_to_conn_cache: del self._uuid_to_conn_cache[k]

            # Clean identity cache
            if uri in self._name_to_uuid_cache:
                del self._name_to_uuid_cache[uri]
            if uri in self._uuid_to_name_cache:
                del self._uuid_to_name_cache[uri]

            logging.info(f"Invalidated {len(keys_to_invalidate)} cache entries for URI: {uri}")

    def _connection_close_callback(self, conn, reason, opaque):
        """Callback for when a connection is closed."""
        uri = opaque
        reason_str = "Unknown"
        # Map reason codes (safe lookup)
        reasons = {
            libvirt.VIR_CONNECT_CLOSE_REASON_ERROR: "Error",
            libvirt.VIR_CONNECT_CLOSE_REASON_EOF: "End of file",
            libvirt.VIR_CONNECT_CLOSE_REASON_KEEPALIVE: "Keepalive timeout",
            libvirt.VIR_CONNECT_CLOSE_REASON_CLIENT: "Client requested",
        }
        reason_str = reasons.get(reason, f"Code {reason}")

        logging.warning(f"Connection to {uri} closed: {reason_str}")

        # Send message to UI
        if self._message_callback:
            self._message_callback("warning", f"Connection to {uri} lost: {reason_str}. Attempting to reconnect...")

        # Clean up event callbacks tracking
        with self._registration_lock:
            if uri in self._event_callbacks:
                del self._event_callbacks[uri]

        # Invalidate cache for this URI
        self._invalidate_cache_for_uri(uri)
        self.connection_manager.disconnect(uri)

        # Notify UI
        self._force_update_event.set()
        if self._data_update_callback:
            try:
                self._data_update_callback()
            except Exception as e:
                logging.error(f"Error in data update callback during close: {e}")

    def _register_domain_events(self, conn: libvirt.virConnect, uri: str):
        """Register for domain lifecycle events to invalidate cache reactively."""

        # Register close callback (always, even if events are disabled, to handle disconnects)
        try:
            conn.registerCloseCallback(self._connection_close_callback, uri)
            logging.info(f"Registered close callback for {uri}")
        except Exception as e:
            logging.warning(f"Failed to register close callback for {uri}: {e}")

        if not self._events_enabled:
            return

        def lifecycle_callback(conn, domain, event, detail, opaque):
            try:
                internal_id = self._get_internal_id(domain, conn, known_uri=uri)
                logging.debug(f"Domain event: {event} detail: {detail} for {internal_id}")

                new_state = None
                event_msg = None
                msg_level = "info"

                if event == libvirt.VIR_DOMAIN_EVENT_STOPPED:
                    new_state = libvirt.VIR_DOMAIN_SHUTOFF
                    event_msg = "Shutdown signal"
                elif event == libvirt.VIR_DOMAIN_EVENT_STARTED:
                    new_state = libvirt.VIR_DOMAIN_RUNNING
                    event_msg = "Started"
                elif event == libvirt.VIR_DOMAIN_EVENT_SUSPENDED:
                    new_state = libvirt.VIR_DOMAIN_PAUSED
                    event_msg = "Paused"
                elif event == libvirt.VIR_DOMAIN_EVENT_RESUMED:
                    new_state = libvirt.VIR_DOMAIN_RUNNING
                    event_msg = "Resumed"
                elif event == libvirt.VIR_DOMAIN_EVENT_PMSUSPENDED:
                    new_state = libvirt.VIR_DOMAIN_PMSUSPENDED
                    event_msg = "PM Suspended"
                elif event == libvirt.VIR_DOMAIN_EVENT_CRASHED:
                    new_state = libvirt.VIR_DOMAIN_CRASHED
                    event_msg = "Crashed"
                    msg_level = "error"

                if self._message_callback:
                    try:
                        _, vm_name = self.get_vm_identity(domain, conn, known_uri=uri)

                        final_msg = None
                        if event_msg:
                            final_msg = f"VM [b]{vm_name}[/b] {event_msg}"
                        elif event == libvirt.VIR_DOMAIN_EVENT_DEFINED:
                            # Use detail to differentiate? 0=Added, 1=Updated
                            action_str = "Configuration Updated" if detail == 1 else "Defined"
                            final_msg = f"VM [b]{vm_name}[/b] {action_str}"
                        elif event == libvirt.VIR_DOMAIN_EVENT_UNDEFINED:
                            final_msg = f"VM [b]{vm_name}[/b] Undefined (Deleted)"

                        if final_msg:
                            self._message_callback(msg_level, final_msg)
                    except Exception:
                        pass # Don't let notification errors break the handler

                with self._cache_lock:
                    if event == libvirt.VIR_DOMAIN_EVENT_DEFINED:
                        self._domain_cache[internal_id] = domain
                        self._uuid_to_conn_cache[internal_id] = conn
                        new_state = libvirt.VIR_DOMAIN_SHUTOFF
                        # Also update name cache
                        self.get_vm_identity(domain, conn, known_uri=uri)
                        # Full update for list changes
                        if self._data_update_callback:
                            self._data_update_callback()

                    elif event == libvirt.VIR_DOMAIN_EVENT_UNDEFINED:
                        self.invalidate_vm_cache(internal_id)
                        # Trigger full update via event for list change
                        self._force_update_event.set()
                        if self._data_update_callback:
                            self._data_update_callback()
                        return

                    if new_state is not None:
                        self._vm_data_cache.setdefault(internal_id, {})
                        self._vm_data_cache[internal_id]['state'] = (new_state, detail)
                        self._vm_data_cache[internal_id]['state_ts'] = time.time()

                        # Invalidate info cache if state changed to/from running so we re-fetch details
                        # but we can rely on next get_vms call to do that 1-time fetch
                        if new_state == libvirt.VIR_DOMAIN_RUNNING:
                            # If it just started, we might want to clear old info
                            if 'info' in self._vm_data_cache[internal_id]:
                                del self._vm_data_cache[internal_id]['info']
                        
                        # Notify for specific VM update
                        if self._vm_update_callback:
                            self._vm_update_callback(internal_id)
                        elif self._data_update_callback:
                             # Fallback to full update if no specific callback
                             self._data_update_callback()

            except Exception as e:
                logging.error(f"Error in lifecycle callback: {e}")

        try:
            callback_id = conn.domainEventRegisterAny(
                None,
                libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
                lifecycle_callback,
                None
            )
            # Store connection object to detect changes
            self._event_callbacks[uri] = (conn, callback_id)
            logging.info(f"Registered domain events for {uri}")
        except libvirt.libvirtError as e:
            logging.warning(f"Could not register domain events for {uri}: {e}")
            if "event timer" in str(e).lower() or "event loop" in str(e).lower():
                logging.warning("Disabling event system due to event loop issues")
                self._events_enabled = False

    def _unregister_domain_events(self, conn: libvirt.virConnect, uri: str):
        """Unregister domain events."""
        with self._registration_lock:
            # Unregister close callback
            try:
                conn.unregisterCloseCallback(self._connection_close_callback)
            except Exception:
                pass

            if uri in self._event_callbacks:
                try:
                    # Unwrap tuple
                    _, callback_id = self._event_callbacks[uri]
                    conn.domainEventDeregisterAny(callback_id)
                    del self._event_callbacks[uri]
                    logging.info(f"Unregistered domain events for {uri}")
                except libvirt.libvirtError as e:
                    logging.warning(f"Could not unregister domain events for {uri}: {e}")

    def get_vm_identity(self, domain: libvirt.virDomain, conn: libvirt.virConnect = None, known_uri: str = None) -> tuple[str, str]:
        """
        Returns (internal_id, name) for a domain.
        High-performance method using bidirectional caching to avoid libvirt calls.
        """
        try:
            if known_uri:
                uri = known_uri
            else:
                if not conn:
                    conn = domain.connect()
                uri = conn.getURI()

            name = domain.name()

            with self._cache_lock:
                if uri in self._name_to_uuid_cache:
                    uuid = self._name_to_uuid_cache[uri].get(name)
                    if uuid:
                        return f"{uuid}@{uri}", name

                uuid = domain.UUIDString()

                self._name_to_uuid_cache.setdefault(uri, {})[name] = uuid
                self._uuid_to_name_cache.setdefault(uri, {})[uuid] = name

            return f"{uuid}@{uri}", name
        except libvirt.libvirtError:
            return "unknown", "unknown"

    def _get_internal_id(self, domain: libvirt.virDomain, conn: libvirt.virConnect = None, known_uri: str = None) -> str:
        """Generates a unique internal ID for a VM (UUID@URI). Uses name-to-UUID caching."""
        internal_id, _ = self.get_vm_identity(domain, conn, known_uri)
        return internal_id

    def _monitor_loop(self):
        """Background loop to update VM data."""
        while self._monitoring_active:
            with self._active_uris_lock:
                uris = list(self._active_uris)

            if uris:
                try:
                    self._perform_background_update(uris)
                except Exception as e:
                    logging.error(f"Error in background update loop: {e}")

            # Wait for next cycle or force update
            # Timeout increased to 60s (heartbeat) to avoid polling, relying on events instead.
            self._force_update_event.wait(timeout=60.0)
            if self._force_update_event.is_set():
                self._force_update_event.clear()
                # If forced, we should callback
                if self._data_update_callback:
                    try:
                        self._data_update_callback()
                    except Exception as e:
                        logging.error(f"Error in data update callback: {e}")

    def _perform_background_update(self, active_uris: list[str]):
        """Fetches data for all VMs on active URIs and updates cache."""

        # 1. Connect and list domains
        active_connections = []
        for uri in active_uris:
            conn = self.connect(uri)
            if conn:
                active_connections.append(conn)

        new_domain_cache = {}
        new_uuid_to_conn = {}

        for conn in active_connections:
            # Optimization: Get URI from manager instead of making a libvirt call
            conn_uri = self.connection_manager.get_uri_for_connection(conn)
            try:
                domains = conn.listAllDomains(0) or []
                for domain in domains:
                    # Pass the known URI to avoid another libvirt call inside
                    internal_id, _ = self.get_vm_identity(domain, conn, known_uri=conn_uri)
                    new_domain_cache[internal_id] = domain
                    new_uuid_to_conn[internal_id] = conn
            except libvirt.libvirtError:
                pass

        # Update domain list cache
        with self._cache_lock:
            self._domain_cache = new_domain_cache
            self._uuid_to_conn_cache = new_uuid_to_conn
            visible_uuids = self._visible_uuids.copy()

        # 2. Fetch state/info for visible domains only
        # IF events are enabled, we do NOT poll state here.
        if not self._events_enabled:
            for uuid, domain in new_domain_cache.items():
                if not self._monitoring_active:
                    break

                # ONLY update info for visible VMs in background
                if uuid not in visible_uuids:
                    continue

                try:
                    # Use state() instead of info() - lighter call
                    state, reason = domain.state()
                    now = time.time()

                    with self._cache_lock:
                        self._vm_data_cache.setdefault(uuid, {})
                        vm_cache = self._vm_data_cache[uuid]
                        vm_cache['state'] = (state, reason)
                        vm_cache['state_ts'] = now

                        # Only fetch full info for running/paused VMs
                        if state in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]:
                            try:
                                info = domain.info()
                                vm_cache['info'] = info
                                vm_cache['info_ts'] = now
                                logging.debug(f"Background Cache WRITE for VM info: {uuid}")
                            except libvirt.libvirtError:
                                pass

                except Exception as e:
                    logging.error(f"Error updating cache for VM {uuid}: {e}")

    def invalidate_domain_cache(self):
        """Invalidates the domain cache."""
        with self._cache_lock:
            self._domain_cache.clear()
            self._uuid_to_conn_cache.clear()
            self._name_to_uuid_cache.clear()

    def invalidate_vm_state_cache(self, uuid: str):
        """Invalidates only state/info/xml/stats caches, keeping the domain object."""
        with self._cache_lock:
            # Determine keys (handle UUID vs UUID@URI)
            if "@" in uuid:
                keys_to_invalidate = [uuid]
            else:
                keys_to_invalidate = [
                    k for k in self._vm_data_cache.keys() 
                    if k == uuid or k.startswith(f"{uuid}@")
                ]
                # Also check other caches just in case keys are there but not in data cache
                other_caches = [self._cpu_time_cache, self._io_stats_cache]
                for cache in other_caches:
                    for k in cache.keys():
                        if (k == uuid or k.startswith(f"{uuid}@")) and k not in keys_to_invalidate:
                            keys_to_invalidate.append(k)

            for k in keys_to_invalidate:
                if k in self._vm_data_cache:
                    del self._vm_data_cache[k]
                if k in self._cpu_time_cache:
                    del self._cpu_time_cache[k]
                if k in self._io_stats_cache:
                    del self._io_stats_cache[k]

                logging.debug(f"Invalidated VM state cache for: {k}")

    def invalidate_vm_cache(self, uuid: str):
        """Invalidates all cached data for a specific VM."""
        with self._cache_lock:
            # Determine which keys to invalidate
            if "@" in uuid:
                # Explicit composite ID provided
                keys_to_invalidate = [uuid]
            else:
                # Plain UUID provided, find all composite IDs that start with it
                # or match it exactly (in case of failed URI lookup during creation)
                keys_to_invalidate = [
                    k for k in self._vm_data_cache.keys() 
                    if k == uuid or k.startswith(f"{uuid}@")
                ]
                # Also check other caches
                other_caches = [
                    self._cpu_time_cache, self._io_stats_cache,
                    self._domain_cache, self._uuid_to_conn_cache
                ]
                for cache in other_caches:
                    for k in cache.keys():
                        if (k == uuid or k.startswith(f"{uuid}@")) and k not in keys_to_invalidate:
                            keys_to_invalidate.append(k)

            for k in keys_to_invalidate:
                if k in self._vm_data_cache:
                    del self._vm_data_cache[k]
                if k in self._cpu_time_cache:
                    del self._cpu_time_cache[k]
                if k in self._io_stats_cache:
                    del self._io_stats_cache[k]
                if k in self._domain_cache:
                    del self._domain_cache[k]
                if k in self._uuid_to_conn_cache:
                    del self._uuid_to_conn_cache[k]

                # Invalidate bidirectional identity cache
                raw_uuid = k.split('@')[0]
                for uri in list(self._name_to_uuid_cache.keys()):
                    # Remove from UUID -> Name
                    if uri in self._uuid_to_name_cache and raw_uuid in self._uuid_to_name_cache[uri]:
                        name = self._uuid_to_name_cache[uri].pop(raw_uuid)
                        # Remove from Name -> UUID
                        if uri in self._name_to_uuid_cache and name in self._name_to_uuid_cache[uri]:
                            del self._name_to_uuid_cache[uri][name]

                logging.info(f"Invalidated VM cache for: {k}")

    def _update_domain_cache(self, active_uris: list[str], force: bool = False, preload: bool = False):
        """Updates the domain and connection cache."""
        with self._active_uris_lock:
            current_set = set(self._active_uris)
            new_set = set(active_uris)
            if current_set != new_set:
                self._active_uris = list(active_uris)
                force = True

        if force:
            # Synchronous update of domain list
            active_connections = []
            for uri in active_uris:
                conn = self.connect(uri)
                if conn:
                    active_connections.append(conn)

            new_domain_cache = {}
            new_uuid_to_conn = {}

            for conn in active_connections:
                try:
                    domains = conn.listAllDomains(0) or []
                    for domain in domains:
                        internal_id = self._get_internal_id(domain, conn)
                        new_domain_cache[internal_id] = domain
                        new_uuid_to_conn[internal_id] = conn
                except libvirt.libvirtError:
                    pass

            with self._cache_lock:
                self._domain_cache = new_domain_cache
                self._uuid_to_conn_cache = new_uuid_to_conn

        if preload:
            # Pre-load info and XML for all domains to warm up the cache
            with self._cache_lock:
                domains = list(self._domain_cache.values())

            for domain in domains:
                try:
                    #self._get_domain_info(domain)
                    ##self._get_domain_info_and_xml(domain)
                    # Use state() for preload instead of full info()
                    self._get_domain_state(domain)
                except libvirt.libvirtError:
                    pass

    def _update_target_uris(self, active_uris: list[str], force: bool = False):
        with self._active_uris_lock:
            current_set = set(self._active_uris)
            new_set = set(active_uris)
            if current_set != new_set:
                self._active_uris = list(active_uris)
                force = True

        if force:
            self._force_update_event.set()

    def _get_domain_state(self, domain: libvirt.virDomain, internal_id: str = None) -> tuple | None:
        """Gets domain state from cache or fetches it (lighter than info)."""
        uuid = internal_id or self._get_internal_id(domain)
        now = time.time()

        with self._cache_lock:
            self._vm_data_cache.setdefault(uuid, {})
            vm_cache = self._vm_data_cache[uuid]

            state = vm_cache.get('state')

        # If events are enabled, we trust the cache after the first fetch
        # and do not expire it based on time.
        should_fetch = (state is None)

        if should_fetch:
            try:
                state = domain.state()
                with self._cache_lock:
                    self._vm_data_cache.setdefault(uuid, {})
                    vm_cache = self._vm_data_cache[uuid]
                    vm_cache['state'] = state
                    vm_cache['state_ts'] = now
                    logging.debug(f"Cache WRITE for VM state: {uuid}")
            except libvirt.libvirtError:
                return None
        else:
            logging.debug(f"Cache HIT for VM state: {uuid}")
        return state


    def _get_domain_info_and_xml(self, domain: libvirt.virDomain, internal_id: str = None) -> tuple[tuple, str]:
        """Gets info and XML from cache or fetches them, fetching both if both are missing."""
        info = self._get_domain_info(domain, internal_id)
        xml = self._get_domain_xml(domain, internal_id)

        return info, xml

    def _get_domain_info(self, domain: libvirt.virDomain, internal_id: str = None) -> tuple | None:
        """Gets domain info from cache or fetches it."""
        uuid = internal_id or self._get_internal_id(domain)
        now = time.time()

        with self._cache_lock:
            self._vm_data_cache.setdefault(uuid, {})
            vm_cache = self._vm_data_cache[uuid]

            info = vm_cache.get('info')
            info_ts = vm_cache.get('info_ts', 0)

        # Use cached state if possible
        state_tuple = self._get_domain_state(domain, internal_id=uuid)
        state = state_tuple[0] if state_tuple else None

        # Fetch if Cache is empty or TTL has expired
        if (info is None) or \
           (now - info_ts >= self._info_cache_ttl) or \
           (state is not None and info[0] != state):
            try:
                info = domain.info()
                with self._cache_lock:
                    self._vm_data_cache.setdefault(uuid, {})
                    vm_cache = self._vm_data_cache[uuid]
                    vm_cache['info'] = info
                    vm_cache['info_ts'] = now
                    #logging.info(f"Cache WRITE for VM info: {uuid}")
            except libvirt.libvirtError:
                return None
        else:
            logging.debug(f"Cache HIT for VM info: {uuid}")
        return info

    def _get_domain_xml(self, domain: libvirt.virDomain, internal_id: str = None) -> str | None:
        """Gets domain XML from cache or fetches it."""
        uuid = internal_id or self._get_internal_id(domain)
        now = time.time()

        with self._cache_lock:
            self._vm_data_cache.setdefault(uuid, {})
            vm_cache = self._vm_data_cache[uuid]

            xml = vm_cache.get('xml')
            xml_ts = vm_cache.get('xml_ts', 0)

        if xml is None or (now - xml_ts >= self._xml_cache_ttl):
            try:
                xml = domain.XMLDesc(0)
                with self._cache_lock:
                    self._vm_data_cache.setdefault(uuid, {})
                    vm_cache = self._vm_data_cache[uuid]
                    vm_cache['xml'] = xml
                    vm_cache['xml_ts'] = now
                    logging.info(f"Cache WRITE for VM XML: {uuid}")
            except libvirt.libvirtError:
                return None
        else:
            logging.debug(f"Cache HIT for VM XML: {uuid}")
        return xml

    def _parse_xml_devices(self, xml_content: str) -> dict:
        """Parse XML once and extract all device information."""
        devices = {'disks': [], 'interfaces': []}
        if not xml_content:
            return devices

        try:
            root = ET.fromstring(xml_content)
            for disk in root.findall(".//devices/disk"):
                target = disk.find("target")
                if target is not None:
                    dev = target.get("dev")
                    if dev:
                        devices['disks'].append(dev)

            for interface in root.findall(".//devices/interface"):
                target = interface.find("target")
                if target is not None:
                    dev = target.get("dev")
                    if dev:
                        devices['interfaces'].append(dev)
        except ET.ParseError:
            pass

        return devices


    def get_cached_vm_info(self, domain: libvirt.virDomain) -> tuple | None:
        """Gets domain info ONLY from cache, returning None if not present or expired."""
        uuid = self._get_internal_id(domain)
        now = time.time()

        with self._cache_lock:
            if uuid not in self._vm_data_cache:
                return None

            vm_cache = self._vm_data_cache[uuid]
            info = vm_cache.get('info')
            info_ts = vm_cache.get('info_ts', 0)

            if info and (now - info_ts < self._info_cache_ttl):
                logging.debug(f"Cache HIT for VM info: {uuid}")
                return info
        return None

    def get_cached_vm_details(self, uuid: str) -> dict | None:
        """Returns cached VM details if available and not expired."""
        now = time.time()
        with self._cache_lock:
            vm_cache = self._vm_data_cache.get(uuid)
            if vm_cache:
                details = vm_cache.get('vm_details')
                details_ts = vm_cache.get('vm_details_ts', 0)
                if details and (now - details_ts < self._details_cache_ttl):
                    logging.debug(f"Cache HIT for VM details (cached method): {uuid}")
                    return details
        return None

    def get_vm_runtime_stats(self, domain: libvirt.virDomain) -> dict | None:
        """Gets live statistics for a given, active VM domain."""
        if not domain:
            return None

        try:
            #state, _ = domain.state()
            # Use cached state if available
            state, _ = self._get_domain_state(domain) or domain.state()
            status = get_status(domain, state=state)

            if state not in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]:
                return {
                    "status": status,
                    "cpu_percent": 0.0,
                    "mem_percent": 0.0,
                    "disk_read_kbps": 0.0,
                    "disk_write_kbps": 0.0,
                    "net_rx_kbps": 0.0,
                    "net_tx_kbps": 0.0
                }

            uuid = self._get_internal_id(domain)
            stats = {'status': status}

            # CPU Usage
            try:
                cpu_stats = domain.getCPUStats(True)
                logging.debug(f"Raw CPU Stats for {uuid}: {cpu_stats}")
            except Exception as e:
                logging.error(f"Error getting CPU stats for {uuid}: {e}")
                cpu_stats = []

            if not cpu_stats:
                current_cpu_time = 0
            else:
                current_cpu_time = cpu_stats[0]['cpu_time']

            now = datetime.now().timestamp()
            cpu_percent = 0.0
            last_cpu_time = None

            with self._cache_lock:
                last_cpu_data = self._cpu_time_cache.get(uuid)

            if last_cpu_data:
                last_cpu_time, last_cpu_time_ts = last_cpu_data
                time_diff = now - last_cpu_time_ts
                cpu_diff = current_cpu_time - last_cpu_time
                if time_diff > 0:
                    info = self._get_domain_info(domain)
                    if info:
                        num_cpus = info[3]
                        # nanoseconds to seconds, then divide by number of cpus
                        cpu_percent = (cpu_diff / (time_diff * 1_000_000_000)) * 100
                        cpu_percent = cpu_percent / num_cpus if num_cpus > 0 else 0

            stats['cpu_percent'] = cpu_percent
            with self._cache_lock:
                self._cpu_time_cache[uuid] = (current_cpu_time, now)

            # Memory Usage
            mem_stats = domain.memoryStats()
            mem_percent = 0.0
            if 'rss' in mem_stats:
                info = self._get_domain_info(domain)
                if info:
                    total_mem_kb = info[1]
                    if total_mem_kb > 0:
                        rss_kb = mem_stats['rss']
                        mem_percent = (rss_kb / total_mem_kb) * 100

            stats['mem_percent'] = mem_percent

            # Disk and Network I/O
            disk_read_bytes = 0
            disk_write_bytes = 0
            net_rx_bytes = 0
            net_tx_bytes = 0

            # Use cached XML if available, otherwise skip I/O stats to avoid libvirt XMLDesc call
            xml_content = self._get_domain_xml(domain) # Handles locking internally

            if not xml_content:
                # Skip I/O stats calculation if XML is not cached
                stats['disk_read_kbps'] = 0
                stats['disk_write_kbps'] = 0
                stats['net_rx_kbps'] = 0
                stats['net_tx_kbps'] = 0
                return stats

            # Use cached devices list if available and XML hasn't changed
            with self._cache_lock:
                self._vm_data_cache.setdefault(uuid, {})
                vm_cache = self._vm_data_cache[uuid]
                current_xml_ts = vm_cache.get('xml_ts', 0)
                cached_devices_ts = vm_cache.get('devices_ts', 0)
                devices_list = vm_cache.get('devices_list')

            if devices_list is None or cached_devices_ts != current_xml_ts:
                devices_list = self._parse_xml_devices(xml_content)

                with self._cache_lock:
                    vm_cache['devices_list'] = devices_list
                    vm_cache['devices_ts'] = current_xml_ts

            # Use cached devices to query stats
            for dev in devices_list['disks']:
                try:
                    # blockStats returns (rd_req, rd_bytes, wr_req, wr_bytes, errs)
                    b_stats = domain.blockStats(dev)
                    disk_read_bytes += b_stats[1]
                    disk_write_bytes += b_stats[3]
                except libvirt.libvirtError:
                    pass

            for dev in devices_list['interfaces']:
                try:
                    # interfaceStats returns (rx_bytes, rx_packets, rx_errs, rx_drop, tx_bytes, tx_packets, tx_errs, tx_drop)
                    i_stats = domain.interfaceStats(dev)
                    net_rx_bytes += i_stats[0]
                    net_tx_bytes += i_stats[4]
                except libvirt.libvirtError:
                    pass

            # Calculate I/O Rates
            disk_read_rate = 0.0
            disk_write_rate = 0.0
            net_rx_rate = 0.0
            net_tx_rate = 0.0

            with self._cache_lock:
                last_stats = self._io_stats_cache.get(uuid)

            if last_stats:
                last_ts = last_stats['ts']
                time_diff = now - last_ts

                if time_diff > 0:
                    # Prevent negative rates if counters reset
                    d_read = disk_read_bytes - last_stats['disk_read']
                    d_write = disk_write_bytes - last_stats['disk_write']
                    n_rx = net_rx_bytes - last_stats['net_rx']
                    n_tx = net_tx_bytes - last_stats['net_tx']

                    disk_read_rate = d_read / time_diff if d_read >= 0 else 0
                    disk_write_rate = d_write / time_diff if d_write >= 0 else 0
                    net_rx_rate = n_rx / time_diff if n_rx >= 0 else 0
                    net_tx_rate = n_tx / time_diff if n_tx >= 0 else 0

            # Store cache
            with self._cache_lock:
                self._io_stats_cache[uuid] = {
                    'ts': now,
                    'disk_read': disk_read_bytes,
                    'disk_write': disk_write_bytes,
                    'net_rx': net_rx_bytes,
                    'net_tx': net_tx_bytes
                }

            stats['disk_read_kbps'] = disk_read_rate / 1024
            stats['disk_write_kbps'] = disk_write_rate / 1024
            stats['net_rx_kbps'] = net_rx_rate / 1024
            stats['net_tx_kbps'] = net_tx_rate / 1024

            return stats

        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                # If domain disappears, remove it from cache
                self.invalidate_vm_cache(uuid)
            return None

    def connect(self, uri: str, force_retry: bool = False) -> libvirt.virConnect | None:
        """Connects to a libvirt URI."""
        #return self.connection_manager.connect(uri)
        conn = self.connection_manager.connect(uri, force_retry=force_retry)

        # Check if we need to re-register events because connection object changed
        if conn:
            with self._registration_lock:
                if uri in self._event_callbacks:
                    # Handle unwrapping if it's a wrapper from ConnectionManager
                    real_conn = conn._obj if hasattr(conn, '_obj') else conn
                    registered_conn, _ = self._event_callbacks[uri]
                    real_registered_conn = registered_conn._obj if hasattr(registered_conn, '_obj') else registered_conn

                    if real_conn != real_registered_conn:
                        logging.info(f"Connection object changed for {uri}, re-registering events")
                        # Remove old registration record (old conn is likely dead anyway)
                        del self._event_callbacks[uri]

                if uri not in self._event_callbacks:
                    self._register_domain_events(conn, uri)
        return conn

    def reset_connection_failures(self, uri: str):
        """Resets the failure count for a URI."""
        self.connection_manager.reset_failure_count(uri)

    def disconnect(self, uri: str) -> None:
        """Disconnects from a libvirt URI and cleans up associated VM caches."""
        # Unregister events first
        conn = self.connection_manager.get_connection(uri)
        if conn:
            self._unregister_domain_events(conn, uri)

        # Find UUIDs associated with this URI before disconnecting to clean up caches
        uuids_to_invalidate = []
        with self._cache_lock:
            for uuid, conn in self._uuid_to_conn_cache.items():
                try:
                    if conn.getURI() == uri:
                        uuids_to_invalidate.append(uuid)
                except libvirt.libvirtError:
                    # If connection is already dead/invalid, we can't get URI.
                    pass

        for uuid in uuids_to_invalidate:
            self.invalidate_vm_cache(uuid)

        self.connection_manager.disconnect(uri)

    def disconnect_all(self):
        """Disconnects all active libvirt connections."""
        self.stop_monitoring()
        
        # Release domain objects to ensure connections can be closed fully
        self.invalidate_domain_cache()

        # Unregister all events
        for uri in list(self._event_callbacks.keys()):
            conn = self.connection_manager.get_connection(uri)
            if conn:
                self._unregister_domain_events(conn, uri)

        self.connection_manager.disconnect_all()
        _stop_event_loop()

    def perform_bulk_action(self, active_uris: list[str], vm_uuids: list[str], action_type: str, delete_storage_flag: bool, progress_callback: callable):
        """Performs a bulk action on a list of VMs, reporting progress via a callback."""

        action_dispatcher = {
            VmAction.START: start_vm,
            VmAction.STOP: stop_vm,
            VmAction.FORCE_OFF: force_off_vm,
            VmAction.PAUSE: pause_vm,
        }

        total_vms = len(vm_uuids)
        progress_callback("setup", total=total_vms)
        progress_callback("log", message=f"Starting bulk '{action_type}' on {total_vms} VMs...")

        successful_vms = []
        failed_vms = []

        found_domains = self.find_domains_by_uuids(active_uris, vm_uuids)

        for i, vm_uuid in enumerate(vm_uuids):
            domain = found_domains.get(vm_uuid)
            vm_name = domain.name() if domain else "Unknown VM"

            progress_callback("progress", name=vm_name, current=i + 1, total=total_vms)

            if not domain:
                msg = f"VM with UUID {vm_uuid} not found on any active server."
                progress_callback("log_error", message=msg)
                failed_vms.append(vm_uuid)
                continue

            try:
                action_func = action_dispatcher.get(action_type)
                if action_func:
                    action_func(domain)
                    msg = f"Performed '{action_type}' on VM '{vm_name}'."
                    progress_callback("log", message=msg)
                elif action_type == VmAction.DELETE:
                    # Special case for delete action's own callback
                    delete_log_callback = lambda m: progress_callback("log", message=m)
                    #time.sleep(0.5)
                    delete_vm(domain, delete_storage=delete_storage_flag, log_callback=delete_log_callback)
                else:
                    msg = f"Unknown bulk action type: {action_type}"
                    progress_callback("log_error", message=msg)
                    failed_vms.append(vm_name)
                    continue

                successful_vms.append(vm_name)

            except libvirt.libvirtError as e:
                msg = f"Error performing '{action_type}' on VM '{vm_name}': {e}"
                progress_callback("log_error", message=msg)
                failed_vms.append(vm_name)
            except Exception as e:
                msg = f"Unexpected error on '{action_type}' for VM '{vm_name}': {e}"
                progress_callback("log_error", message=msg)
                failed_vms.append(vm_name)

        # Trigger immediate refresh after bulk action
        self._force_update_event.set()

        return successful_vms, failed_vms

    def get_connection(self, uri: str) -> libvirt.virConnect | None:
        """Gets an existing connection object from the manager."""
        return self.connection_manager.get_connection(uri)

    def get_uri_for_connection(self, conn: libvirt.virConnect) -> str | None:
        """Returns the URI string associated with a given connection object."""
        return self.connection_manager.get_uri_for_connection(conn)

    def get_all_uris(self) -> list[str]:
        """Gets all URIs currently held by the connection manager."""
        return self.connection_manager.get_all_uris()

    def _recover_domain(self, internal_id: str, active_uris: list[str]) -> libvirt.virDomain | None:
        """Attempts to look up a domain directly via libvirt if cache is stale."""
        raw_uuid = internal_id.split('@')[0]
        target_uri = None
        if '@' in internal_id:
            target_uri = internal_id.split('@')[1]

        uris_to_check = [target_uri] if target_uri else active_uris

        for uri in uris_to_check:
            # Check if URI is active (in the list passed by caller, which represents current context)
            if uri not in active_uris:
                continue

            conn = self.connect(uri) # Use connect() to ensure we get a valid/new connection
            if not conn:
                continue
            try:
                domain = conn.lookupByUUIDString(raw_uuid)
                return domain
            except libvirt.libvirtError:
                pass
        return None

    def find_domains_by_uuids(self, active_uris: list[str], vm_uuids: list[str]) -> dict[str, libvirt.virDomain]:
        """Finds and returns a dictionary of domain objects from a list of UUIDs."""
        self._update_target_uris(active_uris)

        # We rely on cache primarily
        found_domains = {}
        missing_uuids = []

        with self._cache_lock:
            domain_cache_copy = self._domain_cache.copy()

        for uuid in vm_uuids:
            domain = domain_cache_copy.get(uuid)

            # Fallback: exact match failed, try to match by UUID prefix (ignore URI part)
            if not domain:
                search_uuid = uuid.split('@')[0]
                for key, d in domain_cache_copy.items():
                    key_uuid = key.split('@')[0]
                    if key_uuid == search_uuid:
                        domain = d
                        break

            valid = False
            if domain:
                try:
                    domain.info() # Check if domain is still valid
                    valid = True
                except libvirt.libvirtError:
                    valid = False

            if valid:
                found_domains[uuid] = domain
            else:
                # Attempt immediate recovery
                recovered_domain = self._recover_domain(uuid, active_uris)
                if recovered_domain:
                    found_domains[uuid] = recovered_domain
                else:
                    missing_uuids.append(uuid)

        if missing_uuids:
            self._force_update_event.set()
            pass

        return found_domains

    def find_domain_by_uuid(self, active_uris: list[str], vm_uuid: str) -> libvirt.virDomain | None:
        """Finds and returns a domain object from a UUID across active connections."""
        domains = self.find_domains_by_uuids(active_uris, [vm_uuid])
        return domains.get(vm_uuid)

    def start_vm(self, domain: libvirt.virDomain) -> None:
        """Performs pre-flight checks and starts the VM."""
        if domain.isActive():
            return # Already running, do nothing

        # Perform pre-flight checks
        check_domain_volumes_in_use(domain)

        # If checks pass, start the VM
        start_vm(domain)
        self.invalidate_vm_state_cache(self._get_internal_id(domain))
        self._force_update_event.set()

    def stop_vm(self, domain: libvirt.virDomain) -> None:
        """Stops the VM."""
        stop_vm(domain)
        self.invalidate_vm_state_cache(self._get_internal_id(domain))
        self._force_update_event.set()

    def pause_vm(self, domain: libvirt.virDomain) -> None:
        """Pauses the VM."""
        pause_vm(domain)
        self.invalidate_vm_state_cache(self._get_internal_id(domain))
        self._force_update_event.set()

    def force_off_vm(self, domain: libvirt.virDomain) -> None:
        """Forcefully stops the VM."""
        force_off_vm(domain)
        self.invalidate_vm_state_cache(self._get_internal_id(domain))
        self._force_update_event.set()

    def delete_vm(self, domain: libvirt.virDomain, delete_storage: bool) -> None:
        """Deletes the VM."""
        uuid = self._get_internal_id(domain)
        delete_vm(domain, delete_storage=delete_storage)
        self.invalidate_vm_cache(uuid)
        self._force_update_event.set()

    def resume_vm(self, domain: libvirt.virDomain) -> None:
        """Resumes the VM."""
        domain.resume()
        self.invalidate_vm_state_cache(self._get_internal_id(domain))
        self._force_update_event.set()

    def get_vm_details(self, active_uris: list[str], vm_uuid: str, domain: libvirt.virDomain = None, conn: libvirt.virConnect = None, cached_ips: list = None) -> tuple | None:
        """Finds a VM by UUID and returns its detailed information."""
        if not domain:
            domain = self.find_domain_by_uuid(active_uris, vm_uuid)

        if not domain:
            return None

        with self._cache_lock:
            conn_for_domain = conn or self._uuid_to_conn_cache.get(vm_uuid)

            # Check for cached details
            vm_cache = self._vm_data_cache.get(vm_uuid)
            if vm_cache:
                details = vm_cache.get('vm_details')
                details_ts = vm_cache.get('vm_details_ts', 0)
                now = time.time()

                # Use XML TTL for details cache as well
                if details and (now - details_ts < self._xml_cache_ttl) and conn_for_domain:
                    try:
                        # Update dynamic fields
                        details['status'] = get_status(domain)
                        # Update IPs if provided and fresh
                        if cached_ips is not None:
                            details['detail_network'] = cached_ips
                        logging.info(f"Cache HIT for VM details: {vm_uuid}")
                        return (details, domain, conn_for_domain)
                    except libvirt.libvirtError:
                        pass # If we can't get status, maybe domain is gone or invalid, drop through to refresh

        # Fallback if not in cache (could happen if cache just cleared)
        if not conn_for_domain:
            if conn:
                conn_for_domain = conn
            else:
                raw_uuid = vm_uuid.split('@')[0] if '@' in vm_uuid else vm_uuid
                for uri in active_uris:
                    c = self.connect(uri)
                    if not c: continue
                    try:
                        if c.lookupByUUIDString(raw_uuid).UUID() == domain.UUID():
                            conn_for_domain = c
                            break
                    except libvirt.libvirtError:
                        continue

        if not conn_for_domain:
            return None

        try:
            info, xml_content = self._get_domain_info_and_xml(domain, internal_id=vm_uuid)
            if info is None or xml_content is None:
                return None

            root = None
            try:
                root = ET.fromstring(xml_content)
            except ET.ParseError:
                pass

            # Use cached IPs if provided, otherwise fetch them
            detail_network_info = cached_ips if cached_ips is not None else get_vm_network_ip(domain)

            vm_info = {
                'name': domain.name(),
                'uuid': domain.UUIDString(),
                'status': get_status(domain),
                'description': get_vm_description(domain),
                'cpu': info[3],
                'cpu_model': get_vm_cpu_model(root),
                'memory': info[1] // 1024,
                'machine_type': get_vm_machine_info(root),
                'firmware': get_vm_firmware_info(root),
                'shared_memory': get_vm_shared_memory_info(root),
                'networks': get_vm_networks_info(root),
                'detail_network': detail_network_info,
                'network_dns_gateway': get_vm_network_dns_gateway_info(domain, root=root),
                'disks': get_vm_disks_info(conn_for_domain, root),
                'devices': get_vm_devices_info(root),
                'boot': get_boot_info(conn_for_domain, root),
                'video_model': get_vm_video_model(root),
                'xml': xml_content,
            }

            # Cache the constructed details
            with self._cache_lock:
                self._vm_data_cache.setdefault(vm_uuid, {})
                self._vm_data_cache[vm_uuid]['vm_details'] = vm_info
                self._vm_data_cache[vm_uuid]['vm_details_ts'] = time.time()
                logging.info(f"Cache WRITE for VM details: {vm_uuid}")

            return (vm_info, domain, conn_for_domain)
        except libvirt.libvirtError:
            raise

    def get_vms(
            self,
            active_uris: list[str],
            servers: list[dict],
            sort_by: str,
            search_text: str,
            selected_vm_uuids: set[str],
            force: bool = False,
            page_start: int = None,
            page_end: int = None
            ) -> tuple:
        """Fetch, filter, and return VM data without creating UI components."""
        # Never preload everything
        self._update_domain_cache(active_uris, force=force, preload=False)

        with self._cache_lock:
            domains_map = self._domain_cache.copy()
            conn_map = self._uuid_to_conn_cache.copy()

        domains_with_conn = []
        for uuid, domain in domains_map.items():
            conn = conn_map.get(uuid)
            if conn:
                domains_with_conn.append((domain, conn))

        total_vms = len(domains_with_conn)

        # Map URIs to Server Names (using connection manager which is thread safe)
        server_names = []
        # Count VMs per URI from the domain cache
        uri_counts = {}
        for internal_id in domains_map.keys():
            if "@" in internal_id:
                uri = internal_id.split("@", 1)[1]
                uri_counts[uri] = uri_counts.get(uri, 0) + 1

        from utils import extract_server_name_from_uri

        # We can just iterate active_uris provided
        for uri in active_uris:
            count = uri_counts.get(uri, 0)
            # Check if we have connection or if it is active
            found = False
            name = None
            for server in servers:
                if server['uri'] == uri:
                    name = server['name']
                    found = True
                    break

            if not found:
                name = extract_server_name_from_uri(uri)

            server_names.append(f"{name} ({count})")

        total_vms_unfiltered = len(domains_with_conn)
        #domains_to_display = domains_with_conn
        domains_to_display = sorted(domains_with_conn, key=lambda x: natural_sort_key(x[0].name()))


        if sort_by != VmStatus.DEFAULT:
            if sort_by == VmStatus.SELECTED:
                domains_to_display = [(d, c) for d, c in domains_to_display if d.UUIDString() in selected_vm_uuids]
            else:
                def status_match(d):
                    # Use cached state or fetch with lighter state() call
                    info = self.get_cached_vm_info(d)
                    if info:
                        state = info[0]
                    else:
                        # Use state() instead of info()
                        state_tuple = self._get_domain_state(d)
                        if state_tuple:
                            state = state_tuple[0]
                        else:
                            try:
                                state, _ = d.state()
                            except libvirt.libvirtError:
                                return False

                    if sort_by == VmStatus.RUNNING:
                        return state == libvirt.VIR_DOMAIN_RUNNING
                    if sort_by == VmStatus.PAUSED:
                        return state == libvirt.VIR_DOMAIN_PAUSED
                    if sort_by == VmStatus.STOPPED:
                        return state not in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]
                    return True

                domains_to_display = [(d, c) for d, c in domains_to_display if status_match(d)]

        if search_text:
            search_lower = search_text.lower()
            # Optimization: Use cached name from identity to avoid libvirt call in loop
            filtered_domains = []
            for d, c in domains_to_display:
                conn_uri = self.connection_manager.get_uri_for_connection(c)
                _, vm_name = self.get_vm_identity(d, c, known_uri=conn_uri)
                if search_lower in vm_name.lower():
                    filtered_domains.append((d, c))
            domains_to_display = filtered_domains

        total_filtered_vms = len(domains_to_display)
        if page_start is not None and page_end is not None and force:
            paginated_domains = domains_to_display[page_start:page_end]
            logging.info(f"Optimized cache refresh: updating only {len(paginated_domains)} VMs ({page_start}-{page_end})")

            for domain, _ in paginated_domains:
                try:
                    self._get_domain_info(domain)
                except libvirt.libvirtError as e:
                    logging.debug(f"Error refreshing cache for VM {domain.name()}: {e}")

        return domains_to_display, total_vms, total_filtered_vms, server_names, list(domains_map.keys())
