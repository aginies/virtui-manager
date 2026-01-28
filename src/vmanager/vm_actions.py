"""
Module for performing actions and modifications on virtual machines.
"""
import os
import secrets
import uuid
import logging
import xml.etree.ElementTree as ET
import libvirt
from .libvirt_utils import (
        _find_vol_by_path,
        VIRTUI_MANAGER_NS,
        _get_disabled_disks_elem,
        _get_backing_chain_elem,
        get_overlay_backing_path,
        get_internal_id,
        get_host_domain_capabilities
        )
from .utils import log_function_call
from .vm_queries import get_vm_disks_info, get_vm_tpm_info, _get_domain_root, get_vm_snapshots
from .vm_cache import invalidate_cache
from .network_manager import list_networks
from .storage_manager import create_overlay_volume

def clone_vm(original_vm, new_vm_name, clone_storage=True, log_callback=None):
    """
    Clones a VM, including its storage using libvirt's storage pool API.
    If clone_storage is False, the new VM will reference the same storage volumes.
    """
    conn = original_vm.connect()
    original_xml = original_vm.XMLDesc(0)

    # Open a dedicated connection for the cloning operation to avoid blocking the main connection
    # which is used by the UI thread for stats updates.
    uri = conn.getURI()
    clone_conn = libvirt.open(uri)
    if not clone_conn:
        raise libvirt.libvirtError(f"Failed to open new connection to {uri} for cloning")

    try:
        root = ET.fromstring(original_xml)

        msg_start = f"Setting up new VM {new_vm_name}, cleaning some paramaters..."
        logging.info(msg_start)
        if log_callback:
            log_callback(msg_start)
        name_elem = root.find('name')
        if name_elem is not None:
            name_elem.text = new_vm_name

        uuid_elem = root.find('uuid')
        if uuid_elem is not None:
            uuid_elem.text = str(uuid.uuid4())

        for interface in root.findall('.//devices/interface'):
            mac_elem = interface.find('mac')
            if mac_elem is not None:
                interface.remove(mac_elem)

        if not clone_storage:
            msg_skip = f"Skipping storage cloning for VM {new_vm_name} (clone_storage=False)"
            logging.info(msg_skip)
            if log_callback:
                log_callback(msg_skip)

        for disk in root.findall('.//devices/disk'):
            if disk.get('device') != 'disk':
                continue

            source_elem = disk.find('source')
            if source_elem is None:
                continue

            # Skip storage cloning if clone_storage is False
            if not clone_storage:
                logging.info(f"Keeping original storage reference for disk: {ET.tostring(source_elem, encoding='unicode').strip()}")
                continue

            original_vol = None
            original_pool = None
            disk_type = disk.get('type')

            if disk_type == 'file':
                original_disk_path = source_elem.get('file')
                if original_disk_path:
                    # Use clone_conn to find volume
                    original_vol, original_pool = _find_vol_by_path(clone_conn, original_disk_path)
            elif disk_type == 'volume':
                pool_name = source_elem.get('pool')
                vol_name = source_elem.get('volume')
                if pool_name and vol_name:
                    try:
                        # Use clone_conn to lookup pool/volume
                        original_pool = clone_conn.storagePoolLookupByName(pool_name)
                        original_vol = original_pool.storageVolLookupByName(vol_name)
                    except libvirt.libvirtError as e:
                        logging.warning(f"Could not find volume '{vol_name}' in pool '{pool_name}'. Skipping disk clone. Error: {e}")
                        continue

            if not original_vol or not original_pool:
                logging.info(f"Skipping cloning for non-volume disk source: {ET.tostring(source_elem, encoding='unicode').strip()}")
                continue

            original_vol_xml = original_vol.XMLDesc(0)
            vol_root = ET.fromstring(original_vol_xml)

            _, vol_name_ext = os.path.splitext(original_vol.name())
            if vol_name_ext:
                # Ensure the extension starts with a dot
                if not vol_name_ext.startswith("."):
                    vol_name_ext = f".{vol_name_ext}"
            else:
                # Fallback if no extension found
                vol_name_ext = ".qcow2"

            new_vol_name = f"{new_vm_name}_{secrets.token_hex(4)}{vol_name_ext}"
            vol_root.find('name').text = new_vol_name

            # Libvirt will handle capacity, allocation, and backing store when cloning.
            # Clear old path/key info for the new volume
            if vol_root.find('key') is not None:
                 vol_root.remove(vol_root.find('key'))
            target_elem = vol_root.find('target')
            if target_elem is not None:
                if target_elem.find('path') is not None:
                    target_elem.remove(target_elem.find('path'))

            new_vol_xml = ET.tostring(vol_root, encoding='unicode')

            # Clone the volume using libvirt's storage pool API
            try:
                msg = f"Creating the new volume: {new_vol_name}"
                logging.info(msg)
                if log_callback:
                    log_callback(msg)
                # Flag 0 indicates a full (deep) clone
                new_vol = original_pool.createXMLFrom(new_vol_xml, original_vol, 0)
            except libvirt.libvirtError as e:
                raise libvirt.libvirtError(f"Failed to perform a full clone of volume '{original_vol.name()}': {e}")

            disk.set('type', 'volume')
            if 'file' in source_elem.attrib:
                del source_elem.attrib['file']
            source_elem.set('pool', original_pool.name())
            source_elem.set('volume', new_vol.name())

        new_xml = ET.tostring(root, encoding='unicode')
        msg_end = "Defining the VM..."
        logging.info(msg_end)
        if log_callback:
            log_callback(msg_end)

        # Define the VM using the clone connection
        new_vm_temp = clone_conn.defineXML(new_xml)
        new_vm_uuid = new_vm_temp.UUIDString()

    finally:
        if clone_conn:
            clone_conn.close()

    # Retrieve the new VM using the original connection to return a valid object
    # that is bound to the connection expected by the caller.
    new_vm = conn.lookupByUUIDString(new_vm_uuid)
    return new_vm

def rename_vm(domain, new_name, delete_snapshots=False):
    """
    Renames a VM.
    The VM must be stopped.
    If delete_snapshots is True, it will delete all snapshots before renaming.
    Handles NVRAM renaming if present.
    """
    if not domain:
        raise ValueError("Invalid domain object.")

    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to be renamed.")

    conn = domain.connect()

    if domain.name() == new_name:
        return  # It's already named this, do nothing.

    # Check for snapshots
    num_snapshots = domain.snapshotNum(0)
    snapshot_xmls = []
    if num_snapshots > 0:
        if delete_snapshots:
            for snapshot in domain.listAllSnapshots(0):
                snapshot.delete(0)
        else:
            # Capture snapshots to restore them later
            try:
                snapshots = get_vm_snapshots(domain)
                # Sort by creation time to ensure parentage is respected
                snapshots.sort(key=lambda x: x.get('creation_time', 0))
                
                for snap in snapshots:
                    snap_obj = snap['snapshot_object']
                    # Get the XML with security info
                    xml = snap_obj.getXMLDesc(libvirt.VIR_DOMAIN_SNAPSHOT_XML_SECURE)
                    snapshot_xmls.append(xml)
            except Exception as e:
                raise libvirt.libvirtError(f"Failed to retrieve snapshots for migration: {e}")

    # Check if a VM with the new name already exists
    try:
        conn.lookupByName(new_name)
        # If lookup succeeds, a VM with the new name already exists.
        raise libvirt.libvirtError(f"A VM with the name '{new_name}' already exists.")
    except libvirt.libvirtError as e:
        # "domain not found" is the expected error if the name is available.
        # We check the error code to be sure, as the error message string
        if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN:
            raise # Re-raise other libvirt errors.

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    # Check for NVRAM
    nvram_elem = root.find("./os/nvram")
    old_nvram_path = None
    new_nvram_path = None
    old_nvram_vol = None
    has_nvram = False

    if nvram_elem is not None:
        has_nvram = True
        old_nvram_path = nvram_elem.text
        # Try to rename volume
        if old_nvram_path:
            try:
                vol, pool = _find_vol_by_path(conn, old_nvram_path)
                if vol and pool:
                    old_nvram_vol = vol
                    _, ext = os.path.splitext(vol.name())
                    if not ext: ext = ".fd"
                    new_vol_name = f"{new_name}_VARS{ext}"
                    # Check if new volume name already exists
                    try:
                        pool.storageVolLookupByName(new_vol_name)
                        logging.warning(f"Target NVRAM volume {new_vol_name} already exists. Will use it.")
                    except libvirt.libvirtError:
                        # Clone old volume to new name
                        vol_xml_desc = vol.XMLDesc(0)
                        vol_root = ET.fromstring(vol_xml_desc)
                        vol_root.find('name').text = new_vol_name

                        # Clear keys/paths
                        if vol_root.find('key') is not None:
                            vol_root.remove(vol_root.find('key'))
                        target = vol_root.find('target')
                        if target is not None and target.find('path') is not None:
                            target.remove(target.find('path'))
              
                        new_vol_xml = ET.tostring(vol_root, encoding='unicode')
                        pool.createXMLFrom(new_vol_xml, vol, 0)
                        logging.info(f"Cloned NVRAM to {new_vol_name}")

                    # Get new path
                    new_vol = pool.storageVolLookupByName(new_vol_name)
                    new_nvram_path = new_vol.path()

                    # Update XML
                    nvram_elem.text = new_nvram_path

            except Exception as e:
                logging.warning(f"Failed to rename NVRAM volume, keeping old one: {e}")

    invalidate_cache(get_internal_id(domain))

    # Build flags for undefining the domain.
    undefine_flags = 0
    if has_nvram:
        # Keep NVRAM file temporarily during rename. It will be cleaned up later if the rename is successful.
        try:
            undefine_flags |= libvirt.VIR_DOMAIN_UNDEFINE_KEEP_NVRAM
        except AttributeError:
            pass # older libvirt

    if snapshot_xmls:
        # Also undefine snapshot metadata. They will be recreated for the new domain.
        try:
            undefine_flags |= libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
        except AttributeError:
            pass # older libvirt

    try:
        # Undefine the domain. Using undefineFlags if necessary.
        if undefine_flags:
            domain.undefineFlags(undefine_flags)
        else:
            domain.undefine()
    except AttributeError:
        # Fallback for older libvirt that doesn't support undefineFlags.
        # This will likely fail if there are snapshots.
        domain.undefine()
    except libvirt.libvirtError as e:
        # If undefine failed, re-raise with a more informative message.
        if "does not support flags" in str(e): # another older libvirt case
            domain.undefine()
        else:
            raise libvirt.libvirtError(f"Failed to undefine domain '{domain.name()}' during rename: {e}")

    try:
        # Modify XML with new name
        # We already modified root if NVRAM changed
        name_elem = root.find('name')
        if name_elem is None:
            msg = "Could not find name element in VM XML."
            logging.error(msg)
            raise Exception(msg)
        name_elem.text = new_name
        new_xml = ET.tostring(root, encoding='unicode')

        # Define the new domain from the modified XML
        conn.defineXML(new_xml)

        # Restore snapshots if any
        if snapshot_xmls:
            new_domain = conn.lookupByName(new_name)
            for snap_xml in snapshot_xmls:
                try:
                    # Update domain name in snapshot XML
                    snap_root = ET.fromstring(snap_xml)
                    # Update domain name in the snapshot's domain definition
                    domain_elem = snap_root.find("domain")
                    if domain_elem is not None:
                        d_name = domain_elem.find("name")
                        if d_name is not None:
                            d_name.text = new_name
                        # Update NVRAM path if it changed
                        if new_nvram_path:
                            nvram_elem = domain_elem.find("./os/nvram")
                            if nvram_elem is not None:
                                nvram_elem.text = new_nvram_path
                    # Update UUID in domain definition to match new domain's UUID (which we just defined)
                    # Actually, for a rename via defineXML, the UUID usually stays the same if we preserved it in root
                    # Let's ensure the snapshot XML uses the correct UUID if it's there
                    if domain_elem is not None:
                        d_uuid = domain_elem.find("uuid")
                        if d_uuid is not None:
                            # We didn't change the UUID in the main XML, so it should be fine.
                            # But if defineXML generated a new one (e.g. if we removed it), we'd need to update it.
                            # In rename_vm we use existing XML, so UUID is preserved.
                            pass

                    updated_snap_xml = ET.tostring(snap_root, encoding='unicode')

                    # Re-create snapshot for the new domain
                    new_domain.snapshotCreateXML(updated_snap_xml, 0)
                    logging.info(f"Re-created snapshot during rename for {new_name}")
                except Exception as e:
                    logging.error(f"Failed to re-create snapshot for renamed VM {new_name}: {e}")
        
        # If successful, delete old NVRAM volume if we cloned it        if old_nvram_vol and new_nvram_path and new_nvram_path != old_nvram_path:
        if old_nvram_vol and new_nvram_path and new_nvram_path != old_nvram_path:
            try:
                old_nvram_vol.delete(0)
                logging.info("Deleted old NVRAM volume")
            except Exception as e:
                logging.warning(f"Failed to delete old NVRAM volume: {e}")

    except Exception as e:
        # Try to restore old domain
        try:
            conn.defineXML(xml_desc)
            msg = f"Failed to rename VM, but restored original state. Error: {e}"
            logging.error(msg)
        except Exception as restore_error:
            msg = f"Failed to rename VM AND failed to restore original state! Error: {e}. Restore Error: {restore_error}"
            logging.critical(msg)
        raise Exception(msg) from e


def add_disk(domain, disk_path, device_type='disk', bus='virtio', create=False, size_gb=10, disk_format='qcow2'):
    """
    Adds a disk to a VM. Can optionally create a new disk image in a libvirt storage pool.
    device_type can be 'disk' or 'cdrom'
    bus can be 'virtio', 'ide', 'sata', 'scsi', 'usb'
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    invalidate_cache(get_internal_id(domain))

    conn = domain.connect()

    # Determine target device
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    def count_disks_on_bus(bus_name):
        c = 0
        for d in root.findall('.//disk'):
            target = d.find('target')
            if target is not None and target.get('bus') == bus_name:
                c += 1
        return c

    # This logic is more robust as it mimics libvirt's own device naming by
    # counting existing devices on a given bus, rather than relying on
    # potentially absent 'dev' attributes in the XML.
    if bus == 'virtio':
        prefix = 'vd'
        # Count virtio disks to determine the next index (e.g., if 1 exists, next is 'b')
        next_index = count_disks_on_bus('virtio')
    elif bus == 'ide':
        prefix = 'hd'
        next_index = count_disks_on_bus('ide')
    elif bus in ['sata', 'scsi', 'usb']:
        prefix = 'sd'
        # SATA, SCSI, USB, etc., share the 'sd' prefix namespace
        next_index = count_disks_on_bus('sata') + count_disks_on_bus('scsi') + count_disks_on_bus('usb')
    else:
        # Fallback for unknown bus types
        prefix = 'sd'
        next_index = 0 # Cannot reliably count, start with 'a'

    all_used_devs = {t.get("dev") for t in root.findall(".//disk/target") if t.get("dev")}

    # Generate device names like 'vda', 'vdb', ..., 'vdz', 'vdaa', 'vdab'
    while True:
        if next_index < 26:
            suffix = chr(ord('a') + next_index)
        else:
            # From 26 onwards, use two letters: aa, ab, ...
            major_index = (next_index - 26) // 26
            minor_index = (next_index - 26) % 26
            if major_index < 26: # Supports up to 'za' (701 devices)
                suffix = f"{chr(ord('a') + major_index)}{chr(ord('a') + minor_index)}"
            else: # Should be more than enough
                raise Exception("Exceeded maximum number of supported disk devices.")

        target_dev = f"{prefix}{suffix}"

        if target_dev not in all_used_devs:
            break

        # If the generated name is somehow already in use, increment and try the next one.
        next_index += 1

    if not target_dev:
        msg = "No available device slots for new disk."
        logging.error(msg)
        raise Exception(msg)

    disk_xml = ""

    if create:
        if device_type != 'disk':
            msg = "Cannot create non-disk device types."
            logging.error(msg)
            raise Exception(msg)

        # Find storage pool from path
        pool = None
        pools = conn.listAllStoragePools(0)
        for p in pools:
            if p.isActive():
                try:
                    p_xml = p.XMLDesc(0)
                    p_root = ET.fromstring(p_xml)
                    target_path = p_root.findtext("target/path")
                    if target_path and os.path.dirname(disk_path) == target_path:
                        pool = p
                        break
                except libvirt.libvirtError:
                    continue  # Some pools might not have paths, etc.

        if not pool:
            # Enhanced diagnostics: list all active pools with types
            active_pools_info = []
            for p in pools:
                if p.isActive():
                    try:
                        p_xml = p.XMLDesc(0)
                        p_root = ET.fromstring(p_xml)
                        pool_type = p_root.get('type', 'unknown')
                        target_path = p_root.findtext("target/path")
                        info = f"{p.name()} ({pool_type})"
                        if target_path:
                            info += f" at {target_path}"
                        active_pools_info.append(info)
                    except:
                        active_pools_info.append(f"{p.name()} (no XML)")

            pools_list = ", ".join(active_pools_info) if active_pools_info else "none"
            msg = (f"No dir-based pool found for '{os.path.dirname(disk_path)}'. "
                   f"Available active pools: {pools_list}. "
                   f"Use existing volume path or dir-based pool directory.")
            logging.error(msg)
            raise Exception(msg) from None

        vol_name = os.path.basename(disk_path)

        # Check if volume already exists
        try:
            pool.storageVolLookupByName(vol_name)
            msg = f"A storage volume named '{vol_name}' already exists in pool '{pool.name()}'."
            logging.error(msg)
            raise Exception(msg)
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_STORAGE_VOL:
                raise

        vol_xml_def = f"""
        <volume>
            <name>{vol_name}</name>
            <capacity unit="G">{size_gb}</capacity>
            <target>
                <format type='{disk_format}'/>
            </target>
        </volume>
        """
        try:
            new_vol = pool.createXML(vol_xml_def, 0)
        except libvirt.libvirtError as e:
            msg = f"Failed to create volume in libvirt pool: {e}"
            logging.error(msg)
            raise Exception(msg) from e

        disk_xml = f"""
        <disk type='volume' device='disk'>
            <driver name='qemu' type='{disk_format}'/>
            <source pool='{pool.name()}' volume='{new_vol.name()}'/>
            <target dev='{target_dev}' bus='{bus}'/>
        </disk>
        """
    else:  # not creating, just attaching
        if device_type == 'cdrom':
            disk_xml = f"""
            <disk type='file' device='cdrom'>
                <driver name='qemu' type='raw'/>
                <source file='{disk_path}'/>
                <target dev='{target_dev}' bus='{bus}'/>
                <readonly/>
            </disk>
            """
        else:  # device_type is 'disk'
            vol, _ = _find_vol_by_path(conn, disk_path)
            vol_format = disk_format
            if vol:
                try:
                    vol_xml_str = vol.XMLDesc(0)
                    vol_root = ET.fromstring(vol_xml_str)
                    format_elem = vol_root.find("target/format")
                    if format_elem is not None:
                        vol_format = format_elem.get('type')
                except (libvirt.libvirtError, ET.ParseError):
                    pass # use default disk_format

            # QEMU does not support 'iso' as a driver type for disks, use 'raw' instead
            if vol_format == 'iso':
                vol_format = 'raw'

            disk_xml = f"""
            <disk type='file' device='disk'>
                <driver name='qemu' type='{vol_format}' discard='unmap'/>
                <source file='{disk_path}'/>
                <target dev='{target_dev}' bus='{bus}'/>
            </disk>
            """

    if not disk_xml:
        msg = "Could not generate disk XML for attaching."
        logging.error(msg)
        raise Exception(msg)

    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

    domain.attachDeviceFlags(disk_xml, flags)
    return target_dev

def remove_disk(domain, disk_dev_path):
    """
    Removes a disk from a VM based on its device path (e.g., /path/to/disk.img),
    device name (e.g., vda), or volume name. If the backing storage volume is missing,
    it will still detach the disk from the VM's XML configuration.

    Returns:
        A warning message if the backing volume was not found, otherwise None.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    invalidate_cache(get_internal_id(domain))

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    logging.debug(f"remove_disk: Attempting to remove disk: {disk_dev_path}")

    disk_to_detach_elem = None
    warning_message = None

    all_disks = root.findall(".//disk[@device='disk']") + root.findall(".//disk[@device='cdrom']")

    for disk in all_disks:
        source = disk.find("source")
        target = disk.find("target")

        # 1. Match by target device name (e.g., 'vda')
        if target is not None and target.get("dev") == disk_dev_path:
            disk_to_detach_elem = disk
            break

        if source is not None:
            # 2. Match by direct file path
            if "file" in source.attrib and source.get("file") == disk_dev_path:
                disk_to_detach_elem = disk
                break

            # 3. Match by pool/volume
            elif "pool" in source.attrib and "volume" in source.attrib:
                pool_name = source.get("pool")
                vol_name = source.get("volume")
                try:
                    pool = domain.connect().storagePoolLookupByName(pool_name)
                    vol = pool.storageVolLookupByName(vol_name)
                    resolved_path = vol.path()
                    # Check against resolved path OR volume name
                    if resolved_path == disk_dev_path or vol_name == disk_dev_path:
                        disk_to_detach_elem = disk
                        break
                except libvirt.libvirtError:
                    # Force removal: If we can't find the volume, but the provided identifier
                    # matches the volume name, assume it's the right one.
                    if os.path.basename(disk_dev_path) == vol_name or disk_dev_path == vol_name:
                        disk_to_detach_elem = disk
                        warning_message = (
                            f"Removed disk entry '{vol_name}' from the VM configuration. "
                            f"Note: The backing volume was not found in pool '{pool_name}' and was not deleted."
                        )
                        break

    if not disk_to_detach_elem:
        msg = f"Disk with device path or name '[red]{disk_dev_path}[/red]' not found."
        logging.error(msg)
        raise Exception(msg)

    disk_to_detach_xml = ET.tostring(disk_to_detach_elem, encoding="unicode")

    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

    domain.detachDeviceFlags(disk_to_detach_xml, flags)

    return warning_message


@log_function_call
def remove_virtiofs(domain: libvirt.virDomain, target_dir: str):
    """
    Removes a virtiofs filesystem from a VM.
    The VM must be stopped to remove a virtiofs device.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    invalidate_cache(get_internal_id(domain))

    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to remove a virtiofs device.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        raise ValueError("Could not find <devices> in VM XML.")

    virtiofs_to_remove = None
    for fs_elem in devices.findall("./filesystem[@type='mount']"):
        driver = fs_elem.find('driver')
        target = fs_elem.find('target')
        if driver is not None and driver.get('type') == 'virtiofs' and target is not None:
            if target.get('dir') == target_dir:
                virtiofs_to_remove = fs_elem
                break

    if virtiofs_to_remove is None:
        raise ValueError(f"VirtIO-FS mount with target directory '{target_dir}' not found.")

    devices.remove(virtiofs_to_remove)

    new_xml = ET.tostring(root, encoding='unicode')

    conn = domain.connect()
    conn.defineXML(new_xml)

@log_function_call
def add_virtiofs(domain: libvirt.virDomain, source_path: str, target_path: str, readonly: bool):
    """
    Adds a virtiofs filesystem to a VM.
    The VM must be stopped to add a virtiofs device.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    invalidate_cache(get_internal_id(domain))

    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to add a virtiofs device.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')

    # Create the new virtiofs XML element
    fs_elem = ET.SubElement(devices, "filesystem", type="mount", accessmode="passthrough")

    driver_elem = ET.SubElement(fs_elem, "driver", type="virtiofs")
    source_elem = ET.SubElement(fs_elem, "source", dir=source_path)
    target_elem = ET.SubElement(fs_elem, "target", dir=target_path)

    if readonly:
        ET.SubElement(fs_elem, "readonly")

    # Redefine the VM with the updated XML
    new_xml = ET.tostring(root, encoding='unicode')

    conn = domain.connect()
    conn.defineXML(new_xml)


def add_network_interface(domain: libvirt.virDomain, network: str, model: str):
    """Adds a network interface to a VM."""
    if not domain:
        raise ValueError("Invalid domain object.")
    invalidate_cache(get_internal_id(domain))

    interface_xml = f"""
    <interface type='network'>
        <source network='{network}'/>
        <model type='{model}'/>
    </interface>
    """

    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

    domain.attachDeviceFlags(interface_xml, flags)

def remove_network_interface(domain: libvirt.virDomain, mac_address: str):
    """Removes a network interface from a VM."""
    if not domain:
        raise ValueError("Invalid domain object.")
    invalidate_cache(get_internal_id(domain))

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    interface_to_remove = None
    for iface in root.findall(".//devices/interface"):
        mac_node = iface.find("mac")
        if mac_node is not None and mac_node.get("address") == mac_address:
            interface_to_remove = iface
            break

    if interface_to_remove is None:
        raise ValueError(f"Interface with MAC {mac_address} not found.")

    interface_xml = ET.tostring(interface_to_remove, 'unicode')

    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

    domain.detachDeviceFlags(interface_xml, flags)


def change_vm_network(domain: libvirt.virDomain, mac_address: str, new_network: str, new_model: str = None):
    """Changes the network for a VM's network interface."""
    invalidate_cache(get_internal_id(domain))
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    interface_to_update = None
    for iface in root.findall(".//devices/interface"):
        mac_node = iface.find("mac")
        if mac_node is not None and mac_node.get("address") == mac_address:
            interface_to_update = iface
            break

    if interface_to_update is None:
        raise ValueError(f"Interface with MAC {mac_address} not found.")

    source_node = interface_to_update.find("source")
    if source_node is None:
        raise ValueError("Interface does not have a source element.")

    model_node = interface_to_update.find("model")
    if model_node is None:
        model_node = ET.SubElement(interface_to_update, "model")

    # Check if network is already the same
    if source_node.get("network") == new_network and (new_model is None or model_node.get("type") == new_model):
        return # Nothing to do

    source_node.set("network", new_network)
    if new_model:
        model_node.set("type", new_model)

    interface_xml = ET.tostring(interface_to_update, 'unicode')

    state = domain.info()[0]
    flags = libvirt.VIR_DOMAIN_DEVICE_MODIFY_CONFIG
    if state in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]:
        flags |= libvirt.VIR_DOMAIN_DEVICE_MODIFY_LIVE

    domain.updateDeviceFlags(interface_xml, flags)


def disable_disk(domain, disk_path):
    """Disables a disk by moving it to a metadata section in the XML."""
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to disable a disk.")

    conn = domain.connect()
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        raise ValueError("Could not find <devices> in VM XML.")

    disk_to_disable = None
    for disk in devices.findall('disk'):
        source = disk.find('source')
        path = None
        if source is not None and 'file' in source.attrib:
            path = source.attrib['file']
        elif source is not None and 'dev' in source.attrib:
            path = source.attrib['dev']

        if path == disk_path:
            disk_to_disable = disk
            break

    if disk_to_disable is None:
        raise ValueError(f"Enabled disk '{disk_path}' not found.")

    devices.remove(disk_to_disable)

    disabled_disks_elem = _get_disabled_disks_elem(root)
    disabled_disks_elem.append(disk_to_disable)

    new_xml = ET.tostring(root, encoding='unicode')
    conn.defineXML(new_xml)

def enable_disk(domain, disk_path):
    """Enables a disk by moving it from metadata back to devices."""
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to enable a disk.")

    conn = domain.connect()
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    disabled_disks_elem = _get_disabled_disks_elem(root)

    disk_to_enable = None
    for disk in disabled_disks_elem.findall('disk'):
        source = disk.find('source')
        path = None
        if source is not None and 'file' in source.attrib:
            path = source.attrib['file']
        elif source is not None and 'dev' in source.attrib:
            path = source.attrib['dev']

        if path == disk_path:
            disk_to_enable = disk
            break

    if disk_to_enable is None:
        raise ValueError(f"Disabled disk '{disk_path}' not found.")

    disabled_disks_elem.remove(disk_to_enable)

    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')
    devices.append(disk_to_enable)

    new_xml = ET.tostring(root, encoding='unicode')
    conn.defineXML(new_xml)

def set_vcpu(domain, vcpu_count: int):
    """
    Sets the number of virtual CPUs for a VM.
    Handles both simple and complex (with attributes) vCPU definitions.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    invalidate_cache(get_internal_id(domain))

    conn = domain.connect()

    xml_flags = libvirt.VIR_DOMAIN_XML_INACTIVE if domain.isPersistent() else 0
    xml_desc = domain.XMLDesc(xml_flags)
    root = ET.fromstring(xml_desc)

    vcpu_elem = root.find('vcpu')
    if vcpu_elem is None:
        vcpu_elem = ET.SubElement(root, 'vcpu')

    vcpu_elem.text = str(vcpu_count)
    new_xml = ET.tostring(root, encoding='unicode')

    conn.defineXML(new_xml)

    # For a running VM, the only way to change vCPU count is setVcpusFlags.
    if domain.isActive():
        try:
            # Attempt a live update.
            domain.setVcpusFlags(vcpu_count, libvirt.VIR_DOMAIN_AFFECT_LIVE)
        except libvirt.libvirtError as e:
            # If live update fails, we inform the user. The persistent config is still updated.
            raise libvirt.libvirtError(
                f"Live vCPU update failed: {e}. "
                "The configuration has been saved and will apply on the next reboot."
            )

def set_memory(domain, memory_mb: int):
    """
    Sets the memory for a VM in megabytes.
    Handles both simple and complex (with attributes) memory definitions.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    invalidate_cache(get_internal_id(domain))

    memory_kb = memory_mb * 1024
    conn = domain.connect()

    xml_flags = libvirt.VIR_DOMAIN_XML_INACTIVE if domain.isPersistent() else 0
    xml_desc = domain.XMLDesc(xml_flags)
    root = ET.fromstring(xml_desc)

    # Update max memory
    memory_elem = root.find('memory')
    if memory_elem is None:
        memory_elem = ET.SubElement(root, 'memory')
    memory_elem.text = str(memory_kb)
    memory_elem.set('unit', 'KiB')

    # Update current memory
    current_memory_elem = root.find('currentMemory')
    if current_memory_elem is None:
        current_memory_elem = ET.SubElement(root, 'currentMemory')
    current_memory_elem.text = str(memory_kb)
    current_memory_elem.set('unit', 'KiB')

    new_xml = ET.tostring(root, encoding='unicode')

    # Update the persistent definition of the VM.
    conn.defineXML(new_xml)

    # For a running VM, we use setMemoryFlags for a live update.
    if domain.isActive():
        try:
            # Attempt a live update.
            domain.setMemoryFlags(memory_kb, libvirt.VIR_DOMAIN_AFFECT_LIVE)
        except libvirt.libvirtError as e:
            # If live update fails, inform the user. The persistent config is still updated.
            raise libvirt.libvirtError(
                f"Live memory update failed: {e}. "
                "The configuration has been saved and will apply on the next reboot."
            )

@log_function_call
def set_disk_properties(domain: libvirt.virDomain, disk_path: str, properties: dict):
    """Sets multiple driver properties for a specific disk."""
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change disk settings.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    conn = domain.connect()

    disk_found = False
    for disk in root.findall(".//disk"):
        source = disk.find("source")
        current_path = None
        if source is not None:
            if "file" in source.attrib:
                current_path = source.attrib["file"]
            elif "dev" in source.attrib:
                current_path = source.attrib["dev"]
            elif "pool" in source.attrib and "volume" in source.attrib:
                pool_name = source.attrib["pool"]
                vol_name = source.attrib["volume"]
                try:
                    pool = conn.storagePoolLookupByName(pool_name)
                    vol = pool.storageVolLookupByName(vol_name)
                    current_path = vol.path()
                except libvirt.libvirtError:
                    pass

        if current_path == disk_path:
            driver = disk.find("driver")
            if driver is None:
                driver = ET.SubElement(disk, "driver", name="qemu", type="qcow2")

            for key, value in properties.items():
                if key == "bus":
                    continue
                if key == "device":
                    disk.set("device", value)
                    continue
                if key == "cache" and value == "default":
                    if key in driver.attrib:
                        del driver.attrib[key]
                else:
                    driver.set(key, value)

            if 'bus' in properties:
                target = disk.find("target")
                if target is not None:
                    target.set('bus', properties['bus'])

            disk_found = True
            break

    if not disk_found:
        raise ValueError(f"Disk with path '{disk_path}' not found.")

    new_xml = ET.tostring(root, encoding='unicode')
    conn.defineXML(new_xml)

def set_machine_type(domain, new_machine_type):
    """
    Sets the machine type for a VM.
    The VM must be stopped.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    invalidate_cache(get_internal_id(domain))

    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change machine type.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    type_elem = root.find(".//os/type")
    if type_elem is None:
        msg = "Could not find OS type element in VM XML."
        logging.error(msg)
        raise Exception(msg)

    current_machine_type = type_elem.get('machine', '')

    # Do nothing if machine type is not actually changing
    if current_machine_type == new_machine_type:
        return

    type_elem.set('machine', new_machine_type)

    current_family = '-'.join(current_machine_type.split('-')[:2])
    new_family = '-'.join(new_machine_type.split('-')[:2])

    new_xml_desc = ET.tostring(root, encoding='unicode')
    conn = domain.connect()
    conn.defineXML(new_xml_desc)


@log_function_call
def migrate_vm_machine_type(domain: libvirt.virDomain, new_machine_type: str, log_callback=None):
    """
    Migrates a VM from its current machine type to a new one, specifically handling
    changes from i440fx to q35. This involves creating a temporary VM with modified XML,
    validating it, defining it, then undefining the original and renaming the new VM.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    invalidate_cache(get_internal_id(domain))

    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change machine type.")

    conn = domain.connect()
    original_vm_name = domain.name()
    original_xml_desc = domain.XMLDesc(0)
    temp_xml_desc = "" # Initialize to prevent UnboundLocalError

    # Prepare XML for the new VM
    root = ET.fromstring(original_xml_desc)

    # Generate a temporary new name for the VM
    temp_vm_name = f"{original_vm_name}-MIGRATE-TEMP"
    name_elem = root.find('name')
    if name_elem is not None:
        name_elem.text = temp_vm_name
    else:
        raise Exception("VM XML is missing the 'name' element.")

    # Generate a new UUID for the temporary VM to avoid conflicts
    uuid_elem = root.find('uuid')
    if uuid_elem is not None:
        uuid_elem.text = str(uuid.uuid4())
    else:
        ET.SubElement(root, 'uuid').text = str(uuid.uuid4())

    # Remove all PCI devices and Watchdog, and chipset-dependent controllers as per `set_machine_type` logic
    devices_elem = root.find('devices')
    if devices_elem is not None:
        # Remove all PCI and USB addresses from all devices
        for device in list(devices_elem):
            pci_address_elem = device.find("address[@type='pci']")
            if pci_address_elem is not None:
                device.remove(pci_address_elem)

            usb_address_elem = device.find("address[@type='usb']")
            if usb_address_elem is not None:
                device.remove(usb_address_elem)

        # Remove all chipset-dependent controllers (PCI, IDE, SATA, USB)
        controllers_to_remove = []
        for controller in devices_elem.findall('controller'):
            ctype = controller.get('type')
            if ctype in ['pci', 'ide', 'sata', 'usb']:
                controllers_to_remove.append(controller)

        for controller in controllers_to_remove:
            devices_elem.remove(controller)

        # Remove all watchdog devices
        watchdog_elements = devices_elem.findall("watchdog")
        for elem in watchdog_elements:
            devices_elem.remove(elem)

    # Change machine type
    type_elem = root.find(".//os/type")
    if type_elem is None:
        raise Exception("Could not find OS type element in VM XML.")

    current_machine_type = type_elem.get('machine', '')
    if current_machine_type == new_machine_type:
        if log_callback:
            log_callback(f"Machine type is already {new_machine_type}. No migration needed.")
        return

    # Set the new machine type
    type_elem.set('machine', new_machine_type)
    temp_xml_desc = ET.tostring(root, encoding='unicode')

    # Validate the XML config with libvirt (by defining transient)
    temp_vm = None
    try:
        temp_vm = conn.defineXML(temp_xml_desc)
        if temp_vm is None:
            raise libvirt.libvirtError(f"Failed to define new VM '{temp_vm_name}'. XML was:\n{temp_xml_desc}")
        domain.undefine()
        rename_vm(temp_vm, original_vm_name)

    except libvirt.libvirtError as e:
        error_msg = f"Libvirt error during machine type migration for '{original_vm_name}': {e}"
        logging.error(error_msg)
        # Attempt to clean up the temporary VM if it was defined
        if temp_vm:
            try:
                temp_vm.undefine()
                logging.info(f"Cleaned up temporary VM '{temp_vm_name}'.")
            except libvirt.libvirtError as cleanup_e:
                logging.warning(f"Failed to clean up temporary VM '{temp_vm_name}': {cleanup_e}")
        # Re-define original VM if it was undefined but migration failed
        try:
            conn.defineXML(original_xml_desc)
            logging.info(f"Original VM '{original_vm_name}' restored after migration failure.")
        except libvirt.libvirtError as restore_e:
            logging.critical(f"CRITICAL ERROR: Failed to restore original VM '{original_vm_name}' after migration failure: {restore_e}")
            error_msg += f"\nCRITICAL: Original VM could not be restored. Manual intervention required."
        raise libvirt.libvirtError(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error during machine type migration for '{original_vm_name}': {e}"
        logging.error(error_msg)
        if temp_vm:
            try:
                temp_vm.undefine()
                logging.info(f"Cleaned up temporary VM '{temp_vm_name}'.")
            except libvirt.libvirtError as cleanup_e:
                logging.warning(f"Failed to clean up temporary VM '{temp_vm_name}': {cleanup_e}")
        try:
            conn.defineXML(original_xml_desc)
            logging.info(f"Original VM '{original_vm_name}' restored after migration failure.")
        except libvirt.libvirtError as restore_e:
            logging.critical(f"CRITICAL ERROR: Failed to restore original VM '{original_vm_name}' after migration failure: {restore_e}")
            error_msg += f"\nCRITICAL: Original VM could not be restored. Manual intervention required."
        raise Exception(error_msg)
    finally:
        invalidate_cache(get_internal_id(domain)) # Invalidate original VM cache in case it was renamed or recreated


def set_shared_memory(domain: libvirt.virDomain, enable: bool):
    """Enable or disable shared memory for a VM."""
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise ValueError("Cannot change shared memory setting on a running VM.")

    xml_content = domain.XMLDesc(0)
    root = ET.fromstring(xml_content)

    memory_backing = root.find('memoryBacking')

    if enable:
        if memory_backing is None:
            memory_backing = ET.SubElement(root, 'memoryBacking')

        # Ensure no conflicting access mode is set
        access_elem = memory_backing.find('access')
        if access_elem is not None and access_elem.get('mode') != 'shared':
            memory_backing.remove(access_elem)
            access_elem = None # It's gone

        # Add it if it doesn't exist
        if access_elem is None:
            ET.SubElement(memory_backing, 'access', mode='shared')

    else:  # disable
        if memory_backing is not None:
            # Remove both possible shared memory indicators
            shared_elem = memory_backing.find('shared')
            if shared_elem is not None:
                memory_backing.remove(shared_elem)

            access_elem = memory_backing.find('access')
            if access_elem is not None and access_elem.get('mode') == 'shared':
                memory_backing.remove(access_elem)

            # If memoryBacking is now empty, and has no attributes, remove it.
            if not list(memory_backing) and not memory_backing.attrib:
                root.remove(memory_backing)

    new_xml = ET.tostring(root, encoding='unicode')

    conn = domain.connect()
    conn.defineXML(new_xml)

@log_function_call
def set_boot_info(domain: libvirt.virDomain, menu_enabled: bool, order: list[str]):
    """Sets the boot configuration for a VM."""
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change boot settings.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    conn = domain.connect()
    os_elem = root.find('.//os')
    if os_elem is None:
        os_elem = ET.SubElement(root, 'os')

    # Remove old <boot> elements under <os>
    for boot_elem in os_elem.findall('boot'):
        os_elem.remove(boot_elem)

    # Remove old <boot> elements under devices
    for dev_node in root.findall('.//devices/*[boot]'):
        boot_elem = dev_node.find('boot')
        if boot_elem is not None:
            dev_node.remove(boot_elem)

    # Set boot menu
    boot_menu_elem = os_elem.find('bootmenu')
    if boot_menu_elem is not None:
        os_elem.remove(boot_menu_elem)
    if menu_enabled:
        ET.SubElement(os_elem, 'bootmenu', enable='yes')

    # Set new boot order
    for i, device_id in enumerate(order, 1):
        # Find the device and add a <boot order='...'> element
        # Check disks first
        disk_found = False
        for disk_elem in root.findall('.//devices/disk'):
            source_elem = disk_elem.find('source')
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
                        pass # Could not resolve path, so it cannot match device_id

                if path == device_id:
                    ET.SubElement(disk_elem, 'boot', order=str(i))
                    disk_found = True
                    break
        if disk_found:
            continue

        # Check interfaces
        for iface_elem in root.findall('.//devices/interface'):
            mac_elem = iface_elem.find('mac')
            if mac_elem is not None and mac_elem.get('address') == device_id:
                ET.SubElement(iface_elem, 'boot', order=str(i))
                break

    # Update the domain
    new_xml = ET.tostring(root, encoding='unicode')
    conn.defineXML(new_xml)

def set_vm_video_model(domain: libvirt.virDomain, model: str | None, accel3d: bool | None = None):
    """Sets the video model and 3D acceleration for a VM."""
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change the video model.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        if model is None or model == 'none':
            return # No devices and no model to set, so nothing to do.
        devices = ET.SubElement(root, 'devices')

    video = devices.find('video')

    # If model is being set to none, remove the entire video tag.
    if model is None or model == 'none':
        if video is not None:
            devices.remove(video)
        new_xml = ET.tostring(root, encoding='unicode')
        domain.connect().defineXML(new_xml)
        return

    if video is None:
        video = ET.SubElement(devices, 'video')

    model_elem = video.find('model')
    if model_elem is None:
        model_elem = ET.SubElement(video, 'model')

    # Check for existing acceleration before clearing (correct location)
    existing_accel = False
    existing_accel_node = model_elem.find('acceleration')
    if existing_accel_node is not None and existing_accel_node.get('accel3d') == 'yes':
        existing_accel = True

    old_attribs = model_elem.attrib.copy()
    model_elem.clear()
    model_elem.set('type', model)

    if model == 'virtio':
        model_elem.set('heads', old_attribs.get('heads', '1'))
        model_elem.set('primary', old_attribs.get('primary', 'yes'))
    elif model == 'qxl':
        model_elem.set('vram', old_attribs.get('vram', '65536'))
        model_elem.set('ram', old_attribs.get('ram', '65536'))
    else:  # vga, cirrus etc.
        model_elem.set('vram', old_attribs.get('vram', '16384'))

    # Handle 3D acceleration
    final_accel = accel3d if accel3d is not None else existing_accel

    if model in ['virtio', 'qxl'] and final_accel:
        accel_elem = ET.SubElement(model_elem, 'acceleration')
        accel_elem.set('accel3d', 'yes')

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)


def set_cpu_model(domain: libvirt.virDomain, cpu_model: str):
    """Sets the CPU model for a VM."""
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change the CPU model.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    # Remove existing cpu element to rebuild it
    cpu = root.find('.//cpu')
    if cpu is not None:
        root.remove(cpu)

    if cpu_model == 'default':
        # Default usually means no specific CPU config, or let libvirt decide.
        pass
    else:
        cpu = ET.SubElement(root, 'cpu')

        if cpu_model == 'host-passthrough':
            cpu.set('mode', 'host-passthrough')
        elif cpu_model == 'host-model':
            cpu.set('mode', 'host-model')
        else:
            # Assume custom model
            cpu.set('mode', 'custom')
            cpu.set('match', 'exact')
            cpu.set('check', 'none')
            model_elem = ET.SubElement(cpu, 'model')
            model_elem.set('fallback', 'allow')
            model_elem.text = cpu_model

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)

@log_function_call
def set_uefi_file(domain: libvirt.virDomain, uefi_path: str | None, secure_boot: bool):
    """
    Sets the UEFI file for a VM and optionally enables/disables secure boot.
    The VM must be stopped.
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change UEFI firmware.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    os_elem = root.find('os')
    if os_elem is None:
        raise ValueError("Could not find <os> element in VM XML.")

    if not uefi_path:  # Switching to BIOS
        if 'firmware' in os_elem.attrib:
            del os_elem.attrib['firmware']

        firmware_feature_elem = os_elem.find('firmware')
        if firmware_feature_elem is not None:
            os_elem.remove(firmware_feature_elem)

        loader_elem = os_elem.find('loader')
        if loader_elem is not None:
            os_elem.remove(loader_elem)

        nvram_elem = os_elem.find('nvram')
        if nvram_elem is not None:
            os_elem.remove(nvram_elem)
    else:  # Switching to UEFI
        os_elem.set('firmware', 'efi')

        loader_elem = os_elem.find('loader')
        if loader_elem is None:
            loader_elem = ET.SubElement(os_elem, 'loader', type='pflash')
        loader_elem.text = uefi_path
        if secure_boot:
            loader_elem.set('secure', 'yes')
        elif 'secure' in loader_elem.attrib:
            del loader_elem.attrib['secure']

        nvram_elem = os_elem.find('nvram')
        if nvram_elem is None:
            ET.SubElement(os_elem, 'nvram', template=f"{uefi_path.replace('.bin', '_VARS.fd')}", templateFormat='qcow2')
        else:
            nvram_elem.set('template', uefi_path.replace('.bin', '_VARS.fd'))
            nvram_elem.set('templateFormat', 'qcow2')

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)

def set_vm_sound_model(domain: libvirt.virDomain, model: str | None):
    """
    Sets the sound model for a VM. If model is None or 'none', the sound device is removed.
    The VM must be stopped.
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change the sound model.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find("devices")
    if devices is None:
        if model is None or model == 'none':
            return
        devices = ET.SubElement(root, "devices")

    sound = devices.find("sound")

    # If the desired model is None or 'none', remove the sound device.
    if model is None or model == 'none':
        if sound is not None:
            devices.remove(sound)
    else:
        if sound is None:
            sound = ET.SubElement(devices, "sound")

        sound.set('model', model)

    new_xml = ET.tostring(root, encoding="unicode")
    domain.connect().defineXML(new_xml)


def set_vm_graphics(domain: libvirt.virDomain, graphics_type: str | None, listen_type: str, address: str, port: int | None, autoport: bool, password_enabled: bool, password: str | None):
    """
    Sets the graphics configuration (VNC/Spice) for a VM.
    The VM must be stopped.
    """
    invalidate_cache(get_internal_id(domain))
    # Password validation and sanitization
    def _sanitize_password(pwd: str | None) -> str | None:
        if not pwd:
            return None
        pwd = pwd.strip()

        # Validation
        if graphics_type == 'vnc' and len(pwd) > 8:
            raise ValueError("VNC password cannot exceed 8 characters")

        if not pwd.isprintable() or any(c in pwd for c in '\n\r\t'):
            raise ValueError("Password contains invalid characters")
        return pwd

    def _log_password_safe(password: str | None) -> str:
        """Returns '[redacted]' instead of actual password for logs"""
        return '[redacted]' if password else 'none'

    # Validate parameters
    if password_enabled and not password:
        raise ValueError("Password is required when password_enabled=True")
    password_safe = _sanitize_password(password)

    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change graphics settings.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')

    # Remove existing graphics elements of other types or if no graphics type is specified
    existing_graphics_elements = devices.findall('graphics')
    for elem in existing_graphics_elements:
        logging.info("Removing previous graphics")
        devices.remove(elem)

    if graphics_type is None:
        # If no graphics type is specified, all graphics elements are been removed
        logging.info("No more graphics")
        pass
    else:
        graphics_elem = ET.SubElement(devices, 'graphics', type=graphics_type)

        # Set port and autoport
        if autoport:
            graphics_elem.set('autoport', 'yes')
            if 'port' in graphics_elem.attrib:
                del graphics_elem.attrib['port']
        else:
            if 'autoport' in graphics_elem.attrib:
                del graphics_elem.attrib['autoport']
            if port is not None:
                graphics_elem.set('port', str(port))
            elif 'port' in graphics_elem.attrib:
                del graphics_elem.attrib['port'] # If autoport is off and no port provided, remove it


        # Set listen address
        listen_elem = graphics_elem.find('listen')
        if listen_type == 'address':
            if listen_elem is None:
                listen_elem = ET.SubElement(graphics_elem, 'listen', type='address')
            else:
                listen_elem.set('type', 'address')
            listen_elem.set('address', address)
        else:  # listen_type == 'none'
            if listen_elem is not None:
                graphics_elem.remove(listen_elem)

        # Set password
        if password_enabled and password_safe:
            graphics_elem.set('passwd', password_safe)
        elif 'passwd' in graphics_elem.attrib:
            del graphics_elem.attrib['passwd']

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)


def set_vm_tpm(domain: libvirt.virDomain, tpm_model: str | None, tpm_type: str = 'emulated', device_path: str = None, backend_type: str = None, backend_path: str = None):
    """
    Sets TPM configuration for a VM. If tpm_model is None, removes the TPM device.
    The VM must be stopped.
    
    Args:
        domain: libvirt domain object
        tpm_model: TPM model (e.g., 'tpm-crb', 'tpm-tis') or None to remove.
        tpm_type: Type of TPM ('emulated' or 'passthrough')
        device_path: Path to TPM device (required for passthrough)
        backend_type: Backend type (e.g., 'emulator', 'passthrough')
        backend_path: Path to backend device (required for passthrough')
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change TPM settings.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        if tpm_model is None:
            return # Nothing to do
        devices = ET.SubElement(root, 'devices')

    # Remove existing TPM elements
    existing_tpm_elements = devices.findall('./tpm')
    for elem in existing_tpm_elements:
        devices.remove(elem)

    # If model is None, we are just removing the device.
    if tpm_model is not None:
        # Create new TPM element
        tpm_elem = ET.SubElement(devices, 'tpm', model=tpm_model)

        if tpm_type == 'passthrough':
            backend_elem = ET.SubElement(tpm_elem, 'backend', type='passthrough')
            if device_path:
                ET.SubElement(backend_elem, 'device', path=device_path)
        elif tpm_type == 'emulated':
            # For emulated TPM, add a backend of type 'emulator'
            ET.SubElement(tpm_elem, 'backend', type='emulator')

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)


@log_function_call
def set_vm_rng(domain: libvirt.virDomain, rng_model: str = 'virtio', backend_model: str = 'random', backend_path: str = '/dev/urandom'):
    """
    Sets RNG (Random Number Generator) configuration for a VM.
    The VM must be stopped.
    
    Args:
        domain: libvirt domain object
        rng_model: RNG model (e.g., 'virtio')
        backend_model: Backend type (e.g., 'random', 'egd')
        backend_path: Path to backend device/file
    """
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change RNG settings.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')

    # Remove existing RNG elements
    existing_rng_elements = devices.findall('./rng')
    for elem in existing_rng_elements:
        devices.remove(elem)

    rng_elem = ET.SubElement(devices, 'rng', model=rng_model)
    backend_elem = ET.SubElement(rng_elem, 'backend', model=backend_model)
    if backend_model == 'random' and backend_path:
        backend_elem.text = backend_path
    elif backend_path:
        ET.SubElement(backend_elem, 'source', path=backend_path)

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)
    invalidate_cache(get_internal_id(domain))


@log_function_call
def set_vm_watchdog(domain: libvirt.virDomain, watchdog_model: str = 'i6300esb', action: str = 'reset'):
    """
    Sets Watchdog configuration for a VM.
    The VM must be stopped.
    
    Args:
        domain: libvirt domain object
        watchdog_model: Watchdog model (e.g., 'i6300esb', 'pcie-vpd')
        action: Action to take when watchdog is triggered (e.g., 'reset', 'shutdown', 'poweroff')
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change Watchdog settings.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')

    # Remove existing Watchdog elements
    existing_watchdog_elements = devices.findall('./watchdog')
    for elem in existing_watchdog_elements:
        devices.remove(elem)

    # Create new Watchdog element
    ET.SubElement(devices, 'watchdog', model=watchdog_model, action=action)

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)


@log_function_call
def remove_vm_watchdog(domain: libvirt.virDomain):
    """
    Removes Watchdog configuration from a VM.
    The VM must be stopped.
    """
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to remove Watchdog settings.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        return # No devices, so no watchdog

    # Remove existing Watchdog elements
    existing_watchdog_elements = devices.findall('./watchdog')
    if not existing_watchdog_elements:
        raise ValueError("No watchdog device found to remove.")

    for elem in existing_watchdog_elements:
        devices.remove(elem)

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)
    invalidate_cache(get_internal_id(domain))


@log_function_call
def set_vm_input(domain: libvirt.virDomain, input_type: str = 'tablet', input_bus: str = 'usb'):
    """
    Sets Input (keyboard and mouse) configuration for a VM.
    The VM must be stopped.
    
    Args:
        domain: libvirt domain object
        input_type: Input device type (e.g., 'mouse', 'keyboard', 'tablet')
        input_bus: Bus type (e.g., 'usb', 'ps2', 'virtio')
    """
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change Input settings.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')

    # Remove existing input elements of the same type
    existing_input_elements = devices.findall(f'./input[@type="{input_type}"]')
    for elem in existing_input_elements:
        devices.remove(elem)

    # Create new input element
    ET.SubElement(devices, 'input', type=input_type, bus=input_bus)

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)
    invalidate_cache(get_internal_id(domain))


@log_function_call
def add_vm_input(domain: libvirt.virDomain, input_type: str, input_bus: str):
    """
    Adds an input device to a VM.
    The VM must be stopped.
    """
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to add an input device.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')

    ET.SubElement(devices, 'input', type=input_type, bus=input_bus)

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)
    invalidate_cache(get_internal_id(domain))


@log_function_call
def remove_vm_input(domain: libvirt.virDomain, input_type: str, input_bus: str):
    """
    Removes an input device from a VM.
    The VM must be stopped.
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to remove an input device.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    devices = root.find('devices')
    if devices is None:
        raise ValueError("Could not find <devices> in VM XML.")

    input_to_remove = None
    for elem in devices.findall('input'):
        if elem.get('type') == input_type and elem.get('bus') == input_bus:
            input_to_remove = elem
            break

    if input_to_remove is None:
        raise ValueError(f"Input device with type '{input_type}' and bus '{input_bus}' not found.")

    devices.remove(input_to_remove)

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)


def start_vm(domain):
    """
    Starts a VM after checking for missing disks.
    """
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    conn = domain.connect()

    for disk in root.findall('.//devices/disk'):
        if disk.get('device') != 'disk':
            continue

        source_elem = disk.find('source')
        if source_elem is None:
            continue

        if 'pool' in source_elem.attrib and 'volume' in source_elem.attrib:
            pool_name = source_elem.get('pool')
            vol_name = source_elem.get('volume')
            try:
                pool = conn.storagePoolLookupByName(pool_name)
                if not pool.isActive():
                    msg = f"Storage pool '{pool_name}' is not active."
                    logging.error(msg)
                    raise Exception(msg)
                # This will raise an exception if the volume doesn't exist
                pool.storageVolLookupByName(vol_name)
            except libvirt.libvirtError as e:
                msg = f"Error checking disk volume '{vol_name}' in pool '{pool_name}': {e}"
                logging.error(msg)
                raise Exception(msg) from e

    invalidate_cache(get_internal_id(domain))
    domain.create()

def stop_vm(domain: libvirt.virDomain):
    """
    Initiates a graceful shutdown of the VM.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    if not domain.isActive():
        raise libvirt.libvirtError(f"VM '{domain.name()}' is not active, cannot shutdown.")

    invalidate_cache(get_internal_id(domain))
    domain.shutdown()

def pause_vm(domain: libvirt.virDomain):
    """
    Pauses the execution of the VM.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    if not domain.isActive():
        raise libvirt.libvirtError(f"VM '{domain.name()}' is not active, cannot pause.")

    invalidate_cache(get_internal_id(domain))
    domain.suspend()

def force_off_vm(domain: libvirt.virDomain):
    """
    Forcefully shuts down (destroys) the VM.
    This is equivalent to pulling the power plug.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    if not domain.isActive():
        raise libvirt.libvirtError(f"VM '{domain.name()}' is not active, cannot force off.")

    invalidate_cache(get_internal_id(domain))
    domain.destroy()

def delete_vm(domain: libvirt.virDomain, delete_storage: bool, delete_nvram: bool = False, log_callback=None, conn: libvirt.virConnect = None):
    """
    Deletes a VM and optionally its associated storage and NVRAM.
    If the VM has snapshots, their metadata will be removed as well.
    """
    if not domain:
        raise ValueError("Invalid domain object.")

    def log(message):
        if log_callback:
            log_callback(message)
        # Also log to file for debugging.
        if "[red]ERROR" in message:
            logging.error(message)
        else:
            logging.info(message)

    vm_name = "unknown"
    vm_uuid = None
    vm_internal_id = None
    try:
        vm_name = domain.name()
        vm_uuid = domain.UUIDString()
        vm_internal_id = get_internal_id(domain)
    except libvirt.libvirtError:
        pass # Domain might already be gone

    log(f"Starting deletion process for VM '{vm_name}'...")

    # Open a dedicated connection for the deletion operation to avoid blocking the main connection
    should_close_conn = False
    if conn:
        delete_conn = conn
    else:
        original_conn = domain.connect()
        uri = original_conn.getURI()
        delete_conn = libvirt.open(uri)
        should_close_conn = True
        if not delete_conn:
            raise libvirt.libvirtError(f"Failed to open new connection to {uri} for deletion")

    try:
        # Get XML from original domain first (if possible) to avoid issues if it's already gone
        # But for storage lookup we might need the connection.
        # We will try to lookup the domain on the new connection.

        domain_to_delete = None
        try:
            domain_to_delete = delete_conn.lookupByUUIDString(vm_uuid)
        except libvirt.libvirtError:
            log(f"VM '{vm_name}' not found on new connection (might be already deleted).")

        root = None
        disks_to_delete = []
        xml_desc = None

        if domain_to_delete:
             if delete_storage or delete_nvram:
                try:
                    xml_desc = domain_to_delete.XMLDesc(0)
                    root = ET.fromstring(xml_desc)
                    if delete_storage:
                        # Pass delete_conn to resolve volumes
                        disks_to_delete = get_vm_disks_info(delete_conn, root)
                except libvirt.libvirtError as e:
                    log(f"[red]ERROR:[/] Could not get XML description for '{vm_name}': {e}")
                    # If we can't get XML, we can't find disks to delete, but we should still try to undefine

             if domain_to_delete.isActive():
                log(f"VM '{vm_name}' is active. Forcefully stopping it...")
                try:
                    domain_to_delete.destroy()
                    log(f"VM '{vm_name}' stopped.")
                except libvirt.libvirtError as e:
                    log(f"[red]ERROR:[/] Failed to stop VM '{vm_name}': {e}")
                    raise

             # Undefine the VM using the new connection object
             if vm_internal_id:
                invalidate_cache(vm_internal_id)
             log(f"Undefining VM '{vm_name}'...")
             undefine_flags = libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
             if delete_nvram and root is not None:
                os_elem = root.find('os')
                if os_elem is not None and os_elem.find('nvram') is not None:
                    log("...including NVRAM.")
                    undefine_flags |= libvirt.VIR_DOMAIN_UNDEFINE_NVRAM

             try:
                domain_to_delete.undefineFlags(undefine_flags)
                log(f"VM '{vm_name}' undefined.")
             except libvirt.libvirtError as e:
                # It might already be gone, which is fine.
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    log(f"VM '{vm_name}' was already undefined.")
                else:
                    log(f"[red]ERROR:[/] Failed to undefine VM '{vm_name}': {e}")
                    raise
        else:
             # Domain not found, but we might still have storage to delete if we could get XML?
             # If domain is not found, we can't get XML, so we can't know which storage to delete.
             pass

        if delete_storage:
            if not disks_to_delete:
                log("No storage volumes found to delete.")
            else:
                log(f"Deleting {len(disks_to_delete)} storage volume(s)...")

            for disk_info in disks_to_delete:
                disk_path = disk_info.get('path')
                if not disk_path or not disk_info.get('status') == 'enabled':
                    continue

                log(f"Attempting to delete volume: {disk_path}")

                # Check for backing file (overlay) before deletion, as we need the XML metadata
                backing_path = None
                if root is not None:
                    backing_path = get_overlay_backing_path(root, disk_path)

                try:
                    # Use delete_conn to find volume
                    vol, pool = _find_vol_by_path(delete_conn, disk_path)

                    if vol:
                        vol.delete(0)
                        log(f"  - Deleted: {disk_path} from pool {pool.name()}")

                        # Delete backing file if it existed
                        if backing_path:
                            log(f"  - Found backing file for overlay: {backing_path}")
                            try:
                                backing_vol, backing_pool = _find_vol_by_path(delete_conn, backing_path)
                                if backing_vol:
                                    backing_vol.delete(0)
                                    log(f"  - Deleted backing file: {backing_path} from pool {backing_pool.name()}")
                                else:
                                    log(f"  - [yellow]Warning:[/] Backing file '{backing_path}' not found as a managed volume.")
                            except Exception as e:
                                log(f"  - [red]ERROR:[/] Failed to delete backing file '{backing_path}': {e}")

                    else:
                        log(f"  - [yellow]Skipped:[/] Disk '{disk_path}' is not a managed libvirt volume.")

                except libvirt.libvirtError as e:
                    if e.get_error_code() == libvirt.VIR_ERR_NO_STORAGE_VOL:
                        log(f"  - [yellow]Skipped:[/] Volume for path '{disk_path}' not found.")
                    else:
                        log(f"  - [red]ERROR:[/] Error deleting volume for path {disk_path}: {e}")
                except Exception as e:
                    log(f"  - [red]ERROR:[/] Unexpected error deleting storage {disk_path}: {e}")

    finally:
        if should_close_conn and delete_conn:
            delete_conn.close()

    log(f"Finished deletion process for VM '{vm_name}'.")


@log_function_call
def check_for_other_spice_devices(domain: libvirt.virDomain) -> bool:
    """
    Checks for SPICE-related devices other than the main graphics element
    in a VM's XML. Returns True if any are found, False otherwise.
    """
    xml_desc = domain.XMLDesc(0)
    logging.info(f"Checking for SPICE devices in XML:\n{xml_desc}")

    root = ET.fromstring(xml_desc)
    devices = root.find('devices')
    if not devices:
        logging.info("No <devices> element found.")
        return False

    for channel in devices.findall("channel"):
        if channel.get('type') == 'spicevmc':
            logging.info("Found spicevmc channel.")
            return True
        elif channel.get('type') == 'spiceport':
            target = channel.find('target')
            if target is not None and target.get('name') == 'com.redhat.spice.0':
                logging.info("Found spiceport channel.")
                return True

    for redirdev in devices.findall("redirdev"):
        if redirdev.get('bus') == 'usb':
            logging.info("Found USB redirection device.")
            return True

    for audio in devices.findall("audio"):
        if audio.get('type') == 'spice':
            logging.info("Found SPICE audio device.")
            return True

    video = devices.find("video/model[@type='qxl']")
    if video is not None:
        logging.info("Found QXL video model.")
        return True

    logging.info("No other SPICE devices found.")
    return False


@log_function_call
def remove_spice_devices(domain: libvirt.virDomain):
    """
    Removes all SPICE-related devices and configurations from a VM's XML.
    The VM must be stopped.
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to remove SPICE devices.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    devices = root.find('devices')
    if not devices:
        return

    for graphics in devices.findall("graphics[@type='spice']"):
        devices.remove(graphics)
        logging.info(f"Removed SPICE graphics from VM '{domain.name()}'.")

    for channel in devices.findall("channel"):
        if channel.get('type') in ['spicevmc', 'spiceport']:
            devices.remove(channel)
            logging.info(f"Removed SPICE channel (type: {channel.get('type')}) from VM '{domain.name()}'.")

    for redirdev in devices.findall("redirdev[@bus='usb']"):
        devices.remove(redirdev)
        logging.info(f"Removed USB redirection device from VM '{domain.name()}'.")

    for audio in devices.findall("audio"):
        if audio.get('type') == 'spice':
            devices.remove(audio)
            logging.info(f"Removed SPICE audio device from VM '{domain.name()}'.")

    # Change qxl video model to virtio
    video_model = devices.find("video/model[@type='qxl']")
    if video_model is not None:
        video_model.set("type", "virtio")
        logging.info(f"Changed qxl video model to virtio for VM '{domain.name()}'.")
        # Remove qxl-specific attributes if they exist
        for attr in ['vram', 'ram', 'vgamem']:
            if attr in video_model.attrib:
                del video_model.attrib[attr]

    # After removing SPICE, it's good to add a default VNC graphics device if no other graphics device exists.
    if not devices.find("graphics"):
        logging.info(f"No graphics device found after removing SPICE. Adding default VNC graphics.")
        graphics_elem = ET.SubElement(devices, 'graphics', type='vnc', port='-1', autoport='yes')
        ET.SubElement(graphics_elem, 'listen', type='address')

    new_xml = ET.tostring(root, encoding='unicode')
    conn = domain.connect()
    conn.defineXML(new_xml)

@log_function_call
def check_server_migration_compatibility(source_conn: libvirt.virConnect, dest_conn: libvirt.virConnect, domain_name: str, is_live: bool):
    """
    Checks if two servers are compatible for migration.
    Returns a list of issues, where each issue is a dict with 'severity' and 'message'.
    """
    issues = []

    # Get source domain object and XML
    source_domain = None
    source_root = None
    try:
        source_domain = source_conn.lookupByName(domain_name)
        _, source_root = _get_domain_root(source_domain)
    except libvirt.libvirtError as e:
        issues.append({'severity': 'ERROR', 'message': f"Could not retrieve source VM '{domain_name}' details: {e}"})
        return issues # Cannot proceed with further checks without source VM info

    try:
        source_arch = source_conn.getInfo()[0]
        dest_arch = dest_conn.getInfo()[0]
        if source_arch != dest_arch:
            issues.append({'severity': 'ERROR', 'message': f"Host architecture mismatch. Source: {source_arch}, Destination: {dest_arch}"})
    except libvirt.libvirtError as e:
        issues.append({'severity': 'WARNING', 'message': f"Could not check host architecture: {e}"})

    # TPM Check
    if source_root:
        source_tpm_info = get_vm_tpm_info(source_root)
        if source_tpm_info:
            try:
                dest_caps_xml = get_host_domain_capabilities(dest_conn)
                if dest_caps_xml:
                    dest_caps_root = ET.fromstring(dest_caps_xml)

                    # Check if destination host supports TPM devices at all
                    if not dest_caps_root.find(".//devices/tpm"):
                        issues.append({
                            'severity': 'ERROR',
                            'message': f"Source VM '{domain_name}' uses TPM, but destination host '{dest_conn.getURI()}' does not appear to support TPM devices."
                        })
                    else:
                        for tpm_dev in source_tpm_info:
                            if tpm_dev['type'] == 'passthrough':
                                # More specific check for passthrough TPM
                                issues.append({
                                    'severity': 'WARNING',
                                    'message': f"Source VM '{domain_name}' uses passthrough TPM ({tpm_dev['model']}). Passthrough TPM migration is often problematic due to hardware dependencies. Manual verification on destination host '{dest_conn.getURI()}' recommended."
                                })
                            elif tpm_dev['type'] == 'emulated' and is_live:
                                # Emulated TPM should generally be fine for cold migration.
                                # Live migration of emulated TPM might be tricky.
                                issues.append({
                                    'severity': 'WARNING',
                                    'message': f"Source VM '{domain_name}' uses emulated TPM. Live migration with TPM can sometimes have issues; cold migration is safer."
                                })
                else:
                    issues.append({'severity': 'WARNING', 'message': f"Could not retrieve destination host capabilities for TPM check."})

            except libvirt.libvirtError as e:
                issues.append({'severity': 'WARNING', 'message': f"Could not retrieve destination host capabilities for TPM check: {e}"})
            except ET.ParseError as e:
                issues.append({'severity': 'WARNING', 'message': f"Failed to parse destination host capabilities XML for TPM check: {e}"})

    return issues


@log_function_call
def check_vm_migration_compatibility(domain: libvirt.virDomain, dest_conn: libvirt.virConnect, is_live: bool):
    """
    Checks if a VM is compatible for migration to a destination host.
    Returns a list of issues, where each issue is a dict with 'severity' and 'message'.
    """
    issues = []

    try:
        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        issues.append({'severity': 'INFO', 'message': "Getting VM XML description"})
    except libvirt.libvirtError as e:
        issues.append({'severity': 'ERROR', 'message': f"Could not get VM XML description: {e}"})
        return issues

    cpu_elem = root.find('cpu')
    if cpu_elem is not None:
        if cpu_elem.get('mode') in ['host-passthrough', 'host-model']:
            issues.append({'severity': 'WARNING', 'message': "VM CPU is set to 'host-passthrough' or 'host-model'. This requires highly compatible CPUs on source and destination."})
            issues.append({'severity': 'WARNING', 'message': "IE: snapshots maybe be not usable after migration as the CPU register could be different on the destination host."})
        cpu_xml = ET.tostring(cpu_elem, encoding='unicode')
        try:
            compare_result = dest_conn.compareCPU(cpu_xml, 0)
            if compare_result == libvirt.VIR_CPU_COMPARE_INCOMPATIBLE:
                issues.append({'severity': 'ERROR', 'message': "The VM's CPU configuration is not compatible with the destination host's CPU."})
            else:
                issues.append({'severity': 'INFO', 'message': "The VM's CPU configuration is compatible with the destination host's CPU"})
        except libvirt.libvirtError as e:
            issues.append({'severity': 'WARNING', 'message': f"Could not compare VM CPU with destination host: {e}"})

    # Network configuration check
    dest_networks = {net['name']: net for net in list_networks(dest_conn)}
    for iface in root.findall(".//devices/interface[@type='network']"):
        source = iface.find('source')
        if source is not None:
            network_name = source.get('network')
            if network_name:
                if network_name not in dest_networks:
                    issues.append({'severity': 'ERROR', 'message': f"Network '{network_name}' not found on the destination host."})
                elif not dest_networks[network_name]['active']:
                    issues.append({'severity': 'ERROR', 'message': f"Network '{network_name}' is not active on the destination host."})

    if is_live:
        for disk in root.findall(".//disk[@device='disk']"):
            target = disk.find('target')
            if target is not None and target.get('bus') == 'sata':
                issues.append({'severity': 'ERROR', 'message': "VM has a SATA disk, which is NOT migratable live."})
                break
            else:
                issues.append({'severity': 'INFO', 'message': "No SATA disk on VM"})

        if root.find(".//devices/filesystem[@type='mount']") is not None:
            issues.append({'severity': 'ERROR', 'message': "VM uses filesystem pass-through, which is incompatible with live migration."})
        else:
            issues.append({'severity': 'INFO', 'message': "VM is NOT using filesystem pass-through,"})

        if root.find(".//devices/hostdev") is not None:
            issues.append({'severity': 'ERROR', 'message': "VM uses PCI or USB pass-through (hostdev), which is not supported for live migration."})
        else:
            issues.append({'severity': 'INFO', 'message': "VM dont uses PCI or USB pass-through (hostdev)"})

    disk_paths = []
    for disk in root.findall(".//devices/disk"):
        source = disk.find('source')
        if source is not None:
            path = source.get('file') or source.get('dev')
            if path:
                disk_paths.append(path)
            elif source.get('pool') and source.get('volume'):
                pool_name = source.get('pool')
                try:
                    dest_pool = dest_conn.storagePoolLookupByName(pool_name)
                    if not dest_pool.isActive():
                        issues.append({'severity': 'ERROR', 'message': f"Storage pool '{pool_name}' is not active on destination host."})
                    else:
                        dest_pool_xml = ET.fromstring(dest_pool.XMLDesc(0))
                        type_elem = dest_pool_xml.find('type')
                        dest_pool_type = type_elem.text if type_elem is not None else "unknown"
                        if dest_pool_type not in ['netfs', 'iscsi', 'glusterfs', 'rbd', 'nfs']:
                            issues.append({'severity': 'WARNING', 'message': f"Storage pool '{pool_name}' on destination is of type '{dest_pool_type}', which may not be shared. Live migration requires shared storage."})
                except libvirt.libvirtError:
                    issues.append({'severity': 'WARNING', 'message': f"Storage pool '{pool_name}' not found on destination host."})

    if disk_paths:
        issues.append({'severity': 'INFO', 'message': "The VM uses disk images at the following paths. For migration to succeed, these paths MUST be accessible on the destination host:"})
        for path in disk_paths:
            issues.append({'severity': 'INFO', 'message': f"  - {path}"})
        issues.append({'severity': 'INFO', 'message': "This usually means using a shared storage system like NFS or iSCSI, mounted at the same location on both hosts."})

    return issues

def commit_disk_changes(domain: libvirt.virDomain, disk_path: str, bandwidth: int = 0):
    """
    Commits changes from the overlay image back to its backing file (base image).
    This flattens the chain, merging the overlay content into the base.
    Uses 'blockCommit' with active pivot.
    """
    if not domain:
        raise ValueError("Invalid domain object.")

    # We need to find the target device name (e.g. vda) from the disk path
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    disk_target = None

    for disk in root.findall(".//disk"):
        source = disk.find("source")
        # Check 'file' attribute or 'dev' attribute or pool/vol
        path = None
        if source is not None:
            if "file" in source.attrib:
                path = source.attrib["file"]
            elif "dev" in source.attrib:
                path = source.attrib["dev"]
            elif "pool" in source.attrib and "volume" in source.attrib:
                # Resolve volume to path
                pool_name = source.attrib["pool"]
                vol_name = source.attrib["volume"]
                try:
                    pool = domain.connect().storagePoolLookupByName(pool_name)
                    vol = pool.storageVolLookupByName(vol_name)
                    path = vol.path()
                except libvirt.libvirtError:
                    pass

        if path == disk_path:
            target = disk.find("target")
            if target is not None:
                disk_target = target.get("dev")
            break

    if not disk_target:
        raise ValueError(f"Could not find disk target device for path '{disk_path}'")

    if not domain.isActive():
        raise Exception("Domain must be running to perform block commit via libvirt API.")

    # Find the backing store path to use as base
    # We need the volume object for the current disk path
    try:
        conn = domain.connect()
        # Locate volume from path
        vol, _ = _find_vol_by_path(conn, disk_path)
        if not vol:
            raise Exception(f"Could not find volume for path '{disk_path}'")

        # Get XML to find backingStore
        vol_xml = vol.XMLDesc(0)
        vol_root = ET.fromstring(vol_xml)
        backing_store = vol_root.find("backingStore")
        if backing_store is None:
            raise Exception(f"Volume '{disk_path}' does not have a backing store (it is not an overlay).")
        
        backing_path_elem = backing_store.find("path")
        if backing_path_elem is None or not backing_path_elem.text:
            raise Exception(f"Could not determine backing file path for '{disk_path}'")

        base_path = backing_path_elem.text

        logging.info(f"Starting blockCommit for disk '{disk_target}' ({disk_path}) into base '{base_path}'...")
        flags = libvirt.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE
        
        domain.blockCommit(
            disk_target,
            base_path,
            disk_path,
            bandwidth,
            flags
        )

        # Monitor the job
        import time
        while True:
            job_info = domain.blockJobInfo(disk_target, 0)
            if not job_info:
                break

            cur = job_info.get('cur', 0)
            end = job_info.get('end', 0)

            if cur == end and end > 0:
                logging.info(f"Block commit synchronized. Pivoting '{disk_target}'...")
                try:
                    # VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT = 2
                    domain.blockJobAbort(disk_target, libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT)
                    logging.info("Pivot successful.")
                except libvirt.libvirtError as e:
                     logging.error(f"Pivot failed: {e}")
                     domain.blockJobAbort(disk_target, libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_ASYNC)
                break

            time.sleep(0.5)

        logging.info(f"Block commit completed for '{disk_target}'.")

    except libvirt.libvirtError as e:
        raise Exception(f"Libvirt error during block commit: {e}")
    except Exception as e:
        raise Exception(f"Error during block commit: {e}")


def attach_usb_device(domain: libvirt.virDomain, vendor_id: str, product_id: str):
    """
    Attaches a host USB device to the specified VM.
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        pass

    # vendor/product ID in hostdev XML is for libvirt to find it.
    device_xml = f"""
    <hostdev mode='subsystem' type='usb' managed='yes'>
      <source>
        <vendor id='{vendor_id}'/>
        <product id='{product_id}'/>
      </source>
    </hostdev>
    """

    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

    try:
        domain.attachDeviceFlags(device_xml, flags)
    except libvirt.libvirtError as e:
        msg = f"Failed to attach USB device {vendor_id}:{product_id}: {e}"
        logging.error(msg)
        raise Exception(msg) from e

def create_vm_snapshot(domain: libvirt.virDomain, name: str, description: str = "", quiesce: bool = False):
    """
    Creates a snapshot for the VM.
    """
    invalidate_cache(get_internal_id(domain))

    xml = f"<domainsnapshot><name>{name}</name>"
    if description:
        xml += f"<description>{description}</description>"
    xml += "</domainsnapshot>"

    flags = 0
    if quiesce:
        flags |= libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE

    try:
        domain.snapshotCreateXML(xml, flags)
    except libvirt.libvirtError as e:
        msg = f"Failed to create snapshot '{name}': {e}"
        logging.error(msg)
        raise libvirt.libvirtError(msg) from e

@log_function_call
def restore_vm_snapshot(domain: libvirt.virDomain, snapshot_name: str):
    """
    Restores the VM to a specific snapshot.
    """
    invalidate_cache(get_internal_id(domain))
    try:
        snapshot = domain.snapshotLookupByName(snapshot_name, 0)
        domain.revertToSnapshot(snapshot, 0)
    except libvirt.libvirtError as e:
        msg = f"Failed to restore snapshot '{snapshot_name}': {e}"
        logging.error(msg)
        raise Exception(msg) from e

@log_function_call
def delete_vm_snapshot(domain: libvirt.virDomain, snapshot_name: str):
    """
    Deletes a snapshot from the VM.
    """
    invalidate_cache(get_internal_id(domain))
    try:
        snapshot = domain.snapshotLookupByName(snapshot_name, 0)
        snapshot.delete(0)
    except libvirt.libvirtError as e:
        msg = f"Failed to delete snapshot '{snapshot_name}': {e}"
        logging.error(msg)
        raise Exception(msg) from e

def detach_usb_device(domain: libvirt.virDomain, vendor_id: str, product_id: str):
    """
    Detaches a host USB device from the specified VM.
    The device is identified by its vendor and product ID.
    """
    if not domain:
        raise ValueError("Invalid domain object.")

    xml = f"""
<hostdev mode='subsystem' type='usb'>
  <source>
    <vendor id='{vendor_id}'/>
    <product id='{product_id}'/>
  </source>
</hostdev>
"""
    flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG
    domain.detachDeviceFlags(xml, flags)

    invalidate_cache(get_internal_id(domain))


def add_serial_console(domain: libvirt.virDomain):
    """Adds a PTY-based serial console to the VM."""
    if not domain:
        raise ValueError("Invalid domain object.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')

    used_ports = [
        int(target.get("port"))
        for target in root.findall(".//serial/target")
        if target.get("port") and target.get("port").isdigit()
    ]
    port = 0
    while port in used_ports:
        port += 1

    serial_elem = ET.SubElement(devices, 'serial', type='pty')
    ET.SubElement(serial_elem, 'target', port=str(port))

    console_elem = ET.SubElement(devices, 'console', type='pty')
    ET.SubElement(console_elem, 'target', type='serial', port=str(port))

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)
    invalidate_cache(get_internal_id(domain))

    if domain.isActive():
        serial_xml_str = ET.tostring(serial_elem, 'unicode')
        console_xml_str = ET.tostring(console_elem, 'unicode')
        try:
            domain.attachDeviceFlags(serial_xml_str, libvirt.VIR_DOMAIN_AFFECT_LIVE)
            domain.attachDeviceFlags(console_xml_str, libvirt.VIR_DOMAIN_AFFECT_LIVE)
        except libvirt.libvirtError as e:
            raise libvirt.libvirtError(
                f"Live attach failed: {e}. "
                "The configuration has been saved and will apply on the next reboot."
            )
    return port

def remove_serial_console(domain: libvirt.virDomain, port: str):
    """Removes a serial console from the VM based on its port."""
    if not domain:
        raise ValueError("Invalid domain object.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    devices = root.find('devices')
    if devices is None:
        return

    serial_elem = root.find(f".//devices/serial/target[@port='{port}']/..")
    console_elem = root.find(f".//devices/console/target[@port='{port}']/..")

    if serial_elem is None and console_elem is None:
        raise ValueError(f"No serial or console device found on port {port}.")

    serial_xml_str = ET.tostring(serial_elem, 'unicode') if serial_elem is not None else None
    console_xml_str = ET.tostring(console_elem, 'unicode') if console_elem is not None else None

    if serial_elem is not None:
        devices.remove(serial_elem)
    if console_elem is not None:
        devices.remove(console_elem)

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)
    invalidate_cache(get_internal_id(domain))

    if domain.isActive():
        try:
            if console_xml_str:
                domain.detachDeviceFlags(console_xml_str, libvirt.VIR_DOMAIN_AFFECT_LIVE)
            if serial_xml_str:
                domain.detachDeviceFlags(serial_xml_str, libvirt.VIR_DOMAIN_AFFECT_LIVE)
        except libvirt.libvirtError as e:
             raise libvirt.libvirtError(
                f"Live detach failed: {e}. "
                "The configuration has been saved and will apply on the next reboot."
            )

def add_usb_device(domain: libvirt.virDomain, usb_type: str, model: str):
    """
    Adds a USB controller to a VM.
    
    Args:
        domain: libvirt domain object
        usb_type: Type of device ('usb' for USB controller)
        model: Model of USB controller ('usb2' or 'usb3')
    """
    if not domain:
        raise ValueError("Invalid domain object.")

    if usb_type != 'usb':
        raise ValueError(f"Unsupported USB type: {usb_type}")

    # Determine next available index for USB controllers
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    usb_controllers = root.findall(".//controller[@type='usb']")

    indices = [int(c.get('index', '0')) for c in usb_controllers if c.get('index')]
    next_index = max(indices) + 1 if indices else 0

    if model == 'usb2':
        # usb2 often needs multiple controllers (UHCI/EHCI) for full compatibility,
        # but here we follow the existing pattern of adding one.
        controller_model = 'piix3-uhci'
    elif model == 'usb3':
        controller_model = 'qemu-xhci'
    else:
        raise ValueError(f"Unsupported USB model: {model}")

    controller_xml = f"<controller type='usb' index='{next_index}' model='{controller_model}'/>"

    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

    domain.attachDeviceFlags(controller_xml, flags)
    invalidate_cache(get_internal_id(domain))


def add_scsi_controller(domain: libvirt.virDomain, model: str = 'virtio-scsi'):
    """
    Adds a SCSI controller to a VM.
    
    Args:
        domain: libvirt domain object
        model: SCSI controller model ('virtio-scsi')
    """
    if not domain:
        raise ValueError("Invalid domain object.")

    if model != 'virtio-scsi':
        raise ValueError(f"Unsupported SCSI model: {model}")

    # Determine next available index for SCSI controllers
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    scsi_controllers = root.findall(".//controller[@type='scsi']")

    indices = [int(c.get('index', '0')) for c in scsi_controllers if c.get('index')]
    next_index = max(indices) + 1 if indices else 0

    controller_xml = f"<controller type='scsi' index='{next_index}' model='virtio-scsi'/>"

    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

    domain.attachDeviceFlags(controller_xml, flags)
    invalidate_cache(get_internal_id(domain))


def remove_usb_device(domain: libvirt.virDomain, model: str, index: str):
    """
    Removes a USB controller from a VM.

    Args:
        domain: libvirt domain object
        model: Model of USB controller ('usb2' or 'usb3')
        index: The index of the controller to remove.
    """
    if not domain:
        raise ValueError("Invalid domain object.")
    controller_model_libvirt = 'piix3-uhci' if model == 'usb2' else 'qemu-xhci' if model == 'usb3' else None

    if not controller_model_libvirt:
        raise ValueError(f"Unsupported USB model: {model}")

    # Find the controller in XML to get its full definition
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    target_controller = None

    for c in root.findall(".//controller[@type='usb']"):
        if c.get('model') == controller_model_libvirt and c.get('index') == index:
            target_controller = c
            break

    if target_controller is None:
        raise ValueError(f"USB controller with model '{controller_model_libvirt}' and index '{index}' not found.")

    controller_xml = ET.tostring(target_controller, encoding='unicode')
    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG

    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

    domain.detachDeviceFlags(controller_xml, flags)
    invalidate_cache(get_internal_id(domain))

def remove_scsi_controller(domain: libvirt.virDomain, model: str, index: str):
    """
    Removes a SCSI controller from a VM.

    Args:
        domain: libvirt domain object
        model: SCSI controller model ('virtio-scsi')
        index: The index of the controller to remove.
    """
    if not domain:
        raise ValueError("Invalid domain object.")

    if model != 'virtio-scsi':
        raise ValueError(f"Unsupported SCSI model: {model}")

    # Find the controller in XML to get its full definition
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    target_controller = None

    for c in root.findall(".//controller[@type='scsi']"):
        if c.get('model') == model and c.get('index') == index:
            target_controller = c
            break

    domain.detachDeviceFlags(controller_xml, flags)
    invalidate_cache(get_internal_id(domain))

def create_external_overlay(domain: libvirt.virDomain, disk_path: str, overlay_name: str):
    """
    Creates an overlay for the given disk and updates the VM to use it.
    The VM must be stopped.
    """
    if domain.isActive():
        raise Exception("VM must be stopped to create a manual overlay.")

    conn = domain.connect()
    vol, pool = _find_vol_by_path(conn, disk_path)
    if not vol:
        raise Exception(f"Disk '{disk_path}' is not a managed volume or could not be found.")

    # Create overlay
    try:
        new_vol = create_overlay_volume(pool, overlay_name, disk_path)
    except Exception as e:
        raise Exception(f"Failed to create overlay volume: {e}")

    # Update VM XML
    try:
        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)

        updated = False
        for disk in root.findall(".//disk"):
            source = disk.find("source")
            if source is not None:
                path = None
                if "file" in source.attrib:
                    path = source.attrib["file"]
                elif "dev" in source.attrib:
                    path = source.attrib["dev"]
                elif "pool" in source.attrib and "volume" in source.attrib:
                    # We have pool and volume name
                    # Retrieve pool/vol from XML
                    p_name = source.attrib["pool"]
                    v_name = source.attrib["volume"]
                    if p_name == pool.name() and v_name == vol.name():
                        path = disk_path # Effective match

                # Check if this is the disk we want to update
                match = False
                if path and path == disk_path:
                    match = True
                elif source.get("pool") == pool.name() and source.get("volume") == vol.name():
                    match = True

                if match:
                    # Update source to new volume
                    # If it was file/dev, remove those attributes
                    if "file" in source.attrib:
                        del source.attrib["file"]
                    if "dev" in source.attrib:
                        del source.attrib["dev"]

                    source.set("pool", pool.name())
                    source.set("volume", new_vol.name())

                    # Ensure disk type is volume
                    disk.set("type", "volume")

                    # Ensure driver type is qcow2
                    driver = disk.find("driver")
                    if driver is None:
                        driver = ET.SubElement(disk, "driver", name="qemu")
                    driver.set("type", "qcow2")

                    # Store backing chain info in metadata
                    backing_chain_elem = _get_backing_chain_elem(root)
                    new_vol_path = new_vol.path()

                    # Check if entry already exists (cleanup if replacing - though rare for new overlay)
                    for entry in backing_chain_elem.findall(f'{{{VIRTUI_MANAGER_NS}}}overlay'):
                        if entry.get('path') == new_vol_path:
                            backing_chain_elem.remove(entry)

                    overlay_elem = ET.SubElement(backing_chain_elem, f'{{{VIRTUI_MANAGER_NS}}}overlay')
                    overlay_elem.set('path', new_vol_path)
                    overlay_elem.set('backing', disk_path)

                    updated = True
                    break

        if updated:
            conn.defineXML(ET.tostring(root, encoding='unicode'))
        else:
            # Cleanup if we didn't find the disk in XML to update
            new_vol.delete(0)
            raise Exception(f"Could not find disk entry in VM XML for path '{disk_path}'")

    except Exception as e:
        # Try to cleanup overlay if XML update failed
        try:
            if 'new_vol' in locals():
                new_vol.delete(0)
        except:
            pass
        raise Exception(f"Failed to update VM configuration: {e}")

def discard_overlay(domain: libvirt.virDomain, disk_path: str):
    """
    Updates the VM to use the backing file of the current overlay, 
    and deletes the overlay volume.
    The VM must be stopped.
    """
    if domain.isActive():
        raise Exception("VM must be stopped to discard overlay.")

    conn = domain.connect()
    vol, pool = _find_vol_by_path(conn, disk_path)
    if not vol:
        raise Exception(f"Disk '{disk_path}' is not a managed volume.")

    xml = vol.XMLDesc(0)
    root = ET.fromstring(xml)
    backing = root.find("backingStore")
    if backing is None:
        raise Exception("Disk is not an overlay (no backing store).")

    backing_path_elem = backing.find("path")
    if backing_path_elem is None or not backing_path_elem.text:
        raise Exception("Could not determine backing file path.")

    backing_path = backing_path_elem.text

    # Check if backing path corresponds to a volume (to set pool/vol in XML properly)
    backing_vol, backing_pool = _find_vol_by_path(conn, backing_path)

    # Update VM XML
    vm_xml = domain.XMLDesc(0)
    vm_root = ET.fromstring(vm_xml)

    updated = False
    for disk in vm_root.findall(".//disk"):
        source = disk.find("source")
        if source is not None:
            # Match logic
            path = None
            if "file" in source.attrib:
                path = source.attrib["file"]
            elif "dev" in source.attrib:
                path = source.attrib["dev"]
            elif "pool" in source.attrib and "volume" in source.attrib:
                p_name = source.attrib["pool"]
                v_name = source.attrib["volume"]
                if pool and p_name == pool.name() and v_name == vol.name():
                    path = disk_path

            # Check match
            match = False
            if path and path == disk_path:
                match = True
            elif pool and source.get("pool") == pool.name() and source.get("volume") == vol.name():
                match = True

            if match:
                # Update source to backing_path
                if backing_pool and backing_vol:
                    source.set("pool", backing_pool.name())
                    source.set("volume", backing_vol.name())
                    if "file" in source.attrib: del source.attrib["file"]
                    if "dev" in source.attrib: del source.attrib["dev"]
                else:
                    # Just set file path
                    source.set("file", backing_path)
                    if "pool" in source.attrib: del source.attrib["pool"]
                    if "volume" in source.attrib: del source.attrib["volume"]

                updated = True
                break

    if updated:
        # Remove backing chain info from metadata
        backing_chain_elem = vm_root.find(f".//{{{VIRTUI_MANAGER_NS}}}backing-chain")
        if backing_chain_elem is not None:
            for entry in backing_chain_elem.findall(f'{{{VIRTUI_MANAGER_NS}}}overlay'):
                if entry.get('path') == disk_path:
                    backing_chain_elem.remove(entry)

            # If backing chain is empty, remove it
            if len(list(backing_chain_elem)) == 0:
                 vmanager_elem = vm_root.find(f".//{{{VIRTUI_MANAGER_NS}}}virtuimanager")
                 if vmanager_elem is not None:
                     vmanager_elem.remove(backing_chain_elem)

        domain.connect().defineXML(ET.tostring(vm_root, encoding='unicode'))
        # Delete the old overlay volume
        try:
            vol.delete(0)
        except Exception as e:
            logging.warning(f"Failed to delete overlay volume '{disk_path}' after reverting: {e}")
    else:
        raise Exception("Could not find disk in VM configuration.")

@log_function_call
def add_vm_channel(domain: libvirt.virDomain, channel_type: str, target_name: str, target_type: str = 'virtio', target_state: str = None):
    """
    Adds a channel device to a VM.
    The VM must be stopped.
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to add a channel.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')

    channel = ET.SubElement(devices, 'channel', type=channel_type)
    target_attrs = {'type': target_type, 'name': target_name}
    if target_state:
        target_attrs['state'] = target_state
    
    ET.SubElement(channel, 'target', **target_attrs)

    # For qemu-guest-agent (unix socket), we usually need a source too (bind)
    if channel_type == 'unix':
        ET.SubElement(channel, 'source', mode='bind')

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)

@log_function_call
def remove_vm_channel(domain: libvirt.virDomain, target_name: str):
    """
    Removes a channel device from a VM by its target name.
    The VM must be stopped.
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to remove a channel.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    devices = root.find('devices')
    if devices is None:
        raise ValueError("Could not find <devices> in VM XML.")

    channel_to_remove = None
    for channel in devices.findall('channel'):
        target = channel.find('target')
        if target is not None and target.get('name') == target_name:
            channel_to_remove = channel
            break
    
    if channel_to_remove is None:
        raise ValueError(f"Channel with target name '{target_name}' not found.")

    devices.remove(channel_to_remove)

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)


def set_vm_cputune(domain: libvirt.virDomain, vcpupin_list: list[dict]):
    """
    Sets cputune configuration (vcpupin) for a VM.
    The VM must be stopped.
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change CPU Tune settings.")

    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    # Remove existing cputune
    cputune = root.find('cputune')
    if cputune is not None:
        root.remove(cputune)

    if vcpupin_list:
        cputune = ET.SubElement(root, 'cputune')
        for pin in vcpupin_list:
            ET.SubElement(cputune, 'vcpupin', vcpu=str(pin['vcpu']), cpuset=str(pin['cpuset']))

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)

def set_vm_numatune(domain: libvirt.virDomain, mode: str, nodeset: str):
    """
    Sets numatune configuration for a VM.
    The VM must be stopped.
    """
    invalidate_cache(get_internal_id(domain))
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped to change NUMA Tune settings.")
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    # Remove existing numatune
    numatune = root.find('numatune')
    if numatune is not None:
        root.remove(numatune)

    if mode and mode != "None":
        numatune = ET.SubElement(root, 'numatune')
        ET.SubElement(numatune, 'memory', mode=mode, nodeset=nodeset)

    new_xml = ET.tostring(root, encoding='unicode')
    domain.connect().defineXML(new_xml)
