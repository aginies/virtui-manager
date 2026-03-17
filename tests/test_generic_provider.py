import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from vmanager.provisioning.os_provider import OSType, OSVersion
from vmanager.provisioning.providers.generic_provider import GenericProvider

class TestGenericProvider:
    @pytest.fixture
    def provider(self):
        return GenericProvider()

    def test_os_type(self, provider):
        assert provider.os_type == OSType.GENERIC

    def test_get_supported_versions(self, provider):
        versions = provider.get_supported_versions()
        assert len(versions) == 1
        assert versions[0].version_id == "custom"
        assert versions[0].os_type == OSType.GENERIC

    @patch("vmanager.provisioning.providers.generic_provider.get_registry")
    def test_generate_automation_file_delegation_ubuntu(self, mock_registry, provider):
        mock_ubuntu_provider = MagicMock()
        mock_registry.return_value.get_provider.return_value = mock_ubuntu_provider
        
        user_config = {"password": "foo"}
        output_path = Path("/tmp")
        
        provider.generate_automation_file(None, "test-vm", user_config, output_path, "autoinstall-basic.yaml")
        
        mock_registry.return_value.get_provider.assert_called_with(OSType.UBUNTU)
        mock_ubuntu_provider.generate_automation_file.assert_called_once()

    @patch("vmanager.provisioning.providers.generic_provider.get_registry")
    def test_generate_automation_file_delegation_opensuse(self, mock_registry, provider):
        mock_opensuse_provider = MagicMock()
        mock_registry.return_value.get_provider.return_value = mock_opensuse_provider
        
        user_config = {"password": "foo"}
        output_path = Path("/tmp")
        
        provider.generate_automation_file(None, "test-vm", user_config, output_path, "autoyast-basic.xml")
        
        mock_registry.return_value.get_provider.assert_called_with(OSType.LINUX) # OpenSUSE uses LINUX OSType
        mock_opensuse_provider.generate_automation_file.assert_called_once()

    @patch("vmanager.provisioning.templates.auto_template_manager.AutoYaSTTemplateManager")
    def test_basic_substitution_fallback(self, mock_tm_class, provider):
        # Create a mock instance
        mock_tm = mock_tm_class.return_value
        mock_tm.get_template_content.return_value = "hostname: {vm_name}\nuser: {username}"
        
        user_config = {"username": "customuser"}
        output_path = Path("/tmp")
        vm_name = "my-generic-vm"
        
        with patch("vmanager.provisioning.providers.generic_provider.open", create=True) as mock_open:
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file
            
            provider.generate_automation_file(None, vm_name, user_config, output_path, "custom-template.txt")
            
            # Verify substitution
            args, _ = mock_file.write.call_args
            written_content = args[0]
            assert f"hostname: {vm_name}" in written_content
            assert "user: customuser" in written_content
