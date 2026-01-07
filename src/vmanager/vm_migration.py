
"""
Module for handling custom VM migration.
"""
import logging
import os
import xml.etree.ElementTree as ET
import libvirt
from vm_queries import get_vm_disks_info
from libvirt_utils import _find_vol_by_path
from storage_manager import copy_volume_across_hosts
from utils import extract_server_name_from_uri

def execute_custom_migration(source_conn: libvirt.virConnect, dest_conn: libvirt.virConnect, actions: list, selections: dict, log_callback=None):
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

    dest_server_name = extract_server_name_from_uri(dest_conn.getURI())

    for i, action in enumerate(actions):
        if action["type"] == "move_volume":
            dest_pool_name = selections.get(i)
            if not dest_pool_name:
                log(f"[yellow]Skipping volume '{action['volume_name']}' - No destination pool selected.[/]")
                continue
            
            log(f"Copying volume '[b]{action['volume_name']}[/b]' to pool '[b]{dest_pool_name}[/b]' on destination server '[b]{dest_server_name}[/b]'...")
            try:
                result = copy_volume_across_hosts(
                    source_conn, 
                    dest_conn, 
                    action['source_pool'], 
                    dest_pool_name, 
                    action['volume_name'], 
                    progress_callback=None, # TODO: Wire up progress
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
                
            except Exception as e:
                log(f"[red]ERROR: Failed to copy volume '{action['volume_name']}': {e}[/]")
                raise e

        elif action["type"] == "undefine_source":
            log(f"Undefining VM '{action['vm_name']}' from source...")
            try:
                source_vm = source_conn.lookupByName(action['vm_name'])
                source_vm.undefine()
                log("Source VM undefined.")
            except Exception as e:
                log(f"[yellow]Warning: Failed to undefine source VM: {e}[/]")

    if xml_updated:
        log("Updating VM configuration on destination...")
        dest_conn.defineXML(ET.tostring(root, encoding='unicode'))
        log("VM configuration updated.")

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

    log(f"Starting custom migration for VM '{domain.name()}'...")

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

    for i, disk in enumerate(disks):
        disk_path = disk.get('path')
        if not disk_path:
            log(f"Skipping disk with no path: {disk}")
            continue

        # For disks that are already on shared storage (NFS, etc.), we might not need to move them.
        # This basic implementation will propose moving all disks.
        # A more advanced version could check if the path is already accessible from the destination.
        
        # We find the original volume and pool to get its details
        try:
            source_vol, source_pool = _find_vol_by_path(source_conn, disk_path)
            if not source_vol:
                log(f"Disk '{disk_path}' is not a managed libvirt volume. Proposing manual copy.")
                # For non-managed disks, we can't do a libvirt-based move.
                # We can still propose a manual action.
                actions.append({
                    "type": "manual_copy",
                    "disk_path": disk_path,
                    "message": f"Disk '{os.path.basename(disk_path)}' is a direct file. You will need to manually copy it to the destination and update the VM's XML."
                })
                continue
            
            source_pool_name = source_pool.name()
            volume_name = source_vol.name()

            actions.append({
                "type": "move_volume",
                "vm_name": domain.name(),
                "disk_path": disk_path,
                "source_pool": source_pool_name,
                "volume_name": volume_name,
                "dest_pools": dest_pools,
                "message": f"Propose moving disk '{volume_name}' from pool '{source_pool_name}' on the source to a new pool on the destination."
            })
        except libvirt.libvirtError as e:
            log(f"[yellow]Warning:[/] Could not resolve source volume for '{disk_path}': {e}. It may be a direct file path.")
            actions.append({
                "type": "manual_copy",
                "disk_path": disk_path,
                "dest_pools": dest_pools,
                "message": f"Disk '{os.path.basename(disk_path)}' could not be resolved as a libvirt volume. You may need to copy it manually."
            })


    # 3. Propose undefining the source VM
    actions.append({
        "type": "undefine_source",
        "vm_name": domain.name(),
        "message": f"Undefine VM '{domain.name()}' from the source host after migration is complete."
    })

    log("Custom migration plan created. Please review the proposed actions.")
    return actions
