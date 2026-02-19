"""
Multi-OS VM Provisioning System

This package provides a pluggable architecture for provisioning VMs with different
operating systems including Windows, Ubuntu, Debian, and OpenSUSE.
"""

from .os_provider import OSProvider, OSVersion, OSType
from .provider_registry import ProviderRegistry

__all__ = ["OSProvider", "OSVersion", "OSType", "ProviderRegistry"]
