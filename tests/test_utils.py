import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import tempfile
import shutil
from pathlib import Path

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.utils import (
    check_novnc_path,
    check_r_viewer,
    check_websockify,
    get_server_color_cached,
    is_running_under_flatpak,
    setup_logging,
    setup_cache_monitoring,
    extract_server_name_from_uri,
)


class TestUtils(unittest.TestCase):
    def test_extract_server_name_from_uri(self):
        """Test extracting server name from URI."""
        # Test various URI formats
        uri1 = "qemu+ssh://user@host/system"
        name1 = extract_server_name_from_uri(uri1)
        self.assertEqual(name1, "host")

        uri2 = "qemu:///system"
        name2 = extract_server_name_from_uri(uri2)
        self.assertEqual(name2, "Local")

        uri3 = "qemu+unix:///system"
        name3 = extract_server_name_from_uri(uri3)
        # Based on extract_server_name_from_uri implementation for generic URIs
        self.assertEqual(name3, "qemu+unix:///system")

        uri4 = "esx://192.168.1.100"
        name4 = extract_server_name_from_uri(uri4)
        self.assertEqual(name4, "192.168.1.100")

    def test_is_running_under_flatpak(self):
        """Test checking if running under flatpak."""
        # This should return a boolean, we can't test the actual environment
        result = is_running_under_flatpak()
        self.assertIsInstance(result, bool)

    @patch("vmanager.utils.os.path.exists")
    def test_check_novnc_path(self, mock_exists):
        """Test checking novnc path."""
        # Mock that the path exists
        mock_exists.return_value = True
        result = check_novnc_path()
        self.assertTrue(result)

        # Mock that the path does not exist
        mock_exists.return_value = False
        result = check_novnc_path()
        self.assertFalse(result)

    @patch("vmanager.utils.shutil.which")
    def test_check_websockify(self, mock_which):
        """Test checking websockify path."""
        # Mock that the path exists
        mock_which.return_value = "/usr/bin/websockify"
        result = check_websockify()
        self.assertTrue(result)

        # Mock that the path does not exist
        mock_which.return_value = None
        result = check_websockify()
        self.assertFalse(result)

    @patch("vmanager.utils.shutil.which")
    def test_check_r_viewer(self, mock_which):
        """Test checking remote viewer."""
        # Mock that the path exists
        mock_which.side_effect = lambda x: f"/usr/bin/{x}" if x in ["virt-viewer", "virtui-remote-viewer"] else None
        
        # Test with configured viewer
        result = check_r_viewer("virt-viewer")
        self.assertEqual(result, "virt-viewer")

        # Test with no configured viewer (auto-detection)
        result = check_r_viewer()
        self.assertEqual(result, "virtui-remote-viewer")

        # Mock that none exist
        mock_which.side_effect = None
        mock_which.return_value = None
        result = check_r_viewer()
        self.assertIsNone(result)

    def test_get_server_color_cached(self):
        """Test getting cached server color."""
        # Test with a simple URI
        uri = "qemu:///system"
        palette = ("#ff0000", "#00ff00", "#0000ff")
        color1 = get_server_color_cached(uri, palette)
        color2 = get_server_color_cached(uri, palette)

        # Should return the same color for the same URI
        self.assertEqual(color1, color2)

        # Should cycle through available colors
        uri2 = "qemu+ssh://user@otherhost/system"
        color3 = get_server_color_cached(uri2, palette)
        self.assertIn(color3, palette)
        self.assertNotEqual(color1, color3)

    def test_setup_logging(self):
        """Test setting up logging."""
        # This test mainly checks that it doesn't raise an exception
        # We need to mock get_log_path because it tries to create directories
        with patch("vmanager.config.get_log_path") as mock_get_log_path:
            with tempfile.TemporaryDirectory() as tmpdir:
                log_file = Path(tmpdir) / "test.log"
                mock_get_log_path.return_value = log_file
                try:
                    setup_logging()
                    # If we get here without exception, the test passes
                    self.assertTrue(True)
                except Exception as e:
                    self.fail(f"setup_logging raised an exception: {e}")

    def test_setup_cache_monitoring(self):
        """Test setting up cache monitoring."""
        # This test mainly checks that it doesn't raise an exception
        try:
            result = setup_cache_monitoring()
            # If we get here without exception, the test passes
            self.assertTrue(True)
        except Exception as e:
            self.fail(f"setup_cache_monitoring raised an exception: {e}")


if __name__ == "__main__":
    unittest.main()
