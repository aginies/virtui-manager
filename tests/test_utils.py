import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.utils import (
    check_novnc_path,
    check_r_viewer,
    check_websockify,
    extract_server_name_from_uri,
    format_uptime,
    get_server_color_cached,
    humanize_bytes,
    is_running_under_flatpak,
    sanitize_credentials,
    setup_cache_monitoring,
    setup_logging,
    snake_case_keys,
    strip_ansi_codes,
)


class TestUtils(unittest.TestCase):
    def test_strip_ansi_codes(self):
        """Test stripping ANSI escape codes."""
        # Test basic colors
        self.assertEqual(strip_ansi_codes("\x1b[31mRed Text\x1b[0m"), "Red Text")
        # Test bold and colors
        self.assertEqual(strip_ansi_codes("\x1b[1;32mBold Green\x1b[0m"), "Bold Green")
        # Test complex sequence from user report
        user_report = "\x1b[1;32m[\x1b]0;nixos@nixos: ~\x07nixos@nixos:~]$\x1b[0m"
        self.assertEqual(strip_ansi_codes(user_report), "[nixos@nixos:~]$")
        # Test normal text
        self.assertEqual(strip_ansi_codes("Normal Text"), "Normal Text")

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
        mock_which.side_effect = lambda x: (
            f"/usr/bin/{x}" if x in ["virt-viewer", "virtui-remote-viewer"] else None
        )

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
            setup_cache_monitoring()
            # If we get here without exception, the test passes
            self.assertTrue(True)
        except Exception as e:
            self.fail(f"setup_cache_monitoring raised an exception: {e}")


class TestHumanizeBytes(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(humanize_bytes(512), "512 B")

    def test_kib(self):
        self.assertEqual(humanize_bytes(1024), "1.0 KiB")

    def test_mib(self):
        self.assertEqual(humanize_bytes(10 * 1024 * 1024), "10.0 MiB")

    def test_gib(self):
        self.assertEqual(humanize_bytes(1.5 * 1024**3), "1.5 GiB")

    def test_none(self):
        self.assertEqual(humanize_bytes(None), "N/A")

    def test_zero(self):
        self.assertEqual(humanize_bytes(0), "0 B")


class TestFormatUptime(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_uptime(0), "00:00:00")

    def test_hours_minutes_seconds(self):
        self.assertEqual(format_uptime(3661), "01:01:01")

    def test_one_day(self):
        self.assertEqual(format_uptime(90061), "1 day, 01:01:01")

    def test_multiple_days(self):
        self.assertEqual(format_uptime(180000), "2 days, 02:00:00")


class TestSnakeCaseKeys(unittest.TestCase):
    def test_dict_keys(self):
        self.assertEqual(
            snake_case_keys({"total-bytes": 1, "pretty-name": "x"}),
            {"total_bytes": 1, "pretty_name": "x"},
        )

    def test_nested(self):
        self.assertEqual(
            snake_case_keys({"outer-key": {"inner-key": ["a-b"]}}),
            {"outer_key": {"inner_key": ["a-b"]}},
        )

    def test_non_container_passthrough(self):
        self.assertEqual(snake_case_keys("a-b"), "a-b")
        self.assertEqual(snake_case_keys(42), 42)
        self.assertIsNone(snake_case_keys(None))


class TestSanitizeCredentials(unittest.TestCase):
    """The hostname lookahead used to stop at a port, so the commonest real URI
    (``@host:port``) came back with its password intact."""

    def test_password_is_redacted(self):
        self.assertEqual(
            sanitize_credentials("qemu+ssh://admin:s3cret@host/system"),
            "qemu+ssh://admin:***@host/system",
        )

    def test_password_is_redacted_when_the_host_has_a_port(self):
        self.assertEqual(
            sanitize_credentials("qemu+ssh://admin:s3cret@192.168.1.5:16514/system"),
            "qemu+ssh://admin:***@192.168.1.5:16514/system",
        )

    def test_password_is_redacted_inside_quotes(self):
        # how the connection manager words its failure log
        message = "Failed to connect to 'qemu+ssh://admin:s3cret@host:22' (Attempt 1/3)"
        self.assertNotIn("s3cret", sanitize_credentials(message))

    def test_password_with_an_at_sign_is_redacted(self):
        self.assertEqual(
            sanitize_credentials("qemu+ssh://admin:p@ss@host/system"),
            "qemu+ssh://admin:***@host/system",
        )

    def test_a_username_without_a_password_is_left_alone(self):
        self.assertEqual(
            sanitize_credentials("qemu+ssh://admin@host:22/system"),
            "qemu+ssh://admin@host:22/system",
        )


if __name__ == "__main__":
    unittest.main()
