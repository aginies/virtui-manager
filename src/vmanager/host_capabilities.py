"""
Host capability detection for VirtUI Manager.

Central place for "can this host do X?" checks. All checks go through the
libvirt connection, so they work identically for local and remote
hypervisors.

Confidential-computing detection (AMD SEV / SEV-SNP, Intel TDX):

- Primary signal: the domain capabilities ``<features>`` section, which
  exposes ``<tdx supported='yes|no'/>`` and ``<sev supported='yes|no'/>``
  (libvirt >= 10/11). This is the reliable way to detect TDX remotely:
  ``tdx_host_platform`` is a *kernel* flag (TDX module enabled via kernel
  parameter), not a CPU model feature, so it is not exposed in the host
  CPU feature list.
- Secondary signal: the host CPU flags from ``getCapabilities()``
  (``<host><cpu>``) — ``sev_snp`` (AMD CPUID flag) and
  ``tdx_host_platform`` (kept for completeness; not currently exposed by
  libvirt, but would be picked up if a future version reports it).

Detection will be improved here later (e.g. KVM module, SEV-SNP firmware,
TDX module checks), so all host capability checks should live in this
module.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Dict, Optional, Set

import libvirt

# CPU flags identifying confidential-computing platform support
SNP_CPU_FLAG = "sev_snp"
TDX_CPU_FLAG = "tdx_host_platform"


def get_host_cpu_flags(conn: Optional[libvirt.virConnect]) -> Set[str]:
    """Return the set of CPU flags/features of the host.

    Parsed from ``conn.getCapabilities()`` (``<host><cpu>``), which works
    for both local and remote hypervisors. Both ``<flags><flag name=...>``
    and ``<feature name=...>`` elements are collected, depending on the
    libvirt version.

    Note: this is the host CPU *model* feature set, not the full
    /proc/cpuinfo flag list — kernel-level flags such as
    ``tdx_host_platform`` are not included.

    Returns an empty set if the connection is unusable or parsing fails.
    """
    if conn is None:
        return set()
    try:
        root = ET.fromstring(conn.getCapabilities())
        cpu = root.find("host/cpu")
        if cpu is None:
            return set()
        flags: Set[str] = set()
        for flag in cpu.findall("flags/flag"):
            name = flag.get("name")
            if name:
                flags.add(name)
        for feature in cpu.findall("feature"):
            name = feature.get("name")
            if name:
                flags.add(name)
        return flags
    except Exception as e:
        logging.warning(f"Could not read host CPU flags: {e}")
        return set()


def get_confidential_guest_support(conn: Optional[libvirt.virConnect]) -> Dict[str, bool]:
    """Return ``{"tdx": bool, "sev": bool}`` from the domain capabilities.

    libvirt exposes hypervisor support for confidential guests in the
    domain capabilities ``<features>`` section (``<tdx supported='yes'/>``
    and ``<sev supported='yes'/>``). Both the flat format (libvirt >= 11,
    ``<features>`` directly under ``<domainCapabilities>``) and the older
    nested format (``<guest><features>``) are handled.

    Returns ``{"tdx": False, "sev": False}`` if the connection is unusable,
    parsing fails, or the libvirt version does not report these features.
    """
    support: Dict[str, bool] = {"tdx": False, "sev": False}
    if conn is None:
        return support
    try:
        root = ET.fromstring(conn.getDomainCapabilities())
    except Exception as e:
        logging.warning(f"Could not read domain capabilities: {e}")
        return support
    features_list = [root.find("features")]  # libvirt >= 11 flat format
    features_list.extend(root.findall("guest/features"))  # older nested format
    for features in features_list:
        if features is None:
            continue
        tdx_elem = features.find("tdx")
        if tdx_elem is not None and tdx_elem.get("supported") == "yes":
            support["tdx"] = True
        sev_elem = features.find("sev")
        if sev_elem is not None and sev_elem.get("supported") == "yes":
            support["sev"] = True
    return support


def is_snp_capable(conn: Optional[libvirt.virConnect]) -> bool:
    """True if the host CPU exposes the ``sev_snp`` flag (AMD SEV-SNP)."""
    return SNP_CPU_FLAG in get_host_cpu_flags(conn)


def is_tdx_capable(conn: Optional[libvirt.virConnect]) -> bool:
    """True if the hypervisor can run Intel TDX guests.

    Checked via the domain capabilities ``<tdx supported='yes'/>`` feature
    (the ``tdx_host_platform`` kernel flag is not exposed by libvirt in
    the host CPU feature list).
    """
    return get_confidential_guest_support(conn)["tdx"]


def is_sev_capable(conn: Optional[libvirt.virConnect]) -> bool:
    """True if the hypervisor can run AMD SEV guests."""
    return get_confidential_guest_support(conn)["sev"]


def has_secure_vm_capability(conn: Optional[libvirt.virConnect]) -> bool:
    """True if the host can run Secure VMs (Intel TDX or AMD SEV/SNP)."""
    support = get_confidential_guest_support(conn)
    if support["tdx"] or support["sev"]:
        return True
    # Fallback: CPU flags (covers hosts whose libvirt does not report the
    # tdx/sev domain capabilities features)
    flags = get_host_cpu_flags(conn)
    return SNP_CPU_FLAG in flags or TDX_CPU_FLAG in flags
