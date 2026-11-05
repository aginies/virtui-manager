"""
Tests for host capability detection (host_capabilities.py).
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.host_capabilities import (
    get_confidential_guest_support,
    get_host_cpu_flags,
    has_secure_vm_capability,
    is_sev_capable,
    is_snp_capable,
    is_tdx_capable,
)


def make_conn(caps_xml: str = "", domcaps_xml: str = "") -> MagicMock:
    """Create a mock libvirt connection with canned XML responses."""
    conn = MagicMock()
    conn.getCapabilities.return_value = caps_xml
    conn.getDomainCapabilities.return_value = domcaps_xml
    return conn


# --- get_host_cpu_flags ---

def test_cpu_flags_from_feature_elements():
    """Newer libvirt reports <feature name='...'> elements."""
    xml = """
    <capabilities>
      <host>
        <cpu>
          <architecture>x86_64</architecture>
          <model>GraniteRapids</model>
          <feature name='vmx'/>
          <feature name='sev_snp'/>
          <feature name='avx2'/>
        </cpu>
      </host>
    </capabilities>
    """
    conn = make_conn(caps_xml=xml)
    flags = get_host_cpu_flags(conn)
    assert flags == {"vmx", "sev_snp", "avx2"}


def test_cpu_flags_from_flag_elements():
    """Older libvirt reports <flags><flag name='...'> elements."""
    xml = """
    <capabilities>
      <host>
        <cpu>
          <flags>
            <flag name='vmx'/>
            <flag name='tdx_host_platform'/>
          </flags>
        </cpu>
      </host>
    </capabilities>
    """
    conn = make_conn(caps_xml=xml)
    flags = get_host_cpu_flags(conn)
    assert flags == {"vmx", "tdx_host_platform"}


def test_cpu_flags_none_conn():
    assert get_host_cpu_flags(None) == set()


def test_cpu_flags_parse_error():
    conn = make_conn(caps_xml="not xml at all")
    assert get_host_cpu_flags(conn) == set()


def test_cpu_flags_missing_cpu_section():
    xml = "<capabilities><host></host></capabilities>"
    conn = make_conn(caps_xml=xml)
    assert get_host_cpu_flags(conn) == set()


def test_cpu_flags_libvirt_error():
    conn = MagicMock()
    conn.getCapabilities.side_effect = Exception("connection closed")
    assert get_host_cpu_flags(conn) == set()


# --- get_confidential_guest_support ---

def test_domcaps_tdx_supported_flat_format():
    """libvirt >= 11: <features> directly under <domainCapabilities>."""
    xml = """
    <domainCapabilities>
      <features>
        <tdx supported='yes'/>
        <sev supported='no'/>
      </features>
    </domainCapabilities>
    """
    conn = make_conn(domcaps_xml=xml)
    support = get_confidential_guest_support(conn)
    assert support == {"tdx": True, "sev": False}


def test_domcaps_sev_supported_nested_format():
    """Older libvirt: <guest><features> nesting."""
    xml = """
    <capabilities>
      <guest arch='x86_64' type='hvm'>
        <features>
          <tdx supported='no'/>
          <sev supported='yes'/>
        </features>
      </guest>
    </capabilities>
    """
    conn = make_conn(domcaps_xml=xml)
    support = get_confidential_guest_support(conn)
    assert support == {"tdx": False, "sev": True}


def test_domcaps_features_absent():
    """libvirt without tdx/sev feature reporting."""
    xml = "<domainCapabilities><features><ps2 supported='yes'/></features></domainCapabilities>"
    conn = make_conn(domcaps_xml=xml)
    support = get_confidential_guest_support(conn)
    assert support == {"tdx": False, "sev": False}


def test_domcaps_none_conn():
    support = get_confidential_guest_support(None)
    assert support == {"tdx": False, "sev": False}


def test_domcaps_parse_error():
    conn = make_conn(domcaps_xml="garbage")
    support = get_confidential_guest_support(conn)
    assert support == {"tdx": False, "sev": False}


# --- is_snp_capable / is_tdx_capable / is_sev_capable ---

def test_is_snp_capable_true():
    xml = "<capabilities><host><cpu><feature name='sev_snp'/></cpu></host></capabilities>"
    conn = make_conn(caps_xml=xml)
    assert is_snp_capable(conn) is True


def test_is_snp_capable_false():
    xml = "<capabilities><host><cpu><feature name='vmx'/></cpu></host></capabilities>"
    conn = make_conn(caps_xml=xml)
    assert is_snp_capable(conn) is False


def test_is_tdx_capable_true():
    xml = "<domainCapabilities><features><tdx supported='yes'/></features></domainCapabilities>"
    conn = make_conn(domcaps_xml=xml)
    assert is_tdx_capable(conn) is True


def test_is_tdx_capable_false():
    xml = "<domainCapabilities><features><tdx supported='no'/></features></domainCapabilities>"
    conn = make_conn(domcaps_xml=xml)
    assert is_tdx_capable(conn) is False


def test_is_sev_capable_true():
    xml = "<domainCapabilities><features><sev supported='yes'/></features></domainCapabilities>"
    conn = make_conn(domcaps_xml=xml)
    assert is_sev_capable(conn) is True


def test_is_sev_capable_false():
    xml = "<domainCapabilities><features><sev supported='no'/></features></domainCapabilities>"
    conn = make_conn(domcaps_xml=xml)
    assert is_sev_capable(conn) is False


# --- has_secure_vm_capability ---

def test_secure_capable_tdx_via_domcaps():
    xml = "<domainCapabilities><features><tdx supported='yes'/><sev supported='no'/></features></domainCapabilities>"
    conn = make_conn(caps_xml="<capabilities><host><cpu></cpu></host></capabilities>", domcaps_xml=xml)
    assert has_secure_vm_capability(conn) is True


def test_secure_capable_sev_via_domcaps():
    xml = "<domainCapabilities><features><tdx supported='no'/><sev supported='yes'/></features></domainCapabilities>"
    conn = make_conn(caps_xml="<capabilities><host><cpu></cpu></host></capabilities>", domcaps_xml=xml)
    assert has_secure_vm_capability(conn) is True


def test_secure_capable_snp_via_cpu_flag():
    """Older libvirt without domcaps features: CPU flag fallback."""
    caps_xml = "<capabilities><host><cpu><feature name='sev_snp'/></cpu></host></capabilities>"
    domcaps_xml = "<domainCapabilities><features><ps2 supported='yes'/></features></domainCapabilities>"
    conn = make_conn(caps_xml=caps_xml, domcaps_xml=domcaps_xml)
    assert has_secure_vm_capability(conn) is True


def test_secure_capable_tdx_flag_via_cpu_flag():
    """tdx_host_platform CPU flag (if a libvirt ever exposes it)."""
    caps_xml = "<capabilities><host><cpu><feature name='tdx_host_platform'/></cpu></host></capabilities>"
    domcaps_xml = "<domainCapabilities><features></features></domainCapabilities>"
    conn = make_conn(caps_xml=caps_xml, domcaps_xml=domcaps_xml)
    assert has_secure_vm_capability(conn) is True


def test_secure_not_capable():
    caps_xml = "<capabilities><host><cpu><feature name='vmx'/></cpu></host></capabilities>"
    domcaps_xml = "<domainCapabilities><features><tdx supported='no'/><sev supported='no'/></features></domainCapabilities>"
    conn = make_conn(caps_xml=caps_xml, domcaps_xml=domcaps_xml)
    assert has_secure_vm_capability(conn) is False


def test_secure_not_capable_none_conn():
    assert has_secure_vm_capability(None) is False


def test_secure_not_capable_both_xml_fail():
    conn = make_conn(caps_xml="bad", domcaps_xml="worse")
    assert has_secure_vm_capability(conn) is False
