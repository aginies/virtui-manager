"""
Tests for Alpine Linux OS Provider
"""

import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys
import os

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.provisioning.providers.alpine_provider import AlpineProvider, AlpineDistro
from vmanager.provisioning.os_provider import OSType, OSVersion


class TestAlpineProvider(unittest.TestCase):
    def setUp(self):
        self.provider = AlpineProvider(host_arch="x86_64")

    def test_os_type(self):
        """Test that the provider returns the correct OS type"""
        self.assertEqual(self.provider.os_type, OSType.ALPINE)

    def test_get_supported_versions(self):
        """Test that supported versions are returned correctly"""
        versions = self.provider.get_supported_versions()
        self.assertTrue(len(versions) >= 3)
        self.assertEqual(versions[0].version_id, "3.23")
        self.assertEqual(versions[0].os_type, OSType.ALPINE)

    def test_get_iso_sources(self):
        """Test that the ISO sources URL is correct"""
        version = OSVersion(OSType.ALPINE, "3.23", "Alpine Linux 3.23", "x86_64")
        sources = self.provider.get_iso_sources(version)
        self.assertIn("https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/x86_64/", sources)

    @patch("urllib.request.urlopen")
    def test_get_iso_list(self, mock_urlopen):
        """Test fetching the ISO list from the mirror"""
        # Mock HTML response
        mock_response = MagicMock()
        mock_response.read.return_value = b"""
        <html>
        <body>
        <a href="alpine-virt-3.23.0-x86_64.iso">alpine-virt-3.23.0-x86_64.iso</a>
        <a href="alpine-standard-3.23.0-x86_64.iso">alpine-standard-3.23.0-x86_64.iso</a>
        </body>
        </html>
        """
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        # Mock HEAD request for metadata
        mock_head_res = MagicMock()
        mock_head_res.getheader.side_effect = lambda h: "104857600" if h == "Content-Length" else "Mon, 27 Jan 2026 12:00:00 GMT"
        mock_head_res.__enter__.return_value = mock_head_res
        
        # This is simplified, in reality it would be called multiple times
        mock_urlopen.side_effect = [mock_response, mock_head_res, mock_head_res]

        iso_list = self.provider.get_iso_list("3.23")
        self.assertEqual(len(iso_list), 2)
        self.assertEqual(iso_list[0]["name"], "alpine-virt-3.23.0-x86_64.iso")
        self.assertEqual(iso_list[0]["size"], "100 MB")

    def test_generate_basic_answers(self):
        """Test generating a basic answers file content"""
        config = {
            "vm_name": "test-alpine",
            "username": "tester",
            "password": "testpassword",
            "root_password": "rootpassword"
        }
        content = self.provider._generate_basic_answers(config)
        self.assertIn('HOSTNAMEOPTS="-n test-alpine"', content)
        self.assertIn('USEROPTS="-a -u tester -g \'Alpine User\' testpassword"', content)
        self.assertIn('ROOTOPTS="rootpassword"', content)

    @patch("os.open")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_generate_automation_file_default(self, mock_file, mock_os_open):
        """Test generating the automation file using the default template"""
        # Mock template finding to return None so it uses basic answers
        with patch.object(self.provider, "_find_template_file", return_value=None):
            output_path = Path("/tmp")
            user_config = {"username": "alpine", "password": "password"}
            
            result_path = self.provider.generate_automation_file(
                None, "testvm", user_config, output_path
            )
            
            self.assertEqual(result_path, output_path / "answers.txt")
            mock_file.assert_called()


if __name__ == "__main__":
    unittest.main()
