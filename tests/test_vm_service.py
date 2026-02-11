import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vm_service import VMService


class TestVMService(unittest.TestCase):
    def setUp(self):
        # Create a mock VMService instance without starting monitoring
        self.vm_service = VMService()
        # Mock the connection manager to avoid actual connections
        self.vm_service.connection_manager = MagicMock()

    def test_init(self):
        """Test VMService initialization."""
        self.assertIsInstance(self.vm_service, VMService)
        self.assertIsNotNone(self.vm_service.connection_manager)
        self.assertFalse(self.vm_service._global_updates_suspended)

    def test_set_callbacks(self):
        """Test setting update callbacks."""
        callback = MagicMock()

        self.vm_service.set_data_update_callback(callback)
        self.assertEqual(self.vm_service._data_update_callback, callback)

        self.vm_service.set_vm_update_callback(callback)
        self.assertEqual(self.vm_service._vm_update_callback, callback)

        self.vm_service.set_message_callback(callback)
        self.assertEqual(self.vm_service._message_callback, callback)

    @patch("threading.Thread")
    def test_start_stop_monitoring(self, mock_thread_class):
        """Test starting and stopping monitoring."""
        # Test start
        self.vm_service.start_monitoring()
        self.assertTrue(self.vm_service._monitoring_active)

        # Test stop
        self.vm_service.stop_monitoring()
        self.assertFalse(self.vm_service._monitoring_active)

    def test_suppress_unsuppress_events(self):
        """Test suppressing and unsuspressing VM events."""
        uuid = "test-uuid"

        self.vm_service.suppress_vm_events(uuid)
        self.assertIn(uuid, self.vm_service._suppressed_uuids)

        self.vm_service.unsuppress_vm_events(uuid)
        self.assertNotIn(uuid, self.vm_service._suppressed_uuids)

    def test_suspend_resume_global_updates(self):
        """Test suspending and resuming global updates."""
        self.vm_service.suspend_global_updates()
        self.assertTrue(self.vm_service._global_updates_suspended)

        self.vm_service.resume_global_updates()
        self.assertFalse(self.vm_service._global_updates_suspended)


if __name__ == "__main__":
    unittest.main()
