import unittest
import sys
import os

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.constants import AppInfo, VmAction, VmStatus, ButtonLabels


class TestConstants(unittest.TestCase):
    def test_app_info(self):
        """Test AppInfo constants."""
        self.assertEqual(AppInfo.name, "virtui-manager")
        self.assertEqual(AppInfo.namecase, "VirtUI Manager")
        self.assertEqual(AppInfo.version, "1.9.0")

    def test_vm_action(self):
        """Test VmAction constants."""
        self.assertEqual(VmAction.START, "start")
        self.assertEqual(VmAction.STOP, "stop")
        self.assertEqual(VmAction.FORCE_OFF, "force_off")
        self.assertEqual(VmAction.PAUSE, "pause")
        self.assertEqual(VmAction.RESUME, "resume")
        self.assertEqual(VmAction.DELETE, "delete")

    def test_vm_status(self):
        """Test VmStatus constants."""
        self.assertEqual(VmStatus.DEFAULT, "default")
        self.assertEqual(VmStatus.RUNNING, "running")
        self.assertEqual(VmStatus.PAUSED, "paused")
        self.assertEqual(VmStatus.PMSUSPENDED, "pmsuspended")
        self.assertEqual(VmStatus.BLOCKED, "blocked")
        self.assertEqual(VmStatus.STOPPED, "stopped")
        self.assertEqual(VmStatus.SELECTED, "selected")

    def test_button_labels_exist(self):
        """Test that button labels are defined."""
        # Just check a few to make sure the structure is working
        self.assertIsNotNone(ButtonLabels.ADD)
        self.assertIsNotNone(ButtonLabels.SAVE)
        self.assertIsNotNone(ButtonLabels.START)
        self.assertIsNotNone(ButtonLabels.STOP)
        self.assertIsNotNone(ButtonLabels.DELETE)
        self.assertIsNotNone(ButtonLabels.PAUSE)
        self.assertIsNotNone(ButtonLabels.RESUME)
        self.assertIsNotNone(ButtonLabels.CONNECT)


if __name__ == "__main__":
    unittest.main()
