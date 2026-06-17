"""
Tests for VMProvisioner NVRAM setup logic
"""

import unittest
from unittest.mock import patch, MagicMock, mock_open
import sys
import os
from pathlib import Path
import libvirt

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vm_provisioner import VMProvisioner, VMType
from vmanager.provisioning.os_provider import OSType
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
    def test_setup_uefi_nvram_creates_raw_format(
        self,
        mock_select_fw,
        mock_get_uefi,
    ):
        """Test that NVRAM is created in raw format without qemu-img conversion"""
        self._setup_common_mocks(mock_get_uefi, mock_select_fw)

        fw = Firmware()
        fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
        fw.nvram_template = "/usr/share/OVMF/OVMF_VARS.fd"
        fw.interfaces = ["pflash"]
        mock_select_fw.return_value = fw

        self.mock_target_vol.path.return_value = "/path/to/testvm_VARS.fd"

        loader, nvram = self.provisioner._setup_uefi_nvram(
            "testvm", "default", VMType.DESKTOP, support_snapshots=True
        )

        self.assertEqual(loader, "/usr/share/OVMF/OVMF_CODE.fd")
        self.assertEqual(nvram, "/path/to/testvm_VARS.fd")
        # Verify volume was created in target pool
        self.mock_target_pool.createXML.assert_called_once()
        # Verify the volume XML uses raw format
        vol_xml = self.mock_target_pool.createXML.call_args[0][0]
        self.assertIn("raw", vol_xml)

    @patch("vmanager.vm_provisioner.get_uefi_files")
    @patch("vmanager.vm_provisioner.select_best_firmware")
    def test_setup_uefi_nvram_raw_regardless_of_snapshots(
        self,
        mock_select_fw,
        mock_get_uefi,
    ):
        """Test that NVRAM is created in raw format regardless of support_snapshots flag"""
        self._setup_common_mocks(mock_get_uefi, mock_select_fw)

        fw = Firmware()
        fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
        fw.nvram_template = "/usr/share/OVMF/OVMF_VARS.fd"
        fw.interfaces = ["pflash"]
        mock_select_fw.return_value = fw

        self.mock_target_vol.path.return_value = "/path/to/testvm_VARS.fd"

        loader, nvram = self.provisioner._setup_uefi_nvram(
            "testvm", "default", VMType.DESKTOP, support_snapshots=False
        )

        # NVRAM should use raw format (no qemu-img conversion)
        self.assertEqual(nvram, "/path/to/testvm_VARS.fd")
        self.mock_target_pool.createXML.assert_called_once()
        vol_xml = self.mock_target_pool.createXML.call_args[0][0]
        self.assertIn("raw", vol_xml)

    @patch("vmanager.vm_provisioner.get_uefi_files")
    @patch("vmanager.vm_provisioner.select_best_firmware")
    @patch("vmanager.vm_provisioner.subprocess.run")
    @patch("vmanager.vm_provisioner.tempfile.NamedTemporaryFile")
    @patch("vmanager.vm_provisioner.os.remove")
    @patch("vmanager.vm_provisioner.os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data=b"mock_nvram_content")
    def test_setup_uefi_nvram_uses_nvram_pool(
        self,
        mock_file,
        mock_exists,
        mock_remove,
        mock_tempfile,
        mock_run,
        mock_select_fw,
        mock_get_uefi,
    ):
        """Test that 'nvram' pool is used if it exists and is active"""
        mock_get_uefi.return_value = []

        # Mocks for connection and pools
        self.mock_nvram_pool = MagicMock()
        self.mock_nvram_pool.isActive.return_value = True
        self.mock_target_pool = MagicMock()
        self.mock_temp_pool = MagicMock()

        def pool_lookup(name):
            if name == "nvram":
                return self.mock_nvram_pool
            if "virtui-fw-" in name:
                raise libvirt.libvirtError("Not found")
            return self.mock_target_pool

        self.mock_conn.storagePoolLookupByName.side_effect = pool_lookup
        self.mock_conn.storagePoolDefineXML.return_value = self.mock_temp_pool

        # Stream mock to avoid infinite loop in while True: data = stream.recv()
        self.mock_stream = MagicMock()
        self.mock_stream.recv.side_effect = [b"raw_content", b""]
        self.mock_conn.newStream.return_value = self.mock_stream

        # Volumes
        self.mock_source_vol = MagicMock()
        self.mock_source_vol.info.return_value = [0, 1024, 0]
        self.mock_temp_pool.storageVolLookupByName.return_value = self.mock_source_vol

        # NVRAM volume setup
        self.mock_nvram_pool.storageVolLookupByName.side_effect = libvirt.libvirtError("Not found")
        self.mock_nvram_vol = MagicMock()
        self.mock_nvram_pool.createXML.return_value = self.mock_nvram_vol
        self.mock_nvram_vol.path.return_value = "/var/lib/libvirt/nvram/testvm_VARS.qcow2"

        mock_exists.return_value = True
        fw = Firmware()
        fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
        fw.nvram_template = "/usr/share/OVMF/OVMF_VARS.fd"
        fw.interfaces = ["pflash"]
        mock_select_fw.return_value = fw

        mock_temp = MagicMock()
        mock_temp.name = "/tmp/temp_input.raw"
        mock_tempfile.return_value.__enter__.return_value = mock_temp

        loader, nvram = self.provisioner._setup_uefi_nvram("testvm", "default", VMType.DESKTOP)

        # Verify nvram path is from nvram pool
        self.assertEqual(nvram, "/var/lib/libvirt/nvram/testvm_VARS.qcow2")
        # Verify createXML was called on nvram pool, not target pool
        self.mock_nvram_pool.createXML.assert_called()
        self.mock_target_pool.createXML.assert_not_called()

    @patch("vmanager.vm_provisioner.get_uefi_files")
    @patch("vmanager.vm_provisioner.select_best_firmware")
    def test_setup_uefi_nvram_nopflash_uses_raw(
        self,
        mock_select_fw,
        mock_get_uefi,
    ):
        """Test NVRAM for non-pflash firmware uses raw format without conversion"""
        self._setup_common_mocks(mock_get_uefi, mock_select_fw)

        fw = Firmware()
        fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
        fw.nvram_template = "/usr/share/OVMF/OVMF_VARS.fd"
        fw.interfaces = ["uefi"]  # Not pflash
        mock_select_fw.return_value = fw

        self.mock_target_vol.path.return_value = "/path/to/testvm_VARS.fd"

        loader, nvram = self.provisioner._setup_uefi_nvram(
            "testvm", "default", VMType.DESKTOP, support_snapshots=False
        )

        # Should use raw format regardless of interface type
        self.assertEqual(nvram, "/path/to/testvm_VARS.fd")
        self.mock_target_pool.createXML.assert_called_once()
        vol_xml = self.mock_target_pool.createXML.call_args[0][0]
        self.assertIn("raw", vol_xml)

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

    @patch("vmanager.vm_provisioner.get_uefi_files")
    @patch("vmanager.vm_provisioner.select_best_firmware")
    @patch("vmanager.vm_provisioner.subprocess.run")
    @patch("vmanager.vm_provisioner.tempfile.NamedTemporaryFile")
    @patch("vmanager.vm_provisioner.os.remove")
    @patch("vmanager.vm_provisioner.os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data=b"mock_nvram_content")
    def test_setup_uefi_nvram_alpine_disables_secure_boot(
        self,
        mock_file,
        mock_exists,
        mock_remove,
        mock_tempfile,
        mock_run,
        mock_select_fw,
        mock_get_uefi,
    ):
        """Test that Secure Boot is disabled for Alpine Linux even if VMType.SECURE is used"""
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

        # Call with VMType.SECURE and OSType.ALPINE
        loader, nvram = self.provisioner._setup_uefi_nvram(
            "testvm", "default", VMType.SECURE, os_type=OSType.ALPINE
        )

        # Assert select_best_firmware was called with secure_boot=False
        args, kwargs = mock_select_fw.call_args
        self.assertFalse(kwargs["secure_boot"], "Secure Boot should be False for Alpine Linux")

    def test_generate_xml_alpine_uefi_no_secure_boot(self):
        """Test that generated XML for Alpine Linux with UEFI has secure='no'"""
        xml = self.provisioner.generate_xml(
            vm_name="alpinevm",
            vm_type=VMType.SECURE,  # Even with SECURE type
            disk_path="/path/to/disk",
            iso_path="/path/to/iso",
            boot_uefi=True,
            os_type=OSType.ALPINE,
        )

        # Check that it contains UEFI firmware autoselection with secure-boot disabled
        self.assertIn("<firmware>", xml)
        self.assertIn("<feature enabled='no' name='secure-boot'/>", xml)
        self.assertIn("firmware='efi'", xml)
        self.assertNotIn("<loader", xml)
        # Check that SEV/TPM are NOT present (since we disabled them for Alpine)
        self.assertNotIn("<launchSecurity", xml)
        self.assertNotIn("<tpm", xml)

    def test_generate_xml_arch_uefi_no_secure_boot(self):
        """Test that generated XML for Arch Linux with UEFI has secure='no'"""
        xml = self.provisioner.generate_xml(
            vm_name="archvm",
            vm_type=VMType.SECURE,  # Even with SECURE type
            disk_path="/path/to/disk",
            iso_path="/path/to/iso",
            boot_uefi=True,
            os_type=OSType.ARCHLINUX,
        )

        # Check that it contains UEFI firmware autoselection with secure-boot disabled
        self.assertIn("<firmware>", xml)
        self.assertIn("<feature enabled='no' name='secure-boot'/>", xml)
        self.assertIn("firmware='efi'", xml)
        self.assertNotIn("<loader", xml)
        # Check that SEV/TPM are NOT present (since we disabled them for Arch)
        self.assertNotIn("<launchSecurity", xml)
        self.assertNotIn("<tpm", xml)

    def test_generate_xml_vnc_graphics(self):
        """Test that generated XML with VNC graphics has correct type"""
        xml = self.provisioner.generate_xml(
            vm_name="vncvm",
            vm_type=VMType.DESKTOP,
            disk_path="/path/to/disk",
            iso_path="/path/to/iso",
            graphics_type="vnc",
        )

        self.assertIn("type='vnc'", xml)
        self.assertNotIn("type='spice'", xml)


if __name__ == "__main__":
    unittest.main()
