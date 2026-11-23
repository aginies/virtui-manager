"""FastAPI application exposing a read-only web API over the VM fleet.

Design notes:
- Every libvirt call is blocking, so all service-layer access goes through
  ``run_in_threadpool`` and never blocks the event loop.
- The API reuses ``VMService`` (and its caches) so statistics match the TUI
  sparklines exactly (same CPU-time / IO-stats deltas).
- Authentication is a single bearer token (generated at startup by
  ``main.py``); the WebSocket accepts the same token as a query parameter.
- P1 is deliberately read-only: servers, VMs and live statistics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

import libvirt
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ..config import load_config, save_config
from ..constants import VmAction
from ..guest_agent import (
    GuestAgentError,
    agent_status,
    ga_download_file,
    ga_exec_wait,
    ga_fsfreeze_freeze,
    ga_fsfreeze_status,
    ga_fsfreeze_thaw,
    ga_fsinfo,
    ga_fstrim,
    ga_info,
    ga_set_time,
    ga_set_user_password,
    ga_shutdown,
    ga_ssh_add_authorized_keys,
    ga_ssh_get_authorized_keys,
    ga_ssh_remove_authorized_keys,
    ga_supported,
    ga_suspend,
    ga_upload_file,
)
from ..libvirt_utils import (
    get_active_vm_allocation,
    get_host_resources,
    get_internal_id,
)
from ..modals.input_modals import _sanitize_domain_name
from ..modals.server_modals import _is_valid_server_uri
from ..network_manager import (
    delete_network,
    get_vms_using_network,
    list_networks,
    set_network_active,
    set_network_autostart,
)
from ..storage_manager import (
    _safe_get_volume_path,
    delete_storage_pool,
    delete_volume,
    find_vms_using_volume,
    list_storage_pools,
    list_storage_volumes,
    set_pool_active,
    set_pool_autostart,
)
from ..utils import extract_server_name_from_uri, natural_sort_key, sanitize_sensitive_data
from ..vm_actions import (
    create_vm_snapshot,
    delete_vm_snapshot,
    rename_vm,
    restore_vm_snapshot,
)
from ..vm_queries import (
    get_boot_info,
    get_status,
    get_vm_cpu_model,
    get_vm_description,
    get_vm_devices_info,
    get_vm_disks_info,
    get_vm_firmware_info,
    get_vm_machine_info,
    get_vm_network_dns_gateway_info,
    get_vm_network_ip,
    get_vm_networks_info,
    get_vm_shared_memory_info,
    get_vm_snapshots,
    get_vm_video_model,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_STATS_INTERVAL = 5
WS_INVALID_TOKEN_CODE = 4401
MAX_GUEST_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_GUEST_EXEC_TIMEOUT = 300

logger = logging.getLogger(__name__)


class ActionRequest(BaseModel):
    """Body of POST /api/vms/action."""

    id: str  # internal id, UUID@URI
    action: str


class RenameRequest(BaseModel):
    """Body of POST /api/vms/rename."""

    id: str
    new_name: str


class DeleteRequest(BaseModel):
    """Body of POST /api/vms/delete."""

    id: str
    delete_storage: bool = False
    delete_nvram: bool = False


class SnapshotCreateRequest(BaseModel):
    """Body of POST /api/vms/snapshots."""

    id: str
    name: str
    description: str = ""
    quiesce: bool = False


class SnapshotRefRequest(BaseModel):
    """Body of POST /api/vms/snapshots/{restore,delete}."""

    id: str
    name: str


class ConsoleRequest(BaseModel):
    """Body of POST /api/vms/console{,/stop}."""

    id: str


class GuestActionRequest(BaseModel):
    """Body of POST /api/vms/guest/action."""

    id: str
    action: str


class GuestPasswordRequest(BaseModel):
    """Body of POST /api/vms/guest/password."""

    id: str
    username: str
    password: str
    crypted: bool = False


class GuestExecRequest(BaseModel):
    """Body of POST /api/vms/guest/exec."""

    id: str
    command: str
    timeout: int = 30


class GuestSshKeysRequest(BaseModel):
    """Body of POST /api/vms/guest/ssh_keys{,/delete}."""

    id: str
    user: str
    keys: list = []


class BulkRequest(BaseModel):
    """Body of POST /api/vms/bulk."""

    ids: list  # internal ids, UUID@URI
    action: str


class EventBus:
    """Thread-safe fan-out of libvirt lifecycle events to WS subscribers.

    VMService callbacks fire on the libvirt event-loop thread; subscribers
    are asyncio queues owned by the uvicorn loop. Publishing therefore hops
    threads with call_soon_threadsafe. Queues are bounded: on overflow the
    oldest event is dropped (state is re-fetched by the client anyway).
    """

    QUEUE_SIZE = 256

    def __init__(self):
        self._subscribers: set = set()
        self._loop = None

    def bind_loop(self, loop) -> None:
        self._loop = loop

    def subscribe(self):
        queue = asyncio.Queue(maxsize=self.QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue) -> None:
        self._subscribers.discard(queue)

    def publish_threadsafe(self, event: dict) -> None:
        loop = self._loop
        if loop is None or not self._subscribers:
            return
        for queue in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(self._enqueue, queue, event)
            except RuntimeError:
                pass  # loop already closed

    @staticmethod
    def _enqueue(queue, event: dict) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except Exception:  # pylint: disable=broad-except
                pass


class StatsSubscription:
    """One /ws/stats client: its delivery queue and server selection."""

    __slots__ = ("queue", "uris")

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        self.uris = None  # None = all servers; set() = nothing selected


class StatsCollector:
    """Single background stats loop shared by all /ws/stats clients.

    Previously each client ran its own collection loop, so N open tabs
    meant N times the libvirt/SSH load. The collector runs once per
    interval, gathers the union of the subscribers' server selections and
    fans out filtered payloads (bounded queues, drop-oldest on overflow).
    It starts lazily with the first subscriber and stops after the last
    one leaves, so an idle dashboard costs nothing.
    """

    def __init__(self, backend: "Backend", interval: int):
        self.backend = backend
        self.interval = max(1, int(interval))
        self._subs: set = set()
        self._task = None

    def subscribe(self) -> StatsSubscription:
        sub = StatsSubscription()
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: StatsSubscription) -> None:
        self._subs.discard(sub)

    def ensure_running(self) -> None:
        """(Re)start the loop; must be called from the event loop thread."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    @staticmethod
    def _deliver(sub: StatsSubscription, payload: str) -> None:
        try:
            sub.queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                sub.queue.get_nowait()
                sub.queue.put_nowait(payload)
            except Exception:  # pylint: disable=broad-except
                pass

    async def _run(self) -> None:
        try:
            while self._subs:
                want_all = False
                union: set = set()
                for sub in list(self._subs):
                    if sub.uris is None:
                        want_all = True
                        break
                    union |= sub.uris

                stats = {}
                if want_all or union:
                    stats = await run_in_threadpool(
                        self.backend.stats_snapshot, None if want_all else sorted(union)
                    )

                ts = time.time()
                for sub in list(self._subs):
                    if sub.uris is None:
                        payload = stats
                    else:
                        payload = {
                            key: value
                            for key, value in stats.items()
                            if key.partition("@")[2] in sub.uris
                        }
                    self._deliver(sub, json.dumps({"ts": ts, "stats": payload}))
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            raise
        except Exception:  # pylint: disable=broad-except
            logger.error("Stats collector crashed", exc_info=True)


class ServerConnectRequest(BaseModel):
    """Body of POST /api/servers/{connect,disconnect}."""

    uri: str


class ServerAddRequest(BaseModel):
    """Body of POST /api/servers."""

    name: str
    uri: str
    autoconnect: bool = False


class ServerEditRequest(BaseModel):
    """Body of PUT /api/servers."""

    original_uri: str
    name: str
    uri: str
    autoconnect: bool = False


class NetworkRequest(BaseModel):
    """Body of network preference mutations."""

    uri: str
    name: str


class NetworkActiveRequest(NetworkRequest):
    active: bool


class NetworkAutostartRequest(NetworkRequest):
    autostart: bool


class PoolRequest(BaseModel):
    """Body of storage preference mutations."""

    uri: str
    pool: str


class PoolActiveRequest(PoolRequest):
    active: bool


class PoolAutostartRequest(PoolRequest):
    autostart: bool


class VolumeRequest(PoolRequest):
    volume: str


class _WebConsoleAppStub:
    """Minimal stand-in for the Textual app expected by WebConsoleManager.

    The manager was written for the TUI: it talks to ``app`` for dialogs,
    messages and worker scheduling. For the web API everything runs inline
    (already inside a worker thread) and messages are captured instead of
    displayed. Sessions live in the same file as the TUI's, so consoles
    are shared between both frontends.
    """

    def __init__(self, config: dict):
        self.WC_PORT_RANGE_START = config.get("WC_PORT_RANGE_START", 40000)
        self.WC_PORT_RANGE_END = config.get("WC_PORT_RANGE_END", 40050)
        self.errors: list[str] = []
        self.messages: list[str] = []
        # WebConsoleManager schedules via app.worker_manager.run(fn, name=...);
        # expose the same signature on self and point worker_manager at it.
        self.worker_manager = self

    def run(self, fn, name=None):  # pylint: disable=unused-argument
        """Execute the worker inline: we are already in a worker thread."""
        fn()

    def call_from_thread(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def push_screen(self, dialog, callback=None):  # pylint: disable=unused-argument
        # The TUI shows the console URL in a dialog; the web API reads the
        # saved session instead, so just record it.
        url = getattr(dialog, "url", None)
        if url:
            self.messages.append(url)

    def show_error_message(self, message):
        self.errors.append(str(message))

    def show_success_message(self, message):
        self.messages.append(str(message))

    def post_message(self, message):  # pylint: disable=unused-argument
        pass


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that forces browser revalidation on every request.

    Without a Cache-Control header browsers apply heuristic caching and
    can keep running stale JS/CSS after an upgrade (etag alone is not
    revalidated). "no-cache" keeps the cheap 304 path but guarantees the
    client always sees the current assets.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _first_ipv4(domain: libvirt.virDomain) -> Optional[str]:
    """Return the first IPv4 address of a domain (lease-based), or None."""
    try:
        state, _ = domain.state()
        if state not in (libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED):
            return None
        for iface in get_vm_network_ip(domain):
            for addr in iface.get("ipv4", []):
                return addr.split("/", 1)[0]
    except libvirt.libvirtError as exc:
        logger.debug("IP lookup failed: %s", sanitize_sensitive_data(str(exc)))
    except Exception:  # pylint: disable=broad-except
        logger.debug("Unexpected error during IP lookup", exc_info=True)
    return None


class Backend:
    """Glue between the HTTP layer and the existing service layer.

    All methods are blocking (libvirt) and must be called from a worker
    thread, never from the event loop directly.
    """

    # Lease IPs: positive results change only across reboots (lifecycle
    # events invalidate immediately), so cache them long; None results are
    # retried soon because DHCP may still be pending after a VM boots.
    IP_CACHE_TTL = 300
    IP_CACHE_MISS_TTL = 30
    # domain.info() is 1 RPC per VM (the dominant inventory cost). Freshness
    # is guaranteed by lifecycle-event invalidation; the TTL is only a safety
    # net, and must exceed the frontend's 30s refresh cycle so periodic
    # refreshes stay warm.
    INFO_CACHE_TTL = 120
    NAME_MAP_TTL = 5       # seconds; URI->name read from config on every snapshot
    HOST_RES_TTL = 60      # seconds; host hardware info is effectively static

    def __init__(self, config: Optional[dict] = None, vm_service=None):
        self.config = config if config is not None else load_config()
        if vm_service is not None:
            self.vm_service = vm_service
        else:
            from ..vm_service import VMService  # heavy import, keep lazy

            self.vm_service = VMService()
        self.connection_manager = self.vm_service.connection_manager
        self.event_bus = EventBus()
        # Route libvirt lifecycle events to WebSocket subscribers. The
        # callbacks fire on the libvirt event thread; registration happens
        # for connections made through VMService.connect().
        try:
            self.vm_service.set_vm_update_callback(self._on_vm_update)
            self.vm_service.set_data_update_callback(self._on_data_update)
            self.vm_service.set_message_callback(self._on_message)
        except Exception:  # pylint: disable=broad-except
            logger.debug("Could not register VMService event callbacks", exc_info=True)
        self._config_lock = threading.Lock()
        self._ip_cache: dict = {}        # internal_id -> (ts, ipv4|None)
        self._info_cache: dict = {}      # internal_id -> (ts, domain.info() tuple)
        self._name_map_cache = None      # (ts, {uri: name})
        self._host_res_cache: dict = {}  # uri -> (ts, host_resources dict)
        self._webconsole_manager = None
        self._webconsole_stub = None
        self._console_lock = threading.Lock()

    # -- connections -------------------------------------------------------

    def _on_vm_update(self, internal_id: str) -> None:
        # State changed: cached info/IP for this VM may be stale.
        self._info_cache.pop(internal_id, None)
        self._ip_cache.pop(internal_id, None)
        self.event_bus.publish_threadsafe({"type": "vm", "id": internal_id, "ts": time.time()})

    def _on_data_update(self) -> None:
        # VM defined/undefined: the whole inventory may have shifted.
        self._info_cache.clear()
        self.event_bus.publish_threadsafe({"type": "inventory", "ts": time.time()})

    def _on_message(self, level, message) -> None:
        # VMService messages carry rich markup ([b]...[/b]); strip it.
        text = str(message).replace("[b]", "").replace("[/b]", "")
        self.event_bus.publish_threadsafe(
            {"type": "message", "level": str(level), "text": text, "ts": time.time()}
        )

    @staticmethod
    def _configured_servers() -> list[dict]:
        """Server entries from the on-disk config (picks up TUI/CLI edits)."""
        return [s for s in load_config().get("servers", []) if s.get("uri")]

    def _server_name_map(self) -> dict:
        """URI -> display name; briefly cached (read on every snapshot)."""
        now = time.time()
        hit = self._name_map_cache
        if hit is not None and now - hit[0] < self.NAME_MAP_TTL:
            return hit[1]
        name_map = {s["uri"]: s.get("name") for s in self._configured_servers()}
        self._name_map_cache = (now, name_map)
        return name_map

    def connect_configured_servers(self) -> None:
        """Connect to every server listed in the configuration.

        Uses VMService.connect (not ConnectionManager.connect) so libvirt
        domain lifecycle events get registered for each connection.
        """
        for uri in self._server_name_map():
            if self.connection_manager.get_connection(uri) is None:
                try:
                    self.vm_service.connect(uri)
                except Exception:  # pylint: disable=broad-except
                    logger.error(
                        "Failed to connect to %s", sanitize_sensitive_data(uri), exc_info=True
                    )

    def _server_entry(self, server: dict) -> dict:
        """Build the API representation of one configured server."""
        uri = server["uri"]
        entry = {
            "name": server.get("name") or extract_server_name_from_uri(uri),
            "uri": uri,
            "autoconnect": bool(server.get("autoconnect", False)),
            "connected": False,
            "vm_count": 0,
            "error": self.connection_manager.get_connection_error(uri),
        }
        conn = self.connection_manager.get_connection(uri)
        if conn is not None:
            try:
                entry["vm_count"] = len(conn.listAllDomains(0))
                entry["connected"] = True
                entry["error"] = None
            except libvirt.libvirtError as exc:
                entry["error"] = sanitize_sensitive_data(str(exc))
        return entry

    def servers_snapshot(self) -> list[dict]:
        """Return the configured servers with their connection state."""
        return [self._server_entry(server) for server in self._configured_servers()]

    # -- server configuration ------------------------------------------------

    def connect_server(self, uri: str) -> dict:
        server = next((s for s in self._configured_servers() if s["uri"] == uri), None)
        if server is None:
            raise LookupError(f"No such server: {uri}")
        logger.info("Web connect to %s", sanitize_sensitive_data(uri))
        conn = self.vm_service.connect(uri, force_retry=True)
        entry = self._server_entry(server)
        if conn is None and not entry["connected"]:
            error = self.connection_manager.get_connection_error(uri) or "Connection failed"
            raise ValueError(sanitize_sensitive_data(error))
        return entry

    def disconnect_server(self, uri: str) -> dict:
        server = next((s for s in self._configured_servers() if s["uri"] == uri), None)
        if server is None:
            raise LookupError(f"No such server: {uri}")
        logger.info("Web disconnect from %s", sanitize_sensitive_data(uri))
        self.connection_manager.disconnect(uri)
        self._host_res_cache.pop(uri, None)
        return self._server_entry(server)

    @staticmethod
    def _validate_server_fields(name: str, uri: str) -> tuple[str, str]:
        name = (name or "").strip()
        uri = (uri or "").strip()
        if not name:
            raise ValueError("Server name is required")
        if not _is_valid_server_uri(uri):
            raise ValueError("Invalid URI (must start with qemu:/// or qemu+ssh://)")
        return name, uri

    def add_server(self, name: str, uri: str, autoconnect: bool = False) -> dict:
        name, uri = self._validate_server_fields(name, uri)
        with self._config_lock:
            config = load_config()
            servers = config.get("servers", [])
            if any(s.get("uri") == uri for s in servers):
                raise ConnectionError(f"Server already configured: {uri}")
            entry = {"name": name, "uri": uri, "autoconnect": bool(autoconnect)}
            servers.append(entry)
            config["servers"] = servers
            save_config(config)
        self._name_map_cache = None
        logger.info("Web added server %s", sanitize_sensitive_data(uri))
        return self._server_entry(entry)

    def edit_server(
        self, original_uri: str, name: str, uri: str, autoconnect: bool = False
    ) -> dict:
        name, uri = self._validate_server_fields(name, uri)
        with self._config_lock:
            config = load_config()
            servers = config.get("servers", [])
            index = next(
                (i for i, s in enumerate(servers) if s.get("uri") == original_uri), None
            )
            if index is None:
                raise LookupError(f"No such server: {original_uri}")
            if uri != original_uri and any(s.get("uri") == uri for s in servers):
                raise ConnectionError(f"Server already configured: {uri}")
            entry = {"name": name, "uri": uri, "autoconnect": bool(autoconnect)}
            servers[index] = entry
            config["servers"] = servers
            save_config(config)
        self._name_map_cache = None
        if uri != original_uri:
            # The old connection (if any) points at the previous URI.
            self.connection_manager.disconnect(original_uri)
        logger.info("Web edited server %s", sanitize_sensitive_data(uri))
        return self._server_entry(entry)

    def delete_server(self, uri: str) -> dict:
        with self._config_lock:
            config = load_config()
            servers = config.get("servers", [])
            remaining = [s for s in servers if s.get("uri") != uri]
            if len(remaining) == len(servers):
                raise LookupError(f"No such server: {uri}")
            config["servers"] = remaining
            save_config(config)
        self._name_map_cache = None
        self.connection_manager.disconnect(uri)
        self._host_res_cache.pop(uri, None)
        logger.info("Web deleted server %s", sanitize_sensitive_data(uri))
        return {"deleted": uri}

    # -- host statistics -------------------------------------------------------

    def host_stats(self, uri: str) -> dict:
        """Host-level CPU/RAM view, same numbers as the TUI host stats bar
        (allocated vCPUs/RAM of active VMs vs host capacity)."""
        conn = self._get_conn(uri)
        now = time.time()
        hit = self._host_res_cache.get(uri)
        if hit is not None and now - hit[0] < self.HOST_RES_TTL:
            host_res = hit[1]
        else:
            host_res = get_host_resources(conn)
            self._host_res_cache[uri] = (now, host_res)
        alloc = get_active_vm_allocation(conn)
        total_cpus = int(host_res.get("total_cpus") or 0)
        total_mem_mb = int(host_res.get("available_memory") or 0)
        used_cpus = int(alloc.get("active_allocated_vcpus") or 0)
        used_mem_mb = int(alloc.get("active_allocated_memory") or 0)
        return {
            "uri": uri,
            "server": self._server_name_map().get(uri) or extract_server_name_from_uri(uri),
            "model": host_res.get("model"),
            "mhz": host_res.get("mhz"),
            "sockets": host_res.get("sockets"),
            "cores": host_res.get("cores"),
            "threads": host_res.get("threads"),
            "total_cpus": total_cpus,
            "used_cpus": used_cpus,
            "cpu_percent": round(used_cpus / total_cpus * 100, 1) if total_cpus else 0.0,
            "total_memory_mb": total_mem_mb,
            "used_memory_mb": used_mem_mb,
            "free_memory_mb": int(host_res.get("free_memory") or 0),
            "mem_percent": round(used_mem_mb / total_mem_mb * 100, 1) if total_mem_mb else 0.0,
        }

    # -- server preferences: networks ------------------------------------------

    # libvirt volume type constants -> readable names
    VOLUME_TYPES = {0: "file", 1: "block", 2: "dir", 3: "network", 4: "netdir", 5: "ploop"}

    def _get_conn(self, uri: str):
        conn = self.connection_manager.get_connection(uri)
        if conn is None:
            raise ConnectionError(f"Server not connected: {uri}")
        return conn

    @staticmethod
    def _clear_pref_caches() -> None:
        """Drop lru_caches touched by the prefs views (TUI does the same)."""
        list_storage_pools.cache_clear()
        list_storage_volumes.cache_clear()
        get_vms_using_network.cache_clear()
        find_vms_using_volume.cache_clear()

    def networks_list(self, uri: str) -> list[dict]:
        conn = self._get_conn(uri)
        return list_networks(conn)

    def network_set_active(self, uri: str, name: str, active: bool) -> dict:
        conn = self._get_conn(uri)
        logger.info("Web network '%s' active=%s on %s", name, active, sanitize_sensitive_data(uri))
        set_network_active(conn, name, active)
        self._clear_pref_caches()
        return {"name": name, "active": active}

    def network_set_autostart(self, uri: str, name: str, autostart: bool) -> dict:
        conn = self._get_conn(uri)
        logger.info(
            "Web network '%s' autostart=%s on %s", name, autostart, sanitize_sensitive_data(uri)
        )
        set_network_autostart(conn, name, autostart)
        self._clear_pref_caches()
        return {"name": name, "autostart": autostart}

    def network_delete(self, uri: str, name: str) -> dict:
        conn = self._get_conn(uri)
        users = get_vms_using_network(conn, name)
        if users:
            raise ConnectionError(f"Network '{name}' is used by: {', '.join(users)}")
        logger.info("Web delete network '%s' on %s", name, sanitize_sensitive_data(uri))
        delete_network(conn, name)
        self._clear_pref_caches()
        return {"deleted": name}

    def network_xml(self, uri: str, name: str) -> dict:
        conn = self._get_conn(uri)
        try:
            net = conn.networkLookupByName(name)
            return {"name": name, "xml": net.XMLDesc(0)}
        except libvirt.libvirtError as exc:
            raise LookupError(sanitize_sensitive_data(str(exc))) from exc

    # -- server preferences: storage -------------------------------------------

    def storage_snapshot(self, uri: str) -> list[dict]:
        """Pools with nested volumes (live libvirt objects stripped)."""
        conn = self._get_conn(uri)
        pools = []
        for pool_info in list_storage_pools(conn):
            pool_obj = pool_info.get("pool")
            entry = {
                "name": pool_info.get("name"),
                "status": pool_info.get("status"),
                "autostart": pool_info.get("autostart", False),
                "capacity": pool_info.get("capacity", 0),
                "allocation": pool_info.get("allocation", 0),
                "error": pool_info.get("error"),
                "volumes": [],
            }
            if pool_obj is not None and entry["status"] == "active":
                for vol_info in list_storage_volumes(pool_obj):
                    vol_obj = vol_info.get("volume")
                    entry["volumes"].append(
                        {
                            "name": vol_info.get("name"),
                            "type": self.VOLUME_TYPES.get(vol_info.get("type"), "unknown"),
                            "capacity": vol_info.get("capacity", 0),
                            "allocation": vol_info.get("allocation", 0),
                            "path": _safe_get_volume_path(vol_obj) if vol_obj else "",
                        }
                    )
            pools.append(entry)
        return pools

    def _lookup_pool(self, uri: str, pool_name: str):
        conn = self._get_conn(uri)
        try:
            return conn, conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError as exc:
            raise LookupError(sanitize_sensitive_data(str(exc))) from exc

    def pool_set_active(self, uri: str, pool_name: str, active: bool) -> dict:
        _conn, pool = self._lookup_pool(uri, pool_name)
        logger.info(
            "Web pool '%s' active=%s on %s", pool_name, active, sanitize_sensitive_data(uri)
        )
        set_pool_active(pool, active)
        self._clear_pref_caches()
        return {"pool": pool_name, "active": active}

    def pool_set_autostart(self, uri: str, pool_name: str, autostart: bool) -> dict:
        _conn, pool = self._lookup_pool(uri, pool_name)
        logger.info(
            "Web pool '%s' autostart=%s on %s", pool_name, autostart, sanitize_sensitive_data(uri)
        )
        set_pool_autostart(pool, autostart)
        self._clear_pref_caches()
        return {"pool": pool_name, "autostart": autostart}

    def pool_delete(self, uri: str, pool_name: str) -> dict:
        _conn, pool = self._lookup_pool(uri, pool_name)
        logger.info("Web delete pool '%s' on %s", pool_name, sanitize_sensitive_data(uri))
        delete_storage_pool(pool)
        self._clear_pref_caches()
        return {"deleted": pool_name}

    def pool_xml(self, uri: str, pool_name: str) -> dict:
        _conn, pool = self._lookup_pool(uri, pool_name)
        try:
            return {"name": pool_name, "xml": pool.XMLDesc(0)}
        except libvirt.libvirtError as exc:
            raise LookupError(sanitize_sensitive_data(str(exc))) from exc

    def _lookup_volume(self, uri: str, pool_name: str, volume_name: str):
        conn, pool = self._lookup_pool(uri, pool_name)
        try:
            vol = pool.storageVolLookupByName(volume_name)
            return conn, vol
        except libvirt.libvirtError as exc:
            raise LookupError(sanitize_sensitive_data(str(exc))) from exc

    def volume_delete(self, uri: str, pool_name: str, volume_name: str) -> dict:
        conn, vol = self._lookup_volume(uri, pool_name, volume_name)
        path = _safe_get_volume_path(vol)
        users = [d.name() for d in find_vms_using_volume(conn, path, volume_name)]
        if users:
            raise ConnectionError(f"Volume '{volume_name}' is used by: {', '.join(users)}")
        logger.info(
            "Web delete volume '%s' (pool %s) on %s",
            volume_name,
            pool_name,
            sanitize_sensitive_data(uri),
        )
        delete_volume(vol)
        self._clear_pref_caches()
        return {"deleted": volume_name}

    def volume_xml(self, uri: str, pool_name: str, volume_name: str) -> dict:
        _conn, vol = self._lookup_volume(uri, pool_name, volume_name)
        try:
            return {"name": volume_name, "xml": vol.XMLDesc(0)}
        except libvirt.libvirtError as exc:
            raise LookupError(sanitize_sensitive_data(str(exc))) from exc

    # -- VM inventory ------------------------------------------------------

    def _iter_domains(self, uris=None):
        """Yield (internal_id, uri, server_name, conn, domain) for every VM.

        Args:
            uris: optional collection of server URIs to restrict iteration
                to (None = every connected server).
        """
        wanted = set(uris) if uris else None
        name_map = self._server_name_map()
        for uri in self.connection_manager.get_all_uris():
            if wanted is not None and uri not in wanted:
                continue
            conn = self.connection_manager.get_connection(uri)
            if conn is None:
                continue
            try:
                domains = conn.listAllDomains(0)
            except libvirt.libvirtError as exc:
                logger.error(
                    "Cannot list domains on %s: %s",
                    sanitize_sensitive_data(uri),
                    sanitize_sensitive_data(str(exc)),
                )
                continue
            for domain in domains:
                try:
                    # Build the id from the connection-dict key, NOT
                    # conn.getURI(): libvirt may report a normalized URI
                    # (e.g. "?no_tty=1") that would not resolve back
                    # through get_connection(). Same reason VMService
                    # passes known_uri to get_vm_identity().
                    yield f"{domain.UUIDString()}@{uri}", uri, name_map.get(
                        uri
                    ) or extract_server_name_from_uri(uri), conn, domain
                except libvirt.libvirtError:
                    continue

    def _cached_domain_info(self, internal_id: str, domain: libvirt.virDomain) -> tuple:
        """domain.info() with a short TTL cache: one SSH round-trip per VM is
        the dominant cost of the inventory fetch. Lifecycle events drop the
        entry immediately, so state changes still appear within ~1s."""
        now = time.time()
        hit = self._info_cache.get(internal_id)
        if hit is not None and now - hit[0] < self.INFO_CACHE_TTL:
            return hit[1]
        info = tuple(domain.info())
        self._info_cache[internal_id] = (now, info)
        return info

    def _cached_first_ipv4(self, internal_id: str, domain: libvirt.virDomain, state=None):
        """Lease lookup with a short TTL cache (slow over SSH; lifecycle
        events invalidate the entry as soon as a VM changes state)."""
        # Not running -> no lease possible; skip the state() RPC when the
        # caller already knows the state (mirrors _first_ipv4's early exit).
        if state is not None and state not in (
            libvirt.VIR_DOMAIN_RUNNING,
            libvirt.VIR_DOMAIN_PAUSED,
        ):
            return None
        now = time.time()
        hit = self._ip_cache.get(internal_id)
        if hit is not None:
            ttl = self.IP_CACHE_MISS_TTL if hit[1] is None else self.IP_CACHE_TTL
            if now - hit[0] < ttl:
                return hit[1]
        ipv4 = _first_ipv4(domain)
        self._ip_cache[internal_id] = (now, ipv4)
        return ipv4

    def vms_snapshot(self) -> list[dict]:
        """Return the full VM inventory across all connected servers."""
        vms = []
        for internal_id, uri, server_name, conn, domain in self._iter_domains():
            try:
                info = self._cached_domain_info(internal_id, domain)
                vms.append(
                    {
                        "id": internal_id,
                        "uuid": domain.UUIDString(),
                        "name": domain.name(),
                        "status": get_status(domain, state=info[0]),
                        "server": server_name,
                        "uri": uri,
                        "vcpus": info[3],
                        "memory_mb": int(info[2]) // 1024,
                        "ipv4": self._cached_first_ipv4(internal_id, domain, state=info[0]),
                    }
                )
            except libvirt.libvirtError as exc:
                logger.debug(
                    "Skipping unreadable domain: %s", sanitize_sensitive_data(str(exc))
                )
        vms.sort(key=lambda vm: natural_sort_key(vm["name"]))
        # Drop cache entries for VMs that are gone.
        live_ids = {vm["id"] for vm in vms}
        for stale in [key for key in self._ip_cache if key not in live_ids]:
            self._ip_cache.pop(stale, None)
        for stale in [key for key in self._info_cache if key not in live_ids]:
            self._info_cache.pop(stale, None)
        return vms

    def _lookup_domain(self, internal_id: str):
        """Resolve an internal id (UUID@URI) into (uri, conn, domain)."""
        uuid, sep, uri = internal_id.partition("@")
        if not sep or not uuid or not uri:
            raise ValueError("Malformed VM id")
        conn = self.connection_manager.get_connection(uri)
        if conn is None:
            raise ConnectionError(f"Server not connected: {uri}")
        try:
            domain = conn.lookupByUUIDString(uuid)
        except libvirt.libvirtError as exc:
            raise LookupError(sanitize_sensitive_data(str(exc))) from exc
        return uri, conn, domain

    # -- actions -------------------------------------------------------------

    # Same actions the TUI quick-action bar exposes for power management;
    # they map onto VMService methods which handle cache invalidation.
    ACTION_METHODS = {
        "start": "start_vm",
        "shutdown": "stop_vm",       # graceful shutdown
        "force_off": "force_off_vm",  # destroy
        "pause": "pause_vm",
        "resume": "resume_vm",
        "hibernate": "hibernate_vm",  # managed save
    }

    def perform_action(self, internal_id: str, action: str) -> dict:
        """Run a power action on one VM. Blocking; call from a worker thread."""
        method_name = self.ACTION_METHODS.get(action)
        if method_name is None:
            raise ValueError(f"Unsupported action: {action}")
        _uri, _conn, domain = self._lookup_domain(internal_id)
        logger.info("Web action '%s' on VM %s", action, domain.UUIDString())
        getattr(self.vm_service, method_name)(domain)
        try:
            new_status = get_status(domain)
        except libvirt.libvirtError:
            new_status = None
        return {"id": internal_id, "action": action, "status": new_status}

    # -- bulk actions ----------------------------------------------------------

    BULK_ACTIONS = {
        "start": VmAction.START,
        "shutdown": VmAction.STOP,  # graceful
        "force_off": VmAction.FORCE_OFF,
        "pause": VmAction.PAUSE,
        "resume": VmAction.RESUME,
        "hibernate": VmAction.HIBERNATE,
    }

    def perform_bulk(self, ids: list, action: str) -> dict:
        """Run one power action across several VMs via VMService.perform_bulk_action."""
        action_type = self.BULK_ACTIONS.get(action)
        if action_type is None:
            raise ValueError(f"Unsupported bulk action: {action}")
        if not ids:
            raise ValueError("No VMs selected")
        uris: list = []
        for internal_id in ids:
            _uuid, sep, uri = str(internal_id).partition("@")
            if not sep:
                raise ValueError(f"Malformed VM id: {internal_id}")
            if uri not in uris:
                uris.append(uri)
        for uri in uris:
            if self.connection_manager.get_connection(uri) is None:
                raise ConnectionError(f"Server not connected: {uri}")

        log_lines: list = []

        def progress(kind, **kwargs):
            message = kwargs.get("message")
            if message:
                log_lines.append(str(message))

        logger.info("Web bulk '%s' on %d VM(s)", action, len(ids))
        successful, failed = self.vm_service.perform_bulk_action(
            uris, list(ids), action_type, False, progress
        )
        return {
            "action": action,
            "successful": successful,
            "failed": failed,
            "log": log_lines[-50:],
        }

    # -- details ---------------------------------------------------------------

    def vm_details(self, internal_id: str) -> dict:
        """Full read-only detail for one VM (mirrors the TUI details payload).

        Built with the same vm_queries helpers as VMService.get_vm_details,
        without its monitoring-dependent cache path.
        """
        uri, conn, domain = self._lookup_domain(internal_id)
        try:
            info = domain.info()
            xml_content = domain.XMLDesc(0)
        except libvirt.libvirtError as exc:
            raise LookupError(sanitize_sensitive_data(str(exc))) from exc

        root = None
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            pass

        return {
            "id": internal_id,
            "name": domain.name(),
            "uuid": domain.UUIDString(),
            "status": get_status(domain, state=info[0]),
            "description": get_vm_description(domain, root=root),
            "server": self._server_name_map().get(uri) or extract_server_name_from_uri(uri),
            "vcpus": info[3],
            "cpu_model": get_vm_cpu_model(root),
            "memory_mb": int(info[1]) // 1024,
            "machine_type": get_vm_machine_info(root),
            "firmware": get_vm_firmware_info(root),
            "shared_memory": get_vm_shared_memory_info(root),
            "boot": get_boot_info(conn, root),
            "video_model": get_vm_video_model(root),
            "networks": get_vm_networks_info(root),
            "detail_network": get_vm_network_ip(domain),
            "network_dns_gateway": get_vm_network_dns_gateway_info(domain, root=root),
            "disks": get_vm_disks_info(conn, root),
            "devices": get_vm_devices_info(root),
            "xml": xml_content,
        }

    # -- rename / delete -----------------------------------------------------

    def perform_rename(self, internal_id: str, new_name: str) -> dict:
        """Rename a (stopped) VM, preserving snapshots and NVRAM."""
        sanitized, _modified = _sanitize_domain_name(new_name)
        if not sanitized:
            raise ValueError(
                "Invalid new name (only letters, digits, hyphens and dots are allowed)"
            )
        _uri, _conn, domain = self._lookup_domain(internal_id)
        old_name = domain.name()
        logger.info("Web rename VM '%s' -> '%s'", old_name, sanitized)
        rename_vm(domain, sanitized)
        self.vm_service.invalidate_domain_cache()
        return {"id": internal_id, "old_name": old_name, "new_name": sanitized}

    def perform_delete(
        self,
        internal_id: str,
        delete_storage: bool = False,
        delete_nvram: bool = False,
    ) -> dict:
        """Undefine a VM, optionally removing its storage and NVRAM."""
        _uri, conn, domain = self._lookup_domain(internal_id)
        name = domain.name()
        logger.info(
            "Web delete VM '%s' (storage=%s, nvram=%s)", name, delete_storage, delete_nvram
        )
        self.vm_service.delete_vm(domain, delete_storage, delete_nvram, conn=conn)
        return {"id": internal_id, "deleted": name}

    # -- snapshots -------------------------------------------------------------

    def list_snapshots(self, internal_id: str) -> list[dict]:
        _uri, _conn, domain = self._lookup_domain(internal_id)
        snapshots = get_vm_snapshots(domain)
        for snap in snapshots:
            snap.pop("snapshot_object", None)  # live libvirt object, not JSON
        return snapshots

    def create_snapshot(
        self,
        internal_id: str,
        name: str,
        description: str = "",
        quiesce: bool = False,
    ) -> dict:
        sanitized, _modified = _sanitize_domain_name(name)
        if not sanitized:
            raise ValueError(
                "Invalid snapshot name (only letters, digits, hyphens and dots are allowed)"
            )
        _uri, _conn, domain = self._lookup_domain(internal_id)
        logger.info("Web snapshot create '%s' on %s", sanitized, internal_id.split("@", 1)[0])
        # Escape the free-form description: it is interpolated into the
        # snapshot XML by create_vm_snapshot.
        create_vm_snapshot(domain, sanitized, escape(description or ""), quiesce)
        return {"id": internal_id, "snapshot": sanitized}

    def restore_snapshot(self, internal_id: str, name: str) -> dict:
        _uri, _conn, domain = self._lookup_domain(internal_id)
        logger.info("Web snapshot restore '%s' on %s", name, internal_id.split("@", 1)[0])
        restore_vm_snapshot(domain, name)
        return {"id": internal_id, "restored": name}

    def delete_snapshot(self, internal_id: str, name: str) -> dict:
        _uri, _conn, domain = self._lookup_domain(internal_id)
        logger.info("Web snapshot delete '%s' on %s", name, internal_id.split("@", 1)[0])
        delete_vm_snapshot(domain, name)
        return {"id": internal_id, "deleted": name}

    # -- web console -----------------------------------------------------------

    def _get_webconsole_manager(self):
        """Lazily build a WebConsoleManager driven by the app stub."""
        if self._webconsole_manager is None:
            from ..webconsole_manager import WebConsoleManager  # heavy import

            class _TunnelWebConsoleManager(WebConsoleManager):
                """Force the SSH-tunnel + local-websockify path.

                REMOTE_WEBCONSOLE (run websockify on the hypervisor) makes
                sense for the TUI on the admin's workstation, but the web
                dashboard is itself served from a host every browser can
                reach: tunneling VNC here means noVNC and websockify are
                only needed on the dashboard host, and browsers never
                have to reach the hypervisors directly.
                """

                def _start_console_worker(self, vm, conn):
                    from .. import webconsole_manager as wcm

                    original = wcm.load_config

                    def patched():
                        cfg = original()
                        cfg["REMOTE_WEBCONSOLE"] = False
                        return cfg

                    wcm.load_config = patched
                    try:
                        super()._start_console_worker(vm, conn)
                    finally:
                        wcm.load_config = original

            stub = _WebConsoleAppStub(self.config)
            self._webconsole_manager = _TunnelWebConsoleManager(stub)
            self._webconsole_stub = stub
        return self._webconsole_manager, self._webconsole_stub

    @staticmethod
    def _console_uuid(domain: libvirt.virDomain, conn) -> str:
        """Session key used by WebConsoleManager (normalized internal id)."""
        uuid = get_internal_id(domain, conn)
        return uuid.split("?")[0] if "?" in uuid else uuid

    @staticmethod
    def _rewrite_console_url(url: str, browser_host: Optional[str]) -> str:
        """Point the saved localhost URL at the host the browser used."""
        if browser_host and "://localhost" in url:
            return url.replace("://localhost", f"://{browser_host}", 1)
        return url

    def start_console(self, internal_id: str, browser_host: Optional[str] = None) -> dict:
        """Start (or reuse) a noVNC websockify session for a VM."""
        _uri, conn, domain = self._lookup_domain(internal_id)
        manager, stub = self._get_webconsole_manager()
        console_uuid = self._console_uuid(domain, conn)
        with self._console_lock:
            stub.errors.clear()
            logger.info("Web console start for VM %s", domain.name())
            # Runs inline through the stub's worker_manager.
            manager.start_console_async(domain, conn)
            if stub.errors:
                raise ValueError("; ".join(stub.errors))
            session = manager.load_sessions().get(console_uuid)
        if not session or not session.get("url"):
            raise ValueError("Web console session did not start")
        return {
            "url": self._rewrite_console_url(session["url"], browser_host),
            "port": session.get("port"),
            "running": True,
        }

    def console_status(self, internal_id: str, browser_host: Optional[str] = None) -> dict:
        _uri, conn, domain = self._lookup_domain(internal_id)
        manager, _stub = self._get_webconsole_manager()
        console_uuid = self._console_uuid(domain, conn)
        if manager.is_running(console_uuid):
            session = manager.load_sessions().get(console_uuid, {})
            return {
                "running": True,
                "port": session.get("port"),
                "url": self._rewrite_console_url(session.get("url", ""), browser_host),
            }
        return {"running": False}

    def stop_console(self, internal_id: str) -> dict:
        _uri, conn, domain = self._lookup_domain(internal_id)
        manager, _stub = self._get_webconsole_manager()
        console_uuid = self._console_uuid(domain, conn)
        with self._console_lock:
            manager.stop_console(console_uuid, domain.name())
        return {"stopped": console_uuid}

    # -- guest agent -------------------------------------------------------------

    def guest_overview(self, internal_id: str) -> dict:
        """Agent status plus, when responsive, guest info and filesystems."""
        _uri, _conn, domain = self._lookup_domain(internal_id)
        agent_state = agent_status(domain)
        result = {"status": agent_state}
        if agent_state != "running":
            return result
        result["supported"] = ga_supported(domain)
        result["info"] = ga_info(domain)
        try:
            result["fsinfo"] = ga_fsinfo(domain)
        except GuestAgentError:
            result["fsinfo"] = []  # guest-get-fsinfo unsupported on this agent
        try:
            result["fsfreeze"] = ga_fsfreeze_status(domain)
        except GuestAgentError:
            result["fsfreeze"] = None
        return result

    def guest_action(self, internal_id: str, action: str) -> dict:
        _uri, _conn, domain = self._lookup_domain(internal_id)
        short_id = internal_id.split("@", 1)[0]
        if action == "freeze":
            logger.info("Web guest fsfreeze on %s", short_id)
            return {"action": action, "frozen": ga_fsfreeze_freeze(domain)}
        if action == "thaw":
            logger.info("Web guest fsthaw on %s", short_id)
            return {"action": action, "thawed": ga_fsfreeze_thaw(domain)}
        if action == "trim":
            logger.info("Web guest fstrim on %s", short_id)
            return {"action": action, "trimmed": ga_fstrim(domain)}
        if action == "sync_time":
            logger.info("Web guest time sync on %s", short_id)
            ga_set_time(domain)
            return {"action": action}
        if action == "shutdown":
            logger.info("Web guest shutdown on %s", short_id)
            ga_shutdown(domain, "powerdown")
            return {"action": action}
        if action == "suspend_ram":
            logger.info("Web guest suspend-to-RAM on %s", short_id)
            ga_suspend(domain, "ram")
            return {"action": action}
        if action == "suspend_disk":
            logger.info("Web guest suspend-to-disk on %s", short_id)
            ga_suspend(domain, "disk")
            return {"action": action}
        raise ValueError(f"Unsupported guest action: {action}")

    def guest_set_password(
        self, internal_id: str, username: str, password: str, crypted: bool = False
    ) -> dict:
        if not username:
            raise ValueError("Username is required")
        if not password:
            raise ValueError("Password is required")
        _uri, _conn, domain = self._lookup_domain(internal_id)
        # Never log the password itself.
        logger.info(
            "Web guest set-password for user '%s' on %s",
            username,
            internal_id.split("@", 1)[0],
        )
        ga_set_user_password(domain, username, password, crypted)
        return {"username": username}

    def guest_exec(self, internal_id: str, command: str, timeout: int = 30) -> dict:
        command = (command or "").strip()
        if not command:
            raise ValueError("Command is required")
        _uri, _conn, domain = self._lookup_domain(internal_id)
        bounded = max(1, min(int(timeout or 30), MAX_GUEST_EXEC_TIMEOUT))
        logger.info(
            "Web guest exec on %s: %.200s", internal_id.split("@", 1)[0], command
        )
        return ga_exec_wait(domain, command, overall_timeout=bounded)

    def guest_ssh_keys(self, internal_id: str, user: str) -> dict:
        if not user:
            raise ValueError("User is required")
        _uri, _conn, domain = self._lookup_domain(internal_id)
        return {"user": user, "keys": ga_ssh_get_authorized_keys(domain, user)}

    def guest_ssh_add_keys(self, internal_id: str, user: str, keys: list) -> dict:
        if not user:
            raise ValueError("User is required")
        clean = [str(k).strip() for k in (keys or []) if str(k).strip()]
        if not clean:
            raise ValueError("No keys provided")
        _uri, _conn, domain = self._lookup_domain(internal_id)
        logger.info(
            "Web guest ssh add %d key(s) for user '%s' on %s",
            len(clean), user, internal_id.split("@", 1)[0],
        )
        ga_ssh_add_authorized_keys(domain, user, clean)
        return {"user": user, "added": len(clean)}

    def guest_ssh_remove_keys(self, internal_id: str, user: str, keys: list) -> dict:
        if not user:
            raise ValueError("User is required")
        clean = [str(k).strip() for k in (keys or []) if str(k).strip()]
        if not clean:
            raise ValueError("No keys provided")
        _uri, _conn, domain = self._lookup_domain(internal_id)
        logger.info(
            "Web guest ssh remove %d key(s) for user '%s' on %s",
            len(clean), user, internal_id.split("@", 1)[0],
        )
        ga_ssh_remove_authorized_keys(domain, user, clean)
        return {"user": user, "removed": len(clean)}

    def guest_upload(self, internal_id: str, remote_path: str, tmp_path: str) -> dict:
        if not remote_path.startswith("/"):
            raise ValueError("Remote path must be absolute")
        _uri, _conn, domain = self._lookup_domain(internal_id)
        logger.info(
            "Web guest upload -> '%s' on %s",
            remote_path,
            internal_id.split("@", 1)[0],
        )
        written = ga_upload_file(domain, tmp_path, remote_path)
        return {"path": remote_path, "bytes": written}

    def guest_download(self, internal_id: str, remote_path: str, tmp_path: str) -> dict:
        if not remote_path.startswith("/"):
            raise ValueError("Remote path must be absolute")
        _uri, _conn, domain = self._lookup_domain(internal_id)
        logger.info(
            "Web guest download <- '%s' on %s",
            remote_path,
            internal_id.split("@", 1)[0],
        )
        read = ga_download_file(domain, remote_path, tmp_path)
        return {"path": remote_path, "bytes": read}

    # -- live statistics ---------------------------------------------------

    def stats_snapshot(self, uris=None) -> dict:
        """Return runtime statistics keyed by internal id (UUID@URI).

        Args:
            uris: optional list of server URIs to restrict collection to
                (None = every connected server).
        """
        stats = {}
        for internal_id, _uri, _server_name, _conn, domain in self._iter_domains(uris=uris):
            try:
                vm_stats = self.vm_service.get_vm_runtime_stats(domain)
            except Exception:  # pylint: disable=broad-except
                logger.debug("Stats collection failed for %s", internal_id, exc_info=True)
                vm_stats = None
            if vm_stats:
                stats[internal_id] = vm_stats
        return stats


def create_app(
    backend: Backend,
    token: str,
    stats_interval: Optional[int] = None,
) -> FastAPI:
    """Build the FastAPI application.

    Args:
        backend: service-layer accessor (injectable for tests).
        token: bearer token required on every API/WS call.
        stats_interval: override for the WebSocket push interval (seconds).
    """
    app = FastAPI(
        title="VirtUI Manager Web API",
        version="1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    bearer = HTTPBearer(auto_error=False)
    interval = DEFAULT_STATS_INTERVAL
    if stats_interval:
        interval = stats_interval
    else:
        try:
            interval = int(backend.config.get("STATS_INTERVAL", DEFAULT_STATS_INTERVAL))
        except (TypeError, ValueError, AttributeError):
            pass
    collector = StatsCollector(backend, interval)

    def require_token(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> None:
        if credentials is None or not secrets.compare_digest(credentials.credentials, token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API token",
            )

    @app.get("/api/health")
    async def health() -> dict:
        """Unauthenticated liveness probe."""
        return {"status": "ok"}

    @app.get("/api/servers", dependencies=[Depends(require_token)])
    async def servers() -> list[dict]:
        return await run_in_threadpool(backend.servers_snapshot)

    @app.post("/api/servers", dependencies=[Depends(require_token)])
    async def server_add(payload: ServerAddRequest) -> dict:
        return await run_backend(
            backend.add_server, payload.name, payload.uri, payload.autoconnect
        )

    @app.put("/api/servers", dependencies=[Depends(require_token)])
    async def server_edit(payload: ServerEditRequest) -> dict:
        return await run_backend(
            backend.edit_server,
            payload.original_uri,
            payload.name,
            payload.uri,
            payload.autoconnect,
        )

    @app.delete("/api/servers", dependencies=[Depends(require_token)])
    async def server_delete(uri: str) -> dict:
        return await run_backend(backend.delete_server, uri)

    @app.post("/api/servers/connect", dependencies=[Depends(require_token)])
    async def server_connect(payload: ServerConnectRequest) -> dict:
        return await run_backend(backend.connect_server, payload.uri)

    @app.post("/api/servers/disconnect", dependencies=[Depends(require_token)])
    async def server_disconnect(payload: ServerConnectRequest) -> dict:
        return await run_backend(backend.disconnect_server, payload.uri)

    @app.get("/api/servers/stats", dependencies=[Depends(require_token)])
    async def server_stats(uri: str) -> dict:
        return await run_backend(backend.host_stats, uri)

    # -- server preferences: networks --------------------------------------

    @app.get("/api/prefs/networks", dependencies=[Depends(require_token)])
    async def prefs_networks(uri: str) -> list[dict]:
        return await run_backend(backend.networks_list, uri)

    @app.post("/api/prefs/networks/active", dependencies=[Depends(require_token)])
    async def prefs_network_active(payload: NetworkActiveRequest) -> dict:
        return await run_backend(
            backend.network_set_active, payload.uri, payload.name, payload.active
        )

    @app.post("/api/prefs/networks/autostart", dependencies=[Depends(require_token)])
    async def prefs_network_autostart(payload: NetworkAutostartRequest) -> dict:
        return await run_backend(
            backend.network_set_autostart, payload.uri, payload.name, payload.autostart
        )

    @app.post("/api/prefs/networks/delete", dependencies=[Depends(require_token)])
    async def prefs_network_delete(payload: NetworkRequest) -> dict:
        return await run_backend(backend.network_delete, payload.uri, payload.name)

    @app.get("/api/prefs/networks/xml", dependencies=[Depends(require_token)])
    async def prefs_network_xml(uri: str, name: str) -> dict:
        return await run_backend(backend.network_xml, uri, name)

    # -- server preferences: storage ----------------------------------------

    @app.get("/api/prefs/storage", dependencies=[Depends(require_token)])
    async def prefs_storage(uri: str) -> list[dict]:
        return await run_backend(backend.storage_snapshot, uri)

    @app.post("/api/prefs/storage/pool/active", dependencies=[Depends(require_token)])
    async def prefs_pool_active(payload: PoolActiveRequest) -> dict:
        return await run_backend(
            backend.pool_set_active, payload.uri, payload.pool, payload.active
        )

    @app.post("/api/prefs/storage/pool/autostart", dependencies=[Depends(require_token)])
    async def prefs_pool_autostart(payload: PoolAutostartRequest) -> dict:
        return await run_backend(
            backend.pool_set_autostart, payload.uri, payload.pool, payload.autostart
        )

    @app.post("/api/prefs/storage/pool/delete", dependencies=[Depends(require_token)])
    async def prefs_pool_delete(payload: PoolRequest) -> dict:
        return await run_backend(backend.pool_delete, payload.uri, payload.pool)

    @app.get("/api/prefs/storage/pool/xml", dependencies=[Depends(require_token)])
    async def prefs_pool_xml(uri: str, pool: str) -> dict:
        return await run_backend(backend.pool_xml, uri, pool)

    @app.post("/api/prefs/storage/volume/delete", dependencies=[Depends(require_token)])
    async def prefs_volume_delete(payload: VolumeRequest) -> dict:
        return await run_backend(
            backend.volume_delete, payload.uri, payload.pool, payload.volume
        )

    @app.get("/api/prefs/storage/volume/xml", dependencies=[Depends(require_token)])
    async def prefs_volume_xml(uri: str, pool: str, volume: str) -> dict:
        return await run_backend(backend.volume_xml, uri, pool, volume)

    @app.get("/api/vms", dependencies=[Depends(require_token)])
    async def vms() -> list[dict]:
        return await run_in_threadpool(backend.vms_snapshot)

    async def run_backend(fn, *args):
        """Run a blocking Backend method with common error mapping."""
        try:
            return await run_in_threadpool(fn, *args)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, sanitize_sensitive_data(str(exc))
            ) from exc
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except GuestAgentError as exc:
            logger.error("Guest agent error: %s", sanitize_sensitive_data(str(exc)))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, sanitize_sensitive_data(str(exc))
            ) from exc
        except libvirt.libvirtError as exc:
            logger.error("Backend call failed: %s", sanitize_sensitive_data(str(exc)))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, sanitize_sensitive_data(str(exc))
            ) from exc
        except Exception as exc:  # pylint: disable=broad-except
            # vm_actions raises plain Exception with a message on some paths
            # (e.g. snapshot restore/delete failures).
            logger.error("Backend call failed: %s", sanitize_sensitive_data(str(exc)))
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, sanitize_sensitive_data(str(exc))
            ) from exc

    @app.post("/api/vms/action", dependencies=[Depends(require_token)])
    async def vm_action(request: ActionRequest) -> dict:
        return await run_backend(backend.perform_action, request.id, request.action)

    @app.post("/api/vms/bulk", dependencies=[Depends(require_token)])
    async def vms_bulk(payload: BulkRequest) -> dict:
        return await run_backend(backend.perform_bulk, payload.ids, payload.action)

    @app.get("/api/vms/detail", dependencies=[Depends(require_token)])
    async def vm_detail(id: str) -> dict:  # pylint: disable=redefined-builtin
        return await run_backend(backend.vm_details, id)

    @app.post("/api/vms/rename", dependencies=[Depends(require_token)])
    async def vm_rename(request: RenameRequest) -> dict:
        return await run_backend(backend.perform_rename, request.id, request.new_name)

    @app.post("/api/vms/delete", dependencies=[Depends(require_token)])
    async def vm_delete(request: DeleteRequest) -> dict:
        return await run_backend(
            backend.perform_delete,
            request.id,
            request.delete_storage,
            request.delete_nvram,
        )

    @app.get("/api/vms/snapshots", dependencies=[Depends(require_token)])
    async def snapshots_list(id: str) -> list[dict]:  # pylint: disable=redefined-builtin
        return await run_backend(backend.list_snapshots, id)

    @app.post("/api/vms/snapshots", dependencies=[Depends(require_token)])
    async def snapshots_create(request: SnapshotCreateRequest) -> dict:
        return await run_backend(
            backend.create_snapshot,
            request.id,
            request.name,
            request.description,
            request.quiesce,
        )

    @app.post("/api/vms/snapshots/restore", dependencies=[Depends(require_token)])
    async def snapshots_restore(request: SnapshotRefRequest) -> dict:
        return await run_backend(backend.restore_snapshot, request.id, request.name)

    @app.post("/api/vms/snapshots/delete", dependencies=[Depends(require_token)])
    async def snapshots_delete(request: SnapshotRefRequest) -> dict:
        return await run_backend(backend.delete_snapshot, request.id, request.name)

    @app.post("/api/vms/console", dependencies=[Depends(require_token)])
    async def console_start(request: Request, payload: ConsoleRequest) -> dict:
        return await run_backend(
            backend.start_console, payload.id, request.url.hostname
        )

    @app.get("/api/vms/console", dependencies=[Depends(require_token)])
    async def console_status(request: Request, id: str) -> dict:  # pylint: disable=redefined-builtin
        return await run_backend(backend.console_status, id, request.url.hostname)

    @app.post("/api/vms/console/stop", dependencies=[Depends(require_token)])
    async def console_stop(payload: ConsoleRequest) -> dict:
        return await run_backend(backend.stop_console, payload.id)

    # -- guest agent -----------------------------------------------------------

    @app.get("/api/vms/guest", dependencies=[Depends(require_token)])
    async def guest_overview(id: str) -> dict:  # pylint: disable=redefined-builtin
        return await run_backend(backend.guest_overview, id)

    @app.post("/api/vms/guest/action", dependencies=[Depends(require_token)])
    async def guest_action(payload: GuestActionRequest) -> dict:
        return await run_backend(backend.guest_action, payload.id, payload.action)

    @app.post("/api/vms/guest/password", dependencies=[Depends(require_token)])
    async def guest_password(payload: GuestPasswordRequest) -> dict:
        return await run_backend(
            backend.guest_set_password,
            payload.id,
            payload.username,
            payload.password,
            payload.crypted,
        )

    @app.post("/api/vms/guest/exec", dependencies=[Depends(require_token)])
    async def guest_exec(payload: GuestExecRequest) -> dict:
        return await run_backend(
            backend.guest_exec, payload.id, payload.command, payload.timeout
        )

    @app.get("/api/vms/guest/ssh_keys", dependencies=[Depends(require_token)])
    async def guest_ssh_keys(id: str, user: str) -> dict:  # pylint: disable=redefined-builtin
        return await run_backend(backend.guest_ssh_keys, id, user)

    @app.post("/api/vms/guest/ssh_keys", dependencies=[Depends(require_token)])
    async def guest_ssh_add(payload: GuestSshKeysRequest) -> dict:
        return await run_backend(backend.guest_ssh_add_keys, payload.id, payload.user, payload.keys)

    @app.post("/api/vms/guest/ssh_keys/delete", dependencies=[Depends(require_token)])
    async def guest_ssh_remove(payload: GuestSshKeysRequest) -> dict:
        return await run_backend(
            backend.guest_ssh_remove_keys, payload.id, payload.user, payload.keys
        )

    @app.post("/api/vms/guest/upload", dependencies=[Depends(require_token)])
    async def guest_upload(
        id: str = Form(...),  # pylint: disable=redefined-builtin
        path: str = Form(...),
        file: UploadFile = File(...),
    ) -> dict:
        tmp = tempfile.NamedTemporaryFile(
            delete=False, prefix="virtui-web-upload-", dir=tempfile.gettempdir()
        )
        size = 0
        try:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_GUEST_UPLOAD_BYTES:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"File too large (max {MAX_GUEST_UPLOAD_BYTES // (1024 * 1024)} MB)",
                    )
                tmp.write(chunk)
            tmp.close()
            return await run_backend(backend.guest_upload, id, path, tmp.name)
        finally:
            try:
                tmp.close()
            except Exception:  # pylint: disable=broad-except
                pass
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    @app.get("/api/vms/guest/download", dependencies=[Depends(require_token)])
    async def guest_download(id: str, path: str):  # pylint: disable=redefined-builtin
        tmp = tempfile.NamedTemporaryFile(
            delete=False, prefix="virtui-web-download-", dir=tempfile.gettempdir()
        )
        tmp.close()
        try:
            await run_backend(backend.guest_download, id, path, tmp.name)
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
        filename = os.path.basename(path) or "download"

        def _cleanup() -> None:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        return FileResponse(
            tmp.name, filename=filename, background=BackgroundTask(_cleanup)
        )

    @app.websocket("/ws/stats")
    async def ws_stats(websocket: WebSocket) -> None:
        """Streams live VM statistics from the shared collector.

        Clients may send {"type": "selection", "uris": [...]} messages to
        restrict collection (and delivery) to the servers they display;
        before the first selection message, all servers are covered.
        """
        ws_token = websocket.query_params.get("token", "")
        # Accept first, then close with the custom code: closing before
        # accept() surfaces as an HTTP 403 handshake failure and browsers
        # report code 1006 instead of WS_INVALID_TOKEN_CODE.
        await websocket.accept()
        if not secrets.compare_digest(ws_token, token):
            await websocket.close(code=WS_INVALID_TOKEN_CODE)
            return

        sub = collector.subscribe()
        collector.ensure_running()

        async def send_loop() -> None:
            while True:
                payload = await sub.queue.get()
                await websocket.send_text(payload)

        async def recv_loop() -> None:
            while True:
                text = await websocket.receive_text()
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "selection":
                    uris = msg.get("uris")
                    sub.uris = None if uris is None else {str(u) for u in uris}

        sender = asyncio.create_task(send_loop())
        receiver = asyncio.create_task(recv_loop())
        try:
            done, pending = await asyncio.wait(
                {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    logger.debug("Stats WS task ended: %s", task.exception())
        except WebSocketDisconnect:
            sender.cancel()
            receiver.cancel()
        finally:
            collector.unsubscribe(sub)

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        """Pushes libvirt lifecycle events (vm state, inventory, messages)."""
        ws_token = websocket.query_params.get("token", "")
        await websocket.accept()
        if not secrets.compare_digest(ws_token, token):
            await websocket.close(code=WS_INVALID_TOKEN_CODE)
            return
        bus = getattr(backend, "event_bus", None)
        if bus is None:
            await websocket.close()
            return
        bus.bind_loop(asyncio.get_running_loop())
        queue = bus.subscribe()
        try:
            while True:
                event = await queue.get()
                await websocket.send_text(json.dumps(event))
        except WebSocketDisconnect:
            pass
        except Exception:  # pylint: disable=broad-except
            logger.debug("Events WebSocket closed", exc_info=True)
        finally:
            bus.unsubscribe(queue)

    # Static frontend (mounted last so /api/* always wins).
    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"}
        )

    return app
