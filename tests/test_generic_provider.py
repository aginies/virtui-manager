import pytest
from vmanager.provisioning.os_provider import OSType
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
