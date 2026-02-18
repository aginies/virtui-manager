"""
Tests for VMProvisioner NVRAM setup logic
"""

import unittest
from unittest.mock import patch, MagicMock, mock_open
import sys
import os
import libvirt

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vm_provisioner import VMProvisioner, VMType
from vmanager.firmware_manager import Firmware


class TestVMProvisionerNVRAM(unittest.TestCase):
    def setUp(self):
        self.mock_conn = MagicMock(spec=libvirt.virConnect)
        with patch("vmanager.vm_provisioner.get_host_architecture") as mock_get_arch:
            mock_get_arch.return_value = "x86_64"
            self.provisioner = VMProvisioner(self.mock_conn)
        self.provisioner.host_arch = "x86_64"

    def _setup_common_mocks(self, mock_get_uefi, mock_select_fw):
        mock_get_uefi.return_value = []

        # Pools
        self.mock_temp_pool = MagicMock()
        self.mock_target_pool = MagicMock()

        def pool_lookup(name):
            if "virtui-fw-" in name:
                # Cleanup check usually fails in our test or returns temp pool
                raise libvirt.libvirtError("Not found")
            return self.mock_target_pool

        self.mock_conn.storagePoolLookupByName.side_effect = pool_lookup
        self.mock_conn.storagePoolDefineXML.return_value = self.mock_temp_pool

        # Volumes
        self.mock_source_vol = MagicMock()
        self.mock_source_vol.info.return_value = [0, 1024, 0]
        self.mock_temp_pool.storageVolLookupByName.return_value = self.mock_source_vol

        # Target volume (does not exist initially)
        self.mock_target_pool.storageVolLookupByName.side_effect = libvirt.libvirtError("Not found")
        self.mock_target_vol = MagicMock()
        self.mock_target_pool.createXML.return_value = self.mock_target_vol

        # Stream
        self.mock_stream = MagicMock()
        self.mock_stream.recv.side_effect = [b"raw_content", b""]
        self.mock_conn.newStream.return_value = self.mock_stream

    @patch("vmanager.vm_provisioner.get_uefi_files")
    @patch("vmanager.vm_provisioner.select_best_firmware")
    @patch("vmanager.vm_provisioner.subprocess.run")
    @patch("vmanager.vm_provisioner.tempfile.NamedTemporaryFile")
    @patch("vmanager.vm_provisioner.os.remove")
    @patch("vmanager.vm_provisioner.os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data=b"mock_nvram_content")
    def test_setup_uefi_nvram_conversion_qcow2(
        self, mock_file, mock_exists, mock_remove, mock_tempfile, mock_run, mock_select_fw, mock_get_uefi
    ):
        """Test that NVRAM is always created in QCOW2 format"""
        self._setup_common_mocks(mock_get_uefi, mock_select_fw)
        mock_exists.return_value = True # Simulate file exists for removal

        fw = Firmware()
        fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
        fw.nvram_template = "/usr/share/OVMF/OVMF_VARS.fd"
        fw.interfaces = ["pflash"]
        mock_select_fw.return_value = fw

        self.mock_target_vol.path.return_value = "/path/to/testvm_VARS.qcow2"

        mock_temp = MagicMock()
        mock_temp.name = "/tmp/temp_input.raw"
        mock_tempfile.return_value.__enter__.return_value = mock_temp

        loader, nvram = self.provisioner._setup_uefi_nvram(
            "testvm", "default", VMType.DESKTOP, support_snapshots=True
        )

        self.assertEqual(nvram, "/path/to/testvm_VARS.qcow2")
        self.assertTrue(mock_run.called)
        args = mock_run.call_args[0][0]
        self.assertIn("qcow2", args)

    @patch("vmanager.vm_provisioner.get_uefi_files")
    @patch("vmanager.vm_provisioner.select_best_firmware")
    @patch("vmanager.vm_provisioner.subprocess.run")
    @patch("vmanager.vm_provisioner.tempfile.NamedTemporaryFile")
    @patch("vmanager.vm_provisioner.os.remove")
    @patch("vmanager.vm_provisioner.os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data=b"mock_nvram_content")
    def test_setup_uefi_nvram_qcow2_always_used(
        self, mock_file, mock_exists, mock_remove, mock_tempfile, mock_run, mock_select_fw, mock_get_uefi
    ):
        """Test that NVRAM is always created in QCOW2 format even when support_snapshots=False"""
        self._setup_common_mocks(mock_get_uefi, mock_select_fw)
        mock_exists.return_value = True

        fw = Firmware()
        fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
        fw.nvram_template = "/usr/share/OVMF/OVMF_VARS.fd"
        fw.interfaces = ["pflash"]
        mock_select_fw.return_value = fw

        self.mock_target_vol.path.return_value = "/path/to/testvm_VARS.qcow2"
        
        mock_temp = MagicMock()
        mock_temp.name = "/tmp/temp_input.raw"
        mock_tempfile.return_value.__enter__.return_value = mock_temp

        loader, nvram = self.provisioner._setup_uefi_nvram(
            "testvm", "default", VMType.DESKTOP, support_snapshots=False
        )

        # Even with support_snapshots=False, NVRAM should be QCOW2
        self.assertEqual(nvram, "/path/to/testvm_VARS.qcow2")
        self.assertTrue(mock_run.called)
        args = mock_run.call_args[0][0]
        self.assertIn("qcow2", args)

    @patch("vmanager.vm_provisioner.get_uefi_files")
    @patch("vmanager.vm_provisioner.select_best_firmware")
    @patch("vmanager.vm_provisioner.subprocess.run")
    @patch("vmanager.vm_provisioner.tempfile.NamedTemporaryFile")
    @patch("vmanager.vm_provisioner.os.remove")
    @patch("vmanager.vm_provisioner.os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data=b"mock_converted_content")
    def test_setup_uefi_nvram_conversion_nopflash(
        self, mock_file, mock_exists, mock_remove, mock_tempfile, mock_run, mock_select_fw, mock_get_uefi
    ):
        """Test NVRAM conversion for non-pflash firmware still uses QCOW2"""
        self._setup_common_mocks(mock_get_uefi, mock_select_fw)
        mock_exists.return_value = True

        fw = Firmware()
        fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
        fw.nvram_template = "/usr/share/OVMF/OVMF_VARS.fd"
        fw.interfaces = ["uefi"]  # Not pflash
        mock_select_fw.return_value = fw

        self.mock_target_vol.path.return_value = "/path/to/testvm_VARS.qcow2"

        mock_temp = MagicMock()
        mock_temp.name = "/tmp/temp_input.raw"
        mock_tempfile.return_value.__enter__.return_value = mock_temp

        loader, nvram = self.provisioner._setup_uefi_nvram(
            "testvm", "default", VMType.DESKTOP, support_snapshots=False
        )

        # Should still convert to QCOW2
        self.assertEqual(nvram, "/path/to/testvm_VARS.qcow2")
        self.assertTrue(mock_run.called)
        args = mock_run.call_args[0][0]
        self.assertIn("qcow2", args)

    @patch("vmanager.vm_provisioner.get_uefi_files")
    @patch("vmanager.vm_provisioner.select_best_firmware")
    def test_setup_uefi_nvram_fallback_to_auto(self, mock_select_fw, mock_get_uefi):
        """Test that if no firmware with NVRAM template is found, return None, None"""
        mock_get_uefi.return_value = []

        # Scenario: Firmware found, but no template (and inference failed or N/A)
        fw = Firmware()
        fw.executable = "/usr/share/OVMF/OVMF.fd"
        fw.nvram_template = None
        mock_select_fw.return_value = fw

        # Execute
        loader, nvram = self.provisioner._setup_uefi_nvram("testvm", "default", VMType.DESKTOP)

        # Should return None, None to let libvirt auto-select
        self.assertIsNone(loader)
        self.assertIsNone(nvram)


if __name__ == "__main__":
    unittest.main()
