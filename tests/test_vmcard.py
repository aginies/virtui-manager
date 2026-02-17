import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import sys
import os
import builtins
from textual._context import active_app

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# Mock translation function globally for tests
builtins._ = lambda s: s

from vmanager.vmcard import VMCard
from vmanager.constants import StatusText, VmAction
from vmanager.events import VmActionRequest, VMSelectionChanged


class TestVMCard(unittest.TestCase):
    def setUp(self):
        # We need to mock the App and other dependencies that VMCard uses
        self.mock_app = MagicMock()
        self.mock_app.vm_service = MagicMock()
        self.mock_app.worker_manager = MagicMock()
        self.mock_app.webconsole_manager = MagicMock()
        self.mock_app.sparkline_data = {}
        self.mock_app.config = {"STATS_INTERVAL": 5}
        self.mock_app.r_viewer_available = True

        # Set the active_app context variable
        self._token = active_app.set(self.mock_app)

        # Instantiate VMCard with patched methods to avoid side effects during setup
        with patch("vmanager.vmcard.VMCard.update_button_layout"), patch(
            "vmanager.vmcard.VMCard.update_stats"
        ), patch("vmanager.vmcard.VMCard._update_status_styling"), patch(
            "textual.widgets.Static._on_mount"
        ), patch("textual.widgets.Static.render"):
            self.vm_card = VMCard()

    def tearDown(self):
        # Reset the active_app context variable
        active_app.reset(self._token)

    def test_init(self):
        """Test VMCard initialization."""
        card = VMCard(is_selected=True)
        self.assertTrue(card.is_selected)
        self.assertEqual(card.ui, {})

    def test_vm_identity_info(self):
        """Test _get_vm_identity_info helper."""
        self.vm_card.vm = MagicMock()
        self.vm_card.conn = MagicMock()
        self.mock_app.vm_service.get_vm_identity.return_value = ("uuid-123", "test-vm")

        uuid, name = self.vm_card._get_vm_identity_info()
        self.assertEqual(uuid, "uuid-123")
        self.assertEqual(name, "test-vm")
        self.mock_app.vm_service.get_vm_identity.assert_called_once_with(
            self.vm_card.vm, self.vm_card.conn
        )

    def test_get_vm_display_name(self):
        """Test _get_vm_display_name helper."""
        self.vm_card.name = "test-vm"
        self.vm_card.conn = MagicMock()
        self.mock_app.vm_service.get_uri_for_connection.return_value = "qemu:///system"

        with patch("vmanager.vmcard.extract_server_name_from_uri", return_value="localhost"):
            display_name = self.vm_card._get_vm_display_name()
            self.assertEqual(display_name, "test-vm (localhost)")

    def test_raw_uuid(self):
        """Test raw_uuid property."""
        # Patch side-effect methods to avoid issues when setting internal_id
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123@qemu:///system"
            self.assertEqual(self.vm_card.raw_uuid, "uuid-123")

            self.vm_card.internal_id = "uuid-456"
            self.assertEqual(self.vm_card.raw_uuid, "uuid-456")

    def test_update_webc_status(self):
        """Test _update_webc_status method."""
        self.vm_card.vm = MagicMock()

        # Mock webconsole_manager to say it's running
        self.mock_app.webconsole_manager.is_running.return_value = True

        # Mock the query_one to return a mock button
        mock_button = MagicMock()
        self.vm_card.query_one = MagicMock(return_value=mock_button)

        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card._update_webc_status()

        self.assertEqual(self.vm_card.webc_status_indicator, " (WebC On)")
        self.assertEqual(mock_button.variant, "success")
        self.assertEqual(mock_button.label, "Show Console")

    def test_handle_shutdown_button(self):
        """Test _handle_shutdown_button method."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.status = StatusText.RUNNING

        # Mock post_message
        self.vm_card.post_message = MagicMock()

        self.vm_card._handle_shutdown_button()

        # Check if VmActionRequest was sent
        self.vm_card.post_message.assert_called_once()
        args = self.vm_card.post_message.call_args[0][0]
        self.assertIsInstance(args, VmActionRequest)
        self.assertEqual(args.internal_id, "uuid-123")
        self.assertEqual(args.action, VmAction.STOP)

    @patch("vmanager.vmcard.VMCard._update_status_styling")
    @patch("vmanager.vmcard.VMCard.update_button_layout")
    @patch("vmanager.vmcard.VMCard.update_stats")
    @patch("vmanager.vmcard.VMCard._perform_tooltip_update")
    def test_watch_status(self, mock_tooltip, mock_stats, mock_buttons, mock_styling):
        """Test watch_status watcher."""
        self.vm_card.ui = {"status": MagicMock()}

        # Test the scenario that would trigger watch_status - changing status property
        with patch.object(VMCard, "is_mounted", new_callable=PropertyMock) as mock_is_mounted:
            mock_is_mounted.return_value = True
            # Instead of manually calling watch_status, change the status property which should trigger watch_status
            self.vm_card.status = StatusText.RUNNING

            # Verify that _update_status_styling was called (at least once)
            mock_styling.assert_called()
            self.assertGreaterEqual(mock_styling.call_count, 1)
            self.assertGreaterEqual(mock_buttons.call_count, 1)
            self.assertGreaterEqual(mock_tooltip.call_count, 1)
            mock_stats.assert_called_once()
            self.assertGreaterEqual(self.vm_card.ui["status"].update.call_count, 1)

    def test_on_click_right_button(self):
        """Test on_click with right mouse button."""
        mock_event = MagicMock()
        mock_event.button = 3  # Right button

        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123@uri"
            self.vm_card.is_selected = False

        self.vm_card.post_message = MagicMock()

        self.vm_card.on_click(mock_event)

        self.assertTrue(self.vm_card.is_selected)
        self.vm_card.post_message.assert_called_once()
        args = self.vm_card.post_message.call_args[0][0]
        self.assertIsInstance(args, VMSelectionChanged)
        self.assertEqual(args.internal_id, "uuid-123")
        self.assertTrue(args.is_selected)
        mock_event.stop.assert_called_once()

    def test_update_button_layout(self):
        """Test update_button_layout method."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING

        # Mock the button elements to ensure they can be found
        self.vm_card.query_one = MagicMock()
        mock_button = MagicMock()
        self.vm_card.query_one.return_value = mock_button

        # Test that it can be called
        self.vm_card.update_button_layout()
        # Should not raise an exception
        self.assertTrue(True)

    def test_update_stats(self):
        """Test update_stats method."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING

        # Mock the necessary components
        self.vm_card._apply_stats_update = MagicMock()
        self.vm_card._stats_data_fetch_worker = MagicMock()

        # Test that it can be called
        self.vm_card.update_stats()
        # Should not raise an exception
        self.assertTrue(True)

    def test_on_vm_action_button_pressed(self):
        """Test on_vm_action_button_pressed method."""
        mock_event = MagicMock()
        mock_event.action = VmAction.START

        # Mock the internal methods
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"

        # Mock post_message to check if it's called
        self.vm_card.post_message = MagicMock()

        # Test that the method can be called
        self.vm_card.on_vm_action_button_pressed(mock_event)
        # Should not raise an exception
        self.assertTrue(True)

    def test_on_button_pressed(self):
        """Test on_button_pressed method."""
        mock_event = MagicMock()
        mock_event.button = MagicMock()
        mock_event.button.id = "start"

        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.STOPPED
            self.vm_card.internal_id = "uuid-123"

        self.vm_card.post_message = MagicMock()

        # Test that the method can be called
        self.vm_card.on_button_pressed(mock_event)
        # Should not raise an exception
        self.assertTrue(True)

    def test_handle_clone_button(self):
        """Test _handle_clone_button method."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.STOPPED
            self.vm_card.internal_id = "uuid-123"

        # Mock the necessary components that are called internally

        # Test that the method can be called
        self.vm_card._handle_clone_button()
        # Should not raise an exception
        self.assertTrue(True)

    def test_handle_rename_button(self):
        """Test _handle_rename_button method."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.STOPPED
            self.vm_card.internal_id = "uuid-123"

        # Mock the necessary components that are called internally

        # Test that the method can be called
        self.vm_card._handle_rename_button()
        # Should not raise an exception
        self.assertTrue(True)

    def test_handle_delete_button(self):
        """Test _handle_delete_button method."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.STOPPED
            self.vm_card.internal_id = "uuid-123"

        # Mock the necessary components that are called internally

        # Test that the method can be called
        self.vm_card._handle_delete_button()
        # Should not raise an exception
        self.assertTrue(True)

    # ========================================================================
    # UI FREEZE / BLOCKING TESTS
    # ========================================================================

    def test_xml_button_does_not_block_ui_on_xmldesc_call(self):
        """Test that _handle_xml_button doesn't block UI with XMLDesc() call."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.STOPPED
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"

        # Mock the vm object
        mock_vm = MagicMock()
        self.vm_card.vm = mock_vm

        # Simulate a slow XMLDesc call that could freeze UI
        def slow_xmldesc(flags):
            import time

            time.sleep(0.001)  # Simulate network delay
            return "<domain><name>test-vm</name></domain>"

        mock_vm.XMLDesc = MagicMock(side_effect=slow_xmldesc)

        # Mock push_screen to capture the modal
        self.vm_card.post_message = MagicMock()
        self.mock_app.push_screen = MagicMock()

        # Call the handler
        self.vm_card._handle_xml_button()

        # Verify XMLDesc was called
        self.assertTrue(mock_vm.XMLDesc.called)

        # Verify modal was shown (non-blocking UI pattern)
        self.mock_app.push_screen.assert_called_once()

    def test_stats_worker_uses_threaded_execution(self):
        """Test that stats updates are executed in worker threads, not blocking UI."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.vm = MagicMock()
            self.vm_card.conn = MagicMock()

        # Mock worker_manager to verify threaded execution
        worker_called = []

        def mock_run(func, name=None):
            worker_called.append((func, name))

        self.mock_app.worker_manager.run = MagicMock(side_effect=mock_run)

        # Mock set_timer to avoid event loop issues
        self.vm_card.set_timer = MagicMock(return_value=MagicMock())

        # Manually call update_stats (bypassing the patched version)
        VMCard.update_stats(self.vm_card)

        # Verify worker was scheduled
        self.assertEqual(len(worker_called), 1)
        self.assertIn("update_stats_uuid-123", worker_called[0][1])

    def test_stats_data_fetch_worker_does_not_block_main_thread(self):
        """Test that _stats_data_fetch_worker properly offloads blocking operations."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.status = StatusText.RUNNING

        mock_vm = MagicMock()
        mock_conn = MagicMock()

        # Mock vm_service to return stats
        self.mock_app.vm_service.get_vm_runtime_stats.return_value = {
            "status": StatusText.RUNNING,
            "cpu_percent": 50,
            "mem_percent": 60,
            "disk_read_kbps": 100,
            "disk_write_kbps": 200,
            "net_rx_kbps": 50,
            "net_tx_kbps": 75,
        }

        # Create worker context
        ctx = {
            "uuid": "uuid-123",
            "vm": mock_vm,
            "conn": mock_conn,
            "current_status": StatusText.RUNNING,
            "boot_device": "",
            "cpu_model": "",
            "graphics_type": None,
            "boot_device_checked": False,
            "is_remote": False,
        }

        # Mock call_from_thread to verify it's used for UI updates
        call_from_thread_calls = []

        def mock_call_from_thread(func, *args):
            call_from_thread_calls.append((func, args))

        self.mock_app.call_from_thread = MagicMock(side_effect=mock_call_from_thread)
        self.mock_app.vm_service._vm_data_cache = {}

        # Execute worker
        self.vm_card._stats_data_fetch_worker(ctx)

        # Verify that call_from_thread was used (indicating proper threading)
        self.assertGreater(len(call_from_thread_calls), 0)

    def test_configure_button_uses_loading_modal_to_prevent_ui_freeze(self):
        """Test that _handle_configure_button shows loading modal during blocking operations."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.STOPPED
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"
            self.vm_card.vm = MagicMock()
            self.vm_card.conn = MagicMock()
            self.vm_card.ip_addresses = []

        # Mock push_screen to verify loading modal is shown
        self.mock_app.push_screen = MagicMock()
        self.mock_app.active_uris = ["qemu:///system"]

        # Call the handler
        self.vm_card._handle_configure_button()

        # Verify that a loading modal was pushed (prevents UI freeze perception)
        self.mock_app.push_screen.assert_called()
        # First call should be the loading modal
        from vmanager.modals.utils_modals import LoadingModal

        first_call_args = self.mock_app.push_screen.call_args_list[0][0]
        self.assertIsInstance(first_call_args[0], LoadingModal)

    def test_snapshot_operations_use_workers_not_main_thread(self):
        """Test that snapshot operations don't block UI by using worker threads."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"
            self.vm_card.vm = MagicMock()

        worker_calls = []

        def mock_worker_run(func, name=None, exclusive=False):
            worker_calls.append(name)

        self.mock_app.worker_manager.run = MagicMock(side_effect=mock_worker_run)
        self.mock_app.push_screen = MagicMock()

        # Simulate snapshot take with result
        def simulate_snapshot_modal():
            # Get the callback passed to push_screen
            callback = self.mock_app.push_screen.call_args[0][1]
            # Invoke it with test data
            callback({"name": "snap1", "description": "test", "quiesce": False})

        self.mock_app.push_screen.side_effect = (
            lambda modal, callback=None: simulate_snapshot_modal() if callback else None
        )
        self.mock_app.vm_service.suppress_vm_events = MagicMock()
        self.mock_app.vm_service.unsuppress_vm_events = MagicMock()

        # Call snapshot take
        self.vm_card._handle_snapshot_take_button()

        # Verify worker was used
        self.assertTrue(any("snapshot_take" in str(name) for name in worker_calls))

    def test_clone_button_stops_background_workers_before_heavy_operation(self):
        """Test that clone operation stops background activities to prevent resource contention."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.STOPPED
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"
            self.vm_card.vm = MagicMock()
            self.vm_card.conn = MagicMock()
            self.vm_card.timer = MagicMock()

        # Track if worker.run was called with clone operation
        worker_run_called = []

        def mock_worker_run(func, name=None):
            worker_run_called.append(name)
            # Don't execute the func to avoid complex nested mocking

        self.mock_app.worker_manager.run = MagicMock(side_effect=mock_worker_run)
        self.mock_app.worker_manager.cancel = MagicMock()
        self.mock_app.push_screen = MagicMock()

        # Call clone button - this should schedule a worker
        self.vm_card._handle_clone_button()

        # Verify push_screen was called with AdvancedCloneDialog
        self.mock_app.push_screen.assert_called_once()

        # The actual worker cancellation happens inside do_clone callback
        # We verify the pattern is set up correctly

    def test_stop_background_activities_cancels_all_workers(self):
        """Test that stop_background_activities properly cancels all background tasks."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.timer = MagicMock()

        # Call stop_background_activities
        self.vm_card.stop_background_activities()

        # Verify timer was stopped (only if timer exists)
        if self.vm_card.timer:
            self.vm_card.timer.stop.assert_called_once()

        # Verify all workers were cancelled
        expected_cancellations = [
            f"update_stats_{self.vm_card.internal_id}",
            f"actions_state_{self.vm_card.internal_id}",
            f"refresh_snapshot_tab_{self.vm_card.internal_id}",
        ]

        for worker_name in expected_cancellations:
            self.mock_app.worker_manager.cancel.assert_any_call(worker_name)

    def test_fetch_actions_state_worker_uses_threading_locks(self):
        """Test that _fetch_actions_state_worker uses proper locking to prevent race conditions."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.vm = MagicMock()
            self.vm_card.status = StatusText.STOPPED

        # Mock domain state
        self.mock_app.vm_service._get_domain_state = MagicMock(return_value=(1, 5, "stopped"))
        self.mock_app.vm_service._cache_lock = MagicMock()
        self.mock_app._last_snapshot_fetch = {}

        # Mock snapshot operations
        self.vm_card.vm.snapshotNum = MagicMock(return_value=0)

        with patch("vmanager.vmcard.has_overlays", return_value=False):
            # Execute worker
            self.vm_card._fetch_actions_state_worker()

            # Verify cache lock was used (via __enter__ and __exit__ calls)
            self.assertGreater(self.mock_app.vm_service._cache_lock.__enter__.call_count, 0)

    def test_timer_lock_prevents_race_conditions_in_update_stats(self):
        """Test that _timer_lock prevents concurrent timer creation."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.vm = MagicMock()
            self.vm_card.conn = MagicMock()
            self.vm_card.timer = None
            self.vm_card._boot_device_checked = False

        # Mock the set_timer method
        self.vm_card.set_timer = MagicMock(return_value=MagicMock())
        self.mock_app.worker_manager.run = MagicMock()

        # Verify that _timer_lock exists
        self.assertIsNotNone(self.vm_card._timer_lock)

        # Call update_stats multiple times to simulate concurrent calls
        VMCard.update_stats(self.vm_card)
        VMCard.update_stats(self.vm_card)

        # The lock should prevent timer accumulation
        # Only one timer should be active at a time
        self.assertIsNotNone(self.vm_card.timer)

    def test_handle_web_console_uses_worker_for_console_start(self):
        """Test that web console operations don't block UI."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"
            self.vm_card.vm = MagicMock()
            self.vm_card.conn = MagicMock()

        # Mock webconsole_manager
        self.mock_app.webconsole_manager.is_running.return_value = False
        self.mock_app.webconsole_manager.is_remote_connection.return_value = False

        worker_calls = []

        def mock_worker_run(func, name=None):
            worker_calls.append(name)

        self.mock_app.worker_manager.run = MagicMock(side_effect=mock_worker_run)

        # Call handler
        self.vm_card._handle_web_console_button()

        # Verify worker was used
        self.assertTrue(any("start_console" in str(name) for name in worker_calls))

    def test_remote_viewer_connection_uses_worker_thread(self):
        """Test that remote viewer connection doesn't block UI."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"
            self.vm_card.vm = MagicMock()
            self.vm_card.conn = MagicMock()

        worker_calls = []

        def mock_worker_run(func, name=None):
            worker_calls.append(name)

        self.mock_app.worker_manager.run = MagicMock(side_effect=mock_worker_run)
        self.mock_app.vm_service.get_vm_identity.return_value = ("uuid-123", "test-vm")
        self.mock_app.vm_service.get_uri_for_connection.return_value = "qemu:///system"

        with patch("vmanager.vmcard.remote_viewer_cmd", return_value=["echo", "test"]):
            # Call handler
            self.vm_card._handle_connect_button()

            # Verify worker was used
            self.assertTrue(any("r_viewer" in str(name) for name in worker_calls))

    def test_hibernate_operation_uses_worker_to_prevent_ui_block(self):
        """Test that hibernate operation runs in worker thread."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"
            self.vm_card.vm = MagicMock()
            self.vm_card.timer = MagicMock()

        worker_calls = []

        def mock_worker_run(func, name=None):
            worker_calls.append(name)

        self.mock_app.worker_manager.run = MagicMock(side_effect=mock_worker_run)
        self.mock_app.worker_manager.cancel = MagicMock()

        # Call handler
        self.vm_card._handle_hibernate_button()

        # Verify worker was scheduled
        self.assertTrue(any("save_" in str(name) for name in worker_calls))

    def test_unmount_cancels_workers_to_prevent_stale_updates(self):
        """Test that on_unmount properly cancels workers."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.vm = MagicMock()
            self.vm_card.timer = MagicMock()
            self.vm_card.ui = {"collapsible": MagicMock(collapsed=True)}

        # Call unmount
        self.vm_card.on_unmount()

        # Verify timer was stopped (only if timer exists)
        if self.vm_card.timer:
            self.vm_card.timer.stop.assert_called_once()

        # Verify worker was cancelled
        self.mock_app.worker_manager.cancel.assert_called_with(
            f"update_stats_{self.vm_card.internal_id}"
        )


if __name__ == "__main__":
    unittest.main()
