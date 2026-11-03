"""
Tests for the declarative deployment config (``vmc --json``) and the
openSUSE/SLES extra-packages handling in the automation engine.
"""

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.deploy_config import DeployConfigError, load_deploy_config
from vmanager.provisioning.automation_engine import AutomationEngine
from vmanager.provisioning.os_provider import OSType


class TestLoadDeployConfig(unittest.TestCase):
    def _write(self, obj, name="c.json"):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(obj, f)
            path = f.name
        self.addCleanup(os.unlink, path)
        return path

    def test_valid_minimal_gets_defaults(self):
        path = self._write(
            {
                "server": "Localhost",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
            }
        )
        cfg = load_deploy_config(path)
        # For a profile, hardware is optional and resolved from the profile
        # defaults at deploy time (see resolve_vm_resources).
        self.assertIsNone(cfg["memory_mb"])
        self.assertIsNone(cfg["vcpu"])
        self.assertIsNone(cfg["disk_size_gb"])
        self.assertTrue(cfg["boot_uefi"])
        self.assertTrue(cfg["start"])
        self.assertIsNone(cfg["autoinstall"])

    def test_resolve_vm_resources_profile_defaults(self):
        from vmanager.deploy_config import resolve_vm_resources
        from vmanager.vm_provisioner import VMType

        cfg = load_deploy_config(
            self._write(
                {
                    "server": "s",
                    "vm_name": "v",
                    "vm_type": "desktop",
                    "iso": {"source": "url", "url": "u"},
                }
            )
        )
        vm_type, memory_mb, vcpu, disk = resolve_vm_resources(cfg)
        self.assertEqual(vm_type, VMType.DESKTOP)
        # Disk is None so provision_vm applies DEFAULT_DISK_SIZE_GB[DESKTOP] (30).
        self.assertEqual((memory_mb, vcpu, disk), (4096, 4, None))

    def test_resolve_vm_resources_profile_override(self):
        from vmanager.deploy_config import resolve_vm_resources
        from vmanager.vm_provisioner import VMType

        cfg = load_deploy_config(
            self._write(
                {
                    "server": "s",
                    "vm_name": "v",
                    "vm_type": "server",
                    "memory_mb": 8192,
                    "iso": {"source": "url", "url": "u"},
                }
            )
        )
        vm_type, memory_mb, vcpu, disk = resolve_vm_resources(cfg)
        self.assertEqual(vm_type, VMType.SERVER)
        # memory is overridden; vcpu uses the SERVER default; disk is None
        # (provision_vm applies DEFAULT_DISK_SIZE_GB[SERVER] = 32).
        self.assertEqual((memory_mb, vcpu, disk), (8192, 6, None))

    def test_resolve_vm_resources_custom(self):
        from vmanager.deploy_config import resolve_vm_resources
        from vmanager.vm_provisioner import VMType

        cfg = load_deploy_config(
            self._write(
                {
                    "server": "s",
                    "vm_name": "v",
                    "vm_type": "custom",
                    "memory_mb": 2048,
                    "vcpu": 1,
                    "disk_size_gb": 10,
                    "iso": {"source": "url", "url": "u"},
                }
            )
        )
        vm_type, memory_mb, vcpu, disk = resolve_vm_resources(cfg)
        self.assertEqual(vm_type, VMType.SERVER)  # neutral base for custom
        self.assertEqual((memory_mb, vcpu, disk), (2048, 1, 10))

    def test_custom_requires_hardware(self):
        path = self._write(
            {
                "server": "s",
                "vm_name": "v",
                "vm_type": "custom",
                "memory_mb": 1024,
                "vcpu": 1,
                "iso": {"source": "url", "url": "u"},
            }
        )
        with self.assertRaises(DeployConfigError) as ctx:
            load_deploy_config(path)
        self.assertIn("disk_size_gb", str(ctx.exception))

    def test_missing_required_field(self):
        path = self._write(
            {"vm_name": "x", "vm_type": "server", "iso": {"source": "url", "url": "u"}}
        )
        with self.assertRaises(DeployConfigError):
            load_deploy_config(path)

    def test_invalid_vm_type(self):
        path = self._write(
            {
                "server": "s",
                "vm_name": "x",
                "vm_type": "nonsense",
                "iso": {"source": "url", "url": "u"},
            }
        )
        with self.assertRaises(DeployConfigError) as ctx:
            load_deploy_config(path)
        self.assertIn("vm_type", str(ctx.exception))

    def test_pool_requires_volume(self):
        path = self._write(
            {
                "server": "s",
                "vm_name": "x",
                "vm_type": "server",
                "iso": {"source": "pool", "pool": "default"},
            }
        )
        with self.assertRaises(DeployConfigError):
            load_deploy_config(path)

    def test_repository_requires_version(self):
        path = self._write(
            {
                "server": "s",
                "vm_name": "x",
                "vm_type": "server",
                "iso": {"source": "repository"},
            }
        )
        with self.assertRaises(DeployConfigError):
            load_deploy_config(path)

    def test_autoinstall_enabled_requires_template(self):
        path = self._write(
            {
                "server": "s",
                "vm_name": "x",
                "vm_type": "server",
                "iso": {"source": "url", "url": "u"},
                "autoinstall": {"enabled": True},
            }
        )
        with self.assertRaises(DeployConfigError):
            load_deploy_config(path)

    def test_bad_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            path = f.name
        self.addCleanup(os.unlink, path)
        with self.assertRaises(DeployConfigError):
            load_deploy_config(path)

    def test_not_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(DeployConfigError):
                load_deploy_config(d)

    def test_invalid_memory(self):
        path = self._write(
            {
                "server": "s",
                "vm_name": "x",
                "vm_type": "server",
                "memory_mb": -5,
                "iso": {"source": "url", "url": "u"},
            }
        )
        with self.assertRaises(DeployConfigError):
            load_deploy_config(path)

    def _autoinstall(self):
        return {
            "enabled": True,
            "template": "autoyast-server.xml",
            "root_password": "r",
            "user_password": "u",
        }

    def test_post_install_validated(self):
        path = self._write(
            {
                "server": "s",
                "vm_name": "x",
                "vm_type": "server",
                "iso": {"source": "url", "url": "u"},
                "autoinstall": self._autoinstall(),
                "post_install": {"commands": ["echo hi", "a, b > c"]},
            }
        )
        cfg = load_deploy_config(path)
        self.assertEqual(
            cfg["post_install"]["commands"], ["echo hi", "a, b > c"]
        )

    def test_post_install_requires_autoinstall(self):
        # No autoinstall block at all -> post_install is rejected.
        path = self._write(
            {
                "server": "s",
                "vm_name": "x",
                "vm_type": "server",
                "iso": {"source": "url", "url": "u"},
                "post_install": {"commands": ["echo hi"]},
            }
        )
        with self.assertRaises(DeployConfigError):
            load_deploy_config(path)

    def test_post_install_requires_autoinstall_enabled(self):
        # autoinstall present but disabled -> post_install is rejected.
        ai = self._autoinstall()
        ai["enabled"] = False
        path = self._write(
            {
                "server": "s",
                "vm_name": "x",
                "vm_type": "server",
                "iso": {"source": "url", "url": "u"},
                "autoinstall": ai,
                "post_install": {"commands": ["echo hi"]},
            }
        )
        with self.assertRaises(DeployConfigError):
            load_deploy_config(path)

    def test_post_install_must_be_object(self):
        path = self._write(
            {
                "server": "s",
                "vm_name": "x",
                "vm_type": "server",
                "iso": {"source": "url", "url": "u"},
                "autoinstall": self._autoinstall(),
                "post_install": ["echo hi"],
            }
        )
        with self.assertRaises(DeployConfigError):
            load_deploy_config(path)

    def test_post_install_commands_must_be_strings(self):
        path = self._write(
            {
                "server": "s",
                "vm_name": "x",
                "vm_type": "server",
                "iso": {"source": "url", "url": "u"},
                "autoinstall": self._autoinstall(),
                "post_install": {"commands": ["echo hi", 42]},
            }
        )
        with self.assertRaises(DeployConfigError):
            load_deploy_config(path)


class TestOpenSUSEPackages(unittest.TestCase):
    """Extra packages must be injected into AutoYaST XML and Agama JSON."""

    def setUp(self):
        self.engine = AutomationEngine()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_autoyast_packages_inserted(self):
        out = self.engine.generate(
            os_type=OSType.OPENSUSE,
            version_id="24.3",
            vm_name="web01",
            user_config={
                "template_name": "autoyast-server.xml",
                "root_password": "root",
                "username": "admin",
                "user_password": "pw",
                "packages": ["ssh", "vim-extra"],
            },
            output_path=self.tmp,
            template_name="autoyast-server.xml",
        )
        content = out.read_text()
        # Must still be valid XML
        ET.fromstring(content)
        # Extra packages present, in order, before the closing tag
        self.assertIn("<package>ssh</package>", content)
        self.assertIn("<package>vim-extra</package>", content)
        self.assertLess(
            content.index("<package>ssh</package>"), content.index("</packages>")
        )

    def test_agama_packages_appended(self):
        out = self.engine.generate(
            os_type=OSType.OPENSUSE,
            version_id="16.0",
            vm_name="web02",
            user_config={
                "template_name": "agama-server.json",
                "root_password": "root",
                "username": "admin",
                "user_password": "pw",
                "packages": ["nginx", "docker"],
            },
            output_path=self.tmp,
            template_name="agama-server.json",
        )
        data = json.loads(out.read_text())
        pkgs = data["software"]["packages"]
        self.assertIn("nginx", pkgs)
        self.assertIn("docker", pkgs)

    def test_no_packages_is_noop(self):
        out = self.engine.generate(
            os_type=OSType.OPENSUSE,
            version_id="24.3",
            vm_name="web03",
            user_config={
                "template_name": "autoyast-server.xml",
                "root_password": "root",
                "username": "admin",
                "user_password": "pw",
            },
            output_path=self.tmp,
            template_name="autoyast-server.xml",
        )
        content = out.read_text()
        ET.fromstring(content)
        self.assertNotIn("<package>ssh</package>", content)


class TestOpenSUSEPostInstall(unittest.TestCase):
    """Post-install commands must be baked into the auto-install template."""

    def setUp(self):
        self.engine = AutomationEngine()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _base_config(self, template, **extra):
        cfg = {
            "template_name": template,
            "root_password": "root",
            "username": "admin",
            "user_password": "pw",
        }
        cfg.update(extra)
        return cfg

    def test_autoyast_post_install_inserted(self):
        out = self.engine.generate(
            os_type=OSType.OPENSUSE,
            version_id="24.3",
            vm_name="web01",
            user_config=self._base_config(
                "autoyast-server.xml",
                post_install_commands=["systemctl enable foo", "echo a, b > /tmp/c"],
            ),
            output_path=self.tmp,
            template_name="autoyast-server.xml",
        )
        content = out.read_text()
        # Must still be valid XML
        root = ET.fromstring(content)
        # The template declares a default namespace, so all elements (including
        # the ones we insert) live in it.
        ns = "{http://www.suse.com/1.0/yast2ns}"
        # The <scripts><chroot-scripts> section is present with both commands,
        # in order, wrapped in a <source> CDATA block and chrooted into the
        # installed system.
        scripts = root.find(f"{ns}scripts")
        self.assertIsNotNone(scripts, "<scripts> section missing")
        chroot_scripts = scripts.find(f"{ns}chroot-scripts")
        self.assertIsNotNone(chroot_scripts, "<chroot-scripts> section missing")
        script = chroot_scripts.find(f"{ns}script")
        self.assertIsNotNone(script, "<script> element missing")
        # chrooted must be true so it runs in the installed system
        chrooted = script.find(f"{ns}chrooted")
        self.assertIsNotNone(chrooted, "<chrooted> element missing")
        self.assertEqual(chrooted.text.strip(), "true")
        # The commands live in a <source> element, in order
        source = script.find(f"{ns}source")
        self.assertIsNotNone(source, "<source> element missing")
        self.assertIn("systemctl enable foo", source.text)
        self.assertIn("echo a, b > /tmp/c", source.text)
        self.assertLess(
            source.text.index("systemctl enable foo"),
            source.text.index("echo a, b > /tmp/c"),
        )
        # The scripts section is inside the profile (before </profile>)
        self.assertLess(content.index("<scripts>"), content.index("</profile>"))

    def test_agama_post_install_appended(self):
        out = self.engine.generate(
            os_type=OSType.OPENSUSE,
            version_id="16.0",
            vm_name="web02",
            user_config=self._base_config(
                "agama-server.json",
                post_install_commands=["systemctl enable bar"],
            ),
            output_path=self.tmp,
            template_name="agama-server.json",
        )
        data = json.loads(out.read_text())
        post = data["scripts"]["post"]
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0]["name"], "virtui-post-install.sh")
        self.assertTrue(post[0]["chroot"])
        self.assertTrue(post[0]["content"].startswith("#!/bin/sh"))
        self.assertIn("systemctl enable bar", post[0]["content"])

    def test_no_post_install_is_noop(self):
        out = self.engine.generate(
            os_type=OSType.OPENSUSE,
            version_id="24.3",
            vm_name="web03",
            user_config=self._base_config("autoyast-server.xml"),
            output_path=self.tmp,
            template_name="autoyast-server.xml",
        )
        content = out.read_text()
        ET.fromstring(content)
        self.assertNotIn("<scripts>", content)


if __name__ == "__main__":
    unittest.main()
