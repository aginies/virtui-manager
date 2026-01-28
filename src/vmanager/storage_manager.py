"""
Module for managing libvirt storage pools and volumes.
"""
from typing import List, Dict, Any
import logging
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
import threading
from functools import lru_cache
import subprocess
import libvirt
from .libvirt_utils import (
        _find_vol_by_path,
        )
from .vm_queries import get_vm_disks_info


def _safe_is_pool_active(pool: libvirt.virStoragePool) -> bool:
    """
    Safely check if a storage pool is active without blocking the UI.
    Returns False if the check fails or times out.
    """
    try:
        return pool.isActive()
    except libvirt.libvirtError as e:
        logging.debug(f"Failed to check if pool '{pool.name()}' is active: {e}")
        return False
    except Exception as e:
        logging.debug(f"Unexpected error checking pool '{pool.name()}' status: {e}")
        return False


def _ensure_pool_active(pool: libvirt.virStoragePool) -> bool:
    """
    Ensure a storage pool is active. If not active, try to activate it.
    Returns True if pool is active (or was successfully activated), False otherwise.
    """
    if _safe_is_pool_active(pool):
        return True
    
    try:
        logging.info(f"Pool '{pool.name()}' is not active, attempting to activate...")
        pool.create(0)
        return True
    except libvirt.libvirtError as e:
        logging.error(f"Failed to activate pool '{pool.name()}': {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error activating pool '{pool.name()}': {e}")
        return False


def _safe_get_pool_info(pool: libvirt.virStoragePool) -> tuple:
    """
    Safely get pool info without blocking the UI.
    Returns (capacity, allocation, available) or (0, 0, 0) on failure.
    """
    try:
        info = pool.info()
        return info[1], info[2], info[3]  # capacity, allocation, available
    except libvirt.libvirtError as e:
        logging.debug(f"Failed to get info for pool '{pool.name()}': {e}")
        return 0, 0, 0
    except Exception as e:
        logging.debug(f"Unexpected error getting info for pool '{pool.name()}': {e}")
        return 0, 0, 0


def _safe_get_pool_autostart(pool: libvirt.virStoragePool) -> bool:
    """
    Safely get pool autostart setting without blocking the UI.
    Returns False on failure.
    """
    try:
        return pool.autostart() == 1
    except libvirt.libvirtError as e:
        logging.debug(f"Failed to get autostart for pool '{pool.name()}': {e}")
        return False
    except Exception as e:
        logging.debug(f"Unexpected error getting autostart for pool '{pool.name()}': {e}")
        return False


def _safe_refresh_pool(pool: libvirt.virStoragePool) -> bool:
    """
    Safely refresh a storage pool without blocking the UI.
    Returns True on success, False on failure.
    """
    try:
        pool.refresh(0)
        return True
    except libvirt.libvirtError as e:
        logging.debug(f"Failed to refresh pool '{pool.name()}': {e}")
        return False
    except Exception as e:
        logging.debug(f"Unexpected error refreshing pool '{pool.name()}': {e}")
        return False


def _safe_get_volume_info(vol: libvirt.virStorageVol) -> tuple:
    """
    Safely get volume info without blocking the UI.
    Returns (type, capacity, allocation) or (0, 0, 0) on failure.
    """
    try:
        vol_type, capacity, allocation = _safe_get_volume_info(vol)
        return vol_type, capacity, allocation
    except libvirt.libvirtError as e:
        logging.debug(f"Failed to get info for volume '{vol.name()}': {e}")
        return 0, 0, 0
    except Exception as e:
        logging.debug(f"Unexpected error getting info for volume '{vol.name()}': {e}")
        return 0, 0, 0


def _safe_get_volume_path(vol: libvirt.virStorageVol) -> str:
    """
    Safely get volume path without blocking the UI.
    Returns empty string on failure.
    """
    try:
        return vol.path()
    except libvirt.libvirtError as e:
        logging.debug(f"Failed to get path for volume '{vol.name()}': {e}")
        return ""
    except Exception as e:
        logging.debug(f"Unexpected error getting path for volume '{vol.name()}': {e}")
        return ""

@lru_cache(maxsize=16)
def list_storage_pools(conn: libvirt.virConnect) -> List[Dict[str, Any]]:
    """
    Lists all storage pools with their status and details.
    """
    if not conn:
        return []

    pools_info = []
    try:
        pools = conn.listAllStoragePools(0)
        for pool in pools:
            try:
                # Try to get basic info
                try:
                    name = pool.name()
                except libvirt.libvirtError:
                    name = "Unknown Pool"

                is_active = _safe_is_pool_active(pool)
                capacity, allocation, _ = _safe_get_pool_info(pool)
                autostart = _safe_get_pool_autostart(pool)
                
                pools_info.append({
                    'name': name,
                    'pool': pool,
                    'status': 'active' if is_active else 'inactive',
                    'autostart': autostart,
                    'capacity': capacity,
                    'allocation': allocation,
                })
            except libvirt.libvirtError as e:
                # If we fail to get details (e.g. NFS down), still list the pool but as unavailable
                if 'name' not in locals():
                     try:
                        name = pool.name()
                     except:
                        name = "Unknown Pool"

                logging.warning(f"Failed to get details for pool '{name}': {e}")
                pools_info.append({
                    'name': name,
                    'pool': pool,
                    'status': 'unavailable',
                    'autostart': False,
                    'capacity': 0,
                    'allocation': 0,
                    'error': str(e)
                })
    except libvirt.libvirtError:
        return []

    return pools_info

@lru_cache(maxsize=32)
def list_storage_volumes(pool: libvirt.virStoragePool) -> List[Dict[str, Any]]:
    """
    Lists all storage volumes in a given pool.
    """
    volumes_info = []
    if not pool or not _safe_is_pool_active(pool):
        return volumes_info

    try:
        vol_names = pool.listVolumes()
        for name in vol_names:
            try:
                vol = pool.storageVolLookupByName(name)
                vol_type, capacity, allocation = _safe_get_volume_info(vol)
                volumes_info.append({
                    'name': name,
                    'volume': vol,
                    'type': vol_type,
                    'capacity': capacity,
                    'allocation': allocation,
                })
            except libvirt.libvirtError:
                continue
    except libvirt.libvirtError:
        pass # Or log error
    return volumes_info

def set_pool_active(pool: libvirt.virStoragePool, active: bool):
    """
    Sets a storage pool's active state.
    """
    try:
        if active:
            pool.create(0)
        else:
            pool.destroy()
    except libvirt.libvirtError as e:
        state = "activate" if active else "deactivate"
        msg = f"Error trying to {state} pool '{pool.name()}': {e}"
        logging.error(msg)
        raise Exception(msg) from e

def set_pool_autostart(pool: libvirt.virStoragePool, autostart: bool):
    """
    Sets a storage pool's autostart flag.
    """
    try:
        pool.setAutostart(1 if autostart else 0)
    except libvirt.libvirtError as e:
        msg = f"Error setting autostart for pool '{pool.name()}': {e}"
        logging.error(msg)
        raise Exception(msg) from e

def calculate_qcow2_cache_sizes(disk_size_bytes: int, cluster_size_bytes: int = 65536) -> tuple[int, int]:
    """
    Calculates the recommended L2 and refcount cache sizes for a qcow2 image
    to cover the entire disk, ensuring optimal performance.
    
    Returns:
        (l2_cache_size_bytes, refcount_cache_size_bytes)
    """
    # L2 cache needed to cover the full disk
    # Formula: disk_size_bytes / cluster_size_bytes * 8

    # Ensure cluster_size_bytes is valid to avoid division by zero
    if cluster_size_bytes <= 0:
        cluster_size_bytes = 65536

    l2_cache_size = (disk_size_bytes // cluster_size_bytes) * 8

    # Enforce a reasonable minimum (e.g., 1MB)
    min_l2_cache = 1048576 # 1MB
    if l2_cache_size < min_l2_cache:
        l2_cache_size = min_l2_cache

    # Refcount cache: usually 1/4 of L2 cache is sufficient
    refcount_cache_size = l2_cache_size // 4

    return l2_cache_size, refcount_cache_size

def create_storage_pool(conn, name, pool_type, target, source_host=None, source_path=None, source_format=None):
    """
    Creates and starts a new storage pool.
    """
    xml = f"<pool type='{pool_type}'>"
    xml += f"<name>{name}</name>"
    if pool_type == 'dir':
        xml += f"<target><path>{target}</path></target>"
    elif pool_type == 'netfs':
        xml += "<source>"
        if source_host:
            xml += f"<host name='{source_host}'/>"
        if source_path:
            xml += f"<dir path='{source_path}'/>"
        if source_format:
            xml += f"<format type='{source_format}'/>"
        xml += "</source>"
        xml += f"<target><path>{target}</path></target>"
    xml += "</pool>"
    pool = conn.storagePoolDefineXML(xml, 0)
    pool.create(0)
    pool.setAutostart(1)
    return pool

def create_volume(pool: libvirt.virStoragePool, name: str, size_gb: int, vol_format: str,
                  preallocation: str = None, lazy_refcounts: bool = False, cluster_size: str = None):
    """
    Creates a new storage volume in a pool.
    """
    if not _ensure_pool_active(pool):
        msg = f"Pool '{pool.name()}' is not active and could not be activated."
        logging.error(msg)
        raise Exception(msg)

    size_bytes = size_gb * 1024 * 1024 * 1024

    # Build features
    features_xml = ""
    if lazy_refcounts and vol_format == 'qcow2':
        features_xml = "<features><lazy_refcounts/></features>"

    # Preallocation handling
    # Libvirt supports preallocation via allocation element or target/format/features
    # For qcow2, metadata preallocation is common.
    
    format_attr = f"type='{vol_format}'"
    
    # Cluster size
    cluster_xml = ""
    cluster_size_bytes = 65536 # Default
    
    if cluster_size and vol_format == 'qcow2':
        # cluster_size can be '1024k', '64k' etc.
        unit = 'B'
        value = cluster_size
        if cluster_size.endswith('k'):
            unit = 'KiB'
            value = cluster_size[:-1]
            cluster_size_bytes = int(value) * 1024
        elif cluster_size.endswith('M'):
            unit = 'MiB'
            value = cluster_size[:-1]
            cluster_size_bytes = int(value) * 1024 * 1024
        else:
             try:
                 cluster_size_bytes = int(cluster_size)
             except ValueError:
                 pass # keep default
        
        cluster_xml = f"<cluster_size unit='{unit}'>{value}</cluster_size>"

    if vol_format == 'qcow2':
        l2_size, ref_size = calculate_qcow2_cache_sizes(size_bytes, cluster_size_bytes)
        logging.info(f"Recommended caches for volume '{name}': l2-cache-size={l2_size}, refcount-cache-size={ref_size}")

    vol_xml = f"""
    <volume>
        <name>{name}</name>        <capacity unit="bytes">{size_bytes}</capacity>
    """

    if preallocation == 'full' or preallocation == 'falloc':
         vol_xml += f"        <allocation unit='bytes'>{size_bytes}</allocation>\n"

    vol_xml += f"""
        <target>
            <format {format_attr}/>
            {features_xml}
            {cluster_xml}
        </target>
    </volume>
    """
    try:
        pool.createXML(vol_xml, 0)
    except libvirt.libvirtError as e:
        msg = f"Error creating volume '{name}': {e}"
        logging.error(msg)
        raise Exception(msg) from e


def attach_volume(pool: libvirt.virStoragePool, name: str, path: str, vol_format: str):
    """
    Attaches an existing file as a storage volume in a pool.
    """
    if not _ensure_pool_active(pool):
        msg = f"Pool '{pool.name()}' is not active and could not be activated."
        logging.error(msg)
        raise Exception(msg)

    if not os.path.exists(path):
        msg = f"File not found at path: {path}"
        logging.error(msg)
        raise Exception(msg)

    capacity = os.path.getsize(path)
    pool_xml = pool.XMLDesc(0)
    root = ET.fromstring(pool_xml)
    pool_type = root.get("type")

    if pool_type == 'dir':
        target_path_elem = root.find("target/path")
        if target_path_elem is None:
            raise Exception("Could not determine target path for 'dir' pool.")
        pool_target_path = target_path_elem.text

        # The destination path for the volume inside the pool's directory
        dest_path = os.path.join(pool_target_path, name)

        if os.path.abspath(path) != os.path.abspath(dest_path):
            # If the source file is not already in the target directory with the correct name, copy it.
            logging.info(f"Copying file from {path} to {dest_path}")
            try:
                shutil.copy(path, dest_path)
            except Exception as e:
                msg = f"Failed to copy file from {path} to {dest_path}: {e}"
                logging.error(msg)
                raise Exception(msg) from e

        vol_xml = f"""
        <volume>
            <name>{name}</name>
            <capacity unit="bytes">{capacity}</capacity>
            <target>
                <format type='{vol_format}'/>
            </target>
        </volume>
        """
    else:
        vol_xml = f"""
        <volume>
            <name>{name}</name>
            <capacity unit="bytes">{capacity}</capacity>
            <target>
                <path>{path}</path>
                <format type='{vol_format}'/>
            </target>
        </volume>
        """

    try:
        # Refresh the pool to make sure libvirt knows about the file if it was just copied.
        _safe_refresh_pool(pool)
        vol = pool.storageVolLookupByName(name)
        if vol:
            logging.warning(f"Volume '{name}' already exists in pool '{pool.name()}'. Not creating.")
            return
    except libvirt.libvirtError:
        pass

    try:
        pool.createXML(vol_xml, 0)
        # Refresh again after creating the volume from XML
        _safe_refresh_pool(pool)
    except libvirt.libvirtError as e:
        # If creation fails, attempt to clean up the copied file
        if pool_type == 'dir' and 'dest_path' in locals() and os.path.exists(dest_path):
            if os.path.abspath(path) != os.path.abspath(dest_path):
                os.remove(dest_path)
        msg = f"Error attaching volume '{name}': {e}"
        logging.error(msg)
        raise Exception(msg) from e


def create_overlay_volume(pool: libvirt.virStoragePool, name: str, backing_vol_path: str, backing_vol_format: str = 'qcow2') -> libvirt.virStorageVol:
    """
    Creates a qcow2 overlay volume backed by another volume (backing file).
    The new volume will record changes, while the backing file remains untouched.
    """
    if not _ensure_pool_active(pool):
        msg = f"Pool '{pool.name()}' is not active and could not be activated."
        logging.error(msg)
        raise Exception(msg)

    conn = pool.connect()
    backing_vol, _ = _find_vol_by_path(conn, backing_vol_path)

    if not backing_vol:
        raise Exception(f"Could not find backing volume for path '{backing_vol_path}' to determine capacity.")

    _, capacity, _ = _safe_get_volume_info(backing_vol)

    vol_xml = f"""
    <volume>
        <name>{name}</name>
        <capacity unit="bytes">{capacity}</capacity>
        <target>
            <format type='qcow2'/>
        </target>
        <backingStore>
            <path>{backing_vol_path}</path>
            <format type='{backing_vol_format}'/>
        </backingStore>
    </volume>
    """
    try:
        vol = pool.createXML(vol_xml, 0)
        return vol
    except libvirt.libvirtError as e:
        msg = f"Error creating overlay volume '{name}': {e}"
        logging.error(msg)
        raise Exception(msg) from e

def delete_volume(vol: libvirt.virStorageVol):
    """
    Deletes a storage volume.
    """
    try:
        # The flag VIR_STORAGE_VOL_DELETE_NORMAL = 0 is for normal deletion.
        vol.delete(0)
    except libvirt.libvirtError as e:
        # Re-raise with a more informative message
        msg = f"Error deleting volume '{vol.name()}': {e}"
        logging.error(msg)
        raise Exception(msg) from e

@lru_cache(maxsize=32)
def find_vms_using_volume(conn: libvirt.virConnect, vol_path: str, vol_name: str) -> List[libvirt.virDomain]:
    """Finds VMs that are using a specific storage volume path by checking different disk types."""
    vms_using_volume = []
    if not conn:
        return vms_using_volume

    try:
        domains = conn.listAllDomains(0)
        for domain in domains:
            # Quick check to avoid parsing XML for every VM if volume name isn't there
            xml_desc = domain.XMLDesc(0)
            if vol_name not in xml_desc:
                continue

            root = ET.fromstring(xml_desc)
            for disk in root.findall('.//disk'):
                source_element = disk.find('source')
                if source_element is None:
                    continue

                # Case 1: Disk path is specified directly
                disk_path = source_element.get('file') or source_element.get('dev')
                if disk_path and disk_path == vol_path:
                    vms_using_volume.append(domain)
                    break  # Found it, move to the next domain

                # Case 2: Disk is specified by pool and volume name
                if disk.get('type') == 'volume':
                    pool_name = source_element.get('pool')
                    volume_name_from_xml = source_element.get('volume')
                    if pool_name and volume_name_from_xml:
                        try:
                            p = conn.storagePoolLookupByName(pool_name)
                            v = p.storageVolLookupByName(volume_name_from_xml)
                            if _safe_get_volume_path(v) == vol_path:
                                vms_using_volume.append(domain)
                                break  # Found it, move to the next domain
                        except libvirt.libvirtError:
                            # This can happen if the pool/volume is not found, which is not necessarily an error to halt on.
                            logging.warning(f"Could not resolve volume '{volume_name_from_xml}' in pool '{pool_name}' for VM '{domain.name()}'.")
                            continue
    except (libvirt.libvirtError, ET.ParseError) as e:
        logging.error(f"Error finding VMs using volume {vol_path}: {e}")

    return vms_using_volume

def check_domain_volumes_in_use(domain: libvirt.virDomain) -> None:
    """
    Check if any volumes used by the domain are in use by other running VMs.
    Raises a ValueError if a volume is in use.
    """
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    conn = domain.connect()

    for disk in root.findall(".//devices/disk"):
        if disk.get("device") != "disk":
            continue

        source_elem = disk.find("source")
        if source_elem is None or "pool" not in source_elem.attrib or "volume" not in source_elem.attrib:
            continue

        pool_name = source_elem.get("pool")
        vol_name = source_elem.get("volume")
        try:
            # Check against all other running domains
            for other_domain in conn.listAllDomains(libvirt.VIR_DOMAIN_RUNNING):
                if other_domain.UUIDString() == domain.UUIDString():
                    continue

                other_xml = other_domain.XMLDesc(0)
                other_root = ET.fromstring(other_xml)
                for other_disk in other_root.findall(".//devices/disk"):
                    other_source = other_disk.find("source")
                    if (other_source is not None and 
                        other_source.get("pool") == pool_name and 
                        other_source.get("volume") == vol_name):
                        raise ValueError(f"Volume '{vol_name}' is in use by running VM '{other_domain.name()}'")
        except libvirt.libvirtError:
            # Ignore errors during check (e.g., pool not found on other host)
            continue

def move_volume(conn: libvirt.virConnect, source_pool_name: str, dest_pool_name: str, volume_name: str, new_volume_name: str = None, progress_callback=None, log_callback=None) -> List[str]:
    """
    Moves a storage volume using an in-memory pipe for direct streaming.
    This method avoids intermediate disk I/O by streaming data from the source
    to the destination volume concurrently.
    """
    def log_and_callback(message):
        logging.info(message)
        if log_callback:
            log_callback(message)

    if not new_volume_name:
        new_volume_name = volume_name

    source_pool = conn.storagePoolLookupByName(source_pool_name)
    dest_pool = conn.storagePoolLookupByName(dest_pool_name)
    source_vol = source_pool.storageVolLookupByName(volume_name)

    # Check for available space before starting the move
    _, source_capacity, _ = _safe_get_volume_info(source_vol)  # in bytes

    # Check if the volume is in use by any running VMs before starting the move
    source_path = _safe_get_volume_path(source_vol)
    vms_using_volume = find_vms_using_volume(conn, source_path, source_vol.name())
    running_vms = [vm.name() for vm in vms_using_volume if vm.state()[0] == libvirt.VIR_DOMAIN_RUNNING]

    if running_vms:
        msg = f"Cannot move volume '{volume_name}' because it is in use by running VM(s): {', '.join(running_vms)}."
        log_and_callback(f"ERROR: {msg}")
        raise Exception(msg)

    if vms_using_volume:
        log_and_callback(f"Volume is used by offline VM(s):\n{[vm.name() for vm in vms_using_volume]}.\nTheir configuration will be updated after the move.\nWait Until the process is finished (can take a lot of time).")

    _, source_capacity, _ = _safe_get_volume_info(source_vol)
    source_format = "qcow2"  # Default
    try:
        source_format = ET.fromstring(source_vol.XMLDesc(0)).findtext("target/format[@type]", "qcow2")
    except (ET.ParseError, libvirt.libvirtError):
        pass  # Use default if XML parsing fails

    new_vol_xml = f"""
    <volume>
        <name>{new_volume_name}</name>
        <capacity>{source_capacity}</capacity>
        <target>
            <format type='{source_format}'/>
        </target>
    </volume>
    """
    new_vol = dest_pool.createXML(new_vol_xml, 0)
    updated_vm_names = []

    # Create a pipe for in-memory streaming
    r_fd, w_fd = os.pipe()
    log_and_callback("Starting in-memory stream for volume move...")

    download_thread = None
    upload_thread = None
    download_error = None
    upload_error = None
    download_stream = conn.newStream(0)
    upload_stream = conn.newStream(0)

    try:
        # --- Download Thread ---
        def download_volume_task(stream, write_fd, capacity, callback):
            nonlocal download_error
            try:
                log_and_callback(f"Downloading '{volume_name}'...")
                downloaded_bytes = 0

                def stream_writer_pipe(st, data, opaque_fd):
                    nonlocal downloaded_bytes, download_error
                    try:
                        os.write(opaque_fd, data)
                        downloaded_bytes += len(data)
                        if callback and capacity > 0:
                            progress = (downloaded_bytes / capacity) * 50
                            callback(progress)
                        return 0
                    except Exception as e:
                        logging.error(f"Error in stream writer pipe: {e}")
                        download_error = e
                        return -1  # Abort stream

                source_vol.download(stream, 0, capacity)
                stream.recvAll(stream_writer_pipe, write_fd)

                if download_error:
                    stream.abort()
                else:
                    stream.finish()
                log_and_callback("Download stream finished.")
            except Exception as e:
                logging.error(f"Error in download thread: {e}")

                download_error = e
                stream.abort()
            finally:
                os.close(write_fd)

        # --- Upload Thread ---
        def upload_volume_task(stream, read_fd, capacity, callback):
            nonlocal upload_error
            try:
                log_and_callback(f"Uploading to '{new_volume_name}'...")
                if callback:
                    callback(0)
                uploaded_bytes = 0

                def stream_reader_pipe(st, nbytes, opaque_fd):
                    nonlocal uploaded_bytes
                    try:
                        chunk = os.read(opaque_fd, nbytes)
                        uploaded_bytes += len(chunk)
                        if callback and capacity > 0:
                            progress = 50 + (uploaded_bytes / capacity) * 50
                            callback(progress)
                        return chunk
                    except Exception as e:
                        logging.error(f"Error in stream reader pipe: {e}")
                        raise e # Propagate error to sendAll

                new_vol.upload(stream, 0, capacity)
                stream.sendAll(stream_reader_pipe, read_fd)
                stream.finish()
                log_and_callback("Upload stream finished.")
            except Exception as e:
                logging.error(f"Error in upload thread: {e}")
                nonlocal upload_error
                upload_error = e
                stream.abort()
            finally:
                os.close(read_fd)

        # Create and start threads
        download_thread = threading.Thread(target=download_volume_task, args=(download_stream, w_fd, source_capacity, progress_callback))
        upload_thread = threading.Thread(target=upload_volume_task, args=(upload_stream, r_fd, source_capacity, progress_callback))

        download_thread.start()
        upload_thread.start()

        download_thread.join()
        upload_thread.join()

        # Check for errors during streaming
        if download_error:
            raise Exception(f"Failed to download volume: {download_error}") from download_error
        if upload_error:
            raise Exception(f"Failed to upload volume: {upload_error}") from upload_error

        log_and_callback("In-memory stream transfer complete.")
        if progress_callback:
            progress_callback(100)

        # Refresh destination pool to make the new volume visible
        log_and_callback(f"Refreshing destination pool '{dest_pool.name()}'...")
        _safe_refresh_pool(dest_pool)

        # Update any VM configurations that use this volume
        old_path = _safe_get_volume_path(source_vol)
        new_path = _safe_get_volume_path(new_vol)
        old_pool_name = source_pool.name()
        new_pool_name = dest_pool.name()

        if vms_using_volume:
            log_and_callback(f"Updating configurations for {len(vms_using_volume)} VM(s)...")
            for vm in vms_using_volume:
                xml_desc = vm.XMLDesc(0)
                root = ET.fromstring(xml_desc)
                updated = False
                for disk in root.findall('.//disk'):
                    source_element = disk.find('source')
                    if source_element is None:
                        continue

                    # Case 1: file or dev
                    if source_element.get('file') == old_path:
                        source_element.set('file', new_path)
                        updated = True
                    if source_element.get('dev') == old_path:
                        source_element.set('dev', new_path)
                        updated = True

                    # Case 2: volume
                    if disk.get('type') == 'volume':
                        if source_element.get('pool') == old_pool_name and source_element.get('volume') == volume_name:
                            source_element.set('pool', new_pool_name)
                            source_element.set('volume', new_volume_name)
                            updated = True

                if updated:
                    log_and_callback(f"Updating VM '{vm.name()}' configuration...")
                    conn.defineXML(ET.tostring(root, encoding='unicode'))
                    updated_vm_names.append(vm.name())
            log_and_callback(f"Updated configurations for VMs: {', '.join(updated_vm_names)}")

        # Delete the original volume after successful copy
        log_and_callback(f"Deleting original volume '{volume_name}'...")
        source_vol.delete(0)
        log_and_callback("Original volume deleted.")

        # Refresh source pool to remove the old volume from listings
        log_and_callback(f"Refreshing source pool '{source_pool.name()}'...")
        _safe_refresh_pool(source_pool)
        log_and_callback("\nMove Finished, you can close this window")

    except Exception as e:
        # If anything fails, try to clean up the newly created (but possibly incomplete) volume
        logging.error(f"An error occurred during volume move: {e}. Cleaning up destination volume.")
        if new_vol:
            try:
                new_vol.delete(0)
            except libvirt.libvirtError as del_e:
                logging.error(f"Failed to clean up destination volume '{new_volume_name}': {del_e}")
        # Re-raise the original exception
        raise
    finally:
        # Abort streams if they are still active
        try:
            if download_stream:
                download_stream.abort()
        except libvirt.libvirtError:
            pass
        try:
            if upload_stream:
                upload_stream.abort()
        except libvirt.libvirtError:
            pass

    return updated_vm_names

def delete_storage_pool(pool: libvirt.virStoragePool):
    """
    Deletes a storage pool.
    The pool must be inactive first.
    """
    try:
        # If pool is active, destroy it first (make it inactive)
        if _safe_is_pool_active(pool):
            try:
                pool.destroy()
            except libvirt.libvirtError as e:
                logging.warning(f"Failed to destroy active pool '{pool.name()}': {e}")
                # Continue with undefine even if destroy fails
        # Undefine the pool (delete it)
        pool.undefine()
    except libvirt.libvirtError as e:
        msg = f"Error deleting storage pool '{pool.name()}': {e}"
        logging.error(msg)
        raise Exception(msg) from e

@lru_cache(maxsize=16)
def get_all_storage_volumes(conn: libvirt.virConnect) -> List[libvirt.virStorageVol]:
    """
    Retrieves all storage volumes across all active storage pools.
    """
    all_volumes = []
    if not conn:
        return all_volumes

    pools_info = list_storage_pools(conn)
    for pool_info in pools_info:
        pool = pool_info['pool']
        if _safe_is_pool_active(pool):
            try:
                all_volumes.extend(pool.listAllVolumes())
            except libvirt.libvirtError:
                continue
    return all_volumes


def list_unused_volumes(conn: libvirt.virConnect, pool_name: str = None) -> List[libvirt.virStorageVol]:
    """
    Lists all storage volumes that are not attached to any VM.
    If pool_name is provided, only checks volumes in that specific pool.
    """
    if not conn:
        return []

    # If pool_name is specified, get volumes from that specific pool
    if pool_name:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
            if not _safe_is_pool_active(pool):
                return []
            all_volumes = pool.listAllVolumes()
        except libvirt.libvirtError:
            return []
    else:
        all_volumes = get_all_storage_volumes(conn)

    used_disk_paths = set()

    try:
        domains = conn.listAllDomains(0)
        for domain in domains:
            xml_content = domain.XMLDesc(0)
            try:
                root = ET.fromstring(xml_content)
            except ET.ParseError:
                continue

            disks_info = get_vm_disks_info(conn, root)
            for disk in disks_info:
                if disk.get('path'):
                    used_disk_paths.add(disk['path'])

            # Check metadata for backing chains (overlays) to ensure backing files are marked as used
            try:
                metadata = root.find("metadata")
                if metadata is not None:
                    # Scan ALL descendants of metadata for 'overlay' tags
                    # This bypasses potential structure/namespace issues in intermediate nodes
                    # and avoids using helpers that might create new empty elements.
                    for elem in metadata.iter():
                        # Check for overlay tag (namespaced or not)
                        if elem.tag.endswith("}overlay") or elem.tag == "overlay":
                            backing_path = elem.get('backing')
                            if backing_path:
                                used_disk_paths.add(backing_path)
            except Exception:
                pass

    except libvirt.libvirtError as e:
        print(f"Error retrieving VM disk information: {e}")
        return []

    unused_volumes = []
    for vol in all_volumes:
        vol_path = _safe_get_volume_path(vol)
        if vol_path and vol_path not in used_disk_paths:
            unused_volumes.append(vol)

    return unused_volumes

def find_shared_storage_pools(source_conn: libvirt.virConnect, dest_conn: libvirt.virConnect) -> List[Dict[str, Any]]:
    """
    Finds storage pools that are present on both source and destination servers.

    A pool is considered shared if it has the same name, type, and target configuration.
    This is useful for identifying shared storage for live migration.
    The function returns detailed information for each shared pool, including a warning
    if a pool is not active on either server.
    """
    if not source_conn or not dest_conn:
        return []

    source_pools_list = list_storage_pools(source_conn)
    dest_pools_map = {p['name']: p for p in list_storage_pools(dest_conn)}

    def _is_default_image_pool(pool: libvirt.virStoragePool) -> bool:
        """Checks if the given pool is the default 'dir' type pool with path /var/lib/libvirt/images."""
        if pool.name() == "default":
            try:
                xml_desc = pool.XMLDesc(0)
                root = ET.fromstring(xml_desc)
                pool_type = root.get("type")
                path_element = root.find("target/path")

                if pool_type == "dir" and path_element is not None and path_element.text == "/var/lib/libvirt/images":
                    return True
            except (libvirt.libvirtError, ET.ParseError):
                pass
        return False

    def get_pool_details(pool: libvirt.virStoragePool) -> Dict[str, Any] | None:
        """Parse pool XML to get its type and target details for comparison."""
        try:
            xml_desc = pool.XMLDesc(0)
            root = ET.fromstring(xml_desc)
            pool_type = root.get("type")

            target_details = {}
            if pool_type == 'dir':
                path_elem = root.find("target/path")
                if path_elem is not None:
                    target_details['path'] = path_elem.text
            elif pool_type == 'netfs':
                host_elem = root.find("source/host")
                dir_elem = root.find("source/dir")
                if host_elem is not None:
                    target_details['host'] = host_elem.get('name')
                if dir_elem is not None:
                    target_details['path'] = dir_elem.get('path')
            # Other pool types can be added here (e.g., iscsi, rbd)

            return {"type": pool_type, "target": target_details}
        except (libvirt.libvirtError, ET.ParseError) as e:
            logging.warning(f"Could not parse XML for pool {pool.name()}: {e}")
            return None

    shared_pools_info = []
    for source_pool_info in source_pools_list:
        source_name = source_pool_info['name']
        source_pool = source_pool_info['pool']

        if _is_default_image_pool(source_pool):
            continue

        if source_name in dest_pools_map:
            dest_pool_info = dest_pools_map[source_name]
            dest_pool = dest_pool_info['pool']

            if _is_default_image_pool(dest_pool):
                continue

            source_details = get_pool_details(source_pool)
            dest_details = get_pool_details(dest_pool)

            # A pool is shared if its name, type, and target are identical
            if source_details and dest_details and source_details == dest_details:
                warning = ""
                if source_pool_info['status'] != 'active':
                    warning += f"Source pool '{source_name}' is inactive. "
                if dest_pool_info['status'] != 'active':
                    warning += f"Destination pool '{source_name}' is inactive."

                shared_pools_info.append({
                    "name": source_name,
                    "type": source_details.get('type'),
                    "target": source_details.get('target'),
                    "source_status": source_pool_info['status'],
                    "dest_status": dest_pool_info['status'],
                    "warning": warning.strip()
                })

    return shared_pools_info


def copy_volume_across_hosts(source_conn: libvirt.virConnect, dest_conn: libvirt.virConnect, source_pool_name: str, dest_pool_name: str, volume_name: str, new_volume_name: str = None, new_backing_path: str = None, backing_format: str = 'qcow2', progress_callback=None, log_callback=None) -> dict:
    """
    Copies a storage volume from a source host to a destination host using direct streaming.
    If new_backing_path is provided, it downloads to a temp file, rebases, and then uploads.
    """
    def log_and_callback(message):
        if log_callback:
            log_callback(message)
        else:
            logging.info(message)

    if not new_volume_name:
        new_volume_name = volume_name

    try:
        source_pool = source_conn.storagePoolLookupByName(source_pool_name)
        dest_pool = dest_conn.storagePoolLookupByName(dest_pool_name)
        source_vol = source_pool.storageVolLookupByName(volume_name)
    except libvirt.libvirtError as e:
        log_and_callback(f"[red]ERROR:[/ ] Could not find source/destination resources: {e}")
        raise

    _, source_capacity, _ = _safe_get_volume_info(source_vol)
    source_format = "qcow2"
    try:
        source_format = ET.fromstring(source_vol.XMLDesc(0)).findtext("target/format[@type]", "qcow2")
    except (ET.ParseError, libvirt.libvirtError):
        pass

    # Check if volume already exists on destination
    try:
        dest_pool.storageVolLookupByName(new_volume_name)
        raise Exception(f"A volume named '{new_volume_name}' already exists in the destination pool '{dest_pool_name}'.")
    except libvirt.libvirtError as e:
        if e.get_error_code() != libvirt.VIR_ERR_NO_STORAGE_VOL:
            raise

    # Create new volume definition on destination
    # Ensure qcow2 if backing store is present, as raw doesn't support it
    target_format = 'qcow2' if new_backing_path else source_format

    new_vol_xml = f"""
    <volume>
        <name>{new_volume_name}</name>
        <capacity>{source_capacity}</capacity>
        <target>
            <format type='{target_format}'/>
        </target>
    """
    if new_backing_path:
        new_vol_xml += f"""
        <backingStore>
            <path>{new_backing_path}</path>
            <format type='{backing_format}'/>
        </backingStore>
        """
    new_vol_xml += "</volume>"

    log_and_callback(f"Creating new volume '{new_volume_name}' on destination pool '{dest_pool_name}'.")
    dest_vol = dest_pool.createXML(new_vol_xml, 0)

    download_stream = None
    upload_stream = None

    # Pipe for direct streaming
    r_fd, w_fd = os.pipe()

    download_error = None
    upload_error = None
    tmp_file = None

    try:
        if new_backing_path:
            # We need to rebase, so we must download to a temp file
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_file = tmp.name
            log_and_callback(f"Using temporary file for rebase: {tmp_file}")

            # Download
            log_and_callback(f"Downloading '{volume_name}' for rebase...")
            download_stream = source_conn.newStream(0)
            source_vol.download(download_stream, 0, source_capacity)

            with open(tmp_file, "wb") as f:
                def writer(st, data, opaque):
                    f.write(data)
                    return len(data)

                downloaded_bytes = 0
                while True:
                    data = download_stream.recv(1024*1024)
                    if not data: break
                    f.write(data)
                    downloaded_bytes += len(data)
                    if progress_callback:
                        progress_callback((downloaded_bytes / source_capacity) * 100)

            download_stream.finish()

            # Rebase
            log_and_callback(f"Rebasing volume to new backing path: {new_backing_path}")
            try:
                # qemu-img rebase -u -b backing_file -F backing_fmt filename
                cmd = ["qemu-img", "rebase", "-u", "-b", new_backing_path, "-F", backing_format, tmp_file]
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                raise Exception(f"qemu-img rebase failed: {e.stderr}")
            except FileNotFoundError:
                raise Exception("qemu-img not found. Please install qemu-img to migrate overlays.")

            # Upload
            log_and_callback(f"Uploading rebased volume to '{new_volume_name}'...")
            upload_stream = dest_conn.newStream(0)
            dest_vol.upload(upload_stream, 0, source_capacity)

            with open(tmp_file, "rb") as f:
                total_sent = 0
                while True:
                    data = f.read(1024*1024)
                    if not data: break
                    upload_stream.send(data)
                    total_sent += len(data)
                    if progress_callback:
                        progress_callback((total_sent / source_capacity) * 100)

            upload_stream.finish()

        else:
            # Direct streaming
            log_and_callback(f"Starting direct stream copy for '{volume_name}'...")
            download_stream = source_conn.newStream(0)
            upload_stream = dest_conn.newStream(0)

            # --- Download Thread ---
            def download_task(stream, fd, capacity):
                nonlocal download_error
                try:
                    def writer(st, data, opaque):
                        try:
                            os.write(opaque, data)
                            return 0
                        except Exception as e:
                            nonlocal download_error
                            download_error = e
                            return -1

                    source_vol.download(stream, 0, capacity)
                    stream.recvAll(writer, fd)
                    stream.finish()
                except Exception as e:
                    download_error = e
                    try: stream.abort()
                    except: pass
                finally:
                    os.close(fd)

            # --- Upload Thread ---
            def upload_task(stream, fd, capacity, callback):
                nonlocal upload_error
                try:
                    if callback:
                        callback(0)
                    uploaded_bytes = 0
                    def reader(st, nbytes, opaque):
                        nonlocal uploaded_bytes
                        try:
                            chunk = os.read(opaque, nbytes)
                            uploaded_bytes += len(chunk)
                            if callback and capacity > 0:
                                callback((uploaded_bytes / capacity) * 100)
                            return chunk
                        except Exception as e:
                            nonlocal upload_error
                            upload_error = e
                            raise e

                    dest_vol.upload(stream, 0, capacity)
                    stream.sendAll(reader, fd)
                    stream.finish()
                except Exception as e:
                    upload_error = e
                    try: stream.abort()
                    except: pass
                finally:
                    os.close(fd)

            d_thread = threading.Thread(target=download_task, args=(download_stream, w_fd, source_capacity))
            u_thread = threading.Thread(target=upload_task, args=(upload_stream, r_fd, source_capacity, progress_callback))

            d_thread.start()
            u_thread.start()
            d_thread.join()
            u_thread.join()

            if download_error: raise download_error
            if upload_error: raise upload_error

        log_and_callback("Transfer complete.")
        _safe_refresh_pool(dest_pool)

        return {
            "old_disk_path": _safe_get_volume_path(source_vol),
            "new_pool_name": dest_pool.name(),
            "new_volume_name": dest_vol.name(),
            "new_disk_path": _safe_get_volume_path(dest_vol),
        }

    except Exception as e:
        log_and_callback(f"[red]ERROR:[/ ] Failed to copy volume: {e}")
        if dest_vol:
            try: dest_vol.delete(0)
            except: pass
        raise
    finally:
        try:
            if download_stream: download_stream.abort()
        except: pass
        try:
            if upload_stream: upload_stream.abort()
        except: pass
        if tmp_file and os.path.exists(tmp_file):
            os.remove(tmp_file)
