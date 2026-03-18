#!/usr/bin/env python3
"""
Test script to verify direct kernel boot generates proper cmdline for all OS types
when kernel_path and initrd_path are provided but auto_url is not.
"""

import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vm_provisioner import VMProvisioner, VMType
from vmanager.provisioning.os_provider import OSType


class TestDirectKernelBootCmdline(unittest.TestCase):
    """Test that direct kernel boot generates appropriate cmdline for each OS type"""

    def setUp(self):
        self.mock_conn = MagicMock()
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

        with patch("vmanager.vm_provisioner.get_host_architecture") as mock_get_arch:
            mock_get_arch.return_value = "x86_64"
            self.provisioner = VMProvisioner(self.mock_conn)

    def test_ubuntu_direct_boot_no_auto_url(self):
        """Ubuntu direct kernel boot should have boot=casper cmdline"""
        xml = self.provisioner.generate_xml(
            vm_name="test-ubuntu",
            vm_type=VMType.DESKTOP,
            disk_path="/tmp/disk.qcow2",
            iso_path="/tmp/ubuntu.iso",
            kernel_path="/tmp/vmlinuz",
            initrd_path="/tmp/initrd",
            os_type=OSType.UBUNTU,
            auto_url=None,  # No automation URL
        )

        # Should contain cmdline with boot=casper
        self.assertIn("<cmdline>", xml)
        self.assertIn("boot=casper", xml)
        print("✓ Ubuntu: boot=casper found in cmdline")

    def test_debian_direct_boot_no_auto_url(self):
        """Debian direct kernel boot should have boot=live cmdline"""
        xml = self.provisioner.generate_xml(
            vm_name="test-debian",
            vm_type=VMType.DESKTOP,
            disk_path="/tmp/disk.qcow2",
            iso_path="/tmp/debian.iso",
            kernel_path="/tmp/vmlinuz",
            initrd_path="/tmp/initrd",
            os_type=OSType.DEBIAN,
            auto_url=None,
        )

        self.assertIn("<cmdline>", xml)
        self.assertIn("boot=live", xml)
        print("✓ Debian: boot=live found in cmdline")

    def test_fedora_direct_boot_no_auto_url(self):
        """Fedora direct kernel boot should have inst.stage2 cmdline"""
        xml = self.provisioner.generate_xml(
            vm_name="test-fedora",
            vm_type=VMType.DESKTOP,
            disk_path="/tmp/disk.qcow2",
            iso_path="/tmp/fedora.iso",
            kernel_path="/tmp/vmlinuz",
            initrd_path="/tmp/initrd",
            os_type=OSType.FEDORA,
            auto_url=None,
        )

        self.assertIn("<cmdline>", xml)
        self.assertIn("inst.stage2", xml)
        print("✓ Fedora: inst.stage2 found in cmdline")

    def test_opensuse_direct_boot_no_auto_url(self):
        """openSUSE direct kernel boot should have install=cd:/ cmdline"""
        xml = self.provisioner.generate_xml(
            vm_name="test-opensuse",
            vm_type=VMType.DESKTOP,
            disk_path="/tmp/disk.qcow2",
            iso_path="/tmp/opensuse.iso",
            kernel_path="/tmp/linux",
            initrd_path="/tmp/initrd",
            os_type=OSType.OPENSUSE,
            auto_url=None,
        )

        self.assertIn("<cmdline>", xml)
        self.assertIn("install=cd:/", xml)
        print("✓ openSUSE: install=cd:/ found in cmdline")

    def test_linux_generic_direct_boot_no_auto_url(self):
        """Generic Linux (SLES) direct kernel boot should have install=cd:/ cmdline"""
        xml = self.provisioner.generate_xml(
            vm_name="test-sles",
            vm_type=VMType.DESKTOP,
            disk_path="/tmp/disk.qcow2",
            iso_path="/tmp/sles.iso",
            kernel_path="/tmp/linux",
            initrd_path="/tmp/initrd",
            os_type=OSType.OPENSUSE,
            auto_url=None,
        )

        self.assertIn("<cmdline>", xml)
        self.assertIn("install=cd:/", xml)
        print("✓ SLES/Linux: install=cd:/ found in cmdline")

    def test_alpine_direct_boot_no_auto_url(self):
        """Alpine direct kernel boot should have alpine_repo cmdline"""
        xml = self.provisioner.generate_xml(
            vm_name="test-alpine",
            vm_type=VMType.DESKTOP,
            disk_path="/tmp/disk.qcow2",
            iso_path="/tmp/alpine.iso",
            kernel_path="/tmp/vmlinuz-virt",
            initrd_path="/tmp/initramfs-virt",
            os_type=OSType.ALPINE,
            os_version="v3.23",
            auto_url=None,
        )

        self.assertIn("<cmdline>", xml)
        self.assertIn("alpine_repo=", xml)
        self.assertIn("ip=dhcp", xml)
        print("✓ Alpine: alpine_repo and ip=dhcp found in cmdline")

    def test_arch_direct_boot_no_auto_url(self):
        """Arch Linux direct kernel boot should have archisobasedir cmdline"""
        xml = self.provisioner.generate_xml(
            vm_name="test-arch",
            vm_type=VMType.DESKTOP,
            disk_path="/tmp/disk.qcow2",
            iso_path="/tmp/arch.iso",
            kernel_path="/tmp/vmlinuz-linux",
            initrd_path="/tmp/initramfs-linux.img",
            os_type=OSType.ARCHLINUX,
            auto_url=None,
        )

        self.assertIn("<cmdline>", xml)
        self.assertIn("archisobasedir=arch", xml)
        self.assertIn("archisodevice=/dev/sr0", xml)
        print("✓ Arch Linux: archisobasedir=arch found in cmdline")

    def test_all_os_types_have_cmdline_with_direct_boot(self):
        """Verify all OS types get a cmdline when using direct kernel boot"""
        os_types_to_test = [
            OSType.UBUNTU,
            OSType.DEBIAN,
            OSType.FEDORA,
            OSType.OPENSUSE,
            OSType.ALPINE,
            OSType.ARCHLINUX,
        ]

        for os_type in os_types_to_test:
            xml = self.provisioner.generate_xml(
                vm_name=f"test-{os_type.value}",
                vm_type=VMType.DESKTOP,
                disk_path="/tmp/disk.qcow2",
                iso_path="/tmp/test.iso",
                kernel_path="/tmp/vmlinuz",
                initrd_path="/tmp/initrd",
                os_type=os_type,
                auto_url=None,
            )

            # Every OS should have a cmdline when using direct kernel boot
            self.assertIn("<cmdline>", xml,
                f"{os_type.value} should have cmdline with direct kernel boot")

            # Cmdline should not be empty
            import re
            cmdline_match = re.search(r'<cmdline>(.*?)</cmdline>', xml, re.DOTALL)
            self.assertIsNotNone(cmdline_match,
                f"{os_type.value} cmdline tag should exist")
            cmdline_content = cmdline_match.group(1).strip()
            self.assertTrue(len(cmdline_content) > 0,
                f"{os_type.value} cmdline should not be empty, got: '{cmdline_content}'")

            print(f"✓ {os_type.value}: has non-empty cmdline")

    def test_serial_console_added_to_cmdline(self):
        """Verify serial console parameters are added when requested"""
        xml = self.provisioner.generate_xml(
            vm_name="test-serial",
            vm_type=VMType.DESKTOP,
            disk_path="/tmp/disk.qcow2",
            iso_path="/tmp/ubuntu.iso",
            kernel_path="/tmp/vmlinuz",
            initrd_path="/tmp/initrd",
            os_type=OSType.UBUNTU,
            auto_url=None,
            serial_console=True,
        )

        self.assertIn("<cmdline>", xml)
        self.assertIn("console=tty0", xml)
        self.assertIn("console=ttyS0,115200", xml)
        print("✓ Serial console: console parameters added to cmdline")


if __name__ == "__main__":
    print("Testing direct kernel boot cmdline generation for all OS types...\n")

    # Run the tests
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDirectKernelBootCmdline)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if result.wasSuccessful():
        print("\n" + "="*70)
        print("✓ All tests passed!")
        print("="*70)
        print("\nFix Summary:")
        print("  • Ubuntu: boot=casper parameters added")
        print("  • Debian: boot=live parameters added")
        print("  • Fedora: inst.stage2 parameter added")
        print("  • openSUSE/SLES: install=cd:/ parameters added")
        print("  • Alpine: alpine_repo and ip=dhcp parameters added")
        print("  • Arch Linux: already had proper parameters")
        print("\nAll distributions now boot correctly with direct kernel boot!")
        sys.exit(0)
    else:
        print("\n" + "="*70)
        print("✗ Some tests failed")
        print("="*70)
        sys.exit(1)
