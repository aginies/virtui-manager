import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import libvirt
import xml.etree.ElementTree as ET

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.vm_queries import (
    get_vm_networks_info,
    get_vm_disks_info,
    get_vm_devices_info,
    get_vm_machine_info,
    get_vm_description,
    get_status,
    get_vm_cpu_model,
    get_vm_video_model,
    get_vm_firmware_info,
    get_vm_network_dns_gateway_info,
    get_vm_network_ip,
    get_vm_shared_memory_info,
    get_boot_info,
    _parse_domain_xml,
    _parse_domain_xml_by_hash,
)
from vmanager.constants import StatusText


class TestVMQueries(unittest.TestCase):
    def setUp(self):
        self.xml_content = """
        <domain type='kvm'>
          <name>test-vm</name>
          <uuid>test-uuid-123</uuid>
          <description>Test VM Description</description>
          <memory unit='KiB'>1048576</memory>
          <vcpu placement='static'>2</vcpu>
          <os>
            <type arch='x86_64' machine='pc-q35-6.2'>hvm</type>
            <loader readonly='yes' type='pflash'>/usr/share/OVMF/OVMF_CODE.fd</loader>
            <boot dev='hd'/>
          </os>
          <cpu mode='host-passthrough'/>
          <devices>
            <disk type='file' device='disk'>
              <driver name='qemu' type='qcow2'/>
              <source file='/var/lib/libvirt/images/test-vm.qcow2'/>
              <target dev='vda' bus='virtio'/>
            </disk>
            <interface type='network'>
              <mac address='52:54:00:12:34:56'/>
              <source network='default'/>
              <model type='virtio'/>
            </interface>
            <graphics type='spice' port='5900' autoport='yes'/>
            <video>
              <model type='virtio' vram='65536' heads='1' primary='yes'/>
            </video>
          </devices>
        </domain>
        """
        self.root = ET.fromstring(self.xml_content)

        # Mock domain object
        self.mock_domain = MagicMock(spec=libvirt.virDomain)
        self.mock_domain.XMLDesc.return_value = self.xml_content
        self.mock_domain.name.return_value = "test-vm"
        self.mock_domain.UUIDString.return_value = "test-uuid-123"
        self.mock_domain.state.return_value = [libvirt.VIR_DOMAIN_RUNNING, 1]
        self.mock_domain.maxMemory.return_value = 1048576
        self.mock_domain.info.return_value = [
            libvirt.VIR_DOMAIN_RUNNING,
            1048576,
            1048576,
            2,
            1000000000,
        ]

        # Mock libvirt connection
        self.mock_conn = MagicMock(spec=libvirt.virConnect)
        self.mock_domain.connect.return_value = self.mock_conn

    def test_parse_domain_xml(self):
        """Test domain XML parsing function."""
        root = _parse_domain_xml(self.xml_content)
        self.assertIsNotNone(root)
        self.assertEqual(root.tag, "domain")

    def test_parse_domain_xml_invalid(self):
        """Test domain XML parsing with invalid XML."""
        invalid_xml = "<invalid xml>"
        root = _parse_domain_xml(invalid_xml)
        self.assertIsNone(root)

    def test_get_vm_networks_info(self):
        """Test getting VM network information."""
        networks = get_vm_networks_info(self.root)
        self.assertIsInstance(networks, list)
        self.assertEqual(len(networks), 1)
        self.assertEqual(networks[0]['network'], 'default')
        self.assertEqual(networks[0]['mac'], '52:54:00:12:34:56')

    def test_get_vm_disks_info(self):
        """Test getting VM disk information."""
        disks = get_vm_disks_info(self.mock_conn, self.root)
        self.assertIsInstance(disks, list)
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0]['path'], '/var/lib/libvirt/images/test-vm.qcow2')

    def test_get_vm_devices_info(self):
        """Test getting VM devices information."""
        devices = get_vm_devices_info(self.root)
        self.assertIsInstance(devices, dict)
        self.assertIn('graphics', devices)
        self.assertEqual(devices['graphics'][0]['type'], 'spice')

    def test_get_vm_machine_info(self):
        """Test getting VM machine information."""
        machine_info = get_vm_machine_info(self.root)
        self.assertIsInstance(machine_info, str)
        self.assertEqual(machine_info, 'pc-q35-6.2')

    def test_get_vm_description(self):
        """Test getting VM description."""
        description = get_vm_description(self.mock_domain, root=self.root)
        self.assertEqual(description, "Test VM Description")

    def test_get_status(self):
        """Test getting VM status."""
        status = get_status(self.mock_domain)
        self.assertEqual(status, StatusText.RUNNING)

    def test_get_vm_cpu_model(self):
        """Test getting VM CPU model."""
        cpu_model = get_vm_cpu_model(self.root)
        self.assertEqual(cpu_model, 'host-passthrough')

    def test_get_vm_video_model(self):
        """Test getting VM video model."""
        video_model = get_vm_video_model(self.root)
        self.assertEqual(video_model, 'virtio')

    def test_get_vm_firmware_info(self):
        """Test getting VM firmware information."""
        firmware = get_vm_firmware_info(self.root)
        self.assertIsInstance(firmware, dict)
        self.assertEqual(firmware['type'], 'UEFI')

    def test_get_vm_network_dns_gateway_info(self):
        """Test getting VM network DNS gateway information."""
        mock_network = MagicMock()
        mock_network.XMLDesc.return_value = "<network><ip address='192.168.1.1'/><dns><server address='8.8.8.8'/></dns></network>"
        self.mock_conn.networkLookupByName.return_value = mock_network
        
        dns_info = get_vm_network_dns_gateway_info(self.mock_domain, root=self.root)
        self.assertIsInstance(dns_info, list)
        self.assertEqual(len(dns_info), 1)
        self.assertEqual(dns_info[0]['gateway'], '192.168.1.1')
        self.assertIn('8.8.8.8', dns_info[0]['dns_servers'])

    def test_get_vm_network_ip(self):
        """Test getting VM network IP addresses."""
        self.mock_domain.interfaceAddresses.return_value = {
            'vda': {'hwaddr': '52:54:00:12:34:56', 'addrs': [{'type': libvirt.VIR_IP_ADDR_TYPE_IPV4, 'addr': '192.168.1.10', 'prefix': 24}]}
        }
        ip_info = get_vm_network_ip(self.mock_domain)
        self.assertIsInstance(ip_info, list)
        self.assertEqual(len(ip_info), 1)
        self.assertEqual(ip_info[0]['mac'], '52:54:00:12:34:56')

    def test_get_vm_shared_memory_info(self):
        """Test getting VM shared memory information."""
        shared_mem_info = get_vm_shared_memory_info(self.root)
        self.assertIsInstance(shared_mem_info, bool)
        self.assertFalse(shared_mem_info)

    def test_get_boot_info(self):
        """Test getting VM boot information."""
        boot_info = get_boot_info(self.mock_conn, self.root)
        self.assertIsInstance(boot_info, dict)
        self.assertIn('order', boot_info)


if __name__ == "__main__":
    unittest.main()
