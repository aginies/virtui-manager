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
from pathlib import Path
from typing import Optional

import libvirt
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from ..config import load_config
from ..libvirt_utils import get_internal_id
from ..utils import extract_server_name_from_uri, natural_sort_key, sanitize_sensitive_data
from ..vm_queries import get_status, get_vm_network_ip

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_STATS_INTERVAL = 5
WS_INVALID_TOKEN_CODE = 4401

logger = logging.getLogger(__name__)


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
                    yield get_internal_id(domain, conn), uri, server_name, conn, domain
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

    @app.websocket("/ws/stats")
    async def ws_stats(websocket: WebSocket) -> None:
        ws_token = websocket.query_params.get("token", "")
        if not secrets.compare_digest(ws_token, token):
            await websocket.close(code=WS_INVALID_TOKEN_CODE)
            return
        await websocket.accept()
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
