"""
Module for managing firmware-related information and operations.
"""

import json
import logging
import os
import xml.etree.ElementTree as ET

import libvirt

from .libvirt_utils import get_host_domain_capabilities, get_domain_capabilities_xml
from .utils import log_function_call

FIRMWARE_META_BASE_DIR = "/usr/share/qemu/firmware/"

# Cache for firmware list to avoid repeated calls
_firmware_cache = {}


class Firmware:
    """
    firmware class
    """

    def __init__(self):
        """
        Set default values
        """
        self.executable = None
        self.nvram_template = None
        self.architectures = []
        self.features = []
        self.interfaces = []

    def load_from_json(self, jsondata):
        """
        Initialize object from a json firmware description
        """
        if "interface-types" in jsondata:
            self.interfaces = jsondata["interface-types"]
        else:
            return False

        if "mapping" in jsondata:
            if (
                "executable" in jsondata["mapping"]
                and "filename" in jsondata["mapping"]["executable"]
            ):
                self.executable = jsondata["mapping"]["executable"]["filename"]
            elif "filename" in jsondata["mapping"]:
                self.executable = jsondata["mapping"]["filename"]
            if (
                "nvram-template" in jsondata["mapping"]
                and "filename" in jsondata["mapping"]["nvram-template"]
            ):
                self.nvram_template = jsondata["mapping"]["nvram-template"]["filename"]

        if self.executable is None:
            return False

        if "features" in jsondata:
            for feat in jsondata["features"]:
                self.features.append(feat)

        if "targets" in jsondata:
            for target in jsondata["targets"]:
                self.architectures.append(target["architecture"])

        if not self.architectures:
            return False

        return True


@log_function_call
def get_uefi_files(conn: libvirt.virConnect | None = None, use_cache: bool = True):
    """
    Retrieves available UEFI firmware configurations from the hypervisor via libvirt.

    When a connection is provided, retrieves firmware from the remote/local hypervisor
    using libvirt's domain capabilities and reads firmware JSON metadata files.
    Falls back to local filesystem access if connection is None.

    Args:
        conn: libvirt connection object. If None, reads from local filesystem.
        use_cache: If True, use cached results when available.

    Returns:
        List of Firmware objects with available configurations.
    """
    # Generate cache key based on connection
    cache_key = "local" if conn is None else "remote"

    # Check cache first
    if use_cache and cache_key in _firmware_cache:
        logging.debug(f"Using cached firmware list for {cache_key} system")
        return _firmware_cache[cache_key]

    uefi_files = []

    if conn:
        # Use libvirt to retrieve firmware info from the host
        try:
            # Try to get domain capabilities for x86_64 first (most common)
            # This works for both local and remote systems
            caps_xml = get_domain_capabilities_xml(
                conn=conn, emulatorbin=None, arch="x86_64", machine="pc", flags=0
            )

            if not caps_xml:
                logging.warning("Could not get domain capabilities from libvirt")
                # Fall back to local filesystem if no capabilities available
                _load_firmware_from_files(uefi_files)
                _firmware_cache[cache_key] = uefi_files
                return uefi_files

            root = ET.fromstring(caps_xml)

            # Extract loader values from domain capabilities
            loader_values = []
            loader_elem = root.find(".//os/loader")
            if loader_elem is not None:
                for value_elem in loader_elem.findall("value"):
                    if value_elem.text:
                        loader_values.append(value_elem.text.strip())

            if not loader_values:
                logging.warning("No loader values found in domain capabilities")
                # Fall back to local filesystem if no loaders found
                _load_firmware_from_files(uefi_files)
                _firmware_cache[cache_key] = uefi_files
                return uefi_files

            # For remote systems, we need to read the firmware JSON metadata
            # Try to read from the firmware directory if it's accessible
            # This might require the directory to be NFS-mounted or similar for remote hosts
            try:
                _load_firmware_from_files(uefi_files)
                if uefi_files:
                    # Successfully loaded metadata
                    _firmware_cache[cache_key] = uefi_files
                    return uefi_files
            except (OSError, IOError) as e:
                logging.warning(f"Could not read firmware JSON files: {e}")

            # Fallback: create Firmware objects from loader values alone
            # This provides basic firmware info even if metadata is unavailable
            logging.info("Using fallback firmware creation from loader values")
            for loader_path in loader_values:
                firmware = Firmware()
                firmware.executable = loader_path

                # Infer architecture from path (default to x86_64)
                if "aarch64" in loader_path.lower() or "arm64" in loader_path.lower():
                    firmware.architectures = ["aarch64"]
                else:
                    firmware.architectures = ["x86_64"]

                # Infer interface type from path
                if "bios" in loader_path.lower():
                    firmware.interfaces = ["bios"]
                else:
                    firmware.interfaces = ["uefi"]

                # Try to infer features from the path
                if "secboot" in loader_path.lower() or "secure" in loader_path.lower():
                    firmware.features = ["secure-boot"]
                if "sev" in loader_path.lower():
                    if "sev-es" in loader_path.lower() or "snp" in loader_path.lower():
                        firmware.features.append("amd-sev-es")
                    else:
                        firmware.features.append("amd-sev")

                uefi_files.append(firmware)

        except libvirt.libvirtError as e:
            logging.error(f"Error retrieving firmware via libvirt: {e}")
            # Fall back to local filesystem
            _load_firmware_from_files(uefi_files)
    else:
        # Original behavior: read from local filesystem
        _load_firmware_from_files(uefi_files)

    # Cache the result
    _firmware_cache[cache_key] = uefi_files
    return uefi_files


def clear_firmware_cache(cache_key: str | None = None):
    """
    Clear the firmware cache. Useful when the system firmware might have changed.

    Args:
        cache_key: Specific cache key to clear ("local" or "remote").
                  If None, clears all cache entries.
    """
    global _firmware_cache
    if cache_key is None:
        logging.debug("Clearing all firmware cache")
        _firmware_cache.clear()
    elif cache_key in _firmware_cache:
        logging.debug(f"Clearing firmware cache for {cache_key}")
        del _firmware_cache[cache_key]


def _load_firmware_from_files(uefi_files: list):
    """
    Load firmware information from JSON metadata files.

    Args:
        uefi_files: List to append Firmware objects to.
    """
    if not os.path.isdir(FIRMWARE_META_BASE_DIR):
        return

    for file in os.listdir(FIRMWARE_META_BASE_DIR):
        if file.endswith(".json"):
            full_path = os.path.join(FIRMWARE_META_BASE_DIR, file)
            try:
                with open(full_path, encoding="utf-8") as f:
                    jsondata = json.load(f)

                firmware = Firmware()
                if firmware.load_from_json(jsondata):
                    uefi_files.append(firmware)
            except (OSError, json.JSONDecodeError):
                # ignore malformed or unreadable files
                continue


@log_function_call
def get_host_sev_capabilities(conn):
    """
    Checks if the host supports AMD SEV and SEV-ES.
    """
    sev_caps = {"sev": False, "sev-es": False}
    if conn is None:
        return sev_caps
    try:
        caps_xml = get_host_domain_capabilities(conn)
        if not caps_xml:
            return sev_caps
        root = ET.fromstring(caps_xml)
        sev_elem = root.find(".//host/cpu/sev")
        if sev_elem is not None:
            sev_caps["sev"] = True

        guest_arch = root.find(".//guest/arch[@name='x86_64']")
        if guest_arch is not None:
            features = guest_arch.find("features")
            if features is not None:
                if features.find("sev-es") is not None:
                    sev_caps["sev-es"] = True
    except (libvirt.libvirtError, ET.ParseError):
        pass
    return sev_caps
