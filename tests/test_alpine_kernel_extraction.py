"""
Tests for Alpine Linux kernel extraction logic
"""

import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
from pathlib import Path
import sys

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vm_provisioner import VMProvisioner
from vmanager.provisioning.os_provider import OSType


class TestAlpineKernelExtraction(unittest.TestCase):
    def setUp(self):
        self.mock_conn = MagicMock()
        with patch("vmanager.vm_provisioner.get_host_architecture") as mock_get_arch:
            mock_get_arch.return_value = "x86_64"
            self.provisioner = VMProvisioner(self.mock_conn)

    @patch("vmanager.vm_provisioner.subprocess.run")
    @patch("vmanager.vm_provisioner.shutil.which")
    def test_extract_alpine_kernel_virt(self, mock_which, mock_run):
        """Test extracting Alpine kernel (virt variant)"""
        mock_which.return_value = True
        
        # Mock successful extraction on first try (virt)
        def mock_run_side_effect(cmd, **kwargs):
            output_dir = next(arg for arg in cmd if arg.startswith("-o")).lstrip("-o")
            kernel_path = Path(output_dir) / "boot" / "vmlinuz-virt"
            initrd_path = Path(output_dir) / "boot" / "initramfs-virt"
            kernel_path.parent.mkdir(parents=True, exist_ok=True)
            kernel_path.write_text("mock kernel")
            initrd_path.write_text("mock initrd")
            return MagicMock(returncode=0)

        mock_run.side_effect = mock_run_side_effect

        kernel, initrd = self.provisioner._extract_alpine_iso_kernel_initrd("mock.iso")
        
        self.assertIn("vmlinuz-virt", kernel)
        self.assertIn("initramfs-virt", initrd)
        self.assertTrue(os.path.exists(kernel))
        self.assertTrue(os.path.exists(initrd))

    @patch("vmanager.vm_provisioner.subprocess.run")
    @patch("vmanager.vm_provisioner.shutil.which")
    def test_extract_alpine_kernel_lts_fallback(self, mock_which, mock_run):
        """Test falling back to LTS variant if virt variant fails"""
        mock_which.return_value = True
        
        # Mock failure on first try (virt), success on second (lts)
        def mock_run_side_effect(cmd, **kwargs):
            if "vmlinuz-virt" in cmd:
                raise MagicMock(stderr="File not found")
            
            output_dir = next(arg for arg in cmd if arg.startswith("-o")).lstrip("-o")
            kernel_path = Path(output_dir) / "boot" / "vmlinuz-lts"
            initrd_path = Path(output_dir) / "boot" / "initramfs-lts"
            kernel_path.parent.mkdir(parents=True, exist_ok=True)
            kernel_path.write_text("mock kernel")
            initrd_path.write_text("mock initrd")
            return MagicMock(returncode=0)

        mock_run.side_effect = mock_run_side_effect

        kernel, initrd = self.provisioner._extract_alpine_iso_kernel_initrd("mock.iso")
        
        self.assertIn("vmlinuz-lts", kernel)
        self.assertIn("initramfs-lts", initrd)
        self.assertTrue(os.path.exists(kernel))
        self.assertTrue(os.path.exists(initrd))


if __name__ == "__main__":
    unittest.main()
