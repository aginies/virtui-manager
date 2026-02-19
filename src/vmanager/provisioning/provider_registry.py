"""
Provider Registry for OS Provisioning

This module manages the registration and retrieval of OS providers,
allowing the system to dynamically support multiple operating systems.
"""

import logging
from typing import Dict, List, Optional

from .os_provider import OSProvider, OSType, OSVersion


class ProviderRegistry:
    """Registry for managing OS providers."""

    def __init__(self):
        self._providers: Dict[OSType, OSProvider] = {}
        self._logger = logging.getLogger(__name__)

    def register_provider(self, provider: OSProvider) -> None:
        """Register an OS provider."""
        os_type = provider.os_type
        if os_type in self._providers:
            self._logger.warning(f"Overriding existing provider for {os_type.value}")

        self._providers[os_type] = provider
        self._logger.info(f"Registered provider for {os_type.value}")

    def get_provider(self, os_type: OSType) -> Optional[OSProvider]:
        """Get provider for a specific OS type."""
        return self._providers.get(os_type)

    def get_all_providers(self) -> Dict[OSType, OSProvider]:
        """Get all registered providers."""
        return self._providers.copy()

    def get_supported_os_types(self) -> List[OSType]:
        """Get list of supported OS types."""
        return list(self._providers.keys())

    def get_all_supported_versions(self) -> Dict[OSType, List[OSVersion]]:
        """Get all supported versions for all registered providers."""
        versions = {}
        for os_type, provider in self._providers.items():
            try:
                versions[os_type] = provider.get_supported_versions()
            except Exception as e:
                self._logger.error(f"Error getting versions for {os_type.value}: {e}")
                versions[os_type] = []
        return versions

    def find_version(self, os_type: OSType, version_id: str) -> Optional[OSVersion]:
        """Find a specific OS version by type and version ID."""
        provider = self.get_provider(os_type)
        if not provider:
            return None

        try:
            versions = provider.get_supported_versions()
            for version in versions:
                if version.version_id == version_id:
                    return version
        except Exception as e:
            self._logger.error(f"Error searching for version {version_id} in {os_type.value}: {e}")

        return None

    def is_supported(self, os_type: OSType) -> bool:
        """Check if an OS type is supported."""
        return os_type in self._providers


# Global registry instance
_registry = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    """Get the global provider registry instance."""
    return _registry


def register_provider(provider: OSProvider) -> None:
    """Register a provider with the global registry."""
    _registry.register_provider(provider)
