"""
Library for VM creation and provisioning, specifically focused on OpenSUSE.
"""
import os
import logging
import urllib.request
import ssl
import re
import hashlib
import subprocess
import tempfile
import shutil
from datetime import datetime
from enum import Enum
from typing import Callable, Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
from pathlib import Path
import libvirt

from .config import load_config
from .storage_manager import create_volume
from .libvirt_utils import get_host_architecture
from .firmware_manager import get_uefi_files
from .constants import AppInfo

class VMType(Enum):
    SECURE = "Secure VM"
    COMPUTATION = "Computation"
    DESKTOP = "Desktop (Linux)"
    WDESKTOP = "Windows"
    WLDESKTOP = "Windows Legacy"
    SERVER = "Server"

class OpenSUSEDistro(Enum):
    LEAP = "Leap"
    TUMBLEWEED = "Tumbleweed"
    SLOWROLL = "Slowroll"
    STABLE = "Stable (Leap)"
    CURRENT = "Current (Tumbleweed)"
    CUSTOM = "Custom ISO"

class VMProvisioner:
    def __init__(self, conn: libvirt.virConnect):
        self.conn = conn
        self.host_arch = get_host_architecture(conn)
        self.distro_base_urls = {
            OpenSUSEDistro.LEAP: "https://download.opensuse.org/distribution/leap/",
            OpenSUSEDistro.TUMBLEWEED: "https://download.opensuse.org/tumbleweed/iso/",
            OpenSUSEDistro.SLOWROLL: "https://download.opensuse.org/slowroll/iso/",
            OpenSUSEDistro.STABLE: "https://download.opensuse.org/distribution/openSUSE-stable/offline/",
            OpenSUSEDistro.CURRENT: "https://download.opensuse.org/distribution/openSUSE-current/installer/iso/"
        }

    def get_custom_repos(self) -> List[Dict[str, str]]:
        """
        Retrieves the list of custom ISO repositories from the configuration.
        """
        config = load_config()
        return config.get('custom_ISO_repo', [])

    def get_iso_details(self, url: str) -> Dict[str, Any]:
        """
        Fetches details (Last-Modified) for a given ISO URL.
        """
        name = url.split('/')[-1]
        try:
            context = ssl._create_unverified_context()
            req = urllib.request.Request(url, method='HEAD')
            with urllib.request.urlopen(req, context=context, timeout=5) as response:
                last_modified = response.getheader('Last-Modified')
                date_str = ""
                if last_modified:
                    try:
                        dt = parsedate_to_datetime(last_modified)
                        date_str = dt.strftime("%Y-%m-%d %H:%M")
                    except:
                        date_str = last_modified

                return {'name': name, 'url': url, 'date': date_str}
        except Exception as e:
            logging.warning(f"Failed to get details for {url}: {e}")
            return {'name': name, 'url': url, 'date': ''}

    def get_cached_isos(self) -> List[Dict[str, Any]]:
        """
        Retrieves a list of ISOs already present in the local cache directory.
        """
        config = load_config()
        iso_cache_dir = Path(config.get('ISO_DOWNLOAD_PATH', str(Path.home() / ".cache" / AppInfo.name / "isos")))

        if not iso_cache_dir.exists():
            return []

        isos = []
        try:
            for f in iso_cache_dir.glob("*.iso"):
                # Use stats for date
                mtime = f.stat().st_mtime
                dt_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                isos.append({
                    'name': f.name,
                    'url': f.name, # Use filename as URL for local detection logic
                    'date': f"{dt_str} (Cached)"
                })
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
                    results.append({
                        'name': f.name,
                        'url': str(f.absolute()),
                        'date': dt_str
                    })
                except Exception as e:
                     logging.warning(f"Error reading file {f}: {e}")

            results.sort(key=lambda x: x['name'], reverse=True)
        except Exception as e:
            logging.error(f"Error listing local ISOs from {path}: {e}")

        return results

    def get_iso_list(self, distro: OpenSUSEDistro | str) -> List[Dict[str, Any]]:
        """
        Retrieves a list of available ISOs with details for the specified distribution or custom repo URL.
        """
        if distro == OpenSUSEDistro.CUSTOM:
            return []

        base_url = ""
        if isinstance(distro, OpenSUSEDistro):
            base_url = self.distro_base_urls.get(distro)
        elif isinstance(distro, str):
            base_url = distro

        if not base_url:
            return []

        # Check for local directory or file URI
        if base_url.startswith("/") or base_url.startswith("file://") or os.path.isdir(base_url):
            return self._get_local_iso_list(base_url)

        logging.info(f"Fetching ISO list from {base_url} for arch {self.host_arch}")

        # Create unverified context to avoid SSL errors with some mirrors
        context = ssl._create_unverified_context()
        iso_urls = []

        try:
            # Helper to fetch and find ISOs in a specific URL
            def fetch_isos_from_url(url):
                try:
                    with urllib.request.urlopen(url, context=context, timeout=10) as response:
                        html = response.read().decode('utf-8')

                    pattern = rf'href="([^"]+\.iso)"' # Relaxed to find any ISO
                    links = re.findall(pattern, html)

                    valid_links = []
                    for link in links:
                        # Basic filtering: ends with .iso
                        if not link.endswith('.iso'): continue

                        link_lower = link.lower()
                        is_arch_specific = any(a in link_lower for a in ['x86_64', 'amd64', 'aarch64', 'arm64'])

                        if is_arch_specific:
                            # Map host arch to common names
                            # self.host_arch is likely x86_64
                            target_arch = self.host_arch
                            if target_arch == 'x86_64':
                                if 'x86_64' in link_lower or 'amd64' in link_lower:
                                    pass
                                else:
                                    continue # specific to another arch
                            elif target_arch == 'aarch64':
                                if 'aarch64' in link_lower or 'arm64' in link_lower:
                                    pass
                                else:
                                    continue

                        full_url = os.path.join(url, link) if not link.startswith('http') else link
                        valid_links.append(full_url)

                    return valid_links
                except Exception as e:
                    logging.warning(f"Error fetching ISOs from {url}: {e}")
                    return []

            if isinstance(distro, OpenSUSEDistro) and distro == OpenSUSEDistro.LEAP:
                # Use hardcoded versions
                versions = ['15.5', '15.6', '16.0']
                for ver in versions:
                    ver_iso_url = f"{base_url}{ver}/iso/"
                    iso_urls.extend(fetch_isos_from_url(ver_iso_url))

            else:
                # Direct ISO directories
                iso_urls.extend(fetch_isos_from_url(base_url))

            # Deduplicate URLs
            unique_urls = sorted(list(set(iso_urls)), reverse=True)

            # Fetch details in parallel
            with ThreadPoolExecutor(max_workers=10) as executor:
                results = list(executor.map(self.get_iso_details, unique_urls))

            # Sort by name descending (or date?) - keeping name sort for consistency
            results.sort(key=lambda x: x['name'], reverse=True)

            return results

        except Exception as e:
            logging.error(f"Failed to fetch ISO list: {e}")
            return []

    def download_iso(self, url: str, dest_path: str, progress_callback: Optional[Callable[[int], None]] = None):
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
            with urllib.request.urlopen(url, context=context) as response, open(dest_path, 'wb') as out_file:
                total_size = int(response.getheader('Content-Length').strip())
                downloaded_size = 0
                chunk_size = 1024 * 1024 # 1MB chunks

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
                os.remove(dest_path) # Clean up partial file
            raise e

    def upload_iso(self, local_path: str, storage_pool_name: str, progress_callback: Optional[Callable[[int], None]] = None) -> str:
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
            logging.info(f"Volume '{iso_name}' already exists in pool '{storage_pool_name}'. Skipping upload.")
            if progress_callback:
                progress_callback(100)
            return vol.path()
        except libvirt.libvirtError:
            pass # Volume does not exist, proceed to create

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
            logging.info(f"Set libvirt keepalive to 10s for ISO upload.")
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
                        data = f.read(1024*1024) # 1MB chunk
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
                    logging.info(f"Restored libvirt keepalive to interval={old_interval}, count={old_count}.")
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
            'cbitpos': 47, # Typical for AMD EPYC
            'reducedPhysBits': 1,
            'policy': '0x0033'
        }

    def _setup_uefi_nvram(self, vm_name: str, target_pool_name: str, vm_type: VMType,
                           support_snapshots: bool = True) -> tuple[str, str]:
        """
        Sets up UEFI NVRAM on the server side by:
        1. Finding suitable firmware using the firmware_manager.
        2. Identifying the code/vars pair from the firmware metadata.
        3. Cloning the vars template to the target pool.

        Args:
            support_snapshots: If True, ensures NVRAM format is snapshot-compatible

        Returns: (loader_path, nvram_path)
        """
        all_firmwares = get_uefi_files()
        candidate_fw = None

        # Score each firmware to find the best match
        best_score = -1

        for fw in all_firmwares:
            if self.host_arch not in fw.architectures:
                continue

            if not fw.nvram_template:
                continue

            score = 0
            is_secure = 'secure-boot' in fw.features
            has_pflash = 'pflash' in fw.interfaces

            # Match VM type requirements
            if vm_type == VMType.SECURE:
                if is_secure:
                    score += 100  # High priority for secure boot
            else:
                if not is_secure:
                    score += 50  # Prefer non-secure for non-secure VMs

            # Check for snapshot-related features
            # Some firmware metadata may include hints about snapshot support
            if 'enrolled-keys' in fw.features:
                score += 10

           # Prefer firmware with certain naming patterns known to work well
            if fw.executable:
                exec_lower = fw.executable.lower()
                if 'ovmf' in exec_lower:
                    score += 5
                if 'code' in exec_lower:
                    score += 5

            # Massive preference for pflash to preserve original behavior
            if has_pflash:
                score += 1000

            if score > best_score:
                best_score = score
                candidate_fw = fw

        if not candidate_fw or not candidate_fw.executable or not candidate_fw.nvram_template:
            raise Exception("Could not find suitable UEFI firmware (OVMF) using firmware_manager.")

        loader_path = candidate_fw.executable
        vars_template_path = candidate_fw.nvram_template

        # Check if we need conversion (fallback to non-pflash)
        has_pflash = 'pflash' in candidate_fw.interfaces
        needs_conversion = not has_pflash

        logging.info(f"Selected firmware (score={best_score}, pflash={has_pflash}): loader='{loader_path}', nvram_template='{vars_template_path}'")

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

           # Determine NVRAM format based on snapshot requirement
            # For snapshot support, qcow2 is generally better, but we need to ensure
            # the template can be properly converted
            if support_snapshots:
                nvram_format = 'qcow2'
                nvram_name = f"{vm_name}_VARS.qcow2"
            else:
                # Use raw format - simpler, but no snapshot support for NVRAM
                nvram_format = 'raw'
                nvram_name = f"{vm_name}_VARS.fd"

            nvram_path = None

            # Check if already exists in target pool
            try:
                target_vol = target_pool.storageVolLookupByName(nvram_name)
                logging.info(f"NVRAM volume '{nvram_name}' already exists.")
                nvram_path = target_vol.path()
            except libvirt.libvirtError:
                logging.info(f"Creating new {nvram_format} NVRAM volume '{nvram_name}' from '{vars_vol_name}'")

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
                    raise Exception(f"Failed to download content from NVRAM template '{vars_vol_name}'. Template appears to be empty.")

                if needs_conversion:
                    logging.info(f"Firmware does not support pflash directly. Converting NVRAM template to {nvram_format} using qemu-img.")
                    # Create temporary files for conversion
                    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp_in:
                        try:
                            tmp_in.write(received_data)
                            tmp_in.flush()
                            tmp_in_name = tmp_in.name
                        except Exception as e:
                            os.remove(tmp_in.name)
                            raise e

                    tmp_out_name = tmp_in_name + f".{nvram_format}" # safe suffix

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
                        if os.path.exists(tmp_in_name): os.remove(tmp_in_name)
                        if os.path.exists(tmp_out_name): os.remove(tmp_out_name)

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

    def _get_vm_settings(self, vm_type: VMType, boot_uefi: bool, disk_format: str | None = None) -> Dict[str, Any]:
        """
        Returns a dictionary of VM settings based on type and options.
        """
        settings = {
            # Storage
            'disk_bus': 'virtio',
            'disk_format': 'qcow2',
            'disk_cache': 'none',
            # Guest
            'machine': 'pc-q35-10.1' if boot_uefi else 'pc-i440fx-10.1',
            'video': 'virtio',
            'network_model': 'e1000',
            'suspend_to_mem': 'off',
            'suspend_to_disk': 'off',
            'boot_uefi': boot_uefi,
            'iothreads': 0,
            'input_bus': 'virtio',
            'sound_model': 'none',
            # Features
            'sev': False,
            'tpm': False,
            'mem_backing': False,
            'watchdog': False,
            'on_poweroff': 'destroy',
            'on_reboot': 'restart',
            'on_crash': 'destroy',
        }
        if vm_type == VMType.SECURE:
            settings.update({
                'disk_cache': 'writethrough',
                'disk_format': 'qcow2',
                'video': 'qxl',
                'tpm': True,
                'sev': True,
                'input_bus': 'ps2',
                'mem_backing': False, # Explicitly off in table
                'on_poweroff': 'destroy',
                'on_reboot': 'destroy',
                'on_crash': 'destroy',
            })
        elif vm_type == VMType.COMPUTATION:
            settings.update({
                'disk_cache': 'unsafe',
                'disk_format': 'raw',
                'video': 'qxl',
                'network_model': 'virtio',
                'iothreads': 4,
                'mem_backing': 'memfd', # memfd/shared
                'watchdog': True,
                'on_poweroff': 'restart',
                'on_reboot': 'restart',
                'on_crash': 'restart',
            })
        elif vm_type == VMType.DESKTOP:
            settings.update({
                'disk_cache': 'none',
                'disk_format': 'qcow2',
                'video': 'virtio',
                'network_model': 'e1000',
                'suspend_to_mem': 'on',
                'suspend_to_disk': 'on',
                'mem_backing': 'memfd',
                'sound_model': 'ich9',
                'on_poweroff': 'destroy',
                'on_reboot': 'restart',
                'on_crash': 'destroy',
            })
        elif vm_type == VMType.WDESKTOP or vm_type == VMType.WLDESKTOP:
            settings.update({
                'disk_bus': 'sata',
                'disk_cache': 'none',
                'disk_format': 'qcow2',
                'video': 'virtio',
                'network_model': 'e1000',
                'suspend_to_mem': 'on',
                'suspend_to_disk': 'on',
                'mem_backing': 'memfd',
                'sound_model': 'ich9',
                'tpm': True if vm_type == VMType.WDESKTOP else False,
                'on_poweroff': 'destroy',
                'on_reboot': 'restart',
                'on_crash': 'destroy',
            })
            if vm_type == VMType.WLDESKTOP:
                settings['machine'] = 'pc-i440fx-10.1'
                settings['input_bus'] = 'usb'

        elif vm_type == VMType.SERVER:
            settings.update({
                'disk_cache': 'none',
                'disk_format': 'qcow2',
                'video': 'virtio',
                'network_model': 'virtio',
                'suspend_to_mem': 'on',
                'suspend_to_disk': 'on',
                'mem_backing': False,
                'on_poweroff': 'destroy',
                'on_reboot': 'restart',
                'on_crash': 'restart',
            })

        # Override disk format if provided
        if disk_format:
            settings['disk_format'] = disk_format
            
        return settings

    def generate_xml(self, vm_name: str, vm_type: VMType, disk_path: str, iso_path: str, memory_mb: int = 4096, vcpu: int = 2, disk_format: str | None = None, loader_path: str | None = None, nvram_path: str | None = None, boot_uefi: bool = True) -> str:
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
        if settings['boot_uefi']:
            if loader_path and nvram_path:
                xml += f"""
  <os>
    <type arch='x86_64' machine='{settings['machine']}'>hvm</type>
    <loader readonly='yes' type='pflash'>{loader_path}</loader>
    <nvram format='qcow2'>{nvram_path}</nvram>
"""
            else:
                xml += f"""
  <os firmware='efi'>
    <type arch='x86_64' machine='{settings['machine']}'>hvm</type>
    <loader readonly='yes' type='pflash'/>
"""
                if nvram_path:
                    xml += f"    <nvram format='qcow2'>{nvram_path}</nvram>\n"
                else:
                    xml += "    <nvram format='qcow2'/>\n"
        else:
            xml += f"""
  <os>
    <type arch='x86_64' machine='{settings['machine']}'>hvm</type>
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
""".format(settings.get('on_poweroff', 'destroy'), settings.get('on_reboot', 'restart'), settings.get('on_crash', 'destroy'))

        if settings['suspend_to_mem'] == 'on' or settings['suspend_to_disk'] == 'on':
            xml += "  <pm>\n"
            if settings['suspend_to_mem'] == 'on': xml += "    <suspend-to-mem enabled='yes'/>\n"
            if settings['suspend_to_disk'] == 'on': xml += "    <suspend-to-disk enabled='yes'/>\n"
            xml += "  </pm>\n"

        if settings['sev']:
            sev_caps = self._get_sev_capabilities()
            xml += f"""
  <launchSecurity type='sev'>
    <cbitpos>{sev_caps['cbitpos']}</cbitpos>
    <reducedPhysBits>{sev_caps['reducedPhysBits']}</reducedPhysBits>
    <policy>{sev_caps['policy']}</policy>
  </launchSecurity>
"""

        xml += "  <devices>\n"

        # Disk
        xml += f"""
    <disk type='file' device='disk'>
      <driver name='qemu' type='{settings['disk_format']}' cache='{settings['disk_cache']}'/>
      <source file='{disk_path}'/>
      <target dev='vda' bus='{settings['disk_bus']}'/>
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

        # Interface
        xml += f"""
    <interface type='network'>
      <source network='default'/>
      <model type='{settings['network_model']}'/>
    </interface>
"""

        # Video
        xml += f"""
    <video>
      <model type='{settings['video']}'/>
    </video>
    <graphics type='vnc' port='-1' autoport='yes' listen='0.0.0.0'>
      <listen type='address' address='0.0.0.0'/>
    </graphics>
"""
        # Sound
        if settings.get('sound_model') and settings['sound_model'] != 'none':
            xml += f"""
    <sound model='{settings['sound_model']}'/>
"""

        # TPM (Secure VM)
        if settings['tpm']:
            xml += """
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>
"""
        # Watchdog (Computation)
        if settings['watchdog']:
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
    <input type='mouse' bus='{settings['input_bus']}'/>
    <input type='keyboard' bus='{settings['input_bus']}'/>
"""

        xml += "  </devices>\n"

        if settings['mem_backing']:
            xml += f"  <memoryBacking>\n    <source type='{settings['mem_backing']}'/>\n"
            if settings.get('sev'):
                xml += "    <locked/>\n" # Often needed for SEV
            xml += "  </memoryBacking>\n"
        xml += "</domain>"
        return xml

    def check_virt_install(self) -> bool:
        """Checks if virt-install is available on the system."""
        return shutil.which("virt-install") is not None

    def _run_virt_install(self, vm_name: str, settings: Dict[str, Any], disk_path: str, iso_path: str,
                          memory_mb: int, vcpu: int, loader_path: str | None, nvram_path: str | None,
                          print_xml: bool = False) -> str | None:
        """
        Executes virt-install to create the VM using the provided settings.
        If print_xml is True, it returns the generated XML instead of creating the VM.
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

        # ISO
        cmd.extend(["--cdrom", iso_path])

        # Network
        cmd.extend(["--network", f"default,model={settings['network_model']}"])

        # Graphics
        cmd.extend(["--graphics", "vnc,port=-1,listen=0.0.0.0"])

        # Video
        cmd.extend(["--video", settings['video']])

        # Sound
        if settings.get('sound_model') and settings['sound_model'] != 'none':
            cmd.extend(["--sound", f"model={settings['sound_model']}"])

        # Console
        cmd.extend(["--console", "pty,target.type=serial"])

        # QEMU Guest Agent
        cmd.extend(["--channel", "unix,target.type=virtio,name=org.qemu.guest_agent.0"])

        # Machine
        cmd.extend(["--machine", settings['machine']])

        # Boot / Firmware
        if settings['boot_uefi']:
            if loader_path and nvram_path:
                # Explicit paths
                cmd.extend(["--boot", f"loader={loader_path},loader.readonly=yes,loader.type=pflash,nvram={nvram_path},nvram.templateFormat=qcow2"])
            else:
                # Auto
                cmd.extend(["--boot", "uefi"])

        # Features
        if settings['sev']:
            sev_caps = self._get_sev_capabilities()
            cmd.extend(["--launchSecurity", f"sev,cbitpos={sev_caps['cbitpos']},reducedPhysBits={sev_caps['reducedPhysBits']},policy={sev_caps['policy']}"])

        if settings['tpm']:
            cmd.extend(["--tpm", "model=tpm-crb,backend.type=emulator,backend.version=2.0"])

        if settings['watchdog']:
            cmd.extend(["--watchdog", "model=i6300esb,action=poweroff"])

        # PM
        if settings['suspend_to_mem'] == 'on' or settings['suspend_to_disk'] == 'on':
            pm_opts = []
            if settings['suspend_to_mem'] == 'on': pm_opts.append("suspend_to_mem=on")
            if settings['suspend_to_disk'] == 'on': pm_opts.append("suspend_to_disk=on")
            cmd.extend(["--pm", ",".join(pm_opts)])

        # Memory Backing - not directly supported by virt-install CLI, needs custom XML for --mem-path

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

    def provision_vm(self, vm_name: str, vm_type: VMType, iso_url: str, storage_pool_name: str,
                     memory_mb: int = 4096, vcpu: int = 2, disk_size_gb: int = 8, disk_format: str | None = None,
                     boot_uefi: bool = True, use_virt_install: bool = True,
                     configure_before_install: bool = False,
                     show_config_modal_callback: Optional[Callable[[libvirt.virDomain], None]] = None,
                     progress_callback: Optional[Callable[[str, int], None]] = None) -> libvirt.virDomain:
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

        report("Checking Environment", 0)

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
            storage_format = 'raw' if vm_type == VMType.COMPUTATION else 'qcow2'

        disk_name = f"{vm_name}.{storage_format}"
        disk_path = os.path.join(pool_target_path, disk_name)

        # Download ISO
        # Define local cache path for ISOs
        config = load_config()
        iso_cache_dir = Path(config.get('ISO_DOWNLOAD_PATH', str(Path.home() / ".cache" / AppInfo.name / "isos")))
        iso_cache_dir.mkdir(parents=True, exist_ok=True)

        # Helper function to determine the final ISO path
        def _determine_iso_path(current_iso_url: str) -> str:
            # Check if iso_url already points to an existing libvirt storage volume
            existing_iso_volume_path = self._find_iso_volume_by_path(current_iso_url)
            if existing_iso_volume_path:
                report(f"Using existing ISO volume: {existing_iso_volume_path}", 55)
                return existing_iso_volume_path
            else: # original behavior, downloads/copies to cache and then uploads to storage pool
                iso_name = current_iso_url.split('/')[-1]
                is_local_source = current_iso_url.startswith("/") or current_iso_url.startswith("file://") or os.path.exists(current_iso_url)

                if is_local_source:
                    if current_iso_url.startswith("file://"):
                        local_iso_path_for_upload = current_iso_url[7:]
                    else:
                        local_iso_path_for_upload = current_iso_url
                    report("Using local ISO image", 50)
                    return local_iso_path_for_upload
                else:
                    local_iso_path_for_upload = str(iso_cache_dir / iso_name)

                    def download_cb(percent):
                        report(f"Downloading ISO: {percent}%", 10 + int(percent * 0.4)) # 10-50%

                    if not os.path.exists(local_iso_path_for_upload):
                        self.download_iso(current_iso_url, local_iso_path_for_upload, download_cb)
                    else:
                        report("ISO found in cache, skipping download", 50)

                # Return the local cached path directly
                return local_iso_path_for_upload

        iso_path = _determine_iso_path(iso_url)

        # Setup NVRAM if UEFI


        # Setup NVRAM if UEFI
        loader_path = None
        nvram_path = None

        is_virt_install_available = use_virt_install and self.check_virt_install()

        if boot_uefi and not is_virt_install_available:
            report("Setting up UEFI Firmware", 75)
            # Only setup NVRAM if we are not using virt-install
            # virt-install --boot uefi will handle this automatically if we don't pass paths
            loader_path, nvram_path = self._setup_uefi_nvram(vm_name, storage_pool_name, vm_type)

        # Create Disk
        report("Creating Storage", 78) # Adjusted percentage

        preallocation = 'metadata' if vm_type in [VMType.SECURE, VMType.DESKTOP, VMType.WDESKTOP, VMType.WLDESKTOP] else 'off'
        lazy_refcounts = True if vm_type in [VMType.SECURE, VMType.COMPUTATION] else False
        cluster_size = '1024k' if vm_type in [VMType.SECURE, VMType.DESKTOP, VMType.WDESKTOP, VMType.WLDESKTOP] else None

        create_volume(
            pool,
            disk_name,
            disk_size_gb,
            vol_format=storage_format,
            preallocation=preallocation,
            lazy_refcounts=lazy_refcounts,
            cluster_size=cluster_size
        )

        # Handle Configure Before Install feature
        if configure_before_install:
            # Generate the XML configuration that would be used
            if is_virt_install_available:
                settings = self._get_vm_settings(vm_type, boot_uefi, disk_format)
                xml_desc = self._run_virt_install(vm_name, settings, disk_path, iso_path, memory_mb, vcpu, loader_path, nvram_path, print_xml=True)
            else:
                xml_desc = self.generate_xml(vm_name, vm_type, disk_path, iso_path, memory_mb, vcpu, disk_format, loader_path=loader_path, nvram_path=nvram_path, boot_uefi=boot_uefi)

            # Define the VM
            report("Defining VM", 85)
            dom = self.conn.defineXML(xml_desc)

            # Show the configuration in a modal if callback is provided
            if show_config_modal_callback:
                show_config_modal_callback(dom)
            else:
                # Fallback: just log the configuration
                logging.info(f"VM configuration defined for {vm_name}")

            report("Provisioning Complete (Configuration Mode)", 100)
            return dom

        # Continue with normal VM creation
        if is_virt_install_available:
            report("Configuring VM (virt-install)", 80)
            settings = self._get_vm_settings(vm_type, boot_uefi, disk_format)
            try:
                self._run_virt_install(vm_name, settings, disk_path, iso_path, memory_mb, vcpu, loader_path, nvram_path)
            except Exception as e:
                logging.info(f"Can't install domain {vm_name}: {e}")

            report("Waiting for VM", 95)
            # Fetch the domain object
            dom = self.conn.lookupByName(vm_name)
        else:
            # Generate XML
            report("Configuring VM (XML)", 80)
            xml_desc = self.generate_xml(vm_name, vm_type, disk_path, iso_path, memory_mb, vcpu, disk_format, loader_path=loader_path, nvram_path=nvram_path, boot_uefi=boot_uefi)

            # Define and Start VM
            report("Starting VM", 90)
            dom = self.conn.defineXML(xml_desc)
            dom.create()

        report("Provisioning Complete", 100)
        return dom
