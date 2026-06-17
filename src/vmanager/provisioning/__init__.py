"""
Multi-OS VM Provisioning System

This package provides a pluggable architecture for provisioning VMs with different
operating systems including Windows, Ubuntu, Debian, and OpenSUSE.
"""

from .os_provider import OSVersion, OSType
from .libosinfo_manager import LibosinfoManager
from .automation_engine import AutomationEngine

__all__ = ["OSVersion", "OSType", "LibosinfoManager", "AutomationEngine"]
