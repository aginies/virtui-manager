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
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

import libvirt
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import load_config
from ..modals.input_modals import _sanitize_domain_name
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
        self._server_names = {
            server.get("uri"): server.get("name")
            for server in self.config.get("servers", [])
            if server.get("uri")
        }

    # -- connections -------------------------------------------------------

    def connect_configured_servers(self) -> None:
        """Connect to every server listed in the configuration."""
        for uri in self._server_names:
            if self.connection_manager.get_connection(uri) is None:
                try:
                    self.connection_manager.connect(uri)
                except Exception:  # pylint: disable=broad-except
                    logger.error(
                        "Failed to connect to %s", sanitize_sensitive_data(uri), exc_info=True
                    )

    def servers_snapshot(self) -> list[dict]:
        """Return the configured servers with their connection state."""
        servers = []
        for server in self.config.get("servers", []):
            uri = server.get("uri")
            if not uri:
                continue
            entry = {
                "name": server.get("name") or extract_server_name_from_uri(uri),
                "uri": uri,
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
            servers.append(entry)
        return servers

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
            server_name = self._server_names.get(uri) or extract_server_name_from_uri(uri)
            for domain in domains:
                try:
                    # Build the id from the connection-dict key, NOT
                    # conn.getURI(): libvirt may report a normalized URI
                    # (e.g. "?no_tty=1") that would not resolve back
                    # through get_connection(). Same reason VMService
                    # passes known_uri to get_vm_identity().
                    yield f"{domain.UUIDString()}@{uri}", uri, server_name, conn, domain
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
            "server": self._server_names.get(uri) or extract_server_name_from_uri(uri),
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
