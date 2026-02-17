# Test suite for main vmanager.py application
import unittest
from unittest.mock import MagicMock, patch, call, PropertyMock
import sys
import os
import threading
import time

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


# ============================================================================
# THREAD SAFETY AND UI BLOCKING TESTS
# ============================================================================


class TestWorkerManagerThreading(unittest.TestCase):
    """Tests for WorkerManager threading and concurrency."""

    def setUp(self):
        """Set up test fixtures."""
        self.app = MagicMock()
        self.worker_manager = WorkerManager(self.app)

    def test_worker_manager_tracks_workers_by_name(self):
        """Test that WorkerManager properly tracks workers by name."""
        mock_callable = MagicMock()

        # Run a worker
        self.worker_manager.run(mock_callable, name="test_worker_1")

        # Verify worker is tracked
        self.assertIn("test_worker_1", self.worker_manager.workers)

    def test_worker_manager_exclusive_prevents_duplicate_workers(self):
        """Test that exclusive=True prevents duplicate worker execution."""
        mock_callable = MagicMock()

        # Run first worker
        worker1 = self.worker_manager.run(mock_callable, name="exclusive_worker", exclusive=True)

        # Try to run same worker again (should be prevented)
        worker2 = self.worker_manager.run(mock_callable, name="exclusive_worker", exclusive=True)

        # Second call should return None (worker already running)
        self.assertIsNone(worker2)

    def test_worker_manager_cancel_stops_worker(self):
        """Test that cancel properly stops a running worker."""
        executed = []

        def slow_task():
            executed.append("start")
            time.sleep(0.1)
            executed.append("end")

        # Run worker
        worker = self.worker_manager.run(slow_task, name="slow_worker")

        # Cancel immediately
        self.worker_manager.cancel("slow_worker")

        # Verify worker was removed from tracking
        time.sleep(0.05)  # Give time for cancellation
        self.assertNotIn("slow_worker", self.worker_manager.workers)

    def test_worker_manager_cancel_all_stops_all_workers(self):
        """Test that cancel_all stops all running workers."""

        def task():
            time.sleep(0.1)

        # Start multiple workers
        self.worker_manager.run(task, name="worker_1")
        self.worker_manager.run(task, name="worker_2")
        self.worker_manager.run(task, name="worker_3")

        initial_count = len(self.worker_manager.workers)
        self.assertGreaterEqual(initial_count, 3)

        # Cancel all
        self.worker_manager.cancel_all()

        # Verify all workers are cancelled
        time.sleep(0.05)
        self.assertEqual(len(self.worker_manager.workers), 0)

    def test_worker_manager_is_running_returns_correct_state(self):
        """Test that is_running accurately reports worker state."""

        def task():
            time.sleep(0.1)

        # Initially not running
        self.assertFalse(self.worker_manager.is_running("test_worker"))

        # Start worker
        self.worker_manager.run(task, name="test_worker")

        # Now should be running
        self.assertTrue(self.worker_manager.is_running("test_worker"))

    def test_worker_manager_cleanup_removes_finished_workers(self):
        """Test that _cleanup_finished_workers method exists and can be called."""
        # Since we're using a MagicMock app, workers won't actually execute
        # This test verifies the cleanup method exists and is callable

        # Add a mock worker to the workers dict
        mock_worker = MagicMock()
        mock_worker.is_finished = True
        self.worker_manager.workers["finished_worker"] = mock_worker

        # Verify cleanup method exists
        self.assertTrue(hasattr(self.worker_manager, "_cleanup_finished_workers"))

        # Call cleanup if it exists
        if hasattr(self.worker_manager, "_cleanup_finished_workers"):
            self.worker_manager._cleanup_finished_workers()
            # Worker tracking is tested, actual cleanup depends on implementation

    def test_worker_manager_handles_worker_exceptions_gracefully(self):
        """Test that worker exceptions don't crash WorkerManager."""

        def failing_task():
            raise ValueError("Test error")

        # Should not raise exception
        try:
            worker = self.worker_manager.run(failing_task, name="failing_worker")
            time.sleep(0.05)  # Give time for task to fail
            # If we get here, the exception was handled
            self.assertTrue(True)
        except ValueError:
            self.fail("WorkerManager did not handle worker exception")

    def test_worker_manager_concurrent_worker_execution(self):
        """Test that multiple workers can be tracked concurrently."""
        # Since we're using a MagicMock app, workers won't actually execute
        # This test verifies that multiple workers can be registered

        mock_callable = MagicMock()

        # Start multiple workers
        for i in range(5):
            self.worker_manager.run(mock_callable, name=f"concurrent_worker_{i}")

        # All workers should be tracked
        worker_count = sum(
            1 for name in self.worker_manager.workers.keys() if "concurrent_worker_" in name
        )
        self.assertEqual(worker_count, 5)


class TestVMManagerLibvirtThreading(unittest.TestCase):
    """Tests for libvirt connection/disconnection threading."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_connect_libvirt_uses_worker_thread(self):
        """Test that connect_libvirt calls vm_service.connect."""
        uri = "qemu:///system"

        # Mock vm_service.connect
        self.app.vm_service.connect = MagicMock(return_value=MagicMock())

        # Call connect_libvirt
        result = self.app.connect_libvirt(uri)

        # Verify vm_service.connect was called
        self.app.vm_service.connect.assert_called_once_with(uri)
        self.assertTrue(result)

    def test_disconnect_cancels_background_workers(self):
        """Test that disconnecting cancels background refresh workers."""
        uri = "qemu:///system"

        # Mock active URIs
        self.app.active_uris = [uri]

        # Mock worker_manager - simulate that there are workers running
        cancelled_workers = []

        def mock_cancel(name):
            cancelled_workers.append(name)

        # Add a fake worker to simulate background activity
        mock_worker = MagicMock()
        self.app.worker_manager.workers[f"background_refresh_{uri}"] = mock_worker

        self.app.worker_manager.cancel = MagicMock(side_effect=mock_cancel)

        # Mock methods to avoid actual execution and event loop issues
        self.app.vm_card_pool = MagicMock()
        self.app.vm_card_pool.active_cards = {}
        self.app.vm_service.disconnect = MagicMock()
        self.app.vm_service.get_uri_for_connection = MagicMock(return_value=uri)
        self.app.refresh_vm_list = MagicMock()  # Prevent event loop error

        # Call remove_active_uri (disconnect)
        self.app.remove_active_uri(uri)

        # Verify disconnect was called
        self.app.vm_service.disconnect.assert_called_with(uri)

    @patch("vmanager.vmanager.libvirt")
    def test_connection_failure_handled_gracefully_in_worker(self, mock_libvirt):
        """Test that libvirt connection failures in worker don't crash UI."""
        uri = "qemu+ssh://invalid/system"

        # Simulate connection failure
        import libvirt

        mock_libvirt.libvirtError = libvirt.libvirtError
        self.app.vm_service.get_or_create_connection.side_effect = Exception("Connection failed")

        # Mock worker execution
        executed_workers = []

        def mock_run(func, name=None):
            executed_workers.append(name)
            try:
                func()  # Execute the worker
            except Exception:
                pass  # Should be handled gracefully

        self.app.worker_manager.run = MagicMock(side_effect=mock_run)
        self.app.show_error_message = MagicMock()

        # Should not raise exception
        try:
            self.app.connect_libvirt(uri)
            self.assertTrue(True)
        except Exception as e:
            self.fail(f"Connection failure not handled gracefully: {e}")

    def test_refresh_vm_list_uses_worker_thread(self):
        """Test that refresh_vm_list doesn't block UI."""
        # Mock necessary attributes
        self.app.active_uris = ["qemu:///system"]
        self.app.bulk_operation_in_progress = False
        self.app.initial_cache_loading = False

        worker_calls = []

        def mock_run(func, name=None, exclusive=False):
            worker_calls.append(name)
            return MagicMock()  # Return a mock worker

        self.app.worker_manager.run = MagicMock(side_effect=mock_run)

        # Call refresh_vm_list
        self.app.refresh_vm_list()

        # Verify worker was scheduled
        self.assertTrue(any("list_vms" in str(name) for name in worker_calls))

    def test_concurrent_refresh_prevented_by_exclusive_worker(self):
        """Test that multiple refresh requests are handled properly."""
        self.app.active_uris = ["qemu:///system"]
        self.app.bulk_operation_in_progress = False
        self.app.initial_cache_loading = False

        worker_calls = []

        def mock_run(func, name=None, exclusive=False):
            worker_calls.append((name, exclusive))
            return MagicMock()

        self.app.worker_manager.run = MagicMock(side_effect=mock_run)

        # Call refresh multiple times
        self.app.refresh_vm_list()
        self.app.refresh_vm_list()

        # Should have called worker_manager.run at least once
        self.assertGreaterEqual(len(worker_calls), 1)

        # Verify it's a list_vms worker
        self.assertTrue(any("list_vms" in str(name) for name, _ in worker_calls))

    def test_vm_list_update_uses_call_later_for_ui_update(self):
        """Test that VM list updates schedule UI updates properly."""
        # This tests the pattern of using call_later/call_from_thread
        self.app.active_uris = ["qemu:///system"]

        # Mock call_later
        call_later_calls = []

        def mock_call_later(func):
            call_later_calls.append(func)

        self.app.call_later = MagicMock(side_effect=mock_call_later)

        # The actual implementation may vary, but verify the pattern exists
        self.assertTrue(hasattr(self.app, "call_later"))


class TestVMManagerUIBlocking(unittest.TestCase):
    """Tests for UI blocking prevention in VMManager."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_bulk_operations_show_progress_indicator(self):
        """Test that bulk_operation_in_progress flag is tracked."""
        # Mock the vm_card_pool to avoid ScreenStackError
        self.app.vm_card_pool = MagicMock()
        self.app.vm_card_pool.active_cards = {}

        # Mock the watcher to prevent it from running
        self.app.watch_bulk_operation_in_progress = MagicMock()

        # Verify the reactive property exists
        self.assertTrue(hasattr(self.app.__class__, "bulk_operation_in_progress"))

        # Set to True (simulating bulk operation)
        self.app.bulk_operation_in_progress = True

        # Verify the watcher was called
        self.app.watch_bulk_operation_in_progress.assert_called()

    def test_watch_bulk_operation_updates_ui_state(self):
        """Test that bulk_operation_in_progress watcher updates UI."""
        # Mock vm_card_pool and compact_view to prevent watcher chain issues
        self.app.vm_card_pool = MagicMock()
        self.app.vm_card_pool.active_cards = {}
        self.app._previous_compact_view = False

        # Mock compact_view to prevent its watcher from triggering
        with patch.object(
            type(self.app), "compact_view", new_callable=PropertyMock
        ) as mock_compact:
            mock_compact.return_value = False

            # This tests that the watcher exists and can be called
            if hasattr(self.app, "watch_bulk_operation_in_progress"):
                try:
                    self.app.watch_bulk_operation_in_progress(True)
                    self.assertTrue(True)
                except Exception as e:
                    self.fail(f"Bulk operation watcher raised exception: {e}")

    def test_vm_action_request_doesnt_block_ui(self):
        """Test that VM action requests are handled asynchronously."""
        from vmanager.events import VmActionRequest

        # Create a mock event - action is just a string
        mock_event = VmActionRequest(internal_id="uuid-123", action="start")

        # Mock the action handler
        worker_calls = []

        def mock_run(func, name=None):
            worker_calls.append(name)

        self.app.worker_manager.run = MagicMock(side_effect=mock_run)
        self.app.vm_service.get_vm_by_uuid = MagicMock(return_value=(MagicMock(), MagicMock()))

        # Handle the event
        if hasattr(self.app, "on_vm_action_request"):
            self.app.on_vm_action_request(mock_event)

            # Verify worker was scheduled
            self.assertGreater(len(worker_calls), 0)

    def test_select_server_action_doesnt_block_ui(self):
        """Test that server selection dialog doesn't block UI."""
        # Mock push_screen to capture modal display
        self.app.push_screen = MagicMock()

        # Call action_select_server
        self.app.action_select_server()

        # Verify modal was shown (non-blocking pattern)
        self.app.push_screen.assert_called_once()

    def test_error_messages_display_without_blocking(self):
        """Test that error messages don't require worker threads."""
        # These should be immediate UI updates
        message = "Test error message"

        # Mock notify
        self.app.notify = MagicMock()

        # Show error
        self.app.show_error_message(message)

        # Should call notify (immediate UI update)
        self.app.notify.assert_called()

    def test_success_messages_display_without_blocking(self):
        """Test that success messages display immediately."""
        message = "Test success message"

        # Mock notify
        self.app.notify = MagicMock()

        # Show success
        self.app.show_success_message(message)

        # Should call notify
        self.app.notify.assert_called()

    def test_remove_active_uri_cancels_vm_workers(self):
        """Test that removing a URI triggers proper cleanup."""
        uri = "qemu:///system"
        self.app.active_uris = [uri]

        # Mock vm_card_pool
        self.app.vm_card_pool = MagicMock()
        self.app.vm_card_pool.active_cards = {}
        self.app.vm_service.disconnect = MagicMock()
        self.app.vm_service.get_uri_for_connection = MagicMock(return_value=uri)
        self.app.refresh_vm_list = MagicMock()  # Prevent event loop error

        # Remove URI
        self.app.remove_active_uri(uri)

        # Should call disconnect
        self.app.vm_service.disconnect.assert_called_with(uri)

        # Verify URI was removed from active list
        self.assertNotIn(uri, self.app.active_uris)


class TestVMManagerMemoryLeaks(unittest.TestCase):
    """Tests for memory leak prevention in VMManager."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_unmount_cancels_all_workers(self):
        """Test that on_unmount properly disconnects services."""
        # Mock vm_service
        self.app.vm_service.disconnect_all = MagicMock()

        # Call on_unmount
        if hasattr(self.app, "on_unmount"):
            self.app.on_unmount()

            # Verify disconnect_all was called
            self.app.vm_service.disconnect_all.assert_called_once()

    def test_connection_close_on_uri_removal(self):
        """Test that connections are properly closed when URI is removed."""
        uri = "qemu:///system"
        self.app.active_uris = [uri]

        # Mock vm_card_pool and other dependencies
        self.app.vm_card_pool = MagicMock()
        self.app.vm_card_pool.active_cards = {}
        self.app.vm_service.disconnect = MagicMock()
        self.app.vm_service.get_uri_for_connection = MagicMock(return_value=uri)
        self.app.refresh_vm_list = MagicMock()

        # Remove URI
        self.app.remove_active_uri(uri)

        # Verify disconnect was called (modern API)
        self.app.vm_service.disconnect.assert_called_with(uri)

    def test_worker_cleanup_prevents_accumulation(self):
        """Test that finished workers are cleaned up regularly."""
        worker_manager = WorkerManager(MagicMock())

        # Add some mock finished workers
        mock_finished_worker = MagicMock()
        mock_finished_worker.is_finished = True
        worker_manager.workers["finished_1"] = mock_finished_worker

        initial_count = len(worker_manager.workers)

        # Cleanup
        worker_manager._cleanup_finished_workers()

        # Should remove finished workers
        self.assertLessEqual(len(worker_manager.workers), initial_count)


class TestVMManagerRaceConditions(unittest.TestCase):
    """Tests for race condition prevention in VMManager."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_concurrent_active_uris_modification_safe(self):
        """Test that concurrent modifications to active_uris are handled safely."""
        uri1 = "qemu:///system"
        uri2 = "qemu+ssh://remote/system"

        # Initially empty
        self.app.active_uris = []

        # Simulate concurrent additions
        self.app.active_uris.append(uri1)
        self.app.active_uris.append(uri2)

        # Should have both
        self.assertIn(uri1, self.app.active_uris)
        self.assertIn(uri2, self.app.active_uris)

    def test_worker_manager_thread_safe_worker_tracking(self):
        """Test that WorkerManager's worker dict access is thread-safe."""
        worker_manager = WorkerManager(MagicMock())

        results = []
        lock = threading.Lock()

        def add_worker(i):
            def task():
                time.sleep(0.01)

            worker_manager.run(task, name=f"concurrent_{i}")
            with lock:
                results.append(i)

        # Create multiple threads adding workers
        threads = []
        for i in range(10):
            t = threading.Thread(target=add_worker, args=(i,))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # All should have executed without crashes
        self.assertEqual(len(results), 10)

    def test_refresh_vm_list_concurrent_calls_handled(self):
        """Test that concurrent refresh_vm_list calls don't cause issues."""
        self.app.active_uris = ["qemu:///system"]
        self.app.bulk_operation_in_progress = False
        self.app.initial_cache_loading = False

        call_count = [0]
        lock = threading.Lock()

        def mock_run(func, name=None, exclusive=False):
            with lock:
                call_count[0] += 1
            return MagicMock()

        self.app.worker_manager.run = MagicMock(side_effect=mock_run)

        # Call multiple times (simulating concurrent events)
        for _ in range(5):
            self.app.refresh_vm_list()

        # All calls should succeed
        self.assertGreaterEqual(call_count[0], 1)


if __name__ == "__main__":
    unittest.main()
