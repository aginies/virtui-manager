import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import sys
import os
import builtins
from textual._context import active_app

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# Mock libvirt to avoid actual system calls
mock_libvirt = MagicMock()
sys.modules['libvirt'] = mock_libvirt

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
        with patch('vmanager.vmcard.VMCard.update_button_layout'), \
             patch('vmanager.vmcard.VMCard.update_stats'), \
             patch('vmanager.vmcard.VMCard._update_status_styling'), \
             patch('textual.widgets.Static._on_mount'), \
             patch('textual.widgets.Static.render'):
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
        self.mock_app.vm_service.get_vm_identity.assert_called_once_with(self.vm_card.vm, self.vm_card.conn)

    def test_get_vm_display_name(self):
        """Test _get_vm_display_name helper."""
        self.vm_card.name = "test-vm"
        self.vm_card.conn = MagicMock()
        self.mock_app.vm_service.get_uri_for_connection.return_value = "qemu:///system"
        
        with patch('vmanager.vmcard.extract_server_name_from_uri', return_value="localhost"):
            display_name = self.vm_card._get_vm_display_name()
            self.assertEqual(display_name, "test-vm (localhost)")

    def test_raw_uuid(self):
        """Test raw_uuid property."""
        # Patch side-effect methods to avoid issues when setting internal_id
        with patch.object(VMCard, 'update_button_layout'), \
             patch.object(VMCard, 'update_stats'), \
             patch.object(VMCard, '_perform_tooltip_update'):
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
        
        with patch.object(VMCard, 'update_button_layout'), \
             patch.object(VMCard, 'update_stats'), \
             patch.object(VMCard, '_perform_tooltip_update'):
            self.vm_card.internal_id = "uuid-123"
            self.vm_card._update_webc_status()
        
        self.assertEqual(self.vm_card.webc_status_indicator, " (WebC On)")
        self.assertEqual(mock_button.variant, "success")
        self.assertEqual(mock_button.label, "Show Console")

    def test_handle_shutdown_button(self):
        """Test _handle_shutdown_button method."""
        with patch.object(VMCard, 'update_button_layout'), \
             patch.object(VMCard, 'update_stats'), \
             patch.object(VMCard, '_perform_tooltip_update'):
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

    @patch('vmanager.vmcard.VMCard._update_status_styling')
    @patch('vmanager.vmcard.VMCard.update_button_layout')
    @patch('vmanager.vmcard.VMCard.update_stats')
    @patch('vmanager.vmcard.VMCard._perform_tooltip_update')
    def test_watch_status(self, mock_tooltip, mock_stats, mock_buttons, mock_styling):
        """Test watch_status watcher."""
        self.vm_card.ui = {"status": MagicMock()}
        
        # Test the scenario that would trigger watch_status - changing status property
        with patch.object(VMCard, 'is_mounted', new_callable=PropertyMock) as mock_is_mounted:
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
        mock_event.button = 3 # Right button
        
        with patch.object(VMCard, 'update_button_layout'), \
             patch.object(VMCard, 'update_stats'), \
             patch.object(VMCard, '_perform_tooltip_update'):
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

if __name__ == "__main__":
    unittest.main()
