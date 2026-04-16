
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock
import libvirt
import pytest
from src.vmanager.vm_actions import set_direct_kernel_boot
from src.vmanager.vm_queries import get_direct_kernel_boot

def test_set_and_get_direct_kernel_boot():
    # Mock domain
    domain = MagicMock(spec=libvirt.virDomain)
    domain.isActive.return_value = False
    conn = MagicMock(spec=libvirt.virConnect)
    domain.connect.return_value = conn
    
    # Initial XML
    initial_xml = "<domain><os><type>hvm</type></os></domain>"
    domain.XMLDesc.return_value = initial_xml
    
    # Mock get_internal_id to return a fixed ID
    with MagicMock() as mock_get_id:
        # We need to mock invalidate_cache and get_internal_id as they are used in set_direct_kernel_boot
        import src.vmanager.vm_actions as vm_actions
        original_invalidate = vm_actions.invalidate_cache
        original_get_id = vm_actions.get_internal_id
        vm_actions.invalidate_cache = MagicMock()
        vm_actions.get_internal_id = MagicMock(return_value="test-uuid")
        
        try:
            # Test setting values (enabled)
            set_direct_kernel_boot(domain, True, kernel="/path/to/vmlinuz", initrd="/path/to/initrd", cmdline="root=/dev/sda1")
            
            # Verify defineXML was called with updated XML
            args, _ = conn.defineXML.call_args
            updated_xml = args[0]
            root = ET.fromstring(updated_xml)
            
            assert root.findtext(".//os/kernel") == "/path/to/vmlinuz"
            assert root.findtext(".//os/initrd") == "/path/to/initrd"
            assert root.findtext(".//os/cmdline") == "root=/dev/sda1"
            
            # Test get_direct_kernel_boot
            dkb_info = get_direct_kernel_boot(root)
            assert dkb_info["kernel"] == "/path/to/vmlinuz"
            assert dkb_info["initrd"] == "/path/to/initrd"
            assert dkb_info["cmdline"] == "root=/dev/sda1"
            assert dkb_info["enabled"] is True
            
            # Test disabling values (should move to metadata)
            domain.XMLDesc.return_value = updated_xml
            set_direct_kernel_boot(domain, False, kernel="/path/to/vmlinuz", initrd="/path/to/initrd", cmdline="root=/dev/sda1")
            
            args, _ = conn.defineXML.call_args
            disabled_xml = args[0]
            root_disabled = ET.fromstring(disabled_xml)
            
            assert root_disabled.find(".//os/kernel") is None
            assert root_disabled.find(".//os/initrd") is None
            assert root_disabled.find(".//os/cmdline") is None
            
            # Test get_direct_kernel_boot on disabled XML (should find in metadata)
            dkb_info_disabled = get_direct_kernel_boot(root_disabled)
            assert dkb_info_disabled["kernel"] == "/path/to/vmlinuz"
            assert dkb_info_disabled["enabled"] is False
            
        finally:
            vm_actions.invalidate_cache = original_invalidate
            vm_actions.get_internal_id = original_get_id

def test_get_direct_kernel_boot_none():
    assert get_direct_kernel_boot(None) == {}
    
    root = ET.fromstring("<domain><os></os></domain>")
    assert get_direct_kernel_boot(root) == {"kernel": None, "initrd": None, "cmdline": None, "enabled": False}
