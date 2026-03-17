"""
Tests for VManagerCMD class.
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vmanager_cmd import VManagerCMD

class TestVManagerCMD(unittest.TestCase):
    @patch("vmanager.vmanager_cmd.load_config")
    @patch("vmanager.vmanager_cmd.get_log_path")
    @patch("vmanager.vmanager_cmd.CLILogger")
    def setUp(self, mock_logger, mock_log_path, mock_load_config):
        # Mock configuration
        self.mock_config = {
            "servers": [
                {"name": "local", "uri": "qemu:///system", "autoconnect": False},
                {"name": "remote", "uri": "qemu+ssh://user@remote/system", "autoconnect": False}
            ]
        }
        mock_load_config.return_value = self.mock_config
        mock_log_path.return_value = "/tmp/vmanager_test.log"
        
        # Mock VMService
        self.mock_vm_service = MagicMock()
        
        # Initialize VManagerCMD with mocked VMService
        # Capture stdout to avoid printing during tests
        with patch("sys.stdout", MagicMock()):
            self.cmd = VManagerCMD(vm_service=self.mock_vm_service)

    def test_connect_by_name(self):
        """Test connecting to a configured server by name."""
        self.mock_vm_service.connect.return_value = MagicMock()
        
        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect("local")
            
        self.mock_vm_service.connect.assert_called_with("qemu:///system")
        self.assertIn("local", self.cmd.active_connections)

    def test_connect_by_uri(self):
        """Test connecting directly to a URI."""
        self.mock_vm_service.connect.return_value = MagicMock()
        
        with patch("sys.stdout", MagicMock()):
            # Direct URI connection
            self.cmd.do_connect("qemu:///system")
            
        self.mock_vm_service.connect.assert_called_with("qemu:///system")
        # For qemu:///system, extract_server_name_from_uri returns "Local"
        self.assertIn("Local", self.cmd.active_connections)

    def test_connect_by_remote_uri(self):
        """Test connecting directly to a remote URI."""
        self.mock_vm_service.connect.return_value = MagicMock()
        
        uri = "qemu+ssh://user@another-host/system"
        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect(uri)
            
        self.mock_vm_service.connect.assert_called_with(uri)
        # extract_server_name_from_uri should return "another-host"
        self.assertIn("another-host", self.cmd.active_connections)

    def test_connect_multiple(self):
        """Test connecting to multiple targets (name and URI)."""
        self.mock_vm_service.connect.return_value = MagicMock()
        
        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect("remote qemu:///system")
            
        self.assertEqual(self.mock_vm_service.connect.call_count, 2)
        self.assertIn("remote", self.cmd.active_connections)
        self.assertIn("Local", self.cmd.active_connections)

    def test_connect_all(self):
        """Test 'connect all' command."""
        self.mock_vm_service.connect.return_value = MagicMock()
        
        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect("all")
            
        self.assertEqual(self.mock_vm_service.connect.call_count, 2)
        self.assertIn("local", self.cmd.active_connections)
        self.assertIn("remote", self.cmd.active_connections)

    def test_connect_already_connected(self):
        """Test connecting to an already connected server."""
        self.cmd.active_connections["local"] = MagicMock()
        
        with patch("sys.stdout", MagicMock()) as mock_stdout:
            self.cmd.do_connect("local")
            
        # Should not try to connect again
        self.assertEqual(self.mock_vm_service.connect.call_count, 0)

    def test_connect_invalid_name(self):
        """Test connecting to an invalid server name."""
        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect("nonexistent")
            
        self.assertEqual(self.mock_vm_service.connect.call_count, 0)
        self.assertEqual(len(self.cmd.active_connections), 0)

    def test_complete_connect(self):
        """Test completion for connect command."""
        # Configured: local, remote
        # We also add "all" in the completion
        completions = self.cmd.complete_connect("loc", "connect loc", 8, 11)
        self.assertEqual(completions, ["local"])
        
        completions = self.cmd.complete_connect("", "connect ", 8, 8)
        # Should include all configured servers + "all"
        expected = sorted(["local", "remote", "all"])
        self.assertEqual(completions, expected)

    def test_complete_connect_with_active(self):
        """Test completion for connect command includes active connections."""
        self.cmd.active_connections["ActiveServer"] = MagicMock()
        
        completions = self.cmd.complete_connect("Act", "connect Act", 8, 11)
        self.assertIn("ActiveServer", completions)

if __name__ == "__main__":
    unittest.main()
