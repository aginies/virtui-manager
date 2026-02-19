"""
Windows OS Provider Implementation

This module provides Windows-specific provisioning capabilities including:
- Windows 10/11 and Windows Server evaluation versions
- Virtio-win driver integration
- Unattend.xml generation for automated installation
- Windows-optimized VM configurations
"""

import hashlib
import logging
import os
import re
import shutil
import ssl
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..os_provider import AutomationConfig, DriverInfo, OSProvider, OSType, OSVersion


class WindowsProvider(OSProvider):
    """Provider for Windows operating systems."""

    # Windows evaluation ISO URLs (these are official Microsoft eval versions)
    EVAL_ISO_URLS = {
        "10": {
            "display_name": "Windows 10 Enterprise Evaluation",
            "url": "https://software-download.microsoft.com/download/pr/19041.388.200601-1853.rs_release_windowsleaks_cliententerprise_vol_x64fre_en-us.iso",
            "checksum": "6911e3c15b4d94c15c8f1929c8a4e7b7e83ad7f65d1b65e82a791a4a2b8f5b6a",
        },
        "11": {
            "display_name": "Windows 11 Enterprise Evaluation",
            "url": "https://software-download.microsoft.com/download/pr/22000.194.210913-1444.co_release_cliententerprise_vol_x64fre_en-us.iso",
            "checksum": "ef7312733a9f5d7d51cfa04ac497671995674ca5e1058d5164d6028f0938d668",
        },
        "2019": {
            "display_name": "Windows Server 2019 Evaluation",
            "url": "https://software-download.microsoft.com/download/pr/17763.737.190906-2324.rs5_release_svc_refresh_server_eval_x64fre_en-us_1.iso",
            "checksum": "549bca46c055157291be6c22a3aaaed8330e78ef4382c99ee82c896426a1cee1",
        },
        "2022": {
            "display_name": "Windows Server 2022 Evaluation",
            "url": "https://software-download.microsoft.com/download/pr/20348.169.210806-2348.fe_release_svc_refresh_server_eval_x64fre_en-us.iso",
            "checksum": "3e4fa6d8507b554856fc9ca6079cc402df11a8b79344871669a8ae07b9e7efd8",
        },
    }

    # Virtio-win driver URLs
    VIRTIO_WIN_URLS = {
        "latest": "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/latest-virtio/virtio-win.iso",
        "stable": "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso",
    }

    def __init__(self, cache_dir: Optional[Path] = None):
        self.logger = logging.getLogger(__name__)

        # Set up cache directory
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "virtui-manager" / "windows"

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Subdirectories for organization
        self.iso_cache = self.cache_dir / "isos"
        self.driver_cache = self.cache_dir / "drivers"
        self.template_cache = self.cache_dir / "templates"

        for cache in [self.iso_cache, self.driver_cache, self.template_cache]:
            cache.mkdir(exist_ok=True)

    @property
    def os_type(self) -> OSType:
        return OSType.WINDOWS

    def get_supported_versions(self) -> List[OSVersion]:
        """Return supported Windows versions."""
        versions = []

        for version_id, info in self.EVAL_ISO_URLS.items():
            versions.append(
                OSVersion(
                    os_type=OSType.WINDOWS,
                    version_id=version_id,
                    display_name=info["display_name"],
                    architecture="x86_64",
                    is_evaluation=True,
                )
            )

        return versions

    def get_iso_sources(self, version: OSVersion) -> List[str]:
        """Return ISO download URLs for a Windows version."""
        if version.version_id in self.EVAL_ISO_URLS:
            return [self.EVAL_ISO_URLS[version.version_id]["url"]]
        return []

    def get_default_vm_settings(self, version: OSVersion, vm_type: str) -> Dict[str, Any]:
        """Return Windows-optimized VM settings."""

        # Base Windows settings optimized for compatibility and performance
        base_settings = {
            "disk_bus": "sata",  # SATA for Windows compatibility (can upgrade to virtio later)
            "disk_format": "qcow2",
            "disk_cache": "writethrough",  # Safe caching for Windows
            "machine": "pc-q35-7.2",  # Modern machine type
            "cpu_model": "host-passthrough",  # Best performance
            "network_model": "e1000",  # e1000 for initial compatibility (virtio-net after drivers)
            "video_model": "qxl",  # QXL for better graphics
            "sound_model": "hda",  # HDA audio
            "boot_uefi": True,  # UEFI for modern Windows
            "secure_boot": False,  # Disable initially for compatibility
            "tpm": False,  # Disable initially
            "watchdog": False,  # Disable watchdog
            "virtio_channels": ["org.qemu.guest_agent.0"],  # Guest agent channel
        }

        # VM type specific adjustments
        if vm_type in ["WDESKTOP", "DESKTOP"]:
            base_settings.update(
                {
                    "cpu": 4,
                    "memory": 8192,  # 8GB for desktop
                    "disk_size": 80,  # 80GB disk
                    "sound_model": "hda",
                    "video_model": "qxl",
                }
            )
        elif vm_type in ["SERVER", "WSERVER"]:
            base_settings.update(
                {
                    "cpu": 4,
                    "memory": 4096,  # 4GB for server
                    "disk_size": 60,  # 60GB disk
                    "sound_model": None,  # No sound for server
                    "video_model": "vga",  # Basic graphics for server
                }
            )
        elif vm_type == "COMPUTATION":
            base_settings.update(
                {
                    "cpu": 8,
                    "memory": 16384,  # 16GB for computation
                    "disk_size": 100,
                    "disk_cache": "unsafe",  # Performance over safety for computation
                    "iothreads": True,
                }
            )

        return base_settings

    def supports_unattended_install(self, version: OSVersion) -> bool:
        """Windows supports unattended installation via unattend.xml."""
        return True

    def get_automation_config(self, version: OSVersion) -> Optional[AutomationConfig]:
        """Return automation configuration for Windows."""
        return AutomationConfig(
            template_name="unattend.xml",
            variables={
                "computer_name": "Windows-VM",
                "admin_password": "VirtUIManager123!",
                "auto_logon": True,
                "timezone": "UTC",
                "language": "en-US",
                "keyboard": "en-US",
            },
            supports_custom_user=True,
            supports_network_config=False,  # Windows networking is typically configured post-install
        )

    def get_required_drivers(self, version: OSVersion) -> List[DriverInfo]:
        """Return required drivers for Windows."""
        return [
            DriverInfo(
                name="virtio-win",
                url=self.VIRTIO_WIN_URLS["latest"],
                description="VirtIO drivers for Windows (network, storage, balloon, etc.)",
                required=True,
            )
        ]

    def generate_automation_file(
        self, version: OSVersion, vm_name: str, user_config: Dict[str, Any], output_path: Path
    ) -> Path:
        """Generate unattend.xml file for Windows automated installation."""

        # Get automation config with defaults
        config = self.get_automation_config(version)
        variables = config.variables.copy()

        # Override with user-provided values
        variables.update(user_config)

        # Ensure computer name is VM name (cleaned)
        clean_vm_name = re.sub(r"[^a-zA-Z0-9-]", "", vm_name)[:15]  # Windows limit
        variables["computer_name"] = clean_vm_name or "Windows-VM"

        # Generate unattend.xml content
        unattend_content = self._generate_unattend_xml(version, variables)

        # Write to output file
        output_file = output_path / "unattend.xml"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(unattend_content)

        self.logger.info(f"Generated unattend.xml for {version.display_name} at {output_file}")
        return output_file

    def get_post_install_scripts(self, version: OSVersion) -> List[str]:
        """Return PowerShell commands to run after Windows installation."""
        return [
            # Enable Remote Desktop
            'Set-ItemProperty -Path "HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server" -Name "fDenyTSConnections" -Value 0',
            'Enable-NetFirewallRule -DisplayGroup "Remote Desktop"',
            # Install virtio-win drivers (if available)
            'if (Test-Path "D:\\virtio-win-gt-x64.msi") { Start-Process "D:\\virtio-win-gt-x64.msi" -ArgumentList "/quiet" -Wait }',
            # Configure Windows Update
            'Set-ItemProperty -Path "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU" -Name "AUOptions" -Value 2',
            # Enable guest agent (if installed)
            'if (Get-Service -Name "QEMU Guest Agent" -ErrorAction SilentlyContinue) { Set-Service -Name "QEMU Guest Agent" -StartupType Automatic; Start-Service -Name "QEMU Guest Agent" }',
        ]

    def download_virtio_drivers(self, force_refresh: bool = False) -> Path:
        """Download and cache virtio-win drivers."""
        driver_iso = self.driver_cache / "virtio-win-latest.iso"

        # Check if we need to download
        need_download = force_refresh or not driver_iso.exists()

        if not need_download:
            # Check if cached version is older than 30 days
            try:
                age_days = (time.time() - driver_iso.stat().st_mtime) / (24 * 3600)
                need_download = age_days > 30
            except OSError:
                need_download = True

        if need_download:
            self.logger.info("Downloading latest virtio-win drivers...")
            self._download_file_with_progress(
                self.VIRTIO_WIN_URLS["latest"], driver_iso, "Downloading virtio-win drivers"
            )

        return driver_iso

    def validate_iso(self, iso_path: Path, version: OSVersion) -> bool:
        """Validate Windows ISO file."""
        if not super().validate_iso(iso_path, version):
            return False

        # Check minimum file size (Windows ISOs are typically > 2GB)
        if iso_path.stat().st_size < 2 * 1024 * 1024 * 1024:
            self.logger.warning(f"ISO file {iso_path} seems too small for Windows")
            return False

        # Optional: Check checksum if available
        if version.version_id in self.EVAL_ISO_URLS:
            expected_checksum = self.EVAL_ISO_URLS[version.version_id].get("checksum")
            if expected_checksum:
                actual_checksum = self._calculate_sha256(iso_path)
                if actual_checksum != expected_checksum:
                    self.logger.warning(f"Checksum mismatch for {iso_path}")
                    return False

        return True

    def get_vm_type_mapping(self) -> Dict[str, str]:
        """Map VirtUI VM types to Windows-specific configurations."""
        return {
            "DESKTOP": "WDESKTOP",
            "WDESKTOP": "WDESKTOP",
            "WLDESKTOP": "WDESKTOP",  # Legacy Windows -> Desktop
            "SERVER": "WSERVER",
            "SECURE": "WDESKTOP",  # Secure VMs use desktop config
            "COMPUTATION": "COMPUTATION",
        }

    def _generate_unattend_xml(self, version: OSVersion, variables: Dict[str, Any]) -> str:
        """Generate unattend.xml content for automated Windows installation."""

        # Load unattend.xml template from external file
        template_path = Path(__file__).parent.parent / "templates" / "unattend.xml"

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
        except FileNotFoundError:
            self.logger.error(f"Unattend template not found at {template_path}")
            raise Exception(f"Unattend template file not found: {template_path}")
        except Exception as e:
            self.logger.error(f"Error reading unattend template: {e}")
            raise Exception(f"Failed to read unattend template: {e}")

        # Format template with variables
        return template.format(
            computer_name=variables.get("computer_name", "Windows-VM"),
            admin_password=variables.get("admin_password", "VirtUIManager123!"),
            auto_logon_enabled="true" if variables.get("auto_logon", True) else "false",
            timezone=variables.get("timezone", "UTC"),
            language=variables.get("language", "en-US"),
            keyboard=variables.get("keyboard", "en-US"),
        )

    def _download_file_with_progress(
        self, url: str, output_path: Path, description: str = "Downloading"
    ):
        """Download a file with progress indication."""
        try:
            self.logger.info(f"{description} from {url}")

            # Create SSL context that doesn't verify certificates (for compatibility)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(url, context=ssl_context) as response:
                total_size = int(response.headers.get("Content-Length", 0))

                with open(output_path, "wb") as f:
                    downloaded = 0
                    chunk_size = 8192

                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break

                        f.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            self.logger.debug(
                                f"{description}: {percent:.1f}% ({downloaded}/{total_size} bytes)"
                            )

            self.logger.info(f"Download completed: {output_path}")

        except Exception as e:
            self.logger.error(f"Failed to download {url}: {e}")
            if output_path.exists():
                output_path.unlink()
            raise

    def _calculate_sha256(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of a file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
