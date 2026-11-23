import asyncio
import builtins
import copy
import json
import os
import sys
import unittest
import urllib.parse
from unittest.mock import MagicMock, patch

import pytest

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# Mock translation function globally for tests
builtins._ = lambda s: s

try:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from vmanager.web.api import Backend, create_app

    HAS_WEB_DEPS = True
except ImportError:
    HAS_WEB_DEPS = False

TOKEN = "test-token-123"

SERVERS = [
    {
        "name": "Localhost",
        "uri": "qemu:///system",
        "connected": True,
        "vm_count": 1,
        "error": None,
    }
]

VMS = [
    {
        "id": "aaaa-bbbb@qemu:///system",
        "uuid": "aaaa-bbbb",
        "name": "vm-web",
        "status": "Running",
        "server": "Localhost",
        "uri": "qemu:///system",
        "vcpus": 4,
        "memory_mb": 8192,
        "ipv4": "192.168.122.10",
    }
]

STATS = {
    "aaaa-bbbb@qemu:///system": {
        "status": "Running",
        "cpu_percent": 12.5,
        "mem_percent": 40.0,
        "disk_read_kbps": 0.0,
        "disk_write_kbps": 100.0,
        "net_rx_kbps": 10.0,
        "net_tx_kbps": 5.0,
    }
}


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApi(unittest.TestCase):
    def setUp(self):
        self.backend = MagicMock()
        self.backend.config = {"STATS_INTERVAL": 1}
        self.backend.servers_snapshot.return_value = SERVERS
        self.backend.vms_snapshot.return_value = VMS
        self.backend.stats_snapshot.return_value = STATS
        # Small interval so the WS test does not wait long.
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def test_health_requires_no_token(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_servers_requires_token(self):
        resp = self.client.get("/api/servers")
        self.assertEqual(resp.status_code, 401)

        resp = self.client.get("/api/servers", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(resp.status_code, 401)

        resp = self.client.get("/api/servers", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), SERVERS)

    def test_vms_requires_token(self):
        resp = self.client.get("/api/vms")
        self.assertEqual(resp.status_code, 401)

        resp = self.client.get("/api/vms", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        vms = resp.json()
        self.assertEqual(len(vms), 1)
        self.assertEqual(vms[0]["name"], "vm-web")
        self.assertEqual(vms[0]["ipv4"], "192.168.122.10")

    def test_index_served(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])

    def test_static_assets_served(self):
        for asset in ("app.js", "sparkline.js", "style.css"):
            resp = self.client.get(f"/static/{asset}")
            self.assertEqual(resp.status_code, 200, asset)

    def test_ws_stats_pushes_snapshot(self):
        with self.client.websocket_connect(f"/ws/stats?token={TOKEN}") as ws:
            msg = ws.receive_json()
            self.assertIn("ts", msg)
            self.assertIn("aaaa-bbbb@qemu:///system", msg["stats"])
            stats = msg["stats"]["aaaa-bbbb@qemu:///system"]
            self.assertEqual(stats["status"], "Running")
            self.assertAlmostEqual(stats["cpu_percent"], 12.5)

    def test_ws_stats_rejects_bad_token(self):
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect("/ws/stats?token=nope") as ws:
                ws.receive_json()
        self.assertEqual(ctx.exception.code, 4401)


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApiActions(unittest.TestCase):
    def setUp(self):
        self.backend = MagicMock()
        self.backend.config = {"STATS_INTERVAL": 1}
        self.backend.perform_action.return_value = {
            "id": "aaaa-bbbb@qemu:///system",
            "action": "start",
            "status": "Running",
        }
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def _post(self, payload, headers=None):
        return self.client.post(
            "/api/vms/action",
            json=payload,
            headers=headers if headers is not None else self.auth,
        )

    def test_action_requires_token(self):
        resp = self._post({"id": "aaaa-bbbb@qemu:///system", "action": "start"}, headers={})
        self.assertEqual(resp.status_code, 401)

    def test_action_success(self):
        resp = self._post({"id": "aaaa-bbbb@qemu:///system", "action": "start"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "Running")
        self.backend.perform_action.assert_called_once_with(
            "aaaa-bbbb@qemu:///system", "start"
        )

    def test_action_bad_request_mapping(self):
        self.backend.perform_action.side_effect = ValueError("Unsupported action: explode")
        resp = self._post({"id": "aaaa-bbbb@qemu:///system", "action": "explode"})
        self.assertEqual(resp.status_code, 400)

        self.backend.perform_action.side_effect = ConnectionError("Server not connected")
        resp = self._post({"id": "aaaa-bbbb@qemu:///system", "action": "start"})
        self.assertEqual(resp.status_code, 409)

        self.backend.perform_action.side_effect = LookupError("no such domain")
        resp = self._post({"id": "aaaa-bbbb@qemu:///system", "action": "start"})
        self.assertEqual(resp.status_code, 404)

    def test_action_body_validation(self):
        resp = self._post({"action": "start"})  # missing id
        self.assertEqual(resp.status_code, 422)


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApiDetail(unittest.TestCase):
    DETAIL = {
        "id": "aaaa-bbbb@qemu:///system",
        "name": "vm-web",
        "uuid": "aaaa-bbbb",
        "status": "Running",
        "description": "web vm",
        "server": "Localhost",
        "vcpus": 4,
        "cpu_model": "host-passthrough",
        "memory_mb": 8192,
        "machine_type": "q35",
        "firmware": {"type": "UEFI", "path": None, "secure_boot": True},
        "shared_memory": False,
        "boot": {"menu_enabled": False, "order": ["hd"]},
        "video_model": "virtio",
        "networks": [{"mac": "52:54:00:12:34:56", "network": "default", "model": "virtio"}],
        "detail_network": [],
        "network_dns_gateway": [],
        "disks": [
            {"path": "/var/lib/libvirt/images/vm-web.qcow2", "status": "enabled",
             "bus": "virtio", "cache_mode": "none", "discard_mode": "unmap"}
        ],
        "devices": {"graphics": [{"type": "vnc"}], "tpm": []},
        "xml": "<domain></domain>",
    }

    def setUp(self):
        self.backend = MagicMock()
        self.backend.config = {"STATS_INTERVAL": 1}
        self.backend.vm_details.return_value = self.DETAIL
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def test_detail_requires_token(self):
        resp = self.client.get("/api/vms/detail", params={"id": self.DETAIL["id"]})
        self.assertEqual(resp.status_code, 401)

    def test_detail_success(self):
        resp = self.client.get(
            "/api/vms/detail", params={"id": self.DETAIL["id"]}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["name"], "vm-web")
        self.assertTrue(data["firmware"]["secure_boot"])
        self.assertEqual(data["disks"][0]["bus"], "virtio")
        self.backend.vm_details.assert_called_once_with(self.DETAIL["id"])

    def test_detail_not_found(self):
        self.backend.vm_details.side_effect = LookupError("no such domain")
        resp = self.client.get(
            "/api/vms/detail", params={"id": "gone@qemu:///system"}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 404)

    def test_detail_requires_id(self):
        resp = self.client.get("/api/vms/detail", headers=self.auth)
        self.assertEqual(resp.status_code, 422)


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApiManage(unittest.TestCase):
    """Rename / delete / snapshot endpoints (phase 3-4)."""

    VM_ID = "aaaa-bbbb@qemu:///system"

    def setUp(self):
        self.backend = MagicMock()
        self.backend.config = {"STATS_INTERVAL": 1}
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def _post(self, url, payload, headers=None):
        return self.client.post(
            url, json=payload, headers=headers if headers is not None else self.auth
        )

    def test_rename(self):
        self.backend.perform_rename.return_value = {
            "id": self.VM_ID, "old_name": "vm-old", "new_name": "vm-new",
        }
        resp = self._post("/api/vms/rename", {"id": self.VM_ID, "new_name": "vm-new"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["new_name"], "vm-new")
        self.backend.perform_rename.assert_called_once_with(self.VM_ID, "vm-new")

        resp = self._post("/api/vms/rename", {"id": self.VM_ID, "new_name": "x"}, headers={})
        self.assertEqual(resp.status_code, 401)

    def test_rename_invalid_name_maps_400(self):
        self.backend.perform_rename.side_effect = ValueError("Invalid new name")
        resp = self._post("/api/vms/rename", {"id": self.VM_ID, "new_name": "!!!"})
        self.assertEqual(resp.status_code, 400)

    def test_delete_defaults(self):
        self.backend.perform_delete.return_value = {"id": self.VM_ID, "deleted": "vm-old"}
        resp = self._post("/api/vms/delete", {"id": self.VM_ID})
        self.assertEqual(resp.status_code, 200)
        self.backend.perform_delete.assert_called_once_with(self.VM_ID, False, False)

    def test_delete_with_flags(self):
        self.backend.perform_delete.return_value = {"id": self.VM_ID, "deleted": "vm-old"}
        resp = self._post(
            "/api/vms/delete",
            {"id": self.VM_ID, "delete_storage": True, "delete_nvram": True},
        )
        self.assertEqual(resp.status_code, 200)
        self.backend.perform_delete.assert_called_once_with(self.VM_ID, True, True)

    def test_snapshots_list(self):
        self.backend.list_snapshots.return_value = [
            {"name": "snap1", "description": "", "creation_time": "2026-01-01 00:00:00",
             "state": "shutoff"},
        ]
        resp = self.client.get(
            "/api/vms/snapshots", params={"id": self.VM_ID}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["name"], "snap1")

        resp = self.client.get("/api/vms/snapshots", params={"id": self.VM_ID})
        self.assertEqual(resp.status_code, 401)

    def test_snapshot_create(self):
        self.backend.create_snapshot.return_value = {"id": self.VM_ID, "snapshot": "snap2"}
        resp = self._post(
            "/api/vms/snapshots",
            {"id": self.VM_ID, "name": "snap2", "description": "pre-upgrade", "quiesce": True},
        )
        self.assertEqual(resp.status_code, 200)
        self.backend.create_snapshot.assert_called_once_with(
            self.VM_ID, "snap2", "pre-upgrade", True
        )

    def test_snapshot_restore_and_delete(self):
        self.backend.restore_snapshot.return_value = {"id": self.VM_ID, "restored": "snap1"}
        self.backend.delete_snapshot.return_value = {"id": self.VM_ID, "deleted": "snap1"}
        resp = self._post("/api/vms/snapshots/restore", {"id": self.VM_ID, "name": "snap1"})
        self.assertEqual(resp.status_code, 200)
        resp = self._post("/api/vms/snapshots/delete", {"id": self.VM_ID, "name": "snap1"})
        self.assertEqual(resp.status_code, 200)

    def test_generic_backend_error_maps_500(self):
        # vm_actions raises plain Exception on snapshot failures
        self.backend.restore_snapshot.side_effect = Exception(
            "Failed to restore snapshot 'snap1': disk error"
        )
        resp = self._post("/api/vms/snapshots/restore", {"id": self.VM_ID, "name": "snap1"})
        self.assertEqual(resp.status_code, 500)
        self.assertIn("Failed to restore", resp.json()["detail"])


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApiConsole(unittest.TestCase):
    """Web console endpoints (phase 5)."""

    VM_ID = "aaaa-bbbb@qemu:///system"

    def setUp(self):
        self.backend = MagicMock()
        self.backend.config = {"STATS_INTERVAL": 1}
        self.backend.start_console.return_value = {
            "url": "http://browser-host:40000/vnc.html?path=websockify",
            "port": 40000,
            "running": True,
        }
        self.backend.console_status.return_value = {"running": False}
        self.backend.stop_console.return_value = {"stopped": self.VM_ID}
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def _post(self, url, payload, headers=None):
        return self.client.post(
            url, json=payload, headers=headers if headers is not None else self.auth
        )

    def test_console_start(self):
        resp = self._post("/api/vms/console", {"id": self.VM_ID})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["port"], 40000)
        # browser host comes from the request URL
        self.backend.start_console.assert_called_once_with(self.VM_ID, "testserver")

    def test_console_start_requires_token(self):
        resp = self._post("/api/vms/console", {"id": self.VM_ID}, headers={})
        self.assertEqual(resp.status_code, 401)

    def test_console_start_error_maps_400(self):
        self.backend.start_console.side_effect = ValueError(
            "Web console supports VNC only"
        )
        resp = self._post("/api/vms/console", {"id": self.VM_ID})
        self.assertEqual(resp.status_code, 400)

    def test_console_status(self):
        resp = self.client.get(
            "/api/vms/console", params={"id": self.VM_ID}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["running"])

    def test_console_stop(self):
        resp = self._post("/api/vms/console/stop", {"id": self.VM_ID})
        self.assertEqual(resp.status_code, 200)
        self.backend.stop_console.assert_called_once_with(self.VM_ID)


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApiServers(unittest.TestCase):
    """Server management endpoints."""

    ENTRY = {
        "name": "nas", "uri": "qemu+ssh://root@nas/system", "autoconnect": False,
        "connected": False, "vm_count": 0, "error": None,
    }

    def setUp(self):
        self.backend = MagicMock()
        self.backend.config = {"STATS_INTERVAL": 1}
        self.backend.add_server.return_value = self.ENTRY
        self.backend.edit_server.return_value = self.ENTRY
        self.backend.delete_server.return_value = {"deleted": self.ENTRY["uri"]}
        self.backend.connect_server.return_value = dict(self.ENTRY, connected=True)
        self.backend.disconnect_server.return_value = self.ENTRY
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def _send(self, method, url, payload=None, headers=None):
        kwargs = {"headers": self.auth if headers is None else headers}
        if payload is not None:
            kwargs["json"] = payload
        return self.client.request(method, url, **kwargs)

    def test_add_server(self):
        resp = self._send("POST", "/api/servers",
                          {"name": "nas", "uri": "qemu+ssh://root@nas/system"})
        self.assertEqual(resp.status_code, 200)
        self.backend.add_server.assert_called_once_with(
            "nas", "qemu+ssh://root@nas/system", False
        )

    def test_add_server_requires_token(self):
        resp = self._send("POST", "/api/servers",
                          {"name": "nas", "uri": "qemu+ssh://root@nas/system"}, headers={})
        self.assertEqual(resp.status_code, 401)

    def test_add_server_invalid_uri_maps_400(self):
        self.backend.add_server.side_effect = ValueError("Invalid URI")
        resp = self._send("POST", "/api/servers", {"name": "x", "uri": "bad://x"})
        self.assertEqual(resp.status_code, 400)

    def test_add_server_duplicate_maps_409(self):
        self.backend.add_server.side_effect = ConnectionError("Server already configured")
        resp = self._send("POST", "/api/servers",
                          {"name": "nas", "uri": "qemu+ssh://root@nas/system"})
        self.assertEqual(resp.status_code, 409)

    def test_edit_server(self):
        resp = self._send("PUT", "/api/servers", {
            "original_uri": "qemu+ssh://root@nas/system",
            "name": "nas2", "uri": "qemu+ssh://root@nas2/system", "autoconnect": True,
        })
        self.assertEqual(resp.status_code, 200)
        self.backend.edit_server.assert_called_once_with(
            "qemu+ssh://root@nas/system", "nas2", "qemu+ssh://root@nas2/system", True
        )

    def test_delete_server(self):
        resp = self._send(
            "DELETE", "/api/servers?uri=" + urllib.parse.quote(self.ENTRY["uri"], safe="")
        )
        self.assertEqual(resp.status_code, 200)
        self.backend.delete_server.assert_called_once_with(self.ENTRY["uri"])

    def test_delete_server_requires_uri(self):
        resp = self._send("DELETE", "/api/servers")
        self.assertEqual(resp.status_code, 422)

    def test_connect_disconnect(self):
        resp = self._send("POST", "/api/servers/connect", {"uri": self.ENTRY["uri"]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["connected"])
        self.backend.connect_server.assert_called_once_with(self.ENTRY["uri"])

        resp = self._send("POST", "/api/servers/disconnect", {"uri": self.ENTRY["uri"]})
        self.assertEqual(resp.status_code, 200)
        self.backend.disconnect_server.assert_called_once_with(self.ENTRY["uri"])

    def test_connect_failure_maps_400(self):
        self.backend.connect_server.side_effect = ValueError("No route to host")
        resp = self._send("POST", "/api/servers/connect", {"uri": self.ENTRY["uri"]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No route to host", resp.json()["detail"])


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestBackendServerConfig(unittest.TestCase):
    """Backend config read-modify-write logic against a fake config file."""

    def setUp(self):
        from vmanager.web import api as web_api

        self.web_api = web_api
        self.config = {
            "servers": [
                {"name": "Local", "uri": "qemu:///system", "autoconnect": True},
            ]
        }
        self.saved = []

        def fake_load():
            return copy.deepcopy(self.config)

        def fake_save(cfg):
            self.saved.append(cfg)
            self.config["servers"] = cfg["servers"]

        self.load_patcher = unittest.mock.patch.object(web_api, "load_config", fake_load)
        self.save_patcher = unittest.mock.patch.object(web_api, "save_config", fake_save)
        self.load_patcher.start()
        self.save_patcher.start()
        self.addCleanup(self.load_patcher.stop)
        self.addCleanup(self.save_patcher.stop)

        vm_service = MagicMock()
        # No live connections in these tests
        vm_service.connection_manager.get_connection.return_value = None
        vm_service.connection_manager.get_connection_error.return_value = None
        self.backend = web_api.Backend(config=self.config, vm_service=vm_service)
        self.cm = vm_service.connection_manager

    def test_add_server_persists(self):
        entry = self.backend.add_server("nas", "qemu+ssh://root@nas/system", True)
        self.assertEqual(entry["name"], "nas")
        self.assertTrue(entry["autoconnect"])
        self.assertFalse(entry["connected"])
        self.assertEqual(len(self.saved), 1)
        self.assertIn(
            {"name": "nas", "uri": "qemu+ssh://root@nas/system", "autoconnect": True},
            self.config["servers"],
        )
        # adding does not connect
        self.cm.connect.assert_not_called()

    def test_add_server_rejects_invalid_uri(self):
        with self.assertRaises(ValueError):
            self.backend.add_server("x", "http://not-libvirt")
        self.assertEqual(self.saved, [])

    def test_add_server_rejects_duplicate(self):
        with self.assertRaises(ConnectionError):
            self.backend.add_server("dup", "qemu:///system")
        self.assertEqual(self.saved, [])

    def test_edit_server_changes_uri_and_disconnects_old(self):
        entry = self.backend.edit_server("qemu:///system", "Local2", "qemu:///session", False)
        self.assertEqual(entry["uri"], "qemu:///session")
        self.cm.disconnect.assert_called_once_with("qemu:///system")
        self.assertEqual(
            self.config["servers"],
            [{"name": "Local2", "uri": "qemu:///session", "autoconnect": False}],
        )

    def test_edit_server_unknown_404(self):
        with self.assertRaises(LookupError):
            self.backend.edit_server("qemu:///nope", "x", "qemu:///system")

    def test_delete_server_removes_and_disconnects(self):
        self.backend.add_server("nas", "qemu+ssh://root@nas/system")
        result = self.backend.delete_server("qemu+ssh://root@nas/system")
        self.assertEqual(result["deleted"], "qemu+ssh://root@nas/system")
        self.cm.disconnect.assert_called_with("qemu+ssh://root@nas/system")
        self.assertEqual(
            [s["uri"] for s in self.config["servers"]], ["qemu:///system"]
        )

    def test_delete_server_unknown_404(self):
        with self.assertRaises(LookupError):
            self.backend.delete_server("qemu:///nope")

    def test_connect_server_uses_force_retry_and_reports_failure(self):
        # connect goes through VMService.connect so lifecycle events get registered
        self.backend.vm_service.connect.return_value = None
        self.cm.get_connection_error.return_value = "No route to host"
        with self.assertRaises(ValueError) as ctx:
            self.backend.connect_server("qemu:///system")
        self.assertIn("No route to host", str(ctx.exception))
        self.backend.vm_service.connect.assert_called_once_with(
            "qemu:///system", force_retry=True
        )

    def test_connect_unknown_server_404(self):
        with self.assertRaises(LookupError):
            self.backend.connect_server("qemu:///nope")


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApiPrefs(unittest.TestCase):
    """Server preferences endpoints (networks / storage)."""

    URI = "qemu:///system"

    def setUp(self):
        self.backend = MagicMock()
        self.backend.config = {"STATS_INTERVAL": 1}
        self.backend.networks_list.return_value = [
            {"name": "default", "mode": "nat", "active": True, "autostart": True},
        ]
        self.backend.storage_snapshot.return_value = [
            {
                "name": "default", "status": "active", "autostart": True,
                "capacity": 100 * 2**30, "allocation": 10 * 2**30, "error": None,
                "volumes": [
                    {"name": "vm.qcow2", "type": "file", "capacity": 20 * 2**30,
                     "allocation": 5 * 2**30, "path": "/var/lib/libvirt/images/vm.qcow2"},
                ],
            },
        ]
        self.backend.network_set_active.return_value = {"name": "default", "active": False}
        self.backend.network_set_autostart.return_value = {"name": "default", "autostart": False}
        self.backend.network_delete.return_value = {"deleted": "default"}
        self.backend.pool_set_active.return_value = {"pool": "default", "active": False}
        self.backend.pool_set_autostart.return_value = {"pool": "default", "autostart": True}
        self.backend.pool_delete.return_value = {"deleted": "default"}
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def _send(self, method, url, payload=None, headers=None):
        kwargs = {"headers": self.auth if headers is None else headers}
        if payload is not None:
            kwargs["json"] = payload
        return self.client.request(method, url, **kwargs)

    def test_networks_list(self):
        resp = self.client.get(
            "/api/prefs/networks", params={"uri": self.URI}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["name"], "default")
        self.backend.networks_list.assert_called_once_with(self.URI)

    def test_networks_list_requires_token(self):
        resp = self.client.get("/api/prefs/networks", params={"uri": self.URI})
        self.assertEqual(resp.status_code, 401)

    def test_network_toggles(self):
        resp = self._send("POST", "/api/prefs/networks/active",
                          {"uri": self.URI, "name": "default", "active": False})
        self.assertEqual(resp.status_code, 200)
        self.backend.network_set_active.assert_called_once_with(self.URI, "default", False)

        resp = self._send("POST", "/api/prefs/networks/autostart",
                          {"uri": self.URI, "name": "default", "autostart": False})
        self.assertEqual(resp.status_code, 200)
        self.backend.network_set_autostart.assert_called_once_with(self.URI, "default", False)

    def test_network_delete_in_use_maps_409(self):
        self.backend.network_delete.side_effect = ConnectionError(
            "Network 'default' is used by: vm1, vm2"
        )
        resp = self._send("POST", "/api/prefs/networks/delete",
                          {"uri": self.URI, "name": "default"})
        self.assertEqual(resp.status_code, 409)
        self.assertIn("used by", resp.json()["detail"])

    def test_network_xml(self):
        self.backend.network_xml.return_value = {"name": "default", "xml": "<network/>"}
        resp = self.client.get(
            "/api/prefs/networks/xml",
            params={"uri": self.URI, "name": "default"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["xml"], "<network/>")

    def test_storage_snapshot(self):
        resp = self.client.get(
            "/api/prefs/storage", params={"uri": self.URI}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        pools = resp.json()
        self.assertEqual(pools[0]["name"], "default")
        self.assertEqual(pools[0]["volumes"][0]["name"], "vm.qcow2")
        self.backend.storage_snapshot.assert_called_once_with(self.URI)

    def test_pool_operations(self):
        resp = self._send("POST", "/api/prefs/storage/pool/active",
                          {"uri": self.URI, "pool": "default", "active": False})
        self.assertEqual(resp.status_code, 200)
        self.backend.pool_set_active.assert_called_once_with(self.URI, "default", False)

        resp = self._send("POST", "/api/prefs/storage/pool/autostart",
                          {"uri": self.URI, "pool": "default", "autostart": True})
        self.assertEqual(resp.status_code, 200)
        self.backend.pool_set_autostart.assert_called_once_with(self.URI, "default", True)

        resp = self._send("POST", "/api/prefs/storage/pool/delete",
                          {"uri": self.URI, "pool": "default"})
        self.assertEqual(resp.status_code, 200)
        self.backend.pool_delete.assert_called_once_with(self.URI, "default")

    def test_pool_xml(self):
        self.backend.pool_xml.return_value = {"name": "default", "xml": "<pool/>"}
        resp = self.client.get(
            "/api/prefs/storage/pool/xml",
            params={"uri": self.URI, "pool": "default"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["xml"], "<pool/>")

    def test_volume_delete_in_use_maps_409(self):
        self.backend.volume_delete.side_effect = ConnectionError(
            "Volume 'vm.qcow2' is used by: vm1"
        )
        resp = self._send("POST", "/api/prefs/storage/volume/delete",
                          {"uri": self.URI, "pool": "default", "volume": "vm.qcow2"})
        self.assertEqual(resp.status_code, 409)

    def test_volume_delete_and_xml(self):
        self.backend.volume_delete.return_value = {"deleted": "vm.qcow2"}
        resp = self._send("POST", "/api/prefs/storage/volume/delete",
                          {"uri": self.URI, "pool": "default", "volume": "vm.qcow2"})
        self.assertEqual(resp.status_code, 200)
        self.backend.volume_delete.assert_called_once_with(self.URI, "default", "vm.qcow2")

        self.backend.volume_xml.return_value = {"name": "vm.qcow2", "xml": "<volume/>"}
        resp = self.client.get(
            "/api/prefs/storage/volume/xml",
            params={"uri": self.URI, "pool": "default", "volume": "vm.qcow2"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["xml"], "<volume/>")


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApiBulk(unittest.TestCase):
    """Bulk action endpoint."""

    def setUp(self):
        self.backend = MagicMock()
        self.backend.config = {"STATS_INTERVAL": 1}
        self.backend.perform_bulk.return_value = {
            "action": "start", "successful": ["vm1", "vm2"], "failed": ["vm3"], "log": [],
        }
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def test_bulk_success(self):
        ids = ["u1@qemu:///system", "u2@qemu:///system", "u3@qemu:///system"]
        resp = self.client.post(
            "/api/vms/bulk", json={"ids": ids, "action": "start"}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["successful"], ["vm1", "vm2"])
        self.assertEqual(data["failed"], ["vm3"])
        self.backend.perform_bulk.assert_called_once_with(ids, "start")

    def test_bulk_requires_token(self):
        resp = self.client.post("/api/vms/bulk", json={"ids": ["a@b"], "action": "start"})
        self.assertEqual(resp.status_code, 401)

    def test_bulk_bad_action_maps_400(self):
        self.backend.perform_bulk.side_effect = ValueError("Unsupported bulk action: x")
        resp = self.client.post(
            "/api/vms/bulk", json={"ids": ["a@b"], "action": "x"}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 400)


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestBackendBulk(unittest.TestCase):
    """Backend.perform_bulk logic against a mocked VMService."""

    def setUp(self):
        self.vm_service = MagicMock()
        # every server is connected
        self.vm_service.connection_manager.get_connection.return_value = MagicMock()
        self.vm_service.perform_bulk_action.return_value = (["vm1"], ["vm2"])
        self.backend = Backend(config={}, vm_service=self.vm_service)

    def test_shutdown_maps_to_stop_action(self):
        result = self.backend.perform_bulk(
            ["u1@qemu:///system", "u2@qemu+ssh://h/system"], "shutdown"
        )
        self.assertEqual(result["successful"], ["vm1"])
        self.assertEqual(result["failed"], ["vm2"])
        args = self.vm_service.perform_bulk_action.call_args[0]
        self.assertEqual(sorted(args[0]), sorted(["qemu:///system", "qemu+ssh://h/system"]))
        self.assertEqual(args[2], "stop")  # VmAction.STOP
        self.assertEqual(args[3], False)  # delete_storage_flag

    def test_unsupported_action(self):
        with self.assertRaises(ValueError):
            self.backend.perform_bulk(["u1@qemu:///system"], "delete")

    def test_empty_selection(self):
        with self.assertRaises(ValueError):
            self.backend.perform_bulk([], "start")

    def test_malformed_id(self):
        with self.assertRaises(ValueError):
            self.backend.perform_bulk(["no-separator"], "start")

    def test_disconnected_server_maps_connection_error(self):
        self.vm_service.connection_manager.get_connection.return_value = None
        with self.assertRaises(ConnectionError):
            self.backend.perform_bulk(["u1@qemu:///system"], "start")

    def test_progress_log_captured(self):
        def fake_bulk(uris, ids, action, flag, progress):
            progress("setup", total=len(ids))
            progress("log", message="Starting bulk 'start' on 1 VMs...")
            progress("log_error", message="Error performing 'start' on VM 'vm2': boom")
            return (["vm1"], ["vm2"])

        self.vm_service.perform_bulk_action.side_effect = fake_bulk
        result = self.backend.perform_bulk(["u1@qemu:///system"], "start")
        self.assertIn("Starting bulk", result["log"][0])
        self.assertIn("boom", result["log"][1])


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApiEventsWS(unittest.TestCase):
    """Libvirt lifecycle event push over /ws/events."""

    def setUp(self):
        self.vm_service = MagicMock()
        self.backend = Backend(config={"STATS_INTERVAL": 1}, vm_service=self.vm_service)
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)

    def test_events_ws_rejects_bad_token(self):
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect("/ws/events?token=nope") as ws:
                ws.receive_json()
        self.assertEqual(ctx.exception.code, 4401)

    def test_events_ws_pushes_published_events(self):
        import time as _time

        with self.client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
            # Wait until the handler has subscribed its queue
            for _ in range(100):
                if self.backend.event_bus._subscribers:  # noqa: SLF001
                    break
                _time.sleep(0.02)
            self.assertTrue(self.backend.event_bus._subscribers)  # noqa: SLF001

            self.backend._on_data_update()  # noqa: SLF001
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "inventory")

            self.backend._on_vm_update("u1@qemu:///system")  # noqa: SLF001
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "vm")
            self.assertEqual(msg["id"], "u1@qemu:///system")

            self.backend._on_message("info", "VM [b]vm1[/b] on [b]local[/b]: Started")  # noqa: SLF001
            msg = ws.receive_json()
            self.assertEqual(msg["type"], "message")
            self.assertEqual(msg["text"], "VM vm1 on local: Started")

    def test_callbacks_registered_on_vm_service(self):
        self.vm_service.set_vm_update_callback.assert_called_once()
        self.vm_service.set_data_update_callback.assert_called_once()
        self.vm_service.set_message_callback.assert_called_once()


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApiHostStats(unittest.TestCase):
    """Host dashboard stats endpoint."""

    STATS = {
        "uri": "qemu:///system", "server": "Local", "model": "Xeon",
        "mhz": 3000, "sockets": 2, "cores": 8, "threads": 16,
        "total_cpus": 32, "used_cpus": 8, "cpu_percent": 25.0,
        "total_memory_mb": 65536, "used_memory_mb": 16384,
        "free_memory_mb": 40000, "mem_percent": 25.0,
    }

    def setUp(self):
        self.backend = MagicMock()
        self.backend.config = {"STATS_INTERVAL": 1}
        self.backend.host_stats.return_value = dict(self.STATS)
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def test_host_stats(self):
        resp = self.client.get(
            "/api/servers/stats", params={"uri": "qemu:///system"}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["used_cpus"], 8)
        self.assertEqual(data["cpu_percent"], 25.0)
        self.assertEqual(data["total_memory_mb"], 65536)
        self.backend.host_stats.assert_called_once_with("qemu:///system")

    def test_host_stats_requires_token(self):
        resp = self.client.get("/api/servers/stats", params={"uri": "qemu:///system"})
        self.assertEqual(resp.status_code, 401)

    def test_host_stats_disconnected_maps_409(self):
        self.backend.host_stats.side_effect = ConnectionError("Server not connected: x")
        resp = self.client.get(
            "/api/servers/stats", params={"uri": "qemu:///x"}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 409)


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestStatsCollector(unittest.TestCase):
    """Shared collector: scoped collection and per-subscriber filtering."""

    def _run_scenario(self, uris):
        from vmanager.web.api import StatsCollector

        backend = MagicMock()
        backend.stats_snapshot.return_value = {
            "u1@qemu:///system": {"status": "Running"},
            "u2@qemu:///other": {"status": "Running"},
        }
        collector = StatsCollector(backend, interval=60)

        async def scenario():
            sub = collector.subscribe()
            sub.uris = uris
            collector.ensure_running()
            payload = await asyncio.wait_for(sub.queue.get(), timeout=5)
            collector._task.cancel()  # noqa: SLF001
            try:
                await collector._task  # noqa: SLF001
            except asyncio.CancelledError:
                pass
            collector.unsubscribe(sub)
            return json.loads(payload), backend.stats_snapshot.call_args

        return asyncio.run(scenario())

    def test_scoped_collection_and_filtering(self):
        data, call_args = self._run_scenario({"qemu:///system"})
        self.assertIn("u1@qemu:///system", data["stats"])
        self.assertNotIn("u2@qemu:///other", data["stats"])
        self.assertEqual(call_args[0][0], ["qemu:///system"])

    def test_empty_selection_collects_nothing(self):
        data, call_args = self._run_scenario(set())
        self.assertEqual(data["stats"], {})
        self.assertIsNone(call_args)  # backend never hit

    def test_none_selection_collects_all(self):
        data, call_args = self._run_scenario(None)
        self.assertEqual(len(data["stats"]), 2)
        self.assertIsNone(call_args[0][0])  # uris=None -> every server


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestBackendCaching(unittest.TestCase):
    """Lease-IP and host-resource caches; scoped stats collection."""

    @staticmethod
    def _make_backend():
        vm_service = MagicMock()
        return Backend(config={"servers": []}, vm_service=vm_service), vm_service

    def test_ip_cache_avoids_repeated_lookups(self):
        backend, _ = self._make_backend()
        domain = MagicMock()
        domain.state.return_value = (5, 0)  # VIR_DOMAIN_SHUTOFF -> no lease lookup
        self.assertIsNone(backend._cached_first_ipv4("id@uri", domain))  # noqa: SLF001
        self.assertIsNone(backend._cached_first_ipv4("id@uri", domain))  # noqa: SLF001
        domain.state.assert_called_once()  # second call served from cache

    def test_ip_cache_invalidated_on_vm_event(self):
        backend, _ = self._make_backend()
        domain = MagicMock()
        domain.state.return_value = (5, 0)
        backend._cached_first_ipv4("id@uri", domain)  # noqa: SLF001
        backend._on_vm_update("id@uri")  # noqa: SLF001
        backend._cached_first_ipv4("id@uri", domain)  # noqa: SLF001
        self.assertEqual(domain.state.call_count, 2)

    def test_positive_ip_cached(self):
        import libvirt as libvirt_mod

        backend, _ = self._make_backend()
        domain = MagicMock()
        domain.state.return_value = (1, 0)  # VIR_DOMAIN_RUNNING
        domain.interfaceAddresses.return_value = {
            "vnet0": {
                "hwaddr": "52:54:00:12:34:56",
                "addrs": [
                    {"addr": "192.168.122.5", "prefix": 24,
                     "type": libvirt_mod.VIR_IP_ADDR_TYPE_IPV4}
                ],
            }
        }
        ip1 = backend._cached_first_ipv4("id@uri", domain)  # noqa: SLF001
        ip2 = backend._cached_first_ipv4("id@uri", domain)  # noqa: SLF001
        self.assertEqual(ip1, "192.168.122.5")
        self.assertEqual(ip2, "192.168.122.5")
        domain.interfaceAddresses.assert_called_once()  # second served from cache

    def test_stopped_vm_skips_ip_lookup_entirely(self):
        backend, _ = self._make_backend()
        domain = MagicMock()
        # state 5 = VIR_DOMAIN_SHUTOFF: caller passes it, no RPC needed
        self.assertIsNone(backend._cached_first_ipv4("id@uri", domain, state=5))  # noqa: SLF001
        domain.state.assert_not_called()
        domain.interfaceAddresses.assert_not_called()

    def test_info_cache_avoids_repeated_rpc(self):
        backend, _ = self._make_backend()
        domain = MagicMock()
        domain.info.return_value = (1, 4096 * 1024, 2048 * 1024, 4, 0)
        i1 = backend._cached_domain_info("id@uri", domain)  # noqa: SLF001
        i2 = backend._cached_domain_info("id@uri", domain)  # noqa: SLF001
        domain.info.assert_called_once()  # second call served from cache
        self.assertEqual(i1, i2)

    def test_info_cache_invalidated_on_vm_event(self):
        backend, _ = self._make_backend()
        domain = MagicMock()
        domain.info.return_value = (1, 4096 * 1024, 2048 * 1024, 4, 0)
        backend._cached_domain_info("id@uri", domain)  # noqa: SLF001
        backend._on_vm_update("id@uri")  # noqa: SLF001
        backend._cached_domain_info("id@uri", domain)  # noqa: SLF001
        self.assertEqual(domain.info.call_count, 2)

    def test_info_cache_cleared_on_inventory_event(self):
        backend, _ = self._make_backend()
        domain = MagicMock()
        domain.info.return_value = (1, 4096 * 1024, 2048 * 1024, 4, 0)
        backend._cached_domain_info("id@uri", domain)  # noqa: SLF001
        backend._on_data_update()  # noqa: SLF001
        backend._cached_domain_info("id@uri", domain)  # noqa: SLF001
        self.assertEqual(domain.info.call_count, 2)

    def test_host_resources_cached(self):
        backend, vm_service = self._make_backend()
        vm_service.connection_manager.get_connection.return_value = MagicMock()
        host_res = {
            "total_cpus": 8, "available_memory": 16384, "free_memory": 8192,
            "model": "Xeon", "mhz": 3000, "sockets": 1, "cores": 8, "threads": 1,
        }
        alloc = {"active_allocated_vcpus": 2, "active_allocated_memory": 2048}
        with patch("vmanager.web.api.load_config", return_value={"servers": []}), \
             patch("vmanager.web.api.get_host_resources", return_value=host_res) as ghr, \
             patch("vmanager.web.api.get_active_vm_allocation", return_value=alloc):
            s1 = backend.host_stats("qemu:///system")
            s2 = backend.host_stats("qemu:///system")
        ghr.assert_called_once()  # second call served from cache
        self.assertEqual(s1["cpu_percent"], 25.0)
        self.assertEqual(s2["used_cpus"], 2)

    def test_stats_snapshot_filters_uris(self):
        backend, vm_service = self._make_backend()
        cm = vm_service.connection_manager
        conn1, conn2 = MagicMock(), MagicMock()
        cm.get_all_uris.return_value = ["qemu:///one", "qemu:///two"]
        cm.get_connection.side_effect = lambda uri: {
            "qemu:///one": conn1, "qemu:///two": conn2,
        }.get(uri)
        d1, d2 = MagicMock(), MagicMock()
        d1.UUIDString.return_value = "u1"
        d2.UUIDString.return_value = "u2"
        conn1.listAllDomains.return_value = [d1]
        conn2.listAllDomains.return_value = [d2]
        vm_service.get_vm_runtime_stats.side_effect = lambda domain: {"status": "Running"}
        with patch("vmanager.web.api.load_config", return_value={"servers": []}):
            stats = backend.stats_snapshot(uris=["qemu:///one"])
        self.assertIn("u1@qemu:///one", stats)
        self.assertNotIn("u2@qemu:///two", stats)
        conn2.listAllDomains.assert_not_called()


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestBackendGuest(unittest.TestCase):
    """Backend guest-agent logic with patched guest_agent functions."""

    VM_ID = "aaaa@qemu:///system"

    def setUp(self):
        vm_service = MagicMock()
        vm_service.connection_manager.get_connection.return_value = MagicMock()
        self.backend = Backend(config={}, vm_service=vm_service)

    def test_overview_short_circuits_when_agent_unavailable(self):
        with patch("vmanager.web.api.agent_status", return_value="not_running"), \
             patch("vmanager.web.api.ga_info") as gi:
            result = self.backend.guest_overview(self.VM_ID)
        self.assertEqual(result["status"], "not_running")
        self.assertNotIn("info", result)
        gi.assert_not_called()

    def test_overview_running_includes_info_and_fs(self):
        with patch("vmanager.web.api.agent_status", return_value="running"), \
             patch("vmanager.web.api.ga_supported",
                   return_value={"version": "5.2.0", "commands": []}), \
             patch("vmanager.web.api.ga_info", return_value={"hostname": "guest1"}), \
             patch("vmanager.web.api.ga_fsinfo", return_value=[{"mountpoint": "/"}]), \
             patch("vmanager.web.api.ga_fsfreeze_status", return_value="thawed"):
            result = self.backend.guest_overview(self.VM_ID)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["info"]["hostname"], "guest1")
        self.assertEqual(result["fsinfo"], [{"mountpoint": "/"}])
        self.assertEqual(result["fsfreeze"], "thawed")

    def test_overview_survives_unsupported_fsinfo(self):
        from vmanager.guest_agent import GuestAgentError

        with patch("vmanager.web.api.agent_status", return_value="running"), \
             patch("vmanager.web.api.ga_supported", return_value={}), \
             patch("vmanager.web.api.ga_info", return_value={}), \
             patch("vmanager.web.api.ga_fsinfo",
                   side_effect=GuestAgentError("unsupported")), \
             patch("vmanager.web.api.ga_fsfreeze_status",
                   side_effect=GuestAgentError("unsupported")):
            result = self.backend.guest_overview(self.VM_ID)
        self.assertEqual(result["fsinfo"], [])
        self.assertIsNone(result["fsfreeze"])

    def test_password_validation(self):
        with self.assertRaises(ValueError):
            self.backend.guest_set_password(self.VM_ID, "", "pw")
        with self.assertRaises(ValueError):
            self.backend.guest_set_password(self.VM_ID, "root", "")

    def test_exec_clamps_timeout(self):
        with patch("vmanager.web.api.ga_exec_wait",
                   return_value={"exited": True, "exitcode": 0}) as gew:
            self.backend.guest_exec(self.VM_ID, "hostname", timeout=9999)
        self.assertEqual(gew.call_args[1]["overall_timeout"], 300)

    def test_upload_requires_absolute_path(self):
        with self.assertRaises(ValueError):
            self.backend.guest_upload(self.VM_ID, "relative/path", "/tmp/x")


@pytest.mark.skipif(not HAS_WEB_DEPS, reason="fastapi/uvicorn not installed")
class TestWebApiGuest(unittest.TestCase):
    """Guest-agent endpoints."""

    VM_ID = "aaaa-bbbb@qemu:///system"

    def setUp(self):
        self.backend = MagicMock()
        self.backend.config = {"STATS_INTERVAL": 1}
        self.backend.guest_overview.return_value = {
            "status": "running", "supported": {"version": "5.2.0"},
            "info": {}, "fsinfo": [], "fsfreeze": "thawed",
        }
        self.backend.guest_action.return_value = {"action": "trim", "trimmed": 1024}
        self.backend.guest_set_password.return_value = {"username": "root"}
        self.backend.guest_exec.return_value = {
            "exited": True, "exitcode": 0, "signal": None,
            "stdout": "guest1\n", "stderr": "", "truncated": False,
        }
        self.backend.guest_ssh_keys.return_value = {"user": "root", "keys": ["ssh-rsa AAA"]}
        self.backend.guest_ssh_add_keys.return_value = {"user": "root", "added": 1}
        self.backend.guest_ssh_remove_keys.return_value = {"user": "root", "removed": 1}
        self.backend.guest_upload.return_value = {"path": "/root/test.txt", "bytes": 5}
        self.app = create_app(self.backend, TOKEN, stats_interval=1)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def _post(self, url, payload=None, headers=None):
        return self.client.post(
            url, json=payload, headers=self.auth if headers is None else headers
        )

    def test_guest_overview(self):
        resp = self.client.get(
            "/api/vms/guest", params={"id": self.VM_ID}, headers=self.auth
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "running")
        self.backend.guest_overview.assert_called_once_with(self.VM_ID)

        resp = self.client.get("/api/vms/guest", params={"id": self.VM_ID})
        self.assertEqual(resp.status_code, 401)

    def test_guest_action_dispatch(self):
        resp = self._post("/api/vms/guest/action", {"id": self.VM_ID, "action": "trim"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["trimmed"], 1024)
        self.backend.guest_action.assert_called_once_with(self.VM_ID, "trim")

    def test_guest_action_errors(self):
        self.backend.guest_action.side_effect = ValueError("Unsupported guest action: x")
        resp = self._post("/api/vms/guest/action", {"id": self.VM_ID, "action": "x"})
        self.assertEqual(resp.status_code, 400)

    def test_guest_agent_error_maps_400(self):
        from vmanager.guest_agent import GuestAgentError

        self.backend.guest_action.side_effect = GuestAgentError("agent is not responding")
        resp = self._post("/api/vms/guest/action", {"id": self.VM_ID, "action": "freeze"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not responding", resp.json()["detail"])

    def test_guest_password_does_not_echo_secret(self):
        resp = self._post("/api/vms/guest/password",
                          {"id": self.VM_ID, "username": "root", "password": "s3cret"})
        self.assertEqual(resp.status_code, 200)
        self.backend.guest_set_password.assert_called_once_with(
            self.VM_ID, "root", "s3cret", False
        )
        self.assertNotIn("s3cret", resp.text)

    def test_guest_exec(self):
        resp = self._post("/api/vms/guest/exec", {"id": self.VM_ID, "command": "hostname"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["stdout"], "guest1\n")
        self.backend.guest_exec.assert_called_once_with(self.VM_ID, "hostname", 30)

    def test_guest_ssh_keys(self):
        resp = self.client.get(
            "/api/vms/guest/ssh_keys",
            params={"id": self.VM_ID, "user": "root"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["keys"], ["ssh-rsa AAA"])

        resp = self._post("/api/vms/guest/ssh_keys",
                          {"id": self.VM_ID, "user": "root", "keys": ["ssh-ed25519 BBB"]})
        self.assertEqual(resp.status_code, 200)
        self.backend.guest_ssh_add_keys.assert_called_once_with(
            self.VM_ID, "root", ["ssh-ed25519 BBB"]
        )

        resp = self._post("/api/vms/guest/ssh_keys/delete",
                          {"id": self.VM_ID, "user": "root", "keys": ["ssh-ed25519 BBB"]})
        self.assertEqual(resp.status_code, 200)
        self.backend.guest_ssh_remove_keys.assert_called_once_with(
            self.VM_ID, "root", ["ssh-ed25519 BBB"]
        )

    def test_guest_upload_multipart(self):
        resp = self.client.post(
            "/api/vms/guest/upload",
            data={"id": self.VM_ID, "path": "/root/test.txt"},
            files={"file": ("test.txt", b"hello", "text/plain")},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        args = self.backend.guest_upload.call_args[0]
        self.assertEqual(args[0], self.VM_ID)
        self.assertEqual(args[1], "/root/test.txt")
        self.assertFalse(os.path.exists(args[2]))  # temp file cleaned up

    def test_guest_upload_requires_token(self):
        resp = self.client.post(
            "/api/vms/guest/upload",
            data={"id": self.VM_ID, "path": "/root/x"},
            files={"file": ("x", b"x", "text/plain")},
        )
        self.assertEqual(resp.status_code, 401)

    def test_guest_download_streams_file(self):
        def fake_download(iid, path, tmp):
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write("guest-content")
            return {"path": path, "bytes": 13}

        self.backend.guest_download.side_effect = fake_download
        resp = self.client.get(
            "/api/vms/guest/download",
            params={"id": self.VM_ID, "path": "/etc/hostname"},
            headers=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"guest-content")
        self.assertIn("hostname", resp.headers.get("content-disposition", ""))


if __name__ == "__main__":
    unittest.main()
