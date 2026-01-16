"""
Library for VM creation and provisioning, specifically focused on OpenSUSE.
"""
import os
import logging
import urllib.request
import ssl
import re
import hashlib
from datetime import datetime
from enum import Enum
from typing import Callable, Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
from pathlib import Path
import libvirt

from config import load_config
from storage_manager import create_volume
from libvirt_utils import get_host_architecture
from constants import AppInfo

class VMType(Enum):
    SECURE = "Secure VM"
    COMPUTATION = "Computation"
    DESKTOP = "Desktop"

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
                # Use hardcoded versions as requested
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

    def generate_xml(self, vm_name: str, vm_type: VMType, disk_path: str, iso_path: str, memory_mb: int = 4096, vcpu: int = 2, disk_format: str | None = None) -> str:
        """
        Generates the Libvirt XML for the VM based on the type and default settings.
        """

        # --- Defaults based on DEFAULT_SETTINGS.md ---
        settings = {
            # Storage
            'disk_bus': 'virtio',
            'disk_format': 'qcow2',
            'disk_cache': 'none',

            # Guest
            'machine': 'pc-q35-10.1',
            'video': 'virtio',
            'network_model': 'e1000',
            'suspend_to_mem': 'off',
            'suspend_to_disk': 'off',
            'boot_uefi': True,
            'iothreads': 0,

            # Features
            'sev': False,
            'tpm': False,
            'mem_backing': False,
        }

        if vm_type == VMType.SECURE:
            settings.update({
                'disk_cache': 'writethrough',
                'disk_format': 'qcow2',
                'video': 'qxl',
                'tpm': True,
                'sev': True,
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
                'on_poweroff': 'destroy',
                'on_reboot': 'restart',
                'on_crash': 'destroy',
            })

        # Override disk format if provided
        if disk_format:
            settings['disk_format'] = disk_format

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
             xml += f"""
  <os firmware='efi'>
    <type arch='x86_64' machine='{settings['machine']}'>hvm</type>
"""
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

        # TPM (Secure VM)
        if settings['tpm']:
            xml += """
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>
"""
        # Watchdog (Computation)
        if vm_type == VMType.COMPUTATION:
            xml += """
    <watchdog model='i6300esb' action='poweroff'/>
"""

        # Console/Serial
        xml += """
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
"""

        # Input devices (Tablet for better mouse)
        xml += """
    <input type='tablet' bus='usb'/>
    <input type='mouse' bus='ps2'/>
    <input type='keyboard' bus='ps2'/>
"""

        xml += "  </devices>\n"

        if settings['mem_backing']:
            xml += f"  <memoryBacking>\n    <source type='{settings['mem_backing']}'/>\n"
            if settings.get('sev'):
                xml += "    <locked/>\n" # Often needed for SEV
            xml += "  </memoryBacking>\n"

        xml += "</domain>"

        return xml

    def provision_vm(self, vm_name: str, vm_type: VMType, iso_url: str, storage_pool_name: str,
                     memory_mb: int = 4096, vcpu: int = 2, disk_size_gb: int = 8, disk_format: str | None = None,
                     progress_callback: Optional[Callable[[str, int], None]] = None) -> libvirt.virDomain:
        """
        Orchestrates the VM provisioning process.
        """
        def report(stage, percent):
            if progress_callback:
                progress_callback(stage, percent)

        report("Checking Environment", 0)

        # 1. Prepare Storage Pool for Disk
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

        # 2. Download ISO
        # Define local cache path for ISOs
        config = load_config()
        iso_cache_dir = Path(config.get('ISO_DOWNLOAD_PATH', str(Path.home() / ".cache" / AppInfo.name / "isos")))
        iso_cache_dir.mkdir(parents=True, exist_ok=True)

        # Derive name from URL
        iso_name = iso_url.split('/')[-1]
        iso_cache_path = str(iso_cache_dir / iso_name)

        def download_cb(percent):
            report(f"Downloading ISO: {percent}%", 10 + int(percent * 0.4)) # 10-50%

        if not os.path.exists(iso_cache_path):
            self.download_iso(iso_url, iso_cache_path, download_cb)
        else:
            report("ISO found, skipping download", 50)

        # 3. Upload ISO from cache to storage pool
        report("Uploading ISO to storage pool", 55)
        def upload_cb(percent):
            # This stage is quick, allocate a small progress percentage (e.g., 5%)
            report(f"Uploading ISO: {percent}%", 55 + int(percent * 0.05))

        iso_path = self.upload_iso(iso_cache_path, storage_pool_name, upload_cb)

        # 4. Create Disk
        report("Creating Storage", 60)

        preallocation = 'metadata' if vm_type in [VMType.SECURE, VMType.DESKTOP] else 'off'
        lazy_refcounts = True if vm_type in [VMType.SECURE, VMType.COMPUTATION] else False
        cluster_size = '1024k' if vm_type in [VMType.SECURE, VMType.DESKTOP] else None

        create_volume(
            pool,
            disk_name,
            disk_size_gb,
            vol_format=storage_format,
            preallocation=preallocation,
            lazy_refcounts=lazy_refcounts,
            cluster_size=cluster_size
        )

        # 4. Generate XML
        report("Configuring VM", 80)
        xml_desc = self.generate_xml(vm_name, vm_type, disk_path, iso_path, memory_mb, vcpu, disk_format)

        # 5. Define and Start VM
        report("Starting VM", 90)
        dom = self.conn.defineXML(xml_desc)
        dom.create()

        report("Provisioning Complete", 100)
        return dom
