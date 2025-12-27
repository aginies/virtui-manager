"""
VM Service Layer
Handles all libvirt interactions and data processing.
"""
import libvirt
from connection_manager import ConnectionManager

class VMService:
    """A service class to abstract libvirt operations."""

    def __init__(self):
        self.connection_manager = ConnectionManager()

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

        if sort_by != "default":
            if sort_by == "running":
                domains_to_display = [(d, c) for d, c in domains_to_display if d.info()[0] == libvirt.VIR_DOMAIN_RUNNING]
            elif sort_by == "paused":
                domains_to_display = [(d, c) for d, c in domains_to_display if d.info()[0] == libvirt.VIR_DOMAIN_PAUSED]
            elif sort_by == "stopped":
                domains_to_display = [(d, c) for d, c in domains_to_display if d.info()[0] not in [libvirt.VIR_DOMAIN_RUNNING, libvirt.VIR_DOMAIN_PAUSED]]
            elif sort_by == "selected":
                domains_to_display = [(d, c) for d, c in domains_to_display if d.UUIDString() in selected_vm_uuids]

        if search_text:
            domains_to_display = [(d, c) for d, c in domains_to_display if search_text.lower() in d.name().lower()]

        total_filtered_vms = len(domains_to_display)
        
        return domains_to_display, total_vms, total_filtered_vms, server_names
