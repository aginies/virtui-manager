"""
Library for VM creation and provisioning, specifically focused on OpenSUSE.
"""
import os
import time
import logging
import urllib.request
import ssl
import re
import hashlib
from enum import Enum
from typing import Callable, Optional, Dict, Any, List
import libvirt
import xml.etree.ElementTree as ET

from storage_manager import create_volume
from libvirt_utils import get_domain_capabilities_xml, get_host_architecture

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

    def get_iso_list(self, distro: OpenSUSEDistro) -> List[str]:
        """
        Retrieves a list of available ISO URLs for the specified distribution.
        """
        if distro == OpenSUSEDistro.CUSTOM:
            return []

        base_url = self.distro_base_urls.get(distro)
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
                    # Regex to find ISOs matching host architecture
                    pattern = rf'href="([^"]+{self.host_arch}[^"]+\.iso)"'
                    links = re.findall(pattern, html)
                    return [os.path.join(url, link) if not link.startswith('http') else link for link in links]
                except Exception as e:
                    logging.warning(f"Error fetching ISOs from {url}: {e}")
                    return []

            if distro == OpenSUSEDistro.LEAP:
                # Use hardcoded versions as requested
                versions = ['15.5', '15.6', '16.0']
                for ver in versions:
                    ver_iso_url = f"{base_url}{ver}/iso/"
                    iso_urls.extend(fetch_isos_from_url(ver_iso_url))
            
            else:
                # Direct ISO directories
                iso_urls.extend(fetch_isos_from_url(base_url))

            # Deduplicate and sort
            return sorted(list(set(iso_urls)), reverse=True)
            
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
        
        # Upload data
        stream = self.conn.newStream(0)
        try:
            vol.upload(stream, 0, file_size)
            
            with open(local_path, "rb") as f:
                uploaded = 0
                while True:
                    data = f.read(1024*1024) # 1MB chunk
                    if not data:
                        break
                    stream.send(data)
                    uploaded += len(data)
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
        # This is a placeholder. In a real scenario, we'd parse 
        # getDomainCapabilities or /sys/module/kvm_amd/parameters/sev
        # For now, we return 'auto' defaults or hardcoded safe values if needed.
        return {
            'cbitpos': 47, # Typical for AMD EPYC
            'reducedPhysBits': 1,
            'policy': '0x0033'
        }

    def generate_xml(self, vm_name: str, vm_type: VMType, disk_path: str, iso_path: str, memory_mb: int = 4096, vcpu: int = 2) -> str:
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
        
        disk_name = f"{vm_name}.{ 'raw' if vm_type == VMType.COMPUTATION else 'qcow2' }"
        disk_path = os.path.join(pool_target_path, disk_name)
        
        # 2. Download ISO
        # Derive name from URL
        iso_name = iso_url.split('/')[-1]
        iso_path = os.path.join(pool_target_path, iso_name)
        
        def download_cb(percent):
            report(f"Downloading ISO: {percent}%", 10 + int(percent * 0.4)) # 10-50%

        if not os.path.exists(iso_path):
             self.download_iso(iso_url, iso_path, download_cb)
        else:
             report("ISO found, skipping download", 50)

        # 3. Create Disk
        report("Creating Storage", 60)
        # 8GB default from settings
        create_volume(pool, disk_name, 8, 'raw' if vm_type == VMType.COMPUTATION else 'qcow2')
        
        # 4. Generate XML
        report("Configuring VM", 80)
        xml_desc = self.generate_xml(vm_name, vm_type, disk_path, iso_path)
        
        # 5. Define and Start VM
        report("Starting VM", 90)
        dom = self.conn.defineXML(xml_desc)
        dom.create()
        
        report("Provisioning Complete", 100)
        return dom
