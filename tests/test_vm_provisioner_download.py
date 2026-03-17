"""
Tests for VMProvisioner ISO download and validation logic.
"""

import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys
import tempfile
import shutil
from pathlib import Path
import libvirt
import urllib.error

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vm_provisioner import VMProvisioner
from vmanager.constants import StaticText

class TestVMProvisionerDownload(unittest.TestCase):
    def setUp(self):
        self.mock_conn = MagicMock()
        with patch("vmanager.vm_provisioner.get_host_architecture") as mock_get_arch:
            mock_get_arch.return_value = "x86_64"
            self.provisioner = VMProvisioner(self.mock_conn)
        
        self.test_url = "https://example.com/test.iso"
        self.temp_dir = tempfile.mkdtemp()
        self.dest_path = os.path.join(self.temp_dir, "test.iso")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @patch("vmanager.vm_provisioner.urllib.request.urlopen")
    @patch("vmanager.vm_provisioner.urllib.request.Request")
    def test_download_iso_success(self, mock_request, mock_urlopen):
        """Test successful ISO download."""
        # Mock response
        mock_response = MagicMock()
        mock_response.getheader.return_value = "100"
        # Return 10 bytes then empty
        mock_response.read.side_effect = [b"0123456789", b""]
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        # Mock progress callback
        progress_cb = MagicMock()

        result = self.provisioner.download_iso(
            self.test_url, 
            dest_path=self.dest_path, 
            progress_callback=progress_cb
        )

        self.assertEqual(result, self.dest_path)
        self.assertTrue(os.path.exists(self.dest_path))
        with open(self.dest_path, "rb") as f:
            self.assertEqual(f.read(), b"0123456789")
        
        # Verify progress callback was called
        self.assertTrue(progress_cb.called)

    def test_download_iso_existing(self):
        """Test skipping download if ISO already exists."""
        # Create a dummy file
        with open(self.dest_path, "wb") as f:
            f.write(b"existing")
        
        progress_cb = MagicMock()
        
        # Should return existing path without calling urlopen
        with patch("vmanager.vm_provisioner.urllib.request.urlopen") as mock_urlopen:
            result = self.provisioner.download_iso(
                self.test_url, 
                dest_path=self.dest_path,
                progress_callback=progress_cb
            )
            self.assertEqual(result, self.dest_path)
            self.assertFalse(mock_urlopen.called)
            
        # Verify progress callback was called with 100%
        progress_cb.assert_called_with("ISO already exists", 100)

    def test_download_iso_invalid_url(self):
        """Test download with invalid URL protocol."""
        with self.assertRaises(ValueError):
            self.provisioner.download_iso("ftp://example.com/test.iso")

    @patch("vmanager.vm_provisioner.urllib.request.urlopen")
    def test_download_iso_failure_cleanup(self, mock_urlopen):
        """Test partial file cleanup on download failure."""
        mock_urlopen.side_effect = Exception("Download failed")
        
        with self.assertRaises(Exception) as cm:
            self.provisioner.download_iso(self.test_url, dest_path=self.dest_path)
        
        self.assertIn("Download failed", str(cm.exception))
        self.assertFalse(os.path.exists(self.dest_path))

    @patch("vmanager.vm_provisioner.urllib.request.urlopen")
    def test_download_iso_temp_path(self, mock_urlopen):
        """Test download with automatic temporary path generation."""
        # Mock response
        mock_response = MagicMock()
        mock_response.getheader.return_value = "5"
        mock_response.read.side_effect = [b"hello", b""]
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        result = self.provisioner.download_iso(self.test_url)
        
        try:
            # Should be in a temp dir
            self.assertTrue(os.path.exists(result))
            self.assertTrue(result.endswith("test.iso"))
            with open(result, "rb") as f:
                self.assertEqual(f.read(), b"hello")
        finally:
            # Clean up the automatically created temp dir
            temp_dir = os.path.dirname(result)
            if os.path.exists(temp_dir) and "virtui_iso_" in temp_dir:
                shutil.rmtree(temp_dir)

    @patch("vmanager.vm_provisioner.hashlib.sha256")
    @patch("builtins.open", new_callable=mock_open, read_data=b"iso content")
    @patch("os.path.exists")
    def test_validate_iso_match(self, mock_exists, mock_file, mock_sha):
        """Test ISO validation with matching checksum."""
        mock_exists.return_value = True
        
        # Mock sha256
        mock_sha_inst = mock_sha.return_value
        mock_sha_inst.hexdigest.return_value = "abcdef1234"
        
        result = self.provisioner.validate_iso("/dummy/path.iso", "ABCDEF1234")
        self.assertTrue(result)

    @patch("vmanager.vm_provisioner.hashlib.sha256")
    @patch("builtins.open", new_callable=mock_open, read_data=b"iso content")
    @patch("os.path.exists")
    def test_validate_iso_mismatch(self, mock_exists, mock_file, mock_sha):
        """Test ISO validation with mismatching checksum."""
        mock_exists.return_value = True
        
        # Mock sha256
        mock_sha_inst = mock_sha.return_value
        mock_sha_inst.hexdigest.return_value = "abcdef1234"
        
        result = self.provisioner.validate_iso("/dummy/path.iso", "wrong")
        self.assertFalse(result)

    @patch("os.path.exists")
    def test_validate_iso_not_found(self, mock_exists):
        """Test ISO validation when file doesn't exist."""
        mock_exists.return_value = False
        result = self.provisioner.validate_iso("/dummy/path.iso", "any")
        self.assertFalse(result)

    def test_format_download_speed(self):
        """Test download speed formatting."""
        self.assertEqual(self.provisioner._format_download_speed(500), "500.0 B")
        self.assertEqual(self.provisioner._format_download_speed(1500), "1.5 KB")
        self.assertEqual(self.provisioner._format_download_speed(1500000), "1.4 MB")
        self.assertEqual(self.provisioner._format_download_speed(1500000000), "1.4 GB")

    @patch("vmanager.vm_provisioner.os.path.exists")
    @patch("vmanager.vm_provisioner.os.path.getsize")
    @patch("builtins.open", new_callable=mock_open, read_data=b"file content")
    def test_upload_file_success(self, mock_file, mock_getsize, mock_exists):
        """Test successful file upload to storage pool."""
        mock_exists.return_value = True
        mock_getsize.return_value = 12 # len(b"file content")
        
        # Mock pool
        mock_pool = MagicMock()
        mock_pool.isActive.return_value = True
        # Volume doesn't exist initially
        mock_pool.storageVolLookupByName.side_effect = libvirt.libvirtError("Not found")
        
        # Mock volume creation
        mock_vol = MagicMock()
        mock_pool.createXML.return_value = mock_vol
        mock_vol.path.return_value = "/var/lib/libvirt/images/test.iso"
        
        self.mock_conn.storagePoolLookupByName.return_value = mock_pool
        self.mock_conn.getKeepAlive.return_value = [5, 5]
        
        # Mock stream
        mock_stream = MagicMock()
        self.mock_conn.newStream.return_value = mock_stream
        
        progress_cb = MagicMock()
        
        result = self.provisioner.upload_file(
            "/local/path/test.iso", 
            "default", 
            progress_callback=progress_cb
        )
        
        self.assertEqual(result, "/var/lib/libvirt/images/test.iso")
        self.assertTrue(mock_pool.createXML.called)
        self.assertTrue(mock_vol.upload.called)
        self.assertTrue(mock_stream.send.called)
        self.assertTrue(mock_stream.finish.called)
        self.assertTrue(progress_cb.called)

if __name__ == "__main__":
    import sys
    unittest.main()
