import builtins
import copy
import os
import sys
import unittest
import urllib.parse
from unittest.mock import MagicMock

import pytest

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# Mock translation function globally for tests
builtins._ = lambda s: s

try:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from vmanager.web.api import create_app

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
        self.cm.connect.return_value = None
        self.cm.get_connection_error.return_value = "No route to host"
        with self.assertRaises(ValueError) as ctx:
            self.backend.connect_server("qemu:///system")
        self.assertIn("No route to host", str(ctx.exception))
        self.cm.connect.assert_called_once_with("qemu:///system", force_retry=True)

    def test_connect_unknown_server_404(self):
        with self.assertRaises(LookupError):
            self.backend.connect_server("qemu:///nope")


if __name__ == "__main__":
    unittest.main()
