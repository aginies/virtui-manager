"""
Module for retrieving information about virtual machines.
"""
import xml.etree.ElementTree as ET
import logging
from functools import lru_cache
import hashlib
import concurrent.futures
import libvirt
from .libvirt_utils import (
        VIRTUI_MANAGER_NS,
        _find_vol_by_path,
        _get_disabled_disks_elem,
        get_overlay_backing_path,
        _get_backing_chain_elem,
        get_host_domain_capabilities,
        )
from .constants import StatusText

def _parse_domain_xml_by_hash(xml_hash: str, xml_content: str) -> ET.Element | None:
    """
    Cache XML parsing results by hash for better hit rate.
    Default cache size: 2048 (configurable via VIRTUI_XML_CACHE_SIZE env var)
    Memory impact: ~30-90 MB for full cache
    """
    try:
        return ET.fromstring(xml_content)
    except ET.ParseError:
        return None

@lru_cache(maxsize=256)
def _parse_domain_xml(xml_content: str) -> ET.Element | None:
    """Cache XML parsing results."""
    if not xml_content:
        return None
    # Use hash for cache key to handle equivalent XML with different whitespace
    xml_hash = hashlib.md5(xml_content.encode()).hexdigest()
    return _parse_domain_xml_by_hash(xml_hash, xml_content)

def _get_domain_root(domain) -> tuple[str, ET.Element | None]:
    """Returns (xml_content, root_element) for a domain."""
    try:
        xml_content = domain.XMLDesc(0)
        return xml_content, _parse_domain_xml(xml_content)
    except libvirt.libvirtError:
        return "", None

#@log_function_call
def get_vm_network_dns_gateway_info(domain: libvirt.virDomain, root=None):
    """
    Extracts DNS and gateway information for networks connected to the VM.
    """
    if not domain:
        return []

    conn = domain.connect()
    if root is None:
        _, root = _get_domain_root(domain)

    if root is None:
        return []

    network_details = []

    # Find all network names from the VM's interfaces
    vm_networks = []
    for interface in root.findall(".//devices/interface"):
        source = interface.find("source")
        if source is not None:
            network_name = source.get("network")
            if network_name and network_name not in vm_networks:
                vm_networks.append(network_name)

    for net_name in vm_networks:
        try:
            network = conn.networkLookupByName(net_name)
            net_xml = network.XMLDesc(0)
            net_root = ET.fromstring(net_xml)

            gateway = None
            ip_elem = net_root.find("ip")
            if ip_elem is not None:
                gateway = ip_elem.get("address")

            dns_servers = []
            dns_elem = net_root.find("dns")
            if dns_elem is not None:
                for server in dns_elem.findall("server"):
                    dns_servers.append(server.get("address"))

            if gateway or dns_servers:
                network_details.append({
                    "network_name": net_name,
                    "gateway": gateway,
                    "dns_servers": dns_servers
                })

        except libvirt.libvirtError:
            # Network might not be found or other libvirt error
            continue

    return network_details

def get_status(domain, state=None):
    """
    state of a VM
    """
    if state is None:
        try:
            state, _ = domain.state()
        except libvirt.libvirtError:
            return StatusText.UNKNOWN

    if state == libvirt.VIR_DOMAIN_RUNNING:
        return StatusText.RUNNING
    elif state == libvirt.VIR_DOMAIN_PAUSED:
        return StatusText.PAUSED
    elif state == libvirt.VIR_DOMAIN_PMSUSPENDED:
        return StatusText.PMSUSPENDED
    elif state == libvirt.VIR_DOMAIN_BLOCKED:
        return StatusText.BLOCKED
    else:
        return StatusText.STOPPED

@lru_cache(maxsize=16)
def get_vm_description(domain):
    """
    desc of the VM
    """
    try:
        return domain.metadata(libvirt.VIR_DOMAIN_METADATA_DESCRIPTION, None)
    except libvirt.libvirtError:
        return "No description available"

def get_vm_firmware_info(root: ET.Element) -> dict:
    """
    Extracts firmware (BIOS/UEFI) from a VM's XML definition.
    Returns a dictionary with firmware info.
    """
    firmware_info = {'type': 'BIOS', 'path': None, 'secure_boot': False} # Default to BIOS

    if root is None:
        return firmware_info

    try:
        os_elem = root.find('os')

        if os_elem is not None:
            loader_elem = os_elem.find('loader')
            if loader_elem is not None and loader_elem.get('type') == 'pflash':
                loader_path = loader_elem.text
                if loader_path:
                    firmware_info['type'] = 'UEFI'
                    firmware_info['path'] = loader_path
                    if loader_elem.get('secure') == 'yes':
                        firmware_info['secure_boot'] = True
            else:
                bootloader_elem = os_elem.find('bootloader')
                if bootloader_elem is not None:
                    firmware_info['type'] = 'BIOS'

    except Exception:
        pass # Return default values if error

    return firmware_info

def get_vm_machine_info(root: ET.Element) -> str:
    """
    Extracts machine type from a VM's XML definition.
    """
    machine_type = "N/A"

    if root is None:
        return machine_type

    try:
        os_elem = root.find('os')

        # Get machine type from the 'machine' attribute of the 'type' element within 'os'
        if os_elem is not None:
            type_elem = os_elem.find('type')
            if type_elem is not None and 'machine' in type_elem.attrib:
                machine_type = type_elem.get('machine')

    except Exception:
        pass # Return default values if error

    return machine_type

def get_vm_networks_info(root: ET.Element) -> list[dict]:
    """Extracts network interface information from a VM's XML definition."""
    networks = []
    if root is None:
        return networks
    for interface in root.findall(".//devices/interface"):
        mac_address_node = interface.find("mac")
        if mac_address_node is None:
            continue
        mac_address = mac_address_node.get("address")

        source = interface.find("source")
        network_name = None
        if source is not None:
            network_name = source.get("network")

        model_node = interface.find("model")
        model = model_node.get("type") if model_node is not None else "default"

        # We are interested in interfaces that are part of a network
        if network_name:
            networks.append({"mac": mac_address, "network": network_name, "model": model})
    return networks


def get_vm_network_ip(domain) -> list:
    """
    Retrieves network interface IP addresses for a given VM domain.
    Requires qemu-guest-agent to be installed and running in the guest VM.
    Returns a list of dictionaries, where each dictionary represents an interface
    and contains its MAC address and a list of IP addresses.
    """
    if domain is None:
        return []
    if domain.state()[0] == libvirt.VIR_DOMAIN_RUNNING or domain.state()[0] == libvirt.VIR_DOMAIN_PAUSED:
        ip_addresses = []
        try:
            addresses = domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE)
            if addresses:
                for iface_name, iface_info in addresses.items():
                    interface_ips = {
                        'interface': iface_name,
                        'mac': iface_info['hwaddr'],
                        'ipv4': [],
                        'ipv6': []
                    }
                    if iface_info['addrs']:
                        for addr in iface_info['addrs']:
                            if addr['type'] == libvirt.VIR_IP_ADDR_TYPE_IPV4:
                                interface_ips['ipv4'].append(f"{addr['addr']}/{addr['prefix']}")
                            elif addr['type'] == libvirt.VIR_IP_ADDR_TYPE_IPV6:
                                interface_ips['ipv6'].append(f"{addr['addr']}/{addr['prefix']}")
                    ip_addresses.append(interface_ips)
        except libvirt.libvirtError:
            pass # Return empty list if there's an error or VM is not running
        return ip_addresses
    return []

def get_vm_devices_info(root: ET.Element) -> dict:
    """
    Extracts information about various virtual devices from a VM's XML definition.
    """
    devices_info = {
        'virtiofs': [],
        'virtio-serial': [],
        'isa-serial': [],
        'qemu_guest_agent': [],
        'graphics': [],
        'usb': [],
        'random': [],
        'tpm': [],
        'video': [],
        'watchdog': [],
        'input': [],
        'sound': [],
        'scsi': [],
        'channels': [],
    }

    if root is None:
        return devices_info

    try:
        devices = root.find("devices")

        if devices is not None:
            # virtiofs
            for fs_elem in devices.findall("./filesystem[@type='mount']"):
                driver = fs_elem.find('driver')
                if driver is not None and driver.get('type') == 'virtiofs':
                    source = fs_elem.find('source')
                    target = fs_elem.find('target')
                    if source is not None and target is not None:
                        readonly = fs_elem.find('readonly') is not None
                        devices_info['virtiofs'].append({
                            'source': source.get('dir'),
                            'target': target.get('dir'),
                            'readonly': readonly
                        })

            # Generic channels extraction
            devices_info['channels'] = []
            for channel_elem in devices.findall('channel'):
                channel_type = channel_elem.get('type')
                channel_details = {'type': channel_type}

                target_elem = channel_elem.find('target')
                if target_elem is not None:
                    target_type = target_elem.get('type')
                    name = target_elem.get('name')
                    state = target_elem.get('state')

                    if target_type: channel_details['target_type'] = target_type
                    if name: channel_details['name'] = name
                    if state: channel_details['state'] = state

                source_elem = channel_elem.find('source')
                if source_elem is not None:
                    mode = source_elem.get('mode')
                    path = source_elem.get('path')
                    if mode: channel_details['mode'] = mode
                    if path: channel_details['path'] = path

                devices_info['channels'].append(channel_details)

            # virtio-serial and qemu.guest_agent (legacy support/specific categorization)
            for channel_elem in devices.findall('channel'):
                channel_type = channel_elem.get('type')
                if channel_type == 'virtio':
                    target_elem = channel_elem.find('target')
                    if target_elem is not None:
                        name = target_elem.get('name')
                        if name == 'org.qemu.guest_agent.0':
                            devices_info['qemu_guest_agent'].append({'type': 'virtio-serial', 'name': name})
                        else:
                            devices_info['virtio-serial'].append({'name': name})
                elif channel_type == 'unix':
                    target_elem = channel_elem.find('target')
                    if target_elem is not None and target_elem.get('name') == 'org.qemu.guest_agent.0':
                        devices_info['qemu_guest_agent'].append({'type': 'unix-channel', 'path': target_elem.get('path')})

            # isa-serial
            for serial_elem in devices.findall("./serial[@type='isa']"):
                target_elem = serial_elem.find('target')
                if target_elem is not None:
                    port = target_elem.get('port', '0')
                    devices_info['isa-serial'].append({'port': port})

            # graphics (spice, vnc, etc.)
            for graphics_elem in devices.findall('graphics'):
                graphics_type = graphics_elem.get('type')
                if graphics_type:
                    detail = {'type': graphics_type}
                    if graphics_type == 'spice':
                        detail.update({
                            'port': graphics_elem.get('port'),
                            'tlsPort': graphics_elem.get('tlsPort'),
                            'autoport': graphics_elem.get('autoport'),
                        })
                    elif graphics_type == 'vnc':
                        detail.update({
                            'port': graphics_elem.get('port'),
                            'autoport': graphics_elem.get('autoport'),
                            'display': graphics_elem.get('display'),
                        })
                    devices_info['graphics'].append(detail)


            # usb controllers and devices
            for controller_elem in devices.findall("./controller[@type='usb']"):
                devices_info['usb'].append({
                    'type': 'controller',
                    'model': controller_elem.get('model'),
                    'index': controller_elem.get('index')
                })
            for usb_dev_elem in devices.findall("./hostdev[@type='usb']"):
                address = usb_dev_elem.find('address')
                if address is not None:
                    bus = address.get('bus')
                    device = address.get('device')
                    devices_info['usb'].append({'type': 'hostdev', 'bus': bus, 'device': device})

            # scsi controllers
            for controller_elem in devices.findall("./controller[@type='scsi']"):
                devices_info['scsi'].append({
                    'type': 'controller',
                    'model': controller_elem.get('model'),
                    'index': controller_elem.get('index')
                })

            # watchdog
            for watchdog_elem in devices.findall('watchdog'):
                devices_info['watchdog'].append({
                    'model': watchdog_elem.get('model'),
                    'action': watchdog_elem.get('action'),
                })
            # input
            for input_elem in devices.findall('input'):
                devices_info['input'].append({
                    'type': input_elem.get('type'),
                    'bus': input_elem.get('bus'),
                })
            # sound
            for sound_elem in devices.findall('sound'):
                model_elem = sound_elem.find('model')
                if model_elem is not None:
                    devices_info['sound'].append({
                        'model': model_elem.get('model'),
                })
            # random number generator
            rng_elem = devices.find("./rng")
            if rng_elem is not None:
                devices_info['random'].append({'model': rng_elem.get('model')})

            # tpm
            tpm_elem = devices.find("./tpm")
            if tpm_elem is not None:
                model = tpm_elem.get('model')
                devices_info['tpm'].append({'model': model})


    except Exception:
        pass

    return devices_info

@lru_cache(maxsize=32)
def get_vm_disks(domain: libvirt.virDomain) -> list[dict]:
    """
    Retrieves disk information for a domain.
    """
    conn = domain.connect()
    _, root = _get_domain_root(domain)
    return get_vm_disks_info(conn, root)


@lru_cache(maxsize=256)
def get_vm_disks_info(conn: libvirt.virConnect, root: ET.Element) -> list[dict]:
    """
    Extracts disks info from a VM's XML definition.
    Returns a list of dictionaries with 'path', 'status', 'bus', 'cache_mode', and 'discard_mode'.
    """
    disks = []
    if root is None:
        return disks
    try:
        # Enabled disks
        devices = root.find("devices")
        if devices is not None:
            for disk in devices.findall("disk"):
                disk_path = ""
                device_type = disk.get("device", "disk") # Get device type (disk/cdrom)
                disk_source = disk.find("source")
                if disk_source is not None:
                    if "file" in disk_source.attrib:
                        disk_path = disk_source.attrib["file"]
                    elif "dev" in disk_source.attrib:
                        disk_path = disk_source.attrib["dev"]
                    elif "pool" in disk_source.attrib and "volume" in disk_source.attrib:
                        pool_name = disk_source.attrib["pool"]
                        vol_name = disk_source.attrib["volume"]
                        try:
                            pool = conn.storagePoolLookupByName(pool_name)
                            vol = pool.storageVolLookupByName(vol_name)
                            disk_path = vol.path()
                        except libvirt.libvirtError:
                            disk_path = f"Error: volume '{vol_name}' not found in pool '{pool_name}'"

                if disk_path:
                    driver = disk.find("driver")
                    cache_mode = driver.get("cache") if driver is not None else "default"
                    discard_mode = driver.get("discard") if driver is not None else "ignore"

                    target_elem = disk.find('target')
                    bus = target_elem.get('bus') if target_elem is not None else 'N/A'

                    disks.append({
                        'path': disk_path,
                        'status': 'enabled',
                        'cache_mode': cache_mode,
                        'discard_mode': discard_mode,
                        'bus': bus,
                        'device_type': device_type
                    })

        # Disabled disks from metadata
        metadata_elem = root.find('metadata')
        if metadata_elem is not None:
            vmanager_meta_elem = metadata_elem.find(f'{{{VIRTUI_MANAGER_NS}}}virtuimanager')
            if vmanager_meta_elem is not None:
                # Use _get_disabled_disks_elem to get the element correctly
                disabled_disks_elem = _get_disabled_disks_elem(root)
                if disabled_disks_elem is not None:
                    for disk in disabled_disks_elem.findall('disk'):
                        disk_path = ""
                        device_type = disk.get("device", "disk") # Get device type
                        disk_source = disk.find("source")
                        if disk_source is not None:
                            if "file" in disk_source.attrib:
                                disk_path = disk_source.attrib["file"]
                            elif "dev" in disk_source.attrib:
                                disk_path = disk_source.attrib["dev"]
                            elif "pool" in disk_source.attrib and "volume" in disk_source.attrib:
                                pool_name = disk_source.attrib["pool"]
                                vol_name = disk_source.attrib["volume"]
                                try:
                                    pool = conn.storagePoolLookupByName(pool_name)
                                    vol = pool.storageVolLookupByName(vol_name)
                                    disk_path = vol.path()
                                except libvirt.libvirtError:
                                    disk_path = f"Error: volume '{vol_name}' not found in pool '{pool_name}'"

                        if disk_path:
                            driver = disk.find("driver")
                            cache_mode = driver.get("cache") if driver is not None else "default"
                            discard_mode = driver.get("discard") if driver is not None else "ignore"

                            target_elem = disk.find('target')
                            bus = target_elem.get('bus') if target_elem is not None else 'N/A'

                            disks.append({
                                'path': disk_path, 
                                'status': 'disabled', 
                                'cache_mode': cache_mode, 
                                'discard_mode': discard_mode, 
                                'bus': bus,
                                'device_type': device_type
                            })
    except Exception:
        pass  # Failed to get disks, continue without them

    return disks

@lru_cache(maxsize=32)
def get_all_vm_disk_usage(conn: libvirt.virConnect) -> dict[str, list[str]]:
    """
    Scans all VMs and returns a mapping of disk path to a list of VM names.
    Optimized to fetch VM XMLs and resolve disks in parallel.
    """
    disk_to_vms_map = {}
    if not conn:
        return disk_to_vms_map

    try:
        domains = conn.listAllDomains(0)
    except libvirt.libvirtError:
        return disk_to_vms_map

    def process_domain_disk_usage(domain):
        """Helper to process a single domain for disk usage."""
        try:
            _, root = _get_domain_root(domain)
            if root is not None:
                # get_vm_disks_info uses conn to look up storage pools/vols
                return domain.name(), get_vm_disks_info(conn, root)
        except Exception:
            pass
        return None, []

    # Use ThreadPoolExecutor for parallel processing
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = executor.map(process_domain_disk_usage, domains)

    for vm_name, disks in results:
        if vm_name:
            for disk in disks:
                path = disk.get('path')
                if path:
                    if path not in disk_to_vms_map:
                        disk_to_vms_map[path] = []
                    if vm_name not in disk_to_vms_map[path]:
                        disk_to_vms_map[path].append(vm_name)

    return disk_to_vms_map

def get_all_vm_overlay_usage(conn: libvirt.virConnect) -> dict[str, list[str]]:
    """
    Scans all VMs and returns a mapping of backing file path to a list of VM names
    that use it via an overlay (checked via metadata).
    Optimized to fetch VM XMLs in parallel.
    """
    backing_to_vms_map = {}
    if not conn:
        return backing_to_vms_map

    try:
        domains = conn.listAllDomains(0)
    except libvirt.libvirtError:
        return backing_to_vms_map

    def process_domain_overlay_usage(domain):
        """Helper to process a single domain for overlay usage."""
        try:
            _, root = _get_domain_root(domain)
            if root is not None:
                # Check all disks to see if they are overlays in metadata
                disks = get_vm_disks_info(conn, root)
                vm_name = domain.name()
                overlay_mappings = []
                for disk in disks:
                    path = disk.get('path')
                    if path:
                        backing_path = get_overlay_backing_path(root, path)
                        if backing_path:
                            overlay_mappings.append((backing_path, vm_name))
                return overlay_mappings
        except Exception:
            pass
        return []

    # Use ThreadPoolExecutor for parallel processing
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = executor.map(process_domain_overlay_usage, domains)

    for overlay_mappings in results:
        for backing_path, vm_name in overlay_mappings:
            if backing_path not in backing_to_vms_map:
                backing_to_vms_map[backing_path] = []
            if vm_name not in backing_to_vms_map[backing_path]:
                backing_to_vms_map[backing_path].append(vm_name)

    return backing_to_vms_map

def get_all_vm_nvram_usage(conn: libvirt.virConnect) -> dict[str, list[str]]:
    """
    Scans all VMs and returns a mapping of NVRAM file path to a list of VM names.
    Optimized to fetch VM XMLs in parallel.
    """
    nvram_to_vms_map = {}
    if not conn:
        return nvram_to_vms_map

    try:
        domains = conn.listAllDomains(0)
    except libvirt.libvirtError:
        return nvram_to_vms_map

    def process_domain_nvram_usage(domain):
        """Helper to process a single domain for NVRAM usage."""
        try:
            _, root = _get_domain_root(domain)
            if root is not None:
                nvram_elem = root.find('.//os/nvram')
                if nvram_elem is not None:
                    nvram_path = nvram_elem.text
                    if nvram_path:
                        return nvram_path, domain.name()
        except Exception:
            pass
        return None, None

    # Use ThreadPoolExecutor for parallel processing
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = executor.map(process_domain_nvram_usage, domains)

    for nvram_path, vm_name in results:
        if nvram_path and vm_name:
            if nvram_path not in nvram_to_vms_map:
                nvram_to_vms_map[nvram_path] = []
            if vm_name not in nvram_to_vms_map[nvram_path]:
                nvram_to_vms_map[nvram_path].append(vm_name)

    return nvram_to_vms_map


@lru_cache(maxsize=32)
def get_supported_machine_types(conn, domain):
    """
    Returns a list of supported machine types for the domain's architecture.
    """
    if not conn or not domain:
        return []

    try:
        # Get domain architecture
        _, domain_root = _get_domain_root(domain)
        if domain_root is None:
            return []
        arch_elem = domain_root.find(".//os/type")
        arch = arch_elem.get('arch') if arch_elem is not None else 'x86_64' # default

        # Get capabilities
        caps_xml = get_host_domain_capabilities(conn)
        if not caps_xml:
            return []
        caps_root = ET.fromstring(caps_xml)

        # Find machines for that arch
        machines = [m.text for m in caps_root.findall(f".//guest/arch[@name='{arch}']/machine")]
        return sorted(list(set(machines)))
    except (libvirt.libvirtError, ET.ParseError) as e:
        print(f"Error getting machine types: {e}")
        return []


def get_vm_shared_memory_info(root: ET.Element) -> bool:
    """Check if shared memory is enabled for the VM."""
    if root is None:
        return False
    try:
        memory_backing = root.find('memoryBacking')
        if memory_backing is not None:
            if memory_backing.find('shared') is not None:
                return True
            access_elem = memory_backing.find('access')
            if access_elem is not None and access_elem.get('mode') == 'shared':
                return True
    except Exception:
        pass
    return False


@lru_cache(maxsize=32)
def get_boot_info(conn: libvirt.virConnect, root: ET.Element) -> dict:
    """Extracts boot information from the VM's XML."""
    if root is None:
        return {'menu_enabled': False, 'order': []}
    os_elem = root.find('.//os')
    if os_elem is None:
        return {'menu_enabled': False, 'order': []}

    boot_menu = os_elem.find('bootmenu')
    menu_enabled = boot_menu is not None and boot_menu.get('enable') == 'yes'

    # First, try to get boot order from devices
    devices = []
    # Find all devices with a <boot order='...'> element
    for dev_node in root.findall('.//devices/*[boot]'):
        boot_elem = dev_node.find('boot')
        order = boot_elem.get('order')
        if order:
            try:
                order = int(order)
                if dev_node.tag == 'disk':
                    source_elem = dev_node.find('source')
                    if source_elem is not None:
                        path = None
                        if "file" in source_elem.attrib:
                            path = source_elem.attrib["file"]
                        elif "dev" in source_elem.attrib:
                            path = source_elem.attrib["dev"]
                        elif "pool" in source_elem.attrib and "volume" in source_elem.attrib:
                            pool_name = source_elem.attrib["pool"]
                            vol_name = source_elem.attrib["volume"]
                            try:
                                pool = conn.storagePoolLookupByName(pool_name)
                                vol = pool.storageVolLookupByName(vol_name)
                                path = vol.path()
                            except libvirt.libvirtError:
                                pass # Could not resolve path
                        if path:
                            devices.append((order, path))

                elif dev_node.tag == 'interface':
                    mac_elem = dev_node.find('mac')
                    if mac_elem is not None:
                        devices.append((order, mac_elem.get('address')))
            except (ValueError, TypeError):
                continue

    # Sort devices by boot order
    devices.sort(key=lambda x: x[0])
    order_from_devices = [dev[1] for dev in devices]

    if order_from_devices:
        return {'menu_enabled': menu_enabled, 'order': order_from_devices}

    # Fallback to legacy <boot dev='...'>
    order_from_os = [boot.get('dev') for boot in os_elem.findall('boot')]

    return {'menu_enabled': menu_enabled, 'order': order_from_os}


def get_vm_video_model(root: ET.Element) -> str | None:
    """Extracts the video model from a VM's XML definition."""
    if root is None:
        return None
    try:
        video = root.find('.//devices/video/model')
        if video is not None:
            return video.get('type')
    except Exception:
        pass
    return None

def get_vm_cpu_model(root: ET.Element) -> str | None:
    """Extracts the cpu model from a VM's XML definition."""
    if root is None:
        return None
    try:
        cpu = root.find('.//cpu')
        if cpu is not None:
            mode = cpu.get('mode')
            if mode == 'custom':
                model_elem = cpu.find('model')
                if model_elem is not None and model_elem.text:
                    return model_elem.text
            return mode
    except Exception:
        pass
    return None

def get_vm_cpu_details(root: ET.Element) -> str | None:
    """Extracts the cpu mode and model from a VM's XML definition for display."""
    if root is None:
        return None
    try:
        cpu = root.find('.//cpu')
        if cpu is not None:
            mode = cpu.get('mode')
            model_elem = cpu.find('model')
            if model_elem is not None and model_elem.text:
                return f"{mode} ({model_elem.text})"
            return mode
    except Exception:
        pass
    return None

def get_vm_sound_model(root: ET.Element) -> str | None:
    """Extracts the sound model from a VM's XML definition."""
    if root is None:
        return None
    try:
        sound = root.find('.//devices/sound')
        if sound is not None:
            return sound.get("model")
    except Exception:
        pass
    return None

def get_vm_video_info(root: ET.Element) -> dict:
    """
    Extracts video model and 3D acceleration info from a VM's XML definition.
    """
    video_info = {
        'model': 'none',
        'accel3d': False,
    }
    if root is None:
        return video_info
    try:
        model_elem = root.find('.//devices/video/model')
        if model_elem is not None:
            video_info['model'] = model_elem.get('type', 'none')

            accel_elem = model_elem.find('acceleration')
            if accel_elem is not None:
                video_info['accel3d'] = accel_elem.get('accel3d') == 'yes'
    except Exception:
        pass # Return default info on error
    return video_info

def get_vm_tpm_info(root: ET.Element) -> list[dict]:
    """
    Extracts TPM information from a VM's XML definition.
    Returns a list of dictionaries with TPM details including passthrough devices.
    """
    tpm_info = []
    if root is None:
        return tpm_info
    try:
        devices = root.find("devices")

        if devices is not None:
            for tpm_elem in devices.findall("./tpm"):
                tpm_model = tpm_elem.get('model')

                backend_elem = tpm_elem.find('backend')
                tpm_type = 'emulated'  # Default
                device_path = ''
                backend_type = ''
                backend_path = ''

                if backend_elem is not None:
                    backend_type = backend_elem.get('type', '')
                    if backend_type == 'passthrough':
                        tpm_type = 'passthrough'
                        device_elem = backend_elem.find('device')
                        if device_elem is not None:
                            device_path = device_elem.get('path', '')
                    elif backend_type == 'emulator':
                        tpm_type = 'emulated'
                        # For emulator, backend_path might be in text if used as file (less common for default emulator)
                        backend_path = backend_elem.text if backend_elem.text else ''

                tpm_info.append({
                    'model': tpm_model,
                    'type': tpm_type,
                    'device_path': device_path,
                    'backend_type': backend_type,
                    'backend_path': backend_path
                })

    except Exception:
        pass

    return tpm_info

def get_vm_rng_info(root: ET.Element) -> dict:
    """
    Extracts RNG (Random Number Generator) information from a VM's XML definition.
    Returns a dictionary with RNG details.
    """
    rng_info = {
        'rng_model': None,
        'backend_model': None,
        'backend_path': None,
    }
    if root is None:
        return rng_info
    try:
        devices = root.find("devices")

        if devices is not None:
            rng_elem = devices.find("./rng")
            if rng_elem is not None:
                rng_info['rng_model'] = rng_elem.get('model')

                backend_elem = rng_elem.find('backend')
                if backend_elem is not None:
                    rng_info['backend_model'] = backend_elem.get('model')

                    if rng_info['backend_model'] == 'random':
                        rng_info['backend_path'] = backend_elem.text
                    else:
                        source_elem = backend_elem.find('source')
                        if source_elem is not None:
                            rng_info['backend_path'] = source_elem.get('path')

    except Exception:
        pass

    return rng_info

def get_vm_watchdog_info(root: ET.Element) -> dict:
    """
    Extracts Watchdog information from a VM's XML definition.
    Returns a dictionary with Watchdog details.
    """
    watchdog_info = {
        'model': None,
        'action': None
    }
    if root is None:
        return watchdog_info

    try:
        devices = root.find("devices")

        if devices is not None:
            watchdog_elem = devices.find("./watchdog")
            if watchdog_elem is not None:
                watchdog_info['model'] = watchdog_elem.get('model')
                watchdog_info['action'] = watchdog_elem.get('action')

    except Exception:
        pass

    return watchdog_info

def get_vm_input_info(root: ET.Element) -> list[dict]:
    """
    Extracts Input (keyboard and mouse) information from a VM's XML definition.
    Returns a list of dictionaries with input device details.
    """
    input_info = []
    if root is None:
        return input_info

    try:
        devices = root.find("devices")

        if devices is not None:
            for input_elem in devices.findall("./input"):
                input_type = input_elem.get('type')
                input_bus = input_elem.get('bus')

                input_details = {
                    'type': input_type,
                    'bus': input_bus
                }

                # Add specific details for different input types
                if input_type == 'tablet':
                    tablet_elem = input_elem.find('tablet')
                    if tablet_elem is not None:
                        input_details['tablet'] = True
                elif input_type == 'mouse' or input_type == 'keyboard':
                    # Mouse and keyboard devices might have specific properties
                    pass  # Add more specific handling if needed

                input_info.append(input_details)

    except Exception:
        pass

    return input_info

def get_vm_graphics_info(root: ET.Element) -> dict:
    """
    Extracts graphics information (VNC/Spice) from a VM's XML definition.
    Returns a dictionary with graphics details.
    """
    graphics_info = {
        'type': "",
        'listen_type': 'none',  # 'none' or 'address'
        'address': '0.0.0.0', # Default to all interfaces
        'port': None,
        'autoport': True,
        'password_enabled': False,
        'password': None,
    }

    if root is None:
        return graphics_info

    try:
        devices = root.find('devices')
        if devices is None:
            return graphics_info

        graphics_elem = devices.find('graphics')
        if graphics_elem is None:
            return graphics_info

        graphics_type = graphics_elem.get('type')
        if graphics_type not in ['vnc', 'spice']:
            return graphics_info

        graphics_info['type'] = graphics_type
        graphics_info['port'] = graphics_elem.get('port')
        graphics_info['autoport'] = graphics_elem.get('autoport') != 'no'

        listen_elem = graphics_elem.find('listen')
        if listen_elem is not None:
            listen_type = listen_elem.get('type')
            if listen_type in ['address', 'network']: # 'network' is deprecated but might be found
                graphics_info['listen_type'] = 'address'
                graphics_info['address'] = listen_elem.get('address', '0.0.0.0')
            else: # 'none' (default), 'socket' (not exposed in UI)
                graphics_info['listen_type'] = 'none'
                graphics_info['address'] = '' # Clear address if listen type is none

        if graphics_elem.get('passwd'):
            graphics_info['password_enabled'] = True
            graphics_info['password'] = graphics_elem.get('passwd') # Note: libvirt XML may not store password

    except Exception:
        pass

    return graphics_info

def check_for_spice_vms(conn):
    """
    Checks if any VM uses Spice graphics.
    Returns a message if a Spice VM is found, otherwise None.
    Optimized to fetch VM XMLs in parallel.
    """
    if not conn:
        return None
    try:
        all_domains = conn.listAllDomains(0) or []
    except libvirt.libvirtError:
        return None

    def check_domain_for_spice(domain):
        """Helper to check a single domain for Spice graphics."""
        try:
            _, root = _get_domain_root(domain)
            if root is not None:
                graphics_info = get_vm_graphics_info(root)
                if graphics_info.get("type") == "spice":
                    return True
        except Exception:
            pass
        return False

    # Use ThreadPoolExecutor for parallel processing
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = executor.map(check_domain_for_spice, all_domains)
        # Check if any domain uses Spice
        if any(results):
            return "Some VMs use Spice graphics. 'Web Console' is only available for VNC."

    return None

def get_all_network_usage(conn: libvirt.virConnect) -> dict[str, list[str]]:
    """
    Scans all VMs and returns a mapping of network name to a list of VM names using it.
    Optimized to fetch VM XMLs in parallel.
    """
    network_to_vms = {}
    if not conn:
        return network_to_vms

    try:
        domains = conn.listAllDomains(0)
    except libvirt.libvirtError:
        return network_to_vms

    def process_domain(domain):
        """Helper to process a single domain."""
        try:
            _, root = _get_domain_root(domain)
            if root is not None:
                return domain.name(), get_vm_networks_info(root)
        except Exception:
            pass
        return None, []

    # Use a ThreadPoolExecutor to fetch XMLs in parallel
    # Limit max_workers to a reasonable number (e.g., 20) to balance speed and resource usage
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = executor.map(process_domain, domains)

    for vm_name, networks in results:
        if vm_name:
            for net in networks:
                net_name = net.get('network')
                if net_name:
                    if net_name not in network_to_vms:
                        network_to_vms[net_name] = []
                    if vm_name not in network_to_vms[net_name]:
                        network_to_vms[net_name].append(vm_name)

    return network_to_vms


def get_attached_usb_devices(root: ET.Element) -> list[dict]:
    """Gets all USB devices attached to the VM described by root."""
    attached_devices = []
    if root is None:
        return attached_devices
    try:
        for hostdev in root.findall(".//devices/hostdev[@type='usb']"):
            source = hostdev.find('source')
            vendor = source.find('vendor')
            product = source.find('product')
            if vendor is not None and product is not None:
                vendor_id = vendor.get('id')
                product_id = product.get('id')
                attached_devices.append({
                    "vendor_id": vendor_id,
                    "product_id": product_id,
                })
    except Exception as e:
        logging.error(f"Unexpected error getting attached USB devices: {e}")
    return attached_devices


def get_serial_devices(root: ET.Element) -> list[dict]:
    """
    Extracts serial and console device information from a VM's XML definition.
    """
    devices = []
    if root is None:
        return devices
    try:
        # Find serial devices
        for serial in root.findall(".//devices/serial"):
            dev_type = serial.get('type')
            target = serial.find('target')
            port = target.get('port') if target is not None else 'N/A'
            devices.append({
                'device': 'serial',
                'type': dev_type,
                'port': port,
                'details': f"Type: {dev_type}, Port: {port}"
            })
        # Find console devices
        for console in root.findall(".//devices/console"):
            dev_type = console.get('type')
            target = console.find('target')
            target_type = target.get('type') if target is not None else 'N/A'
            port = target.get('port') if target is not None else 'N/A'
            devices.append({
                'device': 'console',
                'type': dev_type,
                'port': port,
                'details': f"Type: {dev_type}, Target: {target_type} on port {port}"
            })
    except Exception as e:
        logging.error(f"Error getting serial devices: {e}")
    return devices

def get_attached_pci_devices(root: ET.Element) -> list[dict]:
    """
    Parses the VM XML description and returns a list of attached PCI devices (hostdev).
    """
    attached_pci_devices = []
    if root is None:
        return attached_pci_devices
    try:
        # Find all hostdev devices with a PCI address
        for hostdev_elem in root.findall(".//devices/hostdev[@type='pci']"):
            source_elem = hostdev_elem.find('source')
            if source_elem is not None:
                address_elem = source_elem.find('address')
                if address_elem is not None:
                    domain = address_elem.get('domain')
                    bus = address_elem.get('bus')
                    slot = address_elem.get('slot')
                    function = address_elem.get('function')
                    if all([domain, bus, slot, function]):
                        pci_address = f"{int(domain, 16):04x}:{int(bus, 16):02x}:{int(slot, 16):02x}.{int(function, 16):01x}"
                        attached_pci_devices.append({
                            'pci_address': pci_address,
                            'source_xml': ET.tostring(hostdev_elem, encoding='unicode')
                        })
    except Exception as e:
        logging.error(f"Unexpected error getting attached PCI devices: {e}")
    return attached_pci_devices

def get_vm_snapshots(domain: libvirt.virDomain) -> list[dict]:
    """
    Get all snapshots for a VM with details.
    """
    snapshots_info = []
    try:
        snapshots = domain.listAllSnapshots(0)
        for snapshot in snapshots:
            xml_desc = snapshot.getXMLDesc(0)
            root = ET.fromstring(xml_desc)

            name = root.findtext("name")
            description = root.findtext("description") or ""
            creation_time = root.findtext("creationTime")
            state = root.findtext("state")

            # Convert timestamp to readable date
            if creation_time:
                try:
                    import datetime
                    ts = float(creation_time)
                    creation_time = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    pass

            snapshots_info.append({
                "name": name,
                "description": description,
                "creation_time": creation_time,
                "state": state,
                "snapshot_object": snapshot # Keep the object for operations
            })
    except (libvirt.libvirtError, ET.ParseError) as e:
        logging.error(f"Error getting snapshots: {e}")

    # Sort by creation time descending (newest first)
    snapshots_info.sort(key=lambda x: x.get('creation_time', ''), reverse=True)

    return snapshots_info

def get_overlay_disks(domain: libvirt.virDomain) -> list[str]:
    """
    Returns a list of disk paths that are overlays.
    Checks both domain XML and underlying volume XML.
    """
    overlay_disks = []
    try:
        conn = domain.connect()
        if conn is None:
             return []

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)

        for disk in root.findall(".//disk"):
            # Get path first as we need it for return value and volume lookup
            path = None
            source = disk.find("source")
            if source is not None:
                if "file" in source.attrib:
                    path = source.attrib["file"]
                elif "dev" in source.attrib:
                    path = source.attrib["dev"]
                elif "pool" in source.attrib and "volume" in source.attrib:
                    # Resolve pool/vol
                    try:
                        pool = conn.storagePoolLookupByName(source.attrib["pool"])
                        vol = pool.storageVolLookupByName(source.attrib["volume"])
                        path = vol.path()
                    except:
                        pass

            if not path:
                continue

            is_overlay = False

            # 1. Check Domain XML (Runtime or Explicit)
            backing = disk.find("backingStore")
            if backing is not None:
                if backing.find("source") is not None or backing.get("type"):
                    is_overlay = True

            # 2. Check Volume XML (Persistent/Storage level)
            if not is_overlay:
                # If we have a path, check the volume
                vol, _ = _find_vol_by_path(conn, path)
                if vol:
                    try:
                        vol_xml = vol.XMLDesc(0)
                        vol_root = ET.fromstring(vol_xml)
                        vol_backing = vol_root.find("backingStore")
                        if vol_backing is not None and vol_backing.find("path") is not None:
                            is_overlay = True
                    except:
                        pass

            # 3. Check VM Metadata (Custom tracking)
            if not is_overlay:
                backing_chain_elem = _get_backing_chain_elem(root)
                if backing_chain_elem is not None:
                    for entry in backing_chain_elem.findall(f'{{{VIRTUI_MANAGER_NS}}}overlay'):
                        if entry.get('path') == path:
                            is_overlay = True
                            break

            if is_overlay:
                overlay_disks.append(path)

        return overlay_disks
    except Exception:
        return []

def has_overlays(domain: libvirt.virDomain) -> bool:
    """
    Checks if the VM has any disks that are overlays.
    """
    return len(get_overlay_disks(domain)) > 0


def is_qemu_agent_running(domain: libvirt.virDomain) -> bool:
    """
    Checks if the QEMU guest agent is configured and likely running (VM is active).
    This doesn't guarantee the agent inside the guest is actually up, but that the channel exists
    and the VM is on.
    """
    if domain.isActive() == 0:
        return False

    try:
        _, root = _get_domain_root(domain)
        if root is None:
            return False

        devices_info = get_vm_devices_info(root)
        if devices_info.get('qemu_guest_agent'):
            return True

    except Exception:
        pass

    return False

def get_vm_cputune(root: ET.Element) -> dict:
    """
    Extracts cputune information from a VM's XML definition.
    """
    cputune_info = {
        'vcpupin': []
    }
    if root is None:
        return cputune_info

    try:
        cputune = root.find('cputune')
        if cputune is not None:
            for vcpupin in cputune.findall('vcpupin'):
                cputune_info['vcpupin'].append({
                    'vcpu': vcpupin.get('vcpu'),
                    'cpuset': vcpupin.get('cpuset')
                })
            # Sort by vcpu id
            cputune_info['vcpupin'].sort(key=lambda x: int(x['vcpu']))
    except Exception:
        pass

    return cputune_info

def get_vm_numatune(root: ET.Element) -> dict:
    """
    Extracts numatune information from a VM's XML definition.
    """
    numatune_info = {
        'memory': {'mode': 'strict', 'nodeset': ''}
    }
    if root is None:
        return numatune_info

    try:
        numatune = root.find('numatune')
        if numatune is not None:
            memory = numatune.find('memory')
            if memory is not None:
                numatune_info['memory'] = {
                    'mode': memory.get('mode', 'strict'),
                    'nodeset': memory.get('nodeset', '')
                }
    except Exception:
        pass

    return numatune_info

def get_domain_info_dict(domain: libvirt.virDomain, conn: libvirt.virConnect) -> dict:
    """
    Retrieves comprehensive information about a domain and returns it as a dictionary.
    This is useful for initializing VM detail views without relying on VMService caching.
    """
    try:
        xml_content, root = _get_domain_root(domain)
        state, _ = domain.state()
        info = domain.info()

        # Helper to get internal ID if possible, otherwise simple UUID
        internal_id = domain.UUIDString()
        try:
            uri = conn.getURI()
            if uri:
                internal_id = f"{internal_id}@{uri}"
        except:
            pass

        vm_info = {
            'name': domain.name(),
            'uuid': domain.UUIDString(),
            'internal_id': internal_id,
            'status': get_status(domain, state=state),
            'description': get_vm_description(domain),
            'cpu': info[3],
            'cpu_model': get_vm_cpu_model(root),
            'memory': info[1] // 1024,
            'machine_type': get_vm_machine_info(root),
            'firmware': get_vm_firmware_info(root),
            'shared_memory': get_vm_shared_memory_info(root),
            'networks': get_vm_networks_info(root),
            'detail_network': get_vm_network_ip(domain),
            'network_dns_gateway': get_vm_network_dns_gateway_info(domain, root=root),
            'disks': get_vm_disks_info(conn, root),
            'devices': get_vm_devices_info(root),
            'boot': get_boot_info(conn, root),
            'video_model': get_vm_video_model(root),
            'xml': xml_content,
        }
        return vm_info
    except libvirt.libvirtError as e:
        logging.error(f"Error getting domain info dict: {e}")
        return {}
