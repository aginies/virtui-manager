"""
Utility functions for libvirt XML parsing and common helpers.
"""
import logging
import xml.etree.ElementTree as ET
from functools import lru_cache

import libvirt

VIRTUI_MANAGER_NS = "http://github.com/aginies/virtui-manager"
ET.register_namespace("virtui-manager", VIRTUI_MANAGER_NS)


def get_internal_id(domain: libvirt.virDomain, conn: libvirt.virConnect = None) -> str:
    """
    Constructs the internal ID (UUID@URI) for a given domain.
    """
    if not conn:
        conn = domain.connect()
    try:
        uri = conn.getURI()
    except libvirt.libvirtError:
        uri = "unknown" # Should not happen if connection is valid

    try:
        uuid_str = domain.UUIDString()
    except libvirt.libvirtError:
        uuid_str = "unknown"

    return f"{uuid_str}@{uri}"


def _find_vol_by_path(conn: libvirt.virConnect, vol_path):
    """Finds a storage volume by its path and returns the volume and its pool."""
    try:
        # Get only active pools first (faster)
        active_pool_names = conn.listStoragePools()

        # Check active pools first (most common case)
        for pool_name in active_pool_names:
            try:
                pool = conn.storagePoolLookupByName(pool_name)
                # listAllVolumes returns a list of virStorageVol objects
                for vol in pool.listAllVolumes():
                    if vol and vol.path() == vol_path:
                        return vol, pool
            except libvirt.libvirtError:
                continue

        # Only check inactive pools if needed (rare case)
        inactive_pool_names = conn.listDefinedStoragePools()
        for pool_name in inactive_pool_names:
            try:
                pool = conn.storagePoolLookupByName(pool_name)
                # Only activate if we need to check volumes
                if pool.isActive():
                    for vol in pool.listAllVolumes():
                        if vol and vol.path() == vol_path:
                            return vol, pool
            except libvirt.libvirtError:
                continue

    except libvirt.libvirtError:
        pass
    return None, None

def _get_vmanager_metadata(root):
    metadata_elem = root.find('metadata')
    if metadata_elem is None:
        metadata_elem = ET.SubElement(root, 'metadata')

    vmanager_meta_elem = metadata_elem.find(f'{{{VIRTUI_MANAGER_NS}}}virtuimanager')
    if vmanager_meta_elem is None:
        vmanager_meta_elem = ET.SubElement(metadata_elem, f'{{{VIRTUI_MANAGER_NS}}}virtuimanager')

    return vmanager_meta_elem

def _get_metadata_elem(root, elem_name):
    vmanager_meta_elem = _get_vmanager_metadata(root)
    elem = vmanager_meta_elem.find(f'{{{VIRTUI_MANAGER_NS}}}{elem_name}')
    if elem is None:
        elem = ET.SubElement(vmanager_meta_elem, f'{{{VIRTUI_MANAGER_NS}}}{elem_name}')
    return elem

def _get_disabled_disks_elem(root):
    return _get_metadata_elem(root, 'disabled-disks')

def _get_backing_chain_elem(root):
    return _get_metadata_elem(root, 'backing-chain')

def get_overlay_backing_path(root, overlay_path):
    """
    Retrieves the backing path for a given overlay path from the metadata.
    """
    backing_chain_elem = _get_backing_chain_elem(root)
    if backing_chain_elem is not None:
        for overlay in backing_chain_elem.findall(f'{{{VIRTUI_MANAGER_NS}}}overlay'):
            if overlay.get('path') == overlay_path:
                return overlay.get('backing')
    return None

def _find_pool_by_path(conn: libvirt.virConnect, file_path: str):
    """
    Finds an active storage pool that contains or manages the given file path.
    """
    for pool_name in conn.listStoragePools():
        try:
            pool = conn.storagePoolLookupByName(pool_name)
            if not pool.isActive():
                continue
            pool_info = ET.fromstring(pool.XMLDesc(0))
            source_path = pool_info.findtext("source/directory") or pool_info.findtext("target/path")
            if source_path and file_path.startswith(source_path):
                return pool
        except libvirt.libvirtError:
            continue
    return None

def get_cpu_models(conn: libvirt.virConnect, arch: str):
    """
    Get a list of CPU models for a given architecture.
    """
    if not conn:
        return []
    try:
        # Returns a list of supported CPU model names
        models = conn.getCPUModelNames(arch)
        return models
    except libvirt.libvirtError as e:
        print(f"Error getting CPU models for arch {arch}: {e}")
        return []

def get_host_resources(conn: libvirt.virConnect) -> dict:
    """
    Retrieves host resource information (CPU, Memory).
    """
    try:
        node_info = conn.getInfo()
        # node_info: [model, memory (KB), cpus, mhz, nodes, sockets, cores, threads]
        mem_stats = conn.getMemoryStats(libvirt.VIR_NODE_MEMORY_STATS_ALL_CELLS)
        # mem_stats might have: total, free, buffers, cached
        host_info = {
            'model': node_info[0],
            'total_memory': node_info[1] // 1024, # MB
            'total_cpus': node_info[2],
            'mhz': node_info[3],
            'nodes': node_info[4],
            'sockets': node_info[5],
            'cores': node_info[6],
            'threads': node_info[7],
            'free_memory': mem_stats.get('free', 0) // 1024, # MB
            'available_memory': mem_stats.get('total', 0) // 1024, # MB
        }

        if host_info['available_memory'] == 0:
            host_info['available_memory'] = host_info['total_memory']

        return host_info
    except libvirt.libvirtError as e:
        logging.error(f"Error getting host resources: {e}")
        return {}

def get_total_vm_allocation(conn: libvirt.virConnect, progress_callback=None) -> dict:
    """
    Calculates total resource allocation across all running or paused VMs on a host.
    """
    total_memory = 0
    total_vcpus = 0
    active_memory = 0
    active_vcpus = 0

    try:
        domains = conn.listAllDomains(0)
        total_vms = len(domains)

        for i, domain in enumerate(domains):
            if progress_callback:
                progress_callback(i + 1, total_vms)

            try:
                # domain.info() returns [state, maxMem, memory, nrVirtCpu, cpuTime]
                # memory is in Kilobytes
                info = domain.info()
                state = info[0]
                max_mem = info[1]  # KB
                n_cpus = info[3]

                total_memory += max_mem
                total_vcpus += n_cpus

                if state in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]:
                    active_memory += max_mem
                    active_vcpus += n_cpus

            except libvirt.libvirtError:
                continue

        return {
            'total_allocated_memory': total_memory // 1024,  # Convert to MB
            'total_allocated_vcpus': total_vcpus,
            'active_allocated_memory': active_memory // 1024,  # Convert to MB
            'active_allocated_vcpus': active_vcpus,
        }
    except libvirt.libvirtError as e:
        logging.error(f"Error calculating VM allocation: {e}")
        return {}

def get_active_vm_allocation(conn: libvirt.virConnect, progress_callback=None) -> dict:
    """
    Calculates resource allocation for only active (running/paused) VMs.
    More efficient than get_total_vm_allocation for large numbers of inactive VMs.
    """
    active_memory = 0
    active_vcpus = 0

    try:
        # Only list active domains
        domains = conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE)
        total_vms = len(domains)

        for i, domain in enumerate(domains):
            if progress_callback:
                progress_callback(i + 1, total_vms)

            try:
                info = domain.info()
                max_mem = info[1]  # KB
                n_cpus = info[3]

                active_memory += max_mem
                active_vcpus += n_cpus

            except libvirt.libvirtError:
                continue

        return {
            'active_allocated_memory': active_memory // 1024,  # Convert to MB
            'active_allocated_vcpus': active_vcpus,
        }
    except libvirt.libvirtError as e:
        logging.error(f"Error calculating active VM allocation: {e}")
        return {}

def get_host_architecture(conn: libvirt.virConnect) -> str:
    """
    Returns the host architecture (e.g., 'x86_64', 'aarch64').
    """
    try:
        # getCapabilities returns an XML string describing the host capabilities
        caps_xml = get_host_domain_capabilities(conn)
        if not caps_xml:
            return 'x86_64'
        root = ET.fromstring(caps_xml)

        arch = root.findtext('host/cpu/arch')
        if arch:
            return arch

        # in case of failure check guest
        guest_arch = root.findtext('guest/arch[@name]')
        if guest_arch:
            return guest_arch

    except libvirt.libvirtError as e:
        logging.error(f"Error getting host architecture: {e}")
    except ET.ParseError as e:
        logging.error(f"Error parsing capabilities XML: {e}")

    return 'x86_64'

def find_all_vm(conn: libvirt.virConnect):
    """
    Find all VM from the current Hypervisor
    """
    allvm_list = []
    # Store all VM from the hypervisor
    domains = conn.listAllDomains(0)
    for domain in domains:
        if domain.name():
            vmdomain = domain.name()
            allvm_list.append(vmdomain)
    return allvm_list

@lru_cache(maxsize=4)
def get_domain_capabilities_xml(
    conn: libvirt.virConnect,
    emulatorbin: str,
    arch: str,
    machine: str,
    flags: int = 0
) -> str | None:
    """
    Retrieves the domain capabilities XML for a specific guest configuration.
    """
    try:
        caps_xml = conn.getDomainCapabilities(
            emulatorbin=emulatorbin,
            arch=arch,
            machine=machine,
            flags=flags
        )
        return caps_xml
    except libvirt.libvirtError as e:
        logging.error(f"Error getting domain capabilities: {e}")
        return None

def get_video_domain_capabilities(xml_content: str) -> dict:
    """
    Parses the domain capabilities XML to extract supported video
    """
    supported_models = {
        'video_models': [],
    }

    if not xml_content:
        return supported_models

    try:
        root = ET.fromstring(xml_content)

        # Extract supported video models
        for video_elem in root.findall(".//video[@supported='yes']/enum[@name='modelType']"):
            for value_elem in video_elem.findall('value'):
                if value_elem.text:
                    supported_models['video_models'].append(value_elem.text)

    except ET.ParseError as e:
        logging.error(f"Error parsing domain capabilities XML: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during XML parsing: {e}")

    return supported_models

def get_sound_domain_capabilities(xml_content: str) -> dict:
    """
    Parses the domain capabilities XML to extract supported sound models.
    """
    supported_models = {
        'sound_models': [],
    }

    if not xml_content:
        return supported_models

    try:
        root = ET.fromstring(xml_content)

        # Extract supported sound models
        for sound_elem in root.findall(".//sound[@supported='yes']/enum[@name='model']"):
            for value_elem in sound_elem.findall('value'):
                if value_elem.text:
                    supported_models['sound_models'].append(value_elem.text)

    except ET.ParseError as e:
        logging.error(f"Error parsing domain capabilities XML: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during XML parsing: {e}")

    return supported_models

def _get_vm_names_from_uuids(conn: libvirt.virConnect, vm_uuids: list[str]) -> list[str]:
    """
    Get VM name from their vm_uuids
    """
    vm_names = []
    for uuid in vm_uuids:
        try:
            domain = conn.lookupByUUIDString(uuid)
            vm_names.append(domain.name())
        except libvirt.libvirtError:
            pass
    return vm_names


def get_network_info(conn: libvirt.virConnect, network_name: str) -> dict:
    """
    Get detailed information about a specific network based on its name.
    Extracts forward mode, port, bridge, network, and DHCP information.
    """
    try:
        network = conn.networkLookupByName(network_name)
        xml_desc = network.XMLDesc(0)
        root = ET.fromstring(xml_desc)

        info = {
            'name': network.name(),
            'uuid': network.UUIDString(),
            'forward_mode': 'isolated',  # Default
            'bridge_name': None,
            'ip_address': None,
            'dhcp': False
        }

        # Extract forward info
        forward_elem = root.find('forward')
        if forward_elem is not None:
            info['forward_mode'] = forward_elem.get('mode', 'isolated')
            info['forward_dev'] = forward_elem.get('dev') or (
                forward_elem.find('interface').get('dev') if forward_elem.find('interface') is not None else None
            )

            # NAT port range
            nat_elem = forward_elem.find('nat')
            if nat_elem is not None:
                port_elem = nat_elem.find('port')
                if port_elem is not None:
                    info['port_forward_start'] = port_elem.get('start')
                    info['port_forward_end'] = port_elem.get('end')

        # Extract bridge info
        bridge_elem = root.find('bridge')
        if bridge_elem is not None:
            info['bridge_name'] = bridge_elem.get('name')

        # Extract IP info
        ip_elem = root.find('ip')
        if ip_elem is not None:
            info.update({
                'ip_address': ip_elem.get('address'),
                'netmask': ip_elem.get('netmask'),
                'prefix': ip_elem.get('prefix'),
                'dhcp': ip_elem.find('dhcp') is not None
            })

            if info['dhcp']:
                dhcp_elem = ip_elem.find('dhcp')
                if dhcp_elem is not None:
                    range_elem = dhcp_elem.find('range')
                    if range_elem is not None:
                        info.update({
                            'dhcp_start': range_elem.get('start'),
                            'dhcp_end': range_elem.get('end')
                        })

        # Extract domain name
        domain_elem = root.find('domain')
        if domain_elem is not None:
            info['domain_name'] = domain_elem.get('name')
        return info

    except libvirt.libvirtError:
        return {}

def get_host_numa_nodes(conn: libvirt.virConnect) -> int:
    """
    Returns the number of NUMA nodes on the host.
    """
    caps_xml = get_host_domain_capabilities(conn)
    if not caps_xml:
        return 1
    try:
        root = ET.fromstring(caps_xml)
        cells = root.findall(".//host/topology/cells/cell")
        return len(cells) if cells else 1
    except ET.ParseError as e:
        logging.error(f"Error getting host NUMA topology: {e}")
    return 1

@lru_cache(maxsize=4)
def get_host_usb_devices(conn: libvirt.virConnect) -> list[dict]:
    """Gets all USB devices from the host."""
    usb_devices = []
    try:
        devices = conn.listAllDevices(0)
        for dev in devices:
            try:
                xml_desc = dev.XMLDesc(0)
                root = ET.fromstring(xml_desc)
                if root.find("capability[@type='usb_device']") is not None:
                    capability = root.find("capability[@type='usb_device']")
                    vendor_elem = capability.find('vendor')
                    product_elem = capability.find('product')
                    vendor_id = vendor_elem.get('id') if vendor_elem is not None else None
                    product_id = product_elem.get('id') if product_elem is not None else None

                    if not vendor_id or not product_id:
                        continue

                    product_name = "Unknown"
                    if product_elem is not None and product_elem.text:
                        product_name = product_elem.text.strip()

                    vendor_name = "Unknown"
                    if vendor_elem is not None and vendor_elem.text:
                        vendor_name = vendor_elem.text.strip()

                    usb_devices.append({
                        "name": dev.name(),
                        "vendor_id": vendor_id,
                        "product_id": product_id,
                        "vendor_name": vendor_name,
                        "product_name": product_name,
                        "description": f"{vendor_name} - {product_name} ({vendor_id}:{product_id})"
                    })
            except libvirt.libvirtError as e:
                logging.warning(f"Skipping device {dev.name() if hasattr(dev, 'name') else 'unknown'}: {e}")
                continue
    except libvirt.libvirtError as e:
        logging.error(f"Error getting host USB devices: {e}")
    return usb_devices

@lru_cache(maxsize=4)
def get_host_pci_devices(conn: libvirt.virConnect) -> list[dict]:
    """Gets all PCI devices from the host that are available for passthrough."""
    pci_devices = []
    try:
        # Filter only PCI devices
        devices = conn.listAllDevices(libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_PCI_DEV)
        for dev in devices:
            try:
                xml_desc = dev.XMLDesc(0)
                root = ET.fromstring(xml_desc)
                capability = root.find("capability[@type='pci']")
                if capability is not None:
                    vendor_elem = capability.find('vendor')
                    product_elem = capability.find('product')
                    address_elem = capability.find('address')

                    vendor_id = vendor_elem.get('id') if vendor_elem is not None else None
                    product_id = product_elem.get('id') if product_elem is not None else None

                    if not vendor_id or not product_id:
                        continue

                    product_name = "Unknown"
                    if product_elem is not None and product_elem.text:
                        product_name = product_elem.text.strip()

                    vendor_name = "Unknown"
                    if vendor_elem is not None and vendor_elem.text:
                        vendor_name = vendor_elem.text.strip()

                    pci_address = None
                    if address_elem is not None:
                        domain = address_elem.get('domain')
                        bus = address_elem.get('bus')
                        slot = address_elem.get('slot')
                        function = address_elem.get('function')
                        if all([domain, bus, slot, function]):
                            pci_address = f"{int(domain, 16):04x}:{int(bus, 16):02x}:{int(slot, 16):02x}.{int(function, 16)}"

                    pci_devices.append({
                        "name": dev.name(),
                        "vendor_id": vendor_id,
                        "product_id": product_id,
                        "vendor_name": vendor_name,
                        "product_name": product_name,
                        "pci_address": pci_address,
                        "description": f"{vendor_name} - {product_name} ({pci_address})" if pci_address else f"{vendor_name} - {product_name} ({vendor_id}:{product_id})"
                    })
            except (libvirt.libvirtError, ET.ParseError) as e:
                logging.warning(f"Skipping device {dev.name() if hasattr(dev, 'name') else 'unknown'}: {e}")
                continue
    except (libvirt.libvirtError, AttributeError) as e:
        logging.error(f"Error getting host PCI devices: {e}")
    return pci_devices

@lru_cache(maxsize=8)
def get_host_domain_capabilities(conn: libvirt.virConnect) -> str | None:
    """
    Get the host capabilities XML (which describes host and guest capabilities).
    The result is cached per connection.
    """
    if not conn:
        return None
    try:
        return conn.getCapabilities()
    except libvirt.libvirtError as e:
        logging.error(f"Error getting host capabilities: {e}")
        return None
