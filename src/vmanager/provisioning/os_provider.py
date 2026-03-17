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
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


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

    @property
    def preferred_boot_uefi(self) -> bool:
        """Return the preferred boot mode (UEFI vs BIOS).
        
        Default is True (UEFI).
        """
        return True

    def validate_iso(self, iso_path: Path, version: OSVersion) -> bool:
        """Validate that an ISO file matches the expected OS version.

        Default implementation just checks file existence.
        Providers can override for specific validation logic.
        """
        return iso_path.exists() and iso_path.stat().st_size > 0

    def _get_iso_details(self, url: str, arch: Optional[str] = None) -> Dict[str, Any]:
        """Fetch details (Size, Last-Modified) for a given ISO URL."""
        name = url.split("/")[-1].lstrip("./")
        size_str = "Unknown"
        date_str = ""

        try:
            # Use unverified context to be more compatible with various mirrors
            context = ssl._create_unverified_context()
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, context=context, timeout=5) as response:
                # Size
                content_length = response.getheader("Content-Length")
                if content_length:
                    size_mb = int(content_length) // (1024 * 1024)
                    size_str = f"{size_mb} MB"

                # Date
                last_modified = response.getheader("Last-Modified")
                if last_modified:
                    try:
                        dt = parsedate_to_datetime(last_modified)
                        date_str = dt.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        date_str = last_modified
        except Exception:
            pass

        return {"name": name, "url": url, "size": size_str, "date": date_str, "arch": arch}

    def _get_local_iso_list(self, path: str, arch: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists ISO files from a local directory."""
        if path.startswith("file://"):
            path = path[7:]

        results = []
        try:
            path_obj = Path(path)
            if not path_obj.exists() or not path_obj.is_dir():
                return []

            for f in path_obj.glob("*.iso"):
                try:
                    stats = f.stat()
                    dt_str = datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M")
                    results.append(
                        {"name": f.name, "url": str(f.absolute()), "date": dt_str, "arch": arch}
                    )
                except Exception:
                    continue

            results.sort(key=lambda x: x["name"], reverse=True)
        except Exception:
            pass

        return results

    def get_iso_list_from_url(
        self,
        url: str,
        name_prefix: str = "",
        arch: Optional[str] = None,
        filter_pattern: str = r'href="([^"]*\.iso)"',
    ) -> List[Dict[str, Any]]:
        """
        Generic method to fetch ISO list from a directory listing URL or local path.

        Args:
            url: The URL or path to scrape for .iso links
            name_prefix: Prefix to add to ISO names
            arch: Architecture string to include in results
            filter_pattern: Regex pattern to find ISO links in HTML

        Returns:
            List of ISO dictionaries
        """
        if url.startswith("/") or url.startswith("file://") or os.path.isdir(url):
            results = self._get_local_iso_list(url, arch=arch)
            if name_prefix:
                for res in results:
                    res["name"] = f"{name_prefix}{res['name']}"
            return results

        logging.info(f"Fetching ISO list from {url}")

        try:
            # Use unverified context for compatibility
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(url, context=context, timeout=10) as response:
                content = response.read().decode("utf-8")

            # Parse HTML to find ISO files
            links = re.findall(filter_pattern, content)
            unique_urls = []
            for link in links:
                clean_link = link.lstrip("./")
                if link.startswith("http"):
                    full_url = link
                else:
                    base_url = url if url.endswith("/") else url + "/"
                    full_url = base_url + clean_link

                unique_urls.append(full_url)

            unique_urls = sorted(list(set(unique_urls)), reverse=True)

            # Fetch details in parallel for performance
            results = []
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(self._get_iso_details, u, arch) for u in unique_urls]
                for future in futures:
                    try:
                        res = future.result()
                        if name_prefix:
                            res["name"] = f"{name_prefix}{res['name']}"
                        results.append(res)
                    except Exception:
                        continue

            results.sort(key=lambda x: x["name"], reverse=True)
            return results

        except Exception as e:
            logging.error(f"Error fetching ISO list from {url}: {e}")
            return []
