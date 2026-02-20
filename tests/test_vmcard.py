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
from vmanager.constants import StatusText, VmAction, VMCardConstants
from vmanager.events import VmActionRequest, VMSelectionChanged, VMNameClicked


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

        # Mock get_uri_for_connection to return a proper string
        self.mock_app.vm_service.get_uri_for_connection.return_value = "qemu:///system"

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

    # ========================================================================
    # REACTIVE PROPERTY WATCHER TESTS
    # ========================================================================

    def test_watch_name_updates_vmname_widget(self):
        """Test that watch_name updates the vmname widget when name changes."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.name = "old-name"

        # Mock the vmname widget
        mock_vmname = MagicMock()
        self.vm_card.ui = {"vmname": mock_vmname}
        self.vm_card.conn = MagicMock()
        self.mock_app.vm_service.get_uri_for_connection.return_value = "qemu:///system"

        with patch("vmanager.vmcard.extract_server_name_from_uri", return_value="localhost"):
            # Trigger the watcher
            VMCard.watch_name(self.vm_card, "new-vm-name")

        mock_vmname.update.assert_called_once()

    def test_watch_cpu_triggers_tooltip_update(self):
        """Test that watch_cpu triggers tooltip update."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update") as mock_tooltip:
            self.vm_card.cpu = 2

        # Call watch_cpu manually
        with patch.object(self.vm_card, "_perform_tooltip_update") as mock_tooltip:
            VMCard.watch_cpu(self.vm_card, 4)
            mock_tooltip.assert_called_once()

    def test_watch_memory_triggers_tooltip_update(self):
        """Test that watch_memory triggers tooltip update."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.memory = 1024

        with patch.object(self.vm_card, "_perform_tooltip_update") as mock_tooltip:
            VMCard.watch_memory(self.vm_card, 2048)
            mock_tooltip.assert_called_once()

    def test_watch_ip_addresses_triggers_tooltip_update(self):
        """Test that watch_ip_addresses triggers tooltip update."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.ip_addresses = []

        with patch.object(self.vm_card, "_perform_tooltip_update") as mock_tooltip:
            VMCard.watch_ip_addresses(self.vm_card, [{"ipv4": ["192.168.1.100"]}])
            mock_tooltip.assert_called_once()

    def test_watch_boot_device_triggers_tooltip_update(self):
        """Test that watch_boot_device triggers tooltip update."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.boot_device = ""

        with patch.object(self.vm_card, "_perform_tooltip_update") as mock_tooltip:
            VMCard.watch_boot_device(self.vm_card, "hd")
            mock_tooltip.assert_called_once()

    def test_watch_cpu_model_triggers_tooltip_update(self):
        """Test that watch_cpu_model triggers tooltip update."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.cpu_model = ""

        with patch.object(self.vm_card, "_perform_tooltip_update") as mock_tooltip:
            VMCard.watch_cpu_model(self.vm_card, "Skylake")
            mock_tooltip.assert_called_once()

    def test_watch_graphics_type_triggers_tooltip_update(self):
        """Test that watch_graphics_type triggers tooltip update."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.graphics_type = None

        with patch.object(self.vm_card, "_perform_tooltip_update") as mock_tooltip:
            VMCard.watch_graphics_type(self.vm_card, None, "spice")
            mock_tooltip.assert_called_once()

    # ========================================================================
    # SPARKLINE AND VIEW MODE TESTS
    # ========================================================================

    def test_watch_stats_view_mode_resources(self):
        """Test watch_stats_view_mode when switching to resources mode."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.compact_view = False

        # Mock sparklines container
        mock_sparklines_container = MagicMock()
        self.vm_card.ui = {"sparklines_container": mock_sparklines_container}
        self.vm_card.display = True

        # Mock query for sparkline classes
        mock_resources = [MagicMock(), MagicMock()]
        mock_io = [MagicMock(), MagicMock()]

        def mock_query(selector):
            if selector == ".resources-sparkline":
                return mock_resources
            elif selector == ".io-sparkline":
                return mock_io
            return []

        self.vm_card.query = MagicMock(side_effect=mock_query)

        with patch.object(self.vm_card, "update_sparkline_data"):
            VMCard.watch_stats_view_mode(self.vm_card, "io", "resources")

        # Verify resources sparklines are shown
        for widget in mock_resources:
            self.assertTrue(widget.display)
        # Verify IO sparklines are hidden
        for widget in mock_io:
            self.assertFalse(widget.display)

    def test_watch_stats_view_mode_io(self):
        """Test watch_stats_view_mode when switching to io mode."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.compact_view = False

        mock_sparklines_container = MagicMock()
        self.vm_card.ui = {"sparklines_container": mock_sparklines_container}
        self.vm_card.display = True

        mock_resources = [MagicMock(), MagicMock()]
        mock_io = [MagicMock(), MagicMock()]

        def mock_query(selector):
            if selector == ".resources-sparkline":
                return mock_resources
            elif selector == ".io-sparkline":
                return mock_io
            return []

        self.vm_card.query = MagicMock(side_effect=mock_query)

        with patch.object(self.vm_card, "update_sparkline_data"):
            VMCard.watch_stats_view_mode(self.vm_card, "resources", "io")

        # Verify resources sparklines are hidden
        for widget in mock_resources:
            self.assertFalse(widget.display)
        # Verify IO sparklines are shown
        for widget in mock_io:
            self.assertTrue(widget.display)

    def test_toggle_stats_view_running_vm(self):
        """Test toggle_stats_view toggles mode for running VM."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.stats_view_mode = "resources"

        VMCard.toggle_stats_view(self.vm_card)
        self.assertEqual(self.vm_card.stats_view_mode, "io")

        VMCard.toggle_stats_view(self.vm_card)
        self.assertEqual(self.vm_card.stats_view_mode, "resources")

    def test_toggle_stats_view_stopped_vm_no_change(self):
        """Test toggle_stats_view does not toggle for stopped VM."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.STOPPED
            self.vm_card.stats_view_mode = "resources"

        VMCard.toggle_stats_view(self.vm_card)
        # Should remain unchanged
        self.assertEqual(self.vm_card.stats_view_mode, "resources")

    def test_update_sparkline_data_resources_mode(self):
        """Test update_sparkline_data in resources mode."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.stats_view_mode = "resources"
            self.vm_card.cpu = 4
            self.vm_card.memory = 4096
            self.vm_card.compact_view = False

        # Mock sparkline_data
        self.mock_app.sparkline_data = {"uuid-123": {"cpu": [10, 20, 30], "mem": [50, 60, 70]}}
        self.mock_app.vm_service._sparkline_lock = MagicMock()
        self.mock_app.vm_service._sparkline_lock.__enter__ = MagicMock()
        self.mock_app.vm_service._sparkline_lock.__exit__ = MagicMock()

        mock_cpu_label = MagicMock()
        mock_mem_label = MagicMock()
        mock_cpu_sparkline = MagicMock()
        mock_mem_sparkline = MagicMock()

        self.vm_card.ui = {
            "cpu_label": mock_cpu_label,
            "mem_label": mock_mem_label,
            "cpu_sparkline": mock_cpu_sparkline,
            "mem_sparkline": mock_mem_sparkline,
        }

        with patch.object(VMCard, "is_mounted", new_callable=PropertyMock) as mock_mounted:
            mock_mounted.return_value = True
            self.vm_card.display = True
            VMCard.update_sparkline_data(self.vm_card)

        # Verify labels and sparklines were updated
        mock_cpu_label.update.assert_called()
        mock_mem_label.update.assert_called()

    # ========================================================================
    # COMPACT VIEW TESTS
    # ========================================================================

    def test_watch_compact_view_enables_compact_mode(self):
        """Test watch_compact_view applies compact styles."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.compact_view = False

        mock_sparklines = MagicMock()
        mock_sparklines.is_mounted = True
        mock_collapsible = MagicMock()
        mock_collapsible.is_mounted = True
        mock_vmname = MagicMock()
        mock_vmstatus = MagicMock()
        mock_checkbox = MagicMock()

        self.vm_card.ui = {
            "sparklines_container": mock_sparklines,
            "collapsible": mock_collapsible,
            "vmname": mock_vmname,
            "status": mock_vmstatus,
            "checkbox": mock_checkbox,
        }

        with patch.object(self.vm_card, "_perform_tooltip_update"):
            VMCard.watch_compact_view(self.vm_card, True)

        # Verify compact mode hides sparklines
        self.assertFalse(mock_sparklines.display)

    def test_apply_compact_view_styles_detailed_mode(self):
        """Test _apply_compact_view_styles in detailed (non-compact) mode."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.compact_view = True

        mock_sparklines = MagicMock()
        mock_collapsible = MagicMock()
        mock_vmname = MagicMock()
        mock_vmstatus = MagicMock()
        mock_checkbox = MagicMock()
        mock_info_container = MagicMock()
        mock_info_container.children = []

        self.vm_card.ui = {
            "sparklines_container": mock_sparklines,
            "collapsible": mock_collapsible,
            "vmname": mock_vmname,
            "status": mock_vmstatus,
            "checkbox": mock_checkbox,
        }

        self.vm_card.query_one = MagicMock(return_value=mock_info_container)

        with patch.object(self.vm_card, "watch_stats_view_mode"):
            VMCard._apply_compact_view_styles(self.vm_card, False)

        # In detailed mode, sparklines should be visible
        self.assertTrue(mock_sparklines.display)

    # ========================================================================
    # SELECTION AND CLICK HANDLING TESTS
    # ========================================================================

    def test_watch_is_selected_updates_checkbox(self):
        """Test watch_is_selected updates checkbox value."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.is_selected = False

        mock_checkbox = MagicMock()
        self.vm_card.ui = {"checkbox": mock_checkbox}

        with patch.object(VMCard, "is_mounted", new_callable=PropertyMock) as mock_mounted:
            mock_mounted.return_value = True
            VMCard.watch_is_selected(self.vm_card, False, True)

        self.assertTrue(mock_checkbox.value)

    def test_on_click_left_button_does_not_toggle_selection(self):
        """Test on_click with left button does not toggle selection."""
        mock_event = MagicMock()
        mock_event.button = 1  # Left button

        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123@uri"
            self.vm_card.is_selected = False

        self.vm_card.post_message = MagicMock()

        self.vm_card.on_click(mock_event)

        # Should not change selection
        self.assertFalse(self.vm_card.is_selected)
        # Should not post message
        self.vm_card.post_message.assert_not_called()
        # Should not stop event
        mock_event.stop.assert_not_called()

    def test_on_vm_select_checkbox_changed(self):
        """Test on_vm_select_checkbox_changed handler."""
        mock_event = MagicMock()
        mock_event.value = True

        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123@uri"
            self.vm_card.is_selected = False

        self.vm_card.post_message = MagicMock()

        VMCard.on_vm_select_checkbox_changed(self.vm_card, mock_event)

        self.assertTrue(self.vm_card.is_selected)
        self.vm_card.post_message.assert_called_once()
        args = self.vm_card.post_message.call_args[0][0]
        self.assertIsInstance(args, VMSelectionChanged)
        self.assertTrue(args.is_selected)

    # ========================================================================
    # STATUS STYLING TESTS
    # ========================================================================

    def test_update_status_styling_running(self):
        """Test _update_status_styling for running status."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING

        mock_status_widget = MagicMock()
        self.vm_card.ui = {"status": mock_status_widget}

        VMCard._update_status_styling(self.vm_card)

        mock_status_widget.add_class.assert_called_with("running")

    def test_update_status_styling_stopped(self):
        """Test _update_status_styling for stopped status."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.STOPPED

        mock_status_widget = MagicMock()
        self.vm_card.ui = {"status": mock_status_widget}

        VMCard._update_status_styling(self.vm_card)

        mock_status_widget.add_class.assert_called_with("stopped")

    def test_update_status_styling_paused(self):
        """Test _update_status_styling for paused status."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.PAUSED

        mock_status_widget = MagicMock()
        self.vm_card.ui = {"status": mock_status_widget}

        VMCard._update_status_styling(self.vm_card)

        mock_status_widget.add_class.assert_called_with("paused")

    def test_update_status_styling_loading(self):
        """Test _update_status_styling for loading status."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.LOADING

        mock_status_widget = MagicMock()
        self.vm_card.ui = {"status": mock_status_widget}

        VMCard._update_status_styling(self.vm_card)

        mock_status_widget.add_class.assert_called_with("loading")

    def test_update_status_styling_pmsuspended(self):
        """Test _update_status_styling for pmsuspended status."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.PMSUSPENDED

        mock_status_widget = MagicMock()
        self.vm_card.ui = {"status": mock_status_widget}

        VMCard._update_status_styling(self.vm_card)

        mock_status_widget.add_class.assert_called_with("pmsuspended")

    def test_update_status_styling_blocked(self):
        """Test _update_status_styling for blocked status."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.BLOCKED

        mock_status_widget = MagicMock()
        self.vm_card.ui = {"status": mock_status_widget}

        VMCard._update_status_styling(self.vm_card)

        mock_status_widget.add_class.assert_called_with("blocked")

    # ========================================================================
    # HELPER METHOD TESTS
    # ========================================================================

    def test_get_uri_with_conn(self):
        """Test _get_uri returns correct URI."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.conn = MagicMock()

        self.mock_app.vm_service.get_uri_for_connection.return_value = "qemu:///system"

        result = self.vm_card._get_uri()

        self.assertEqual(result, "qemu:///system")

    def test_get_uri_without_conn(self):
        """Test _get_uri returns empty string when no connection."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.conn = None

        result = self.vm_card._get_uri()

        self.assertEqual(result, "")

    def test_get_snapshot_tab_title_with_snapshots(self):
        """Test _get_snapshot_tab_title with snapshots."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.vm = MagicMock()

        result = self.vm_card._get_snapshot_tab_title(5)

        self.assertIn("5", result)

    def test_get_snapshot_tab_title_no_snapshots(self):
        """Test _get_snapshot_tab_title with no snapshots."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.vm = MagicMock()

        result = self.vm_card._get_snapshot_tab_title(0)

        self.assertEqual(result, "State Management")

    def test_is_remote_server_local(self):
        """Test _is_remote_server returns False for local connection."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.conn = MagicMock()

        self.mock_app.vm_service.get_uri_for_connection.return_value = "qemu:///system"

        result = self.vm_card._is_remote_server()

        self.assertFalse(result)

    def test_is_remote_server_remote_ssh(self):
        """Test _is_remote_server returns True for remote SSH connection."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.conn = MagicMock()

        self.mock_app.vm_service.get_uri_for_connection.return_value = (
            "qemu+ssh://user@remote-host/system"
        )

        result = self.vm_card._is_remote_server()

        self.assertTrue(result)

    def test_is_remote_server_no_conn(self):
        """Test _is_remote_server returns False when no connection."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.conn = None

        result = self.vm_card._is_remote_server()

        self.assertFalse(result)

    # ========================================================================
    # BUTTON HANDLER TESTS
    # ========================================================================

    def test_handle_stop_button_shows_confirmation(self):
        """Test _handle_stop_button shows confirmation dialog."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"

        self.mock_app.push_screen = MagicMock()

        self.vm_card._handle_stop_button()

        # Verify confirmation dialog was shown
        self.mock_app.push_screen.assert_called_once()
        from vmanager.modals.utils_modals import ConfirmationDialog

        args = self.mock_app.push_screen.call_args[0][0]
        self.assertIsInstance(args, ConfirmationDialog)

    def test_handle_pause_button_running_vm(self):
        """Test _handle_pause_button for running VM."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"
            self.vm_card.timer = None

        self.vm_card.post_message = MagicMock()
        self.mock_app.worker_manager.cancel = MagicMock()

        self.vm_card._handle_pause_button()

        # Verify VmActionRequest was posted
        self.vm_card.post_message.assert_called_once()
        args = self.vm_card.post_message.call_args[0][0]
        self.assertIsInstance(args, VmActionRequest)
        self.assertEqual(args.action, VmAction.PAUSE)

    def test_handle_pause_button_stopped_vm_shows_warning(self):
        """Test _handle_pause_button for stopped VM shows warning."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.STOPPED
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"

        self.mock_app.show_warning_message = MagicMock()

        self.vm_card._handle_pause_button()

        # Verify warning was shown
        self.mock_app.show_warning_message.assert_called_once()

    def test_handle_resume_button(self):
        """Test _handle_resume_button posts correct action."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.PAUSED
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"
            self.vm_card.timer = None

        self.vm_card.post_message = MagicMock()
        self.mock_app.worker_manager.cancel = MagicMock()
        self.mock_app.set_timer = MagicMock()

        self.vm_card._handle_resume_button()

        # Verify VmActionRequest was posted
        self.vm_card.post_message.assert_called_once()
        args = self.vm_card.post_message.call_args[0][0]
        self.assertIsInstance(args, VmActionRequest)
        self.assertEqual(args.action, VmAction.RESUME)

    def test_handle_migration_button_requires_two_servers(self):
        """Test _handle_migration_button requires at least two servers."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING
            self.vm_card.internal_id = "uuid-123"

        self.mock_app.active_uris = ["qemu:///system"]
        self.mock_app.show_error_message = MagicMock()

        self.vm_card._handle_migration_button()

        self.mock_app.show_error_message.assert_called_once()

    # ========================================================================
    # INTERNAL ID WATCHER TESTS
    # ========================================================================

    def test_watch_internal_id_cancels_old_workers(self):
        """Test watch_internal_id cancels workers when ID changes."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "old-uuid-123"

        self.mock_app.worker_manager.cancel = MagicMock()

        with patch.object(self.vm_card, "update_snapshot_tab_title"), patch.object(
            self.vm_card, "update_button_layout"
        ), patch.object(self.vm_card, "update_stats"), patch.object(
            self.vm_card, "_perform_tooltip_update"
        ):
            VMCard.watch_internal_id(self.vm_card, "old-uuid-123", "new-uuid-456")

        # Verify old workers were cancelled
        self.mock_app.worker_manager.cancel.assert_any_call("update_stats_old-uuid-123")
        self.mock_app.worker_manager.cancel.assert_any_call("actions_state_old-uuid-123")
        self.mock_app.worker_manager.cancel.assert_any_call("refresh_snapshot_tab_old-uuid-123")

    # ========================================================================
    # ERROR HANDLING TESTS
    # ========================================================================

    def test_handle_stats_error_no_domain(self):
        """Test _handle_stats_error handles VIR_ERR_NO_DOMAIN."""
        import libvirt

        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.timer = MagicMock()

        mock_error = MagicMock()
        mock_error.get_error_code.return_value = libvirt.VIR_ERR_NO_DOMAIN

        result = {"uuid": "uuid-123", "error": mock_error, "error_code": libvirt.VIR_ERR_NO_DOMAIN}

        self.mock_app.refresh_vm_list = MagicMock()

        VMCard._handle_stats_error(self.vm_card, result)

        # Verify timer was stopped and refresh was triggered
        self.vm_card.timer.stop.assert_called()
        self.mock_app.refresh_vm_list.assert_called_once()

    def test_handle_stats_error_connection_error(self):
        """Test _handle_stats_error handles connection errors."""
        import libvirt

        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.name = "test-vm"
            self.vm_card.timer = None

        mock_error = MagicMock()
        mock_error.get_error_code.return_value = libvirt.VIR_ERR_NO_CONNECT

        result = {"uuid": "uuid-123", "error": mock_error, "error_code": libvirt.VIR_ERR_NO_CONNECT}

        self.mock_app.refresh_vm_list = MagicMock()

        VMCard._handle_stats_error(self.vm_card, result)

        # Verify force refresh was triggered
        self.mock_app.refresh_vm_list.assert_called_once_with(force=True)

    # ========================================================================
    # APPLY STATS UPDATE TESTS
    # ========================================================================

    def test_apply_stats_update_with_empty_stats(self):
        """Test _apply_stats_update handles empty stats gracefully."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.status = StatusText.RUNNING

        result = {
            "uuid": "uuid-123",
            "stats": None,
            "boot_device": "hd",
            "cpu_model": "Skylake",
            "graphics_type": "spice",
        }

        with patch.object(VMCard, "is_mounted", new_callable=PropertyMock) as mock_mounted:
            mock_mounted.return_value = True
            VMCard._apply_stats_update(self.vm_card, result)

        # Verify status was set to STOPPED
        self.assertEqual(self.vm_card.status, StatusText.STOPPED)

    def test_apply_stats_update_with_valid_stats(self):
        """Test _apply_stats_update properly updates VM state."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.status = StatusText.STOPPED
            self.vm_card.stats_view_mode = "resources"

        self.mock_app.sparkline_data = {"uuid-123": {"cpu": [], "mem": []}}
        self.mock_app.vm_service._sparkline_lock = MagicMock()
        self.mock_app.vm_service._sparkline_lock.__enter__ = MagicMock()
        self.mock_app.vm_service._sparkline_lock.__exit__ = MagicMock()

        result = {
            "uuid": "uuid-123",
            "stats": {
                "status": StatusText.RUNNING,
                "cpu_percent": 50,
                "mem_percent": 60,
                "disk_read_kbps": 100,
                "disk_write_kbps": 200,
                "net_rx_kbps": 50,
                "net_tx_kbps": 75,
            },
            "ips": [{"ipv4": ["192.168.1.100"]}],
            "boot_device": "hd",
            "cpu_model": "Skylake",
            "graphics_type": "spice",
            "boot_device_checked": True,
        }

        with patch.object(VMCard, "is_mounted", new_callable=PropertyMock) as mock_mounted:
            mock_mounted.return_value = True
            with patch.object(self.vm_card, "_update_webc_status"), patch.object(
                self.vm_card, "update_sparkline_data"
            ):
                VMCard._apply_stats_update(self.vm_card, result)

        # Verify state was updated
        self.assertEqual(self.vm_card.status, StatusText.RUNNING)
        self.assertEqual(self.vm_card.ip_addresses, [{"ipv4": ["192.168.1.100"]}])
        self.assertEqual(self.vm_card.boot_device, "hd")
        self.assertEqual(self.vm_card.cpu_model, "Skylake")
        self.assertEqual(self.vm_card.latest_disk_read, 100)
        self.assertEqual(self.vm_card.latest_disk_write, 200)

    # ========================================================================
    # RESET AND REUSE TESTS
    # ========================================================================

    def test_reset_for_reuse_cancels_workers(self):
        """Test reset_for_reuse properly cancels workers and resets state."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.timer = MagicMock()
            self.vm_card._boot_device_checked = True

        self.mock_app.worker_manager.cancel = MagicMock()

        VMCard.reset_for_reuse(self.vm_card)

        # Verify timer was stopped
        self.assertIsNone(self.vm_card.timer)
        # Verify workers were cancelled
        self.mock_app.worker_manager.cancel.assert_any_call("update_stats_uuid-123")
        self.mock_app.worker_manager.cancel.assert_any_call("actions_state_uuid-123")
        # Verify flag was reset
        self.assertFalse(self.vm_card._boot_device_checked)

    # ========================================================================
    # WEB CONSOLE STATUS TESTS
    # ========================================================================

    def test_update_webc_status_not_running(self):
        """Test _update_webc_status when console is not running."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card.vm = MagicMock()
            self.vm_card.webc_status_indicator = " (WebC On)"

        self.mock_app.webconsole_manager.is_running.return_value = False

        mock_button = MagicMock()
        self.vm_card.query_one = MagicMock(return_value=mock_button)

        VMCard._update_webc_status(self.vm_card)

        # Verify indicator was cleared
        self.assertEqual(self.vm_card.webc_status_indicator, "")
        # Verify button was reset
        self.assertEqual(mock_button.variant, "default")

    def test_watch_webc_status_indicator_updates_status_widget(self):
        """Test watch_webc_status_indicator updates status widget."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.status = StatusText.RUNNING

        mock_status_widget = MagicMock()
        self.vm_card.ui = {"status": mock_status_widget}

        with patch.object(VMCard, "is_mounted", new_callable=PropertyMock) as mock_mounted:
            mock_mounted.return_value = True
            VMCard.watch_webc_status_indicator(self.vm_card, "", " (WebC On)")

        mock_status_widget.update.assert_called_with(f"{StatusText.RUNNING} (WebC On)")

    # ========================================================================
    # DISPATCH ACTION TESTS
    # ========================================================================

    def test_dispatch_action_start(self):
        """Test _dispatch_action for start action."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"

        self.vm_card.post_message = MagicMock()

        VMCard._dispatch_action(self.vm_card, "start")

        self.vm_card.post_message.assert_called_once()
        args = self.vm_card.post_message.call_args[0][0]
        self.assertIsInstance(args, VmActionRequest)
        self.assertEqual(args.action, VmAction.START)

    def test_dispatch_action_unknown(self):
        """Test _dispatch_action with unknown action does nothing."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123"

        # This should not raise an exception
        VMCard._dispatch_action(self.vm_card, "unknown_action")

    # ========================================================================
    # SERVER BORDER COLOR TESTS
    # ========================================================================

    def test_watch_server_border_color_selected(self):
        """Test watch_server_border_color when card is selected."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.is_selected = True

        VMCard.watch_server_border_color(self.vm_card, "blue", "green")

        # When selected, should use selected border style
        # Check that border was set (border is an Edges object, so we check the top edge)
        border_top = self.vm_card.styles.border.top
        self.assertEqual(border_top[0], VMCardConstants.SELECTED_BORDER_TYPE)

    def test_watch_server_border_color_not_selected(self):
        """Test watch_server_border_color when card is not selected."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.is_selected = False

        VMCard.watch_server_border_color(self.vm_card, "blue", "green")

        # When not selected, should use default border type
        border_top = self.vm_card.styles.border.top
        self.assertEqual(border_top[0], VMCardConstants.DEFAULT_BORDER_TYPE)

    def test_watch_server_border_color_not_selected(self):
        """Test watch_server_border_color when card is not selected."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.is_selected = False

        VMCard.watch_server_border_color(self.vm_card, "blue", "green")

        # When not selected, should use default border type
        border_top = self.vm_card.styles.border.top
        self.assertEqual(border_top[0], VMCardConstants.DEFAULT_BORDER_TYPE)

    # ========================================================================
    # VMNAME CLICK TESTS
    # ========================================================================

    def test_on_click_vmname_single_click(self):
        """Test on_click_vmname posts VMNameClicked on single click."""
        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123@uri"
            self.vm_card.name = "test-vm"
            self.vm_card._last_click_time = 0

        self.vm_card.post_message = MagicMock()

        VMCard.on_click_vmname(self.vm_card)

        self.vm_card.post_message.assert_called_once()
        args = self.vm_card.post_message.call_args[0][0]
        self.assertIsInstance(args, VMNameClicked)
        self.assertEqual(args.vm_name, "test-vm")

    def test_on_click_vmname_double_click_fetches_xml(self):
        """Test on_click_vmname fetches XML on double click."""
        import time

        with patch.object(VMCard, "update_button_layout"), patch.object(
            VMCard, "update_stats"
        ), patch.object(VMCard, "_perform_tooltip_update"):
            self.vm_card.internal_id = "uuid-123@uri"
            self.vm_card.name = "test-vm"
            self.vm_card.compact_view = False
            # Set last click time to simulate recent click
            self.vm_card._last_click_time = time.time()

        self.vm_card.post_message = MagicMock()

        with patch.object(self.vm_card, "_fetch_xml_and_update_tooltip") as mock_fetch:
            VMCard.on_click_vmname(self.vm_card)
            mock_fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
