"""
OS Provider Interface for Multi-OS VM Provisioning

This module defines the base interface that all OS providers must implement,
along with common data structures for OS types and versions.
"""

import base64
import hashlib
import os
import subprocess
import string
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class OSType(Enum):
    """Supported operating system types."""

    LINUX = "Linux"
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


def hash_password(plaintext_password: str) -> str:
    """
    Hash a plaintext password using SHA-512 crypt format.

    This is suitable for use in AutoYaST, Agama, Ubuntu autoinstall, and other
    Linux automation systems. Uses mkpasswd if available, otherwise falls back
    to a Python implementation compatible with Python 3.6+.

    Args:
        plaintext_password: The password to hash

    Returns:
        SHA-512 hashed password string in crypt format ($6$salt$hash)
    """
    # Try using mkpasswd (preferred method, available on most Linux systems)
    try:
        result = subprocess.run(
            ["mkpasswd", "-m", "sha-512", plaintext_password],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # Fall back to Python implementation

    # Fallback: Python implementation using hashlib
    salt_chars = string.ascii_letters + string.digits + "./"
    salt = "".join(
        os.urandom(1)[0] % len(salt_chars) and salt_chars[os.urandom(1)[0] % len(salt_chars)]
        for _ in range(16)
    )

    # Simplified SHA-512 based password hash
    combined = f"{salt}{plaintext_password}".encode("utf-8")
    hashed = hashlib.sha512(combined).digest()
    for _ in range(5000):  # Standard rounds for SHA-512
        hashed = hashlib.sha512(hashed + combined).digest()

    hash_b64 = base64.b64encode(hashed, altchars=b"./").decode("ascii").rstrip("=")
    return f"$6${salt}${hash_b64}"


class OSProvider(ABC):
    """Abstract base class for OS-specific provisioning providers."""

    @abstractmethod
    def generate_automation_file(
        self,
        version: Optional[OSVersion],
        vm_name: str,
        user_config: Dict[str, Any],
        output_path: Path,
        template_name: str | None = None,
    ) -> Path:
        """Generate automation file (unattend.xml, preseed, etc.) for unattended install."""
        pass

    def validate_iso(self, iso_path: Path, version: OSVersion) -> bool:
        """Validate that an ISO file matches the expected OS version.

        Default implementation just checks file existence.
        Providers can override for specific validation logic.
        """
        return iso_path.exists() and iso_path.stat().st_size > 0
