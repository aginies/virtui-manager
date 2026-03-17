"""
Tests for OS Provider Registry and selection logic.
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add the src directory to the path to import vmanager modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from vmanager.provisioning.provider_registry import ProviderRegistry
from vmanager.provisioning.os_provider import OSType, OSProvider
from vmanager.vm_provisioner import VMProvisioner

class TestProviderRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = ProviderRegistry()

    def test_register_and_get_provider(self):
        """Test registering and retrieving a provider."""
        mock_provider = MagicMock(spec=OSProvider)
        mock_provider.os_type = OSType.UBUNTU
        
        self.registry.register_provider(mock_provider)
        self.assertEqual(self.registry.get_provider(OSType.UBUNTU), mock_provider)
        self.assertIn(OSType.UBUNTU, self.registry.get_supported_os_types())

    def test_get_all_providers(self):
        """Test retrieving all registered providers."""
        p1 = MagicMock(spec=OSProvider); p1.os_type = OSType.UBUNTU
        p2 = MagicMock(spec=OSProvider); p2.os_type = OSType.DEBIAN
        
        self.registry.register_provider(p1)
        self.registry.register_provider(p2)
        
        providers = self.registry.get_all_providers()
        self.assertEqual(len(providers), 2)
        self.assertEqual(providers[OSType.UBUNTU], p1)
        self.assertEqual(providers[OSType.DEBIAN], p2)

    def test_is_supported(self):
        """Test checking if an OS type is supported."""
        mock_provider = MagicMock(spec=OSProvider)
        mock_provider.os_type = OSType.ALPINE
        
        self.registry.register_provider(mock_provider)
        self.assertTrue(self.registry.is_supported(OSType.ALPINE))
        self.assertFalse(self.registry.is_supported(OSType.ARCHLINUX))

class TestVMProvisionerProviderMapping(unittest.TestCase):
    def setUp(self):
        self.mock_conn = MagicMock()
        with patch("vmanager.vm_provisioner.get_host_architecture") as mock_get_arch:
            mock_get_arch.return_value = "x86_64"
            self.provisioner = VMProvisioner(self.mock_conn)

    def test_get_provider_mapping(self):
        """Test that VMProvisioner maps string OS types to correct providers."""
        # VMProvisioner registers real providers in its __init__
        from vmanager.provisioning.providers.ubuntu_provider import UbuntuProvider
        from vmanager.provisioning.providers.debian_provider import DebianProvider
        from vmanager.provisioning.providers.alpine_provider import AlpineProvider
        from vmanager.provisioning.providers.opensuse_provider import OpenSUSEProvider
        
        self.assertIsInstance(self.provisioner.get_provider("ubuntu"), UbuntuProvider)
        self.assertIsInstance(self.provisioner.get_provider("debian"), DebianProvider)
        self.assertIsInstance(self.provisioner.get_provider("alpine"), AlpineProvider)
        self.assertIsInstance(self.provisioner.get_provider("opensuse"), OpenSUSEProvider)
        self.assertIsInstance(self.provisioner.get_provider("linux"), OpenSUSEProvider)

if __name__ == "__main__":
    unittest.main()
