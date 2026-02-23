"""
Library for VM creation and provisioning, specifically focused on OpenSUSE.
"""

import hashlib
import logging
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import libvirt

from .config import load_config
from .constants import AppInfo, StaticText
from .firmware_manager import get_uefi_files, select_best_firmware
from .libvirt_utils import get_host_architecture
from .provisioning.provider_registry import ProviderRegistry
from .provisioning.os_provider import OSType
from .provisioning.providers.opensuse_provider import OpenSUSEProvider, OpenSUSEDistro
from .storage_manager import create_volume


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

        # Initialize provider registry and register OS providers
        self.provider_registry = ProviderRegistry()
        self._register_providers()

    def _register_providers(self):
        """Register available OS providers."""
        try:
            opensuse_provider = OpenSUSEProvider()
            self.provider_registry.register_provider(opensuse_provider)
        except Exception as e:
            self.logger.warning(f"Failed to register OpenSUSE provider: {e}")

    def get_provider(self, os_type_str: str):
        """Get provider by OS type string."""
        # Convert string to OSType enum
        # Note: Both openSUSE and SLES use the same provider (AutoYaST)
        os_type_map = {
            "linux": OSType.LINUX,
            "opensuse": OSType.LINUX,
            "sles": OSType.LINUX,  # SLES uses same AutoYaST provider as openSUSE
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
            distro: Either an OpenSUSEDistro enum or a custom repository URL string

        Returns:
            List of ISO dictionaries with 'name', 'url', and 'date' keys
        """
        # Handle OpenSUSE distributions - delegate to provider
        if isinstance(distro, OpenSUSEDistro):
            provider = self.get_provider("linux")
            if provider and hasattr(provider, "get_iso_list"):
                return provider.get_iso_list(distro)
            else:
                logging.warning("OpenSUSE provider not available or doesn't support get_iso_list")
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

    def download_iso(
        self, url: str, dest_path: str, progress_callback: Optional[Callable[[int], None]] = None
    ):
        """
        Downloads the ISO from the given URL to the destination path.
        """
        if os.path.exists(dest_path):
            logging.info(f"ISO already exists at {dest_path}, skipping download.")
            if progress_callback:
                progress_callback(100)
            return

        logging.info(f"Downloading ISO from {url} to {dest_path}")

        # Create unverified context to avoid SSL errors with some mirrors if certs are missing
        context = ssl._create_unverified_context()

        try:
            with urllib.request.urlopen(url, context=context) as response, open(
                dest_path, "wb"
            ) as out_file:
                total_size = int(response.getheader("Content-Length").strip())
                downloaded_size = 0
                chunk_size = 1024 * 1024  # 1MB chunks

                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded_size += len(chunk)

                    if progress_callback and total_size > 0:
                        percent = int((downloaded_size / total_size) * 100)
                        progress_callback(percent)

        except Exception as e:
            logging.error(f"Failed to download ISO: {e}")
            if os.path.exists(dest_path):
                os.remove(dest_path)  # Clean up partial file
            raise e

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
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        file_size = os.path.getsize(local_path)
        iso_name = os.path.basename(local_path)

        pool = self.conn.storagePoolLookupByName(storage_pool_name)
        if not pool.isActive():
            raise Exception(f"Storage pool {storage_pool_name} is not active.")

        # Check if volume already exists
        try:
            vol = pool.storageVolLookupByName(iso_name)
            logging.info(
                f"Volume '{iso_name}' already exists in pool '{storage_pool_name}'. Skipping upload."
            )
            if progress_callback:
                progress_callback(100)
            return vol.path()
        except libvirt.libvirtError:
            pass  # Volume does not exist, proceed to create

        # Create volume
        vol_xml = f"""
        <volume>
            <name>{iso_name}</name>
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
            logging.info("Set libvirt keepalive to 10s for ISO upload.")
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
        self, vm_name: str, target_pool_name: str, vm_type: VMType, support_snapshots: bool = True
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

        Returns: (loader_path, nvram_path)
        """
        all_firmwares = get_uefi_files(self.conn)

        # Determine requirements based on vm_type
        secure_boot = vm_type == VMType.SECURE

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
                    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp_in:
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
        self, vm_type: VMType, boot_uefi: bool, disk_format: str | None = None
    ) -> Dict[str, Any]:
        """
        Returns a dictionary of VM settings based on type and options.
        """
        settings = {
            # Storage
            "disk_bus": "virtio",
            "disk_format": "qcow2",
            "disk_cache": "none",
            # Guest
            "machine": "pc-q35-10.1" if boot_uefi else "pc-i440fx-10.1",
            "video": "virtio",
            "network_model": "e1000",
            "suspend_to_mem": "off",
            "suspend_to_disk": "off",
            "boot_uefi": boot_uefi,
            "iothreads": 0,
            "input_bus": "virtio",
            "sound_model": "none",
            # Features
            "sev": False,
            "tpm": False,
            "mem_backing": False,
            "watchdog": False,
            "on_poweroff": "destroy",
            "on_reboot": "restart",
            "on_crash": "destroy",
        }
        if vm_type == VMType.SECURE:
            settings.update(
                {
                    "disk_cache": "writethrough",
                    "disk_format": "qcow2",
                    "video": "qxl",
                    "tpm": True,
                    "sev": True,
                    "input_bus": "ps2",
                    "mem_backing": False,  # Explicitly off in table
                    "on_poweroff": "destroy",
                    "on_reboot": "destroy",
                    "on_crash": "destroy",
                }
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
                settings["machine"] = "pc-i440fx-10.1"
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
        boot_uefi: bool = True,
        automation_file_path: str | None = None,
    ) -> str:
        """
        Generates the Libvirt XML for the VM based on the type and default settings.
        """
        settings = self._get_vm_settings(vm_type, boot_uefi, disk_format)

        # --- XML Construction ---
        # UUID generation handled by libvirt if omitted
        xml = f"""
<domain type='kvm'>
  <name>{vm_name}</name>
  <memory unit='KiB'>{memory_mb * 1024}</memory>
  <currentMemory unit='KiB'>{memory_mb * 1024}</currentMemory>
  <vcpu placement='static'>{vcpu}</vcpu>
"""
        if settings["boot_uefi"]:
            if loader_path and nvram_path:
                xml += f"""
  <os>
    <type arch='x86_64' machine='{settings["machine"]}'>hvm</type>
    <loader readonly='yes' type='pflash'>{loader_path}</loader>
    <nvram format='qcow2'>{nvram_path}</nvram>
"""
            else:
                xml += f"""
  <os firmware='efi'>
    <type arch='x86_64' machine='{settings["machine"]}'>hvm</type>
    <loader readonly='yes' type='pflash'/>
"""
                if nvram_path:
                    xml += f"    <nvram format='qcow2'>{nvram_path}</nvram>\n"
                else:
                    xml += "    <nvram format='qcow2'/>\n"
        else:
            xml += f"""
  <os>
    <type arch='x86_64' machine='{settings["machine"]}'>hvm</type>
"""
        xml += """
    <boot dev='hd'/>
    <boot dev='cdrom'/>
  </os>
  
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
    </disk>
"""

        # CDROM (ISO)
        xml += f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{iso_path}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
"""

        # Floppy disk for AutoYaST automation file
        if automation_file_path:
            xml += f"""
    <disk type='file' device='floppy'>
      <driver name='qemu' type='raw'/>
      <source file='{automation_file_path}'/>
      <target dev='fda' bus='fdc'/>
      <readonly/>
    </disk>
"""

        # Interface
        xml += f"""
    <interface type='network'>
      <source network='default'/>
      <model type='{settings["network_model"]}'/>
    </interface>
"""

        # Video
        xml += f"""
    <video>
      <model type='{settings["video"]}'/>
    </video>
    <graphics type='vnc' port='-1' autoport='yes' listen='0.0.0.0'>
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

        # QEMU Guest Agent
        xml += """
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
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
        xml += "</domain>"
        return xml

    def _create_autoyast_iso(
        self, automation_file_path: str, output_dir: Path, storage_pool_name: str, vm_name: str
    ) -> str:
        """
        Create an ISO image containing the AutoYaST file and upload to storage pool.
        ISO images are more reliably detected by installers than floppy disks.

        Args:
            automation_file_path: Path to the autoyast.xml file
            output_dir: Directory to create the ISO image in (local temp)
            storage_pool_name: Name of the storage pool to upload to
            vm_name: Name of the VM (used to create unique ISO image)

        Returns:
            Path to the ISO image in the storage pool
        """
        iso_filename = f"autoyast_{vm_name}.iso"
        local_iso_path = output_dir / iso_filename

        try:
            # Create a temporary directory for ISO content
            iso_content_dir = output_dir / f"autoyast_iso_content_{vm_name}"
            iso_content_dir.mkdir(exist_ok=True)

            # Copy the autoyast.xml file with both names for compatibility
            import shutil

            shutil.copy(automation_file_path, iso_content_dir / "autoyast.xml")
            shutil.copy(automation_file_path, iso_content_dir / "autoinst.xml")

            # Create ISO using genisoimage or mkisofs
            iso_cmd = None
            if shutil.which("genisoimage"):
                iso_cmd = ["genisoimage"]
            elif shutil.which("mkisofs"):
                iso_cmd = ["mkisofs"]
            else:
                raise Exception(
                    "Neither genisoimage nor mkisofs found. Please install genisoimage."
                )

            iso_cmd.extend(
                [
                    "-o",
                    str(local_iso_path),
                    "-V",
                    "AUTOINSTALL",  # Volume label that might be detected
                    "-r",  # Rock Ridge extensions
                    "-J",  # Joliet extensions
                    str(iso_content_dir),
                ]
            )

            subprocess.run(iso_cmd, check=True, capture_output=True)
            self.logger.info(f"Created local AutoYaST ISO at {local_iso_path}")

            # Clean up the temporary directory
            shutil.rmtree(iso_content_dir)

            # Upload the ISO to the storage pool
            pool = self.conn.storagePoolLookupByName(storage_pool_name)

            # Check if volume already exists - if so, reuse it
            try:
                existing_vol = pool.storageVolLookupByName(iso_filename)
                existing_path = existing_vol.path()
                self.logger.info(
                    f"Reusing existing AutoYaST ISO volume {iso_filename} at {existing_path}"
                )
                # Clean up local temporary file
                local_iso_path.unlink()
                return existing_path
            except libvirt.libvirtError:
                pass  # Volume doesn't exist, create it

            # Get ISO file size
            iso_size = local_iso_path.stat().st_size

            # Create volume in the pool
            volume_xml = f"""
            <volume>
                <name>{iso_filename}</name>
                <capacity unit="bytes">{iso_size}</capacity>
                <target>
                    <format type='raw'/>
                </target>
            </volume>
            """
            vol = pool.createXML(volume_xml, 0)

            # Upload the local file to the volume
            stream = self.conn.newStream(0)
            vol.upload(stream, 0, iso_size, 0)

            # Read and send the ISO data
            with open(local_iso_path, "rb") as f:
                iso_data = f.read()
                stream.send(iso_data)

            stream.finish()

            # Get the full path to the ISO in the pool
            iso_pool_path = vol.path()
            self.logger.info(f"Uploaded AutoYaST ISO to storage pool at {iso_pool_path}")

            # Clean up local file
            local_iso_path.unlink()

            return iso_pool_path

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to create AutoYaST ISO: {e}")
            raise Exception(f"Failed to create AutoYaST ISO: {e}")
        except libvirt.libvirtError as e:
            self.logger.error(f"Failed to upload AutoYaST ISO to storage pool: {e}")
            raise Exception(f"Failed to upload AutoYaST ISO to storage pool: {e}")

    def _inject_qemu_kernel_args(self, domain_xml: str, kernel_args: str) -> str:
        """
        Injects QEMU kernel arguments into domain XML using qemu:commandline namespace.

        This is used for AutoYaST to pass boot parameters that enable automatic unattended
        installation. Since virt-install --install kernel_args does NOT work with --cdrom,
        we use QEMU's -append argument to inject kernel parameters directly.

        Args:
            domain_xml: The original domain XML string from virt-install --print-xml
            kernel_args: Kernel command line arguments to inject (e.g., "autoyast=cd:/autoinst.xml")

        Returns:
            Modified XML string with QEMU arguments injected

        Raises:
            Exception: If XML parsing or modification fails

        Example output XML:
            <domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
              <name>my-vm</name>
              ...
              <qemu:commandline>
                <qemu:arg value='-append'/>
                <qemu:arg value='autoyast=cd:/autoinst.xml'/>
              </qemu:commandline>
            </domain>
        """
        try:
            # Define QEMU namespace
            QEMU_NS = "http://libvirt.org/schemas/domain/qemu/1.0"

            # Register namespace to ensure proper serialization
            ET.register_namespace("qemu", QEMU_NS)

            self.logger.info("Parsing domain XML for kernel argument injection")
            self.logger.debug(f"Original XML length: {len(domain_xml)} bytes")

            # Parse the XML
            root = ET.fromstring(domain_xml)

            # Create qemu:commandline element at the end of domain
            # This must be at the end of the domain definition
            qemu_commandline = ET.SubElement(root, f"{{{QEMU_NS}}}commandline")

            # Add -append argument
            ET.SubElement(qemu_commandline, f"{{{QEMU_NS}}}arg", value="-append")

            # Add the kernel arguments
            ET.SubElement(qemu_commandline, f"{{{QEMU_NS}}}arg", value=kernel_args)

            self.logger.info(f"Successfully injected kernel arguments: {kernel_args}")
            self.logger.debug(f"QEMU commandline element created with 2 args")

            # Serialize back to XML string
            modified_xml = ET.tostring(root, encoding="unicode")
            self.logger.debug(
                f"Modified XML length: {len(modified_xml)} bytes (before namespace fix)"
            )

            # Add the QEMU namespace declaration to the root element
            # ElementTree doesn't always serialize namespace declarations properly,
            # so we add it manually to the opening <domain> tag
            if "<domain" in modified_xml and "xmlns:qemu=" not in modified_xml:
                # Find the position after the opening <domain tag (before the closing >)
                domain_tag_end = modified_xml.find(">")
                if domain_tag_end > 0:
                    # Insert the namespace declaration before the closing >
                    namespace_decl = f' xmlns:qemu="{QEMU_NS}"'
                    modified_xml = (
                        modified_xml[:domain_tag_end]
                        + namespace_decl
                        + modified_xml[domain_tag_end:]
                    )
                    self.logger.debug("Added QEMU namespace declaration to domain element")

            self.logger.debug(f"Final XML length: {len(modified_xml)} bytes")

            return modified_xml

        except ET.ParseError as parse_error:
            self.logger.error(f"XML parsing error: {parse_error}")
            self.logger.error(
                f"Failed to parse XML at position: {parse_error.position if hasattr(parse_error, 'position') else 'unknown'}"
            )
            raise Exception(f"Invalid XML from virt-install: {parse_error}") from parse_error

        except Exception as e:
            self.logger.error(f"Unexpected error injecting QEMU kernel arguments: {e}")
            self.logger.error(f"Error type: {type(e).__name__}")
            raise Exception(f"Failed to inject kernel arguments: {e}") from e

    def _create_floppy_image(
        self, automation_file_path: str, output_dir: Path, storage_pool_name: str, vm_name: str
    ) -> str:
        """
        Create a floppy disk image containing the AutoYaST file and upload to storage pool.

        Args:
            automation_file_path: Path to the autoyast.xml file
            output_dir: Directory to create the floppy image in (local temp)
            storage_pool_name: Name of the storage pool to upload to
            vm_name: Name of the VM (used to create unique floppy image)

        Returns:
            Path to the floppy image in the storage pool
        """
        floppy_filename = f"autoyast_floppy_{vm_name}.img"
        local_floppy_path = output_dir / floppy_filename

        try:
            # Create a 1.44MB floppy image using qemu-img
            subprocess.run(
                ["qemu-img", "create", "-f", "raw", str(local_floppy_path), "1440K"],
                check=True,
                capture_output=True,
            )

            # Format as FAT12 filesystem
            subprocess.run(
                ["mkfs.vfat", str(local_floppy_path)],
                check=True,
                capture_output=True,
            )

            # Copy the autoyast.xml file to the floppy image using mcopy
            # Create both autoyast.xml and autoinst.xml for maximum compatibility
            # Different SUSE versions look for different filenames
            subprocess.run(
                ["mcopy", "-i", str(local_floppy_path), automation_file_path, "::autoyast.xml"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["mcopy", "-i", str(local_floppy_path), automation_file_path, "::autoinst.xml"],
                check=True,
                capture_output=True,
            )

            self.logger.info(f"Created local floppy image at {local_floppy_path}")

            # Upload the floppy image to the storage pool
            pool = self.conn.storagePoolLookupByName(storage_pool_name)

            # Check if volume already exists - if so, reuse it
            try:
                existing_vol = pool.storageVolLookupByName(floppy_filename)
                existing_path = existing_vol.path()
                self.logger.info(
                    f"Reusing existing floppy volume {floppy_filename} at {existing_path}"
                )
                # Clean up local temporary file
                local_floppy_path.unlink()
                return existing_path
            except libvirt.libvirtError:
                pass  # Volume doesn't exist, create it

            # Create volume in the pool
            volume_xml = f"""
            <volume>
                <name>{floppy_filename}</name>
                <capacity unit="bytes">{1440 * 1024}</capacity>
                <target>
                    <format type='raw'/>
                </target>
            </volume>
            """
            vol = pool.createXML(volume_xml, 0)

            # Upload the local file to the volume
            stream = self.conn.newStream(0)
            vol.upload(stream, 0, 1440 * 1024, 0)

            # Read and send the floppy data
            with open(local_floppy_path, "rb") as f:
                floppy_data = f.read()
                stream.send(floppy_data)

            stream.finish()

            # Get the full path to the floppy in the pool
            floppy_pool_path = vol.path()
            self.logger.info(f"Uploaded floppy image to storage pool at {floppy_pool_path}")

            # Clean up local file
            local_floppy_path.unlink()

            return floppy_pool_path

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to create floppy image: {e}")
            raise Exception(f"Failed to create floppy image for AutoYaST: {e}")
        except libvirt.libvirtError as e:
            self.logger.error(f"Failed to upload floppy to storage pool: {e}")
            raise Exception(f"Failed to upload floppy to storage pool: {e}")

    def check_virt_install(self) -> bool:
        """Checks if virt-install is available on the system."""
        return shutil.which("virt-install") is not None

    def _run_virt_install(
        self,
        vm_name: str,
        settings: Dict[str, Any],
        disk_path: str,
        iso_path: str,
        memory_mb: int,
        vcpu: int,
        loader_path: str | None,
        nvram_path: str | None,
        print_xml: bool = False,
        floppy_image_path: str | None = None,
        autoyast_kernel_args: str | None = None,
    ) -> str | None:
        """
        Executes virt-install to create the VM using the provided settings.
        If print_xml is True, it returns the generated XML instead of creating the VM.

        Args:
            autoyast_kernel_args: Optional AutoYaST kernel arguments for two-phase creation.
                                 If provided, will use --print-xml to generate XML, inject
                                 QEMU commandline arguments, then define VM from modified XML.
        """
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

        # ISO - Determine whether to use --location or --cdrom
        # --location doesn't work over remote SSH connections with ISO files
        uri = self.conn.getURI()
        is_remote = uri and ("ssh" in uri or "tcp" in uri or "tls" in uri)

        # Always use --cdrom for now (simpler and works for both local and remote)
        # We'll use --install kernel_args to pass AutoYaST boot parameters
        cmd.extend(["--cdrom", iso_path])
        logging.info(f"Using --cdrom for installation ISO: {iso_path}")

        # Network
        cmd.extend(["--network", f"default,model={settings['network_model']}"])

        # Graphics
        cmd.extend(["--graphics", "vnc,port=-1,listen=0.0.0.0"])

        # Video
        cmd.extend(["--video", settings["video"]])

        # Sound
        if settings.get("sound_model") and settings["sound_model"] != "none":
            cmd.extend(["--sound", f"model={settings['sound_model']}"])

        # Console
        cmd.extend(["--console", "pty,target.type=serial"])

        # QEMU Guest Agent
        cmd.extend(["--channel", "unix,target.type=virtio,name=org.qemu.guest_agent.0"])

        # Machine
        cmd.extend(["--machine", settings["machine"]])

        # Boot / Firmware
        if settings["boot_uefi"]:
            if loader_path and nvram_path:
                # Explicit paths
                cmd.extend(
                    [
                        "--boot",
                        f"loader={loader_path},loader.readonly=yes,loader.type=pflash,nvram={nvram_path},nvram.templateFormat=qcow2",
                    ]
                )
            else:
                # Auto
                cmd.extend(["--boot", "uefi"])

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

        # Memory Backing - not directly supported by virt-install CLI, needs custom XML for --mem-path

        # AutoYaST/Automation media injection with kernel boot parameters
        # SOLUTION: Use two-phase VM creation with QEMU commandline namespace
        #
        # PROBLEM: virt-install --install kernel_args does NOT work with --cdrom, only with --location
        # ERROR from virt-install: "Kernel arguments are only supported with location or kernel installs"
        #
        # SOLUTION IMPLEMENTED:
        # When autoyast_kernel_args parameter is provided, we use a two-phase approach:
        #   Phase 1: Generate domain XML with: virt-install --print-xml ...
        #   Phase 2: Inject QEMU arguments using qemu:commandline namespace:
        #            <qemu:arg value="-append"/>
        #            <qemu:arg value="autoyast=cd:/autoinst.xml"/>
        #   Phase 3: Define VM from modified XML: conn.defineXML(modified_xml)
        #
        # This allows us to pass kernel boot parameters even when using --cdrom for installation.
        # The QEMU -append argument injects boot parameters directly, bypassing the virt-install limitation.
        #
        # Boot parameter format:
        #   - For ISO media: autoyast=cd:/autoinst.xml
        #   - For floppy media: autoyast=device://fd0/autoinst.xml
        #
        # Fallback behavior:
        # If two-phase creation fails (e.g., QEMU namespace unsupported), the code falls back
        # to direct virt-install without kernel arguments. The AutoYaST media is still attached,
        # so the installer MAY auto-detect it. If not, user must manually add boot parameter.
        #
        # OpenSUSE installer auto-detection (secondary method):
        # The installer searches for AutoYaST config files in this order:
        #   1. Boot command line (autoyast=... parameter) - NOW POSSIBLE via QEMU commandline
        #   2. Floppy drive - looks for autoinst.xml, autoyast.xml, or autoyast/autoinst.xml
        #   3. CD/DVD - looks for autoinst.xml or autoyast.xml at root
        #   4. USB devices - similar to CD/DVD
        #
        # Our ISO/floppy contains both autoinst.xml AND autoyast.xml at root for maximum compatibility.

        if floppy_image_path:
            # Determine if this is an ISO or floppy based on extension
            is_iso = floppy_image_path.lower().endswith(".iso")

            if is_iso:
                # Attach as second CD-ROM
                cmd.extend(["--disk", f"path={floppy_image_path},device=cdrom,readonly=on"])
                logging.info(f"AutoYaST ISO attached as second CD-ROM: {floppy_image_path}")
                logging.info(
                    "ISO contains autoinst.xml and autoyast.xml at root with volume label 'AUTOINSTALL'"
                )
                logging.info("Installer should auto-detect AutoYaST config during boot")
                logging.info(
                    "If auto-detection fails, manually add boot parameter: autoyast=cd:/autoinst.xml"
                )
            else:
                # Attach as floppy
                cmd.extend(["--disk", f"path={floppy_image_path},device=floppy,readonly=on"])
                logging.info(f"AutoYaST floppy attached: {floppy_image_path}")
                logging.info("Floppy contains autoinst.xml and autoyast.xml at root")
                logging.info("Installer should auto-detect AutoYaST config during boot")
                logging.info(
                    "If auto-detection fails, manually add boot parameter: autoyast=device://fd0/autoinst.xml"
                )

        # Determine if we need two-phase creation for AutoYaST with kernel arguments
        use_two_phase = autoyast_kernel_args is not None and not print_xml

        if use_two_phase:
            # Two-Phase VM Creation for AutoYaST
            # Phase 1: Generate XML with virt-install --print-xml
            # Phase 2: Inject QEMU kernel arguments using qemu:commandline namespace
            # Phase 3: Define VM from modified XML
            logging.info("=" * 80)
            logging.info("Using two-phase creation for AutoYaST with kernel arguments")
            logging.info(f"Kernel arguments to inject: {autoyast_kernel_args}")
            logging.info("=" * 80)

            # Create command for XML generation (add --print-xml and completion flags)
            cmd_for_xml = cmd + ["--print-xml", "--noautoconsole", "--wait", "0"]

            try:
                logging.info("Phase 1: Generating domain XML with virt-install --print-xml")
                logging.info(f"Command: {' '.join(cmd_for_xml)}")
                result = subprocess.run(cmd_for_xml, check=True, capture_output=True, text=True)
                domain_xml = result.stdout

                if not domain_xml or not domain_xml.strip():
                    raise Exception("virt-install --print-xml returned empty XML")

                logging.info(f"Generated XML successfully ({len(domain_xml)} bytes)")
                if result.stderr:
                    logging.debug(f"virt-install stderr: {result.stderr.strip()}")

                # Phase 2: Inject QEMU kernel arguments
                logging.info(f"Phase 2: Injecting QEMU kernel arguments: {autoyast_kernel_args}")
                try:
                    modified_xml = self._inject_qemu_kernel_args(domain_xml, autoyast_kernel_args)
                    logging.info("Successfully injected kernel arguments into XML")

                    # Phase 3: Define VM from modified XML
                    logging.info("Phase 3: Defining VM from modified XML")
                    dom = self.conn.defineXML(modified_xml)
                    logging.info(
                        f"VM '{vm_name}' defined successfully with AutoYaST kernel arguments"
                    )
                    logging.info(
                        StaticText.AUTOYAST_TWO_PHASE_SUCCESS.format(
                            kernel_args=autoyast_kernel_args
                        )
                    )
                    logging.info("=" * 80)

                    # Return None to indicate we've already defined the VM
                    # The caller should look up the domain by name
                    return None

                except Exception as xml_error:
                    logging.error("=" * 80)
                    logging.error(f"FAILED to inject kernel arguments: {xml_error}")
                    logging.error("Falling back to direct virt-install without kernel arguments")
                    logging.error("User will need to manually add boot parameter at GRUB menu")
                    logging.error("=" * 80)
                    # Fall through to run virt-install directly without modifications

            except subprocess.CalledProcessError as e:
                logging.error("=" * 80)
                logging.error(f"FAILED to generate XML with virt-install: {e.stderr}")
                logging.error("Falling back to direct virt-install without kernel arguments")
                logging.error("=" * 80)
                # Fall through to run virt-install directly

        cmd.extend(["--noautoconsole"])
        cmd.extend(["--wait", "0"])

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

    def provision_windows_vm(
        self,
        vm_name: str,
        vm_type: VMType,
        windows_version: str,
        storage_pool_name: str,
        iso_path: Optional[str] = None,
        memory_mb: int = 4096,
        vcpu: int = 2,
        disk_size_gb: int = 40,
        disk_format: str | None = None,
        boot_uefi: bool = True,
        configure_before_install: bool = False,
        show_config_modal_callback: Optional[Callable[[libvirt.virDomain], None]] = None,
        progress_callback: Optional[Callable[[str, int], None]] = None,
    ) -> libvirt.virDomain:
        """
        Provision a Windows VM using the Windows provider.

        Args:
            vm_name: Name of the VM to create.
            vm_type: Type of the VM (e.g., Desktop, Server).
            windows_version: Windows version ID (e.g., "10", "11", "2019", "2022").
            storage_pool_name: Name of the storage pool where the disk will be created.
            iso_path: Optional path to Windows ISO file (from cached ISOs). If not provided,
                     will try to use download URL from provider (not recommended as Microsoft
                     doesn't provide stable direct download URLs).
            memory_mb: RAM in megabytes.
            vcpu: Number of virtual CPUs.
            disk_size_gb: Disk size in gigabytes (default 40GB for Windows).
            disk_format: Disk format (e.g., qcow2).
            boot_uefi: Whether to use UEFI boot (recommended for Windows 10+).
            configure_before_install: If True, defines VM and shows details modal before starting.
            show_config_modal_callback: Optional callback to show configuration modal.
            progress_callback: Optional callback for progress updates.

        Returns:
            libvirt.virDomain: The provisioned domain object.
        """
        from .provisioning.providers.windows_provider import WindowsProvider

        self.logger.info(f"Starting Windows VM provisioning: {vm_name}, version: {windows_version}")

        # Determine ISO source
        final_iso_url = None

        if iso_path:
            # User provided an ISO path (from cached ISOs)
            self.logger.info(f"Using provided Windows ISO path: {iso_path}")
            final_iso_url = iso_path
        else:
            # Try to get ISO from provider (will likely fail as Microsoft doesn't provide direct URLs)
            windows_provider = WindowsProvider()
            eval_iso_info = windows_provider.EVAL_ISO_URLS.get(windows_version)

            if not eval_iso_info:
                raise ValueError(
                    f"Unsupported Windows version: {windows_version}. "
                    f"Supported versions: {', '.join(windows_provider.EVAL_ISO_URLS.keys())}"
                )

            iso_url = eval_iso_info.get("url")

            # Check if direct download URL is available
            if not iso_url:
                download_page = eval_iso_info.get(
                    "download_page", "https://www.microsoft.com/en-us/evalcenter"
                )
                from .constants import ErrorMessages

                error_msg = ErrorMessages.WINDOWS_MANUAL_DOWNLOAD_REQUIRED.format(
                    version=windows_version, download_page=download_page
                )
                self.logger.error(error_msg)
                raise ValueError(error_msg)

            self.logger.info(f"Windows {windows_version} ISO URL: {iso_url}")
            final_iso_url = iso_url

        # Call the regular provision_vm method with the ISO URL
        return self.provision_vm(
            vm_name=vm_name,
            vm_type=vm_type,
            iso_url=final_iso_url,
            storage_pool_name=storage_pool_name,
            memory_mb=memory_mb,
            vcpu=vcpu,
            disk_size_gb=disk_size_gb,
            disk_format=disk_format,
            boot_uefi=boot_uefi,
            configure_before_install=configure_before_install,
            show_config_modal_callback=show_config_modal_callback,
            progress_callback=progress_callback,
            automation_config=None,  # TODO: Windows unattend.xml support
        )

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
        boot_uefi: bool = True,
        use_virt_install: bool = True,
        configure_before_install: bool = False,
        show_config_modal_callback: Optional[Callable[[libvirt.virDomain], None]] = None,
        progress_callback: Optional[Callable[[str, int], None]] = None,
        automation_config: Optional[Dict[str, Any]] = None,
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
            boot_uefi: Whether to use UEFI boot.
            use_virt_install: If True, uses virt-install CLI tool.
            configure_before_install: If True, defines VM and shows details modal before starting.
            show_config_modal_callback: Optional callback to show configuration modal. Takes (domain) as parameters.
        """

        def report(stage, percent):
            if progress_callback:
                progress_callback(stage, percent)

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

                    def download_cb(percent):
                        report(
                            StaticText.PROVISIONING_DOWNLOADING_ISO_PERCENT.format(percent=percent),
                            10 + int(percent * 0.4),
                        )  # 10-50%

                    if not os.path.exists(local_iso_path_for_upload):
                        self.download_iso(current_iso_url, local_iso_path_for_upload, download_cb)
                    else:
                        report(StaticText.PROVISIONING_ISO_FOUND_IN_CACHE, 50)

                # Return the local cached path directly
                return local_iso_path_for_upload

        iso_path = _determine_iso_path(iso_url)

        # Setup NVRAM if UEFI

        # Setup NVRAM if UEFI
        loader_path = None
        nvram_path = None

        is_virt_install_available = use_virt_install and self.check_virt_install()

        if boot_uefi and not is_virt_install_available:
            report(StaticText.PROVISIONING_SETTING_UP_UEFI_FIRMWARE, 75)
            # Only setup NVRAM if we are not using virt-install
            # virt-install --boot uefi will handle this automatically if we don't pass paths
            loader_path, nvram_path = self._setup_uefi_nvram(vm_name, storage_pool_name, vm_type)

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

        # Generate automation file if automation is enabled
        automation_file_path = None
        if automation_config:
            try:
                logging.info(f"Automation config provided: {automation_config.keys()}")
                report(StaticText.PROVISIONING_GENERATING_AUTOMATION_CONFIG, 82)

                # Get the OpenSUSE provider to generate automation file
                provider = self.get_provider("opensuse")
                if provider:
                    logging.info("OpenSUSE provider found for automation")
                    # Create a temporary directory for automation file
                    temp_dir = Path(tempfile.mkdtemp(prefix=f"virtui_automation_{vm_name}_"))

                    # Extract template name from automation config
                    template_name = automation_config.get("template_name", "autoyast-basic.xml")

                    # Generate automation file using the provider
                    automation_file_path = provider.generate_automation_file(
                        version=None,  # We don't have version info here, provider should handle this
                        vm_name=vm_name,
                        user_config=automation_config,
                        output_path=temp_dir,
                        template_name=template_name,
                    )
                    self.logger.info(f"Generated automation file: {automation_file_path}")

                    # Validate the generated XML
                    try:
                        with open(automation_file_path, "r", encoding="utf-8") as f:
                            xml_content = f.read()
                        ET.fromstring(xml_content)
                        self.logger.info("Automation file validation passed")
                    except ET.ParseError as parse_error:
                        self.logger.error(f"Invalid XML in automation file: {parse_error}")
                        raise Exception(f"Invalid XML in automation file: {parse_error}")
                    except Exception as e:
                        self.logger.error(f"Error reading automation file: {e}")
                        raise Exception(f"Error reading automation file: {e}")

                else:
                    self.logger.warning("OpenSUSE provider not available for automation")

            except Exception as e:
                self.logger.error(f"Failed to generate automation file: {e}")
                # Continue without automation rather than failing the entire process
                automation_file_path = None

        # Create automation media (ISO or floppy) for AutoYaST
        # For remote connections: use ISO (more reliably detected by installer)
        # For local connections: use floppy with --location and --extra-args
        autoyast_media_path = None
        if automation_file_path:
            try:
                logging.info(f"Creating AutoYaST media for automation file: {automation_file_path}")
                temp_dir = Path(automation_file_path).parent

                # Check if this will be a remote connection
                uri = self.conn.getURI()
                is_remote_conn = uri and ("ssh" in uri or "tcp" in uri or "tls" in uri)
                logging.info(f"Connection URI: {uri}, is_remote: {is_remote_conn}")

                if is_remote_conn:
                    # Remote: Create ISO for better detection
                    logging.info("Creating AutoYaST ISO for remote connection")
                    autoyast_media_path = self._create_autoyast_iso(
                        str(automation_file_path), temp_dir, storage_pool_name, vm_name
                    )
                    logging.info(f"AutoYaST ISO created at: {autoyast_media_path}")
                else:
                    # Local: Create floppy (works with --location + --extra-args)
                    logging.info("Creating AutoYaST floppy for local connection")
                    autoyast_media_path = self._create_floppy_image(
                        str(automation_file_path), temp_dir, storage_pool_name, vm_name
                    )
                    logging.info(f"AutoYaST floppy created at: {autoyast_media_path}")
            except Exception as e:
                logging.error(f"Failed to create AutoYaST media: {e}")
                # Continue without automation
                autoyast_media_path = None
        else:
            if automation_config:
                logging.warning("Automation config provided but no automation file was generated!")

        # Prepare AutoYaST kernel arguments if automation media is available
        autoyast_kernel_args = None
        if autoyast_media_path:
            # Determine the correct autoyast parameter based on media type
            is_iso = autoyast_media_path.lower().endswith(".iso")
            if is_iso:
                autoyast_kernel_args = "autoyast=cd:/autoinst.xml"
                logging.info("AutoYaST: Will use kernel argument for ISO media")
            else:
                autoyast_kernel_args = "autoyast=device://fd0/autoinst.xml"
                logging.info("AutoYaST: Will use kernel argument for floppy media")

            logging.info(f"Prepared AutoYaST kernel arguments: {autoyast_kernel_args}")

        # Handle Configure Before Install feature
        if configure_before_install:
            # Generate the XML configuration that would be used
            if is_virt_install_available:
                settings = self._get_vm_settings(vm_type, boot_uefi, disk_format)
                xml_desc = self._run_virt_install(
                    vm_name,
                    settings,
                    disk_path,
                    iso_path,
                    memory_mb,
                    vcpu,
                    loader_path,
                    nvram_path,
                    print_xml=True,
                    floppy_image_path=str(autoyast_media_path) if autoyast_media_path else None,
                    autoyast_kernel_args=autoyast_kernel_args,
                )

                # If autoyast_kernel_args were provided and XML was generated, inject them
                if autoyast_kernel_args and xml_desc:
                    try:
                        logging.info(
                            "Configure mode: Injecting AutoYaST kernel arguments into preview XML"
                        )
                        xml_desc = self._inject_qemu_kernel_args(xml_desc, autoyast_kernel_args)
                        logging.info("Configure mode: Successfully injected kernel arguments")
                    except Exception as e:
                        logging.warning(f"Configure mode: Failed to inject kernel args: {e}")
                        logging.warning("Configure mode: Continuing with unmodified XML")
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
                    automation_file_path=autoyast_media_path,
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

            report(StaticText.PROVISIONING_COMPLETE_CONFIG_MODE, 100)
            return dom

        # Continue with normal VM creation
        if is_virt_install_available:
            report(StaticText.PROVISIONING_CONFIGURING_VM_VIRT_INSTALL, 80)
            settings = self._get_vm_settings(vm_type, boot_uefi, disk_format)
            try:
                self._run_virt_install(
                    vm_name,
                    settings,
                    disk_path,
                    iso_path,
                    memory_mb,
                    vcpu,
                    loader_path,
                    nvram_path,
                    floppy_image_path=str(autoyast_media_path) if autoyast_media_path else None,
                    autoyast_kernel_args=autoyast_kernel_args,
                )
            except Exception as e:
                logging.info(f"Can't install domain {vm_name}: {e}")

            report(StaticText.PROVISIONING_WAITING_FOR_VM, 95)
            # Fetch the domain object
            dom = self.conn.lookupByName(vm_name)
        else:
            # Generate XML
            report(StaticText.PROVISIONING_CONFIGURING_VM_XML, 80)
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
                automation_file_path=autoyast_media_path,
            )

            # Define and Start VM
            report(StaticText.PROVISIONING_STARTING_VM, 90)
            dom = self.conn.defineXML(xml_desc)
            dom.create()

        report(StaticText.PROVISIONING_COMPLETE, 100)
        return dom
