# Test suite for main vmanager.py application
import unittest
from unittest.mock import MagicMock, patch, call
import sys
import os

# Add the source directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vmanager.vmanager import VMManagerTUI, WorkerManager


class TestVMManager(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.app = MagicMock()
        self.worker_manager = WorkerManager(self.app)

    def test_worker_manager_init(self):
        """Test WorkerManager initialization."""
        self.assertIsInstance(self.worker_manager, WorkerManager)
        self.assertEqual(self.worker_manager.app, self.app)
        self.assertEqual(len(self.worker_manager.workers), 0)

    def test_worker_manager_run_with_exclusive(self):
        """Test run method with exclusive=True."""
        # Mock a callable function
        mock_callable = MagicMock()

        # Test that it runs without exceptions
        worker = self.worker_manager.run(callable=mock_callable, name="test_worker", exclusive=True)

        # Should return a worker object (or None if already running)
        # This is a basic test to make sure the method doesn't crash
        self.assertTrue(True)  # Just verify no exceptions

    def test_worker_manager_is_running(self):
        """Test is_running method."""
        # Should not crash
        result = self.worker_manager.is_running("nonexistent_worker")
        self.assertIsInstance(result, bool)

    def test_worker_manager_cancel(self):
        """Test cancel method."""
        # Should not crash
        worker = self.worker_manager.cancel("nonexistent_worker")
        self.assertIsNone(worker)  # Should return None for non-existent worker

    def test_worker_manager_cancel_all(self):
        """Test cancel_all method."""
        # Should not crash
        self.worker_manager.cancel_all()
        self.assertTrue(True)

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def test_vm_manager_tui_init(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Test VMManagerTUI initialization."""
        # Setup mocks
        mock_load_config.return_value = {}
        mock_vm_service_instance = MagicMock()
        mock_vm_service.return_value = mock_vm_service_instance

        # Create the app
        app = VMManagerTUI()

        # Verify initialization
        self.assertIsInstance(app, VMManagerTUI)
        self.assertIsNotNone(app.config)
        self.assertTrue(hasattr(app, "vm_service"))
        self.assertTrue(hasattr(app, "worker_manager"))

    @patch("vmanager.vmanager.load_config")
    def test_vm_manager_tui_get_initial_active_uris(self, mock_load_config):
        """Test _get_initial_active_uris method."""
        mock_load_config.return_value = {
            "servers": [
                {"uri": "qemu+tcp://localhost/system", "autoconnect": True},
                {"uri": "qemu+ssh://user@host/system", "autoconnect": False},
            ]
        }

        servers_list = [
            {"uri": "qemu+tcp://localhost/system", "autoconnect": True},
            {"uri": "qemu+ssh://user@host/system", "autoconnect": False},
        ]

        # Test the static method
        result = VMManagerTUI._get_initial_active_uris(servers_list)
        self.assertEqual(result, ["qemu+tcp://localhost/system"])

    def test_vm_manager_tui_get_initial_active_uris_empty(self):
        """Test _get_initial_active_uris with empty servers list."""
        result = VMManagerTUI._get_initial_active_uris([])
        self.assertEqual(result, [])

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    def test_vm_manager_tui_get_active_uris_reactive(self, mock_setup_logging, mock_load_config):
        """Test that active_uris is reactive."""
        mock_load_config.return_value = {"servers": []}

        app = VMManagerTUI()
        self.assertTrue(hasattr(app, "active_uris"))

    def test_vm_manager_tui_constants(self):
        """Test that VMManagerTUI has expected constants."""
        self.assertTrue(hasattr(VMManagerTUI, "BINDINGS"))
        self.assertTrue(hasattr(VMManagerTUI, "VMS_PER_PAGE"))
        self.assertTrue(hasattr(VMManagerTUI, "CSS_PATH"))


class TestVMManagerIntegration(unittest.TestCase):
    """Integration tests for VMManager components."""

    def test_worker_manager_structure(self):
        """Test that WorkerManager has all expected methods."""
        worker_manager = WorkerManager(MagicMock())

        expected_methods = [
            "run",
            "is_running",
            "cancel",
            "cancel_all",
            "_cleanup_finished_workers",
        ]

        for method in expected_methods:
            self.assertTrue(
                hasattr(worker_manager, method), f"WorkerManager missing method: {method}"
            )

    def test_vm_manager_structure(self):
        """Test that VMManagerTUI has all expected methods."""
        app = VMManagerTUI()

        expected_methods = [
            "on_unmount",
            "on_service_message",
            "on_vm_data_update",
            "on_vm_update",
            "watch_bulk_operation_in_progress",
            "get_server_color",
            "compose",
            "on_mount",
            "action_compact_view",
            "action_select_server",
            "handle_select_server_result",
            "remove_active_uri",
            "connect_libvirt",
            "show_error_message",
            "show_success_message",
            "show_quick_message",
            "show_in_progress_message",
            "show_warning_message",
        ]

        for method in expected_methods:
            # Just verify the method exists without calling
            self.assertTrue(hasattr(app, method), f"VMManagerTUI missing method: {method}")


if __name__ == "__main__":
    unittest.main()
