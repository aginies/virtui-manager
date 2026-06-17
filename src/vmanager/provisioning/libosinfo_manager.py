"""
Libosinfo Manager for VirtUI Manager.

This module provides a centralized catalog of operating systems and ISO images
by querying the Libosinfo database. It provides a "safe list" of recommended
distributions similar to GNOME Boxes.
"""

import logging
from typing import Any, Dict, List, Optional

from .os_provider import OSType, OSVersion, get_osinfo_db


class LibosinfoManager:
    """Manages OS discovery and ISO retrieval using libosinfo."""

    def __init__(self, host_arch: str = "x86_64"):
        self.logger = logging.getLogger(__name__)
        self.host_arch = host_arch
        self.db = get_osinfo_db()

    def get_os_catalog(self) -> List[OSVersion]:
        """
        Returns a list of OS versions that have valid media URLs.
        Filters for stable, non-EOL distributions where possible.
        """
        if not self.db:
            self.logger.warning("Libosinfo database not available.")
            return []

        os_list = self.db.get_os_list()
        catalog = []
        seen_ids = set()

        for i in range(os_list.get_length()):
            os_info = os_list.get_nth(i)
            short_id = os_info.get_short_id()
            
            # Skip duplicates and unknown versions
            version_num = os_info.get_version()
            if not version_num or version_num.lower() == "unknown" or short_id in seen_ids:
                continue

            # Check for media with URLs
            media_list = os_info.get_media_list()
            has_media_url = False
            for j in range(media_list.get_length()):
                media = media_list.get_nth(j)
                if media.get_url() and media.get_architecture() == self.host_arch:
                    has_media_url = True
                    break
            
            if not has_media_url:
                continue

            # Determine OSType
            os_type = self._map_to_os_type(os_info)
            
            # Get EOL date
            eol_date = None
            try:
                eol_date = os_info.get_eol_date_string()
            except Exception:
                pass

            catalog.append(
                OSVersion(
                    os_type=os_type,
                    version_id=version_num,
                    display_name=os_info.get_name(),
                    architecture=self.host_arch,
                    eol_date=eol_date
                )
            )
            seen_ids.add(short_id)

        # Sort: Primary sort by distribution (alphabetical), secondary sort by version (latest first)
        import re
        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)]

        # Secondary sort: version ID descending
        catalog.sort(key=lambda v: natural_sort_key(v.version_id), reverse=True)
        # Primary sort: distribution name ascending
        return sorted(catalog, key=lambda v: v.os_type.value.lower())

    def get_iso_list(self, version: OSVersion) -> List[Dict[str, Any]]:
        """Returns a list of ISO images for a specific OS version from libosinfo."""
        if not self.db:
            return []

        # Find the OS in the DB
        os_list = self.db.get_os_list()
        os_info = None
        for i in range(os_list.get_length()):
            info = os_list.get_nth(i)
            if info.get_name() == version.display_name and info.get_version() == version.version_id:
                os_info = info
                break
        
        if not os_info:
            return []

        results = []
        media_list = os_info.get_media_list()
        for i in range(media_list.get_length()):
            media = media_list.get_nth(i)
            url = media.get_url()
            arch = media.get_architecture()
            
            if url and (not arch or arch == self.host_arch):
                # Extract filename from URL
                filename = url.split("/")[-1]
                results.append({
                    "name": f"{version.display_name} ({arch}) - {filename}",
                    "url": url,
                    "arch": arch,
                    "size": "Unknown", # Libosinfo doesn't reliably provide size
                    "date": ""         # Nor last-modified date
                })
        
        return sorted(results, key=lambda x: x["name"])

    def _map_to_os_type(self, os_info) -> OSType:
        """Maps libosinfo OS metadata to VirtUI Manager OSType."""
        short_id = os_info.get_short_id().lower()
        distro = ""
        try:
            distro = os_info.get_distro().lower()
        except Exception:
            pass

        if "fedora" in short_id or "fedora" in distro:
            return OSType.FEDORA
        if "ubuntu" in short_id or "ubuntu" in distro:
            return OSType.UBUNTU
        if "debian" in short_id or "debian" in distro:
            return OSType.DEBIAN
        if "opensuse" in short_id or "opensuse" in distro:
            return OSType.OPENSUSE
        if "arch" in short_id or "arch" in distro:
            return OSType.ARCHLINUX
        if "alpine" in short_id or "alpine" in distro:
            return OSType.ALPINE
        if "win" in short_id:
            return OSType.WINDOWS
        
        return OSType.LINUX
