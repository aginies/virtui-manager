"""
Tests for firmware_manager module
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.firmware_manager import (
    Firmware,
    _load_firmware_from_files,
    _load_firmware_from_json_map,
    clear_firmware_cache,
    get_uefi_files,
    select_best_firmware,
)


class TestFirmware(unittest.TestCase):
    """Tests for the Firmware class"""

    def test_firmware_init(self):
        """Test Firmware object initialization"""
        fw = Firmware()
        self.assertIsNone(fw.executable)
        self.assertIsNone(fw.nvram_template)
        self.assertEqual(fw.architectures, [])
        self.assertEqual(fw.features, [])
        self.assertEqual(fw.interfaces, [])

    def test_firmware_load_from_json_valid(self):
        """Test loading firmware from valid JSON data"""
        json_data = {
            "interface-types": ["uefi"],
            "mapping": {
                "executable": {"filename": "/usr/share/OVMF/OVMF_CODE.fd"},
                "nvram-template": {"filename": "/usr/share/OVMF/OVMF_VARS.fd"},
            },
            "features": ["secure-boot"],
            "targets": [{"architecture": "x86_64"}],
        }
        fw = Firmware()
        result = fw.load_from_json(json_data)

        self.assertTrue(result)
        self.assertEqual(fw.executable, "/usr/share/OVMF/OVMF_CODE.fd")
        self.assertEqual(fw.nvram_template, "/usr/share/OVMF/OVMF_VARS.fd")
        self.assertEqual(fw.architectures, ["x86_64"])
        self.assertEqual(fw.features, ["secure-boot"])
        self.assertEqual(fw.interfaces, ["uefi"])

    def test_firmware_load_from_json_missing_interface_types(self):
        """Test that firmware returns False when interface-types is missing"""
        json_data = {
            "mapping": {"executable": {"filename": "/usr/share/OVMF/OVMF_CODE.fd"}},
            "targets": [{"architecture": "x86_64"}],
        }
        fw = Firmware()
        result = fw.load_from_json(json_data)
        self.assertFalse(result)

    def test_firmware_load_from_json_missing_executable(self):
        """Test that firmware returns False when executable is missing"""
        json_data = {
            "interface-types": ["uefi"],
            "mapping": {"nvram-template": {"filename": "/usr/share/OVMF/OVMF_VARS.fd"}},
            "targets": [{"architecture": "x86_64"}],
        }
        fw = Firmware()
        result = fw.load_from_json(json_data)
        self.assertFalse(result)

    def test_firmware_load_from_json_missing_architectures(self):
        """Test that firmware returns False when architectures is missing"""
        json_data = {
            "interface-types": ["uefi"],
            "mapping": {"executable": {"filename": "/usr/share/OVMF/OVMF_CODE.fd"}},
        }
        fw = Firmware()
        result = fw.load_from_json(json_data)
        self.assertFalse(result)


class TestLoadFirmwareFromFiles(unittest.TestCase):
    """Tests for _load_firmware_from_files function"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("vmanager.firmware_manager.FIRMWARE_META_BASE_DIR")
    def test_load_firmware_from_files_success(self, mock_firmware_dir):
        """Test successfully loading firmware files"""
        mock_firmware_dir.return_value = self.temp_dir

        # Create a sample firmware JSON file
        firmware_json = {
            "interface-types": ["uefi"],
            "mapping": {"executable": {"filename": "/usr/share/OVMF/OVMF_CODE.fd"}},
            "targets": [{"architecture": "x86_64"}],
        }

        firmware_path = Path(self.temp_dir) / "ovmf.json"
        with open(firmware_path, "w") as f:
            json.dump(firmware_json, f)

        uefi_files = []
        with patch("vmanager.firmware_manager.FIRMWARE_META_BASE_DIR", self.temp_dir):
            _load_firmware_from_files(uefi_files)

        self.assertEqual(len(uefi_files), 1)
        self.assertEqual(uefi_files[0].executable, "/usr/share/OVMF/OVMF_CODE.fd")
        self.assertEqual(uefi_files[0].architectures, ["x86_64"])

    @patch("vmanager.firmware_manager.FIRMWARE_META_BASE_DIR")
    def test_load_firmware_from_files_missing_directory(self, mock_firmware_dir):
        """Test behavior when firmware directory doesn't exist"""
        mock_firmware_dir.return_value = "/nonexistent/directory"

        uefi_files = []
        with patch("vmanager.firmware_manager.FIRMWARE_META_BASE_DIR", "/nonexistent/directory"):
            _load_firmware_from_files(uefi_files)

        self.assertEqual(len(uefi_files), 0)

    @patch("vmanager.firmware_manager.FIRMWARE_META_BASE_DIR")
    def test_load_firmware_from_files_invalid_json(self, mock_firmware_dir):
        """Test handling of invalid JSON files"""
        mock_firmware_dir.return_value = self.temp_dir

        # Create invalid JSON file
        invalid_path = Path(self.temp_dir) / "invalid.json"
        with open(invalid_path, "w") as f:
            f.write("{invalid json}")

        uefi_files = []
        with patch("vmanager.firmware_manager.FIRMWARE_META_BASE_DIR", self.temp_dir):
            _load_firmware_from_files(uefi_files)

        # Should not crash, just skip the invalid file
        self.assertEqual(len(uefi_files), 0)


class TestGetUefiFiles(unittest.TestCase):
    """Tests for get_uefi_files function"""

    def setUp(self):
        """Clear cache before each test"""
        clear_firmware_cache()

    def tearDown(self):
        """Clear cache after each test"""
        clear_firmware_cache()

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _completed(stdout="", returncode=0, args=None):
        import subprocess

        if args is None:
            args = ["ssh", "host", "cmd"]
        return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout)

    @staticmethod
    def _x86_64_json():
        return json.dumps(
            {
                "interface-types": ["uefi"],
                "mapping": {
                    "executable": {"filename": "/usr/share/OVMF/OVMF_CODE.fd"},
                    "nvram-template": {"filename": "/usr/share/OVMF/OVMF_VARS.fd"},
                },
                "features": ["secure-boot"],
                "targets": [{"architecture": "x86_64", "machines": ["pc-q35-*"]}],
            }
        )

    @staticmethod
    def _aarch64_json():
        return json.dumps(
            {
                "interface-types": ["uefi"],
                "mapping": {
                    "executable": {"filename": "/usr/share/AAVMF/AAVMF_CODE.fd"},
                    "nvram-template": {"filename": "/usr/share/AAVMF/AAVMF_VARS.fd"},
                },
                "targets": [{"architecture": "aarch64", "machines": ["virt-*"]}],
            }
        )

    # -- local connection ----------------------------------------------

    @patch("vmanager.firmware_manager._load_firmware_from_files")
    def test_local_connection_reads_local_files(self, mock_load_files):
        """A None connection reads the local firmware JSON files."""

        def populate(uefi_files):
            fw = Firmware()
            fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
            fw.architectures = ["x86_64"]
            fw.interfaces = ["uefi"]
            uefi_files.append(fw)

        mock_load_files.side_effect = populate

        result = get_uefi_files(None)

        mock_load_files.assert_called_once()
        self.assertGreater(len(result), 0)

    # -- remote connection (SSH) ---------------------------------------

    @patch("vmanager.firmware_manager.run_command")
    @patch("vmanager.firmware_manager.get_ssh_host_from_uri", return_value="user@host")
    def test_remote_connection_reads_remote_files(self, mock_ssh_host, mock_run_command):
        """A qemu+ssh connection reads firmware JSON from the remote host via SSH,
        discovering every architecture (x86_64 and aarch64 here)."""

        def fake_run_command(cmd, remote_host=None, check=True, timeout=30):
            # A single combined remote command; emit RS-separated file blocks.
            combined = (
                "\x1eovmf-x86_64.json\n" + self._x86_64_json() + "\n"
                "\x1eAAVMF-aarch64.json\n" + self._aarch64_json() + "\n"
            )
            return self._completed(combined, returncode=0)

        mock_run_command.side_effect = fake_run_command

        mock_conn = MagicMock()
        mock_conn.getURI.return_value = "qemu+ssh://user@host/system"

        result = get_uefi_files(mock_conn)

        # Both architectures discovered from the remote host.
        archs = {a for fw in result for a in fw.architectures}
        self.assertIn("x86_64", archs)
        self.assertIn("aarch64", archs)
        # NVRAM templates came from the remote JSON metadata.
        nvram = [f for f in result if f.nvram_template]
        self.assertGreater(len(nvram), 0)
        # SSH was used with the parsed host.
        mock_ssh_host.assert_called_once_with("qemu+ssh://user@host/system")

    @patch("vmanager.firmware_manager.run_command")
    @patch("vmanager.firmware_manager.get_ssh_host_from_uri", return_value="user@host")
    def test_remote_ssh_read_failure_returns_empty(self, mock_ssh_host, mock_run_command):
        """If the remote firmware read fails, return an empty list."""

        def fake_run_command(cmd, remote_host=None, check=True, timeout=30):
            return self._completed("", returncode=1)

        mock_run_command.side_effect = fake_run_command
        mock_ssh_host.return_value = "user@host"

        mock_conn = MagicMock()
        mock_conn.getURI.return_value = "qemu+ssh://user@host/system"

        result = get_uefi_files(mock_conn)

        self.assertEqual(result, [])

    @patch("vmanager.firmware_manager.run_command")
    @patch("vmanager.firmware_manager.get_ssh_host_from_uri", return_value="user@host")
    def test_remote_read_does_not_interpolate_filenames(
        self, mock_ssh_host, mock_run_command
    ):
        """Filenames with shell metacharacters are never interpolated into the
        remote command (the remote shell performs the globbing)."""

        weird = "OVMF's 'boot'; rm -rf .json"
        combined = "\x1e" + weird + "\n" + self._x86_64_json() + "\n"

        def fake_run_command(cmd, remote_host=None, check=True, timeout=30):
            # The command must not contain the filename (no interpolation).
            self.assertNotIn(weird, " ".join(cmd))
            return self._completed(combined, returncode=0)

        mock_run_command.side_effect = fake_run_command
        mock_conn = MagicMock()
        mock_conn.getURI.return_value = "qemu+ssh://user@host/system"

        result = get_uefi_files(mock_conn)

        # The file is still parsed and loaded despite the hostile filename.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].architectures, ["x86_64"])

    @patch("vmanager.firmware_manager.get_ssh_host_from_uri", return_value=None)
    @patch("vmanager.firmware_manager._load_firmware_from_files")
    def test_non_ssh_connection_falls_back_to_local(self, mock_load_files, mock_ssh_host):
        """A non-SSH connection (no SSH host) falls back to reading local JSON."""

        def populate(uefi_files):
            fw = Firmware()
            fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
            fw.architectures = ["x86_64"]
            fw.interfaces = ["uefi"]
            uefi_files.append(fw)

        mock_load_files.side_effect = populate

        mock_conn = MagicMock()
        mock_conn.getURI.return_value = "qemu+tls://user@host/system"

        result = get_uefi_files(mock_conn)

        mock_load_files.assert_called_once()
        self.assertGreater(len(result), 0)

    # -- caching -------------------------------------------------------

    @patch("vmanager.firmware_manager._load_firmware_from_files")
    def test_caching_enabled(self, mock_load_files):
        """Repeated calls with use_cache=True return the cached result."""

        def populate(uefi_files):
            fw = Firmware()
            fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
            fw.architectures = ["x86_64"]
            fw.interfaces = ["uefi"]
            uefi_files.append(fw)

        mock_load_files.side_effect = populate

        mock_conn = MagicMock()
        mock_conn.getURI.return_value = "qemu:///system"

        result1 = get_uefi_files(mock_conn, use_cache=True)
        result2 = get_uefi_files(mock_conn, use_cache=True)

        # Only the first call hits the loader; the second uses the cache.
        self.assertEqual(mock_load_files.call_count, 1)
        self.assertEqual(result1[0].executable, result2[0].executable)

    @patch("vmanager.firmware_manager._load_firmware_from_files")
    def test_caching_disabled(self, mock_load_files):
        """use_cache=False bypasses the cache and re-reads."""

        def populate(uefi_files):
            fw = Firmware()
            fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
            fw.architectures = ["x86_64"]
            fw.interfaces = ["uefi"]
            uefi_files.append(fw)

        mock_load_files.side_effect = populate

        mock_conn = MagicMock()
        mock_conn.getURI.return_value = "qemu:///system"

        get_uefi_files(mock_conn, use_cache=True)
        get_uefi_files(mock_conn, use_cache=False)

        self.assertEqual(mock_load_files.call_count, 2)


class TestLoadFirmwareFromJsonMap(unittest.TestCase):
    """Tests for the shared JSON-map parser used by local and remote paths."""

    def test_parses_valid_and_invalid(self):
        json_map = {
            "good.json": json.dumps(
                {
                    "interface-types": ["uefi"],
                    "mapping": {
                        "executable": {"filename": "/usr/share/OVMF/OVMF_CODE.fd"},
                        "nvram-template": {"filename": "/usr/share/OVMF/OVMF_VARS.fd"},
                    },
                    "targets": [{"architecture": "aarch64"}],
                }
            ),
            "bad.json": "{not valid json",
        }
        uefi_files = []
        loaded, rejected = _load_firmware_from_json_map(json_map, uefi_files)

        self.assertEqual(loaded, 1)
        self.assertEqual(rejected, 1)
        self.assertEqual(len(uefi_files), 1)
        self.assertEqual(uefi_files[0].architectures, ["aarch64"])
        self.assertEqual(uefi_files[0].nvram_template, "/usr/share/OVMF/OVMF_VARS.fd")


class TestFirmwareFeatureInference(unittest.TestCase):
    """Tests for firmware feature inference from JSON metadata."""

    def test_secure_boot_and_sev_from_metadata(self):
        json_map = {
            "secure.json": json.dumps(
                {
                    "interface-types": ["uefi"],
                    "mapping": {
                        "executable": {"filename": "/usr/share/OVMF/OVMF_CODE.secboot.fd"},
                        "nvram-template": {"filename": "/usr/share/OVMF/OVMF_VARS.secboot.fd"},
                    },
                    "features": ["secure-boot"],
                    "targets": [{"architecture": "x86_64", "machines": ["pc-q35-*"]}],
                }
            ),
            "sev.json": json.dumps(
                {
                    "interface-types": ["uefi"],
                    "mapping": {
                        "executable": {"filename": "/usr/share/OVMF/OVMF_CODE.sev-es.fd"},
                    },
                    "features": ["amd-sev-es"],
                    "targets": [{"architecture": "x86_64", "machines": ["pc-q35-*"]}],
                }
            ),
        }
        uefi_files = []
        _load_firmware_from_json_map(json_map, uefi_files)

        self.assertTrue(any("secure-boot" in f.features for f in uefi_files))
        self.assertTrue(any("amd-sev-es" in f.features for f in uefi_files))


class TestFirmwareSelection(unittest.TestCase):
    """Tests for select_best_firmware logic"""

    def setUp(self):
        # Create standard non-secure firmware
        self.fw_normal = Firmware()
        self.fw_normal.executable = "/usr/share/OVMF/OVMF_CODE.fd"
        self.fw_normal.architectures = ["x86_64"]
        self.fw_normal.features = ["acpi-s3"]
        self.fw_normal.nvram_template = "/usr/share/OVMF/OVMF_VARS.fd"

        # Create secure boot firmware
        self.fw_secure = Firmware()
        self.fw_secure.executable = "/usr/share/OVMF/OVMF_CODE.secboot.fd"
        self.fw_secure.architectures = ["x86_64"]
        self.fw_secure.features = ["acpi-s3", "secure-boot", "requires-smm"]
        self.fw_secure.nvram_template = "/usr/share/OVMF/OVMF_VARS.secboot.fd"

        self.firmwares = [self.fw_normal, self.fw_secure]

    def test_select_secure_boot_when_required(self):
        """Test that secure-boot firmware is selected when requested"""
        selected = select_best_firmware(self.firmwares, architecture="x86_64", secure_boot=True)
        self.assertEqual(selected.executable, self.fw_secure.executable)

    def test_select_non_secure_boot_when_not_required(self):
        """Test that non-secure firmware is preferred when secure-boot is not requested"""
        selected = select_best_firmware(self.firmwares, architecture="x86_64", secure_boot=False)
        self.assertEqual(selected.executable, self.fw_normal.executable)

    def test_select_by_architecture(self):
        """Test filtering by architecture"""
        fw_aarch64 = Firmware()
        fw_aarch64.executable = "/usr/share/AAVMF/AAVMF_CODE.fd"
        fw_aarch64.architectures = ["aarch64"]

        firmwares = self.firmwares + [fw_aarch64]

        selected = select_best_firmware(firmwares, architecture="aarch64")
        self.assertEqual(selected.executable, fw_aarch64.executable)


class TestFirmwareCaching(unittest.TestCase):
    """Tests for firmware caching functionality"""

    def tearDown(self):
        """Clear cache after each test"""
        clear_firmware_cache()

    @patch("vmanager.firmware_manager._load_firmware_from_files")
    def test_firmware_caching_enabled(self, mock_load_files):
        """Test that firmware caching works and returns cached results"""

        def populate_files(uefi_files):
            fw = Firmware()
            fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
            fw.architectures = ["x86_64"]
            fw.interfaces = ["uefi"]
            uefi_files.append(fw)

        mock_load_files.side_effect = populate_files

        mock_conn = MagicMock()
        mock_conn.getURI.return_value = "qemu:///system"

        # First call - should load from libvirt
        result1 = get_uefi_files(mock_conn, use_cache=True)

        # Second call - should use cache
        result2 = get_uefi_files(mock_conn, use_cache=True)

        # Both should return the same firmware
        self.assertEqual(len(result1), len(result2))
        self.assertEqual(result1[0].executable, result2[0].executable)

        # The loader should only run once; the second call uses the cache.
        self.assertEqual(mock_load_files.call_count, 1)
        self.assertEqual(result1[0].architectures, result2[0].architectures)

    @patch("vmanager.firmware_manager._load_firmware_from_files")
    def test_firmware_caching_disabled(self, mock_load_files):
        """Test that use_cache=False bypasses the cache"""

        def populate_files(uefi_files):
            fw = Firmware()
            fw.executable = "/usr/share/OVMF/OVMF_CODE.fd"
            fw.architectures = ["x86_64"]
            fw.interfaces = ["uefi"]
            uefi_files.append(fw)

        mock_load_files.side_effect = populate_files

        mock_conn = MagicMock()
        mock_conn.getURI.return_value = "qemu:///system"

        # First call - populates cache
        result1 = get_uefi_files(mock_conn, use_cache=True)

        # Second call with use_cache=False - should bypass cache
        result2 = get_uefi_files(mock_conn, use_cache=False)

        # Both should return firmware
        self.assertGreater(len(result1), 0)
        self.assertGreater(len(result2), 0)

        # Loader ran for both calls (cache bypassed on the second).
        self.assertEqual(mock_load_files.call_count, 2)

    def test_clear_firmware_cache(self):
        """Test clearing the firmware cache"""
        # This is a simple test that just ensures clear_firmware_cache doesn't crash
        clear_firmware_cache()
        clear_firmware_cache("local")
        clear_firmware_cache("remote")

        # Should not raise any exception
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
