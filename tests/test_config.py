import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import tempfile
from pathlib import Path

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.config import load_config, save_config, get_log_path


class TestConfig(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for test config files
        self.temp_dir = tempfile.mkdtemp()
        self.config_dir = Path(self.temp_dir)
        self.config_path = self.config_dir / "config.yaml"

        # Mock get_config_paths to return our temp directory
        self.patcher = patch("vmanager.config.get_config_paths")
        self.mock_get_config_paths = self.patcher.start()
        self.mock_get_config_paths.return_value = [self.config_path]

    def tearDown(self):
        self.patcher.stop()
        # Clean up temp directory
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_config(self):
        """Test loading configuration."""
        config = load_config()
        self.assertIsInstance(config, dict)
        self.assertIn("servers", config)
        self.assertIn("LOG_LEVEL", config)
        self.assertIn("STATS_INTERVAL", config)

    def test_save_config(self):
        """Test saving configuration."""
        # Modify config
        test_config = {
            "test": "value",
            "servers": [{"name": "test-server", "uri": "qemu:///system"}],
            "LOG_LEVEL": "DEBUG",
        }

        # Save and reload
        save_config(test_config)
        loaded_config = load_config()

        # Verify
        self.assertEqual(loaded_config["test"], "value")
        self.assertEqual(
            loaded_config["servers"], [{"name": "test-server", "uri": "qemu:///system"}]
        )
        self.assertEqual(loaded_config["LOG_LEVEL"], "DEBUG")

    def test_get_log_path(self):
        """Test getting log file path."""
        log_path = get_log_path()
        self.assertIsInstance(log_path, Path)
        self.assertTrue(str(log_path).endswith(".log"))

    def test_config_defaults(self):
        """Test that config has expected default values."""
        config = load_config()

        # Test default values from src/vmanager/config.py
        self.assertEqual(config.get("LOG_LEVEL"), "INFO")
        self.assertEqual(config.get("STATS_INTERVAL"), 5)

if __name__ == "__main__":
    unittest.main()