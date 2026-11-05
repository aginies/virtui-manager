"""
Tests for VManagerCMD class.
"""

import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import libvirt

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.constants import AppInfo, DeploySummaryText, StaticText
from vmanager.deploy_config import load_deploy_config
from vmanager.machine_mode import EXIT_OK
from vmanager.provisioning.os_provider import OSType
from vmanager.vmanager_cmd import VManagerCMD


class TestVManagerCMD(unittest.TestCase):
    @patch("vmanager.vmanager_cmd.load_config")
    @patch("vmanager.vmanager_cmd.get_log_path")
    @patch("vmanager.vmanager_cmd.CLILogger")
    def setUp(self, mock_logger, mock_log_path, mock_load_config):
        # Mock configuration
        self.mock_config = {
            "servers": [
                {"name": "local", "uri": "qemu:///system", "autoconnect": False},
                {"name": "remote", "uri": "qemu+ssh://user@remote/system", "autoconnect": False},
            ]
        }
        mock_load_config.return_value = self.mock_config
        mock_log_path.return_value = "/tmp/vmanager_test.log"

        # Mock VMService
        self.mock_vm_service = MagicMock()

        # Initialize VManagerCMD with mocked VMService
        # Capture stdout to avoid printing during tests
        with patch("sys.stdout", MagicMock()):
            self.cmd = VManagerCMD(vm_service=self.mock_vm_service)

    def test_connect_by_name(self):
        """Test connecting to a configured server by name."""
        self.mock_vm_service.connect.return_value = MagicMock()

        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect("local")

        self.mock_vm_service.connect.assert_called_with("qemu:///system")
        self.assertIn("local", self.cmd.active_connections)

    def test_connect_by_uri(self):
        """Test connecting directly to a URI."""
        self.mock_vm_service.connect.return_value = MagicMock()

        with patch("sys.stdout", MagicMock()):
            # Direct URI connection
            self.cmd.do_connect("qemu:///system")

        self.mock_vm_service.connect.assert_called_with("qemu:///system")
        # For qemu:///system, extract_server_name_from_uri returns "Local"
        self.assertIn("Local", self.cmd.active_connections)

    def test_connect_by_remote_uri(self):
        """Test connecting directly to a remote URI."""
        self.mock_vm_service.connect.return_value = MagicMock()

        uri = "qemu+ssh://user@another-host/system"
        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect(uri)

        self.mock_vm_service.connect.assert_called_with(uri)
        # extract_server_name_from_uri should return "another-host"
        self.assertIn("another-host", self.cmd.active_connections)

    def test_connect_multiple(self):
        """Test connecting to multiple targets (name and URI)."""
        self.mock_vm_service.connect.return_value = MagicMock()

        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect("remote qemu:///system")

        self.assertEqual(self.mock_vm_service.connect.call_count, 2)
        self.assertIn("remote", self.cmd.active_connections)
        self.assertIn("Local", self.cmd.active_connections)

    def test_connect_all(self):
        """Test 'connect all' command."""
        self.mock_vm_service.connect.return_value = MagicMock()

        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect("all")

        self.assertEqual(self.mock_vm_service.connect.call_count, 2)
        self.assertIn("local", self.cmd.active_connections)
        self.assertIn("remote", self.cmd.active_connections)

    def test_connect_already_connected(self):
        """Test connecting to an already connected server."""
        self.cmd.active_connections["local"] = MagicMock()

        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect("local")

        # Should not try to connect again
        self.assertEqual(self.mock_vm_service.connect.call_count, 0)

    def test_connect_invalid_name(self):
        """Test connecting to an invalid server name."""
        with patch("sys.stdout", MagicMock()):
            self.cmd.do_connect("nonexistent")

        self.assertEqual(self.mock_vm_service.connect.call_count, 0)
        self.assertEqual(len(self.cmd.active_connections), 0)

    def test_complete_connect(self):
        """Test completion for connect command."""
        # Configured: local, remote
        # We also add "all" in the completion
        completions = self.cmd.complete_connect("loc", "connect loc", 8, 11)
        self.assertEqual(completions, ["local"])

        completions = self.cmd.complete_connect("", "connect ", 8, 8)
        # Should include all configured servers + "all"
        expected = sorted(["local", "remote", "all"])
        self.assertEqual(completions, expected)

    def test_complete_connect_with_active(self):
        """Test completion for connect command includes active connections."""
        self.cmd.active_connections["ActiveServer"] = MagicMock()

        completions = self.cmd.complete_connect("Act", "connect Act", 8, 11)
        self.assertIn("ActiveServer", completions)

    def test_list_servers(self):
        """Test list_servers shows all configured servers."""
        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_list_servers("")

        out = buf.getvalue()
        self.assertIn("Configured servers (2):", out)
        self.assertIn("local", out)
        self.assertIn("remote", out)
        self.assertIn("qemu:///system", out)
        # URI credentials must be sanitized
        self.assertNotIn("user@remote", out)

    def test_list_servers_autoconnect_flag(self):
        """Test list_servers shows the autoconnect flag."""
        self.cmd.servers = [
            {"name": "local", "uri": "qemu:///system", "autoconnect": True},
        ]
        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_list_servers("")

        out = buf.getvalue()
        self.assertIn("Autoconnect", out)
        self.assertIn("yes", out)

    def test_list_servers_empty(self):
        """Test list_servers with no configured servers."""
        self.cmd.servers = []
        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_list_servers("")

        self.assertIn("No servers configured.", buf.getvalue())

    def test_list_connected_servers(self):
        """Test list_connected_servers shows active connections with URIs."""
        conn = MagicMock()
        conn.getURI.return_value = "qemu+ssh://user@remote/system"
        self.cmd.active_connections["remote"] = conn

        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_list_connected_servers("")

        out = buf.getvalue()
        self.assertIn("Connected servers (1):", out)
        self.assertIn("remote", out)
        # URI credentials must be sanitized
        self.assertNotIn("user@remote", out)

    def test_list_connected_servers_empty(self):
        """Test list_connected_servers with no active connections."""
        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_list_connected_servers("")

        self.assertIn("Not connected to any server", buf.getvalue())

    def test_version(self):
        """Test version displays the application name and version."""
        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_version("")

        out = buf.getvalue()
        self.assertIn(AppInfo.namecase, out)
        self.assertIn(AppInfo.version, out)

    def _make_vm_conn(self, vm_name="vm1", active=True):
        """Helper: a connected server with a single VM domain."""
        domain = MagicMock()
        domain.name.return_value = vm_name
        domain.UUIDString.return_value = "uuid-1"
        domain.isActive.return_value = active
        conn = MagicMock()
        conn.listAllDomains.return_value = [domain]
        conn.lookupByName.return_value = domain
        self.cmd.active_connections["local"] = conn
        return conn, domain

    def test_reboot(self):
        """Test reboot sends an ACPI reboot to a running VM."""
        conn, domain = self._make_vm_conn()

        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_reboot("vm1")

        domain.reboot.assert_called_once_with(0)
        self.assertIn("Sent reboot signal to VM 'vm1'.", buf.getvalue())

    def test_reboot_not_running(self):
        """Test reboot refuses a stopped VM."""
        conn, domain = self._make_vm_conn(active=False)

        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_reboot("vm1")

        domain.reboot.assert_not_called()
        self.assertIn("is not running", buf.getvalue())

    def test_vm_stats(self):
        """Test vm_stats displays runtime statistics."""
        conn, domain = self._make_vm_conn()
        self.mock_vm_service.get_vm_runtime_stats.return_value = {
            "status": "running",
            "cpu_percent": 12.5,
            "mem_percent": 30.0,
            "disk_read_kbps": 1.0,
            "disk_write_kbps": 2.0,
            "net_rx_kbps": 3.0,
            "net_tx_kbps": 4.0,
        }

        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_vm_stats("vm1")

        out = buf.getvalue()
        self.assertIn("vm1", out)
        self.assertIn("CPU 12.5%", out)
        self.assertIn("Mem 30.0%", out)

    def test_rename_vm(self):
        """Test rename_vm renames a stopped VM and invalidates the cache."""
        conn, domain = self._make_vm_conn(vm_name="old")
        with patch("vmanager.vmanager_cmd.rename_vm") as mock_rename:
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_rename_vm("old new")

        mock_rename.assert_called_once_with(domain, "new")
        self.mock_vm_service.invalidate_domain_cache.assert_called_once()
        self.assertIn("renamed to 'new'", buf.getvalue())

    def test_rename_vm_usage(self):
        """Test rename_vm with a wrong number of arguments."""
        self._make_vm_conn()

        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_rename_vm("old")

        self.assertIn("Usage: rename_vm <vm_name> <new_name>", buf.getvalue())

    def test_list_volumes(self):
        """Test list_volumes lists the volumes of a pool."""
        conn = MagicMock()
        self.cmd.active_connections["local"] = conn
        pool_mock = MagicMock()
        with (
            patch(
                "vmanager.vmanager_cmd.list_storage_pools",
                return_value=[{"name": "default", "pool": pool_mock}],
            ),
            patch(
                "vmanager.vmanager_cmd.list_storage_volumes",
                return_value=[
                    {
                        "name": "vol1.qcow2",
                        "type": "file",
                        "capacity": 10 * 1024**3,
                        "allocation": 5 * 1024**3,
                    }
                ],
            ),
        ):
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_list_volumes("")

        out = buf.getvalue()
        self.assertIn("default", out)
        self.assertIn("vol1.qcow2", out)

    def test_volume_usage(self):
        """Test volume_usage lists the VMs using a volume."""
        conn = MagicMock()
        self.cmd.active_connections["local"] = conn
        vol = MagicMock()
        vol.path.return_value = "/var/lib/libvirt/vol1.qcow2"
        vol.name.return_value = "vol1.qcow2"
        user_vm = MagicMock()
        user_vm.name.return_value = "vm1"
        with (
            patch("vmanager.vmanager_cmd.find_volume", return_value=vol),
            patch("vmanager.vmanager_cmd.find_vms_using_volume", return_value=[user_vm]),
        ):
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_volume_usage("default vol1.qcow2")

        self.assertIn("vm1", buf.getvalue())

    def test_network_usage(self):
        """Test network_usage lists the VMs using a network."""
        conn = MagicMock()
        self.cmd.active_connections["local"] = conn
        with patch("vmanager.vmanager_cmd.get_vms_using_network", return_value=["vm1"]):
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_network_usage("default")

        out = buf.getvalue()
        self.assertIn("default", out)
        self.assertIn("vm1", out)

    def test_network_usage_not_found(self):
        """Test network_usage with an unknown network."""
        conn = MagicMock()
        conn.networkLookupByName.side_effect = libvirt.libvirtError("no such network")
        self.cmd.active_connections["local"] = conn

        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_network_usage("missing")

        self.assertIn("not found", buf.getvalue())

    def test_host_capabilities(self):
        """Test host_capabilities displays arch, CPU models and machine types."""
        conn = MagicMock()
        self.cmd.active_connections["local"] = conn
        with (
            patch("vmanager.vmanager_cmd.get_host_architecture", return_value="x86_64"),
            patch("vmanager.vmanager_cmd.get_cpu_models", return_value=["Haswell", "IvyBridge"]),
            patch(
                "vmanager.vmanager_cmd.get_latest_machine_types",
                return_value={"pc-i440fx": "pc.i440fx.8.1", "pc-q35": "pc.q35.8.1"},
            ),
            patch("vmanager.vmanager_cmd.get_host_domain_capabilities", return_value=None),
            patch(
                "vmanager.vmanager_cmd.get_video_domain_capabilities",
                return_value={"video_models": ["virtio"]},
            ),
        ):
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_host_capabilities("")

        out = buf.getvalue()
        self.assertIn("x86_64", out)
        self.assertIn("Haswell", out)
        self.assertIn("pc.q35.8.1", out)

    def test_list_isos(self):
        """Test list_isos lists ISOs for a distro and version."""
        conn = MagicMock()
        self.cmd.active_connections["local"] = conn
        os_version = MagicMock()
        os_version.display_name = "openSUSE Leap 15.6"
        registry = MagicMock()
        registry.get_supported_os_types.return_value = [OSType.OPENSUSE]
        registry.find_version.return_value = os_version
        provisioner = MagicMock()
        provisioner.provider_registry = registry
        provisioner.get_iso_list.return_value = [
            {"name": "opensuse-15.6.iso", "url": "http://example.com/iso.iso", "size": "Unknown"}
        ]
        with patch("vmanager.vmanager_cmd.VMProvisioner", return_value=provisioner):
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_list_isos("openSUSE 15.6")

        registry.find_version.assert_called_once_with(OSType.OPENSUSE, "15.6")
        self.assertIn("opensuse-15.6.iso", buf.getvalue())

    def test_list_isos_versions(self):
        """Test list_isos lists the supported versions when no version is given."""
        conn = MagicMock()
        self.cmd.active_connections["local"] = conn
        registry = MagicMock()
        registry.get_supported_versions.return_value = [
            MagicMock(version_id="15.5"),
            MagicMock(version_id="15.6"),
        ]
        provisioner = MagicMock()
        provisioner.provider_registry = registry
        with patch("vmanager.vmanager_cmd.VMProvisioner", return_value=provisioner):
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_list_isos("openSUSE")

        registry.get_supported_versions.assert_called_once_with(OSType.OPENSUSE)
        self.assertIn("Supported versions for openSUSE: 15.5, 15.6", buf.getvalue())

    def test_list_isos_no_args(self):
        """Test list_isos lists the supported distros when no argument is given."""
        conn = MagicMock()
        self.cmd.active_connections["local"] = conn
        provisioner = MagicMock()
        provisioner.provider_registry.get_supported_os_types.return_value = [
            OSType.OPENSUSE,
            OSType.ARCHLINUX,
        ]
        with patch("vmanager.vmanager_cmd.VMProvisioner", return_value=provisioner):
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_list_isos("")

        self.assertIn("Supported distros: Arch Linux, openSUSE", buf.getvalue())

    def test_list_isos_distro_with_space(self):
        """Test list_isos handles distro names containing spaces."""
        conn = MagicMock()
        self.cmd.active_connections["local"] = conn
        registry = MagicMock()
        registry.get_supported_versions.return_value = [MagicMock(version_id="13")]
        provisioner = MagicMock()
        provisioner.provider_registry = registry
        with patch("vmanager.vmanager_cmd.VMProvisioner", return_value=provisioner):
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_list_isos("Arch Linux")

        registry.get_supported_versions.assert_called_once_with(OSType.ARCHLINUX)
        self.assertIn("Supported versions for Arch Linux: 13", buf.getvalue())

    def test_list_isos_unknown_distro(self):
        """Test list_isos with an unknown distro."""
        conn = MagicMock()
        self.cmd.active_connections["local"] = conn
        provisioner = MagicMock()
        provisioner.provider_registry.get_supported_os_types.return_value = [OSType.OPENSUSE]
        with patch("vmanager.vmanager_cmd.VMProvisioner", return_value=provisioner):
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_list_isos("FreeBSD 14")

        self.assertIn("Unknown distro", buf.getvalue())

    def test_list_cached_isos(self):
        """Test list_cached_isos lists the local ISO cache."""
        conn = MagicMock()
        self.cmd.active_connections["local"] = conn
        provisioner = MagicMock()
        provisioner.get_cached_isos.return_value = [
            {"name": "cached.iso", "url": "cached.iso", "date": "2026-01-01 00:00 (Cached)"}
        ]
        with patch("vmanager.vmanager_cmd.VMProvisioner", return_value=provisioner):
            with redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_list_cached_isos("")

        self.assertIn("cached.iso", buf.getvalue())


class TestDeployFromConfig(unittest.TestCase):
    """Tests for the ``vmc --json`` deploy path and ``prepare_json``."""

    @patch("vmanager.vmanager_cmd.load_config")
    @patch("vmanager.vmanager_cmd.get_log_path")
    @patch("vmanager.vmanager_cmd.CLILogger")
    def setUp(self, mock_logger, mock_log_path, mock_load_config):
        self.mock_config = {
            "servers": [
                {"name": "local", "uri": "qemu:///system", "autoconnect": False},
            ],
            "ISO_DOWNLOAD_PATH": "/tmp/iso_cache",
        }
        mock_load_config.return_value = self.mock_config
        mock_log_path.return_value = "/tmp/vmanager_test.log"
        self.mock_vm_service = MagicMock()
        with patch("sys.stdout", MagicMock()):
            self.cmd = VManagerCMD(vm_service=self.mock_vm_service)

    def _no_domain_error(self):
        import libvirt

        class NoDomainError(libvirt.libvirtError):
            def get_error_code(self):
                return libvirt.VIR_ERR_NO_DOMAIN

        return NoDomainError("no domain")

    def _write_cfg(self, obj):
        import json
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(obj, f)
            path = f.name
        self.addCleanup(os.unlink, path)
        return path

    def test_connect_one_returns_name_on_success(self):
        self.mock_vm_service.connect.return_value = MagicMock()
        with patch("sys.stdout", MagicMock()):
            name = self.cmd._connect_one("local")
        self.assertEqual(name, "local")
        self.assertIn("local", self.cmd.active_connections)

    def test_connect_one_returns_none_on_failure(self):
        self.mock_vm_service.connect.return_value = None
        with patch("sys.stdout", MagicMock()):
            name = self.cmd._connect_one("local")
        self.assertIsNone(name)

    def test_build_automation_config(self):
        cfg = {
            "autoinstall": {
                "enabled": True,
                "template": "autoyast-server.xml",
                "packages": ["ssh"],
                "username": "admin",
                "user_password": "pw",
                "root_password": "rp",
                "timezone": "Europe/Zurich",
                "scc": {"scc_email": "a@b.c", "scc_reg_code": "XYZ"},
            }
        }
        ac = self.cmd._build_automation_config(cfg, "web01")
        self.assertEqual(ac["template_name"], "autoyast-server.xml")
        self.assertEqual(ac["hostname"], "web01")
        self.assertEqual(ac["packages"], ["ssh"])
        self.assertEqual(ac["timezone"], "Europe/Zurich")
        self.assertEqual(ac["scc_email"], "a@b.c")
        self.assertEqual(ac["scc_reg_code"], "XYZ")

    def test_build_automation_config_timezone_default(self):
        """When the config omits a timezone, the automation config defaults to UTC."""
        cfg = {"autoinstall": {"enabled": True, "template": "autoyast-server.xml"}}
        ac = self.cmd._build_automation_config(cfg, "web01")
        self.assertEqual(ac["timezone"], "UTC")

    def test_build_automation_config_disabled(self):
        cfg = {"autoinstall": {"enabled": False, "template": "x"}}
        self.assertIsNone(self.cmd._build_automation_config(cfg, "web01"))
        self.assertIsNone(self.cmd._build_automation_config({}, "web01"))

    def test_resolve_iso_url_url(self):
        prov = MagicMock()
        cfg = {"iso": {"source": "url", "url": "https://x/y.iso"}}
        self.assertEqual(self.cmd._resolve_iso_url(prov, cfg), "https://x/y.iso")

    def test_resolve_iso_url_pool(self):
        prov = MagicMock()
        prov.check_pool_volume.return_value = "/path/iso"
        cfg = {"iso": {"source": "pool", "pool": "default", "volume": "y.iso"}}
        self.assertEqual(self.cmd._resolve_iso_url(prov, cfg), "/path/iso")
        prov.check_pool_volume.assert_called_with("y.iso", "default")

    def test_resolve_iso_url_cached(self):
        prov = MagicMock()
        cfg = {"iso": {"source": "cached", "volume": "y.iso"}}
        result = self.cmd._resolve_iso_url(prov, cfg)
        self.assertEqual(result, str(Path("/tmp/iso_cache") / "y.iso"))

    def test_resolve_iso_url_repository(self):
        prov = MagicMock()
        version = MagicMock()
        version.version_id = "24.3"
        version.display_name = "openSUSE Leap 24.3"
        prov.provider_registry.get_supported_versions.return_value = [version]
        prov.get_iso_list.return_value = [
            {"name": "openSUSE-Leap-24.3.iso", "url": "https://x/24.3.iso"},
            {"name": "openSUSE-Leap-24.3-dvd.iso", "url": "https://x/24.3-dvd.iso"},
        ]
        cfg = {"iso": {"source": "repository", "version": "24.3", "name": "24.3.iso"}}
        with patch("sys.stdout", MagicMock()):
            result = self.cmd._resolve_iso_url(prov, cfg)
        self.assertEqual(result, "https://x/24.3.iso")

    def test_resolve_iso_url_repository_prefers_stored_url(self):
        """When prepare_json captured an exact URL, it is used verbatim (no re-fetch)."""
        prov = MagicMock()
        cfg = {
            "iso": {
                "source": "repository",
                "version": "24.3",
                "url": "https://x/24.3-dvd.iso",
            }
        }
        result = self.cmd._resolve_iso_url(prov, cfg)
        self.assertEqual(result, "https://x/24.3-dvd.iso")
        # The stored URL short-circuits resolution, so no re-fetch happens.
        prov.get_iso_list.assert_not_called()

    def test_deploy_from_config_dryrun(self):

        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "memory_mb": 2048,
                "vcpu": 1,
                "iso": {"source": "url", "url": "https://x/y.iso"},
                "autoinstall": {
                    "enabled": True,
                    "template": "autoyast-server.xml",
                    "packages": ["ssh", "vim"],
                    "scc": {"scc_email": "a@b.c", "scc_reg_code": "XYZ"},
                },
            }
        )
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            redirect_stdout(io.StringIO()) as buf,
        ):
            result = self.cmd.deploy_from_config(cfg_path, dryrun=True)

        out = buf.getvalue()
        self.assertIn("Summary of Actions", out)
        self.assertIn("web01", out)
        self.assertIn("Automated Install: Yes", out)
        self.assertIn("Extra Packages: ssh, vim", out)
        self.assertIn("SCC Registration: Yes", out)
        # provision_vm must NOT be called in dry-run
        mock_prov.return_value.provision_vm.assert_not_called()
        # and the caller can read the result instead of the prose
        self.assertEqual(result["domain"], "web01")
        self.assertEqual(result["storage_pool"], "default")
        self.assertTrue(result["dryrun"])
        self.assertTrue(result["automated_install"])

    # What a caller must never see after asking "what would happen": a name on
    # the hypervisor that was not there before.
    MUTATING = {
        "createXML",
        "defineXML",
        "create",
        "createWithFlags",
        "storageVolCreateXML",
        "storagePoolCreateXML",
        "upload",
        "download",
        "stream",
        "delete",
        "destroy",
        "poweroff",
        "reboot",
        "reset",
        "undefine",
        "undefineFlags",
        "rename",
        "setMetadata",
        "attachDevice",
        "detachDevice",
    }

    def _overlay_cfg(self):
        """A boot config whose disk is not there yet, but names a base to overlay."""
        return self._write_cfg(
            {
                "server": "local",
                "vm_name": "sidekick-worker01",
                "vm_type": "custom",
                "memory_mb": 2048,
                "vcpu": 2,
                "disk_size_gb": 20,
                "disk": {"volume": "sidekick-worker01", "backing_volume": "base"},
                "seed": {"volume": "sidekick-worker01-seed"},
            }
        )

    def _boot_dry_run(self, cfg_path, present, active=False):
        """Run a boot deploy with `dryrun` and give back what the hypervisor saw.

        `active` is whether the pool is started, which decides whether its
        volumes can be read at all. The deploy runs under a machine session so
        that a test reads the status a caller reads: the prose a dry run prints
        names the volume whether it found it or refused the deploy for it, so a
        test that looks for a name cannot tell the two apart.
        """
        from vmanager.machine_mode import MachineError, MachineSession

        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        pool = self._pool_with(*present)
        pool.isActive.return_value = active
        conn.storagePoolLookupByName.return_value = pool
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        answer = io.StringIO()
        session = MachineSession("deploy", stdout=answer)
        with (
            patch("vmanager.storage_manager.create_overlay_volume") as overlay,
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            redirect_stdout(io.StringIO()) as buf,
        ):
            try:
                result = self.cmd.deploy_from_config(cfg_path, dryrun=True, session=session)
            except MachineError as error:
                status = session.fail(error)  # What `vmc --machine` answers.
            else:
                status = session.succeed(result)
        return result, buf.getvalue(), conn, pool, overlay, mock_prov, status

    def test_a_dry_run_of_an_overlay_deploy_makes_no_volume(self):
        """The overlay is the artifact a dry run is asked about most.

        The pool is started here, because only a started pool can be asked
        whether the disk is in it yet: that is what the summary reports the
        deploy would make.
        """
        cfg_path = self._overlay_cfg()
        result, out, _conn, pool, overlay, _prov, status = self._boot_dry_run(
            cfg_path, present=["base", "sidekick-worker01-seed"], active=True
        )
        self.assertEqual(status, EXIT_OK)
        self.assertTrue(result["dryrun"])
        overlay.assert_not_called()
        self.assertIn("would be made as an overlay of: base", out)
        self.assertEqual(self._pool_start_lines(out), [], "a started pool has nothing to start")

    def test_a_dry_run_of_a_boot_deploy_defines_no_domain(self):
        """Even when everything it names is there, a dry run defines nothing."""
        cfg_path = self._boot_cfg()
        _result, _out, _conn, _pool, _overlay, prov, status = self._boot_dry_run(
            cfg_path, present=["sidekick-worker01", "sidekick-worker01-seed"]
        )
        self.assertEqual(status, EXIT_OK)
        prov.return_value.provision_existing_volume.assert_not_called()

    def test_a_dry_run_leaves_the_hypervisor_alone(self):
        """Nothing the hypervisor could still carry away after a dry run."""
        cfg_path = self._overlay_cfg()
        result, _out, conn, pool, _overlay, _prov, status = self._boot_dry_run(
            cfg_path, present=["base", "sidekick-worker01-seed"]
        )
        for seen in (conn, pool):
            touched = {call[0] for call in seen.method_calls}
            self.assertEqual(touched & self.MUTATING, set())
        self.assertEqual(status, EXIT_OK)
        self.assertFalse(pool.isActive(), "a dry run leaves the pool as it found it")
        self.assertTrue(result["dryrun"])
        self.assertEqual(result["domain"], "sidekick-worker01")
        self.assertEqual(result["volume"], "sidekick-worker01")

    def test_a_dry_run_reports_a_pool_it_would_start(self):
        """A pool that is defined but not started cannot be read, so say it would start.

        While a pool is down libvirt answers "no such volume" for every volume in
        it, so a dry run that may not start the pool used to refuse, with status
        5, a deploy that would have booted. A caller checking a config before
        spending an hour on it was told the config was wrong.
        """
        cfg_path = self._boot_cfg()
        result, out, _conn, pool, overlay, prov, status = self._boot_dry_run(
            cfg_path, present=["sidekick-worker01", "sidekick-worker01-seed"]
        )
        self.assertEqual(status, EXIT_OK)
        self.assertIn("Storage pool: default would be started", out)
        self.assertTrue(result["dryrun"])
        self.assertEqual(result["volume"], "sidekick-worker01")
        # What would be, and nothing of it done.
        self.assertFalse(pool.isActive())
        pool.create.assert_not_called()
        overlay.assert_not_called()
        prov.return_value.provision_existing_volume.assert_not_called()

    def test_a_dry_run_answers_five_for_a_volume_a_started_pool_lacks(self):
        """A pool that can be read still answers for the volume that is not in it.

        The other way this route fails must not be softened with it: the volume
        the pool names as missing is the deploy that cannot happen, and says so
        in the same words a deploy without `dryrun` says it.
        """
        from vmanager.machine_mode import EXIT_NOT_FOUND, MachineError, MachineSession

        cfg_path = self._boot_cfg()
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        # _pool_with is started unless a test says otherwise: this volume is
        # absent from a pool that was asked, not from a pool that could not answer.
        conn.storagePoolLookupByName.return_value = self._pool_with("another-disk")
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(MachineError) as raised:
                self.cmd.deploy_from_config(cfg_path, dryrun=True, session=MachineSession("deploy"))

        self.assertEqual(raised.exception.status, EXIT_NOT_FOUND)
        self.assertEqual(raised.exception.code, "not_found")
        self.assertEqual(
            str(raised.exception), "No volume 'sidekick-worker01' in the pool 'default'."
        )
        mock_prov.return_value.provision_existing_volume.assert_not_called()

    # --- the two dry-run summaries, label by label -------------------------------------
    #
    # A dry run is read by a person deciding whether to run the deploy, so the two
    # summaries are one contract: the same label for the same thing, in the same
    # order, with nothing the deploy would do left unmentioned.

    # What both routes do, and so both have to name, in the order they name it.
    # `VM Type`, `ISO`, `Boots the volume` and `Boot order` are left out on purpose:
    # one route installs and the other boots a disk it already has, so those are two
    # different facts, not one fact said twice.
    SHARED_LABELS = (
        "VM Name:",
        "Server:",
        "Memory:",
        "Seed CD-ROM:",
        "Network:",
        "Storage Pool:",
        "Boot Mode:",
        "Graphics:",
    )

    def _install_dry_run(self, cfg_path, pool=None):
        """An install dry run, with the summary it printed and what it was given.

        The mirror of `_boot_dry_run` for the other route, so one test can read both
        summaries. The provisioner comes back too: asking what would happen is not
        permission to make it happen.
        """
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        conn.storagePoolLookupByName.return_value = pool or self._pool_with("seed-a")
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")
        with (
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            redirect_stdout(io.StringIO()) as buf,
        ):
            result = self.cmd.deploy_from_config(cfg_path, dryrun=True)
        return result, buf.getvalue(), mock_prov

    @staticmethod
    def _summary_labels(out):
        """The label of every labelled line of a summary, in the order printed."""
        return [f"{line.split(':', 1)[0]}:" for line in out.splitlines() if ": " in line]

    @staticmethod
    def _pool_start_lines(out):
        """The lines of a summary that say the pool would have to be started.

        Named by what the line is about rather than by the words in it: a summary now
        says what becomes of the machine too, and that sentence starts with the same
        two words about a different thing.
        """
        return [line for line in out.splitlines() if "pool" in line.lower() and "would be" in line]

    def test_both_dry_run_summaries_share_their_labels_and_their_order(self):
        """The same thing is named with the same words, in the same place.

        Each route used to write its own block, so a label could appear in one and
        never reach the other; this is the guard that keeps the two from drifting.
        Nothing is provisioned while asking, on either route.
        """
        _result, install_out, install_prov = self._install_dry_run(
            self._install_cfg(seed={"volume": "seed-a"})
        )
        _result, boot_out, _conn, _pool, _overlay, boot_prov, _status = self._boot_dry_run(
            self._boot_cfg(), present=["sidekick-worker01", "sidekick-worker01-seed"], active=True
        )
        install_prov.return_value.provision_vm.assert_not_called()
        boot_prov.return_value.provision_existing_volume.assert_not_called()

        install_header = [line for line in install_out.splitlines() if line.startswith("---")]
        boot_header = [line for line in boot_out.splitlines() if line.startswith("---")]
        self.assertEqual(install_header, boot_header)
        self.assertTrue(install_header, "a summary has to say it is a summary")

        for out in (install_out, boot_out):
            labels = [label for label in self._summary_labels(out) if label in self.SHARED_LABELS]
            self.assertEqual(labels, list(self.SHARED_LABELS))

    def test_a_boot_dry_run_names_the_pool_the_machine_lands_in(self):
        """Both routes deploy into a pool, so both have to say which one.

        The route that boots a volume resolved the pool, read its volumes and would
        make the overlay in it, and named it only when it was down and would have to
        be started.
        """
        _result, out, *_rest = self._boot_dry_run(
            self._boot_cfg(), present=["sidekick-worker01", "sidekick-worker01-seed"], active=True
        )
        self.assertIn("Storage Pool: default", out)
        self.assertEqual(self._pool_start_lines(out), [], "a started pool has nothing to start")

        _result, out, *_rest = self._boot_dry_run(
            self._boot_cfg(storage_pool="sidekick"),
            present=["sidekick-worker01", "sidekick-worker01-seed"],
            active=True,
        )
        self.assertIn("Storage Pool: sidekick", out)

    def test_both_dry_runs_name_the_screen_the_machine_would_get(self):
        """A screen is a device the domain is defined with, on either route.

        `null` asks for no screen and is one file's answer on both routes, so it is
        one word in both summaries too — not `None` read back out of Python on one
        and `none` as the file wrote it on the other.
        """
        for graphics, expected in (
            (None, "Graphics: none"),
            ("vnc", "Graphics: vnc"),
            ("spice", "Graphics: spice"),
        ):
            with self.subTest(graphics=graphics):
                _result, boot_out, *_rest = self._boot_dry_run(
                    self._boot_cfg(graphics=graphics),
                    present=["sidekick-worker01", "sidekick-worker01-seed"],
                    active=True,
                )
                self.assertIn(expected, boot_out)

                _result, install_out, _prov = self._install_dry_run(
                    self._install_cfg(graphics=graphics)
                )
                self.assertIn(expected, install_out)

    def test_both_dry_runs_say_whether_the_machine_would_be_started(self):
        """`start` decides how the deploy ends, and no label named it on either route.

        A deploy that ends with the machine running and one that ends with it
        defined and stopped are two different answers to "what would this do".
        """
        for start, expected, absent in (
            (None, "The machine would be started once it is defined.", "left defined"),
            (False, "The machine would be left defined and stopped.", "would be started"),
        ):
            with self.subTest(start=start):
                install_extra = {} if start is None else {"start": start}
                _result, install_out, _prov = self._install_dry_run(
                    self._install_cfg(**install_extra)
                )
                _result, boot_out, *_rest = self._boot_dry_run(
                    self._boot_cfg(**install_extra),
                    present=["sidekick-worker01", "sidekick-worker01-seed"],
                    active=True,
                )
                for out in (install_out, boot_out):
                    self.assertIn(expected, out)
                    self.assertNotIn(absent, out)

    def test_the_summaries_say_nothing_the_constants_do_not_hold(self):
        """Every line of a summary is a line the constants module holds.

        The two blocks are one contract written in two places: a sentence spelled
        out at a call site is the drift this ticket is about, and cannot be told
        apart from the shared wording by reading one of them.
        """
        patterns = [
            re.compile("^" + ".*?".join(map(re.escape, re.split(r"\{\w+\}", piece))) + "$")
            for value in vars(DeploySummaryText).values()
            if isinstance(value, str)
            for piece in value.splitlines()
            if piece
        ]
        _result, install_out, _prov = self._install_dry_run(
            self._install_cfg(
                seed={"volume": "seed-a"},
                autoinstall={
                    "enabled": True,
                    "template": "autoyast-server.xml",
                    "packages": ["ssh", "vim"],
                    "scc": {"scc_email": "a@b.c", "scc_reg_code": "XYZ"},
                },
                post_install={"commands": ["systemctl enable foo"]},
            )
        )
        _result, boot_out, *_rest = self._boot_dry_run(
            self._overlay_cfg(), present=["base", "sidekick-worker01-seed"], active=True
        )
        for out in (install_out, boot_out):
            for line in out.splitlines():
                if line.strip():
                    self.assertTrue(
                        any(pattern.match(line) for pattern in patterns),
                        f"summary line held by no constant: {line!r}",
                    )

    def test_deploy_from_config_vm_exists(self):

        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
            }
        )
        conn = MagicMock()
        conn.lookupByName.return_value = MagicMock()  # VM exists
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            redirect_stdout(io.StringIO()) as buf,
        ):
            self.cmd.deploy_from_config(cfg_path)

        self.assertIn("already exists", buf.getvalue())
        mock_prov.return_value.provision_vm.assert_not_called()

    def test_deploy_in_machine_mode_fails_with_a_status(self):
        """A deploy that did not happen has to fail, not print and exit 0."""
        from vmanager.machine_mode import EXIT_EXISTS, MachineError, MachineSession

        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
            }
        )
        conn = MagicMock()
        conn.lookupByName.return_value = MagicMock()  # VM exists
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
        ):
            with self.assertRaises(MachineError) as ctx:
                self.cmd.deploy_from_config(cfg_path, session=MachineSession("deploy"))

        self.assertEqual(ctx.exception.status, EXIT_EXISTS)
        self.assertEqual(ctx.exception.code, "already_exists")
        mock_prov.return_value.provision_vm.assert_not_called()

    def test_deploy_in_machine_mode_reports_a_bad_config_file(self):
        from vmanager.machine_mode import EXIT_CONFIG, MachineError, MachineSession

        with patch("sys.stdout", MagicMock()):
            with self.assertRaises(MachineError) as ctx:
                self.cmd.deploy_from_config(
                    "/no/such/deploy.json", session=MachineSession("deploy")
                )

        self.assertEqual(ctx.exception.status, EXIT_CONFIG)
        self.assertEqual(ctx.exception.code, "config_invalid")

    def _boot_cfg(self, **extra):
        """A config that boots a disk the pool already has, with a cloud-init seed."""
        cfg = {
            "server": "local",
            "vm_name": "sidekick-worker01",
            "vm_type": "custom",
            "memory_mb": 2048,
            "vcpu": 2,
            "disk_size_gb": 20,
            "disk": {"volume": "sidekick-worker01"},
            "seed": {"volume": "sidekick-worker01-seed"},
            "metadata": {"mcp-sidekick": "instance", "instance": "worker01"},
        }
        cfg.update(extra)
        return self._write_cfg(cfg)

    def _pool_with(self, *names):
        """A pool that answers for those volumes, and no others.

        A pool that is not started answers "no such volume" for every name, the
        way the binding does: libvirt cannot enumerate a pool it has not
        activated. A double that answered regardless of `isActive()` lets a dry
        run read volumes no deploy could reach, and hides what a dry run says
        about a pool it may not start.
        """
        import libvirt

        pool = MagicMock()
        pool.isActive.return_value = True  # A pool is started unless a test says so.

        def lookup(name):
            if not pool.isActive() or name not in names:
                raise libvirt.libvirtError("No such volume")
            volume = MagicMock()
            volume.path.return_value = f"/var/lib/libvirt/images/{name}.qcow2"
            volume.XMLDesc.return_value = (
                "<volume><target><path>%s</path><format type='qcow2'/></target></volume>"
                % volume.path.return_value
            )
            return volume

        pool.storageVolLookupByName.side_effect = lookup
        return pool

    def test_deploy_from_a_boot_volume_skips_the_installer(self):
        cfg_path = self._boot_cfg()
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        conn.storagePoolLookupByName.return_value = self._pool_with(
            "sidekick-worker01", "sidekick-worker01-seed"
        )
        with (
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
        ):
            self.cmd.deploy_from_config(cfg_path)

        provisioner = mock_prov.return_value
        provisioner.provision_vm.assert_not_called()
        kwargs = provisioner.provision_existing_volume.call_args.kwargs
        self.assertEqual(kwargs["disk_path"], "/var/lib/libvirt/images/sidekick-worker01.qcow2")
        self.assertEqual(
            kwargs["seed_path"], "/var/lib/libvirt/images/sidekick-worker01-seed.qcow2"
        )
        self.assertEqual(kwargs["metadata"]["instance"], "worker01")

    def test_an_install_attaches_the_seed_it_was_named(self):
        """A seed belongs to the machine, not to the way the machine was made."""
        result = self._deploy_an_install(
            self._install_cfg(seed={"volume": "seed-a"}), pool=self._pool_with("seed-a")
        )
        self.assertEqual(self.install_request["seed_path"], "/var/lib/libvirt/images/seed-a.qcow2")
        self.assertEqual(result["seed"], "seed-a")

    def test_a_dry_run_of_an_install_names_the_seed_it_would_attach(self):
        """A dry run answers what the deploy would answer, seed and all.

        The lines that put the seed in the answer sat after the `return` that
        builds it, so a caller asking "what would happen" read back a machine
        with no seed attached and saw nothing wrong with the file.
        """
        result = self._deploy_an_install(
            self._install_cfg(seed={"volume": "seed-a"}),
            pool=self._pool_with("seed-a"),
            dryrun=True,
        )
        self.assertTrue(result["dryrun"])
        self.assertEqual(result["seed"], "seed-a")
        self.assertIsNone(self.install_request, "a dry run must not provision")

    def test_an_install_with_no_seed_has_nothing_to_attach(self):
        result = self._deploy_an_install(self._install_cfg(), pool=self._pool_with())
        self.assertIsNone(self.install_request["seed_path"])
        self.assertNotIn("seed", result)

    def test_an_install_naming_a_seed_the_pool_has_not_got_is_status_five(self):
        """A missing seed is a missing resource, said before anything is made."""
        from vmanager.machine_mode import EXIT_NOT_FOUND, MachineError, MachineSession

        with self.assertRaises(MachineError) as raised:
            self._deploy_an_install(
                self._install_cfg(seed={"volume": "seed-a"}),
                pool=self._pool_with(),
                session=MachineSession("deploy"),
            )
        self.assertEqual(raised.exception.status, EXIT_NOT_FOUND)
        self.assertEqual(raised.exception.code, "not_found")
        self.assertIsNone(self.install_request)

    def test_a_deploy_naming_a_pool_that_is_not_there_is_status_five(self):
        """A pool that is not there is the pool's status, not a bad config file."""
        import libvirt

        from vmanager.machine_mode import EXIT_NOT_FOUND, MachineError, MachineSession

        # The pool vmc would fall back to is there; the one the config names is not.
        cfg_path = self._boot_cfg(storage_pool="sidekick")
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()

        def look_up_pool(name):
            if name != "default":
                raise libvirt.libvirtError("no such pool")
            pool = MagicMock()
            pool.isActive.return_value = True
            return pool

        conn.storagePoolLookupByName.side_effect = look_up_pool
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.VMProvisioner"),
            patch("sys.stdout", MagicMock()),
        ):
            with self.assertRaises(MachineError) as raised:
                self.cmd.deploy_from_config(cfg_path, session=MachineSession("deploy"))

        self.assertEqual(raised.exception.status, EXIT_NOT_FOUND)
        self.assertEqual(raised.exception.code, "not_found")

    def test_an_install_naming_a_pool_that_is_not_there_is_status_five(self):
        """The pool is checked before the two deploy routes part company."""
        import libvirt

        from vmanager.machine_mode import EXIT_NOT_FOUND, MachineError, MachineSession

        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
                "storage_pool": "sidekick",
            }
        )
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()

        def look_up_pool(name):
            if name != "default":
                raise libvirt.libvirtError("no such pool")
            pool = MagicMock()
            pool.isActive.return_value = True
            return pool

        conn.storagePoolLookupByName.side_effect = look_up_pool
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
        ):
            with self.assertRaises(MachineError) as raised:
                self.cmd.deploy_from_config(cfg_path, session=MachineSession("deploy"))

        self.assertEqual(raised.exception.status, EXIT_NOT_FOUND)
        self.assertEqual(raised.exception.code, "not_found")
        mock_prov.return_value.provision_vm.assert_not_called()

    def test_a_boot_volume_that_is_not_there_is_status_five(self):
        from vmanager.machine_mode import EXIT_NOT_FOUND, MachineError, MachineSession

        cfg_path = self._boot_cfg()
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        conn.storagePoolLookupByName.return_value = self._pool_with()  # the disk is not in it
        with (
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
        ):
            with self.assertRaises(MachineError) as raised:
                self.cmd.deploy_from_config(cfg_path, session=MachineSession("deploy"))

        self.assertEqual(raised.exception.status, EXIT_NOT_FOUND)
        self.assertEqual(raised.exception.code, "not_found")
        mock_prov.return_value.provision_existing_volume.assert_not_called()

    def test_deploy_in_machine_mode_reports_progress_as_it_happens(self):
        from vmanager.machine_mode import MachineSession

        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
            }
        )
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        def provision(**kwargs):
            kwargs["progress_callback"]("downloading", 45)
            domain = MagicMock()
            domain.isActive.return_value = True
            return domain

        stream = io.StringIO()
        with (
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
            redirect_stdout(io.StringIO()),
        ):
            mock_prov.return_value.provision_vm.side_effect = provision
            result = self.cmd.deploy_from_config(
                cfg_path, session=MachineSession("deploy", stdout=stream)
            )

        self.assertEqual(
            json.loads(stream.getvalue()),
            {"event": "progress", "stage": "install", "message": "downloading", "percent": 45},
        )
        self.assertTrue(result["running"])

    def test_a_boot_deploy_reports_progress_with_a_stage_of_its_own(self):
        from vmanager.machine_mode import MachineSession

        cfg_path = self._boot_cfg()
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")
        conn.storagePoolLookupByName.return_value = self._pool_with(
            "sidekick-worker01", "sidekick-worker01-seed"
        )

        def provision(**kwargs):
            kwargs["progress_callback"](StaticText.PROVISIONING_DEFINING_VM, 40)
            domain = MagicMock()
            domain.isActive.return_value = True
            return domain

        stream = io.StringIO()
        with (
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
            redirect_stdout(io.StringIO()),
        ):
            mock_prov.return_value.provision_existing_volume.side_effect = provision
            self.cmd.deploy_from_config(cfg_path, session=MachineSession("deploy", stdout=stream))

        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(
            events[0],
            {
                "event": "progress",
                "stage": "boot",
                "message": StaticText.PROVISIONING_DEFINING_VM,
                "percent": 40,
            },
        )

    def _install_cfg(self, **extra):
        """A config that installs from media, the other half of the deploy file."""
        cfg = {
            "server": "local",
            "vm_name": "web01",
            "vm_type": "server",
            "iso": {"source": "url", "url": "https://x/y.iso"},
        }
        cfg.update(extra)
        return self._write_cfg(cfg)

    def _domain_that_is_active(self):
        """A domain like libvirt answers: isActive() is the integer 1, not True."""
        domain = MagicMock()
        domain.isActive.return_value = 1
        return domain

    def _deploy_an_install(self, cfg_path, served=None, pool=None, session=None, dryrun=False):
        """Run an install deploy past the point where the answer is built.

        `served` is the auto-install profile the provisioner says it served, or
        None when it generated none at all. `pool` is the storage pool the
        hypervisor answers with, so a test can say which volumes exist.
        What the provisioner was told is left in `self.install_request`, which
        stays None when the deploy never reached it.
        """
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")
        if pool is not None:
            conn.storagePoolLookupByName.return_value = pool
        domain = self._domain_that_is_active()
        self.install_request = None

        def provision(**kwargs):
            self.install_request = kwargs
            report = kwargs.get("autoinstall_report")
            if served is not None and report:
                report(served)
            return domain

        with (
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
            redirect_stdout(io.StringIO()),
        ):
            mock_prov.return_value.provision_vm.side_effect = provision
            return self.cmd.deploy_from_config(cfg_path, dryrun=dryrun, session=session)

    def _deploy_a_boot_volume(self, cfg_path):
        """Run a boot-a-volume deploy past the point where the answer is built."""
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")
        conn.storagePoolLookupByName.return_value = self._pool_with(
            "sidekick-worker01", "sidekick-worker01-seed"
        )
        with (
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
            redirect_stdout(io.StringIO()),
        ):
            mock_prov.return_value.provision_existing_volume.return_value = (
                self._domain_that_is_active()
            )
            return self.cmd.deploy_from_config(cfg_path)

    def test_an_install_answers_running_as_a_boolean(self):
        """`running` is one type on both routes: a caller can write `is True`."""
        result = self._deploy_an_install(self._install_cfg())

        answer = json.loads(json.dumps(result))
        self.assertIs(answer["running"], True)

    def test_a_boot_deploy_answers_running_as_a_boolean(self):
        """The same field, the same type, whatever made the machine."""
        result = self._deploy_a_boot_volume(self._boot_cfg())

        answer = json.loads(json.dumps(result))
        self.assertIs(answer["running"], True)

    def test_both_routes_answer_the_screen_the_file_asked_for(self):
        """One file answers one `graphics`, whichever way the machine was made.

        `null` is the only way a file asks for no screen, and it is the value both
        routes hand the provisioner, so it is the value both answer with. A route
        that spelled its own word there gave a caller two answers to one file, and
        the word was not a word a file may hold: read back into a deploy file, it
        is refused as a bad config.
        """
        for graphics in (None, "vnc", "spice"):
            with self.subTest(graphics=graphics):
                install = json.loads(
                    json.dumps(self._deploy_an_install(self._install_cfg(graphics=graphics)))
                )
                boot = json.loads(
                    json.dumps(self._deploy_a_boot_volume(self._boot_cfg(graphics=graphics)))
                )

                self.assertEqual(install["graphics"], graphics)
                self.assertEqual(boot["graphics"], graphics)

    def test_the_disk_a_deploy_answers_is_the_size_the_file_asked_for(self):
        """`disk_gb` is the file's own number, and a route without one answers none.

        A deploy that took its size from the `vm_type` profile has a disk like any
        other; what it has not got is a size the file named, and inventing the
        provisioner's default in the answer would say the file had said it. A deploy
        that boots a volume leaves the size out the same way and answers `volume`
        instead: the disk it has is the one it was handed.
        """
        from_profile = json.loads(json.dumps(self._deploy_an_install(self._install_cfg())))
        with_size = json.loads(
            json.dumps(self._deploy_an_install(self._install_cfg(disk_size_gb=40)))
        )
        booting = json.loads(json.dumps(self._deploy_a_boot_volume(self._boot_cfg())))

        self.assertNotIn("disk_gb", from_profile)
        self.assertEqual(with_size["disk_gb"], 40)
        self.assertNotIn("disk_gb", booting)
        self.assertEqual(booting["volume"], "sidekick-worker01")
        self.assertNotIn("volume", with_size)

    def test_the_answer_carries_the_profile_that_was_served(self):
        """A caller inspecting an install needs the URL the guest was told."""
        served = {
            "directory": "/tmp/virtui_automation_web01_4c7aa5b9",
            "file": "autoinst-4c7aa5b9.xml",
            "url": "http://192.168.122.1:8000/autoinst-4c7aa5b9.xml",
        }

        result = self._deploy_an_install(self._install_cfg(), served=served)

        self.assertEqual(json.loads(json.dumps(result))["autoinstall"], served)

    def test_a_machine_with_no_profile_answers_with_no_profile(self):
        """Nothing was served, so there is nothing to say — not an empty object."""
        result = self._deploy_an_install(self._install_cfg())

        self.assertNotIn("autoinstall", json.loads(json.dumps(result)))

    def test_the_route_that_boots_a_volume_reports_no_profile(self):
        """Booting an existing disk generates no auto-install profile."""
        result = self._deploy_a_boot_volume(self._boot_cfg())

        self.assertNotIn("autoinstall", json.loads(json.dumps(result)))

    def test_deploy_in_machine_mode_reports_a_provisioning_failure(self):
        from vmanager.machine_mode import EXIT_OPERATION, MachineError, MachineSession

        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
            }
        )
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
            redirect_stdout(io.StringIO()),
        ):
            mock_prov.return_value.provision_vm.side_effect = RuntimeError("no space left")
            with self.assertRaises(MachineError) as ctx:
                self.cmd.deploy_from_config(cfg_path, session=MachineSession("deploy"))

        self.assertEqual(ctx.exception.status, EXIT_OPERATION)
        self.assertEqual(ctx.exception.code, "provision_failed")
        self.assertIn("no space left", str(ctx.exception))

    def _mirror_that_stalls(self):
        """A mirror that answers the request, then stops sending bytes."""
        response = MagicMock()
        response.getheader.return_value = "1024"
        # What the read of a socket with a timeout raises when a transfer stalls.
        response.read.side_effect = socket.timeout("The read operation timed out")
        response.__enter__.return_value = response
        return response

    def _run_deploy_command(self, cfg_path, answer, provision, **mirror):
        """Run `vmc --machine -C <file>` and return the status it exits with.

        The whole command, so the status asserted below is the one a caller
        gets: the answer object goes to `answer` where the session writes it.
        `mirror` says how the download server behaves: `return_value` for a
        response, `side_effect` for a refusal.
        """
        from vmanager.vmanager_cmd import main as cli_main

        with (
            patch("vmanager.vmanager_cmd.register_error_handler"),
            patch("vmanager.vmanager_cmd.VManagerCMD", return_value=self.cmd),
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("vmanager.vm_provisioner.urllib.request.urlopen", **mirror),
            patch("sys.__stdout__", answer),
            patch("sys.stderr", new_callable=io.StringIO),
            patch.object(sys, "argv", ["vmc", "--machine", "-C", cfg_path]),
        ):
            mock_prov.return_value.provision_vm.side_effect = provision
            return cli_main()

    def _download_media_as_the_provisioner_does(self, dest):
        """A `provision_vm` that only downloads, with the real download code.

        The stall has to come from the transfer and be classified by the real
        ``download_iso``, so what is tested is the answer to a stalled mirror
        rather than to an error the test invented.
        """
        from vmanager.vm_provisioner import VMProvisioner

        def provision(**kwargs):
            return VMProvisioner.download_iso(
                MagicMock(), kwargs["iso_url"], dest, kwargs["progress_callback"]
            )

        return provision

    def test_a_stalled_iso_download_is_answered_with_the_timeout_status(self):
        """Install media that stops downloading is the case status 8 was written for."""
        from vmanager.machine_mode import EXIT_TIMEOUT

        url = "https://mirror.example/SLES-15.iso"
        cfg_path = self._install_cfg(iso={"source": "url", "url": url})
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")
        cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, cache, True)
        answer = io.StringIO()

        status = self._run_deploy_command(
            cfg_path,
            answer,
            self._download_media_as_the_provisioner_does(os.path.join(cache, "SLES-15.iso")),
            return_value=self._mirror_that_stalls(),
        )

        self.assertEqual(status, EXIT_TIMEOUT)
        payload = json.loads(answer.getvalue())
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["code"], "timeout")
        self.assertIn(url, payload["message"])
        self.assertIn("read operation timed out", payload["message"])

    def test_a_mirror_that_refuses_the_download_is_still_a_failed_operation(self):
        """Only a wait that ran out is status 8: a refused download stays 7."""
        import urllib.error

        from vmanager.machine_mode import EXIT_OPERATION

        cfg_path = self._install_cfg()
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")
        cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, cache, True)
        answer = io.StringIO()

        status = self._run_deploy_command(
            cfg_path,
            answer,
            self._download_media_as_the_provisioner_does(os.path.join(cache, "y.iso")),
            side_effect=urllib.error.URLError("404: Not Found"),
        )

        self.assertEqual(status, EXIT_OPERATION)
        payload = json.loads(answer.getvalue())
        self.assertEqual(payload["code"], "provision_failed")
        self.assertIn("404", payload["message"])

    def test_deploy_without_a_session_keeps_printing(self):
        """A human still gets prose: nothing raises when nobody asked for JSON."""
        conn = MagicMock()
        conn.lookupByName.return_value = MagicMock()  # VM exists
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")
        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
            }
        )

        with patch("vmanager.vmanager_cmd.VMProvisioner"), redirect_stdout(io.StringIO()) as buf:
            self.assertIsNone(self.cmd.deploy_from_config(cfg_path))

        self.assertIn("already exists", buf.getvalue())

    def test_deploy_without_a_session_gives_no_result_when_it_failed(self):
        """The exit status of `vmc --json` comes from this return value."""
        with patch("sys.stdout", MagicMock()), redirect_stdout(io.StringIO()):
            self.assertIsNone(self.cmd.deploy_from_config("/no/such/deploy.json"))

    def test_deploy_from_config_passes_show_automation_path(self):
        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
                "autoinstall": {
                    "enabled": True,
                    "template": "autoyast-server.xml",
                    "packages": ["ssh"],
                },
            }
        )
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            redirect_stdout(io.StringIO()),
        ):
            self.cmd.deploy_from_config(cfg_path)

        # The generated auto-install file path must be surfaced to the user.
        mock_prov.return_value.provision_vm.assert_called_once()
        _, kwargs = mock_prov.return_value.provision_vm.call_args
        self.assertTrue(kwargs.get("show_automation_path"))

    def test_deploy_from_config_no_autoinstall_does_not_force_path(self):
        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
            }
        )
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            redirect_stdout(io.StringIO()),
        ):
            self.cmd.deploy_from_config(cfg_path)

        mock_prov.return_value.provision_vm.assert_called_once()
        _, kwargs = mock_prov.return_value.provision_vm.call_args
        # No autoinstall -> no automation -> show_automation_path stays False.
        self.assertFalse(kwargs.get("show_automation_path"))

    def test_an_install_keeps_the_metadata_the_caller_gave(self):
        """The install route is the half of the deploy file most callers use."""
        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
                "metadata": {"mcp-sidekick": "instance", "instance": "worker01"},
            }
        )
        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            redirect_stdout(io.StringIO()),
        ):
            self.cmd.deploy_from_config(cfg_path)

        _, kwargs = mock_prov.return_value.provision_vm.call_args
        self.assertEqual(kwargs["metadata"]["instance"], "worker01")

    # A session remembers the list of machines and what it read about each one.
    # A deploy that changed the hypervisor has to say so, as the resource commands
    # do: a command run after it is answered from those caches, and a machine the
    # deploy added is not in a list that was made before it.

    def test_an_install_forgets_the_list_of_machines(self):
        """A machine installed is one more machine, and only that clearing says so."""
        self._deploy_an_install(self._install_cfg())

        self.mock_vm_service.invalidate_domain_cache.assert_called_once()

    def test_a_boot_deploy_forgets_the_list_of_machines(self):
        """The route that boots a volume adds a machine just the same."""
        self._deploy_a_boot_volume(self._boot_cfg())

        self.mock_vm_service.invalidate_domain_cache.assert_called_once()

    def test_a_deploy_forgets_the_state_remembered_of_the_machine(self):
        """The machine's own state is forgotten by its identity, as the resource
        commands forget it, so an action taken next reads the hypervisor."""
        self._deploy_an_install(self._install_cfg())

        identity_of_the_machine = self.mock_vm_service.get_vm_identity.return_value[0]
        self.mock_vm_service.invalidate_vm_state_cache.assert_called_once_with(
            identity_of_the_machine
        )

    def test_a_refused_deploy_forgets_nothing(self):
        """A name already taken changed no hypervisor, so the caches keep what they hold."""
        conn = MagicMock()
        conn.lookupByName.return_value = MagicMock()  # the name is taken
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with patch("vmanager.vmanager_cmd.VMProvisioner"), redirect_stdout(io.StringIO()) as buf:
            self.assertIsNone(self.cmd.deploy_from_config(self._install_cfg()))

        self.assertIn("already exists", buf.getvalue())
        self._nothing_forgotten()

    def test_a_failed_install_forgets_nothing(self):
        """Emptying the caches for a machine that was never made costs the next
        command a round trip for nothing."""
        from vmanager.machine_mode import MachineError, MachineSession

        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")

        with (
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            patch("sys.stdout", MagicMock()),
            redirect_stdout(io.StringIO()),
        ):
            mock_prov.return_value.provision_vm.side_effect = RuntimeError("no space left")
            with self.assertRaises(MachineError):
                self.cmd.deploy_from_config(self._install_cfg(), session=MachineSession("deploy"))

        self._nothing_forgotten()

    def test_a_failed_boot_deploy_forgets_nothing(self):
        """The other route refused to make the machine, and has nothing to forget."""
        from vmanager.machine_mode import MachineError, MachineSession

        conn = MagicMock()
        conn.lookupByName.side_effect = self._no_domain_error()
        self.cmd.active_connections["local"] = conn
        self.cmd._connect_one = MagicMock(return_value="local")
        conn.storagePoolLookupByName.return_value = self._pool_with(
            "sidekick-worker01", "sidekick-worker01-seed"
        )

        with (
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
            redirect_stdout(io.StringIO()),
        ):
            mock_prov.return_value.provision_existing_volume.side_effect = RuntimeError(
                "the hypervisor refused"
            )
            with self.assertRaises(MachineError):
                self.cmd.deploy_from_config(self._boot_cfg(), session=MachineSession("deploy"))

        self._nothing_forgotten()

    def test_a_dry_run_forgets_nothing(self):
        """A dry run changes no hypervisor, on either route, so it invalidates neither
        the list nor the state."""
        volumes = ["sidekick-worker01", "sidekick-worker01-seed"]
        self._deploy_an_install(self._install_cfg(), dryrun=True)
        self._boot_dry_run(self._boot_cfg(), present=volumes)

        self._nothing_forgotten()

    def _nothing_forgotten(self):
        """Neither of the two clearings a deploy does was asked for."""
        self.mock_vm_service.invalidate_domain_cache.assert_not_called()
        self.mock_vm_service.invalidate_vm_state_cache.assert_not_called()

    def test_deploy_vm_no_args_shows_usage(self):
        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_deploy_vm("")
        self.assertIn("Usage: deploy_vm", buf.getvalue())

    def test_deploy_vm_missing_file(self):
        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_deploy_vm("does-not-exist-12345.json")
        out = buf.getvalue()
        self.assertIn("not found", out)
        self.assertIn("list_json", out)

    def test_deploy_vm_full_path_calls_deploy_from_config(self):
        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
            }
        )
        self.cmd.deploy_from_config = MagicMock()
        with redirect_stdout(io.StringIO()):
            self.cmd.do_deploy_vm(cfg_path)
        self.cmd.deploy_from_config.assert_called_once_with(cfg_path, dryrun=False)

    def test_deploy_vm_dryrun_flag(self):
        cfg_path = self._write_cfg(
            {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
            }
        )
        self.cmd.deploy_from_config = MagicMock()
        with redirect_stdout(io.StringIO()):
            self.cmd.do_deploy_vm(f"{cfg_path} --dryrun")
        self.cmd.deploy_from_config.assert_called_once_with(cfg_path, dryrun=True)

    def test_deploy_vm_bare_filename_resolves_in_deploy_dir(self):
        with tempfile.TemporaryDirectory() as home:
            deploy_dir = Path(home) / ".config" / AppInfo.name / "deploy"
            deploy_dir.mkdir(parents=True)
            cfg = {
                "server": "local",
                "vm_name": "web01",
                "vm_type": "server",
                "iso": {"source": "url", "url": "https://x/y.iso"},
            }
            (deploy_dir / "161.json").write_text(json.dumps(cfg), encoding="utf-8")

            self.cmd.deploy_from_config = MagicMock()
            with patch("vmanager.vmanager_cmd.Path.home", return_value=Path(home)):
                with redirect_stdout(io.StringIO()):
                    self.cmd.do_deploy_vm("161.json")
            self.cmd.deploy_from_config.assert_called_once_with(
                str(deploy_dir / "161.json"), dryrun=False
            )

    def test_deploy_vm_tab_completion(self):
        with tempfile.TemporaryDirectory() as home:
            deploy_dir = Path(home) / ".config" / AppInfo.name / "deploy"
            deploy_dir.mkdir(parents=True)
            (deploy_dir / "161.json").write_text("{}", encoding="utf-8")
            (deploy_dir / "262.json").write_text("{}", encoding="utf-8")
            with patch("vmanager.vmanager_cmd.Path.home", return_value=Path(home)):
                self.assertEqual(
                    self.cmd.complete_deploy_vm("", "deploy_vm ", 10, 10),
                    ["161.json", "262.json"],
                )
                self.assertEqual(
                    self.cmd.complete_deploy_vm("16", "deploy_vm 16", 10, 12),
                    ["161.json"],
                )

    def test_prepare_json_init_writes_skeleton(self):
        import json

        with tempfile.TemporaryDirectory() as d:
            out_path = str(Path(d) / "skeleton.json")
            with redirect_stdout(io.StringIO()):
                self.cmd.do_prepare_json(f"{out_path} --init")
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(data["vm_name"], "myvm")
        self.assertEqual(data["vm_type"], "server")
        self.assertEqual(data["iso"]["source"], "url")
        self.assertIn("template", data["autoinstall"])
        self.assertIn("scc", data["autoinstall"])

    def test_prepare_json_requires_connection(self):
        self.cmd.active_connections = {}
        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_prepare_json("")
        self.assertIn("Not connected", buf.getvalue())

    def test_prepare_json_custom_asks_hardware(self):
        import json

        self.cmd.active_connections["local"] = MagicMock()
        self.cmd._select_choice = MagicMock(side_effect=["local", "custom", "UEFI", "spice", "yes"])
        self.cmd._get_input = MagicMock(return_value="myvm")
        self.cmd._get_positive_float = MagicMock(return_value=2)  # 2 GB
        self.cmd._get_positive_int = MagicMock(side_effect=[1, 10])  # vcpu=1, disk=10
        self.cmd._prepare_iso = MagicMock(return_value={"source": "url", "url": "https://x/y.iso"})
        self.cmd._prepare_autoinstall = MagicMock(return_value=None)
        self.cmd._prepare_post_install = MagicMock(return_value=None)

        with tempfile.TemporaryDirectory() as d:
            out_path = str(Path(d) / "custom.json")
            with (
                patch("vmanager.vmanager_cmd.VMProvisioner"),
                patch("vmanager.vmanager_cmd.list_networks", return_value=[]),
                patch("vmanager.vmanager_cmd.list_storage_pools", return_value=[]),
                redirect_stdout(io.StringIO()),
            ):
                self.cmd.do_prepare_json(out_path)
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)

        self.assertEqual(data["vm_type"], "custom")
        self.assertEqual(data["memory_mb"], 2048)  # 2 GB
        self.assertEqual(data["vcpu"], 1)
        self.assertEqual(data["disk_size_gb"], 10)

    def test_prepare_json_profile_no_hardware_questions(self):
        import json

        self.cmd.active_connections["local"] = MagicMock()
        self.cmd._select_choice = MagicMock(
            side_effect=["local", "desktop", "UEFI", "spice", "yes"]
        )
        self.cmd._get_input = MagicMock(return_value="myvm")
        self.cmd._get_positive_float = MagicMock()
        self.cmd._get_positive_int = MagicMock()
        self.cmd._prepare_iso = MagicMock(return_value={"source": "url", "url": "https://x/y.iso"})
        self.cmd._prepare_autoinstall = MagicMock(return_value=None)
        self.cmd._prepare_post_install = MagicMock(return_value=None)

        with tempfile.TemporaryDirectory() as d:
            out_path = str(Path(d) / "profile.json")
            with (
                patch("vmanager.vmanager_cmd.VMProvisioner"),
                patch("vmanager.vmanager_cmd.list_networks", return_value=[]),
                patch("vmanager.vmanager_cmd.list_storage_pools", return_value=[]),
                redirect_stdout(io.StringIO()),
            ):
                self.cmd.do_prepare_json(out_path)
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)

        self.assertEqual(data["vm_type"], "desktop")
        # Profile: hardware not stored (resolved from the profile at deploy time)
        self.assertNotIn("memory_mb", data)
        self.assertNotIn("vcpu", data)
        self.assertNotIn("disk_size_gb", data)
        # No hardware questions asked for a profile
        self.cmd._get_positive_float.assert_not_called()
        self.cmd._get_positive_int.assert_not_called()

    def test_complete_prepare_json(self):
        self.assertEqual(self.cmd.complete_prepare_json("", None, None, None), ["--init"])
        self.assertEqual(self.cmd.complete_prepare_json("--i", None, None, None), ["--init"])
        self.assertEqual(self.cmd.complete_prepare_json("x", None, None, None), [])

    # --- pre-fill proposal (AUTO_INSTALL_PRE_FILL / SUSE_SCC) ---

    def test_autoinstall_settings_uses_prefill_when_accepted(self):
        prefill = {
            "username": "prefilluser",
            "user_password": "upw",
            "root_password": "rpw",
            "timezone": "Europe/Zurich",
            "keyboard": "ch",
            "language": "German (Germany)",
        }
        scc = {"scc_email": "a@b.c", "scc_reg_code": "XYZ"}
        self.cmd._select_choice = MagicMock(return_value="yes")
        self.cmd._get_input = MagicMock()
        self.cmd._get_password = MagicMock()
        result = self.cmd._prepare_autoinstall_settings(prefill, scc, "tmpl.xml", ["tmux"], "myvm")
        self.assertEqual(result["username"], "prefilluser")
        self.assertEqual(result["user_password"], "upw")
        self.assertEqual(result["root_password"], "rpw")
        self.assertEqual(result["timezone"], "Europe/Zurich")
        self.assertEqual(result["keyboard"], "ch")
        self.assertEqual(result["language"], "German (Germany)")
        self.assertEqual(result["hostname"], "myvm")
        self.assertEqual(result["scc"], {"scc_email": "a@b.c", "scc_reg_code": "XYZ"})
        self.assertEqual(result["packages"], ["tmux"])
        self.assertEqual(result["template"], "tmpl.xml")
        # No interactive asks when the pre-fill is accepted
        self.cmd._get_input.assert_not_called()
        self.cmd._get_password.assert_not_called()

    def test_autoinstall_settings_asks_when_declined(self):
        prefill = {"username": "prefilluser", "user_password": "upw", "root_password": "rpw"}
        scc = {"scc_email": "a@b.c", "scc_reg_code": "XYZ"}
        # 1st call (propose prefill) -> "no"; 2nd call (SCC register?) -> "no"
        self.cmd._select_choice = MagicMock(side_effect=["no", "no"])
        self.cmd._get_input = MagicMock(side_effect=["askeduser", "Europe/Zurich", "ch", "German"])
        self.cmd._get_password = MagicMock(side_effect=["askedupw", "askedrpw"])
        result = self.cmd._prepare_autoinstall_settings(prefill, scc, "tmpl.xml", [], "myvm")
        self.assertEqual(result["username"], "askeduser")
        self.assertEqual(result["user_password"], "askedupw")
        self.assertEqual(result["root_password"], "askedrpw")
        self.assertEqual(result["timezone"], "Europe/Zurich")
        self.assertEqual(result["keyboard"], "ch")
        self.assertEqual(result["language"], "German")
        self.assertEqual(result["scc"], {})

    def test_autoinstall_settings_asks_when_no_prefill(self):
        # No pre-fill at all: no proposal shown, fields asked directly.
        self.cmd._select_choice = MagicMock(return_value="no")  # SCC register?
        self.cmd._get_input = MagicMock(side_effect=["askeduser", "UTC", "us", "English (US)"])
        self.cmd._get_password = MagicMock(side_effect=["askedupw", "askedrpw"])
        result = self.cmd._prepare_autoinstall_settings({}, {}, "tmpl.xml", [], "myvm")
        self.assertEqual(result["username"], "askeduser")
        self.assertEqual(result["scc"], {})
        # No "use pre-fill" proposal when there is nothing to propose
        self.assertEqual(self.cmd._select_choice.call_count, 1)

    def test_autoinstall_settings_partial_prefill_still_proposes(self):
        # Only the username is pre-filled (no passwords): the proposal must
        # still appear (this was the reported bug).
        prefill = {"username": "onlyuser"}
        self.cmd._select_choice = MagicMock(return_value="yes")
        self.cmd._get_input = MagicMock()
        self.cmd._get_password = MagicMock()
        result = self.cmd._prepare_autoinstall_settings(prefill, {}, "tmpl.xml", [], "myvm")
        self.assertEqual(result["username"], "onlyuser")
        self.assertEqual(result["user_password"], "")
        self.cmd._get_input.assert_not_called()

    def test_ask_scc_register_yes(self):
        self.cmd._select_choice = MagicMock(return_value="yes")
        self.cmd._get_input = MagicMock(return_value="new@b.c")
        self.cmd._get_password = MagicMock(return_value="NEWCODE")
        result = self.cmd._ask_scc()
        self.assertEqual(result, {"scc_email": "new@b.c", "scc_reg_code": "NEWCODE"})

    def test_ask_scc_register_no(self):
        self.cmd._select_choice = MagicMock(return_value="no")
        self.cmd._get_input = MagicMock()
        self.cmd._get_password = MagicMock()
        result = self.cmd._ask_scc()
        self.assertEqual(result, {})
        self.cmd._get_input.assert_not_called()
        self.cmd._get_password.assert_not_called()


class TestUriOption(unittest.TestCase):
    """Tests for ``vmc -u/--uri``, the hypervisor a scripted caller names."""

    @patch("vmanager.vmanager_cmd.load_config")
    @patch("vmanager.vmanager_cmd.get_log_path")
    @patch("vmanager.vmanager_cmd.CLILogger")
    def setUp(self, mock_logger, mock_log_path, mock_load_config):
        mock_load_config.return_value = {
            "servers": [
                {"name": "local", "uri": "qemu:///system", "autoconnect": True},
                {"name": "other", "uri": "qemu+ssh://user@other/system", "autoconnect": True},
            ]
        }
        mock_log_path.return_value = "/tmp/vmanager_test.log"
        self.mock_vm_service = MagicMock()
        with patch("sys.stdout", MagicMock()):
            self.cmd = VManagerCMD(vm_service=self.mock_vm_service)

    def test_without_uri_the_configured_servers_connect(self):
        self.mock_vm_service.connect.return_value = MagicMock()
        with patch("sys.stdout", MagicMock()):
            self.cmd._perform_autoconnect()
        self.assertEqual(self.mock_vm_service.connect.call_count, 2, "both autoconnect servers")

    def test_uri_connects_to_that_hypervisor_only(self):
        self.mock_vm_service.connect.return_value = MagicMock()
        self.cmd.default_uri = "qemu+ssh://root@h1/system"
        with patch("sys.stdout", MagicMock()):
            self.cmd._perform_autoconnect()
        self.mock_vm_service.connect.assert_called_once_with("qemu+ssh://root@h1/system")
        self.assertIn("h1", self.cmd.active_connections)
        self.assertIn("h1", self.cmd.selected_vms)

    def test_uri_replaces_the_server_of_a_deployment_file(self):
        """``-u`` wins over the file, so one file can go to any hypervisor."""
        calls = []

        def stop(server):
            calls.append(server)
            raise RuntimeError("enough of the deployment")

        self.cmd._connect_one = stop
        cfg = self._write_cfg(
            {
                "server": "local",
                "vm_name": "v",
                "vm_type": "custom",
                "memory_mb": 2048,
                "vcpu": 2,
                "disk_size_gb": 20,
                "iso": {"source": "url", "url": "http://h/SLE-15-SP1.iso"},
            }
        )
        self.cmd.default_uri = "qemu:///system"
        with patch("sys.stdout", MagicMock()), self.assertRaises(RuntimeError):
            self.cmd.deploy_from_config(cfg)
        self.assertEqual(calls, ["qemu:///system"])

    def test_a_file_that_names_no_server_is_a_bad_file_even_with_a_uri(self):
        """``-u`` replaces the file's server; it does not remove the requirement.

        The file is checked the same way whether or not ``-u`` was given, so the same
        bytes are not a bad file in one call and a good one in the next. Refusing it
        is also what happens first: nothing is connected to on the way to telling the
        caller their file is wrong.
        """
        cfg = self._write_cfg(
            {
                "vm_name": "v",
                "vm_type": "server",
                "iso": {"source": "url", "url": "http://h/SLE-15-SP1.iso"},
            }
        )
        self.cmd.default_uri = "qemu:///system"
        self.cmd._connect_one = MagicMock(return_value="local")

        with patch("sys.stdout", MagicMock()), redirect_stdout(io.StringIO()) as out:
            result = self.cmd.deploy_from_config(cfg)

        self.assertIsNone(result, "a refused deployment answers with nothing deployed")
        self.cmd._connect_one.assert_not_called()
        self.assertIn("server", out.getvalue())

    def test_the_answer_names_the_hypervisor_the_override_used(self):
        """A caller can see the override took effect: `server` says where it went."""
        import libvirt

        class NoDomainError(libvirt.libvirtError):
            def get_error_code(self):
                return libvirt.VIR_ERR_NO_DOMAIN

        cfg = self._write_cfg(
            {
                "server": "local",  # the file names one hypervisor ...
                "vm_name": "v",
                "vm_type": "server",
                "iso": {"source": "url", "url": "http://h/SLE-15-SP1.iso"},
            }
        )
        self.cmd.default_uri = "qemu+ssh://root@h1/system"  # ... and -u sends it elsewhere
        conn = MagicMock()
        conn.lookupByName.side_effect = NoDomainError("no domain")
        self.cmd.active_connections["h1"] = conn
        self.cmd._connect_one = MagicMock(return_value="h1")
        with (
            patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True),
            patch("vmanager.vmanager_cmd.ensure_default_network"),
            patch("vmanager.vmanager_cmd.list_storage_pools"),
            patch("vmanager.vmanager_cmd.list_networks"),
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov,
            patch("sys.stdout", MagicMock()),
            redirect_stdout(io.StringIO()),
        ):
            mock_prov.return_value.provision_vm.return_value = MagicMock()
            result = self.cmd.deploy_from_config(cfg)

        self.assertEqual(result["server"], "h1")

    def _write_cfg(self, obj):
        import json
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(obj, f)
            path = f.name
        self.addCleanup(os.unlink, path)
        return path


class TestWriteJson(unittest.TestCase):
    """Tests for the JSON writers used by ``prepare_json`` (``_write_json`` /
    ``_write_skeleton_json``), including the round-trip through the validator.
    """

    @patch("vmanager.vmanager_cmd.load_config")
    @patch("vmanager.vmanager_cmd.get_log_path")
    @patch("vmanager.vmanager_cmd.CLILogger")
    def setUp(self, mock_logger, mock_log_path, mock_load_config):
        mock_load_config.return_value = {"servers": []}
        mock_log_path.return_value = "/tmp/vmanager_test.log"
        with patch("sys.stdout", MagicMock()):
            self.cmd = VManagerCMD(vm_service=MagicMock())

    def _write(self, cfg, name="out.json"):
        with tempfile.TemporaryDirectory() as d:
            out_path = str(Path(d) / name)
            with redirect_stdout(io.StringIO()):
                self.cmd._write_json(out_path, cfg)
            self.assertTrue(os.path.exists(out_path))
            with open(out_path, encoding="utf-8") as f:
                return json.load(f)

    def test_write_json_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            out_path = str(Path(d) / "a" / "b" / "c" / "deploy.json")
            with redirect_stdout(io.StringIO()):
                self.cmd._write_json(out_path, {"vm_name": "x"})
            self.assertTrue(os.path.exists(out_path))

    def test_write_json_is_valid_json(self):
        cfg = {"vm_name": "web01", "memory_mb": 2048, "nested": {"a": [1, 2, 3]}}
        data = self._write(cfg)
        self.assertEqual(data, cfg)

    def test_write_json_preserves_unicode(self):
        cfg = {"vm_name": "web-ünicode", "language": "Deutsch (Deutschland)"}
        data = self._write(cfg)
        self.assertEqual(data["vm_name"], "web-ünicode")
        self.assertEqual(data["language"], "Deutsch (Deutschland)")

    def test_write_json_expands_tilde(self):
        fake_home = tempfile.mkdtemp()
        with patch.dict(os.environ, {"HOME": fake_home}):
            out_path = str(Path("~") / ".config" / "virtui-manager" / "deploy" / "x.json")
            with redirect_stdout(io.StringIO()):
                self.cmd._write_json(out_path, {"vm_name": "x"})
            self.assertTrue(
                (Path(fake_home) / ".config" / "virtui-manager" / "deploy" / "x.json").exists()
            )

    def test_write_json_round_trip_through_validator(self):
        """A config written by ``_write_json`` must load cleanly via the validator."""
        cfg = {
            "server": "Localhost",
            "vm_name": "web01",
            "vm_type": "server",
            "memory_mb": 2048,
            "vcpu": 1,
            "iso": {"source": "url", "url": "https://x/y.iso"},
            "autoinstall": {
                "enabled": True,
                "template": "autoyast-server.xml",
                "packages": ["ssh"],
                "scc": {"scc_email": "a@b.c", "scc_reg_code": "XYZ"},
            },
        }
        with tempfile.TemporaryDirectory() as d:
            out_path = str(Path(d) / "deploy.json")
            with redirect_stdout(io.StringIO()):
                self.cmd._write_json(out_path, cfg)
            loaded = load_deploy_config(out_path)
        self.assertEqual(loaded["vm_name"], "web01")
        self.assertEqual(loaded["memory_mb"], 2048)
        self.assertEqual(loaded["autoinstall"]["template"], "autoyast-server.xml")

    def test_skeleton_is_valid_config(self):
        """The ``--init`` skeleton must be a valid, loadable deployment config."""
        with tempfile.TemporaryDirectory() as d:
            out_path = str(Path(d) / "skeleton.json")
            with redirect_stdout(io.StringIO()):
                self.cmd._write_skeleton_json(out_path)
            loaded = load_deploy_config(out_path)
        self.assertEqual(loaded["vm_name"], "myvm")
        self.assertEqual(loaded["vm_type"], "server")
        self.assertEqual(loaded["iso"]["source"], "url")
        self.assertEqual(loaded["autoinstall"]["template"], "autoyast-server.xml")
        self.assertIn("scc", loaded["autoinstall"])

    def test_skeleton_written_by_do_prepare_json_init(self):
        with tempfile.TemporaryDirectory() as d:
            out_path = str(Path(d) / "skeleton.json")
            with redirect_stdout(io.StringIO()):
                self.cmd.do_prepare_json(f"{out_path} --init")
            self.assertTrue(os.path.exists(out_path))
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(data["vm_name"], "myvm")
        self.assertIn("autoinstall", data)


class TestListShowJson(unittest.TestCase):
    """Tests for the ``list_json`` and ``show_json`` commands."""

    @patch("vmanager.vmanager_cmd.load_config")
    @patch("vmanager.vmanager_cmd.get_log_path")
    @patch("vmanager.vmanager_cmd.CLILogger")
    def setUp(self, mock_logger, mock_log_path, mock_load_config):
        mock_load_config.return_value = {"servers": []}
        mock_log_path.return_value = "/tmp/vmanager_test.log"
        with patch("sys.stdout", MagicMock()):
            self.cmd = VManagerCMD(vm_service=MagicMock())

    def _make_deploy(self, d, files):
        """Create a fake home with a deploy dir and the given {name: dict} files."""
        home = Path(d)
        deploy = home / ".config" / "virtui-manager" / "deploy"
        deploy.mkdir(parents=True)
        for name, obj in files.items():
            (deploy / name).write_text(json.dumps(obj), encoding="utf-8")
        return home

    def test_list_json_lists_files(self):
        with tempfile.TemporaryDirectory() as d:
            home = self._make_deploy(
                d,
                {
                    "web01.json": {"vm_name": "web01", "vm_type": "server"},
                    "other.json": {"vm_name": "other"},
                },
            )
            with (
                patch("vmanager.vmanager_cmd.Path.home", return_value=home),
                redirect_stdout(io.StringIO()) as buf,
            ):
                self.cmd.do_list_json("")
            out = buf.getvalue()
            self.assertIn("web01.json", out)
            self.assertIn("other.json", out)
            self.assertIn("web01", out)
            self.assertIn("server", out)

    def test_list_json_no_dir(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)  # deploy dir intentionally not created
            with (
                patch("vmanager.vmanager_cmd.Path.home", return_value=home),
                redirect_stdout(io.StringIO()) as buf,
            ):
                self.cmd.do_list_json("")
            self.assertIn("No deployment JSON files found", buf.getvalue())

    def test_list_json_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".config" / "virtui-manager" / "deploy").mkdir(parents=True)
            with (
                patch("vmanager.vmanager_cmd.Path.home", return_value=home),
                redirect_stdout(io.StringIO()) as buf,
            ):
                self.cmd.do_list_json("")
            self.assertIn("No deployment JSON files found", buf.getvalue())

    def test_show_json_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            home = self._make_deploy(d, {"web01.json": {"vm_name": "web01", "vm_type": "server"}})
            with (
                patch("vmanager.vmanager_cmd.Path.home", return_value=home),
                redirect_stdout(io.StringIO()) as buf,
            ):
                self.cmd.do_show_json("web01.json")
            out = buf.getvalue()
            payload = out[out.index("{") : out.rindex("}") + 1]
            self.assertEqual(json.loads(payload)["vm_name"], "web01")

    def test_show_json_by_path(self):
        with tempfile.TemporaryDirectory() as d:
            home = self._make_deploy(d, {"web01.json": {"vm_name": "web01"}})
            path = home / ".config" / "virtui-manager" / "deploy" / "web01.json"
            with (
                patch("vmanager.vmanager_cmd.Path.home", return_value=home),
                redirect_stdout(io.StringIO()) as buf,
            ):
                self.cmd.do_show_json(str(path))
            self.assertIn("web01", buf.getvalue())

    def test_show_json_sanitizes_sensitive_data(self):
        with tempfile.TemporaryDirectory() as d:
            home = self._make_deploy(
                d,
                {
                    "web01.json": {
                        "server": "qemu+ssh://admin:secret@remote/system",
                        "vm_name": "web01",
                        "autoinstall": {
                            "username": "admin",
                            "user_password": "SuperSecret123",
                            "scc": {
                                "scc_email": "admin@example.com",
                                "scc_reg_code": "ABCDEF-123456",
                                "scc_we_reg_code": "WE-999999",
                            },
                        },
                    }
                },
            )
            with (
                patch("vmanager.vmanager_cmd.Path.home", return_value=home),
                redirect_stdout(io.StringIO()) as buf,
            ):
                self.cmd.do_show_json("web01.json")
            out = buf.getvalue()
            # The plaintext password must not leak into the output.
            self.assertNotIn("SuperSecret123", out)
            self.assertNotIn("secret", out)
            # Personal SCC registration codes must not leak either.
            self.assertNotIn("ABCDEF-123456", out)
            self.assertNotIn("WE-999999", out)
            # The sanitized placeholder should be present instead.
            self.assertIn("***", out)
            # Non-sensitive fields (email) are preserved.
            self.assertIn("admin@example.com", out)

    def test_show_json_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            with (
                patch("vmanager.vmanager_cmd.Path.home", return_value=home),
                redirect_stdout(io.StringIO()) as buf,
            ):
                self.cmd.do_show_json("nope.json")
            self.assertIn("not found", buf.getvalue())

    def test_show_json_no_arg(self):
        with redirect_stdout(io.StringIO()) as buf:
            self.cmd.do_show_json("")
        self.assertIn("Usage: show_json", buf.getvalue())

    def test_show_json_invalid_json(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            deploy = home / ".config" / "virtui-manager" / "deploy"
            deploy.mkdir(parents=True)
            (deploy / "bad.json").write_text("{not valid json", encoding="utf-8")
            with (
                patch("vmanager.vmanager_cmd.Path.home", return_value=home),
                redirect_stdout(io.StringIO()) as buf,
            ):
                self.cmd.do_show_json("bad.json")
            self.assertIn("Invalid JSON", buf.getvalue())

    def test_complete_show_json(self):
        with tempfile.TemporaryDirectory() as d:
            home = self._make_deploy(
                d,
                {"web01.json": {"vm_name": "web01"}, "web02.json": {"vm_name": "web02"}},
            )
            with patch("vmanager.vmanager_cmd.Path.home", return_value=home):
                self.assertEqual(
                    self.cmd.complete_show_json("web0", None, None, None),
                    ["web01.json", "web02.json"],
                )
                self.assertEqual(
                    self.cmd.complete_show_json("web01", None, None, None),
                    ["web01.json"],
                )

    def test_complete_list_json(self):
        self.assertEqual(self.cmd.complete_list_json("", None, None, None), [])


if __name__ == "__main__":
    unittest.main()
