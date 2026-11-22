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
import secrets
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
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import load_config, save_config
from ..libvirt_utils import get_internal_id
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

    def __init__(self, config: Optional[dict] = None, vm_service=None):
        self.config = config if config is not None else load_config()
        if vm_service is not None:
            self.vm_service = vm_service
        else:
            from ..vm_service import VMService  # heavy import, keep lazy

            self.vm_service = VMService()
        self.connection_manager = self.vm_service.connection_manager
        self._config_lock = threading.Lock()
        self._webconsole_manager = None
        self._webconsole_stub = None
        self._console_lock = threading.Lock()

    # -- connections -------------------------------------------------------

    @staticmethod
    def _configured_servers() -> list[dict]:
        """Server entries from the on-disk config (picks up TUI/CLI edits)."""
        return [s for s in load_config().get("servers", []) if s.get("uri")]

    @classmethod
    def _server_name_map(cls) -> dict:
        return {s["uri"]: s.get("name") for s in cls._configured_servers()}

    def connect_configured_servers(self) -> None:
        """Connect to every server listed in the configuration."""
        for uri in self._server_name_map():
            if self.connection_manager.get_connection(uri) is None:
                try:
                    self.connection_manager.connect(uri)
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
        conn = self.connection_manager.connect(uri, force_retry=True)
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
        self.connection_manager.disconnect(uri)
        logger.info("Web deleted server %s", sanitize_sensitive_data(uri))
        return {"deleted": uri}

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

    def _iter_domains(self):
        """Yield (internal_id, uri, server_name, conn, domain) for every VM."""
        for uri in self.connection_manager.get_all_uris():
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
            server_names = self._server_name_map()
            for domain in domains:
                try:
                    # Build the id from the connection-dict key, NOT
                    # conn.getURI(): libvirt may report a normalized URI
                    # (e.g. "?no_tty=1") that would not resolve back
                    # through get_connection(). Same reason VMService
                    # passes known_uri to get_vm_identity().
                    yield f"{domain.UUIDString()}@{uri}", uri, server_names.get(
                        uri
                    ) or extract_server_name_from_uri(uri), conn, domain
                except libvirt.libvirtError:
                    continue

    def vms_snapshot(self) -> list[dict]:
        """Return the full VM inventory across all connected servers."""
        vms = []
        for internal_id, uri, server_name, conn, domain in self._iter_domains():
            try:
                info = domain.info()
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
                        "ipv4": _first_ipv4(domain),
                    }
                )
            except libvirt.libvirtError as exc:
                logger.debug(
                    "Skipping unreadable domain: %s", sanitize_sensitive_data(str(exc))
                )
        vms.sort(key=lambda vm: natural_sort_key(vm["name"]))
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

    # -- live statistics ---------------------------------------------------

    def stats_snapshot(self) -> dict:
        """Return runtime statistics keyed by internal id (UUID@URI)."""
        stats = {}
        for internal_id, _uri, _server_name, _conn, domain in self._iter_domains():
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

    @app.websocket("/ws/stats")
    async def ws_stats(websocket: WebSocket) -> None:
        ws_token = websocket.query_params.get("token", "")
        # Accept first, then close with the custom code: closing before
        # accept() surfaces as an HTTP 403 handshake failure and browsers
        # report code 1006 instead of WS_INVALID_TOKEN_CODE.
        await websocket.accept()
        if not secrets.compare_digest(ws_token, token):
            await websocket.close(code=WS_INVALID_TOKEN_CODE)
            return
        interval = stats_interval or backend.config.get(
            "STATS_INTERVAL", DEFAULT_STATS_INTERVAL
        )
        try:
            while True:
                stats = await run_in_threadpool(backend.stats_snapshot)
                await websocket.send_text(json.dumps({"ts": time.time(), "stats": stats}))
                await asyncio.sleep(max(1, int(interval)))
        except WebSocketDisconnect:
            pass
        except Exception:  # pylint: disable=broad-except
            logger.debug("Stats WebSocket closed", exc_info=True)

    # Static frontend (mounted last so /api/* always wins).
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
