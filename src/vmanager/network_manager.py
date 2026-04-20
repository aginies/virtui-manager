"""
Module for managing network-related operations for virtual machines.
"""

import ipaddress
import logging
import secrets
import subprocess
import xml.etree.ElementTree as ET
from functools import lru_cache

import libvirt

from .libvirt_utils import get_host_domain_capabilities
from .utils import log_function_call


def ensure_default_network(conn: libvirt.virConnect) -> libvirt.virNetwork | None:
    """
    Ensures a default network exists and is active.
    If no networks exist, creates a 'default' NAT network.
    If 'default' network exists but is inactive, starts it.
    """
    if not conn:
        return None

    try:
        # Check if 'default' network exists
        try:
            net = conn.networkLookupByName("default")
            if not net.isActive():
                logging.info("Default network 'default' exists but is inactive. Starting it...")
                net.create()
            return net
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_NETWORK:
                raise

        # Check if any other networks exist and are active
        networks = conn.listAllNetworks()
        if networks:
            active_nets = [n for n in networks if n.isActive()]
            if active_nets:
                return active_nets[0]
            
            # If no networks are active, try to start the first one
            try:
                networks[0].create()
                return networks[0]
            except libvirt.libvirtError:
                pass # Fallback to creating 'default' if we can't start existing one

        # No networks present or couldn't start existing ones, create 'default'
        name = "default"
        logging.info(f"Creating default NAT network '{name}'...")
        
        # Standard libvirt default network XML
        xml = f"""
<network>
  <name>{name}</name>
  <forward mode='nat'>
    <nat>
      <port start='1024' end='65535'/>
    </nat>
  </forward>
  <bridge name='virbr0' stp='on' delay='0'/>
  <mac address='{generate_mac_address()}'/>
  <ip address='192.168.122.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='192.168.122.2' end='192.168.122.254'/>
    </dhcp>
  </ip>
</network>
"""
        net = conn.networkDefineXML(xml)
        net.create()
        net.setAutostart(True)
        logging.info(f"Default network '{name}' created and started.")
        return net

    except libvirt.libvirtError as e:
        logging.error(f"Failed to ensure default network: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error ensuring default network: {e}")
        return None


@lru_cache(maxsize=16)
def list_networks(conn):
    """
    Lists all networks.
    """
    if not conn:
        return []

    networks = []
    for net in conn.listAllNetworks():
        xml_desc = net.XMLDesc(0)
        root = ET.fromstring(xml_desc)

        forward_elem = root.find("forward")
        mode = forward_elem.get("mode") if forward_elem is not None else "isolated"

        networks.append(
            {
                "name": net.name(),
                "mode": mode,
                "active": net.isActive(),
                "autostart": net.autostart(),
            }
        )
    return networks


def _next_bridge_name(conn) -> str:
    """
    Returns the next available 'virbrN' bridge name by inspecting existing
    libvirt network definitions and live network interfaces.
    """
    used: set[int] = set()

    # Check bridge names already declared in libvirt network XML
    for net in conn.listAllNetworks():
        try:
            root = ET.fromstring(net.XMLDesc(0))
            bridge_elem = root.find("bridge")
            if bridge_elem is not None:
                bname = bridge_elem.get("name", "")
                if bname.startswith("virbr"):
                    try:
                        used.add(int(bname[5:]))
                    except ValueError:
                        pass
        except Exception:
            pass

    # Also check live interfaces on the host (handles interfaces not yet in
    # libvirt config). Uses the libvirt nodedev API so it works on remote hosts.
    try:
        for dev in conn.listAllDevices(libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_NET):
            try:
                cap = ET.fromstring(dev.XMLDesc(0)).find("capability[@type='net']")
                if cap is None:
                    continue
                iface_elem = cap.find("interface")
                if iface_elem is None or not iface_elem.text:
                    continue
                iface = iface_elem.text.strip()
                if iface.startswith("virbr"):
                    try:
                        used.add(int(iface[5:]))
                    except ValueError:
                        pass
            except libvirt.libvirtError:
                continue
    except libvirt.libvirtError:
        pass

    i = 0
    while i in used:
        i += 1
    return f"virbr{i}"


def create_network(
    conn,
    name,
    typenet,
    forward_dev,
    ip_network,
    dhcp_enabled,
    dhcp_start,
    dhcp_end,
    domain_name,
    uuid=None,
):
    """
    Creates a new NAT/Routed network.
    """
    if not conn:
        raise ValueError("Invalid libvirt connection.")

    net = ipaddress.ip_network(ip_network)
    generated_mac = generate_mac_address()
    bridge_name = _next_bridge_name(conn)
    uuid_str = f"<uuid>{uuid}</uuid>" if uuid else ""
    nat_xml = ""
    if typenet == "nat":
        nat_xml = """
    <nat>
      <port start='1024' end='65535'/>
    </nat>"""
    xml_forward_dev = ""
    if forward_dev:
        xml_forward_dev = f"dev='{forward_dev}'"

    xml = f"""
<network>
  <name>{name}</name>
  {uuid_str}
  <forward mode='{typenet}' {xml_forward_dev}>{nat_xml}
  </forward>
  <bridge name='{bridge_name}' stp='on' delay='0'/>
  <mac address='{generated_mac}'/>
  <domain name='{domain_name}'/>
  <ip address='{net.network_address + 1}' netmask='{net.netmask}'>
"""
    if dhcp_enabled:
        xml += f"""
    <dhcp>
      <range start='{dhcp_start}' end='{dhcp_end}'/>
    </dhcp>
"""
    xml += """
  </ip>
</network>
"""

    net = conn.networkDefineXML(xml)
    net.create()
    net.setAutostart(True)


def delete_network(conn, network_name):
    """
    Deletes a network.
    """
    if not conn:
        raise ValueError("Invalid libvirt connection.")

    try:
        net = conn.networkLookupByName(network_name)
        if net.isActive():
            net.destroy()
        net.undefine()
    except libvirt.libvirtError as e:
        msg = f"Error deleting network '{network_name}': {e}"
        logging.error(msg)
        raise Exception(msg) from e


@lru_cache(maxsize=16)
def get_vms_using_network(conn, network_name):
    """
    Get a list of VMs using a specific network.
    """
    if not conn:
        return []

    vm_names = []
    domains = conn.listAllDomains(0)
    if domains:
        for domain in domains:
            xml_desc = domain.XMLDesc(0)
            root = ET.fromstring(xml_desc)
            for iface in root.findall(".//devices/interface[@type='network']"):
                source = iface.find("source")
                if source is not None and source.get("network") == network_name:
                    vm_names.append(domain.name())
                    break
    return vm_names


def set_network_active(conn, network_name, active):
    """
    Sets a network to active or inactive.
    """
    if not conn:
        raise ValueError("Invalid libvirt connection.")
    try:
        net = conn.networkLookupByName(network_name)
        if active:
            net.create()
        else:
            net.destroy()
    except libvirt.libvirtError as e:
        msg = f"Error setting network active status: {e}"
        logging.error(msg)
        raise Exception(msg) from e


@log_function_call
def set_network_autostart(conn, network_name, autostart):
    """
    Sets a network to autostart or not.
    """
    if not conn:
        raise ValueError("Invalid libvirt connection.")
    try:
        net = conn.networkLookupByName(network_name)
        net.setAutostart(autostart)
    except libvirt.libvirtError as e:
        msg = f"Error setting network autostart status: {e}"
        logging.error(msg)
        raise Exception(msg) from e


@log_function_call
def get_host_network_interfaces(conn: libvirt.virConnect) -> list[tuple[str, str]]:
    """
    Retrieves network interface names and their primary IPv4 addresses from the
    libvirt host pointed to by `conn` — works for both local and remote connections.

    Returns a list of tuples: (interface_name, ip_address). The IP may be empty
    if the libvirt 'interface' backend is unavailable or the interface has no
    IPv4 address.
    """
    if conn is None:
        return []

    interfaces: list[tuple[str, str]] = []
    try:
        devices = conn.listAllDevices(libvirt.VIR_CONNECT_LIST_NODE_DEVICES_CAP_NET)
    except libvirt.libvirtError as e:
        logging.error(f"Error listing host network devices: {e}")
        return []

    for dev in devices:
        try:
            root = ET.fromstring(dev.XMLDesc(0))
            cap = root.find("capability[@type='net']")
            if cap is None:
                continue
            iface_elem = cap.find("interface")
            if iface_elem is None or not iface_elem.text:
                continue
            name = iface_elem.text.strip()
            if name == "lo":
                continue

            ip_address = ""
            try:
                iface_xml = conn.interfaceLookupByName(name).XMLDesc(0)
                iface_root = ET.fromstring(iface_xml)
                ip_elem = iface_root.find("protocol[@family='ipv4']/ip")
                if ip_elem is not None:
                    ip_address = ip_elem.get("address", "")
            except libvirt.libvirtError:
                # virInterface backend not available on this host — skip IP lookup
                pass

            interfaces.append((name, ip_address))
        except libvirt.libvirtError as e:
            logging.warning(f"Skipping host net device: {e}")
            continue

    return interfaces


@log_function_call
def generate_mac_address():
    """Generates a random MAC address."""
    mac = [
        0x52,
        0x54,
        0x00,
        secrets.randbelow(0x7F),
        secrets.randbelow(0xFF),
        secrets.randbelow(0xFF),
    ]
    return ":".join(map(lambda x: "%02x" % x, mac))


@log_function_call
def get_existing_subnets(
    conn: libvirt.virConnect,
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """
    Returns a list of all IP subnets currently configured for libvirt networks.
    """
    subnets = []
    for net in conn.listAllNetworks():
        try:
            xml_desc = net.XMLDesc(0)
            root = ET.fromstring(xml_desc)
            ip_elements = root.findall(".//ip")
            for ip_elem in ip_elements:
                ip_addr = ip_elem.get("address")
                netmask = ip_elem.get("netmask")
                prefix = ip_elem.get("prefix")
                if ip_addr:
                    if netmask:
                        subnet_str = f"{ip_addr}/{netmask}"
                        try:
                            # ipaddress can handle netmask just fine
                            subnet = ipaddress.ip_network(subnet_str, strict=False)
                            subnets.append(subnet)
                        except ValueError:
                            pass  # Ignore invalid configurations
                    elif prefix:
                        subnet_str = f"{ip_addr}/{prefix}"
                        try:
                            subnet = ipaddress.ip_network(subnet_str, strict=False)
                            subnets.append(subnet)
                        except ValueError:
                            pass  # Ignore invalid configurations
        except libvirt.libvirtError:
            continue  # Ignore networks we can't get XML for
    return subnets


def suggest_free_subnet(conn: libvirt.virConnect, prefix_len: int = 24) -> str:
    """
    Suggests a free IPv4 subnet (not overlapping with existing libvirt networks).
    Scans common private /24 ranges in 192.168.x.0 and 10.x.0.0 space.
    Returns a CIDR string like '192.168.100.0/24', or '' if none found.
    """
    existing = get_existing_subnets(conn)
    candidates = (
        [ipaddress.ip_network(f"192.168.{i}.0/{prefix_len}") for i in range(1, 255)]
        + [ipaddress.ip_network(f"10.0.{i}.0/{prefix_len}") for i in range(1, 255)]
    )
    for candidate in candidates:
        if not any(candidate.overlaps(s) for s in existing):
            return str(candidate)
    return ""


@lru_cache(maxsize=32)
def get_host_network_info(conn: libvirt.virConnect):
    """
    Parses host capabilities XML to extract IP addresses and their subnet prefixes.
    Returns a list of ipaddress.IPv4Network or IPv6Network objects.
    """
    networks = []
    try:
        caps_xml = get_host_domain_capabilities(conn)
        if not caps_xml:
            return networks
        root = ET.fromstring(caps_xml)
        for interface in root.findall(".//interface"):
            ip_elem = interface.find("ip")
            if ip_elem is not None:
                address = ip_elem.get("address")
                prefix = ip_elem.get("prefix")
                if address and prefix:
                    try:
                        network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
                        networks.append(network)
                    except ValueError:
                        logging.warning(f"Could not parse IP address or prefix: {address}/{prefix}")
    except libvirt.libvirtError as e:
        logging.error(f"Failed to get capabilities or parse XML for host: {e}")
    return networks
