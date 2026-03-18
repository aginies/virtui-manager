"""
Test for auto-installation reboot handling.

Verifies that:
1. During auto-installation, on_reboot is set to "destroy" for all VMs
2. After installation, strip_installation_assets updates on_reboot to "restart"
3. SECURE VMs keep on_reboot as "destroy" even after installation
"""

import unittest
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
import os
import sys

# Add the source directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import libvirt

from vmanager.vm_actions import strip_installation_assets
from vmanager.vm_provisioner import VMProvisioner, VMType


class TestAutoInstallRebootHandling(unittest.TestCase):
    """Tests for auto-installation reboot handling."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a mock libvirt connection
        self.mock_conn = MagicMock(spec=libvirt.virConnect)
        self.mock_conn.getURI.return_value = "qemu:///system"

        # Mock capabilities for machine type detection
        caps_xml = """<?xml version="1.0"?>
<capabilities>
  <host>
    <cpu><arch>x86_64</arch></cpu>
  </host>
  <guest>
    <os_type>hvm</os_type>
    <arch name="x86_64">
      <wordsize>64</wordsize>
      <emulator>/usr/bin/qemu-system-x86_64</emulator>
      <machine>pc-i440fx-9.0</machine>
      <machine>pc-q35-9.0</machine>
    </arch>
  </guest>
</capabilities>"""
        self.mock_conn.getCapabilities.return_value = caps_xml

    def test_auto_install_sets_on_reboot_destroy_for_desktop(self):
        """Test that auto-installation sets on_reboot to 'destroy' for DESKTOP VMs."""
        provisioner = VMProvisioner(self.mock_conn)

        # Get settings for DESKTOP VM with auto-installation
        settings = provisioner._get_vm_settings(
            vm_type=VMType.DESKTOP,
            boot_uefi=True,
            is_auto_install=True,
        )

        # Verify on_reboot is set to "destroy" for auto-installation
        self.assertEqual(settings["on_reboot"], "destroy")

    def test_auto_install_sets_on_reboot_destroy_for_server(self):
        """Test that auto-installation sets on_reboot to 'destroy' for SERVER VMs."""
        provisioner = VMProvisioner(self.mock_conn)

        # Get settings for SERVER VM with auto-installation
        settings = provisioner._get_vm_settings(
            vm_type=VMType.SERVER,
            boot_uefi=True,
            is_auto_install=True,
        )

        # Verify on_reboot is set to "destroy" for auto-installation
        self.assertEqual(settings["on_reboot"], "destroy")

    def test_normal_install_desktop_has_restart(self):
        """Test that normal (non-auto) DESKTOP installation has on_reboot as 'restart'."""
        provisioner = VMProvisioner(self.mock_conn)

        # Get settings for DESKTOP VM without auto-installation
        settings = provisioner._get_vm_settings(
            vm_type=VMType.DESKTOP,
            boot_uefi=True,
            is_auto_install=False,
        )

        # Verify on_reboot is "restart" for normal installation
        self.assertEqual(settings["on_reboot"], "restart")

    def test_auto_install_sets_on_reboot_destroy_for_computation(self):
        """Test that auto-installation sets on_reboot to 'destroy' for COMPUTATION VMs."""
        provisioner = VMProvisioner(self.mock_conn)

        # Get settings for COMPUTATION VM with auto-installation
        settings = provisioner._get_vm_settings(
            vm_type=VMType.COMPUTATION,
            boot_uefi=True,
            is_auto_install=True,
        )

        # Verify on_reboot is set to "destroy" for auto-installation
        self.assertEqual(settings["on_reboot"], "destroy")

    def test_normal_computation_has_restart(self):
        """Test that normal (non-auto) COMPUTATION installation has on_reboot as 'restart'."""
        provisioner = VMProvisioner(self.mock_conn)

        # Get settings for COMPUTATION VM without auto-installation
        settings = provisioner._get_vm_settings(
            vm_type=VMType.COMPUTATION,
            boot_uefi=True,
            is_auto_install=False,
        )

        # Verify on_reboot is "restart" for normal installation
        self.assertEqual(settings["on_reboot"], "restart")

    def test_secure_vm_keeps_destroy_regardless_of_auto_install(self):
        """Test that SECURE VMs always have on_reboot as 'destroy'."""
        provisioner = VMProvisioner(self.mock_conn)

        # Test with auto-installation
        settings_auto = provisioner._get_vm_settings(
            vm_type=VMType.SECURE,
            boot_uefi=True,
            is_auto_install=True,
        )
        self.assertEqual(settings_auto["on_reboot"], "destroy")

        # Test without auto-installation
        settings_normal = provisioner._get_vm_settings(
            vm_type=VMType.SECURE,
            boot_uefi=True,
            is_auto_install=False,
        )
        self.assertEqual(settings_normal["on_reboot"], "destroy")

    def test_strip_installation_assets_updates_on_reboot_to_restart(self):
        """Test that strip_installation_assets changes on_reboot from 'destroy' to 'restart'."""
        # Create a mock domain with auto-installation config
        mock_domain = MagicMock(spec=libvirt.virDomain)
        mock_domain.name.return_value = "test-vm"
        mock_domain.connect.return_value = self.mock_conn

        # XML with kernel/initrd/cmdline and on_reboot="destroy" (non-SECURE VM)
        xml_before = """<?xml version="1.0"?>
<domain type='kvm'>
  <name>test-vm</name>
  <uuid>12345678-1234-1234-1234-123456789012</uuid>
  <memory>4194304</memory>
  <vcpu>2</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-9.0'>hvm</type>
    <kernel>/path/to/kernel</kernel>
    <initrd>/path/to/initrd</initrd>
    <cmdline>console=ttyS0</cmdline>
  </os>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>destroy</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <disk type='file' device='disk'>
      <source file='/path/to/disk.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='floppy'>
      <source file='/path/to/autoinst.img'/>
      <target dev='fda' bus='fdc'/>
    </disk>
  </devices>
</domain>"""

        mock_domain.XMLDesc.return_value = xml_before

        # Mock defineXML to capture the new XML
        new_xml = None

        def capture_xml(xml):
            nonlocal new_xml
            new_xml = xml

        self.mock_conn.defineXML.side_effect = capture_xml

        # Call strip_installation_assets
        strip_installation_assets(mock_domain)

        # Verify defineXML was called
        self.mock_conn.defineXML.assert_called_once()

        # Parse the new XML and verify changes
        root = ET.fromstring(new_xml)

        # Verify kernel/initrd/cmdline were removed
        os_elem = root.find("os")
        self.assertIsNone(os_elem.find("kernel"))
        self.assertIsNone(os_elem.find("initrd"))
        self.assertIsNone(os_elem.find("cmdline"))

        # Verify floppy was removed
        devices = root.find("devices")
        floppy_disks = [d for d in devices.findall("disk") if d.get("device") == "floppy"]
        self.assertEqual(len(floppy_disks), 0)

        # Verify on_reboot was changed to "restart"
        on_reboot = root.find("on_reboot")
        self.assertIsNotNone(on_reboot)
        self.assertEqual(on_reboot.text, "restart")

    def test_strip_installation_assets_keeps_destroy_for_secure_vm(self):
        """Test that strip_installation_assets keeps on_reboot as 'destroy' for SECURE VMs."""
        mock_domain = MagicMock(spec=libvirt.virDomain)
        mock_domain.name.return_value = "secure-vm"
        mock_domain.connect.return_value = self.mock_conn

        # XML with SEV (indicates SECURE VM) and on_reboot="destroy"
        xml_before = """<?xml version="1.0"?>
<domain type='kvm'>
  <name>secure-vm</name>
  <uuid>12345678-1234-1234-1234-123456789012</uuid>
  <memory>4194304</memory>
  <vcpu>2</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-9.0'>hvm</type>
    <kernel>/path/to/kernel</kernel>
    <initrd>/path/to/initrd</initrd>
  </os>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>destroy</on_reboot>
  <on_crash>destroy</on_crash>
  <launchSecurity type='sev'>
    <cbitpos>47</cbitpos>
    <reducedPhysBits>1</reducedPhysBits>
    <policy>0x0033</policy>
  </launchSecurity>
  <devices>
    <disk type='file' device='disk'>
      <source file='/path/to/disk.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
  </devices>
</domain>"""

        mock_domain.XMLDesc.return_value = xml_before

        # Mock defineXML to capture the new XML
        new_xml = None

        def capture_xml(xml):
            nonlocal new_xml
            new_xml = xml

        self.mock_conn.defineXML.side_effect = capture_xml

        # Call strip_installation_assets
        strip_installation_assets(mock_domain)

        # Parse the new XML and verify changes
        root = ET.fromstring(new_xml)

        # Verify kernel/initrd were removed
        os_elem = root.find("os")
        self.assertIsNone(os_elem.find("kernel"))
        self.assertIsNone(os_elem.find("initrd"))

        # Verify on_reboot is STILL "destroy" (SECURE VM)
        on_reboot = root.find("on_reboot")
        self.assertIsNotNone(on_reboot)
        self.assertEqual(on_reboot.text, "destroy")

    def test_strip_installation_assets_is_idempotent(self):
        """Test that strip_installation_assets can be called multiple times safely."""
        mock_domain = MagicMock(spec=libvirt.virDomain)
        mock_domain.name.return_value = "test-vm"
        mock_domain.connect.return_value = self.mock_conn

        # XML already cleaned (no kernel/initrd, on_reboot already "restart")
        xml_clean = """<?xml version="1.0"?>
<domain type='kvm'>
  <name>test-vm</name>
  <uuid>12345678-1234-1234-1234-123456789012</uuid>
  <memory>4194304</memory>
  <vcpu>2</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-9.0'>hvm</type>
  </os>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <disk type='file' device='disk'>
      <source file='/path/to/disk.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
  </devices>
</domain>"""

        mock_domain.XMLDesc.return_value = xml_clean

        # Mock defineXML
        new_xml = None

        def capture_xml(xml):
            nonlocal new_xml
            new_xml = xml

        self.mock_conn.defineXML.side_effect = capture_xml

        # Call strip_installation_assets on already-clean VM
        strip_installation_assets(mock_domain)

        # Parse the new XML
        root = ET.fromstring(new_xml)

        # Verify on_reboot is still "restart"
        on_reboot = root.find("on_reboot")
        self.assertIsNotNone(on_reboot)
        self.assertEqual(on_reboot.text, "restart")


if __name__ == "__main__":
    unittest.main()
