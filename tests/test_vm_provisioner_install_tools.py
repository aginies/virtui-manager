"""
Tests for the install tool pre-flight checks on VMProvisioner:

- _resolve_os_type: resolves the OSType from distro/URL/template.
- required_install_tools: returns the binaries an install path will need.
- check_install_tools: reports which binaries are missing locally or remotely.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.provisioning.os_provider import OSType, OSVersion
from vmanager.vm_provisioner import VMProvisioner


def _make_version(os_type, display_name=""):
    return OSVersion(
        os_type=os_type,
        version_id="test",
        display_name=display_name or os_type.value,
        architecture="x86_64",
        is_evaluation=False,
    )


class TestResolveOSType(unittest.TestCase):
    def setUp(self):
        with patch("vmanager.vm_provisioner.get_host_architecture") as mock_get_arch:
            mock_get_arch.return_value = "x86_64"
            self.provisioner = VMProvisioner(MagicMock())

    def test_from_os_type_enum(self):
        self.assertIs(self.provisioner._resolve_os_type(OSType.ARCHLINUX), OSType.ARCHLINUX)

    def test_from_os_version_object(self):
        v = _make_version(OSType.UBUNTU, "Ubuntu 24.04")
        self.assertIs(self.provisioner._resolve_os_type(v), OSType.UBUNTU)

    def test_from_template_name(self):
        # A generic (non-OSVersion) distro resolves the OSType from the template name,
        # mirroring provision_vm's template-first detection.
        self.assertIs(
            self.provisioner._resolve_os_type("cached", None, None, "autoyast-minimal"),
            OSType.OPENSUSE,
        )
        self.assertIs(
            self.provisioner._resolve_os_type("cached", None, None, "autoinstall-basic"),
            OSType.UBUNTU,
        )
        self.assertIs(
            self.provisioner._resolve_os_type("cached", None, None, "kickstart-minimal"),
            OSType.FEDORA,
        )
        self.assertIs(
            self.provisioner._resolve_os_type("cached", None, None, "alpine-answers-basic"),
            OSType.ALPINE,
        )

    def test_from_iso_url(self):
        self.assertIs(
            self.provisioner._resolve_os_type(OSType.LINUX, "http://x/debian.iso", None, None),
            OSType.DEBIAN,
        )
        self.assertIs(
            self.provisioner._resolve_os_type(OSType.LINUX, "http://x/archlinux.iso", None, None),
            OSType.ARCHLINUX,
        )
        self.assertIs(
            self.provisioner._resolve_os_type(OSType.LINUX, None, "http://x/opensuse-leap.iso"),
            OSType.OPENSUSE,
        )

    def test_unknown_falls_back_to_linux(self):
        self.assertIs(
            self.provisioner._resolve_os_type(OSType.LINUX, "http://x/random.iso", None, None),
            OSType.LINUX,
        )


class TestRequiredInstallTools(unittest.TestCase):
    def setUp(self):
        with patch("vmanager.vm_provisioner.get_host_architecture") as mock_get_arch:
            mock_get_arch.return_value = "x86_64"
            self.provisioner = VMProvisioner(MagicMock())

    def test_qemu_img_always_present(self):
        for tools in (
            self.provisioner.required_install_tools(OSType.LINUX),
            self.provisioner.required_install_tools(OSType.OPENSUSE, boot_uefi=False),
        ):
            self.assertIn("qemu-img", tools)

    def test_plain_bios_iso_only_qemu_img(self):
        # No automation + BIOS + non-direct-boot distro -> plain ISO boot
        tools = self.provisioner.required_install_tools(OSType.LINUX, boot_uefi=False)
        self.assertEqual(tools, ["qemu-img"])

    def test_uefi_arch_requires_7z(self):
        tools = self.provisioner.required_install_tools(OSType.ARCHLINUX, boot_uefi=True)
        self.assertIn("7z", tools)
        self.assertNotIn("mcopy", tools)

    def test_uefi_debian_requires_7z(self):
        self.assertIn("7z", self.provisioner.required_install_tools(OSType.DEBIAN, boot_uefi=True))

    def test_uefi_alpine_requires_7z(self):
        self.assertIn("7z", self.provisioner.required_install_tools(OSType.ALPINE, boot_uefi=True))

    def test_automation_always_requires_7z(self):
        # Any automation template triggers direct kernel boot -> needs 7z
        self.assertIn(
            "7z",
            self.provisioner.required_install_tools(
                OSType.OPENSUSE, automation_template_id="autoyast-minimal", boot_uefi=False
            ),
        )

    def test_opensuse_autoyast_includes_floppy_tools(self):
        tools = self.provisioner.required_install_tools(
            OSType.OPENSUSE, automation_template_id="autoyast-minimal", boot_uefi=True
        )
        for tool in ("dd", "mkfs.vfat", "mcopy"):
            self.assertIn(tool, tools)

    def test_alpine_answers_includes_floppy_tools(self):
        tools = self.provisioner.required_install_tools(
            OSType.ALPINE, automation_template_id="alpine-answers-basic", boot_uefi=True
        )
        for tool in ("dd", "mkfs.vfat", "mcopy"):
            self.assertIn(tool, tools)

    def test_ubuntu_automation_no_floppy(self):
        # Ubuntu uses kernel extraction only, never the floppy fallback
        tools = self.provisioner.required_install_tools(
            OSType.UBUNTU, automation_template_id="autoinstall-basic", boot_uefi=True
        )
        self.assertIn("7z", tools)
        self.assertNotIn("mcopy", tools)

    def test_debian_automation_no_floppy(self):
        tools = self.provisioner.required_install_tools(
            OSType.DEBIAN, automation_template_id="preseed-basic", boot_uefi=True
        )
        self.assertIn("7z", tools)
        self.assertNotIn("mcopy", tools)


class TestCheckInstallTools(unittest.TestCase):
    def setUp(self):
        with patch("vmanager.vm_provisioner.get_host_architecture") as mock_get_arch:
            mock_get_arch.return_value = "x86_64"
            self.provisioner = VMProvisioner(MagicMock())

    def test_check_local_all_present(self):
        with patch("vmanager.vm_provisioner.shutil.which", return_value="/usr/bin/7z"):
            missing = self.provisioner.check_install_tools(["7z", "qemu-img"])
        self.assertEqual(missing, [])

    def test_check_local_some_missing(self):
        present = {"7z": "/usr/bin/7z"}
        with patch("vmanager.vm_provisioner.shutil.which", side_effect=lambda t: present.get(t)):
            missing = self.provisioner.check_install_tools(["7z", "dd", "mcopy"])
        self.assertEqual(sorted(missing), ["dd", "mcopy"])

    def test_check_local_none_present(self):
        with patch("vmanager.vm_provisioner.shutil.which", return_value=None):
            missing = self.provisioner.check_install_tools(["7z", "qemu-img"])
        self.assertEqual(sorted(missing), ["7z", "qemu-img"])

    def test_check_remote_all_present(self):
        ok = MagicMock()
        ok.returncode = 0
        with patch("vmanager.vm_provisioner.run_command", return_value=ok):
            missing = self.provisioner.check_install_tools(["7z"], ssh_host="user@host")
        self.assertEqual(missing, [])

    def test_check_remote_some_missing(self):
        results = {0: MagicMock(returncode=0), 1: MagicMock(returncode=1)}
        order = iter([results[0], results[1]])

        def run_mock(cmd, remote_host=None, check=True):
            # cmd[1] is the tool name for ["which", tool]
            return next(order)

        with patch("vmanager.vm_provisioner.run_command", side_effect=run_mock):
            missing = self.provisioner.check_install_tools(["7z", "mcopy"], ssh_host="user@host")
        self.assertEqual(missing, ["mcopy"])

    def test_check_remote_passes_ssh_host(self):
        with patch("vmanager.vm_provisioner.run_command") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            self.provisioner.check_install_tools(["7z"], ssh_host="user@host")
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args.kwargs["remote_host"], "user@host")


if __name__ == "__main__":
    unittest.main()
