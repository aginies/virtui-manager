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


if __name__ == "__main__":
    unittest.main()
