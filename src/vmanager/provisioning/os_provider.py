"""
OS Provider Interface for Multi-OS VM Provisioning

This module defines the base interface that all OS providers must implement,
along with common data structures for OS types and versions.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class OSType(Enum):
    """Supported operating system types."""

    LINUX = "Linux"  # Generic Linux (used for OpenSUSE provider)
    OPENSUSE = "openSUSE"
    WINDOWS = "Windows"
    UBUNTU = "Ubuntu"
    DEBIAN = "Debian"


@dataclass
class OSVersion:
    """Represents a specific version of an operating system."""

    os_type: OSType
    version_id: str  # e.g., "11", "22.04", "15.5"
    display_name: str  # e.g., "Windows 11", "Ubuntu 22.04 LTS"
    architecture: str = "x86_64"  # Default to x86_64
    is_evaluation: bool = False  # True for evaluation/trial versions

    def __str__(self) -> str:
        return self.display_name


@dataclass
class DriverInfo:
    """Information about required drivers for an OS."""

    name: str
    url: str
    version: Optional[str] = None
    description: Optional[str] = None
    required: bool = True


@dataclass
class AutomationConfig:
    """Configuration for unattended installation."""

    template_name: str
    variables: Dict[str, Any]
    supports_custom_user: bool = True
    supports_network_config: bool = True


class OSProvider(ABC):
    """Abstract base class for OS-specific provisioning providers."""

    @property
    @abstractmethod
    def os_type(self) -> OSType:
        """Return the OS type this provider handles."""
        pass

    @abstractmethod
    def get_supported_versions(self) -> List[OSVersion]:
        """Return list of supported OS versions."""
        pass

    @abstractmethod
    def get_iso_sources(self, version: OSVersion) -> List[str]:
        """Return list of URLs where ISOs can be downloaded."""
        pass

    @abstractmethod
    def get_default_vm_settings(self, version: OSVersion, vm_type: str) -> Dict[str, Any]:
        """Return default VM configuration for this OS and VM type."""
        pass

    @abstractmethod
    def supports_unattended_install(self, version: OSVersion) -> bool:
        """Return True if this OS version supports unattended installation."""
        pass

    @abstractmethod
    def get_automation_config(self, version: OSVersion) -> Optional[AutomationConfig]:
        """Return automation configuration for unattended installation."""
        pass

    @abstractmethod
    def get_required_drivers(self, version: OSVersion) -> List[DriverInfo]:
        """Return list of required drivers for this OS version."""
        pass

    @abstractmethod
    def generate_automation_file(
        self, version: OSVersion, vm_name: str, user_config: Dict[str, Any], output_path: Path
    ) -> Path:
        """Generate automation file (unattend.xml, preseed, etc.) for unattended install."""
        pass

    @abstractmethod
    def get_post_install_scripts(self, version: OSVersion) -> List[str]:
        """Return list of commands to run after OS installation."""
        pass

    def validate_iso(self, iso_path: Path, version: OSVersion) -> bool:
        """Validate that an ISO file matches the expected OS version.

        Default implementation just checks file existence.
        Providers can override for specific validation logic.
        """
        return iso_path.exists() and iso_path.stat().st_size > 0

    def get_vm_type_mapping(self) -> Dict[str, str]:
        """Map VirtUI VM types to OS-specific configurations.

        Returns mapping from VMType enum values to provider-specific settings.
        Default implementation returns empty dict (no special mapping).
        """
        return {}
