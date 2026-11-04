"""
Tests for VManagerCMD class.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import libvirt

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vmanager_cmd import VManagerCMD
from vmanager.deploy_config import DeployConfigError, load_deploy_config
from vmanager.constants import AppInfo
from vmanager.provisioning.os_provider import OSType


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
        with patch(
            "vmanager.vmanager_cmd.list_storage_pools",
            return_value=[{"name": "default", "pool": pool_mock}],
        ), patch(
            "vmanager.vmanager_cmd.list_storage_volumes",
            return_value=[
                {
                    "name": "vol1.qcow2",
                    "type": "file",
                    "capacity": 10 * 1024**3,
                    "allocation": 5 * 1024**3,
                }
            ],
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
        with patch("vmanager.vmanager_cmd.find_volume", return_value=vol), patch(
            "vmanager.vmanager_cmd.find_vms_using_volume", return_value=[user_vm]
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
        with patch("vmanager.vmanager_cmd.get_host_architecture", return_value="x86_64"), patch(
            "vmanager.vmanager_cmd.get_cpu_models", return_value=["Haswell", "IvyBridge"]
        ), patch(
            "vmanager.vmanager_cmd.get_latest_machine_types",
            return_value={"pc-i440fx": "pc.i440fx.8.1", "pc-q35": "pc.q35.8.1"},
        ), patch(
            "vmanager.vmanager_cmd.get_host_domain_capabilities", return_value=None
        ), patch(
            "vmanager.vmanager_cmd.get_video_domain_capabilities",
            return_value={"video_models": ["virtio"]},
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
        import libvirt

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

        with patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True), \
            patch("vmanager.vmanager_cmd.ensure_default_network"), \
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov, \
            patch("vmanager.vmanager_cmd.list_storage_pools"), \
            patch("vmanager.vmanager_cmd.list_networks"), \
            redirect_stdout(io.StringIO()) as buf:
            self.cmd.deploy_from_config(cfg_path, dryrun=True)

        out = buf.getvalue()
        self.assertIn("Summary of Actions", out)
        self.assertIn("web01", out)
        self.assertIn("Automated Install: Yes", out)
        self.assertIn("Extra Packages: ssh, vim", out)
        self.assertIn("SCC Registration: Yes", out)
        # provision_vm must NOT be called in dry-run
        mock_prov.return_value.provision_vm.assert_not_called()

    def test_deploy_from_config_vm_exists(self):
        import libvirt

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

        with patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov, \
            redirect_stdout(io.StringIO()) as buf:
            self.cmd.deploy_from_config(cfg_path)

        self.assertIn("already exists", buf.getvalue())
        mock_prov.return_value.provision_vm.assert_not_called()

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

        with patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True), \
            patch("vmanager.vmanager_cmd.ensure_default_network"), \
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov, \
            patch("vmanager.vmanager_cmd.list_storage_pools"), \
            patch("vmanager.vmanager_cmd.list_networks"), \
            redirect_stdout(io.StringIO()):
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

        with patch("vmanager.vmanager_cmd.ensure_default_pool", return_value=True), \
            patch("vmanager.vmanager_cmd.ensure_default_network"), \
            patch("vmanager.vmanager_cmd.VMProvisioner") as mock_prov, \
            patch("vmanager.vmanager_cmd.list_storage_pools"), \
            patch("vmanager.vmanager_cmd.list_networks"), \
            redirect_stdout(io.StringIO()):
            self.cmd.deploy_from_config(cfg_path)

        mock_prov.return_value.provision_vm.assert_called_once()
        _, kwargs = mock_prov.return_value.provision_vm.call_args
        # No autoinstall -> no automation -> show_automation_path stays False.
        self.assertFalse(kwargs.get("show_automation_path"))

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
        self.cmd._select_choice = MagicMock(
            side_effect=["local", "custom", "UEFI", "spice", "yes"]
        )
        self.cmd._get_input = MagicMock(return_value="myvm")
        self.cmd._get_positive_float = MagicMock(return_value=2)  # 2 GB
        self.cmd._get_positive_int = MagicMock(side_effect=[1, 10])  # vcpu=1, disk=10
        self.cmd._prepare_iso = MagicMock(
            return_value={"source": "url", "url": "https://x/y.iso"}
        )
        self.cmd._prepare_autoinstall = MagicMock(return_value=None)
        self.cmd._prepare_post_install = MagicMock(return_value=None)

        with tempfile.TemporaryDirectory() as d:
            out_path = str(Path(d) / "custom.json")
            with patch("vmanager.vmanager_cmd.VMProvisioner"), \
                patch("vmanager.vmanager_cmd.list_networks", return_value=[]), \
                patch("vmanager.vmanager_cmd.list_storage_pools", return_value=[]), \
                redirect_stdout(io.StringIO()):
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
        self.cmd._prepare_iso = MagicMock(
            return_value={"source": "url", "url": "https://x/y.iso"}
        )
        self.cmd._prepare_autoinstall = MagicMock(return_value=None)
        self.cmd._prepare_post_install = MagicMock(return_value=None)

        with tempfile.TemporaryDirectory() as d:
            out_path = str(Path(d) / "profile.json")
            with patch("vmanager.vmanager_cmd.VMProvisioner"), \
                patch("vmanager.vmanager_cmd.list_networks", return_value=[]), \
                patch("vmanager.vmanager_cmd.list_storage_pools", return_value=[]), \
                redirect_stdout(io.StringIO()):
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
        result = self.cmd._prepare_autoinstall_settings(
            prefill, scc, "tmpl.xml", ["tmux"], "myvm"
        )
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
        self.cmd._get_input = MagicMock(
            side_effect=["askeduser", "Europe/Zurich", "ch", "German"]
        )
        self.cmd._get_password = MagicMock(side_effect=["askedupw", "askedrpw"])
        result = self.cmd._prepare_autoinstall_settings(
            prefill, scc, "tmpl.xml", [], "myvm"
        )
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
        self.cmd._get_input = MagicMock(
            side_effect=["askeduser", "UTC", "us", "English (US)"]
        )
        self.cmd._get_password = MagicMock(side_effect=["askedupw", "askedrpw"])
        result = self.cmd._prepare_autoinstall_settings(
            {}, {}, "tmpl.xml", [], "myvm"
        )
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
        result = self.cmd._prepare_autoinstall_settings(
            prefill, {}, "tmpl.xml", [], "myvm"
        )
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
            self.assertTrue((Path(fake_home) / ".config" / "virtui-manager" / "deploy" / "x.json").exists())

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
            with patch("vmanager.vmanager_cmd.Path.home", return_value=home), \
                redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_list_json("")
            out = buf.getvalue()
            self.assertIn("web01.json", out)
            self.assertIn("other.json", out)
            self.assertIn("web01", out)
            self.assertIn("server", out)

    def test_list_json_no_dir(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)  # deploy dir intentionally not created
            with patch("vmanager.vmanager_cmd.Path.home", return_value=home), \
                redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_list_json("")
            self.assertIn("No deployment JSON files found", buf.getvalue())

    def test_list_json_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".config" / "virtui-manager" / "deploy").mkdir(parents=True)
            with patch("vmanager.vmanager_cmd.Path.home", return_value=home), \
                redirect_stdout(io.StringIO()) as buf:
                self.cmd.do_list_json("")
            self.assertIn("No deployment JSON files found", buf.getvalue())

    def test_show_json_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            home = self._make_deploy(
                d, {"web01.json": {"vm_name": "web01", "vm_type": "server"}}
            )
            with patch("vmanager.vmanager_cmd.Path.home", return_value=home), \
                redirect_stdout(io.StringIO()) as buf:
                    self.cmd.do_show_json("web01.json")
            out = buf.getvalue()
            payload = out[out.index("{") : out.rindex("}") + 1]
            self.assertEqual(json.loads(payload)["vm_name"], "web01")

    def test_show_json_by_path(self):
        with tempfile.TemporaryDirectory() as d:
            home = self._make_deploy(d, {"web01.json": {"vm_name": "web01"}})
            path = home / ".config" / "virtui-manager" / "deploy" / "web01.json"
            with patch("vmanager.vmanager_cmd.Path.home", return_value=home), \
                redirect_stdout(io.StringIO()) as buf:
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
                        }
                    }
                },
            )
            with patch("vmanager.vmanager_cmd.Path.home", return_value=home), \
                redirect_stdout(io.StringIO()) as buf:
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
            with patch("vmanager.vmanager_cmd.Path.home", return_value=home), \
                redirect_stdout(io.StringIO()) as buf:
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
            with patch("vmanager.vmanager_cmd.Path.home", return_value=home), \
                redirect_stdout(io.StringIO()) as buf:
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
