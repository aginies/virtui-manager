"""
OpenSUSE OS Provider Implementation

This module provides OpenSUSE-specific provisioning capabilities including:
- OpenSUSE Leap, Tumbleweed, Slowroll distributions
- ISO fetching and validation from official mirrors
- Linux-optimized VM configurations
- Support for custom repositories and cached ISOs
"""

import hashlib
import logging
import os
import re
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..os_provider import AutomationConfig, DriverInfo, OSProvider, OSType, OSVersion


class OpenSUSEDistro(Enum):
    LEAP = "Leap"
    TUMBLEWEED = "Tumbleweed"
    SLOWROLL = "Slowroll"
    STABLE = "Stable (Leap)"
    CURRENT = "Current (Tumbleweed)"
    CUSTOM = "Custom ISO"


class OpenSUSEProvider(OSProvider):
    """Provider for OpenSUSE operating systems."""

    # OpenSUSE distribution base URLs
    DISTRO_BASE_URLS = {
        OpenSUSEDistro.LEAP: "https://download.opensuse.org/distribution/leap/",
        OpenSUSEDistro.TUMBLEWEED: "https://download.opensuse.org/tumbleweed/iso/",
        OpenSUSEDistro.SLOWROLL: "https://download.opensuse.org/slowroll/iso/",
        OpenSUSEDistro.STABLE: "https://download.opensuse.org/distribution/openSUSE-stable/offline/",
        OpenSUSEDistro.CURRENT: "https://download.opensuse.org/distribution/openSUSE-current/installer/iso/",
    }

    def __init__(self, host_arch: str = "x86_64", cache_dir: Optional[Path] = None):
        self.logger = logging.getLogger(__name__)
        self.host_arch = host_arch

        # Set up cache directory
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "virtui-manager" / "opensuse"

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Subdirectories for organization
        self.iso_cache = self.cache_dir / "isos"
        self.template_cache = self.cache_dir / "templates"

        for cache in [self.iso_cache, self.template_cache]:
            cache.mkdir(exist_ok=True)

    @property
    def os_type(self) -> OSType:
        return OSType.LINUX

    def get_supported_versions(self) -> List[OSVersion]:
        """Return supported OpenSUSE versions."""
        versions = []

        # Add major distributions
        distributions = [
            ("leap-15.6", "openSUSE Leap 15.6", False),
            ("leap-15.5", "openSUSE Leap 15.5", False),
            ("tumbleweed", "openSUSE Tumbleweed", False),
            ("slowroll", "openSUSE Slowroll", False),
        ]

        for version_id, display_name, is_eval in distributions:
            versions.append(
                OSVersion(
                    os_type=OSType.LINUX,
                    version_id=version_id,
                    display_name=display_name,
                    architecture=self.host_arch,
                    is_evaluation=is_eval,
                )
            )

        return versions

    def get_iso_sources(self, version: OSVersion) -> List[str]:
        """Return ISO download URLs for an OpenSUSE version."""
        # Map version IDs to distribution types
        distro_mapping = {
            "leap-15.6": OpenSUSEDistro.LEAP,
            "leap-15.5": OpenSUSEDistro.LEAP,
            "tumbleweed": OpenSUSEDistro.TUMBLEWEED,
            "slowroll": OpenSUSEDistro.SLOWROLL,
        }

        distro = distro_mapping.get(version.version_id)
        if not distro:
            return []

        try:
            iso_list = self._get_iso_list_for_distro(distro)
            return [iso["url"] for iso in iso_list]
        except Exception as e:
            self.logger.error(f"Failed to get ISO sources for {version.version_id}: {e}")
            return []

    def get_default_vm_settings(self, version: OSVersion, vm_type: str) -> Dict[str, Any]:
        """Return Linux-optimized VM settings."""

        # Base Linux settings optimized for performance
        base_settings = {
            "disk_bus": "virtio",
            "disk_format": "qcow2",
            "disk_cache": "none",
            "machine": "pc-q35-10.1",
            "cpu_model": "host-passthrough",
            "network_model": "virtio",
            "video_model": "virtio",
            "sound_model": "ich9",
            "boot_uefi": True,
            "secure_boot": False,
            "tpm": False,
            "watchdog": False,
            "suspend_to_mem": "on",
            "suspend_to_disk": "on",
            "virtio_channels": ["org.qemu.guest_agent.0"],
        }

        # VM type specific adjustments
        if vm_type in ["DESKTOP", "LINUX_DESKTOP"]:
            base_settings.update(
                {
                    "cpu": 4,
                    "memory": 4096,
                    "disk_size": 30,
                    "video_model": "virtio",
                    "sound_model": "ich9",
                    "mem_backing": "memfd",
                }
            )
        elif vm_type in ["SERVER", "LINUX_SERVER"]:
            base_settings.update(
                {
                    "cpu": 6,
                    "memory": 4096,
                    "disk_size": 18,
                    "sound_model": None,
                    "video_model": "virtio",
                    "mem_backing": False,
                }
            )
        elif vm_type == "COMPUTATION":
            base_settings.update(
                {
                    "cpu": 4,
                    "memory": 8192,
                    "disk_size": 8,
                    "disk_cache": "unsafe",
                    "disk_format": "raw",
                    "video_model": "qxl",
                    "network_model": "virtio",
                    "iothreads": 4,
                    "mem_backing": "memfd",
                    "watchdog": True,
                }
            )
        elif vm_type == "SECURE":
            base_settings.update(
                {
                    "cpu": 2,
                    "memory": 4096,
                    "disk_size": 8,
                    "disk_cache": "writethrough",
                    "video_model": "qxl",
                    "network_model": "e1000",
                    "tpm": True,
                    "sev": True,
                    "input_bus": "ps2",
                    "mem_backing": False,
                }
            )

        return base_settings

    def supports_unattended_install(self, version: OSVersion) -> bool:
        """OpenSUSE supports unattended installation via AutoYaST."""
        return True

    def get_automation_config(self, version: OSVersion) -> Optional[AutomationConfig]:
        """Return automation configuration for OpenSUSE."""
        return AutomationConfig(
            template_name="autoyast.xml",
            variables={
                "root_password": "linux",
                "user_name": "user",
                "user_password": "user",
                "timezone": "UTC",
                "language": "en_US",
                "keyboard": "us",
                "hostname": "opensuse-vm",
            },
            supports_custom_user=True,
            supports_network_config=True,
        )

    def get_required_drivers(self, version: OSVersion) -> List[DriverInfo]:
        """Return required drivers for OpenSUSE (typically none needed)."""
        return []  # OpenSUSE has excellent hardware support out of the box

    def generate_automation_file(
        self, version: OSVersion, vm_name: str, user_config: Dict[str, Any], output_path: Path
    ) -> Path:
        """Generate AutoYaST XML file for OpenSUSE automated installation."""

        # Get automation config with defaults
        config = self.get_automation_config(version)
        variables = config.variables.copy()

        # Override with user-provided values
        variables.update(user_config)

        # Ensure hostname is VM name (cleaned)
        clean_vm_name = re.sub(r"[^a-zA-Z0-9-]", "", vm_name)[:63]  # hostname limit
        variables["hostname"] = clean_vm_name or "opensuse-vm"

        # Generate AutoYaST content
        autoyast_content = self._generate_autoyast_xml(version, variables)

        # Write to output file
        output_file = output_path / "autoyast.xml"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(autoyast_content)

        self.logger.info(f"Generated autoyast.xml for {version.display_name} at {output_file}")
        return output_file

    def get_post_install_scripts(self, version: OSVersion) -> List[str]:
        """Return shell commands to run after OpenSUSE installation."""
        return [
            # Update package database
            "zypper refresh",
            # Install essential packages
            "zypper install -y qemu-guest-agent spice-vdagent",
            # Enable guest agent
            "systemctl enable qemu-guest-agent",
            "systemctl start qemu-guest-agent",
            # Configure SSH (if desired)
            "systemctl enable sshd",
        ]

    def validate_iso(self, iso_path: Path, version: OSVersion) -> bool:
        """Validate OpenSUSE ISO file."""
        if not super().validate_iso(iso_path, version):
            return False

        # Check minimum file size (OpenSUSE ISOs are typically > 500MB)
        if iso_path.stat().st_size < 500 * 1024 * 1024:
            self.logger.warning(f"ISO file {iso_path} seems too small for OpenSUSE")
            return False

        return True

    def get_vm_type_mapping(self) -> Dict[str, str]:
        """Map VirtUI VM types to OpenSUSE-specific configurations."""
        return {
            "DESKTOP": "LINUX_DESKTOP",
            "SERVER": "LINUX_SERVER",
            "COMPUTATION": "COMPUTATION",
            "SECURE": "SECURE",
            "WDESKTOP": "LINUX_DESKTOP",  # Fallback Windows -> Linux
            "WLDESKTOP": "LINUX_DESKTOP",
        }

    def _get_iso_list_for_distro(self, distro: OpenSUSEDistro) -> List[Dict[str, Any]]:
        """Get ISO list for a specific OpenSUSE distribution."""
        base_url = self.DISTRO_BASE_URLS.get(distro)
        if not base_url:
            return []

        self.logger.info(f"Fetching ISO list from {base_url} for arch {self.host_arch}")

        # Create unverified context to avoid SSL errors with some mirrors
        context = ssl._create_unverified_context()
        iso_urls = []

        try:
            # Helper to fetch and find ISOs in a specific URL
            def fetch_isos_from_url(url):
                try:
                    with urllib.request.urlopen(url, context=context, timeout=10) as response:
                        html = response.read().decode("utf-8")

                    pattern = r'href="([^"]+\.iso)"'
                    links = re.findall(pattern, html)

                    valid_links = []
                    for link in links:
                        if not link.endswith(".iso"):
                            continue

                        link_lower = link.lower()
                        is_arch_specific = any(
                            a in link_lower for a in ["x86_64", "amd64", "aarch64", "arm64"]
                        )

                        if is_arch_specific:
                            target_arch = self.host_arch
                            if target_arch == "x86_64":
                                if "x86_64" in link_lower or "amd64" in link_lower:
                                    pass
                                else:
                                    continue
                            elif target_arch == "aarch64":
                                if "aarch64" in link_lower or "arm64" in link_lower:
                                    pass
                                else:
                                    continue

                        # Clean the link by removing ./ prefix if present
                        clean_link = link.lstrip("./")
                        full_url = (
                            os.path.join(url, clean_link) if not link.startswith("http") else link
                        )
                        valid_links.append(full_url)

                    return valid_links
                except Exception as e:
                    self.logger.warning(f"Error fetching ISOs from {url}: {e}")
                    return []

            if distro == OpenSUSEDistro.LEAP:
                # Use hardcoded versions
                versions15 = ["15.5", "15.6"]
                versions16 = ["16.0"]
                for ver in versions15 + versions16:
                    if ver in versions15:
                        ver_iso_url = f"{base_url}{ver}/iso/"
                        iso_urls.extend(fetch_isos_from_url(ver_iso_url))
                    if ver in versions16:
                        ver_iso_url = f"{base_url}{ver}/offline/"
                        iso_urls.extend(fetch_isos_from_url(ver_iso_url))
            else:
                # Direct ISO directories
                iso_urls.extend(fetch_isos_from_url(base_url))

            # Deduplicate URLs
            unique_urls = sorted(list(set(iso_urls)), reverse=True)

            # Fetch details in parallel
            with ThreadPoolExecutor(max_workers=10) as executor:
                results = list(executor.map(self._get_iso_details, unique_urls))

            # Sort by name descending
            results.sort(key=lambda x: x["name"], reverse=True)
            return results

        except Exception as e:
            self.logger.error(f"Failed to fetch ISO list: {e}")
            return []

    def _get_iso_details(self, url: str) -> Dict[str, Any]:
        """Fetch details (Last-Modified) for a given ISO URL."""
        name = url.split("/")[-1]
        # Clean the name by removing ./ prefix if present (additional safety)
        name = name.lstrip("./")
        try:
            context = ssl._create_unverified_context()
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, context=context, timeout=5) as response:
                last_modified = response.getheader("Last-Modified")
                date_str = ""
                if last_modified:
                    try:
                        dt = parsedate_to_datetime(last_modified)
                        date_str = dt.strftime("%Y-%m-%d %H:%M")
                    except:
                        date_str = last_modified

                return {"name": name, "url": url, "date": date_str}
        except Exception as e:
            self.logger.warning(f"Failed to get details for {url}: {e}")
            return {"name": name, "url": url, "date": ""}

    def _generate_autoyast_xml(self, version: OSVersion, variables: Dict[str, Any]) -> str:
        """Generate AutoYaST XML content for automated OpenSUSE installation."""

        # Load AutoYaST template from external file
        template_path = Path(__file__).parent.parent / "templates" / "autoyast.xml"

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                template = f.read()
        except FileNotFoundError:
            self.logger.error(f"AutoYaST template not found at {template_path}")
            raise Exception(f"AutoYaST template file not found: {template_path}")
        except Exception as e:
            self.logger.error(f"Error reading AutoYaST template: {e}")
            raise Exception(f"Failed to read AutoYaST template: {e}")

        return template.format(**variables)

    def get_cached_isos(self) -> List[Dict[str, Any]]:
        """Retrieve a list of ISOs already present in the local cache directory."""
        if not self.iso_cache.exists():
            return []

        isos = []
        try:
            for f in self.iso_cache.glob("*.iso"):
                # Use stats for date
                mtime = f.stat().st_mtime
                dt_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                isos.append(
                    {
                        "name": f.name,
                        "url": f.name,  # Use filename as URL for local detection logic
                        "date": f"{dt_str} (Cached)",
                    }
                )
        except Exception as e:
            self.logger.error(f"Error reading cached ISOs: {e}")

        return isos

    def get_iso_list(self, distro: OpenSUSEDistro | str) -> List[Dict[str, Any]]:
        """
        Get list of available ISOs for a specific OpenSUSE distribution.

        Args:
            distro: OpenSUSE distribution type (enum) or custom repository URL (string)

        Returns:
            List of ISO dictionaries with 'name', 'url', and 'date' keys
        """
        # Handle string arguments (custom repositories, cached ISOs, etc.)
        if isinstance(distro, str):
            if distro == "cached":
                return self.get_cached_isos()
            elif distro == "pool_volumes":
                return []  # Pool volumes are handled elsewhere
            else:
                # Treat as custom repository URL
                return self.get_iso_list_from_url(distro)

        # Handle OpenSUSEDistro enum values
        if distro == OpenSUSEDistro.CUSTOM:
            return []

        base_url = self.DISTRO_BASE_URLS.get(distro)
        if not base_url:
            # Use distro name safely - check if it's an enum first
            distro_name = distro.value if hasattr(distro, "value") else str(distro)
            self.logger.warning(f"No base URL configured for {distro_name}")
            return []

        return self.get_iso_list_from_url(base_url)

    def get_iso_list_from_url(self, url: str) -> List[Dict[str, Any]]:
        """
        Get list of ISOs from a custom repository URL or local path.
        This method handles both local directories and remote HTTP/HTTPS URLs.
        """
        # Check for local directory or file URI
        if url.startswith("/") or url.startswith("file://") or os.path.isdir(url):
            return self._get_local_iso_list(url)

        # Handle remote URLs
        return self._get_remote_iso_list(url)

    def _get_local_iso_list(self, path: str) -> List[Dict[str, Any]]:
        """Lists ISO files from a local directory."""
        if path.startswith("file://"):
            path = path[7:]

        results = []
        try:
            path_obj = Path(path)
            if not path_obj.exists() or not path_obj.is_dir():
                self.logger.warning(f"Local path {path} does not exist or is not a directory.")
                return []

            for f in path_obj.glob("*.iso"):
                try:
                    stats = f.stat()
                    dt_str = datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M")
                    results.append({"name": f.name, "url": str(f.absolute()), "date": dt_str})
                except Exception as e:
                    self.logger.warning(f"Error reading file {f}: {e}")

            results.sort(key=lambda x: x["name"], reverse=True)
        except Exception as e:
            self.logger.error(f"Error listing local ISOs from {path}: {e}")

        return results

    def _get_remote_iso_list(self, url: str) -> List[Dict[str, Any]]:
        """Get ISO list from a remote URL by scraping directory listings."""
        self.logger.info(f"Fetching ISO list from {url} for arch {self.host_arch}")

        # Create unverified context to avoid SSL errors with some mirrors
        context = ssl._create_unverified_context()
        iso_urls = []

        try:
            with urllib.request.urlopen(url, context=context, timeout=10) as response:
                html = response.read().decode("utf-8")

            pattern = r'href="([^"]+\.iso)"'
            links = re.findall(pattern, html)

            valid_links = []
            for link in links:
                # Basic filtering: ends with .iso
                if not link.endswith(".iso"):
                    continue

                link_lower = link.lower()
                is_arch_specific = any(
                    a in link_lower for a in ["x86_64", "amd64", "aarch64", "arm64"]
                )

                if is_arch_specific:
                    # Filter by host architecture
                    target_arch = self.host_arch
                    if target_arch == "x86_64":
                        if "x86_64" in link_lower or "amd64" in link_lower:
                            pass
                        else:
                            continue  # specific to another arch
                    elif target_arch == "aarch64":
                        if "aarch64" in link_lower or "arm64" in link_lower:
                            pass
                        else:
                            continue

                # Clean the link by removing ./ prefix if present
                clean_link = link.lstrip("./")
                full_url = os.path.join(url, clean_link) if not link.startswith("http") else link
                valid_links.append(full_url)

            # Fetch details in parallel
            with ThreadPoolExecutor(max_workers=10) as executor:
                results = list(executor.map(self._get_iso_details, valid_links))

            # Sort by name descending
            results.sort(key=lambda x: x["name"], reverse=True)
            return results

        except Exception as e:
            self.logger.error(f"Failed to fetch ISO list from {url}: {e}")
            return []

    def get_custom_repos(self) -> List[Dict[str, str]]:
        """Get custom repository configuration from config."""
        try:
            # Import here to avoid circular imports
            from ...config import load_config

            config = load_config()
            return config.get("custom_ISO_repo", [])
        except Exception as e:
            self.logger.warning(f"Failed to load custom repositories: {e}")
            return []
