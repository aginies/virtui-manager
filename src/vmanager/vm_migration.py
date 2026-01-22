
"""
Module for handling custom VM migration.
"""
import logging
import os
import xml.etree.ElementTree as ET
import libvirt
from .vm_queries import get_vm_disks_info, get_vm_snapshots
from .libvirt_utils import (
        _find_vol_by_path, get_overlay_backing_path,
        VIRTUI_MANAGER_NS, get_internal_id
        )
from .storage_manager import copy_volume_across_hosts
from .utils import extract_server_name_from_uri

def execute_custom_migration(source_conn: libvirt.virConnect, dest_conn: libvirt.virConnect, actions: list, selections: dict, log_callback=None, progress_callback=None):
    """
    Executes the custom migration actions based on user selections.
    """
    def log(message):
        if log_callback:
            log_callback(message)
        logging.info(message)

    vm_name = None
    # We need the VM name to update its XML on the destination.
    # We can find it in the actions.
    for action in actions:
        if "vm_name" in action:
            vm_name = action["vm_name"]
            break

    if not vm_name:
        raise Exception("Could not determine VM name from actions.")

    dest_vm = dest_conn.lookupByName(vm_name)
    xml_desc = dest_vm.XMLDesc(0)
    root = ET.fromstring(xml_desc)

    xml_updated = False

    # Remove UUID to ensure a new one is generated
    uuid_elem = root.find('uuid')
    if uuid_elem is not None:
        root.remove(uuid_elem)
        xml_updated = True

    path_mapping = {}

    dest_server_name = extract_server_name_from_uri(dest_conn.getURI())

    for i, action in enumerate(actions):
        if action["type"] == "move_volume":
            dest_pool_name = selections.get(i)
            if not dest_pool_name:
                log(f"[yellow]Skipping volume '{action['volume_name']}' - No destination pool selected.[/]")
                continue

            # Check if this volume has a backing file in VM metadata or volume XML
            new_backing_path = None
            try:
                old_backing_path = get_overlay_backing_path(root, action['disk_path'])

                if not old_backing_path:
                    # Fallback: Check volume XML directly
                    try:
                        source_pool = source_conn.storagePoolLookupByName(action['source_pool'])
                        source_vol = source_pool.storageVolLookupByName(action['volume_name'])
                        vol_xml = source_vol.XMLDesc(0)
                        vol_root = ET.fromstring(vol_xml)
                        backing_store = vol_root.find("backingStore")
                        if backing_store is not None:
                            backing_path_elem = backing_store.find("path")
                            if backing_path_elem is not None:
                                old_backing_path = backing_path_elem.text
                    except:
                        pass

                if old_backing_path:
                    # Look up where we moved this backing file to
                    if old_backing_path in path_mapping:
                        new_backing_path = path_mapping[old_backing_path]
                        log(f"  - Detected backing file. Will rebase to: {new_backing_path}")
                    else:
                        log(f"  - [yellow]Warning:[/] Backing file '{old_backing_path}' not found in migration plan. Overlay might be broken.")
            except Exception as e:
                log(f"  - [yellow]Warning:[/] Could not check backing chain: {e}")

            log(f"Copying volume '[b]{action['volume_name']}[/b]' to pool '[b]{dest_pool_name}[/b]' on destination server '[b]{dest_server_name}[/b]'...")

            # Ensure we have a progress callback to show activity
            current_progress_callback = progress_callback
            if not current_progress_callback:
                last_p = 0
                def _default_progress(p):
                    nonlocal last_p
                    if p >= last_p + 10:
                        log(f"  ... {int(p)}%")
                        last_p = int(p // 10) * 10
                    if p >= 100: last_p = 0
                current_progress_callback = _default_progress

            try:
                result = copy_volume_across_hosts(
                    source_conn,
                    dest_conn,
                    action['source_pool'],
                    dest_pool_name, 
                    action['volume_name'],
                    new_backing_path=new_backing_path,
                    progress_callback="", #current_progress_callback,
                    log_callback=log
                )

                # Update XML with new path
                old_path = action['disk_path']
                new_path = result.get('new_disk_path')

                if not new_path:
                    # Fallback lookup if for some reason path wasn't returned
                    try:
                        dest_pool = dest_conn.storagePoolLookupByName(result['new_pool_name'])
                        new_vol = dest_pool.storageVolLookupByName(result['new_volume_name'])
                        new_path = new_vol.path()
                    except Exception as e:
                        log(f"[red]ERROR: Could not determine new path for volume '{result['new_volume_name']}': {e}[/]")
                        raise

                # Store mapping for snapshot updates
                path_mapping[old_path] = new_path

                # Update disk source in XML
                for disk in root.findall('.//disk'):
                    source = disk.find('source')
                    if source is not None:
                        # Check both file and dev attributes
                        if source.get('file') == old_path:
                            source.set('file', new_path)
                            xml_updated = True
                        elif source.get('dev') == old_path:
                            source.set('dev', new_path)
                            xml_updated = True

                # Update Metadata in VM XML
                backing_chain_elem = root.find(f".//{{{VIRTUI_MANAGER_NS}}}backing-chain")
                if backing_chain_elem is not None:
                    for overlay in backing_chain_elem.findall(f"{{{VIRTUI_MANAGER_NS}}}overlay"):
                        m_path = overlay.get('path')
                        m_backing = overlay.get('backing')

                        if m_path == old_path:
                            overlay.set('path', new_path)
                            xml_updated = True
                        if m_backing == old_path:
                            overlay.set('backing', new_path)
                            xml_updated = True

            except Exception as e:
                log(f"[red]ERROR: Failed to copy volume '{action['volume_name']}': {e}[/]")
                raise e

    if xml_updated:
        log("Updating VM configuration on destination...")
        # Undefine to allow new UUID generation
        try:
            dest_vm.undefine()
        except libvirt.libvirtError:
            pass
        dest_vm = dest_conn.defineXML(ET.tostring(root, encoding='unicode'))
        log("VM configuration updated.")

    # Handle Snapshot Migration
    try:
        source_vm = source_conn.lookupByName(vm_name)
        snapshots = get_vm_snapshots(source_vm)

        if snapshots:
            log(f"Found {len(snapshots)} snapshot(s) to migrate.")

            # Sort snapshots by creation time (oldest first) to ensure parent snapshots exist
            snapshots.sort(key=lambda x: x.get('creation_time', ''), reverse=False)

            for snap_info in snapshots:
                snap_name = snap_info['name']
                log(f"Migrating snapshot '{snap_name}'...")

                try:
                    # Get secure XML to include security driver info if needed, though secure=0 is usually enough for metadata
                    snapshot_obj = snap_info['snapshot_object']
                    snap_xml = snapshot_obj.getXMLDesc(libvirt.VIR_DOMAIN_SNAPSHOT_XML_SECURE)

                    if path_mapping:
                        snap_root = ET.fromstring(snap_xml)
                        # The snapshot XML contains a <domain> element which describes the VM state
                        domain_elem = snap_root.find('domain')
                        if domain_elem is not None:
                            snap_xml_updated = False
                            for disk in domain_elem.findall('.//devices/disk'):
                                source = disk.find('source')
                                if source is not None:
                                    old_file = source.get('file')
                                    old_dev = source.get('dev')

                                    if old_file and old_file in path_mapping:
                                        source.set('file', path_mapping[old_file])
                                        snap_xml_updated = True
                                    elif old_dev and old_dev in path_mapping:
                                        source.set('dev', path_mapping[old_dev])
                                        snap_xml_updated = True

                            if snap_xml_updated:
                                snap_xml = ET.tostring(snap_root, encoding='unicode')

                    # Redefine on destination
                    dest_vm.snapshotCreateXML(snap_xml, libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_REDEFINE)
                    log(f"Snapshot '{snap_name}' redefined on destination.")

                except libvirt.libvirtError as e:
                    log(f"[red]ERROR: Failed to migrate snapshot '{snap_name}': {e}[/]")
                    # Continue with other snapshots? If parent fails, children might fail.

    except Exception as e:
        log(f"[yellow]Warning: Error preparing for snapshot migration: {e}[/]")

    if selections.get('undefine_source'):
        log(f"Undefining VM '{vm_name}' from source...")
        try:
            source_vm = source_conn.lookupByName(vm_name)
            log(f"Undefining VM ID: {get_internal_id(source_vm)}")
            source_vm.undefine()
            log("Source VM undefined.")
        except Exception as e:
            log(f"[yellow]Warning: Failed to undefine source VM: {e}[/]")

    log("[green]Custom migration execution finished.[/]")

def custom_migrate_vm(source_conn: libvirt.virConnect, dest_conn: libvirt.virConnect, domain: libvirt.virDomain, log_callback=None):
    """
    Performs a custom migration of a VM from a source to a destination server.

    This migration is a "cold" migration, meaning the VM must be shut down.
    The process involves:
    1. Redefining the VM on the destination host.
    2. Analyzing storage and proposing move actions.
    3. Proposing to undefine the VM on the source host.

    Args:
        source_conn: Connection to the source libvirt host.
        dest_conn: Connection to the destination libvirt host.
        domain: The libvirt domain object to migrate.
        log_callback: A function to send log messages to the UI.

    Returns:
        A list of dictionaries, where each dictionary represents a proposed action
        that the user needs to confirm.
    """
    if domain.isActive():
        raise libvirt.libvirtError("VM must be stopped for custom migration.")

    def log(message):
        if log_callback:
            log_callback(message)
        logging.info(message)

    vm_internal_id = get_internal_id(domain, source_conn)
    log(f"Starting custom migration for VM '{domain.name()}' (ID: {vm_internal_id})...")

    # 1. Get the VM's XML and define it on the destination
    xml_desc = domain.XMLDesc(0)
    try:
        log(f"Defining VM '{domain.name()}' on the destination host...")
        dest_conn.defineXML(xml_desc)
        log("VM defined successfully on the destination.")
    except libvirt.libvirtError as e:
        if e.get_error_code() == 9: # VIR_ERR_DOMAIN_EXIST
            log(f"VM '{domain.name()}' already exists on the destination. It will be overwritten.")
            # Undefine existing VM first
            existing_dest_vm = dest_conn.lookupByName(domain.name())
            if existing_dest_vm.isActive():
                raise libvirt.libvirtError(f"A VM with the name '{domain.name()}' is running on the destination.")
            existing_dest_vm.undefine()
            dest_conn.defineXML(xml_desc)
            log("Existing VM on destination has been updated.")
        else:
            raise

    # 2. Analyze storage and propose move actions
    actions = []
    root = ET.fromstring(xml_desc)
    disks = get_vm_disks_info(source_conn, root)

    dest_pools = []
    try:
        dest_pools = [p.name() for p in dest_conn.listAllStoragePools(0)]
    except libvirt.libvirtError as e:
        log(f"[yellow]Warning:[/] Could not list storage pools on destination: {e}")

    if not disks:
        log("No disks found for this VM. No storage migration needed.")
    else:
        log(f"Found {len(disks)} disk(s) to migrate.")

    processed_paths = set()

    for i, disk in enumerate(disks):
        disk_path = disk.get('path')
        if not disk_path:
            log(f"Skipping disk with no path: {disk}")
            continue

        # Build the chain of volumes for this disk
        chain = []
        current_path = disk_path

        while current_path:
            if current_path in processed_paths:
                break

            try:
                source_vol, source_pool = _find_vol_by_path(source_conn, current_path)
                if not source_vol:
                    log(f"Disk/Backing '{current_path}' is not a managed libvirt volume. Proposing manual copy.")
                    actions.append({
                        "type": "manual_copy",
                        "disk_path": current_path,
                        "message": f"File '{os.path.basename(current_path)}' is a direct file. Manually copy to destination."
                    })
                    break

                chain.insert(0, (source_vol, source_pool, current_path)) # Prepend to process backing first
                processed_paths.add(current_path)

                # Check for backing file in VM metadata
                backing_path = get_overlay_backing_path(root, current_path)

                if not backing_path:
                    # Fallback: Check volume XML directly
                    try:
                        vol_xml = source_vol.XMLDesc(0)
                        vol_root = ET.fromstring(vol_xml)
                        backing_store = vol_root.find("backingStore")
                        if backing_store is not None:
                            backing_path_elem = backing_store.find("path")
                            if backing_path_elem is not None:
                                backing_path = backing_path_elem.text
                    except:
                        pass

                current_path = backing_path # Continue loop with backing path

            except libvirt.libvirtError as e:
                log(f"[yellow]Warning:[/] Error analyzing volume '{current_path}': {e}")
                break

        # Add actions for the chain in order (backing files first)
        for source_vol, source_pool, path in chain:
            source_pool_name = source_pool.name()
            volume_name = source_vol.name()

            is_overlay = path != disk_path # If it's not the top-level disk, it's a backing file in this chain context

            actions.append({
                "type": "move_volume",
                "vm_name": domain.name(),
                "disk_path": path,
                "source_pool": source_pool_name,
                "volume_name": volume_name,
                "dest_pools": dest_pools,
                "message": f"Propose moving {'backing ' if is_overlay else ''}volume '{volume_name}' from pool '{source_pool_name}'."
            })

    log("Custom migration plan created. Please review the proposed actions.")
    return actions
