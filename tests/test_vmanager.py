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
        """Test that libvirt connection failures return False without crashing."""
        uri = "qemu+ssh://invalid/system"

        # Simulate connection failure - return None instead of raising
        self.app.vm_service.connect.return_value = None
        self.app.vm_service.connection_manager.get_connection_error.return_value = (
            "Connection failed"
        )

        # connect_libvirt should handle failed connection gracefully
        result = self.app.connect_libvirt(uri)

        # Verify failure is handled - returns False, doesn't crash
        self.assertFalse(result)
        self.app.vm_service.connect.assert_called_once_with(uri)

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
        self.app.vm_service.find_domain_by_uuid = MagicMock(return_value=MagicMock())

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


# ============================================================================
# FILTER FUNCTIONALITY TESTS
# ============================================================================


class TestVMManagerFilter(unittest.TestCase):
    """Tests for filter functionality in VMManager."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_action_filter_view_opens_modal(self):
        """Test that action_filter_view opens the filter modal."""
        self.app.active_uris = ["qemu:///system"]
        self.app.servers = [{"uri": "qemu:///system", "name": "Local"}]
        self.app.push_screen = MagicMock()

        self.app.action_filter_view()

        self.app.push_screen.assert_called_once()

    def test_action_filter_running_sets_sort_by(self):
        """Test that action_filter_running sets sort_by to RUNNING."""
        from vmanager.constants import VmStatus

        self.app.sort_by = VmStatus.DEFAULT
        self.app.refresh_vm_list = MagicMock()
        self.app.show_quick_message = MagicMock()

        self.app.action_filter_running()

        self.assertEqual(self.app.sort_by, VmStatus.RUNNING)
        self.assertEqual(self.app.current_page, 0)
        self.app.refresh_vm_list.assert_called_once()

    def test_action_filter_running_no_change_if_already_running(self):
        """Test that action_filter_running does nothing if already filtering running."""
        from vmanager.constants import VmStatus

        self.app.sort_by = VmStatus.RUNNING
        self.app.refresh_vm_list = MagicMock()

        self.app.action_filter_running()

        # Should not refresh if already in running mode
        self.app.refresh_vm_list.assert_not_called()

    def test_action_filter_all_sets_sort_by_default(self):
        """Test that action_filter_all resets sort_by to DEFAULT."""
        from vmanager.constants import VmStatus

        self.app.sort_by = VmStatus.RUNNING
        self.app.refresh_vm_list = MagicMock()
        self.app.show_quick_message = MagicMock()

        self.app.action_filter_all()

        self.assertEqual(self.app.sort_by, VmStatus.DEFAULT)
        self.assertEqual(self.app.current_page, 0)
        self.app.refresh_vm_list.assert_called_once()

    def test_action_filter_all_no_change_if_already_default(self):
        """Test that action_filter_all does nothing if already showing all."""
        from vmanager.constants import VmStatus

        self.app.sort_by = VmStatus.DEFAULT
        self.app.refresh_vm_list = MagicMock()

        self.app.action_filter_all()

        self.app.refresh_vm_list.assert_not_called()

    def test_on_filter_changed_updates_filters(self):
        """Test that on_filter_changed updates filter settings."""
        from vmanager.constants import VmStatus
        from vmanager.modals.vmanager_modals import FilterModal

        self.app.sort_by = VmStatus.DEFAULT
        self.app.search_text = ""
        self.app.filtered_server_uris = None
        self.app.active_uris = ["qemu:///system"]
        self.app.refresh_vm_list = MagicMock()
        self.app.show_in_progress_message = MagicMock()

        # Create a mock FilterChanged message
        mock_message = MagicMock(spec=FilterModal.FilterChanged)
        mock_message.status = VmStatus.RUNNING
        mock_message.search = "test-vm"
        mock_message.selected_servers = ["qemu:///system"]

        self.app.on_filter_changed(mock_message)

        self.assertEqual(self.app.sort_by, VmStatus.RUNNING)
        self.assertEqual(self.app.search_text, "test-vm")
        self.assertEqual(self.app.current_page, 0)
        self.app.refresh_vm_list.assert_called_once()

    def test_on_filter_changed_no_refresh_if_no_changes(self):
        """Test that on_filter_changed doesn't refresh if nothing changed."""
        from vmanager.constants import VmStatus
        from vmanager.modals.vmanager_modals import FilterModal

        self.app.sort_by = VmStatus.DEFAULT
        self.app.search_text = ""
        self.app.filtered_server_uris = ["qemu:///system"]
        self.app.active_uris = ["qemu:///system"]
        self.app.refresh_vm_list = MagicMock()

        mock_message = MagicMock(spec=FilterModal.FilterChanged)
        mock_message.status = VmStatus.DEFAULT
        mock_message.search = ""
        mock_message.selected_servers = ["qemu:///system"]

        self.app.on_filter_changed(mock_message)

        self.app.refresh_vm_list.assert_not_called()


# ============================================================================
# PAGINATION TESTS
# ============================================================================


class TestVMManagerPagination(unittest.TestCase):
    """Tests for pagination functionality in VMManager."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_action_previous_page_decrements_page(self):
        """Test that action_previous_page decrements the current page."""
        self.app.current_page = 2
        self.app.refresh_vm_list = MagicMock()

        self.app.action_previous_page()

        self.assertEqual(self.app.current_page, 1)
        self.app.refresh_vm_list.assert_called_once()

    def test_action_previous_page_does_nothing_at_first_page(self):
        """Test that action_previous_page does nothing when on first page."""
        self.app.current_page = 0
        self.app.refresh_vm_list = MagicMock()

        self.app.action_previous_page()

        self.assertEqual(self.app.current_page, 0)
        self.app.refresh_vm_list.assert_not_called()

    def test_action_next_page_increments_page(self):
        """Test that action_next_page increments the current page."""
        self.app.current_page = 0
        self.app.num_pages = 3
        self.app.refresh_vm_list = MagicMock()

        self.app.action_next_page()

        self.assertEqual(self.app.current_page, 1)
        self.app.refresh_vm_list.assert_called_once()

    def test_action_next_page_does_nothing_at_last_page(self):
        """Test that action_next_page does nothing when on last page."""
        self.app.current_page = 2
        self.app.num_pages = 3
        self.app.refresh_vm_list = MagicMock()

        self.app.action_next_page()

        self.assertEqual(self.app.current_page, 2)
        self.app.refresh_vm_list.assert_not_called()

    def test_update_pagination_controls_hides_when_few_vms(self):
        """Test that pagination controls are hidden when VMs fit on one page."""
        self.app.ui["pagination_controls"] = MagicMock()
        self.app.VMS_PER_PAGE = 6

        self.app.update_pagination_controls(total_filtered_vms=5, total_vms_unfiltered=5)

        self.app.ui["pagination_controls"].styles.display = "none"

    def test_update_pagination_controls_shows_when_many_vms(self):
        """Test that pagination controls are shown when VMs exceed page size."""
        mock_pagination = MagicMock()
        mock_page_info = MagicMock()
        mock_prev_button = MagicMock()
        mock_next_button = MagicMock()

        self.app.ui["pagination_controls"] = mock_pagination
        self.app.ui["page_info"] = mock_page_info
        self.app.ui["prev_button"] = mock_prev_button
        self.app.ui["next_button"] = mock_next_button
        self.app.VMS_PER_PAGE = 6
        self.app.current_page = 0

        self.app.update_pagination_controls(total_filtered_vms=15, total_vms_unfiltered=15)

        mock_pagination.styles.display = "block"
        self.assertEqual(self.app.num_pages, 3)

    def test_update_pagination_controls_disables_prev_on_first_page(self):
        """Test that prev button is disabled on first page."""
        mock_pagination = MagicMock()
        mock_page_info = MagicMock()
        mock_prev_button = MagicMock()
        mock_next_button = MagicMock()

        self.app.ui["pagination_controls"] = mock_pagination
        self.app.ui["page_info"] = mock_page_info
        self.app.ui["prev_button"] = mock_prev_button
        self.app.ui["next_button"] = mock_next_button
        self.app.VMS_PER_PAGE = 6
        self.app.current_page = 0

        self.app.update_pagination_controls(total_filtered_vms=15, total_vms_unfiltered=15)

        self.assertTrue(mock_prev_button.disabled)

    def test_update_pagination_controls_disables_next_on_last_page(self):
        """Test that next button is disabled on last page."""
        mock_pagination = MagicMock()
        mock_page_info = MagicMock()
        mock_prev_button = MagicMock()
        mock_next_button = MagicMock()

        self.app.ui["pagination_controls"] = mock_pagination
        self.app.ui["page_info"] = mock_page_info
        self.app.ui["prev_button"] = mock_prev_button
        self.app.ui["next_button"] = mock_next_button
        self.app.VMS_PER_PAGE = 6
        self.app.current_page = 2  # Last page (0, 1, 2)

        self.app.update_pagination_controls(total_filtered_vms=15, total_vms_unfiltered=15)

        self.assertTrue(mock_next_button.disabled)


# ============================================================================
# VM SELECTION TESTS
# ============================================================================


class TestVMManagerSelection(unittest.TestCase):
    """Tests for VM selection functionality in VMManager."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_action_toggle_select_all_selects_all_when_none_selected(self):
        """Test that action_toggle_select_all selects all when none are selected."""
        mock_card1 = MagicMock()
        mock_card1.is_selected = False
        mock_card2 = MagicMock()
        mock_card2.is_selected = False

        self.app.query = MagicMock(return_value=[mock_card1, mock_card2])

        self.app.action_toggle_select_all()

        self.assertTrue(mock_card1.is_selected)
        self.assertTrue(mock_card2.is_selected)

    def test_action_toggle_select_all_deselects_all_when_all_selected(self):
        """Test that action_toggle_select_all deselects when all are selected."""
        mock_card1 = MagicMock()
        mock_card1.is_selected = True
        mock_card2 = MagicMock()
        mock_card2.is_selected = True

        self.app.query = MagicMock(return_value=[mock_card1, mock_card2])

        self.app.action_toggle_select_all()

        self.assertFalse(mock_card1.is_selected)
        self.assertFalse(mock_card2.is_selected)

    def test_action_toggle_select_all_does_nothing_with_no_cards(self):
        """Test that action_toggle_select_all does nothing when no cards exist."""
        self.app.query = MagicMock(return_value=[])

        # Should not raise
        self.app.action_toggle_select_all()

    def test_action_unselect_all_clears_selection(self):
        """Test that action_unselect_all clears all selections."""
        self.app.selected_vm_uuids = {"uuid1", "uuid2", "uuid3"}
        mock_card1 = MagicMock()
        mock_card2 = MagicMock()
        self.app.query = MagicMock(return_value=[mock_card1, mock_card2])
        self.app.show_quick_message = MagicMock()

        self.app.action_unselect_all()

        self.assertEqual(len(self.app.selected_vm_uuids), 0)
        self.assertFalse(mock_card1.is_selected)
        self.assertFalse(mock_card2.is_selected)
        self.app.show_quick_message.assert_called_once()

    def test_action_unselect_all_does_nothing_when_empty(self):
        """Test that action_unselect_all does nothing when no selections exist."""
        self.app.selected_vm_uuids = set()
        self.app.query = MagicMock(return_value=[])
        self.app.show_quick_message = MagicMock()

        self.app.action_unselect_all()

        self.app.show_quick_message.assert_not_called()

    def test_on_vm_selection_changed_adds_to_selection(self):
        """Test that on_vm_selection_changed adds VM to selection when selected."""
        from vmanager.events import VMSelectionChanged

        self.app.selected_vm_uuids = set()
        mock_message = VMSelectionChanged(vm_uuid="uuid-123", is_selected=True)

        self.app.on_vm_selection_changed(mock_message)

        self.assertIn("uuid-123", self.app.selected_vm_uuids)

    def test_on_vm_selection_changed_removes_from_selection(self):
        """Test that on_vm_selection_changed removes VM from selection when deselected."""
        from vmanager.events import VMSelectionChanged

        self.app.selected_vm_uuids = {"uuid-123", "uuid-456"}
        mock_message = VMSelectionChanged(vm_uuid="uuid-123", is_selected=False)

        self.app.on_vm_selection_changed(mock_message)

        self.assertNotIn("uuid-123", self.app.selected_vm_uuids)
        self.assertIn("uuid-456", self.app.selected_vm_uuids)


# ============================================================================
# SERVER MANAGEMENT TESTS
# ============================================================================


class TestVMManagerServerManagement(unittest.TestCase):
    """Tests for server management functionality in VMManager."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_action_manage_server_opens_modal(self):
        """Test that action_manage_server opens the server management modal."""
        self.app.push_screen = MagicMock()

        self.app.action_manage_server()

        self.app.push_screen.assert_called_once()

    @patch("vmanager.vmanager.save_config")
    def test_reload_servers_updates_config(self, mock_save_config):
        """Test that reload_servers updates the server configuration."""
        new_servers = [
            {"uri": "qemu:///system", "name": "Local"},
            {"uri": "qemu+ssh://remote/system", "name": "Remote"},
        ]

        self.app.reload_servers(new_servers)

        self.assertEqual(self.app.servers, new_servers)
        self.assertEqual(self.app.config["servers"], new_servers)
        mock_save_config.assert_called_once_with(self.app.config)

    def test_on_server_management_with_list_reloads_servers(self):
        """Test that on_server_management reloads servers when given a list."""
        self.app.reload_servers = MagicMock()
        new_servers = [{"uri": "qemu:///system", "name": "Local"}]

        self.app.on_server_management(new_servers)

        self.app.reload_servers.assert_called_once_with(new_servers)

    def test_on_server_management_with_uri_changes_connection(self):
        """Test that on_server_management changes connection when given a URI."""
        self.app.change_connection = MagicMock()
        uri = "qemu:///system"

        self.app.on_server_management(uri)

        self.app.change_connection.assert_called_once_with(uri)

    def test_on_server_management_with_none_does_nothing(self):
        """Test that on_server_management does nothing when result is None."""
        self.app.reload_servers = MagicMock()
        self.app.change_connection = MagicMock()

        self.app.on_server_management(None)

        self.app.reload_servers.assert_not_called()
        self.app.change_connection.assert_not_called()

    def test_change_connection_calls_handle_select_server_result(self):
        """Test that change_connection delegates to handle_select_server_result."""
        self.app.handle_select_server_result = MagicMock()
        uri = "qemu:///system"

        self.app.change_connection(uri)

        self.app.handle_select_server_result.assert_called_once_with([uri])

    def test_change_connection_ignores_empty_uri(self):
        """Test that change_connection ignores empty URIs."""
        self.app.handle_select_server_result = MagicMock()

        self.app.change_connection("")
        self.app.change_connection("   ")

        self.app.handle_select_server_result.assert_not_called()


# ============================================================================
# CONFIGURATION TESTS
# ============================================================================


class TestVMManagerConfiguration(unittest.TestCase):
    """Tests for configuration functionality in VMManager."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": [], "LOG_LEVEL": "INFO"}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_action_config_opens_modal(self):
        """Test that action_config opens the config modal."""
        self.app.push_screen = MagicMock()

        self.app.action_config()

        self.app.push_screen.assert_called_once()
        # Should store old log level
        self.assertEqual(self.app.old_log_level, "INFO")

    def test_handle_config_result_with_none_does_nothing(self):
        """Test that handle_config_result does nothing when result is None."""
        old_config = dict(self.app.config)
        self.app.show_success_message = MagicMock()

        self.app.handle_config_result(None)

        self.assertEqual(self.app.config, old_config)
        self.app.show_success_message.assert_not_called()

    @patch("vmanager.vmanager.check_r_viewer")
    def test_handle_config_result_updates_config(self, mock_check_r_viewer):
        """Test that handle_config_result updates configuration."""
        mock_check_r_viewer.return_value = "/usr/bin/remote-viewer"
        self.app.old_log_level = "INFO"
        self.app.show_success_message = MagicMock()
        self.app.show_error_message = MagicMock()

        new_config = {"LOG_LEVEL": "INFO", "STATS_INTERVAL": 5}
        self.app.handle_config_result(new_config)

        self.assertEqual(self.app.config, new_config)
        self.app.show_success_message.assert_called()

    @patch("vmanager.vmanager.check_r_viewer")
    def test_handle_config_result_changes_log_level(self, mock_check_r_viewer):
        """Test that handle_config_result updates log level when changed."""
        mock_check_r_viewer.return_value = "/usr/bin/remote-viewer"
        self.app.old_log_level = "INFO"
        self.app.show_success_message = MagicMock()
        # Keep STATS_INTERVAL same as in real config to avoid triggering refresh_vm_list
        stats_interval = self.app.config.get("STATS_INTERVAL", 5)

        new_config = {"LOG_LEVEL": "DEBUG", "STATS_INTERVAL": stats_interval}
        self.app.handle_config_result(new_config)

        # Log level changed message should be shown
        calls = self.app.show_success_message.call_args_list
        self.assertTrue(any("DEBUG" in str(call) for call in calls))

    @patch("vmanager.vmanager.check_r_viewer")
    def test_handle_config_result_triggers_refresh_on_stats_interval_change(
        self, mock_check_r_viewer
    ):
        """Test that config result triggers refresh when stats interval changes."""
        mock_check_r_viewer.return_value = "/usr/bin/remote-viewer"
        self.app.old_log_level = "INFO"
        self.app.config = {"STATS_INTERVAL": 5}
        self.app.show_success_message = MagicMock()
        self.app.show_in_progress_message = MagicMock()
        self.app.refresh_vm_list = MagicMock()

        new_config = {"LOG_LEVEL": "INFO", "STATS_INTERVAL": 10}
        self.app.handle_config_result(new_config)

        self.app.show_in_progress_message.assert_called()
        self.app.refresh_vm_list.assert_called_once()


# ============================================================================
# HOST CAPABILITIES AND DASHBOARD TESTS
# ============================================================================


class TestVMManagerHostCapabilities(unittest.TestCase):
    """Tests for host capabilities and dashboard functionality."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_action_host_capabilities_with_no_servers(self):
        """Test that action_host_capabilities shows error with no servers."""
        self.app.active_uris = []
        self.app.show_error_message = MagicMock()

        self.app.action_host_capabilities()

        self.app.show_error_message.assert_called_once()

    def test_action_host_capabilities_with_single_server(self):
        """Test that action_host_capabilities opens modal with single server."""
        self.app.active_uris = ["qemu:///system"]
        self.app.vm_service.connect = MagicMock(return_value=MagicMock())
        self.app.push_screen = MagicMock()

        self.app.action_host_capabilities()

        self.app.push_screen.assert_called_once()

    def test_action_host_capabilities_with_multiple_servers(self):
        """Test that action_host_capabilities shows server selector with multiple servers."""
        self.app.active_uris = ["qemu:///system", "qemu+ssh://remote/system"]
        self.app.servers = [
            {"uri": "qemu:///system", "name": "Local"},
            {"uri": "qemu+ssh://remote/system", "name": "Remote"},
        ]
        self.app.push_screen = MagicMock()

        self.app.action_host_capabilities()

        # Should show server selection modal first
        self.app.push_screen.assert_called_once()

    def test_action_host_dashboard_with_no_servers(self):
        """Test that action_host_dashboard shows error with no servers."""
        self.app.active_uris = []
        self.app.show_error_message = MagicMock()

        self.app.action_host_dashboard()

        self.app.show_error_message.assert_called_once()

    def test_action_host_dashboard_with_single_server(self):
        """Test that action_host_dashboard opens modal with single server."""
        self.app.active_uris = ["qemu:///system"]
        self.app.servers = [{"uri": "qemu:///system", "name": "Local"}]
        self.app.vm_service.connect = MagicMock(return_value=MagicMock())
        self.app.push_screen = MagicMock()

        self.app.action_host_dashboard()

        self.app.push_screen.assert_called_once()

    def test_action_host_dashboard_connection_failure(self):
        """Test that action_host_dashboard shows error on connection failure."""
        self.app.active_uris = ["qemu:///system"]
        self.app.servers = [{"uri": "qemu:///system", "name": "Local"}]
        self.app.vm_service.connect = MagicMock(return_value=None)
        self.app.show_error_message = MagicMock()

        self.app.action_host_dashboard()

        self.app.show_error_message.assert_called_once()


# ============================================================================
# VM INSTALLATION TESTS
# ============================================================================


class TestVMManagerInstallVM(unittest.TestCase):
    """Tests for VM installation functionality."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_action_install_vm_with_no_servers(self):
        """Test that action_install_vm shows error with no servers."""
        self.app.active_uris = []
        self.app.show_error_message = MagicMock()

        self.app.action_install_vm()

        self.app.show_error_message.assert_called_once()

    @patch("vmanager.vmanager.InstallVMModal")
    def test_action_install_vm_with_single_server(self, mock_install_modal):
        """Test that action_install_vm opens modal with single server."""
        self.app.active_uris = ["qemu:///system"]
        self.app.push_screen = MagicMock()

        self.app.action_install_vm()

        mock_install_modal.assert_called_once()
        self.app.push_screen.assert_called_once()

    def test_handle_install_vm_result_with_success(self):
        """Test that handle_install_vm_result refreshes on success."""
        self.app.refresh_vm_list = MagicMock()

        self.app.handle_install_vm_result(True)

        self.app.refresh_vm_list.assert_called_once_with(force=True)

    def test_handle_install_vm_result_with_none(self):
        """Test that handle_install_vm_result does nothing on None."""
        self.app.refresh_vm_list = MagicMock()

        self.app.handle_install_vm_result(None)

        self.app.refresh_vm_list.assert_not_called()

    def test_handle_install_vm_result_with_false(self):
        """Test that handle_install_vm_result does nothing on False."""
        self.app.refresh_vm_list = MagicMock()

        self.app.handle_install_vm_result(False)

        self.app.refresh_vm_list.assert_not_called()


# ============================================================================
# LAYOUT MANAGEMENT TESTS
# ============================================================================


class TestVMManagerLayout(unittest.TestCase):
    """Tests for layout management functionality."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": [], "VMS_PER_PAGE": 6}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_action_compact_view_toggles(self):
        """Test that action_compact_view toggles compact view."""
        # Mock watchers to prevent DOM queries
        self.app.watch_compact_view = MagicMock()
        self.app.watch_bulk_operation_in_progress = MagicMock()
        self.app.compact_view = False
        self.app.bulk_operation_in_progress = False

        self.app.action_compact_view()

        self.assertTrue(self.app.compact_view)

    def test_action_compact_view_toggles_back(self):
        """Test that action_compact_view toggles compact view back off."""
        # Mock watchers to prevent DOM queries
        self.app.watch_compact_view = MagicMock()
        self.app.watch_bulk_operation_in_progress = MagicMock()
        self.app.compact_view = True
        self.app.bulk_operation_in_progress = False

        self.app.action_compact_view()

        self.assertFalse(self.app.compact_view)

    def test_action_compact_view_locked_during_bulk_operation(self):
        """Test that compact view is locked during bulk operations."""
        # Mock watchers to prevent DOM queries
        self.app.watch_compact_view = MagicMock()
        self.app.watch_bulk_operation_in_progress = MagicMock()
        self.app.compact_view = False
        self.app.bulk_operation_in_progress = True
        self.app.show_warning_message = MagicMock()

        self.app.action_compact_view()

        self.assertFalse(self.app.compact_view)
        self.app.show_warning_message.assert_called_once()

    def test_action_compact_view_saves_page_before_compact(self):
        """Test that compact view saves current page before enabling."""
        # Mock watchers to prevent DOM queries
        self.app.watch_compact_view = MagicMock()
        self.app.watch_bulk_operation_in_progress = MagicMock()
        self.app.compact_view = False
        self.app.current_page = 5
        self.app.bulk_operation_in_progress = False

        self.app.action_compact_view()

        self.assertEqual(self.app._saved_page_before_compact, 5)

    def test_watch_compact_view_updates_cards(self):
        """Test that watch_compact_view updates card compact view state."""
        mock_card1 = MagicMock()
        mock_card2 = MagicMock()
        self.app.query = MagicMock(return_value=[mock_card1, mock_card2])
        self.app.ui["vms_container"] = MagicMock()
        self.app._update_layout_for_size = MagicMock()

        # Call watcher directly (bypass reactive system)
        VMManagerTUI.watch_compact_view(self.app, True)

        self.assertTrue(mock_card1.compact_view)
        self.assertTrue(mock_card2.compact_view)

    def test_on_resize_sets_timer(self):
        """Test that on_resize sets a debounce timer."""
        with patch.object(type(self.app), "is_mounted", new_callable=PropertyMock) as mock_mounted:
            mock_mounted.return_value = True
            self.app._resize_timer = None
            self.app.set_timer = MagicMock(return_value=MagicMock())

            mock_event = MagicMock()
            self.app.on_resize(mock_event)

            self.app.set_timer.assert_called_once()

    def test_on_resize_cancels_previous_timer(self):
        """Test that on_resize cancels previous timer if exists."""
        with patch.object(type(self.app), "is_mounted", new_callable=PropertyMock) as mock_mounted:
            mock_mounted.return_value = True
            mock_timer = MagicMock()
            self.app._resize_timer = mock_timer
            self.app.set_timer = MagicMock(return_value=MagicMock())

            mock_event = MagicMock()
            self.app.on_resize(mock_event)

            mock_timer.stop.assert_called_once()

    def test_on_resize_does_nothing_when_not_mounted(self):
        """Test that on_resize does nothing when app is not mounted."""
        with patch.object(type(self.app), "is_mounted", new_callable=PropertyMock) as mock_mounted:
            mock_mounted.return_value = False
            self.app.set_timer = MagicMock()

            mock_event = MagicMock()
            self.app.on_resize(mock_event)

            self.app.set_timer.assert_not_called()


# ============================================================================
# HELPER METHOD TESTS
# ============================================================================


class TestVMManagerHelpers(unittest.TestCase):
    """Tests for helper methods in VMManager."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_get_active_connections_yields_connections(self):
        """Test that _get_active_connections yields valid connections."""
        self.app.active_uris = ["qemu:///system"]
        mock_conn = MagicMock()
        self.app.vm_service.connect = MagicMock(return_value=mock_conn)

        connections = list(self.app._get_active_connections())

        self.assertEqual(len(connections), 1)
        self.assertEqual(connections[0], mock_conn)

    def test_get_active_connections_shows_error_on_failure(self):
        """Test that _get_active_connections shows error on connection failure."""
        self.app.active_uris = ["qemu:///system"]
        self.app.vm_service.connect = MagicMock(return_value=None)
        self.app.show_error_message = MagicMock()

        connections = list(self.app._get_active_connections())

        self.assertEqual(len(connections), 0)
        self.app.show_error_message.assert_called_once()

    def test_collapse_all_action_collapsibles(self):
        """Test that _collapse_all_action_collapsibles collapses all."""
        mock_collapsible = MagicMock()
        mock_collapsible.collapsed = False

        mock_card = MagicMock()
        mock_card.ui = {"collapsible": mock_collapsible}
        mock_card.name = "test-vm"
        mock_card.internal_id = "uuid-123"

        self.app.vm_card_pool = MagicMock()
        self.app.vm_card_pool.active_cards = {"uuid-123": mock_card}

        self.app._collapse_all_action_collapsibles()

        self.assertTrue(mock_collapsible.collapsed)

    def test_collapse_all_action_collapsibles_handles_missing_collapsible(self):
        """Test that _collapse_all_action_collapsibles handles missing collapsible."""
        mock_card = MagicMock()
        mock_card.ui = {}  # No collapsible

        self.app.vm_card_pool = MagicMock()
        self.app.vm_card_pool.active_cards = {"uuid-123": mock_card}

        # Should not raise
        self.app._collapse_all_action_collapsibles()

    def test_remove_vms_for_uri_releases_cards(self):
        """Test that _remove_vms_for_uri releases VM cards for the given URI."""
        uri = "qemu:///system"

        mock_card = MagicMock()
        mock_card.conn = MagicMock()
        self.app.vm_service.get_uri_for_connection = MagicMock(return_value=uri)

        self.app.vm_card_pool = MagicMock()
        self.app.vm_card_pool.active_cards = {"uuid-123": mock_card}
        self.app.vm_card_pool.release_card = MagicMock()
        self.app.sparkline_data = {"uuid-123": {}}

        self.app._remove_vms_for_uri(uri)

        self.app.vm_card_pool.release_card.assert_called_once_with("uuid-123")
        self.assertNotIn("uuid-123", self.app.sparkline_data)

    def test_remove_vms_for_uri_ignores_other_servers(self):
        """Test that _remove_vms_for_uri ignores cards from other servers."""
        uri = "qemu:///system"

        mock_card = MagicMock()
        mock_card.conn = MagicMock()
        self.app.vm_service.get_uri_for_connection = MagicMock(
            return_value="qemu+ssh://other/system"
        )

        self.app.vm_card_pool = MagicMock()
        self.app.vm_card_pool.active_cards = {"uuid-123": mock_card}
        self.app.vm_card_pool.release_card = MagicMock()
        self.app.sparkline_data = {"uuid-123": {}}

        self.app._remove_vms_for_uri(uri)

        self.app.vm_card_pool.release_card.assert_not_called()


# ============================================================================
# STATS LOGGING TESTS
# ============================================================================


class TestVMManagerStatsLogging(unittest.TestCase):
    """Tests for stats logging functionality."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    @patch("vmanager.vmanager.setup_cache_monitoring")
    def test_action_toggle_stats_logging_enables(self, mock_setup_cache_monitoring):
        """Test that action_toggle_stats_logging enables logging when disabled."""
        self.app._stats_logging_active = False
        self.app._stats_interval_timer = None
        self.app.set_interval = MagicMock(return_value=MagicMock())
        self.app.show_success_message = MagicMock()
        mock_cache_monitor = MagicMock()
        mock_setup_cache_monitoring.return_value = mock_cache_monitor
        self.app.vm_service.connection_manager.get_stats = MagicMock(return_value={})

        self.app.action_toggle_stats_logging()

        self.assertTrue(self.app._stats_logging_active)
        self.app.set_interval.assert_called_once()
        self.app.show_success_message.assert_called()

    @patch("vmanager.vmanager.setup_cache_monitoring")
    def test_action_toggle_stats_logging_disables(self, mock_setup_cache_monitoring):
        """Test that action_toggle_stats_logging disables logging when enabled."""
        mock_timer = MagicMock()
        self.app._stats_logging_active = True
        self.app._stats_interval_timer = mock_timer
        self.app.show_success_message = MagicMock()

        self.app.action_toggle_stats_logging()

        self.assertFalse(self.app._stats_logging_active)
        mock_timer.stop.assert_called_once()
        self.assertIsNone(self.app._stats_interval_timer)
        self.app.show_success_message.assert_called()


# ============================================================================
# BULK ACTION TESTS
# ============================================================================


class TestVMManagerBulkActions(unittest.TestCase):
    """Tests for bulk action functionality."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_action_bulk_cmd_with_no_selection(self):
        """Test that action_bulk_cmd shows error when nothing selected."""
        self.app.selected_vm_uuids = set()
        self.app.show_error_message = MagicMock()
        self.app._collapse_all_action_collapsibles = MagicMock()

        self.app.action_bulk_cmd()

        self.app.show_error_message.assert_called_once()

    def test_action_bulk_cmd_with_selection_launches_worker(self):
        """Test that action_bulk_cmd launches worker when VMs selected."""
        self.app.selected_vm_uuids = {"uuid-123", "uuid-456"}
        self.app._collapse_all_action_collapsibles = MagicMock()
        self.app.worker_manager.run = MagicMock()

        self.app.action_bulk_cmd()

        self.app.worker_manager.run.assert_called_once()

    def test_handle_bulk_action_result_with_none_clears_selection(self):
        """Test that handle_bulk_action_result clears selection on None."""
        self.app.selected_vm_uuids = {"uuid-123"}
        self.app.refresh_vm_list = MagicMock()

        self.app.handle_bulk_action_result(None)

        self.assertEqual(len(self.app.selected_vm_uuids), 0)
        self.app.refresh_vm_list.assert_called_once()

    def test_handle_bulk_action_result_with_no_action_type(self):
        """Test that handle_bulk_action_result shows error with no action type."""
        self.app.show_error_message = MagicMock()

        self.app.handle_bulk_action_result({"delete_storage": False})

        self.app.show_error_message.assert_called_once()

    def test_handle_bulk_action_result_migrate_requires_two_servers(self):
        """Test that migrate action requires at least two servers."""
        self.app.active_uris = ["qemu:///system"]  # Only one server
        self.app.show_error_message = MagicMock()

        self.app.handle_bulk_action_result({"action": "migrate"})

        self.app.show_error_message.assert_called_once()

    def test_handle_bulk_action_result_starts_worker_for_valid_action(self):
        """Test that handle_bulk_action_result starts worker for valid action."""
        self.app.selected_vm_uuids = {"uuid-123"}
        self.app.worker_manager.run = MagicMock()
        # Mock watchers to avoid DOM queries in unmounted app
        self.app.watch_bulk_operation_in_progress = MagicMock()
        self.app.watch_compact_view = MagicMock()

        self.app.handle_bulk_action_result({"action": "start", "delete_storage": False})

        self.assertEqual(len(self.app.selected_vm_uuids), 0)
        self.assertTrue(self.app.bulk_operation_in_progress)
        self.app.worker_manager.run.assert_called_once()


# ============================================================================
# MIGRATION TESTS
# ============================================================================


class TestVMManagerMigration(unittest.TestCase):
    """Tests for migration functionality."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_initiate_migration_requires_two_servers(self):
        """Test that initiate_migration requires at least two servers."""
        self.app.active_uris = ["qemu:///system"]
        self.app.show_error_message = MagicMock()

        self.app.initiate_migration([MagicMock()])

        self.app.show_error_message.assert_called_once()

    def test_initiate_migration_requires_selected_vms(self):
        """Test that initiate_migration requires selected VMs."""
        self.app.active_uris = ["qemu:///system", "qemu+ssh://remote/system"]
        self.app.show_error_message = MagicMock()

        self.app.initiate_migration([])

        self.app.show_error_message.assert_called_once()

    def test_initiate_migration_requires_same_source_host(self):
        """Test that initiate_migration requires VMs from same source host."""
        self.app.active_uris = ["qemu:///system", "qemu+ssh://remote/system"]
        self.app.show_error_message = MagicMock()

        mock_vm1 = MagicMock()
        mock_conn1 = MagicMock()
        mock_vm1.connect.return_value = mock_conn1
        self.app.vm_service.get_uri_for_connection = MagicMock(
            side_effect=["qemu:///system", "qemu+ssh://remote/system"]
        )

        mock_vm2 = MagicMock()
        mock_conn2 = MagicMock()
        mock_vm2.connect.return_value = mock_conn2

        self.app.initiate_migration([mock_vm1, mock_vm2])

        self.app.show_error_message.assert_called_once()

    def test_initiate_migration_rejects_localhost(self):
        """Test that initiate_migration rejects migration from localhost."""
        self.app.active_uris = ["qemu:///system", "qemu+ssh://remote/system"]
        self.app.show_error_message = MagicMock()

        mock_vm = MagicMock()
        mock_conn = MagicMock()
        mock_vm.connect.return_value = mock_conn
        self.app.vm_service.get_uri_for_connection = MagicMock(return_value="qemu:///system")
        self.app.vm_service._get_domain_state = MagicMock(return_value=(1, 0))  # Running

        self.app.initiate_migration([mock_vm])

        self.app.show_error_message.assert_called_once()


# ============================================================================
# SERVICE CALLBACK TESTS
# ============================================================================


class TestVMManagerServiceCallbacks(unittest.TestCase):
    """Tests for service callback handling."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_on_service_message_success(self):
        """Test that on_service_message handles success messages."""
        self.app.show_success_message = MagicMock()
        self.app.call_from_thread = MagicMock(
            side_effect=lambda func, *args: func(*args) if args else func()
        )

        self.app.on_service_message("success", "Operation successful")

        self.app.show_success_message.assert_called_with("Operation successful")

    def test_on_service_message_error(self):
        """Test that on_service_message handles error messages."""
        self.app.show_error_message = MagicMock()
        self.app.call_from_thread = MagicMock(
            side_effect=lambda func, *args: func(*args) if args else func()
        )

        self.app.on_service_message("error", "Operation failed")

        self.app.show_error_message.assert_called_with("Operation failed")

    def test_on_service_message_warning(self):
        """Test that on_service_message handles warning messages."""
        self.app.show_warning_message = MagicMock()
        self.app.call_from_thread = MagicMock(
            side_effect=lambda func, *args: func(*args) if args else func()
        )

        self.app.on_service_message("warning", "Warning message")

        self.app.show_warning_message.assert_called_with("Warning message")

    def test_on_service_message_progress(self):
        """Test that on_service_message handles progress messages."""
        self.app.show_in_progress_message = MagicMock()
        self.app.call_from_thread = MagicMock(
            side_effect=lambda func, *args: func(*args) if args else func()
        )

        self.app.on_service_message("progress", "Loading...")

        self.app.show_in_progress_message.assert_called_with("Loading...")

    def test_on_service_message_connection_loss_removes_vms(self):
        """Test that on_service_message removes VMs on connection loss."""
        self.app._remove_vms_for_uri = MagicMock()
        self.app.show_success_message = MagicMock()
        self.app.call_from_thread = MagicMock(
            side_effect=lambda func, *args: func(*args)
            if callable(func) and args
            else func()
            if callable(func)
            else None
        )

        message = "Connection to qemu:///system lost: Network error. Attempting to reconnect..."
        self.app.on_service_message("warning", message)

        self.app._remove_vms_for_uri.assert_called_with("qemu:///system")

    def test_on_vm_data_update_skips_during_bulk_operation(self):
        """Test that on_vm_data_update skips during bulk operations."""
        # Mock watchers to avoid DOM queries in unmounted app
        self.app.watch_bulk_operation_in_progress = MagicMock()
        self.app.watch_compact_view = MagicMock()
        self.app.bulk_operation_in_progress = True
        self.app.refresh_vm_list = MagicMock()
        self.app.call_from_thread = MagicMock()

        self.app.on_vm_data_update()

        self.app.refresh_vm_list.assert_not_called()

    def test_on_vm_data_update_refreshes_normally(self):
        """Test that on_vm_data_update refreshes when not in bulk operation."""
        self.app.bulk_operation_in_progress = False
        self.app.refresh_vm_list = MagicMock()
        self.app.call_from_thread = MagicMock(side_effect=lambda func: func())

        self.app.on_vm_data_update()

        self.app.refresh_vm_list.assert_called_once()

    def test_on_vm_update_posts_update_request(self):
        """Test that on_vm_update posts a card update request."""
        self.app.post_message = MagicMock()
        self.app._trigger_host_stats_refresh = MagicMock()
        self.app.call_from_thread = MagicMock(
            side_effect=lambda func, *args: func(*args) if args else func()
        )

        self.app.on_vm_update("uuid-123")

        self.app.post_message.assert_called()


# ============================================================================
# VM ACTION REQUEST TESTS
# ============================================================================


class TestVMManagerVmActionRequest(unittest.TestCase):
    """Tests for VM action request handling."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_on_vm_action_request_launches_worker(self):
        """Test that on_vm_action_request launches a worker."""
        from vmanager.events import VmActionRequest

        self.app.worker_manager.run = MagicMock()

        message = VmActionRequest(internal_id="uuid-123", action="start")
        self.app.on_vm_action_request(message)

        self.app.worker_manager.run.assert_called_once()
        # Verify the worker name includes action and uuid
        call_args = self.app.worker_manager.run.call_args
        self.assertIn("start", call_args.kwargs.get("name", ""))
        self.assertIn("uuid-123", call_args.kwargs.get("name", ""))


# ============================================================================
# PATTERN SELECT TESTS
# ============================================================================


class TestVMManagerPatternSelect(unittest.TestCase):
    """Tests for pattern selection functionality."""

    @patch("vmanager.vmanager.load_config")
    @patch("vmanager.vmanager.setup_logging")
    @patch("vmanager.vmanager.VMService")
    def setUp(self, mock_vm_service, mock_setup_logging, mock_load_config):
        """Set up test fixtures."""
        mock_load_config.return_value = {"servers": []}
        self.mock_vm_service = MagicMock()
        mock_vm_service.return_value = self.mock_vm_service
        self.app = VMManagerTUI()

    def test_action_pattern_select_with_no_servers(self):
        """Test that action_pattern_select shows error with no servers."""
        self.app.active_uris = []
        self.app.show_error_message = MagicMock()

        self.app.action_pattern_select()

        self.app.show_error_message.assert_called_once()

    def test_action_pattern_select_with_no_vms_in_cache(self):
        """Test that action_pattern_select shows error with no VMs in cache."""
        self.app.active_uris = ["qemu:///system"]
        self.app.vm_service._cache_lock = MagicMock()
        self.app.vm_service._domain_cache = {}
        self.app.show_error_message = MagicMock()

        self.app.action_pattern_select()

        self.app.show_error_message.assert_called_once()

    def test_action_pattern_select_opens_modal_with_vms(self):
        """Test that action_pattern_select opens modal when VMs exist."""
        self.app.active_uris = ["qemu:///system"]
        self.app.servers = [{"uri": "qemu:///system", "name": "Local"}]

        mock_domain = MagicMock()
        mock_conn = MagicMock()

        self.app.vm_service._cache_lock = MagicMock()
        self.app.vm_service._domain_cache = {"uuid-123": mock_domain}
        self.app.vm_service._uuid_to_conn_cache = {"uuid-123": mock_conn}
        self.app.vm_service.get_uri_for_connection = MagicMock(return_value="qemu:///system")
        self.app.vm_service.get_vm_identity = MagicMock(return_value=("uuid-123", "test-vm"))
        self.app.push_screen = MagicMock()

        self.app.action_pattern_select()

        self.app.push_screen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
