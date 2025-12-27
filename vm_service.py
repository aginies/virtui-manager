"""
VM Service Layer
Handles all libvirt interactions and data processing.
"""
import libvirt
from connection_manager import ConnectionManager
from constants import VmStatus

class VMService:
    """A service class to abstract libvirt operations."""

    def __init__(self):
        self.connection_manager = ConnectionManager()
        self._cpu_time_cache = {} # Cache for calculating CPU usage {uuid: (last_time, last_timestamp)}

    def get_vm_runtime_stats(self, domain: libvirt.virDomain) -> dict | None:
        """Gets live statistics for a given, active VM domain."""
        from vm_queries import get_status
        from datetime import datetime

        if not domain or not domain.isActive():
            return None

        uuid = domain.UUIDString()
        stats = {}
        try:
            # Status
            stats['status'] = get_status(domain)

            # CPU Usage
            cpu_stats = domain.getCPUStats(True)
            current_cpu_time = cpu_stats[0]['cpu_time']
            now = datetime.now().timestamp()
            
            cpu_percent = 0.0
            if uuid in self._cpu_time_cache:
                last_cpu_time, last_cpu_time_ts = self._cpu_time_cache[uuid]
                time_diff = now - last_cpu_time_ts
                cpu_diff = current_cpu_time - last_cpu_time
                if time_diff > 0:
                    num_cpus = domain.info()[3]
                    # nanoseconds to seconds, then divide by number of cpus
                    cpu_percent = (cpu_diff / (time_diff * 1_000_000_000)) * 100
                    cpu_percent = cpu_percent / num_cpus if num_cpus > 0 else 0

            stats['cpu_percent'] = cpu_percent
            self._cpu_time_cache[uuid] = (current_cpu_time, now)

            # Memory Usage
            mem_stats = domain.memoryStats()
            mem_percent = 0.0
            if 'rss' in mem_stats:
                total_mem_kb = domain.info()[1]
                if total_mem_kb > 0:
                    rss_kb = mem_stats['rss']
                    mem_percent = (rss_kb / total_mem_kb) * 100
            
            stats['mem_percent'] = mem_percent
            
            return stats

        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                # If domain disappears, remove it from cache
                if uuid in self._cpu_time_cache:
                    del self._cpu_time_cache[uuid]
            return None

    def connect(self, uri: str) -> libvirt.virConnect | None:
        """Connects to a libvirt URI."""
        return self.connection_manager.connect(uri)

    def disconnect(self, uri: str) -> None:
        """Disconnects from a libvirt URI."""
        self.connection_manager.disconnect(uri)

    def disconnect_all(self):
        """Disconnects all active libvirt connections."""
        self.connection_manager.disconnect_all()

    def get_connection(self, uri: str) -> libvirt.virConnect | None:
        """Gets an existing connection object from the manager."""
        return self.connection_manager.get_connection(uri)

    def get_all_uris(self) -> list[str]:
        """Gets all URIs currently held by the connection manager."""
        return self.connection_manager.get_all_uris()

    def find_domain_by_uuid(self, active_uris: list[str], vm_uuid: str) -> libvirt.virDomain | None:
        """Finds and returns a domain object from a UUID across active connections."""
        for uri in active_uris:
            conn = self.connect(uri)
            if conn:
                try:
                    domain = conn.lookupByUUIDString(vm_uuid)
                    return domain
                except libvirt.libvirtError:
                    continue
        return None

    def start_vm(self, domain: libvirt.virDomain) -> None:
        """Performs pre-flight checks and starts the VM."""
        from vm_actions import start_vm as start_action
        from storage_manager import check_domain_volumes_in_use
        
        if domain.isActive():
            return # Already running, do nothing

        # Perform pre-flight checks
        check_domain_volumes_in_use(domain)

        # If checks pass, start the VM
        start_action(domain)

    def stop_vm(self, domain: libvirt.virDomain) -> None:
        """Stops the VM."""
        from vm_actions import stop_vm as stop_action

        stop_action(domain)

    def pause_vm(self, domain: libvirt.virDomain) -> None:
        """Pauses the VM."""
        from vm_actions import pause_vm as pause_action

        pause_action(domain)

    def force_off_vm(self, domain: libvirt.virDomain) -> None:
        """Forcefully stops the VM."""
        from vm_actions import force_off_vm as force_off_action

        force_off_action(domain)

    def delete_vm(self, domain: libvirt.virDomain, delete_storage: bool) -> None:
        """Deletes the VM."""
        from vm_actions import delete_vm as delete_action

        delete_action(domain, delete_storage=delete_storage)

    def get_vm_details(self, active_uris: list[str], vm_uuid: str) -> tuple | None:
        """Finds a VM by UUID and returns its detailed information."""
        from vm_queries import (
            get_status, get_vm_description, get_vm_machine_info, get_vm_firmware_info,
            get_vm_networks_info, get_vm_network_ip, get_vm_network_dns_gateway_info,
            get_vm_disks_info, get_vm_devices_info, get_vm_shared_memory_info,
            get_boot_info, get_vm_video_model, get_vm_cpu_model
        )

        domain = None
        conn_for_domain = None

        for uri in active_uris:
            conn = self.connect(uri)
            if not conn:
                continue
            try:
                domain = conn.lookupByUUIDString(vm_uuid)
                conn_for_domain = conn
                break
            except libvirt.libvirtError:
                continue

        if not domain or not conn_for_domain:
            return None

        try:
            info = domain.info()
            xml_content = domain.XMLDesc(0)
            vm_info = {
                'name': domain.name(),
                'uuid': domain.UUIDString(),
                'status': get_status(domain),
                'description': get_vm_description(domain),
                'cpu': info[3],
                'cpu_model': get_vm_cpu_model(xml_content),
                'memory': info[2] // 1024,
                'machine_type': get_vm_machine_info(xml_content),
                'firmware': get_vm_firmware_info(xml_content),
                'shared_memory': get_vm_shared_memory_info(xml_content),
                'networks': get_vm_networks_info(xml_content),
                'detail_network': get_vm_network_ip(domain),
                'network_dns_gateway': get_vm_network_dns_gateway_info(domain),
                'disks': get_vm_disks_info(conn_for_domain, xml_content),
                'devices': get_vm_devices_info(xml_content),
                'boot': get_boot_info(xml_content, conn_for_domain),
                'video_model': get_vm_video_model(xml_content),
                'xml': xml_content,
            }
            return (vm_info, domain, conn_for_domain)
        except libvirt.libvirtError:
            # Propagate the error to be handled by the caller
            raise

    def get_vms(self, active_uris: list[str], servers: list[dict], sort_by: str, search_text: str, selected_vm_uuids: list[str]) -> tuple:
        """Fetch, filter, and return VM data without creating UI components."""
        domains_with_conn = []
        total_vms = 0
        server_names = []

        active_connections = [self.connect(uri) for uri in active_uris if self.connect(uri)]

        for conn in active_connections:
            try:
                domains = conn.listAllDomains(0) or []
                total_vms += len(domains)
                for domain in domains:
                    domains_with_conn.append((domain, conn))

                uri = conn.getURI()
                found = False
                for server in servers:
                    if server['uri'] == uri:
                        server_names.append(server['name'])
                        found = True
                        break
                if not found:
                    server_names.append(uri)
            except libvirt.libvirtError:
                # In a more advanced implementation, this could return an error message
                # for the UI to display.
                pass

        total_vms_unfiltered = len(domains_with_conn)
        domains_to_display = domains_with_conn

        if sort_by != VmStatus.DEFAULT:
            if sort_by == VmStatus.RUNNING:
                domains_to_display = [(d, c) for d, c in domains_to_display if d.info()[0] == libvirt.VIR_DOMAIN_RUNNING]
            elif sort_by == VmStatus.PAUSED:
                domains_to_display = [(d, c) for d, c in domains_to_display if d.info()[0] == libvirt.VIR_DOMAIN_PAUSED]
            elif sort_by == VmStatus.STOPPED:
                domains_to_display = [(d, c) for d, c in domains_to_display if d.info()[0] not in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]]
            elif sort_by == VmStatus.SELECTED:
                domains_to_display = [(d, c) for d, c in domains_to_display if d.UUIDString() in selected_vm_uuids]

        if search_text:
            domains_to_display = [(d, c) for d, c in domains_to_display if search_text.lower() in d.name().lower()]

        total_filtered_vms = len(domains_to_display)
        
        return domains_to_display, total_vms, total_filtered_vms, server_names
