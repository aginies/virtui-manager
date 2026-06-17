"""
OS Provider Interface for Multi-OS VM Provisioning

This module defines the base interface that all OS providers must implement,
along with common data structures for OS types and versions.
"""

import base64
import hashlib
import logging
import os
import re
import secrets
import ssl
import subprocess
import string
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import gi

try:
    gi.require_version("Libosinfo", "1.0")
    from gi.repository import Libosinfo
except (ImportError, ValueError) as e:
    logging.error(f"Libosinfo not found. Dynamic OS version discovery will be disabled: {e}")
    Libosinfo = None


class OSType(Enum):
    """Supported operating system types."""

    LINUX = "Linux"
    OPENSUSE = "openSUSE"
    WINDOWS = "Windows"
    UBUNTU = "Ubuntu"
    DEBIAN = "Debian"
    FEDORA = "Fedora"
    ARCHLINUX = "Arch Linux"
    ALPINE = "Alpine Linux"
    GENERIC = "Generic / Custom ISO"


@dataclass(frozen=True)
class OSVersion:
    """Represents a specific version of an operating system."""

    os_type: OSType
    version_id: str  # e.g., "11", "22.04", "15.5"
    display_name: str  # e.g., "Windows 11", "Ubuntu 22.04 LTS"
    architecture: str = "x86_64"  # Default to x86_64
    is_evaluation: bool = False  # True for evaluation/trial versions
    eol_date: Optional[str] = None  # End of Life date (if available)

    def __str__(self) -> str:
        return self.display_name


@dataclass
class AutomationConfig:
    """Configuration for unattended installation."""

    template_name: str
    variables: Dict[str, Any]
    supports_custom_user: bool = True
    supports_network_config: bool = True


_osinfo_db = None


def get_osinfo_db() -> Optional["Libosinfo.Db"]:
    """Load and cache the Libosinfo database."""
    global _osinfo_db
    if Libosinfo is None:
        return None

    if _osinfo_db is None:
        try:
            loader = Libosinfo.Loader()
            loader.process_default_path()
            _osinfo_db = loader.get_db()
        except Exception as e:
            logging.error(f"Failed to load Libosinfo database: {e}")
            return None
    return _osinfo_db


def hash_password(plaintext_password: str) -> str:
    """
    Hash a plaintext password using SHA-512 crypt format.

    This is suitable for use in AutoYaST, Agama, Ubuntu autoinstall, and other
    Linux automation systems. Uses system tools (mkpasswd, openssl) or the
    crypt module (if available) before falling back to a Python implementation.

    Args:
        plaintext_password: The password to hash

    Returns:
        SHA-512 hashed password string in crypt format ($6$salt$hash)
    """
    # Try using mkpasswd first (most reliable and portable)
    try:
        result = subprocess.run(
            ["mkpasswd", "-m", "sha-512", "-s"],
            input=plaintext_password,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            hashed = result.stdout.strip()
            logging.info(f"Password hashed using mkpasswd (SHA-512)")
            return hashed
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logging.debug(f"mkpasswd not available: {e}")
        pass

    # Try using openssl as fallback
    try:
        result = subprocess.run(
            ["openssl", "passwd", "-6", "-stdin"],
            input=plaintext_password,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            hashed = result.stdout.strip()
            logging.info(f"Password hashed using openssl (SHA-512)")
            return hashed
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logging.debug(f"openssl not available: {e}")
        pass

    # Try using crypt module (deprecated in Python 3.11, removed in 3.13)
    try:
        import crypt

        hashed = crypt.crypt(plaintext_password, crypt.mksalt(crypt.METHOD_SHA512))
        logging.info(f"Password hashed using Python crypt module (SHA-512)")
        return hashed
    except (ImportError, AttributeError, ValueError) as e:
        logging.debug(f"crypt module not available or failed: {e}")
        pass

    # Final fallback: Use Python's crypt with manual salt (if available at all)
    # This is a last resort and may not be available on all systems
    try:
        import crypt

        # Create a SHA-512 salt manually
        salt_chars = string.ascii_letters + string.digits + "./"
        salt = "$6$" + "".join(secrets.choice(salt_chars) for _ in range(16))
        hashed = crypt.crypt(plaintext_password, salt)
        logging.info(f"Password hashed using Python crypt with manual salt (SHA-512)")
        return hashed
    except (ImportError, AttributeError, ValueError) as e:
        logging.debug(f"crypt module fallback failed: {e}")

    # If all else fails, raise an error with helpful information
    error_msg = (
        "Unable to hash password: system password hashing tools (mkpasswd, openssl) not found "
        "and crypt module unavailable. Please install 'whois' package (contains mkpasswd) "
        "or ensure 'openssl' is available on the system."
    )
    logging.error(error_msg)
    raise RuntimeError(error_msg)
