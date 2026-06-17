"""
Provider Registry for OS Provisioning

This module manages the registration and retrieval of OS providers,
allowing the system to dynamically support multiple operating systems.
"""

import logging
from typing import Dict, List, Optional

from .os_provider import OSType, OSVersion
from .libosinfo_manager import LibosinfoManager


class ProviderRegistry:
    """Registry for managing OS versions (Bridge for LibosinfoManager)."""

    def __init__(self):
        self._logger = logging.getLogger(__name__)
        self._libosinfo = LibosinfoManager()

    def get_supported_os_types(self) -> List[OSType]:
        """Get list of supported OS types (derived from libosinfo)."""
        catalog = self._libosinfo.get_os_catalog()
        return sorted(list(set(v.os_type for v in catalog)), key=lambda x: x.value)

    def get_supported_versions(self, os_type: OSType) -> List[OSVersion]:
        """Get supported versions for a specific OS type from libosinfo."""
        catalog = self._libosinfo.get_os_catalog()
        return [v for v in catalog if v.os_type == os_type]

    def get_all_supported_versions(self) -> Dict[OSType, List[OSVersion]]:
        """Get all supported versions from libosinfo."""
        catalog = self._libosinfo.get_os_catalog()
        versions = {}
        for v in catalog:
            if v.os_type not in versions:
                versions[v.os_type] = []
            versions[v.os_type].append(v)
        return versions

    def find_version(self, os_type: OSType, version_id: str) -> Optional[OSVersion]:
        """Find a specific OS version by type and version ID."""
        catalog = self._libosinfo.get_os_catalog()
        for v in catalog:
            if v.os_type == os_type and v.version_id == version_id:
                return v
        return None

    def is_supported(self, os_type: OSType) -> bool:
        """Check if an OS type is supported (Legacy)."""
        return os_type in self.get_supported_os_types()


# Global registry instance
_registry = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    """Get the global provider registry instance."""
    return _registry
