import builtins
import os
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
