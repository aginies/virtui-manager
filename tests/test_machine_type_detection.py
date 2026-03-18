"""
Test for machine type detection functionality.
"""

import unittest
from unittest.mock import MagicMock, patch
import xml.etree.ElementTree as ET
import os
import sys

# Add the source directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vmanager.libvirt_utils import get_latest_machine_types


class TestMachineTypeDetection(unittest.TestCase):
    """Tests for detecting latest machine types."""

    def test_get_latest_machine_types_with_valid_capabilities(self):
        """Test that the function correctly parses and returns the latest machine types."""
        # Mock capabilities XML with various machine types
        caps_xml = """<?xml version="1.0"?>
<capabilities>
  <host>
    <uuid>test-uuid</uuid>
  </host>
  <guest>
    <os_type>hvm</os_type>
    <arch name="x86_64">
      <wordsize>64</wordsize>
      <emulator>/usr/bin/qemu-system-x86_64</emulator>
      <machine canonical="pc-i440fx-9.0">pc</machine>
      <machine>pc-i440fx-9.0</machine>
      <machine>pc-i440fx-8.2</machine>
      <machine>pc-i440fx-8.1</machine>
      <machine>pc-i440fx-7.2</machine>
      <machine>pc-q35-9.0</machine>
      <machine>pc-q35-8.2</machine>
      <machine>pc-q35-8.1</machine>
      <machine>pc-q35-7.2</machine>
      <domain type="kvm">
        <emulator>/usr/bin/qemu-system-x86_64</emulator>
      </domain>
    </arch>
  </guest>
</capabilities>"""

        # Create mock connection
        mock_conn = MagicMock()
        mock_conn.getCapabilities.return_value = caps_xml

        # Clear cache before testing
        get_latest_machine_types.cache_clear()

        # Call the function
        result = get_latest_machine_types(mock_conn, "x86_64")

        # Verify results
        self.assertEqual(result["pc-i440fx"], "pc-i440fx-9.0")
        self.assertEqual(result["pc-q35"], "pc-q35-9.0")

    def test_get_latest_machine_types_with_missing_arch(self):
        """Test fallback when architecture is not found."""
        caps_xml = """<?xml version="1.0"?>
<capabilities>
  <guest>
    <arch name="aarch64">
      <machine>virt-8.0</machine>
    </arch>
  </guest>
</capabilities>"""

        mock_conn = MagicMock()
        mock_conn.getCapabilities.return_value = caps_xml

        get_latest_machine_types.cache_clear()

        result = get_latest_machine_types(mock_conn, "x86_64")

        # Should return defaults when architecture not found
        self.assertEqual(result["pc-i440fx"], "pc-i440fx")
        self.assertEqual(result["pc-q35"], "pc-q35")

    def test_get_latest_machine_types_version_sorting(self):
        """Test that versions are correctly sorted."""
        caps_xml = """<?xml version="1.0"?>
<capabilities>
  <guest>
    <arch name="x86_64">
      <machine>pc-i440fx-10.1</machine>
      <machine>pc-i440fx-9.2</machine>
      <machine>pc-i440fx-9.10</machine>
      <machine>pc-q35-10.1</machine>
      <machine>pc-q35-9.2</machine>
      <machine>pc-q35-9.10</machine>
    </arch>
  </guest>
</capabilities>"""

        mock_conn = MagicMock()
        mock_conn.getCapabilities.return_value = caps_xml

        get_latest_machine_types.cache_clear()

        result = get_latest_machine_types(mock_conn, "x86_64")

        # 10.1 should be selected as latest
        self.assertEqual(result["pc-i440fx"], "pc-i440fx-10.1")
        self.assertEqual(result["pc-q35"], "pc-q35-10.1")

    def test_get_latest_machine_types_with_none_connection(self):
        """Test fallback when connection is None."""
        result = get_latest_machine_types(None, "x86_64")

        # Should return defaults
        self.assertEqual(result["pc-i440fx"], "pc-i440fx")
        self.assertEqual(result["pc-q35"], "pc-q35")

    def test_get_latest_machine_types_with_libvirt_error(self):
        """Test fallback when libvirt raises an error."""
        mock_conn = MagicMock()
        mock_conn.getCapabilities.side_effect = Exception("Libvirt error")

        get_latest_machine_types.cache_clear()

        result = get_latest_machine_types(mock_conn, "x86_64")

        # Should return defaults on error
        self.assertEqual(result["pc-i440fx"], "pc-i440fx")
        self.assertEqual(result["pc-q35"], "pc-q35")


if __name__ == "__main__":
    unittest.main()
