"""
Library for VM creation and provisioning, supporting multiple Linux distributions.
"""

import hashlib
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import tarfile
import threading
import json
import urllib.request
import urllib.error
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import yaml
import netifaces

import libvirt
from packaging.version import parse as parse_version

from .auto_http_server import AutoHTTPServer
from .config import load_config
from .constants import AppInfo, StaticText
from .firmware_manager import get_uefi_files, select_best_firmware
from .libvirt_utils import get_host_architecture, get_latest_machine_types
from .provisioning.provider_registry import ProviderRegistry
from .provisioning.os_provider import OSType, OSVersion
from .provisioning.providers.alpine_provider import AlpineProvider, AlpineDistro
from .provisioning.providers.archlinux_provider import ArchLinuxProvider, ArchLinuxDistro
from .provisioning.providers.debian_provider import DebianProvider, DebianDistro
from .provisioning.providers.fedora_provider import FedoraProvider, FedoraDistro
from .provisioning.providers.generic_provider import GenericProvider
from .provisioning.providers.opensuse_provider import OpenSUSEProvider, OpenSUSEDistro
from .provisioning.providers.ubuntu_provider import UbuntuProvider, UbuntuDistro
from .storage_manager import create_volume
from .vm_actions import strip_installation_assets, get_vm_boot_files, delete_boot_files
from .utils import (
    get_ssh_host_from_uri,
    get_virt_install_version,
    manage_firewalld_port,
)


class VMType(Enum):
    SECURE = StaticText.VM_TYPE_SECURE
    COMPUTATION = StaticText.VM_TYPE_COMPUTATION
    DESKTOP = StaticText.VM_TYPE_DESKTOP
    WDESKTOP = StaticText.VM_TYPE_WDESKTOP
    WLDESKTOP = StaticText.VM_TYPE_WLDESKTOP
    SERVER = StaticText.VM_TYPE_SERVER


class VMProvisioner:
    def __init__(self, conn: libvirt.virConnect):
        self.conn = conn
        self.host_arch = get_host_architecture(conn)
        self.logger = logging.getLogger(__name__)
        self.virt_install_version = get_virt_install_version()
        if self.virt_install_version:
            self.logger.info(f"Found virt-install version: {self.virt_install_version}")
        else:
            self.logger.warning("virt-install not found or version could not be determined.")

        # Initialize provider registry and register OS providers
        self.provider_registry = ProviderRegistry()
        self._register_providers()

    def _register_providers(self):
        """Register available OS providers."""
        try:
            opensuse_provider = OpenSUSEProvider(host_arch=self.host_arch)
            self.provider_registry.register_provider(opensuse_provider)
        except Exception as e:
            self.logger.warning(f"Failed to register OpenSUSE provider: {e}")

        try:
            ubuntu_provider = UbuntuProvider()
            self.provider_registry.register_provider(ubuntu_provider)
        except Exception as e:
            self.logger.warning(f"Failed to register Ubuntu provider: {e}")

        try:
            debian_provider = DebianProvider()
            self.provider_registry.register_provider(debian_provider)
        except Exception as e:
            self.logger.warning(f"Failed to register Debian provider: {e}")

        try:
            fedora_provider = FedoraProvider(host_arch=self.host_arch)
            self.provider_registry.register_provider(fedora_provider)
        except Exception as e:
            self.logger.warning(f"Failed to register Fedora provider: {e}")

        try:
            arch_provider = ArchLinuxProvider(host_arch=self.host_arch)
            self.provider_registry.register_provider(arch_provider)
        except Exception as e:
            self.logger.warning(f"Failed to register Arch Linux provider: {e}")

        try:
            alpine_provider = AlpineProvider(host_arch=self.host_arch)
            self.provider_registry.register_provider(alpine_provider)
        except Exception as e:
            self.logger.warning(f"Failed to register Alpine provider: {e}")

        try:
            generic_provider = GenericProvider()
            self.provider_registry.register_provider(generic_provider)
        except Exception as e:
            self.logger.warning(f"Failed to register Generic provider: {e}")

    def get_provider(self, os_type_str: str):
        """Get provider by OS type string."""
        # Convert string to OSType enum
        os_type_map = {
            "linux": OSType.OPENSUSE,
            "opensuse": OSType.OPENSUSE,
            "ubuntu": OSType.UBUNTU,
            "debian": OSType.DEBIAN,
            "fedora": OSType.FEDORA,
            "archlinux": OSType.ARCHLINUX,
            "arch": OSType.ARCHLINUX,
            "alpine": OSType.ALPINE,
            "generic": OSType.GENERIC,
            "custom": OSType.GENERIC,
            "windows": OSType.WINDOWS,
        }

        os_type = os_type_map.get(os_type_str.lower())
        if os_type:
            return self.provider_registry.get_provider(os_type)

        self.logger.warning(f"Unknown OS type: {os_type_str}")
        return None

    def get_iso_sources(self, os_type: str, version_id: str) -> List[str]:
        """
        Get ISO download sources for a specific OS type and version.
        Delegates to the appropriate provider.
        """
        provider = self.get_provider(os_type)
        if not provider:
            logging.warning(f"No provider available for OS type: {os_type}")
            return []

        # Find the version object for this provider
        for version in provider.get_supported_versions():
            if version.version_id == version_id:
                return provider.get_iso_sources(version)

        logging.warning(f"Version {version_id} not found for OS type {os_type}")
        return []

    def get_cached_isos_for_provider(self, os_type: str) -> List[Dict[str, Any]]:
        """
        Get cached ISOs for a specific provider.
        Delegates to the appropriate provider if it supports caching.
        """
        provider = self.get_provider(os_type)
        if provider and hasattr(provider, "get_cached_isos"):
            return provider.get_cached_isos()

        logging.info(f"Provider for {os_type} does not support cached ISOs, returning empty list")
        return []

    def get_iso_list(self, distro) -> List[Dict[str, Any]]:
        """
        Get list of ISOs for a distribution or custom repository.

        Args:
            distro: Either an OpenSUSEDistro, UbuntuDistro, DebianDistro enum or a custom repository URL string

        Returns:
            List of ISO dictionaries with 'name', 'url', and 'date' keys
        """
        # Handle OpenSUSE distributions - delegate to provider
        if isinstance(distro, OpenSUSEDistro):
            provider = self.get_provider("opensuse")
            if provider and hasattr(provider, "get_iso_list"):
                return provider.get_iso_list(distro)
            else:
                logging.warning("OpenSUSE provider not available or doesn't support get_iso_list")
                return []

        # Handle Ubuntu distributions - delegate to provider
        elif isinstance(distro, UbuntuDistro):
            provider = self.get_provider("ubuntu")
            if provider and hasattr(provider, "get_iso_list"):
                return provider.get_iso_list(distro.value)  # Pass the string value
            else:
                logging.warning("Ubuntu provider not available or doesn't support get_iso_list")
                return []

        # Handle Debian distributions - delegate to provider
        elif isinstance(distro, DebianDistro):
            provider = self.get_provider("debian")
            if provider and hasattr(provider, "get_iso_list"):
                return provider.get_iso_list(distro.value)  # Pass the string value
            else:
                logging.warning("Debian provider not available or doesn't support get_iso_list")
                return []

        # Handle Fedora distributions - delegate to provider
        elif isinstance(distro, FedoraDistro):
            provider = self.get_provider("fedora")
            if provider and hasattr(provider, "get_iso_list"):
                return provider.get_iso_list(distro.value)  # Pass the string value
            else:
                logging.warning("Fedora provider not available or doesn't support get_iso_list")
                return []

        # Handle Arch Linux distributions - delegate to provider
        elif isinstance(distro, ArchLinuxDistro):
            provider = self.get_provider("archlinux")
            if provider and hasattr(provider, "get_iso_list"):
                return provider.get_iso_list(distro.value)  # Pass the string value
            else:
                logging.warning("Arch Linux provider not available or doesn't support get_iso_list")
                return []

        # Handle Alpine Linux distributions - delegate to provider
        elif isinstance(distro, AlpineDistro):
            provider = self.get_provider("alpine")
            if provider and hasattr(provider, "get_iso_list"):
                return provider.get_iso_list(distro.value)  # Pass the string value
            else:
                logging.warning(
                    "Alpine Linux provider not available or doesn't support get_iso_list"
                )
                return []

        # Handle custom repository URLs (strings)
        elif isinstance(distro, str):
            return self.get_iso_list_from_url(distro)

        else:
            logging.warning(f"Unknown distribution type: {type(distro)}")
            return []

    def get_iso_list_from_url(self, url: str) -> List[Dict[str, Any]]:
        """
        Get list of ISOs from a custom repository URL.
        Supports both HTTP URLs and local paths.

        Args:
            url: Repository URL (http/https) or local path (file:// or absolute path)

        Returns:
            List of ISO dictionaries with 'name', 'url', and 'date' keys
        """
        if url.startswith(("http://", "https://")):
            return self._get_remote_iso_list(url)
        else:
            return self._get_local_iso_list(url)

    def get_iso_list_from_custom_repo(
        self, url: str, os_type: str = "linux"
    ) -> List[Dict[str, Any]]:
        """
        Get list of ISOs from a custom repository URL.
        Delegates to the appropriate provider.
        """
        provider = self.get_provider(os_type)
        if provider and hasattr(provider, "get_iso_list_from_url"):
            return provider.get_iso_list_from_url(url)

        # Fallback: basic URL handling for providers that don't support it
        self.logger.warning(f"Provider for {os_type} doesn't support custom repositories")
        return []

    def get_custom_repos(self) -> List[Dict[str, str]]:
        """
        Retrieves the list of custom ISO repositories from the configuration.
        """
        config = load_config()
        return config.get("custom_ISO_repo", [])

    def get_cached_isos(self) -> List[Dict[str, Any]]:
        """
        Retrieves a list of ISOs already present in the local cache directory.
        """
        config = load_config()
        iso_cache_dir = Path(
            config.get("ISO_DOWNLOAD_PATH", str(Path.home() / ".cache" / AppInfo.name / "isos"))
        )

        if not iso_cache_dir.exists():
            return []

        isos = []
        try:
            for f in iso_cache_dir.glob("*.iso"):
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
            logging.error(f"Error reading cached ISOs: {e}")

        return isos

    def _get_local_iso_list(self, path: str) -> List[Dict[str, Any]]:
        """
        Lists ISO files from a local directory.
        """
        if path.startswith("file://"):
            path = path[7:]

        results = []
        try:
            path_obj = Path(path)
            if not path_obj.exists() or not path_obj.is_dir():
                logging.warning(f"Local path {path} does not exist or is not a directory.")
                return []

            for f in path_obj.glob("*.iso"):
                try:
                    stats = f.stat()
                    dt_str = datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M")
                    results.append({"name": f.name, "url": str(f.absolute()), "date": dt_str})
                except Exception as e:
                    logging.warning(f"Error reading file {f}: {e}")

            results.sort(key=lambda x: x["name"], reverse=True)
        except Exception as e:
            logging.error(f"Error listing local ISOs from {path}: {e}")

        return results

    def _get_remote_iso_list(self, url: str) -> List[Dict[str, Any]]:
        """
        Lists ISO files from a remote HTTP/HTTPS directory.
        """
        results = []

        try:
            # Create unverified SSL context to handle mirrors with cert issues
            context = ssl._create_unverified_context()

            with urllib.request.urlopen(url, context=context) as response:
                html_content = response.read().decode("utf-8")

                # Extract ISO links from HTML directory listing
                iso_pattern = re.compile(r'href="([^"]*\.iso)"', re.IGNORECASE)
                iso_matches = iso_pattern.findall(html_content)

                if not iso_matches:
                    logging.info(f"No ISO files found in directory listing at {url}")
                    return []

                # Filter architecture if possible and remove duplicates
                unique_isos = list(set(iso_matches))

                # Get details for each ISO with parallel requests
                with ThreadPoolExecutor(max_workers=5) as executor:
                    iso_details = list(
                        executor.map(
                            lambda iso_name: self._get_iso_details(url, iso_name), unique_isos
                        )
                    )

                # Filter out None results and add to results
                for detail in iso_details:
                    if detail:
                        results.append(detail)

                # Sort by name (newest first, typically)
                results.sort(key=lambda x: x["name"], reverse=True)

        except urllib.error.HTTPError as e:
            logging.error(f"HTTP error accessing {url}: {e.code} - {e.reason}")
        except urllib.error.URLError as e:
            logging.error(f"URL error accessing {url}: {e.reason}")
        except Exception as e:
            logging.error(f"Error fetching remote ISO list from {url}: {e}")

        return results

    def _get_iso_details(self, base_url: str, iso_name: str) -> Dict[str, Any] | None:
        """
        Get details for a specific ISO file from a remote server.

        Args:
            base_url: Base URL of the repository
            iso_name: Name of the ISO file

        Returns:
            Dictionary with ISO details or None if failed
        """
        try:
            # Clean the ISO name by removing ./ prefix if present
            clean_iso_name = iso_name.lstrip("./")

            # Construct full URL for the ISO
            if base_url.endswith("/"):
                iso_url = base_url + clean_iso_name
            else:
                iso_url = base_url + "/" + clean_iso_name

            # Make HEAD request to get Last-Modified header
            context = ssl._create_unverified_context()
            req = urllib.request.Request(iso_url, method="HEAD")

            with urllib.request.urlopen(req, context=context) as response:
                # Get Last-Modified header
                last_modified = response.getheader("Last-Modified")
                date_str = "Unknown"

                if last_modified:
                    try:
                        # Parse the date and format it
                        dt = parsedate_to_datetime(last_modified)
                        date_str = dt.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        date_str = last_modified

                return {"name": clean_iso_name, "url": iso_url, "date": date_str}

        except Exception as e:
            logging.warning(f"Failed to get details for {iso_name}: {e}")
            # Clean the ISO name and return basic info even if we can't get details
            clean_iso_name = iso_name.lstrip("./")
            iso_url = f"{base_url.rstrip('/')}/{clean_iso_name}"
            return {"name": clean_iso_name, "url": iso_url, "date": "Unknown"}

    def _format_download_speed(self, bytes_per_second: float) -> str:
        """Format download speed in human-readable format."""
        if bytes_per_second < 1024:
            return f"{bytes_per_second:.1f} B"
        elif bytes_per_second < 1024 * 1024:
            return f"{bytes_per_second / 1024:.1f} KB"
        elif bytes_per_second < 1024 * 1024 * 1024:
            return f"{bytes_per_second / (1024 * 1024):.1f} MB"
        else:
            return f"{bytes_per_second / (1024 * 1024 * 1024):.1f} GB"

    def download_iso(
        self,
        url: str,
        dest_path: Optional[str] = None,
        progress_callback: Optional[Callable[[str, int], None]] = None,
    ) -> str:
        """
        Downloads the ISO from the given URL.
        Shows download progress with network speed.

        Args:
            url: URL to download from
            dest_path: Optional local path to save the file. If None, a temporary file is created.
            progress_callback: Optional callback that receives (message, percent)

        Returns:
            Path to the downloaded ISO file.
        """
        from .constants import ErrorMessages

        # Validate URL
        if not url.startswith(("http://", "https://")):
            raise ValueError(ErrorMessages.CUSTOM_ISO_INVALID_URL_TEMPLATE.format(url=url))

        # Handle temporary path if none provided
        if dest_path is None:
            temp_dir = tempfile.mkdtemp(prefix="virtui_iso_")
            filename = os.path.basename(url.split("?")[0])  # Remove query params
            if not filename or not filename.endswith(".iso"):
                filename = "downloaded.iso"
            dest_path = os.path.join(temp_dir, filename)

        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            logging.info(f"ISO already exists at {dest_path}, skipping download.")
            if progress_callback:
                progress_callback("ISO already exists", 100)
            return dest_path

        logging.info(f"Downloading ISO from {url} to {dest_path}")

        # Create unverified context to avoid SSL errors with some mirrors if certs are missing
        context = ssl._create_unverified_context()

        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", f"{AppInfo.namecase}/{AppInfo.version}")

            with urllib.request.urlopen(req, context=context, timeout=30) as response, open(
                dest_path, "wb"
            ) as out_file:
                content_length = response.getheader("Content-Length")
                total_size = int(content_length.strip()) if content_length else 0
                downloaded_size = 0
                chunk_size = 1024 * 1024  # 1MB chunks

                # Initialize speed calculation variables
                start_time = time.time()
                last_update_time = start_time
                last_downloaded_size = 0
                speed_update_interval = 1.0  # Update speed every second
                speed_str = "0 B"  # Initialize speed display

                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded_size += len(chunk)

                    current_time = time.time()

                    # Update progress with speed calculation
                    if progress_callback and total_size > 0:
                        percent = int((downloaded_size / total_size) * 100)

                        # Calculate speed every second or on final chunk
                        time_since_last_update = current_time - last_update_time
                        if (
                            time_since_last_update >= speed_update_interval
                            or downloaded_size == total_size
                        ):
                            # Calculate current speed
                            bytes_downloaded_since_last = downloaded_size - last_downloaded_size
                            if time_since_last_update > 0:
                                current_speed = bytes_downloaded_since_last / time_since_last_update
                            else:
                                current_speed = 0

                            # Format speed for display and store it
                            speed_str = self._format_download_speed(current_speed)

                            # Update tracking variables
                            last_update_time = current_time
                            last_downloaded_size = downloaded_size

                        # Always show speed (using last calculated value)
                        message = StaticText.PROVISIONING_DOWNLOADING_ISO_SPEED.format(
                            percent=percent, speed=speed_str
                        )
                        progress_callback(message, percent)

            logging.info(f"Successfully downloaded ISO to {dest_path}")
            return dest_path

        except Exception as e:
            logging.error(f"Failed to download ISO from {url}: {e}")
            if os.path.exists(dest_path):
                try:
                    os.remove(dest_path)  # Clean up partial file
                except Exception:
                    pass
            raise Exception(
                ErrorMessages.CUSTOM_ISO_DOWNLOAD_FAILED_TEMPLATE.format(url=url, error=str(e))
            ) from e

    def upload_iso(
        self,
        local_path: str,
        storage_pool_name: str,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> str:
        """
        Uploads a local ISO file to the specified storage pool.
        Returns the path of the uploaded volume on the server.
        """
        return self.upload_file(
            local_path,
            storage_pool_name,
            volume_name=os.path.basename(local_path),
            progress_callback=progress_callback,
        )

    def upload_file(
        self,
        local_path: str,
        storage_pool_name: str,
        volume_name: str | None = None,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> str:
        """
        Uploads a local file to the specified storage pool.
        Returns the path of the uploaded volume on the server.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        file_size = os.path.getsize(local_path)
        if volume_name is None:
            volume_name = os.path.basename(local_path)

        pool = self.conn.storagePoolLookupByName(storage_pool_name)
        if not pool.isActive():
            raise Exception(f"Storage pool {storage_pool_name} is not active.")

        # Check if volume already exists
        try:
            vol = pool.storageVolLookupByName(volume_name)
            logging.info(
                f"Volume '{volume_name}' already exists in pool '{storage_pool_name}'. Skipping upload."
            )
            if progress_callback:
                progress_callback(100)
            return vol.path()
        except libvirt.libvirtError:
            pass  # Volume does not exist, proceed to create

        # Create volume
        vol_xml = f"""
        <volume>
            <name>{volume_name}</name>
            <capacity unit="bytes">{file_size}</capacity>
            <target>
                <format type='raw'/>
            </target>
        </volume>
        """
        vol = pool.createXML(vol_xml, 0)

        # --- Keepalive logic for long uploads ---
        old_interval, old_count = -1, 0
        try:
            # Try to get original keepalive settings
            old_interval, old_count = self.conn.getKeepAlive()
        except (libvirt.libvirtError, AttributeError):
            pass

        try:
            # Set a more aggressive keepalive for the long operation
            self.conn.setKeepAlive(10, 5)
            logging.info("Set libvirt keepalive to 10s for file upload.")
        except (libvirt.libvirtError, AttributeError):
            logging.warning("Could not set libvirt keepalive for upload.")

        try:
            # Upload data
            stream = self.conn.newStream(0)
            try:
                vol.upload(stream, 0, file_size)

                with open(local_path, "rb") as f:
                    uploaded = 0
                    chunk_count = 0
                    while True:
                        data = f.read(1024 * 1024)  # 1MB chunk
                        if not data:
                            break
                        stream.send(data)
                        uploaded += len(data)
                        chunk_count += 1

                        # Periodically ping libvirt to keep connection alive during long uploads
                        # Every 10MB seems reasonable to prevent timeouts on some connections
                        if chunk_count % 10 == 0:
                            try:
                                self.conn.getLibVersion()
                            except:
                                pass

                        if progress_callback:
                            percent = int((uploaded / file_size) * 100)
                            progress_callback(percent)

                stream.finish()
            except Exception as e:
                try:
                    stream.abort()
                except:
                    pass
                vol.delete(0)
                raise e

            return vol.path()
        finally:
            # Restore original keepalive settings
            if old_interval != -1:
                try:
                    self.conn.setKeepAlive(old_interval, old_count)
                    logging.info(
                        f"Restored libvirt keepalive to interval={old_interval}, count={old_count}."
                    )
                except libvirt.libvirtError:
                    logging.warning("Could not restore original libvirt keepalive settings.")

    def validate_iso(self, local_path: str, expected_checksum: str = None) -> bool:
        """
        Validates the integrity of a local ISO file using SHA256.
        If expected_checksum is provided, returns True if matches, False otherwise.
        If not provided, returns True (just calculates and logs).
        """
        if not os.path.exists(local_path):
            return False

        sha256_hash = hashlib.sha256()
        with open(local_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)

        calculated_checksum = sha256_hash.hexdigest()
        logging.info(f"Calculated checksum for {local_path}: {calculated_checksum}")

        if expected_checksum:
            return calculated_checksum.lower() == expected_checksum.lower()

        return True

    def _format_speed(self, bytes_per_sec: float) -> str:
        """Format download speed in human-readable format."""
        units = ["B", "KB", "MB", "GB"]
        unit_index = 0
        speed = bytes_per_sec

        while speed >= 1024 and unit_index < len(units) - 1:
            speed /= 1024
            unit_index += 1

        return f"{speed:.1f} {units[unit_index]}"

    def _get_sev_capabilities(self) -> Dict[str, Any]:
        """
        Retrieves SEV capabilities from the host.
        """
        # getDomainCapabilities or /sys/module/kvm_amd/parameters/sev
        # For now, we return 'auto' defaults or hardcoded safe values if needed.
        return {
            "cbitpos": 47,  # Typical for AMD EPYC
            "reducedPhysBits": 1,
            "policy": "0x0033",
        }

    def _setup_uefi_nvram(
        self,
        vm_name: str,
        target_pool_name: str,
        vm_type: VMType,
        support_snapshots: bool = True,
        os_type: OSType = OSType.OPENSUSE,
    ) -> tuple[str, str]:
        """
        Sets up UEFI NVRAM on the server side by:
        1. Finding suitable firmware using the firmware_manager.
        2. Identifying the code/vars pair from the firmware metadata.
        3. Cloning the vars template to the target pool.

        Note: NVRAM is always created in QCOW2 format to support snapshots.

        Args:
            support_snapshots: Deprecated parameter, kept for backward compatibility.
                              NVRAM is always created in QCOW2 format.
            os_type: The OS type being provisioned.

        Returns: (loader_path, nvram_path)
        """
        all_firmwares = get_uefi_files(self.conn)

        # Determine requirements based on vm_type
        secure_boot = vm_type == VMType.SECURE

        # Disable Secure Boot by default for Arch Linux, Debian and Alpine to prevent installation failures
        if os_type in [OSType.ARCHLINUX, OSType.DEBIAN, OSType.ALPINE]:
            self.logger.info(
                f"Disabling Secure Boot for {os_type.name} to ensure successful installation."
            )
            secure_boot = False

        candidate_fw = select_best_firmware(
            all_firmwares,
            architecture=self.host_arch,
            secure_boot=secure_boot,
            prefer_nvram=True,
        )

        if not candidate_fw or not candidate_fw.executable:
            # If absolutely no firmware is found, we should probably fail or also fallback to auto
            # But if select_best_firmware returns None, it means no suitable firmware found at all.
            logging.warning("No suitable UEFI firmware found by manager. Letting libvirt decide.")
            return None, None

        if not candidate_fw.nvram_template:
            logging.warning(
                f"Selected firmware '{candidate_fw.executable}' has no NVRAM template. "
                "Skipping manual NVRAM setup and letting libvirt decide."
            )
            return None, None

        loader_path = candidate_fw.executable
        vars_template_path = candidate_fw.nvram_template

        fw_dir = os.path.dirname(vars_template_path)
        vars_vol_name = os.path.basename(vars_template_path)
        temp_pool_name = f"virtui-fw-{vm_name}"
        temp_pool = None

        # Clean up any leftover temp pool from a previous failed run
        try:
            p = self.conn.storagePoolLookupByName(temp_pool_name)
            if p.isActive():
                p.destroy()
            p.undefine()
        except libvirt.libvirtError:
            pass

        try:
            # Define a temporary pool for the firmware directory
            xml = f"<pool type='dir'><name>{temp_pool_name}</name><target><path>{fw_dir}</path></target></pool>"
            temp_pool = self.conn.storagePoolDefineXML(xml, 0)
            temp_pool.create(0)

            source_vol = temp_pool.storageVolLookupByName(vars_vol_name)
            
            # Use 'nvram' pool if it exists and is active, otherwise fallback to target_pool_name
            target_pool = None
            try:
                nvram_pool = self.conn.storagePoolLookupByName("nvram")
                if nvram_pool.isActive():
                    target_pool = nvram_pool
                    logging.info("Using dedicated 'nvram' storage pool for UEFI variables.")
            except libvirt.libvirtError:
                pass
                
            if not target_pool:
                target_pool = self.conn.storagePoolLookupByName(target_pool_name)

            # Always use QCOW2 format for NVRAM to support snapshots
            nvram_format = "qcow2"
            nvram_name = f"{vm_name}_VARS.qcow2"

            nvram_path = None

            # Check if we need conversion
            # We need conversion if:
            # 1. The firmware interface is not pflash (legacy reason)
            # 2. OR we are requesting qcow2 format (source templates are almost always raw)
            has_pflash = "pflash" in candidate_fw.interfaces
            needs_conversion = (not has_pflash) or (nvram_format == "qcow2")

            logging.info(
                f"Selected firmware: loader='{loader_path}', nvram_template='{vars_template_path}'"
            )

            # Check if already exists in target pool
            try:
                target_vol = target_pool.storageVolLookupByName(nvram_name)
                logging.info(f"NVRAM volume '{nvram_name}' already exists.")
                nvram_path = target_vol.path()
            except libvirt.libvirtError:
                logging.info(
                    f"Creating new {nvram_format} NVRAM volume '{nvram_name}' from '{vars_vol_name}'"
                )

                source_capacity = source_vol.info()[1]

                # Download source content from template
                stream_down = self.conn.newStream(0)
                source_vol.download(stream_down, 0, source_capacity)

                received_data = bytearray()
                while True:
                    try:
                        chunk = stream_down.recv(1024 * 1024)
                        if not chunk:
                            break
                        received_data.extend(chunk)
                    except libvirt.libvirtError as e:
                        if e.get_error_code() == libvirt.VIR_ERR_RPC:
                            break
                        raise

                stream_down.finish()

                if not received_data:
                    raise Exception(
                        f"Failed to download content from NVRAM template '{vars_vol_name}'. Template appears to be empty."
                    )

                if needs_conversion:
                    logging.info(
                        f"Converting NVRAM template to {nvram_format} using qemu-img (pflash={has_pflash})."
                    )
                    # Create temporary files for conversion
                    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False, dir="/var/tmp") as tmp_in:
                        try:
                            tmp_in.write(received_data)
                            tmp_in.flush()
                            tmp_in_name = tmp_in.name
                        except Exception as e:
                            os.remove(tmp_in.name)
                            raise e

                    tmp_out_name = tmp_in_name + f".{nvram_format}"  # safe suffix

                    try:
                        # Run qemu-img convert
                        cmd = ["qemu-img", "convert", "-O", nvram_format, tmp_in_name, tmp_out_name]
                        subprocess.run(cmd, check=True)

                        # Read back converted data
                        with open(tmp_out_name, "rb") as f:
                            received_data = f.read()

                        # Update capacity to match converted size
                        source_capacity = len(received_data)
                    finally:
                        if os.path.exists(tmp_in_name):
                            os.remove(tmp_in_name)
                        if os.path.exists(tmp_out_name):
                            os.remove(tmp_out_name)

                # Create new volume in target pool with specified format
                new_vol_xml = f"""
                <volume>
                    <name>{nvram_name}</name>
                    <capacity>{source_capacity}</capacity>
                    <target>
                        <format type='{nvram_format}'/>
                    </target>
                </volume>
                """
                target_vol = target_pool.createXML(new_vol_xml, 0)

                # Upload data to the new volume
                stream_up = self.conn.newStream(0)
                target_vol.upload(stream_up, 0, len(received_data))
                stream_up.send(received_data)
                stream_up.finish()

                nvram_path = target_vol.path()
                logging.info(f"Created {nvram_format.upper()} NVRAM: {nvram_name} at {nvram_path}")

            return loader_path, nvram_path

        finally:
            # Cleanup temp pool
            if temp_pool:
                try:
                    if temp_pool.isActive():
                        temp_pool.destroy()
                    temp_pool.undefine()
                except libvirt.libvirtError:
                    pass

    def _get_pool_path(self, pool: libvirt.virStoragePool) -> str:
        xml = ET.fromstring(pool.XMLDesc(0))
        return xml.find("target/path").text

    def _find_iso_volume_by_path(self, path: str) -> str | None:
        """
        Checks if the given path corresponds to an existing libvirt storage volume
        across all active pools. Returns the volume's path if found, otherwise None.
        """
        if path.startswith("file://"):
            path = path[7:]

        try:
            # List all active pools
            pools = self.conn.listAllStoragePools(libvirt.VIR_STORAGE_POOL_RUNNING)
            for pool in pools:
                try:
                    # Check if the path matches any volume in this pool
                    # We can't directly lookup by path, so we list volumes and check their paths
                    volumes = pool.listAllVolumes(0)
                    for vol in volumes:
                        if vol.path() == path:
                            logging.info(f"Found existing ISO volume: {path} in pool {pool.name()}")
                            return path
                except libvirt.libvirtError:
                    # Ignore pools that might be in a bad state
                    pass
        except libvirt.libvirtError as e:
            logging.warning(f"Failed to list storage pools to find ISO volume: {e}")

        return None

    def _get_vm_settings(
        self,
        vm_type: VMType,
        boot_uefi: bool,
        disk_format: str | None = None,
        os_type: OSType = OSType.OPENSUSE,
        graphics_type: str = "spice",
        is_auto_install: bool = False,
    ) -> Dict[str, Any]:
        """
        Returns a dictionary of VM settings based on type and options.

        Args:
            vm_type: The type of VM (SECURE, COMPUTATION, DESKTOP, etc.)
            boot_uefi: Whether to use UEFI boot
            disk_format: Optional disk format override
            os_type: The OS type being installed
            graphics_type: Graphics type (spice, vnc, etc.)
            is_auto_install: Whether this is an auto-installation (sets on_reboot to 'destroy')

        Returns:
            Dictionary of VM settings including machine type, video, network, etc.
        """
        # Detect latest machine types available on the hypervisor
        machine_types = get_latest_machine_types(self.conn, self.host_arch)
        default_machine = machine_types["pc-q35"] if boot_uefi else machine_types["pc-i440fx"]

        settings = {
            # Storage
            "disk_bus": "virtio",
            "disk_format": "qcow2",
            "disk_cache": "none",
            # Guest
            "machine": default_machine,
            "video": "virtio",
            "graphics_type": graphics_type,
            "network_model": "e1000",
            "suspend_to_mem": "off",
            "suspend_to_disk": "off",
            "boot_uefi": boot_uefi,
            "secure_boot": False,
            "iothreads": 0,
            "input_bus": "virtio",
            "sound_model": "none",
            # Features
            "sev": False,
            "tpm": False,
            "mem_backing": False,
            "watchdog": False,
            "on_poweroff": "destroy",
            "on_reboot": "destroy",
            "on_crash": "destroy",
            "virtio_channels": ["org.qemu.guest_agent.0"],
        }

        # Add SPICE agent channel if using SPICE
        if graphics_type == "spice":
            settings["virtio_channels"].append("com.redhat.spice.0")

        # Disable SEV/TPM/Secure Boot by default for Arch Linux, Debian and Alpine
        is_arch_debian_or_alpine = os_type in [OSType.ARCHLINUX, OSType.DEBIAN, OSType.ALPINE]

        if vm_type == VMType.SECURE:
            settings.update(
                {
                    "disk_cache": "writethrough",
                    "disk_format": "qcow2",
                    "video": "qxl",
                    "tpm": True if not is_arch_debian_or_alpine else False,
                    "sev": True if not is_arch_debian_or_alpine else False,
                    "secure_boot": True if not is_arch_debian_or_alpine else False,
                    "input_bus": "ps2",
                    "mem_backing": False,  # Explicitly off in table
                    "on_poweroff": "destroy",
                    "on_reboot": "destroy",
                    "on_crash": "destroy",
                }
            )
            if is_arch_debian_or_alpine:
                self.logger.info(
                    f"Disabling Secure Boot components (TPM/SEV/Secure Boot) for {os_type.name} even in SECURE mode to ensure successful installation."
                )
        elif vm_type == VMType.COMPUTATION:
            settings.update(
                {
                    "disk_cache": "unsafe",
                    "disk_format": "raw",
                    "video": "qxl",
                    "network_model": "virtio",
                    "iothreads": 4,
                    "mem_backing": "memfd",  # memfd/shared
                    "watchdog": True,
                    "on_poweroff": "restart",
                    "on_reboot": "restart",
                    "on_crash": "restart",
                }
            )
        elif vm_type == VMType.DESKTOP:
            settings.update(
                {
                    "disk_cache": "none",
                    "disk_format": "qcow2",
                    "video": "virtio",
                    "network_model": "e1000",
                    "suspend_to_mem": "on",
                    "suspend_to_disk": "on",
                    "mem_backing": "memfd",
                    "sound_model": "ich9",
                    "on_poweroff": "destroy",
                    "on_reboot": "restart",
                    "on_crash": "destroy",
                }
            )
        elif vm_type == VMType.WDESKTOP or vm_type == VMType.WLDESKTOP:
            settings.update(
                {
                    "disk_bus": "sata",
                    "disk_cache": "none",
                    "disk_format": "qcow2",
                    "video": "virtio",
                    "network_model": "e1000",
                    "suspend_to_mem": "on",
                    "suspend_to_disk": "on",
                    "mem_backing": "memfd",
                    "sound_model": "ich9",
                    "tpm": True if vm_type == VMType.WDESKTOP else False,
                    "on_poweroff": "destroy",
                    "on_reboot": "restart",
                    "on_crash": "destroy",
                }
            )
            if vm_type == VMType.WLDESKTOP:
                # Use latest pc-i440fx for WLDESKTOP (legacy Windows desktop)
                settings["machine"] = machine_types["pc-i440fx"]
                settings["input_bus"] = "usb"

        elif vm_type == VMType.SERVER:
            settings.update(
                {
                    "disk_cache": "none",
                    "disk_format": "qcow2",
                    "video": "virtio",
                    "network_model": "virtio",
                    "suspend_to_mem": "on",
                    "suspend_to_disk": "on",
                    "mem_backing": False,
                    "on_poweroff": "destroy",
                    "on_reboot": "restart",
                    "on_crash": "restart",
                }
            )

        # Override disk format if provided
        if disk_format:
            settings["disk_format"] = disk_format

        # Alpine Linux (especially virt ISO) only supports virtio-net
        if os_type == OSType.ALPINE:
            settings["network_model"] = "virtio"

        # For auto-installation, ensure on_reboot is "destroy" so the VM stops after installation
        # This allows the app to detect completion, cleanup HTTP server, and strip installation assets
        # The on_reboot will be changed back to "restart" (except for SECURE VMs) by strip_installation_assets
        if is_auto_install and vm_type != VMType.SECURE:
            self.logger.info(
                f"Auto-installation mode: setting on_reboot to 'destroy' for {vm_type.value} VM"
            )
            settings["on_reboot"] = "destroy"

        return settings

    def generate_xml(
        self,
        vm_name: str,
        vm_type: VMType,
        disk_path: str,
        iso_path: str,
        memory_mb: int = 4096,
        vcpu: int = 2,
        disk_format: str | None = None,
        loader_path: str | None = None,
        nvram_path: str | None = None,
        boot_uefi: bool | None = None,
        automation_file_path: str | None = None,
        auto_url: str | None = None,
        kernel_path: str | None = None,
        initrd_path: str | None = None,
        serial_console: bool = False,
        os_type: OSType = OSType.OPENSUSE,
        graphics_type: str = "spice",
        os_version: str | None = None,
        network_name: str = "default",
        ovmf_debug: bool = False,
    ) -> str:
        """
        Generates the Libvirt XML for the VM based on the type and default settings.
        """
        # If boot_uefi is None, use provider preference
        if boot_uefi is None:
            provider = self.provider_registry.get_provider(os_type)
            boot_uefi = provider.preferred_boot_uefi if provider else True

        # Determine if this is an auto-installation (has auto_url)
        is_auto_install = bool(auto_url)

        settings = self._get_vm_settings(
            vm_type, boot_uefi, disk_format, os_type=os_type, graphics_type=graphics_type,
            is_auto_install=is_auto_install
        )

        # Boot order: if kernel_path is provided, we are doing a direct kernel boot for install
        hd_boot, cd_boot = 1, 2
        # If we have a kernel_path, we are doing a direct boot for installation.
        # After installation, it will reboot and should boot from HD.
        # If we don't have kernel_path (standard ISO boot), we want CDROM first for the install boot.
        if not kernel_path:
            hd_boot, cd_boot = 2, 1

        # --- XML Construction ---
        # UUID generation handled by libvirt if omitted
        xml = f"""
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <name>{vm_name}</name>
  <memory unit='KiB'>{memory_mb * 1024}</memory>
  <currentMemory unit='KiB'>{memory_mb * 1024}</currentMemory>
  <vcpu placement='static'>{vcpu}</vcpu>
"""
        # Kernel-based boot for direct boot (automated or manual Arch/Debian UEFI)
        if kernel_path and initrd_path:
            os_firmware = " firmware='efi'" if settings["boot_uefi"] else ""
            xml += f"""
  <os{os_firmware}>
    <type arch='x86_64' machine='{settings["machine"]}' >hvm</type>
"""
            # Explicitly disable secure boot if UEFI is used but secure_boot is False
            # Only for specific distros that have issues with it (Arch, Debian, Alpine)
            if settings["boot_uefi"] and not settings.get("secure_boot") and os_type in [OSType.ARCHLINUX, OSType.DEBIAN, OSType.ALPINE]:
                xml += """    <firmware>
      <feature enabled='no' name='secure-boot'/>
    </firmware>
"""
            xml += f"""    <kernel>{kernel_path}</kernel>
    <initrd>{initrd_path}</initrd>
"""
            cmdline = ""
            logging.info(
                f"generate_xml: kernel boot detected, auto_url='{auto_url}', os_type={os_type}"
            )
            if auto_url:
                # Determine the appropriate kernel arguments based on file type and URL
                if "archinstall-setup-" in auto_url and auto_url.endswith(".sh"):
                    # Arch Linux archinstall automation with setup script
                    cmdline = f"script={auto_url} ipv6.disable=1 ip=::::arch-installer:eth0:dhcp archisobasedir=arch archisodevice=/dev/sr0"
                elif auto_url.endswith(".json"):
                    # Agama (openSUSE) automation
                    # Multiple flags to disable SSL verification and allow insecure HTTP
                    cmdline = f"inst.auto={auto_url} inst.insecure=1 inst.auto_insecure=1 ssl_verify=no"
                elif auto_url.endswith("/") or "user-data" in auto_url:
                    # Ubuntu autoinstall automation (cloud-init based)
                    # URL should point to directory containing user-data and meta-data
                    cmdline = f"ip=dhcp cloud-config-url={auto_url}user-data autoinstall ds=nocloud-net;s={auto_url}"
                elif "alpine-" in auto_url and ".apkovl.tar.gz" in auto_url:
                    # Alpine Linux apkovl automation
                    ver = os_version if os_version else "v3.23"
                    if not ver.startswith("v"):
                        ver = f"v{ver}"
                    cmdline = (
                        f"alpine_repo=http://dl-cdn.alpinelinux.org/alpine/{ver}/main "
                        f"apkovl={auto_url} "
                        f"ip=dhcp "
                    )
                elif "alpine-answers-" in auto_url and auto_url.endswith(".txt"):
                    # Alpine Linux answers automation
                    # Use version-specific repo if available
                    ver = os_version if os_version else "v3.23"
                    if not ver.startswith("v"):
                        ver = f"v{ver}"
                    # Use 'apkovl' and ensure networking is up with 'ip=dhcp'
                    cmdline = (
                        f"alpine_repo=http://dl-cdn.alpinelinux.org/alpine/{ver}/main "
                        f"apkovl={auto_url} "
                        f"ip=dhcp "
                        f"setup_alpine_noninteractive=1 "
                    )
                elif "ks-" in auto_url and auto_url.endswith(".cfg"):
                    # Fedora kickstart automation
                    cmdline = f"inst.ks={auto_url}"
                elif auto_url.endswith(".cfg"):
                    if os_type in [OSType.UBUNTU, OSType.DEBIAN]:
                        # Ubuntu/Debian preseed automation
                        cmdline = (
                            f"auto=true preseed/url={auto_url} hostname={vm_name} domain=home.net"
                        )
                    else:
                        # Default to AutoYaST for other distros (e.g. SLES/openSUSE)
                        cmdline = f"autoyast={auto_url} netsetup=dhcp"
                else:
                    # OpenSUSE AutoYaST automation (XML format)
                    # Add netsetup=dhcp to ensure network is configured
                    cmdline = f"autoyast={auto_url} netsetup=dhcp"
            else:
                # Default cmdline for direct kernel boot without automation
                if os_type == OSType.UBUNTU:
                    cmdline = "boot=casper"
                elif os_type == OSType.DEBIAN:
                    cmdline = "boot=live"
                elif os_type == OSType.FEDORA:
                    cmdline = "inst.stage2"
                elif os_type == OSType.OPENSUSE:
                    cmdline = "install=cd:/"
                elif os_type == OSType.ARCHLINUX:
                    cmdline = "archisobasedir=arch archisodevice=/dev/sr0"
                elif os_type == OSType.ALPINE:
                    ver = os_version if os_version else "v3.23"
                    if not ver.startswith("v"):
                        ver = f"v{ver}"
                    cmdline = f"alpine_repo=http://dl-cdn.alpinelinux.org/alpine/{ver}/main ip=dhcp"
                else:
                    # Fallback for other Linux distros
                    cmdline = "quiet"

            if serial_console:
                if cmdline:
                    cmdline += " "
                cmdline += "console=tty0 console=ttyS0,115200"

            if cmdline:
                xml += f"    <cmdline>{cmdline}</cmdline>\n"
            xml += "  </os>"
        # Standard UEFI/BIOS boot
        elif settings["boot_uefi"]:
            secure_attr = "yes" if settings.get("secure_boot") else "no"
            if loader_path and nvram_path:
                xml += f"""
  <os>
    <type arch='x86_64' machine='{settings["machine"]}'>hvm</type>
    <loader readonly='yes' secure='{secure_attr}' type='pflash'>{loader_path}</loader>
    <nvram format='qcow2'>{nvram_path}</nvram>
  </os>
"""
            else:
                xml += f"""
  <os firmware='efi'>
    <type arch='x86_64' machine='{settings["machine"]}'>hvm</type>
"""
                # Explicitly disable secure boot if requested OS has issues with it
                if not settings.get("secure_boot") and os_type in [OSType.ARCHLINUX, OSType.DEBIAN, OSType.ALPINE]:
                    xml += """    <firmware>
      <feature enabled='no' name='secure-boot'/>
    </firmware>
"""
                xml += f"""    <loader readonly='yes' secure='{secure_attr}' type='pflash'/>
"""
                if nvram_path:
                    xml += f"    <nvram format='qcow2'>{nvram_path}</nvram>\n"
                else:
                    xml += "    <nvram format='qcow2'/>\n"
                xml += """
  </os>
"""
        else:  # Standard BIOS
            xml += f"""
  <os>
    <type arch='x86_64' machine='{settings["machine"]}'>hvm</type>
  </os>
"""

        xml += """
  <features>
    <acpi/>
    <apic/>
    <pae/>
  </features>
  <cpu mode='host-passthrough' check='none' migratable='on'/>
  <clock offset='utc'/>
  <on_poweroff>{0}</on_poweroff>
  <on_reboot>{1}</on_reboot>
  <on_crash>{2}</on_crash>
""".format(
            settings.get("on_poweroff", "destroy"),
            settings.get("on_reboot", "restart"),
            settings.get("on_crash", "destroy"),
        )

        if settings["suspend_to_mem"] == "on" or settings["suspend_to_disk"] == "on":
            xml += "  <pm>\n"
            if settings["suspend_to_mem"] == "on":
                xml += "    <suspend-to-mem enabled='yes'/>\n"
            if settings["suspend_to_disk"] == "on":
                xml += "    <suspend-to-disk enabled='yes'/>\n"
            xml += "  </pm>\n"

        if settings["sev"]:
            sev_caps = self._get_sev_capabilities()
            xml += f"""
  <launchSecurity type='sev'>
    <cbitpos>{sev_caps["cbitpos"]}</cbitpos>
    <reducedPhysBits>{sev_caps["reducedPhysBits"]}</reducedPhysBits>
    <policy>{sev_caps["policy"]}</policy>
  </launchSecurity>
"""

        xml += "  <devices>\n"

        # Disk
        xml += f"""
    <disk type='file' device='disk'>
      <driver name='qemu' type='{settings["disk_format"]}' cache='{settings["disk_cache"]}'/>
      <source file='{disk_path}'/>
      <target dev='vda' bus='{settings["disk_bus"]}'/>
      <boot order='{hd_boot}'/>
    </disk>
"""

        # CDROM (ISO)
        xml += f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{iso_path}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
      <boot order='{cd_boot}'/>
    </disk>
"""

        # Floppy disk for automation file (fallback for non-kernel boot)
        if automation_file_path and not kernel_path:
            xml += f"""
    <disk type='file' device='floppy'>
      <driver name='qemu' type='raw'/>
      <source file='{automation_file_path}'/>
      <target dev='fda' bus='fdc'/>
      <readonly/>
    </disk>
    <controller type='fdc' index='0'/>
"""

        # Interface
        xml += f"""
    <interface type='network'>
      <source network='{network_name}'/>
      <model type='{settings["network_model"]}'/>
    </interface>
"""

        # Video
        xml += f"""
    <video>
      <model type='{settings["video"]}'/>
    </video>
    <graphics type='{settings["graphics_type"]}' port='-1' autoport='yes' listen='0.0.0.0'>
      <listen type='address' address='0.0.0.0'/>
    </graphics>
"""
        # Sound
        if settings.get("sound_model") and settings["sound_model"] != "none":
            xml += f"""
    <sound model='{settings["sound_model"]}'/>
"""

        # TPM (Secure VM)
        if settings["tpm"]:
            xml += """
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>
"""
        # Watchdog (Computation)
        if settings["watchdog"]:
            xml += """
    <watchdog model='i6300esb' action='poweroff'/>
"""

        # Console/Serial
        xml += """
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
"""

        # Channels (Guest Agent, SPICE Agent, etc)
        for channel_name in settings.get("virtio_channels", []):
            channel_type = "spicevmc" if channel_name == "com.redhat.spice.0" else "unix"
            xml += f"""
    <channel type='{channel_type}'>
      <target type='virtio' name='{channel_name}'/>
    </channel>
"""

        # Input devices (Tablet for better mouse)
        xml += f"""
    <input type='tablet' bus='usb'/>
    <input type='mouse' bus='{settings["input_bus"]}'/>
    <input type='keyboard' bus='{settings["input_bus"]}'/>
"""

        xml += "  </devices>\n"

        if settings["mem_backing"]:
            xml += f"  <memoryBacking>\n    <source type='{settings['mem_backing']}'/>\n"
            if settings.get("sev"):
                xml += "    <locked/>\n"  # Often needed for SEV
            xml += "  </memoryBacking>\n"

        if ovmf_debug:
            # DEBUG OVMF issue
            xml += """
  <qemu:commandline>
    <qemu:arg value='-global'/>
    <qemu:arg value='isa-debugcon.iobase=0x402'/>
    <qemu:arg value='-debugcon'/>
    <qemu:arg value='file:/tmp/debug.log'/>
  </qemu:commandline>
"""
        xml += "</domain>"
        return xml

    def check_virt_install(self) -> bool:
        """Checks if virt-install is available on the system."""
        return shutil.which("virt-install") is not None

    def _get_host_ip_for_vms(self) -> str:
        """
        Gets the host IP address that VMs can use to reach the host.
        For local libvirt connections, this is typically the virbr0 bridge IP (usually 192.168.122.1).
        For remote connections, we try to determine the best IP to use.
        """
        try:
            # For local connections, use the default libvirt bridge IP
            uri = self.conn.getURI()
            if "qemu:///system" in uri or "qemu:///session" in uri:
                # Try to get virbr0 IP address
                try:
                    if "virbr0" in netifaces.interfaces():
                        addrs = netifaces.ifaddresses("virbr0")
                        if netifaces.AF_INET in addrs:
                            return addrs[netifaces.AF_INET][0]["addr"]
                except ImportError:
                    # netifaces not available, use common default
                    pass
                except Exception as e:
                    self.logger.warning(f"Failed to get virbr0 IP: {e}")

                # Fall back to common default for local libvirt
                return "192.168.122.1"

            # For remote connections, try to get the host's primary IP
            # Create a socket to determine which interface would be used to reach the internet
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # Connect to a public DNS server (doesn't actually send data)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                return ip
            finally:
                s.close()

        except Exception as e:
            self.logger.warning(f"Failed to determine host IP, using 192.168.122.1: {e}")
            return "192.168.122.1"

    def _create_automation_floppy_image(
        self, automation_file_path: str, output_dir: Path, internal_filename: str = "autoinst.xml"
    ) -> str:
        """Creates a FAT-formatted floppy image containing the automation file."""
        floppy_image_path = output_dir / "automation.img"

        # Check for required tools
        if not all(shutil.which(cmd) for cmd in ["dd", "mkfs.vfat", "mcopy"]):
            raise Exception(
                "Floppy-based automation requires 'dd', 'mkfs.vfat', and 'mtools' (for mcopy). "
                "Please install these utilities or use the virt-install method."
            )

        try:
            # Create a blank 1.44MB floppy image
            subprocess.run(
                ["dd", "if=/dev/zero", f"of={floppy_image_path}", "bs=1k", "count=1440"],
                check=True,
                capture_output=True,
                text=True,
            )

            # Format the image with a FAT filesystem
            subprocess.run(
                ["mkfs.vfat", str(floppy_image_path)], check=True, capture_output=True, text=True
            )

            # mcopy needs the file to be in the current directory to use ::/ syntax easily
            # so we create a temp copy with the expected internal filename in the output dir
            temp_file_path = output_dir / internal_filename
            if Path(automation_file_path) != temp_file_path:
                shutil.copy(automation_file_path, temp_file_path)

            # Use mcopy to copy the file into the root of the floppy image
            subprocess.run(
                ["mcopy", "-i", str(floppy_image_path), str(temp_file_path), "::/"],
                check=True,
                capture_output=True,
                text=True,
            )

            os.remove(temp_file_path)  # Clean up temp copy

            self.logger.info(f"Successfully created automation floppy image at {floppy_image_path}")
            return str(floppy_image_path)

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if hasattr(e, "stderr") and e.stderr else str(e)
            self.logger.error(f"Failed to create floppy image: {stderr}")
            raise Exception(f"Failed to create automation floppy image: {stderr}") from e

    def _extract_iso_kernel_initrd(self, iso_path: str, arch: str) -> tuple[str, str]:
        """Extracts kernel and initrd from an openSUSE/SLES ISO."""
        if not shutil.which("7z"):
            raise Exception(
                "XML-based Auto with kernel arguments requires '7z' (p7zip-full). "
                "Please install it or use the virt-install method."
            )

        tmp_dir = tempfile.mkdtemp(prefix="virtui_iso_extract_")
        kernel_path_in_iso = f"boot/{arch}/loader/linux"
        initrd_path_in_iso = f"boot/{arch}/loader/initrd"

        try:
            self.logger.info(f"Extracting kernel and initrd from {iso_path}...")
            cmd = [
                "7z",
                "x",
                iso_path,
                kernel_path_in_iso,
                initrd_path_in_iso,
                f"-o{tmp_dir}",
                "-y",  # Assume yes to all queries
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True)

            extracted_kernel_path = os.path.join(tmp_dir, "boot", arch, "loader", "linux")
            extracted_initrd_path = os.path.join(tmp_dir, "boot", arch, "loader", "initrd")

            if not os.path.exists(extracted_kernel_path) or not os.path.exists(
                extracted_initrd_path
            ):
                raise FileNotFoundError(
                    "Kernel or initrd not found in the ISO at the expected path."
                )

            self.logger.info(f"Kernel and initrd extracted to {tmp_dir}")
            return extracted_kernel_path, extracted_initrd_path

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if hasattr(e, "stderr") and e.stderr else str(e)
            self.logger.error(f"Failed to extract kernel/initrd from ISO: {stderr}")
            # Clean up temp dir on failure
            shutil.rmtree(tmp_dir)
            raise Exception(f"Failed to extract kernel/initrd from ISO: {stderr}") from e

    def _extract_debian_iso_kernel_initrd(self, iso_path: str) -> tuple[str, str]:
        """Extracts kernel and initrd from a Debian ISO (install.amd/ directory)."""
        if not shutil.which("7z"):
            raise Exception(
                "Debian kernel extraction requires '7z' (p7zip-full). "
                "Please install it or use the virt-install method."
            )

        tmp_dir = tempfile.mkdtemp(prefix="virtui_debian_iso_extract_")

        # Debian kernel and initrd are in the install.amd/ directory
        kernel_candidates = ["install.amd/vmlinuz", "install.amd/linux"]
        initrd_candidates = ["install.amd/initrd.gz", "install.amd/initrd"]

        # Extract the entire install.amd directory
        try:
            self.logger.info(f"Extracting Debian install.amd/ directory from {iso_path}...")
            cmd = [
                "7z",
                "x",
                iso_path,
                "install.amd/",
                f"-o{tmp_dir}",
                "-y",  # Assume yes to all queries
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True)

            # Find the actual kernel and initrd files
            install_dir = os.path.join(tmp_dir, "install.amd")
            if not os.path.exists(install_dir):
                raise FileNotFoundError("install.amd/ directory not found in Debian ISO")

            # Find kernel file
            kernel_path = None
            for candidate in kernel_candidates:
                full_path = os.path.join(tmp_dir, candidate)
                if os.path.exists(full_path):
                    kernel_path = full_path
                    break

            if not kernel_path:
                # List available files for debugging
                available_files = []
                for file in os.listdir(install_dir):
                    if "vmlinuz" in file or "linux" in file:
                        available_files.append(f"install.amd/{file}")
                raise FileNotFoundError(
                    f"Debian kernel (vmlinuz/linux) not found in install.amd/. Available: {available_files}"
                )

            # Find initrd file
            initrd_path = None
            for candidate in initrd_candidates:
                full_path = os.path.join(tmp_dir, candidate)
                if os.path.exists(full_path):
                    initrd_path = full_path
                    break

            if not initrd_path:
                # List available files for debugging
                available_files = []
                for file in os.listdir(install_dir):
                    if "initrd" in file:
                        available_files.append(f"install.amd/{file}")
                raise FileNotFoundError(
                    f"Debian initrd not found in install.amd/. Available: {available_files}"
                )

            self.logger.info(f"Debian kernel and initrd extracted to {tmp_dir}")
            self.logger.info(f"  Kernel: {kernel_path}")
            self.logger.info(f"  Initrd: {initrd_path}")
            return kernel_path, initrd_path

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if hasattr(e, "stderr") and e.stderr else str(e)
            self.logger.error(f"Failed to extract Debian kernel/initrd from ISO: {stderr}")
            # Clean up temp dir on failure
            shutil.rmtree(tmp_dir)
            raise Exception(f"Failed to extract Debian kernel/initrd from ISO: {stderr}") from e

    def _extract_ubuntu_iso_kernel_initrd(self, iso_path: str) -> tuple[str, str]:
        """Extracts kernel and initrd from an Ubuntu ISO (casper/ directory)."""
        if not shutil.which("7z"):
            raise Exception(
                "Ubuntu kernel extraction requires '7z' (p7zip-full). "
                "Please install it or use the virt-install method."
            )

        tmp_dir = tempfile.mkdtemp(prefix="virtui_ubuntu_iso_extract_")

        # Ubuntu kernel and initrd are in the casper/ directory
        # Try both common naming patterns
        kernel_candidates = ["casper/vmlinuz", "casper/vmlinuz.efi"]
        initrd_candidates = ["casper/initrd", "casper/initrd.lz", "casper/initrd.gz"]

        # First, let's extract the entire casper directory to see what's available
        try:
            self.logger.info(f"Extracting Ubuntu casper/ directory from {iso_path}...")
            cmd = [
                "7z",
                "x",
                iso_path,
                "casper/",
                f"-o{tmp_dir}",
                "-y",  # Assume yes to all queries
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True)

            # Find the actual kernel and initrd files
            casper_dir = os.path.join(tmp_dir, "casper")
            if not os.path.exists(casper_dir):
                raise FileNotFoundError("casper/ directory not found in Ubuntu ISO")

            # Find kernel file
            kernel_path = None
            for candidate in kernel_candidates:
                full_path = os.path.join(tmp_dir, candidate)
                if os.path.exists(full_path):
                    kernel_path = full_path
                    break

            if not kernel_path:
                # List available files for debugging
                available_files = []
                for file in os.listdir(casper_dir):
                    if file.startswith("vmlinuz"):
                        available_files.append(f"casper/{file}")
                raise FileNotFoundError(
                    f"Ubuntu kernel (vmlinuz*) not found in casper/. Available: {available_files}"
                )

            # Find initrd file
            initrd_path = None
            for candidate in initrd_candidates:
                full_path = os.path.join(tmp_dir, candidate)
                if os.path.exists(full_path):
                    initrd_path = full_path
                    break

            if not initrd_path:
                # List available files for debugging
                available_files = []
                for file in os.listdir(casper_dir):
                    if file.startswith("initrd"):
                        available_files.append(f"casper/{file}")
                raise FileNotFoundError(
                    f"Ubuntu initrd not found in casper/. Available: {available_files}"
                )

            self.logger.info(f"Ubuntu kernel and initrd extracted to {tmp_dir}")
            self.logger.info(f"  Kernel: {kernel_path}")
            self.logger.info(f"  Initrd: {initrd_path}")
            return kernel_path, initrd_path

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if hasattr(e, "stderr") and e.stderr else str(e)
            self.logger.error(f"Failed to extract Ubuntu kernel/initrd from ISO: {stderr}")
            # Clean up temp dir on failure
            shutil.rmtree(tmp_dir)
            raise Exception(f"Failed to extract Ubuntu kernel/initrd from ISO: {stderr}") from e

    def _extract_fedora_iso_kernel_initrd(self, iso_path: str) -> tuple[str, str]:
        """Extracts kernel and initrd from a Fedora ISO (images/pxeboot/ directory)."""
        if not shutil.which("7z"):
            raise Exception(
                "Fedora kernel extraction requires '7z' (p7zip-full). "
                "Please install it or use the virt-install method."
            )

        tmp_dir = tempfile.mkdtemp(prefix="virtui_fedora_iso_extract_")

        # Fedora kernel and initrd are in the images/pxeboot/ directory
        kernel_path_in_iso = "images/pxeboot/vmlinuz"
        initrd_path_in_iso = "images/pxeboot/initrd.img"

        try:
            self.logger.info(f"Extracting Fedora kernel and initrd from {iso_path}...")
            cmd = [
                "7z",
                "x",
                iso_path,
                kernel_path_in_iso,
                initrd_path_in_iso,
                f"-o{tmp_dir}",
                "-y",
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True)

            extracted_kernel_path = os.path.join(tmp_dir, "images", "pxeboot", "vmlinuz")
            extracted_initrd_path = os.path.join(tmp_dir, "images", "pxeboot", "initrd.img")

            if not os.path.exists(extracted_kernel_path) or not os.path.exists(
                extracted_initrd_path
            ):
                raise FileNotFoundError(
                    "Fedora kernel or initrd not found in the ISO at the expected path."
                )

            self.logger.info(f"Fedora kernel and initrd extracted to {tmp_dir}")
            return extracted_kernel_path, extracted_initrd_path

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if hasattr(e, "stderr") and e.stderr else str(e)
            self.logger.error(f"Failed to extract Fedora kernel/initrd from ISO: {stderr}")
            # Clean up temp dir on failure
            shutil.rmtree(tmp_dir)
            raise Exception(f"Failed to extract Fedora kernel/initrd from ISO: {stderr}") from e

    def _extract_arch_iso_kernel_initrd(self, iso_path: str) -> tuple[str, str]:
        """Extracts kernel and initrd from an Arch Linux ISO (arch/boot/x86_64/ directory)."""
        if not shutil.which("7z"):
            raise Exception(
                "Arch Linux kernel extraction requires '7z' (p7zip-full). "
                "Please install it or use the virt-install method."
            )

        tmp_dir = tempfile.mkdtemp(prefix="virtui_arch_iso_extract_")

        # Arch kernel and initrd are in the arch/boot/x86_64/ directory
        kernel_path_in_iso = "arch/boot/x86_64/vmlinuz-linux"
        initrd_path_in_iso = "arch/boot/x86_64/initramfs-linux.img"

        try:
            self.logger.info(f"Extracting Arch Linux kernel and initrd from {iso_path}...")
            cmd = [
                "7z",
                "x",
                iso_path,
                kernel_path_in_iso,
                initrd_path_in_iso,
                f"-o{tmp_dir}",
                "-y",
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True)

            extracted_kernel_path = os.path.join(tmp_dir, "arch", "boot", "x86_64", "vmlinuz-linux")
            extracted_initrd_path = os.path.join(
                tmp_dir, "arch", "boot", "x86_64", "initramfs-linux.img"
            )

            if not os.path.exists(extracted_kernel_path) or not os.path.exists(
                extracted_initrd_path
            ):
                raise FileNotFoundError(
                    "Arch Linux kernel or initrd not found in the ISO at the expected path."
                )

            self.logger.info(f"Arch Linux kernel and initrd extracted to {tmp_dir}")
            return extracted_kernel_path, extracted_initrd_path

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if hasattr(e, "stderr") and e.stderr else str(e)
            self.logger.error(f"Failed to extract Arch Linux kernel/initrd from ISO: {stderr}")
            # Clean up temp dir on failure
            shutil.rmtree(tmp_dir)
            raise Exception(f"Failed to extract Arch Linux kernel/initrd from ISO: {stderr}") from e

    def _extract_alpine_iso_kernel_initrd(self, iso_path: str) -> tuple[str, str]:
        """Extracts kernel and initrd from an Alpine Linux ISO (boot/ directory)."""
        if not shutil.which("7z"):
            raise Exception(
                "Alpine Linux kernel extraction requires '7z' (p7zip-full). "
                "Please install it or use the virt-install method."
            )

        tmp_dir = tempfile.mkdtemp(prefix="virtui_alpine_iso_extract_")

        # Alpine kernel and initrd are in the boot/ directory
        # We try 'virt' first, then 'lts'
        variants = [
            ("boot/vmlinuz-virt", "boot/initramfs-virt"),
            ("boot/vmlinuz-lts", "boot/initramfs-lts"),
        ]

        last_error = None
        for kernel_in_iso, initrd_in_iso in variants:
            try:
                self.logger.info(f"Trying to extract Alpine {kernel_in_iso} from {iso_path}...")
                cmd = [
                    "7z",
                    "x",
                    iso_path,
                    kernel_in_iso,
                    initrd_in_iso,
                    f"-o{tmp_dir}",
                    "-y",
                ]
                subprocess.run(cmd, check=True, capture_output=True, text=True)

                extracted_kernel_path = os.path.join(tmp_dir, *kernel_in_iso.split("/"))
                extracted_initrd_path = os.path.join(tmp_dir, *initrd_in_iso.split("/"))

                if os.path.exists(extracted_kernel_path) and os.path.exists(extracted_initrd_path):
                    self.logger.info(f"Alpine Linux kernel and initrd extracted to {tmp_dir}")
                    return extracted_kernel_path, extracted_initrd_path
            except subprocess.CalledProcessError as e:
                last_error = e.stderr if hasattr(e, "stderr") and e.stderr else str(e)
                continue

        # Clean up temp dir on failure
        shutil.rmtree(tmp_dir)
        raise Exception(
            f"Failed to extract Alpine Linux kernel/initrd from ISO. Last error: {last_error}"
        )

    def _run_virt_install(
        self,
        vm_name: str,
        settings: Dict[str, Any],
        disk_path: str,
        iso_path: str,
        storage_pool_name: str,
        memory_mb: int,
        vcpu: int,
        loader_path: str | None,
        nvram_path: str | None,
        print_xml: bool = False,
        floppy_image_path: str | None = None,
        auto_url: str | None = None,
        is_remote_connection: bool = False,
        serial_console: bool = False,
        os_type: OSType = OSType.OPENSUSE,
        kernel_path: str | None = None,
        initrd_path: str | None = None,
        os_version: str | None = None,
        network_name: str = "default",
    ) -> str | None:
        """
        Executes virt-install to create the VM using the provided settings.
        If print_xml is True, it returns the generated XML instead of creating the VM.
        """
        # settings is already passed as an argument, no need to re-fetch it
        cmd = ["virt-install"]
        cmd.extend(["--connect", self.conn.getURI()])
        cmd.extend(["--name", vm_name])
        cmd.extend(["--memory", str(memory_mb)])
        cmd.extend(["--vcpus", str(vcpu)])
        if print_xml:
            cmd.append("--print-xml")

        # OS info
        cmd.extend(["--osinfo", "detect=on,name=generic"])

        # Disk
        disk_opt = f"path={disk_path},bus={settings['disk_bus']},format={settings['disk_format']},cache={settings['disk_cache']}"
        cmd.extend(["--disk", disk_opt])

        # Check virt-install version for vol=pool/vol support
        can_use_vol_location = False
        if self.virt_install_version:
            try:
                if parse_version(self.virt_install_version) >= parse_version("1.4.0"):
                    can_use_vol_location = True
            except Exception as e:
                self.logger.warning(
                    f"Error parsing virt-install version '{self.virt_install_version}': {e}"
                )

        # ISO
        if auto_url or kernel_path:
            if is_remote_connection:
                if not can_use_vol_location:
                    raise Exception(
                        "Remote Auto with virt-install requires virt-install >= 1.4.0 "
                        "for --location vol=... syntax. Please upgrade virt-install on the client "
                        "or disable 'Use virt-install' in the provisioning dialog."
                    )
                # For remote Auto, use vol=pool/volname for --location
                iso_vol_name = os.path.basename(iso_path)
                location_arg = f"vol={storage_pool_name}/{iso_vol_name}"
                cmd.extend(["--location", location_arg])
                logging.info(f"Using remote ISO location for virt-install: {location_arg}")

                # Explicitly add the ISO as a CD-ROM device so it's visible to the installer
                cmd.extend(["--disk", f"vol={storage_pool_name}/{iso_vol_name},device=cdrom"])
            else:
                # For local Auto or manual kernel boot, use --location with the local path
                cmd.extend(["--location", iso_path])

                # Explicitly add the ISO as a CD-ROM device
                cmd.extend(["--disk", f"path={iso_path},device=cdrom"])

            # If we have explicit kernel/initrd (e.g. for Arch/Debian manual UEFI)
            if kernel_path and initrd_path:
                cmd.extend(["--install", f"kernel={kernel_path},initrd={initrd_path}"])
        else:
            # For non-Auto, use --cdrom
            cmd.extend(["--cdrom", iso_path])

        # Network
        cmd.extend(["--network", f"{network_name},model={settings['network_model']}"])

        # Graphics
        cmd.extend(["--graphics", f"{settings['graphics_type']},port=-1,listen=0.0.0.0"])

        # Video
        cmd.extend(["--video", settings["video"]])

        # Sound
        if settings.get("sound_model") and settings["sound_model"] != "none":
            cmd.extend(["--sound", f"model={settings['sound_model']}"])

        # Console
        cmd.extend(["--console", "pty,target.type=serial"])

        # Channels (Guest Agent, SPICE Agent, etc)
        for channel_name in settings.get("virtio_channels", []):
            channel_type = "spicevmc" if channel_name == "com.redhat.spice.0" else "unix"
            cmd.extend(["--channel", f"{channel_type},target.type=virtio,name={channel_name}"])

        # Machine
        cmd.extend(["--machine", settings["machine"]])

        # Boot / Firmware
        boot_opts = []
        if auto_url or kernel_path:
            boot_opts.append("hd,cdrom,menu=on")
        else:
            boot_opts.append("cdrom,hd,menu=on")

        if settings["boot_uefi"]:
            secure_val = "on" if settings.get("secure_boot") else "off"
            if loader_path and nvram_path:
                # Explicit paths
                cmd.extend(
                    [
                        "--boot",
                        f"{boot_opts[0]},loader={loader_path},loader.readonly=yes,loader.type=pflash,loader.secure={secure_val},nvram={nvram_path},nvram.templateFormat=qcow2",
                    ]
                )
            else:
                # Auto
                cmd.extend(["--boot", f"{boot_opts[0]},uefi,uefi.secure={secure_val}"])
        else:
            cmd.extend(["--boot", boot_opts[0]])

        # Features
        if settings["sev"]:
            sev_caps = self._get_sev_capabilities()
            cmd.extend(
                [
                    "--launchSecurity",
                    f"sev,cbitpos={sev_caps['cbitpos']},reducedPhysBits={sev_caps['reducedPhysBits']},policy={sev_caps['policy']}",
                ]
            )

        if settings["tpm"]:
            cmd.extend(["--tpm", "model=tpm-crb,backend.type=emulator,backend.version=2.0"])

        if settings["watchdog"]:
            cmd.extend(["--watchdog", "model=i6300esb,action=poweroff"])

        # PM
        if settings["suspend_to_mem"] == "on" or settings["suspend_to_disk"] == "on":
            pm_opts = []
            if settings["suspend_to_mem"] == "on":
                pm_opts.append("suspend_to_mem=on")
            if settings["suspend_to_disk"] == "on":
                pm_opts.append("suspend_to_disk=on")
            cmd.extend(["--pm", ",".join(pm_opts)])

        # Automation file injection
        extra_args = ""
        if auto_url:
            # HTTP-based automation (uses --extra-args with --location)
            if "archinstall-setup-" in auto_url and auto_url.endswith(".sh"):
                # Arch Linux archinstall automation with setup script
                extra_args = f"script={auto_url} ip=::::arch-installer:eth0:dhcp archisobasedir=arch archisodevice=/dev/sr0"
            elif auto_url.endswith(".json"):
                # Agama (openSUSE) automation
                # Multiple flags to disable SSL verification and allow insecure HTTP
                extra_args = f"inst.auto={auto_url} inst.insecure=1 inst.auto_insecure=1 ssl_verify=no"
            elif auto_url.endswith("/") or "user-data" in auto_url:
                # Ubuntu autoinstall automation (cloud-init based)
                extra_args = f"ip=dhcp cloud-config-url={auto_url}user-data autoinstall ds=nocloud-net;s={auto_url}"
            elif "ks-" in auto_url and auto_url.endswith(".cfg"):
                # Fedora kickstart automation
                extra_args = f"inst.ks={auto_url}"
            elif "alpine-" in auto_url and (".apkovl.tar.gz" in auto_url or ".txt" in auto_url):
                # Alpine Linux automation (apkovl or answers)
                ver = os_version if os_version else "v3.23"
                if not ver.startswith("v"):
                    ver = f"v{ver}"

                extra_args = (
                    f"alpine_repo=http://dl-cdn.alpinelinux.org/alpine/{ver}/main "
                    f"apkovl={auto_url} "
                    f"ip=dhcp "
                )

                if "alpine-answers-" in auto_url:
                    # Add noninteractive flag for answers file installations
                    extra_args += "setup_alpine_noninteractive=1 "

            elif auto_url.endswith(".cfg"):
                if os_type in [OSType.UBUNTU, OSType.DEBIAN]:
                    # Ubuntu/Debian preseed automation
                    extra_args = f"auto=true preseed/url={auto_url}"
                else:
                    # Default to AutoYaST for other distros
                    extra_args = f"autoyast={auto_url} netsetup=dhcp"
            else:
                # OpenSUSE AutoYaST automation (XML format)
                # Add netsetup=dhcp to ensure network is configured
                extra_args = f"autoyast={auto_url} netsetup=dhcp"

        elif kernel_path and os_type == OSType.ARCHLINUX:
            # Manual Arch Linux UEFI boot needs these parameters even without automation
            extra_args = "archisobasedir=arch archisodevice=/dev/sr0"

        if extra_args:
            if serial_console:
                extra_args += " console=tty0 console=ttyS0,115200"
            cmd.extend(["--extra-args", extra_args])
            logging.info(f"Using extra-args: {extra_args}")
        elif floppy_image_path:
            # Legacy floppy-based approach (fallback for non-auto_url installs)
            cmd.extend(["--disk", f"path={floppy_image_path},device=floppy"])
            logging.info(f"Using floppy-based Auto: {floppy_image_path}")

        cmd.extend(["--noautoconsole"])

        logging.info(f"Running: {(' '.join(cmd))}")
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            if print_xml:
                return result.stdout
            if result.stdout:
                logging.info(f"virt-install stdout: {result.stdout.strip()}")
            if result.stderr:
                logging.warning(f"virt-install stderr: {result.stderr.strip()}")
        except subprocess.CalledProcessError as e:
            logging.error(f"virt-install command failed with exit code {e.returncode}")
            logging.error(f"virt-install stdout: {e.stdout.strip()}")
            logging.error(f"virt-install stderr: {e.stderr.strip()}")
            raise Exception(f"virt-install failed: {e.stderr.strip()}") from e
        except Exception as e:
            logging.error(f"An unexpected error occurred while running virt-install: {e}")
            raise

    def _detect_opensuse_version_from_iso(self, iso_url: str) -> Optional[OSVersion]:
        """
        Detect OpenSUSE version from ISO URL or filename.

        Args:
            iso_url: The ISO URL or path

        Returns:
            OSVersion object if detected, None otherwise

        Note: For Agama, we don't need specific version numbers for Leap.
        The product names are: openSUSE_Leap, openSUSE_Leap_Micro, Tumbleweed, Slowroll
        """
        iso_url_lower = iso_url.lower()

        # Detect Tumbleweed
        if "tumbleweed" in iso_url_lower:
            return OSVersion(
                os_type=OSType.OPENSUSE,
                version_id="tumbleweed",
                display_name="openSUSE Tumbleweed",
                architecture=self.host_arch,
                is_evaluation=False,
            )

        # Detect Slowroll
        if "slowroll" in iso_url_lower:
            return OSVersion(
                os_type=OSType.OPENSUSE,
                version_id="slowroll",
                display_name="openSUSE Slowroll",
                architecture=self.host_arch,
                is_evaluation=False,
            )

        # Detect Leap Micro (includes SLE-Micro which uses same Agama product)
        if ("leap" in iso_url_lower and "micro" in iso_url_lower) or "sle-micro" in iso_url_lower:
            return OSVersion(
                os_type=OSType.OPENSUSE,
                version_id="leap-micro",
                display_name="openSUSE Leap Micro",
                architecture=self.host_arch,
                is_evaluation=False,
            )

        # Detect Leap (any version - Agama uses same product name for all)
        if "leap" in iso_url_lower:
            # Extract version for display purposes only
            leap_match = re.search(r"leap[_-]?(\d+\.\d+)", iso_url_lower)
            if leap_match:
                version = leap_match.group(1)
                display_name = f"openSUSE Leap {version}"
            else:
                display_name = "openSUSE Leap"

            return OSVersion(
                os_type=OSType.OPENSUSE,
                version_id="leap",  # Generic "leap" - version number not needed for Agama
                display_name=display_name,
                architecture=self.host_arch,
                is_evaluation=False,
            )

        # Detect MicroOS (not Leap Micro)
        if "microos" in iso_url_lower:
            return OSVersion(
                os_type=OSType.OPENSUSE,
                version_id="microos",
                display_name="openSUSE MicroOS",
                architecture=self.host_arch,
                is_evaluation=False,
            )

        # Default to Tumbleweed if OpenSUSE but version unknown
        if "opensuse" in iso_url_lower or "suse" in iso_url_lower:
            self.logger.warning(
                f"Could not detect specific OpenSUSE version from ISO URL: {iso_url}, defaulting to Tumbleweed"
            )
            return OSVersion(
                os_type=OSType.OPENSUSE,
                version_id="tumbleweed",
                display_name="openSUSE Tumbleweed",
                architecture=self.host_arch,
                is_evaluation=False,
            )

        return None

    def provision_vm(
        self,
        vm_name: str,
        vm_type: VMType,
        iso_url: str,
        storage_pool_name: str,
        memory_mb: int = 4096,
        vcpu: int = 2,
        disk_size_gb: int = 8,
        disk_format: str | None = None,
        graphics_type: str = "spice",
        boot_uefi: bool | None = None,
        use_virt_install: bool = True,
        configure_before_install: bool = False,
        show_config_modal_callback: Optional[Callable[[libvirt.virDomain], None]] = None,
        progress_callback: Optional[Callable[[str, int], None]] = None,
        automation_config: Optional[Dict[str, Any]] = None,
        network_name: str = "default",
        ovmf_debug: bool = False,
    ) -> libvirt.virDomain:
        """
        Orchestrates the VM provisioning process.

        Args:
            vm_name: Name of the VM to create.
            vm_type: Type of the VM (e.g., Desktop, Server).
            iso_url: URL or path to the ISO for installation.
            storage_pool_name: Name of the storage pool where the disk will be created.
            memory_mb: RAM in megabytes.
            vcpu: Number of virtual CPUs.
            disk_size_gb: Disk size in gigabytes.
            disk_format: Disk format (e.g., qcow2).
            graphics_type: Graphics type (e.g., spice, vnc).
            boot_uefi: Whether to use UEFI boot.
            use_virt_install: If True, uses virt-install CLI tool.
            configure_before_install: If True, defines VM and shows details modal before starting.
            show_config_modal_callback: Optional callback to show configuration modal. Takes (domain) as parameters.
        """

        def report(stage, percent):
            if progress_callback:
                progress_callback(stage, percent)

        # Determine OS Type from iso_url or automation_config
        os_type = OSType.LINUX
        os_version = None
        if automation_config:
            template_name = automation_config.get("template_name", "").lower()
            if any(k in template_name for k in ["ubuntu", "autoinstall"]):
                os_type = OSType.UBUNTU
            elif any(k in template_name for k in ["debian", "preseed"]):
                os_type = OSType.DEBIAN
            elif any(k in template_name for k in ["fedora", "kickstart"]):
                os_type = OSType.FEDORA
            elif any(k in template_name for k in ["arch", "archinstall"]):
                os_type = OSType.ARCHLINUX
            elif any(k in template_name for k in ["alpine"]):
                os_type = OSType.ALPINE
            elif any(k in template_name for k in ["suse", "sles", "autoyast", "agama"]):
                os_type = OSType.OPENSUSE

        # If not determined by automation, try to guess from ISO URL
        if os_type == OSType.LINUX:
            iso_url_lower = iso_url.lower()
            if "ubuntu" in iso_url_lower:
                os_type = OSType.UBUNTU
            elif "debian" in iso_url_lower:
                os_type = OSType.DEBIAN
            elif "fedora" in iso_url_lower:
                os_type = OSType.FEDORA
            elif "archlinux" in iso_url_lower or "arch-linux" in iso_url_lower:
                os_type = OSType.ARCHLINUX
            elif "alpine" in iso_url_lower:
                os_type = OSType.ALPINE
            elif "opensuse" in iso_url_lower or "suse" in iso_url_lower:
                os_type = OSType.OPENSUSE

        # If boot_uefi is None, use provider preference
        if boot_uefi is None:
            provider = self.provider_registry.get_provider(os_type)
            boot_uefi = provider.preferred_boot_uefi if provider else True

        # Try to extract version for Alpine if possible
        if os_type == OSType.ALPINE:
            match = re.search(r"v(\d+\.\d+)", iso_url)
            if match:
                os_version = match.group(1)
            else:
                # Try to find it in the filename if not in path
                match = re.search(r"alpine-(?:virt|standard|extended)-(\d+\.\d+)", iso_url)
                if match:
                    os_version = match.group(1)

        # Determine if we should use direct kernel boot (manual UEFI for Arch/Debian/Alpine or automation)
        use_direct_kernel_boot = bool(automation_config) or (
            boot_uefi and os_type in [OSType.ARCHLINUX, OSType.DEBIAN, OSType.ALPINE]
        )

        boot_files = []
        report(StaticText.PROVISIONING_CHECKING_ENVIRONMENT, 0)

        # Prepare Storage Pool for Disk
        pool = self.conn.storagePoolLookupByName(storage_pool_name)
        if not pool.isActive():
            raise Exception(f"Storage pool {storage_pool_name} is not active.")

        pool_xml = ET.fromstring(pool.XMLDesc(0))
        pool_target_path = pool_xml.find("target/path").text

        # Determine storage format
        if disk_format:
            storage_format = disk_format
        else:
            storage_format = "raw" if vm_type == VMType.COMPUTATION else "qcow2"

        disk_name = f"{vm_name}.{storage_format}"
        disk_path = os.path.join(pool_target_path, disk_name)

        # Download ISO
        # Define local cache path for ISOs
        config = load_config()
        iso_cache_dir = Path(
            config.get("ISO_DOWNLOAD_PATH", str(Path.home() / ".cache" / AppInfo.name / "isos"))
        )
        iso_cache_dir.mkdir(parents=True, exist_ok=True)

        # Helper function to determine the final ISO path
        def _determine_iso_path(current_iso_url: str) -> str:
            # Check if iso_url already points to an existing libvirt storage volume
            existing_iso_volume_path = self._find_iso_volume_by_path(current_iso_url)
            if existing_iso_volume_path:
                report(
                    StaticText.PROVISIONING_USING_EXISTING_ISO_VOLUME.format(
                        path=existing_iso_volume_path
                    ),
                    55,
                )
                return existing_iso_volume_path
            else:  # original behavior, downloads/copies to cache and then uploads to storage pool
                iso_name = current_iso_url.split("/")[-1]
                is_local_source = (
                    current_iso_url.startswith("/")
                    or current_iso_url.startswith("file://")
                    or os.path.exists(current_iso_url)
                )

                if is_local_source:
                    if current_iso_url.startswith("file://"):
                        local_iso_path_for_upload = current_iso_url[7:]
                    else:
                        local_iso_path_for_upload = current_iso_url
                    report(StaticText.PROVISIONING_USING_LOCAL_ISO_IMAGE, 50)
                    return local_iso_path_for_upload
                else:
                    local_iso_path_for_upload = str(iso_cache_dir / iso_name)

                    def download_cb(message, percent):
                        report(
                            message,
                            10 + int(percent * 0.4),
                        )  # 10-50%

                    if not os.path.exists(local_iso_path_for_upload):
                        self.download_iso(current_iso_url, local_iso_path_for_upload, download_cb)
                    else:
                        report(StaticText.PROVISIONING_ISO_FOUND_IN_CACHE, 50)

                # Return the local cached path directly
                return local_iso_path_for_upload

        iso_path = _determine_iso_path(iso_url)

        # Check if we need to upload ISO to storage pool
        # This is required when:
        # 1. AutoYaST is enabled (requires --location which needs accessible path on server)
        # 2. ISO path is local (starts with / or file://)
        # 3. Connection is remote (not local qemu)
        uri = self.conn.getURI()
        is_remote = uri and not ("qemu:///system" in uri or "qemu:///session" in uri)
        ssh_host = get_ssh_host_from_uri(uri) if is_remote else None

        needs_iso_upload = False
        if automation_config:
            is_local_path = iso_path.startswith("/") or iso_path.startswith("file://")

            if is_remote and is_local_path:
                needs_iso_upload = True
                logging.info(
                    f"Auto Install with remote connection and local ISO - uploading to storage pool"
                )

        # Upload ISO if needed
        if needs_iso_upload:
            report(StaticText.UPLOADING_ISO, 55)

            def upload_progress(p):
                report(StaticText.UPLOADING_PROGRESS_TEMPLATE.format(progress=p), 55 + int(p * 0.2))

            iso_path = self.upload_iso(iso_path, storage_pool_name, upload_progress)
            logging.info(f"ISO uploaded to storage pool: {iso_path}")

        # Setup NVRAM if UEFI
        loader_path = None
        nvram_path = None

        is_virt_install_available = use_virt_install and self.check_virt_install()

        if boot_uefi and not is_virt_install_available:
            report(StaticText.PROVISIONING_SETTING_UP_UEFI_FIRMWARE, 75)
            # Only setup NVRAM if we are not using virt-install
            # virt-install --boot uefi will handle this automatically if we don't pass paths
            loader_path, nvram_path = self._setup_uefi_nvram(
                vm_name, storage_pool_name, vm_type, os_type=os_type
            )

        # Create Disk
        report(StaticText.PROVISIONING_CREATING_STORAGE, 78)  # Adjusted percentage

        preallocation = (
            "metadata"
            if vm_type in [VMType.SECURE, VMType.DESKTOP, VMType.WDESKTOP, VMType.WLDESKTOP]
            else "off"
        )
        lazy_refcounts = True if vm_type in [VMType.SECURE, VMType.COMPUTATION] else False
        cluster_size = (
            "1024k"
            if vm_type in [VMType.SECURE, VMType.DESKTOP, VMType.WDESKTOP, VMType.WLDESKTOP]
            else None
        )

        create_volume(
            pool,
            disk_name,
            disk_size_gb,
            vol_format=storage_format,
            preallocation=preallocation,
            lazy_refcounts=lazy_refcounts,
            cluster_size=cluster_size,
        )

        # Generate automation file and set up HTTP server if automation is enabled
        auto_url = None
        http_server = None
        port = None
        automation_file_path = None
        serial_console = False
        temp_dir = None

        if automation_config:
            serial_console = automation_config.get("serial_console", False)
            try:
                report(StaticText.PROVISIONING_GENERATING_AUTOMATION_CONFIG, 82)

                # Get the appropriate provider based on template type
                template_name = automation_config.get("template_name", "autoyast-basic.xml")
                is_ubuntu_template = any(
                    keyword in template_name.lower() for keyword in ["ubuntu", "autoinstall"]
                )
                is_debian_template = (
                    any(
                        keyword in template_name.lower()
                        for keyword in ["debian", "preseed", "cloud-init"]
                    )
                    and not is_ubuntu_template
                )
                is_fedora_template = any(
                    keyword in template_name.lower()
                    for keyword in ["fedora", "kickstart", "ks.cfg"]
                )
                is_arch_template = any(
                    keyword in template_name.lower() for keyword in ["arch", "archinstall"]
                )
                is_alpine_template = any(keyword in template_name.lower() for keyword in ["alpine"])

                # If Alpine BIOS, default to answers.txt template if generic one is used
                if is_alpine_template and not boot_uefi and template_name == "autoyast-basic.xml":
                    template_name = "alpine-answers-basic.txt"

                if is_ubuntu_template:
                    provider = self.get_provider("ubuntu")
                elif is_debian_template:
                    provider = self.get_provider("debian")
                elif is_fedora_template:
                    provider = self.get_provider("fedora")
                elif is_arch_template:
                    provider = self.get_provider("archlinux")
                elif is_alpine_template:
                    provider = self.get_provider("alpine")
                else:
                    provider = self.get_provider("opensuse")

                if provider:
                    # Create a temporary directory for automation file
                    temp_dir = Path(tempfile.mkdtemp(prefix=f"virtui_automation_{vm_name}_"))

                    # Detect version from ISO URL if it's an OpenSUSE provider
                    detected_version = None
                    if provider == self.get_provider("opensuse"):
                        detected_version = self._detect_opensuse_version_from_iso(iso_url)
                        if detected_version:
                            self.logger.info(
                                f"Detected OpenSUSE version from ISO: {detected_version.version_id}"
                            )
                        else:
                            self.logger.warning(
                                f"Could not detect OpenSUSE version from ISO URL: {iso_url}"
                            )

                    # Generate automation file using the provider
                    automation_file_path = provider.generate_automation_file(
                        version=detected_version,
                        vm_name=vm_name,
                        user_config=automation_config,
                        output_path=temp_dir,
                        template_name=template_name,
                    )
                    self.logger.info(f"Generated automation file: {automation_file_path}")

                    # Validate the generated automation file (XML, JSON, YAML, or CFG)
                    try:
                        file_path_str = str(automation_file_path)

                        # Special case: binary tarballs (Alpine apkovl)
                        if file_path_str.endswith(".tar.gz"):
                            if tarfile.is_tarfile(file_path_str):
                                self.logger.info("Automation tarball validation passed")
                            else:
                                raise ValueError("Invalid automation tarball file")
                            content = ""  # No text content to validate
                        else:
                            with open(automation_file_path, "r", encoding="utf-8") as f:
                                content = f.read()

                        if file_path_str.endswith(".json"):
                            json.loads(content)
                            self.logger.info("Automation JSON file validation passed")
                        elif (
                            file_path_str.endswith((".yaml", ".yml"))
                            or "user-data" in file_path_str
                        ):
                            # Ubuntu autoinstall uses YAML format
                            yaml.safe_load(content)
                            self.logger.info("Automation YAML file validation passed")
                        elif (
                            "ks-" in file_path_str
                            and file_path_str.endswith(".cfg")
                            or file_path_str.endswith(".ks")
                        ):
                            # Fedora Kickstart (no comprehensive validation, just check keywords)
                            if not any(kw in content for kw in ["%packages", "rootpw"]):
                                self.logger.warning(
                                    "Kickstart file might be missing required keywords"
                                )
                            self.logger.info("Automation Kickstart file basic validation passed")
                        elif file_path_str.endswith(".cfg"):
                            # Ubuntu preseed uses CFG format (no validation, just check non-empty)
                            if not content.strip():
                                raise ValueError("Preseed file is empty")
                            self.logger.info("Automation CFG file validation passed")
                        elif file_path_str.endswith(".xml"):
                            # OpenSUSE AutoYaST uses XML format
                            ET.fromstring(content)
                            self.logger.info("Automation XML file validation passed")
                        elif file_path_str.endswith(".txt") and "alpine" in file_path_str.lower():
                            # Alpine answers file
                            if "HOSTNAMEOPTS" not in content:
                                self.logger.warning(
                                    "Alpine answers file might be missing required keywords"
                                )
                            self.logger.info(
                                "Automation Alpine answers file basic validation passed"
                            )
                        else:
                            # Unknown format, log warning but don't fail
                            self.logger.warning(
                                f"Unknown automation file format: {automation_file_path}"
                            )
                            self.logger.info("Skipping validation for unknown file format")

                    except ET.ParseError as parse_error:
                        self.logger.error(f"Invalid XML in automation file: {parse_error}")
                        raise Exception(f"Invalid XML in automation file: {parse_error}")
                    except ValueError as json_error:
                        self.logger.error(f"Invalid JSON in automation file: {json_error}")
                        raise Exception(f"Invalid JSON in automation file: {json_error}")
                    except Exception as e:
                        self.logger.error(f"Error reading/validating automation file: {e}")
                        raise Exception(f"Error reading/validating automation file: {e}")

                    # Start HTTP server to serve the automation file(s)
                    try:
                        # Check if this is Ubuntu autoinstall (has user-data and meta-data files)
                        user_data_path = temp_dir / "user-data"
                        meta_data_path = temp_dir / "meta-data"
                        is_ubuntu_autoinstall = user_data_path.exists() and meta_data_path.exists()

                        # Check if this is Ubuntu preseed (has preseed.cfg file)
                        preseed_path = temp_dir / "preseed.cfg"
                        is_ubuntu_preseed = preseed_path.exists()

                        # Check if this is Fedora kickstart (has ks.cfg file)
                        ks_path = temp_dir / "ks.cfg"
                        is_fedora_ks = ks_path.exists()

                        # Check if this is Arch Linux (has archinstall.json file)
                        arch_path = temp_dir / "archinstall.json"
                        is_arch_json = arch_path.exists()

                        # Check if this is Alpine Linux (has answers.txt or localhost.apkovl.tar.gz file)
                        alpine_path = temp_dir / "answers.txt"
                        alpine_apkovl_path = temp_dir / "localhost.apkovl.tar.gz"
                        is_alpine_answers = (
                            alpine_path.exists()
                            or alpine_apkovl_path.exists()
                            or (
                                automation_file_path
                                and (
                                    "alpine" in str(automation_file_path).lower()
                                    or "answers" in str(automation_file_path).lower()
                                )
                            )
                        )

                        is_agama = False
                        if is_ubuntu_autoinstall:
                            # Ubuntu autoinstall: serve directory containing user-data and meta-data
                            self.logger.info(
                                "Detected Ubuntu autoinstall files (user-data + meta-data)"
                            )
                            autoinst_filename = ""  # Point to directory root for cloud-init
                        elif is_ubuntu_preseed:
                            # Ubuntu preseed: serve the preseed.cfg file
                            self.logger.info("Detected Ubuntu preseed file (preseed.cfg)")
                            unique_id = uuid.uuid4().hex[:8]
                            autoinst_filename = f"preseed-{unique_id}.cfg"
                            autoinst_path = temp_dir / autoinst_filename
                            shutil.copy(preseed_path, autoinst_path)
                        elif is_fedora_ks:
                            # Fedora kickstart: serve the ks.cfg file
                            self.logger.info("Detected Fedora kickstart file (ks.cfg)")
                            unique_id = uuid.uuid4().hex[:8]
                            autoinst_filename = f"ks-{unique_id}.cfg"
                            autoinst_path = temp_dir / autoinst_filename
                            shutil.copy(ks_path, autoinst_path)
                        elif is_arch_json:
                            # Arch Linux: serve archinstall.json creds.json files and a setup.sh script
                            self.logger.info(
                                "Detected Arch Linux archinstall file (archinstall.json, creds.json)"
                            )
                            unique_id = uuid.uuid4().hex[:8]
                            autoinst_filename = f"archinstall-{unique_id}.json"
                            creds_filename = f"creds-{unique_id}.json"
                            autoinst_path = temp_dir / autoinst_filename
                            creds_path = temp_dir / creds_filename
                            shutil.copy(arch_path, autoinst_path)
                            shutil.copy(arch_path, creds_path)

                            # Prepare setup.sh script filename (content will be created after HTTP server starts)
                            setup_script_filename = f"archinstall-setup-{unique_id}.sh"
                        elif alpine_apkovl_path.exists():
                            # Alpine Linux: serve the apkovl tarball (preferred for trigger script)
                            self.logger.info(
                                "Detected Alpine Linux apkovl file (localhost.apkovl.tar.gz)"
                            )
                            unique_id = uuid.uuid4().hex[:8]
                            autoinst_filename = f"alpine-{unique_id}.apkovl.tar.gz"
                            autoinst_path = temp_dir / autoinst_filename
                            shutil.copy(alpine_apkovl_path, autoinst_path)
                        elif is_alpine_answers:
                            # Alpine Linux: serve the answers.txt file
                            self.logger.info("Detected Alpine Linux answers file (answers.txt)")
                            unique_id = uuid.uuid4().hex[:8]
                            autoinst_filename = f"alpine-answers-{unique_id}.txt"
                            autoinst_path = temp_dir / autoinst_filename
                            shutil.copy(alpine_path, autoinst_path)
                        else:
                            # OpenSUSE/Agama: rename the single file with unique name
                            unique_id = uuid.uuid4().hex[:8]
                            is_agama = str(automation_file_path).endswith(".json")
                            ext = ".json" if is_agama else ".xml"
                            autoinst_filename = f"autoinst-{unique_id}{ext}"
                            autoinst_path = temp_dir / autoinst_filename
                            shutil.copy(automation_file_path, autoinst_path)

                        # Try to start HTTP server on configured port, fallback to random
                        auto_install_port = config.get("AUTO_INSTALL_PORT", 8000)
                        http_server = AutoHTTPServer(temp_dir, port=auto_install_port)
                        port = http_server.start()
                        # Open firewalld port if running
                        self.logger.info(f"Opening firewalld port {port} for auto install.")
                        manage_firewalld_port(port, remote_host=ssh_host)
                    except OSError as e:
                        if e.errno == 98:  # Address already in use
                            self.logger.warning(
                                f"Configured Auto Install port {auto_install_port} is in use. "
                                "Falling back to a random port."
                            )
                            http_server = AutoHTTPServer(temp_dir, port=0)
                            port = http_server.start()
                            # Open firewalld port if running
                            self.logger.info(f"Opening firewalld port {port} for auto install.")
                            manage_firewalld_port(port, remote_host=ssh_host)
                        else:
                            raise

                    except Exception as e:
                        self.logger.error(f"Failed to start HTTP server for Auto Install: {e}")
                        if http_server:
                            http_server.stop()
                        # Cleanup temp directory on error
                        if temp_dir and os.path.exists(str(temp_dir)):
                            try:
                                shutil.rmtree(str(temp_dir), ignore_errors=True)
                                self.logger.info(
                                    f"Cleaned up automation temp directory after error: {temp_dir}"
                                )
                            except Exception as cleanup_error:
                                self.logger.warning(
                                    f"Failed to cleanup temp directory: {cleanup_error}"
                                )
                        # Continue without automation
                        auto_url = None
                        temp_dir = None

                    # Get host IP and build Auto Install URL
                    host_ip = self._get_host_ip_for_vms()
                    if is_ubuntu_autoinstall:
                        # For Ubuntu autoinstall, point to directory root (ends with /)
                        auto_url = f"http://{host_ip}:{port}/"
                        self.logger.info(f"Ubuntu autoinstall files available at: {auto_url}")
                        self.logger.info(f"  user-data: {auto_url}user-data")
                        self.logger.info(f"  meta-data: {auto_url}meta-data")
                    elif is_ubuntu_preseed:
                        # For Ubuntu preseed, point to the .cfg file
                        auto_url = f"http://{host_ip}:{port}/{autoinst_filename}"
                        self.logger.info(f"Ubuntu preseed file available at: {auto_url}")
                    elif is_fedora_ks:
                        # For Fedora kickstart, point to the .cfg file
                        auto_url = f"http://{host_ip}:{port}/{autoinst_filename}"
                        self.logger.info(f"Fedora kickstart file available at: {auto_url}")
                    elif is_arch_json:
                        # For Arch Linux archinstall, create setup.sh script and point to it
                        json_url = f"http://{host_ip}:{port}/{autoinst_filename}"
                        creds_url = f"http://{host_ip}:{port}/{creds_filename}"
                        setup_script_url = f"http://{host_ip}:{port}/{setup_script_filename}"

                        # Get the Arch Linux provider to generate the setup script
                        arch_provider = self.provider_registry.get_provider(OSType.ARCHLINUX)
                        setup_script_content = arch_provider.generate_setup_script(json_url, creds_url)

                        setup_script_path = temp_dir / setup_script_filename
                        with open(setup_script_path, "w", encoding="utf-8") as f:
                            f.write(setup_script_content)
                        os.chmod(setup_script_path, 0o755)
                        auto_url = setup_script_url
                        self.logger.info(f"Arch Linux archinstall setup script available at: {setup_script_url}")
                        self.logger.info(f"Arch Linux archinstall JSON file available at: {json_url}")
                        self.logger.info(f"Arch Linux archinstall creds JSON file available at: {creds_url}")
                    elif is_alpine_answers:
                        # For Alpine Linux, point to the .txt or .apkovl file
                        auto_url = f"http://{host_ip}:{port}/{autoinst_filename}"
                        self.logger.info(f"Alpine Linux automation file available at: {auto_url}")
                    else:
                        # For OpenSUSE/Agama, point to specific file
                        auto_url = f"http://{host_ip}:{port}/{autoinst_filename}"
                        if is_agama:
                            self.logger.info(f"Agama automation URL set: {auto_url}")
                            self.logger.info(
                                f"  This URL will be passed to kernel cmdline as: inst.auto={auto_url}"
                            )
                        else:
                            self.logger.info(f"AutoYaST file available at: {auto_url}")

                else:
                    self.logger.warning("OpenSUSE provider not available for automation")

            except Exception as e:
                self.logger.error(f"Failed to generate automation file: {e}")
                # Cleanup temp directory on error
                if temp_dir and os.path.exists(str(temp_dir)):
                    try:
                        shutil.rmtree(str(temp_dir), ignore_errors=True)
                        self.logger.info(
                            f"Cleaned up automation temp directory after error: {temp_dir}"
                        )
                    except Exception as cleanup_error:
                        self.logger.warning(f"Failed to cleanup temp directory: {cleanup_error}")
                # Continue without automation rather than failing the entire process
                automation_file_path = None
                auto_url = None
                temp_dir = None

        # Handle Configure Before Install feature
        if configure_before_install:
            # Handle manual kernel extraction for Arch/Debian UEFI
            kernel_path, initrd_path = None, None
            if use_direct_kernel_boot:
                try:
                    local_iso_path = _determine_iso_path(iso_url)
                    if os_type == OSType.ARCHLINUX:
                        local_kernel_path, local_initrd_path = self._extract_arch_iso_kernel_initrd(
                            local_iso_path
                        )
                    elif os_type == OSType.DEBIAN:
                        local_kernel_path, local_initrd_path = (
                            self._extract_debian_iso_kernel_initrd(local_iso_path)
                        )
                    elif os_type == OSType.UBUNTU:
                        local_kernel_path, local_initrd_path = (
                            self._extract_ubuntu_iso_kernel_initrd(local_iso_path)
                        )
                    elif os_type == OSType.FEDORA:
                        local_kernel_path, local_initrd_path = (
                            self._extract_fedora_iso_kernel_initrd(local_iso_path)
                        )
                    elif os_type == OSType.ALPINE:
                        local_kernel_path, local_initrd_path = (
                            self._extract_alpine_iso_kernel_initrd(local_iso_path)
                        )
                    else:
                        local_kernel_path, local_initrd_path = self._extract_iso_kernel_initrd(
                            local_iso_path, self.host_arch
                        )

                    report("Uploading kernel and initrd", 81)
                    kernel_path = self.upload_file(
                        local_kernel_path, storage_pool_name, f"{vm_name}-kernel"
                    )
                    initrd_path = self.upload_file(
                        local_initrd_path, storage_pool_name, f"{vm_name}-initrd"
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to extract/upload kernel/initrd for direct boot: {e}"
                    )

            # Generate the XML configuration that would be used
            if is_virt_install_available:
                settings = self._get_vm_settings(
                    vm_type, boot_uefi, disk_format, os_type=os_type, graphics_type=graphics_type,
                    is_auto_install=bool(auto_url)
                )
                xml_desc = self._run_virt_install(
                    vm_name,
                    settings,
                    disk_path,
                    iso_path,
                    storage_pool_name,
                    memory_mb,
                    vcpu,
                    loader_path,
                    nvram_path,
                    print_xml=True,
                    auto_url=auto_url,
                    is_remote_connection=is_remote,
                    serial_console=serial_console,
                    os_type=os_type,
                    kernel_path=kernel_path,
                    initrd_path=initrd_path,
                    os_version=os_version,
                    network_name=network_name,
                )
            else:
                xml_desc = self.generate_xml(
                    vm_name,
                    vm_type,
                    disk_path,
                    iso_path,
                    memory_mb,
                    vcpu,
                    disk_format,
                    loader_path=loader_path,
                    nvram_path=nvram_path,
                    boot_uefi=boot_uefi,
                    automation_file_path=str(automation_file_path)
                    if automation_file_path
                    else None,
                    auto_url=auto_url,
                    kernel_path=kernel_path,
                    initrd_path=initrd_path,
                    serial_console=serial_console,
                    os_type=os_type,
                    graphics_type=graphics_type,
                    os_version=os_version,
                    network_name=network_name,
                    ovmf_debug=ovmf_debug,
                )

            # Define the VM
            report(StaticText.PROVISIONING_DEFINING_VM, 85)
            dom = self.conn.defineXML(xml_desc)

            # Show the configuration in a modal if callback is provided
            if show_config_modal_callback:
                show_config_modal_callback(dom)
            else:
                # Fallback: just log the configuration
                logging.info(f"VM configuration defined for {vm_name}")

            # Clean up HTTP server if it was started
            if http_server:
                http_server.stop()
                self.logger.info("HTTP server stopped")
                if port:
                    self.logger.info(f"Removing firewalld port {port} for auto install.")
                    manage_firewalld_port(port, action="remove", remote_host=ssh_host)

            report(StaticText.PROVISIONING_COMPLETE_CONFIG_MODE, 100)
            return dom

        # Continue with normal VM creation
        if is_virt_install_available:
            report(StaticText.PROVISIONING_CONFIGURING_VM_VIRT_INSTALL, 80)

            # Handle manual kernel extraction for Arch/Debian UEFI even with virt-install
            kernel_path, initrd_path = None, None
            if use_direct_kernel_boot:
                try:
                    local_iso_path = _determine_iso_path(iso_url)
                    if os_type == OSType.ARCHLINUX:
                        local_kernel_path, local_initrd_path = self._extract_arch_iso_kernel_initrd(
                            local_iso_path
                        )
                    elif os_type == OSType.DEBIAN:
                        local_kernel_path, local_initrd_path = (
                            self._extract_debian_iso_kernel_initrd(local_iso_path)
                        )
                    elif os_type == OSType.UBUNTU:
                        local_kernel_path, local_initrd_path = (
                            self._extract_ubuntu_iso_kernel_initrd(local_iso_path)
                        )
                    elif os_type == OSType.FEDORA:
                        local_kernel_path, local_initrd_path = (
                            self._extract_fedora_iso_kernel_initrd(local_iso_path)
                        )
                    elif os_type == OSType.ALPINE:
                        local_kernel_path, local_initrd_path = (
                            self._extract_alpine_iso_kernel_initrd(local_iso_path)
                        )
                    else:
                        local_kernel_path, local_initrd_path = self._extract_iso_kernel_initrd(
                            local_iso_path, self.host_arch
                        )

                    report("Uploading kernel and initrd", 81)
                    kernel_path = self.upload_file(
                        local_kernel_path, storage_pool_name, f"{vm_name}-kernel"
                    )
                    initrd_path = self.upload_file(
                        local_initrd_path, storage_pool_name, f"{vm_name}-initrd"
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to extract/upload kernel/initrd for direct boot: {e}"
                    )

            settings = self._get_vm_settings(
                vm_type, boot_uefi, disk_format, os_type=os_type, graphics_type=graphics_type,
                is_auto_install=bool(auto_url)
            )
            self._run_virt_install(
                vm_name,
                settings,
                disk_path,
                iso_path,
                storage_pool_name,
                memory_mb,
                vcpu,
                loader_path,
                nvram_path,
                auto_url=auto_url,
                is_remote_connection=is_remote,
                serial_console=serial_console,
                os_type=os_type,
                kernel_path=kernel_path,
                initrd_path=initrd_path,
                os_version=os_version,
                network_name=network_name,
            )

            report(StaticText.PROVISIONING_WAITING_FOR_VM, 95)
            # Fetch the domain object
            dom = self.conn.lookupByName(vm_name)
        else:
            # For XML-based provisioning, we need to handle asset uploads to ensure permissions
            kernel_path, initrd_path = None, None
            final_automation_path = None  # This will be the path on the storage pool

            if use_direct_kernel_boot:
                try:
                    # Preferred method: kernel extraction for cmdline boot
                    local_iso_path = _determine_iso_path(iso_url)

                    if os_type == OSType.UBUNTU:
                        # Use Ubuntu-specific kernel extraction (casper/ directory)
                        local_kernel_path, local_initrd_path = (
                            self._extract_ubuntu_iso_kernel_initrd(local_iso_path)
                        )
                    elif os_type == OSType.DEBIAN:
                        # Use Debian-specific kernel extraction (install.amd/ directory)
                        local_kernel_path, local_initrd_path = (
                            self._extract_debian_iso_kernel_initrd(local_iso_path)
                        )
                    elif os_type == OSType.FEDORA:
                        # Use Fedora-specific kernel extraction (images/pxeboot/ directory)
                        local_kernel_path, local_initrd_path = (
                            self._extract_fedora_iso_kernel_initrd(local_iso_path)
                        )
                    elif os_type == OSType.ARCHLINUX:
                        # Use Arch Linux-specific kernel extraction (arch/boot/x86_64/ directory)
                        local_kernel_path, local_initrd_path = self._extract_arch_iso_kernel_initrd(
                            local_iso_path
                        )
                    elif os_type == OSType.ALPINE:
                        # Use Alpine Linux-specific kernel extraction (boot/ directory)
                        local_kernel_path, local_initrd_path = (
                            self._extract_alpine_iso_kernel_initrd(local_iso_path)
                        )
                    else:
                        # Use OpenSUSE/SLES kernel extraction (boot/{arch}/loader/)
                        local_kernel_path, local_initrd_path = self._extract_iso_kernel_initrd(
                            local_iso_path, self.host_arch
                        )

                    report("Uploading kernel and initrd", 81)
                    kernel_path = self.upload_file(
                        local_kernel_path, storage_pool_name, f"{vm_name}-kernel"
                    )
                    initrd_path = self.upload_file(
                        local_initrd_path, storage_pool_name, f"{vm_name}-initrd"
                    )
                    self.logger.info(f"Kernel/initrd uploaded to storage pool for direct boot.")

                except Exception as e:
                    self.logger.error(
                        f"Failed to use kernel extraction for direct boot: {e}. "
                        "Falling back to other methods if possible."
                    )
                    kernel_path, initrd_path = None, None  # Ensure fallback logic triggers

            # Fallback to floppy method if kernel extraction fails
            # OpenSUSE and Alpine use floppy for automation delivery
            if not kernel_path and automation_file_path:
                # Check if this is an OpenSUSE or Alpine (not Debian, Ubuntu, or Arch)
                is_floppy_capable = os_type in [OSType.OPENSUSE, OSType.ALPINE]
                is_other_distro = os_type not in [
                    OSType.DEBIAN,
                    OSType.UBUNTU,
                    OSType.ARCHLINUX,
                    OSType.ALPINE,
                    OSType.FEDORA,
                ]
                # Any distro that is not explicitly excluded OR is known floppy-capable
                should_use_floppy = is_other_distro or is_floppy_capable

                if should_use_floppy:
                    # Create floppy for OpenSUSE/AutoYaST or Alpine/answers
                    try:
                        # Determine internal filename for floppy
                        internal_filename = "autoinst.xml"
                        if os_type == OSType.ALPINE:
                            # Alpine looks for <hostname>.apkovl.tar.gz
                            # Default hostname is localhost
                            internal_filename = "localhost.apkovl.tar.gz"

                        floppy_image_path = self._create_automation_floppy_image(
                            automation_file_path,
                            Path(os.path.dirname(automation_file_path)),
                            internal_filename=internal_filename,
                        )
                        report("Uploading automation floppy image", 82)
                        automation_vol_name = f"{vm_name}-automation.img"
                        final_automation_path = self.upload_file(
                            floppy_image_path, storage_pool_name, volume_name=automation_vol_name
                        )
                    except Exception as e:
                        self.logger.error(
                            f"Could not create or upload floppy image for Auto Install: {e}"
                        )
                        # Re-raise the exception to stop provisioning a broken VM
                        raise e
                else:
                    # For Debian/Ubuntu/Arch/Alpine without kernel extraction, kernel boot is required
                    self.logger.error(
                        f"{os_type.name} direct boot requires kernel extraction. "
                        f"Floppy-based installation is not supported for {os_type.name}."
                    )
                    raise Exception(
                        f"Failed to extract kernel/initrd for {os_type.name} installation. "
                        "Please ensure 7z (p7zip-full) is installed."
                    )

            # Generate XML
            report(StaticText.PROVISIONING_CONFIGURING_VM_XML, 85)
            xml_desc = self.generate_xml(
                vm_name,
                vm_type,
                disk_path,
                iso_path,
                memory_mb,
                vcpu,
                disk_format,
                loader_path=loader_path,
                nvram_path=nvram_path,
                boot_uefi=boot_uefi,
                automation_file_path=final_automation_path,
                auto_url=auto_url,
                kernel_path=kernel_path,
                initrd_path=initrd_path,
                serial_console=serial_console,
                os_type=os_type,
                graphics_type=graphics_type,
                os_version=os_version,
                network_name=network_name,
                ovmf_debug=ovmf_debug,
                )

            report(StaticText.PROVISIONING_CONFIGURING_VM_XML, 90)
            dom = self.conn.defineXML(xml_desc)
            dom.create()

            # After starting the VM for installation, immediately strip the persistent
            # configuration of installation assets (kernel/initrd/cmdline/floppy)
            # so that it's ready for the first boot from disk after installation completes.
            if kernel_path or final_automation_path:
                self.logger.info(
                    f"Cleaning installation assets from persistent configuration for {vm_name}."
                )
                try:
                    # Capture boot files before stripping them from XML
                    root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
                    boot_files = get_vm_boot_files(root)
                    strip_installation_assets(dom)
                except Exception as e:
                    self.logger.warning(f"Failed to strip installation assets: {e}")

        # Clean up HTTP server after VM creation (keep it running during installation)
        # Note: The HTTP server will continue serving during the OS installation
        # and will be stopped when the VMProvisioner object is garbage collected
        # or when explicitly stopped by the caller
        if http_server:
            self.logger.info(f"HTTP server still running at {auto_url} for installation")

        # Auto-restart watcher for AutoYaST (Stage 1 -> Stage 2)
        # Since we set on_reboot='destroy', the VM stops after Stage 1.
        # This thread waits for it to stop and then restarts it to finish installation.
        if automation_config:

            def restart_watcher(
                domain,
                name,
                logger,
                boot_files_to_delete,
                http_server_to_stop,
                temp_dir_to_clean,
                port_to_close=None,
                ssh_host_to_manage=None,
            ):
                logger.info(f"Starting restart watcher for {name}")
                # 1. Wait for it to start running (it might be in 'defining' state)
                timeout = 120
                while timeout > 0:
                    try:
                        if domain.isActive():
                            break
                    except:
                        pass
                    time.sleep(1)
                    timeout -= 1

                # 2. Now wait for it to stop (end of stage 1)
                while True:
                    try:
                        state, reason = domain.state()
                        if state == libvirt.VIR_DOMAIN_SHUTOFF:
                            if reason == libvirt.VIR_DOMAIN_SHUTOFF_DESTROYED:
                                logger.info(
                                    f"VM {name} was manually stopped by user. Disabling auto-restart."
                                )
                            else:
                                logger.info(
                                    f"VM {name} stopped (Stage 1 complete, reason={reason}). Cleaning up and restarting..."
                                )

                                # Strip installation assets from persistent config and update on_reboot
                                try:
                                    logger.info(
                                        f"Stripping installation assets from {name} persistent config"
                                    )
                                    strip_installation_assets(domain)
                                except Exception as e:
                                    logger.warning(
                                        f"Failed to strip installation assets from {name}: {e}"
                                    )

                                time.sleep(3)
                                try:
                                    domain.create()
                                    logger.info(f"VM {name} restarted for Stage 2.")
                                except Exception as e:
                                    logger.error(f"Failed to restart VM {name} for Stage 2: {e}")

                            # Installation assets cleanup (always cleanup when Stage 1 ends or user stops)
                            if boot_files_to_delete:
                                logger.info(
                                    f"Deleting boot files for {name}: {boot_files_to_delete}"
                                )
                                delete_boot_files(domain.connect(), boot_files_to_delete)

                            if http_server_to_stop:
                                logger.info(f"Stopping HTTP server for {name}")
                                http_server_to_stop.stop()
                                if port_to_close:
                                    logger.info(
                                        f"Removing firewalld port {port_to_close} for auto install."
                                    )
                                    manage_firewalld_port(
                                        port_to_close,
                                        action="remove",
                                        remote_host=ssh_host_to_manage,
                                    )

                            if temp_dir_to_clean and os.path.exists(temp_dir_to_clean):
                                logger.info(f"Cleaning up temporary directory: {temp_dir_to_clean}")
                                shutil.rmtree(temp_dir_to_clean, ignore_errors=True)

                            break
                    except Exception as e:
                        logger.error(f"Error in restart watcher for {name}: {e}")
                        break
                    time.sleep(5)

            threading.Thread(
                target=restart_watcher,
                args=(
                    dom,
                    vm_name,
                    self.logger,
                    boot_files,
                    http_server,
                    temp_dir,
                    port if http_server else None,
                    ssh_host,
                ),
                daemon=True,
            ).start()

        # Cleanup any leftover temporary directories that weren't automat setup
        # (extract directories are not tracked by the restart_watcher)
        self._cleanup_extract_temp_dirs()

        report(StaticText.PROVISIONING_COMPLETE, 100)
        return dom

    @staticmethod
    def _cleanup_extract_temp_dirs():
        """
        Clean up temporary directories created during ISO extraction.
        These are created by _extract_*_iso_kernel_initrd methods and should be
        cleaned up after the kernel/initrd files have been uploaded to storage.
        """
        import glob

        temp_patterns = [
            "/tmp/virtui_iso_extract_*",
            "/tmp/virtui_debian_iso_extract_*",
            "/tmp/virtui_ubuntu_iso_extract_*",
            "/tmp/virtui_fedora_iso_extract_*",
            "/tmp/virtui_arch_iso_extract_*",
            "/tmp/virtui_alpine_iso_extract_*",
        ]

        for pattern in temp_patterns:
            for temp_dir in glob.glob(pattern):
                try:
                    if os.path.isdir(temp_dir):
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        logging.info(f"Cleaned up extraction temp directory: {temp_dir}")
                except Exception as e:
                    logging.warning(f"Failed to cleanup temp directory {temp_dir}: {e}")
