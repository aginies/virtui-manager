"""
Utility functions for libvirt XML parsing and common helpers.
"""
import xml.etree.ElementTree as ET
import libvirt
from vm_queries import get_vm_graphics_info, get_vm_networks_info
from utils import log_function_call

VMANAGER_NS = "http://github.com/aginies/vmanager"
ET.register_namespace("vmanager", VMANAGER_NS)

@log_function_call
def _find_vol_by_path(conn, vol_path):
    """Finds a storage volume by its path and returns the volume and its pool."""
    # Slower but more compatible way to find a volume by path
    try:
        all_pool_names = conn.listStoragePools() + conn.listDefinedStoragePools()
    except libvirt.libvirtError:
        all_pool_names = []

    for pool_name in all_pool_names:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
            if not pool.isActive():
                try:
                    pool.create(0)
                except libvirt.libvirtError:
                    continue  # Skip pools we can't start

            # listAllVolumes returns a list of virStorageVol objects
            for vol in pool.listAllVolumes():
                if vol and vol.path() == vol_path:
                    return vol, pool
        except libvirt.libvirtError:
            continue # Permissions issue or other problem, try next pool
    return None, None

def _get_vmanager_metadata(root):
    metadata_elem = root.find('metadata')
    if metadata_elem is None:
        metadata_elem = ET.SubElement(root, 'metadata')

    vmanager_meta_elem = metadata_elem.find(f'{{{VMANAGER_NS}}}vmanager')
    if vmanager_meta_elem is None:
        vmanager_meta_elem = ET.SubElement(metadata_elem, f'{{{VMANAGER_NS}}}vmanager')

    return vmanager_meta_elem

def _get_disabled_disks_elem(root):
    vmanager_meta_elem = _get_vmanager_metadata(root)
    disabled_disks_elem = vmanager_meta_elem.find(f'{{{VMANAGER_NS}}}disabled-disks')
    if disabled_disks_elem is None:
        disabled_disks_elem = ET.SubElement(vmanager_meta_elem, f'{{{VMANAGER_NS}}}disabled-disks')
    return disabled_disks_elem

@log_function_call
def get_cpu_models(conn, arch):
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

@log_function_call
def get_all_network_usage(conn: libvirt.virConnect) -> dict[str, list[str]]:
    """
    Scans all VMs and returns a mapping of network name to a list of VM names using it.
    """
    network_to_vms = {}
    if not conn:
        return network_to_vms

    try:
        domains = conn.listAllDomains(0)
    except libvirt.libvirtError:
        return network_to_vms

    for domain in domains:
        try:
            xml_desc = domain.XMLDesc(0)
            vm_name = domain.name()
            # get_vm_networks_info is already in vm_queries.py
            networks = get_vm_networks_info(xml_desc)
            for net in networks:
                net_name = net.get('network')
                if net_name:
                    if net_name not in network_to_vms:
                        network_to_vms[net_name] = []
                    if vm_name not in network_to_vms[net_name]:
                        network_to_vms[net_name].append(vm_name)
        except libvirt.libvirtError:
            continue

    return network_to_vms

@log_function_call
def check_for_spice_vms(conn):
    """
    Checks if any VM uses Spice graphics.
    Returns a message if a Spice VM is found, otherwise None.
    """
    if not conn:
        return None
    try:
        all_domains = conn.listAllDomains(0) or []
        for domain in all_domains:
            xml_content = domain.XMLDesc(0)
            graphics_info = get_vm_graphics_info(xml_content)
            if graphics_info.get("type") == "spice":
                return "Some VMs use Spice graphics. 'Web Console' is only available for VNC."
    except libvirt.libvirtError:
        pass
    return None


