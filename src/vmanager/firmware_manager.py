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
        self.machines = []
        self.description = ""
        self.tags = []
        self.device = None

    def load_from_json(self, jsondata):
        """
        Initialize object from a json firmware description
        """
        if "interface-types" in jsondata:
            self.interfaces = jsondata["interface-types"]
        else:
            return False

        if "mapping" in jsondata:
            # Extract device type (e.g., "flash" for pflash devices)
            if "device" in jsondata["mapping"]:
                self.device = jsondata["mapping"]["device"]
                logging.debug(f"Firmware device type: {self.device}")

            if (
                "executable" in jsondata["mapping"]
                and "filename" in jsondata["mapping"]["executable"]
            ):
                self.executable = jsondata["mapping"]["executable"]["filename"]
                logging.debug(f"Firmware executable: {self.executable}")
            elif "filename" in jsondata["mapping"]:
                self.executable = jsondata["mapping"]["filename"]
                logging.debug(f"Firmware executable (alt): {self.executable}")
            if (
                "nvram-template" in jsondata["mapping"]
                and "filename" in jsondata["mapping"]["nvram-template"]
            ):
                self.nvram_template = jsondata["mapping"]["nvram-template"]["filename"]
                logging.debug(f"Firmware NVRAM: {self.nvram_template}")

        if self.executable is None:
            logging.debug("Firmware rejected: no executable")
            return False

        if "features" in jsondata:
            for feat in jsondata["features"]:
                self.features.append(feat)
        if "targets" in jsondata:
            for target in jsondata["targets"]:
                self.architectures.append(target["architecture"])
                if "machines" in target:
                    self.machines.extend(target["machines"])
                logging.debug(
                    f"Firmware architectures: {self.architectures}, machines: {self.machines}"
                )

        if not self.architectures:
            logging.debug("Firmware rejected: no architectures")
            return False

        if "description" in jsondata:
            self.description = jsondata["description"]

        if "tags" in jsondata:
            self.tags = jsondata.get("tags", [])

        logging.debug(f"Firmware loaded successfully: {self.executable}")
        return True

    def __repr__(self):
        return f"<Firmware(executable='{self.executable}', archs={self.architectures})>"



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

                # Infer NVRAM template path from loader path
                # Try common patterns like substituting "code" with "vars"
                nvram_guess = None
                if "code" in loader_path:
                    nvram_guess = loader_path.replace("code", "vars")
                elif "CODE" in loader_path:
                    nvram_guess = loader_path.replace("CODE", "VARS")
                elif "Code" in loader_path:
                    nvram_guess = loader_path.replace("Code", "Vars")

                # We can't verify existence easily on remote host, but we can assign it
                # if the pattern matched.
                if nvram_guess and nvram_guess != loader_path:
                    firmware.nvram_template = nvram_guess

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
        logging.debug(f"Firmware directory not found: {FIRMWARE_META_BASE_DIR}")
        return

    files = os.listdir(FIRMWARE_META_BASE_DIR)
    json_files = [f for f in files if f.endswith(".json")]
    logging.info(
        f"Loading firmware from JSON metadata: found {len(json_files)} JSON files in {FIRMWARE_META_BASE_DIR}"
    )

    loaded = 0
    rejected = 0

    for file in json_files:
        full_path = os.path.join(FIRMWARE_META_BASE_DIR, file)
        try:
            with open(full_path, encoding="utf-8") as f:
                jsondata = json.load(f)

            firmware = Firmware()
            if firmware.load_from_json(jsondata):
                uefi_files.append(firmware)
                has_nvram = "✓" if firmware.nvram_template else "✗"
                logging.debug(
                    f"Loaded firmware from {file}: {firmware.executable} ({has_nvram} NVRAM)"
                )
                loaded += 1
            else:
                logging.debug(f"Firmware rejected from {file}: load_from_json returned False")
                rejected += 1
        except (OSError, json.JSONDecodeError) as e:
            # ignore malformed or unreadable files
            logging.debug(f"Failed to load firmware JSON from {file}: {e}")
            rejected += 1
            continue

    logging.info(f"Firmware loading from JSON complete: {loaded} loaded, {rejected} rejected")

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


@log_function_call
def select_best_firmware(
    firmwares: list,
    architecture: str = "x86_64",
    machine_type: str | None = None,
    secure_boot: bool = False,
    prefer_nvram: bool = True,
) -> Firmware | None:
    """
    Intelligently selects the best firmware from available options.
    Implements a scoring system similar to virt-install.

    This function:
    1. Filters firmware compatible with the target architecture
    2. Optionally filters for specific machine types
    3. Scores firmware based on requested features
    4. Returns the firmware with the highest score

    Args:
        firmwares: List of Firmware objects to choose from
        architecture: Target architecture (e.g., 'x86_64', 'aarch64')
        machine_type: Specific machine type pattern (e.g., 'pc-q35-*', 'pc-i440fx-*')
                     If None, any compatible machine is acceptable
        secure_boot: If True, require secure-boot capable firmware
        prefer_nvram: If True, prefer firmware with NVRAM template over loader-only

    Returns:
        Selected Firmware object, or None if no suitable firmware found
    """
    if not firmwares:
        return None

    # Filter by architecture first
    compatible_fw = [fw for fw in firmwares if architecture in fw.architectures]
    if not compatible_fw:
        logging.warning(
            f"No firmware found for architecture {architecture}. "
            f"Available: {[fw.architectures for fw in firmwares]}"
        )
        return None
    # Filter by machine type if specified
    if machine_type:
        machine_compatible = []
        for fw in compatible_fw:
            if not fw.machines:
                # If firmware has no machine restriction, it's compatible
                machine_compatible.append(fw)
            else:
                # Check if any machine pattern matches
                for fw_machine in fw.machines:
                    if _match_machine_pattern(fw_machine, machine_type):
                        machine_compatible.append(fw)
                        break

        if machine_compatible:
            compatible_fw = machine_compatible
        else:
            logging.debug(
                f"No firmware found for machine type {machine_type}. "
                "Using architecture-compatible firmware anyway."
            )

    # Filter by secure-boot requirement
    if secure_boot:
        secure_fw = [fw for fw in compatible_fw if "secure-boot" in fw.features]
        if secure_fw:
            compatible_fw = secure_fw
        else:
            logging.warning(
                "No secure-boot capable firmware found. Returning non-secure firmware as fallback."
            )
    # Score remaining firmware
    best_fw = None
    best_score = -1

    for fw in compatible_fw:
        score = _score_firmware(fw, secure_boot, prefer_nvram)

        logging.debug(
            f"Firmware {fw.executable}: score={score}, "
            f"features={fw.features}, has_nvram={fw.nvram_template is not None}"
        )

        if score > best_score:
            best_score = score
            best_fw = fw

    if best_fw:
        logging.info(
            f"Selected firmware: {best_fw.executable} (score={best_score}, arch={architecture})"
        )
    else:
        logging.error("No suitable firmware found after scoring")

    return best_fw


def _match_machine_pattern(fw_pattern: str, machine_type: str) -> bool:
    """
    Check if a machine type matches a firmware machine pattern.
    Supports wildcard matching (e.g., 'pc-q35-*' matches 'pc-q35-2.12').

    Args:
        fw_pattern: Pattern from firmware metadata (e.g., 'pc-q35-*')
        machine_type: Actual machine type (e.g., 'pc-q35-2.12')

    Returns:
        True if machine_type matches the pattern
    """
    import fnmatch

    return fnmatch.fnmatch(machine_type, fw_pattern)
def _score_firmware(
    firmware: Firmware, secure_boot_required: bool = False, prefer_nvram: bool = True
) -> int:
    """
    Score a firmware based on desired characteristics.
    Higher score means better match. Similar to virt-install's selection logic.

    Args:
        firmware: Firmware object to score
        secure_boot_required: If True, score secure-boot capable firmware higher
        prefer_nvram: If True, score firmware with NVRAM higher

    Returns:
        Numerical score
    """
    score = 0

    # Base score for having required components
    if firmware.executable:
        score += 10
    if firmware.nvram_template:
        score += 20

    # Prioritize nvram availability if preferred
    if prefer_nvram and firmware.nvram_template:
        score += 100

    # Score based on features
    feature_scores = {
        "acpi-s3": 2,  # Modern systems support S3
        "acpi-s4": 2,  # Modern systems support S4
        "requires-smm": 10,  # Secure boot requires SMM
        "secure-boot": 50 if secure_boot_required else 5,  # High value for secure boot
        "verbose-dynamic": 5,  # Debug capability
        "amd-sev": 50,  # SEV capability (valuable but platform-specific)
        "amd-sev-es": 60,  # SEV-ES is more advanced
        "intel-tdx": 60,  # TDX capability (valuable but platform-specific)
        "enrolled-keys": 3,  # Pre-enrolled keys
    }

    for feature in firmware.features:
        score += feature_scores.get(feature, 1)
    # Prefer certain firmware patterns based on naming
    # (These are heuristics based on common OVMF naming conventions)
    if firmware.executable:
        exec_lower = firmware.executable.lower()
        if "ovmf" in exec_lower:
            score += 5
        if "code" in exec_lower:
            score += 3  # Indicates code/vars pair (good for pflash)
        if "vars" in exec_lower:
            score -= 20  # Should not be code loader

    return score


@log_function_call
def generate_firmware_debug_report(conn: libvirt.virConnect | None = None) -> str:
    """
    Generates a detailed debug report of all available firmware options.
    Useful for troubleshooting firmware selection issues.

    Args:
        conn: libvirt connection object (optional)

    Returns:
        Formatted debug report as string
    """
    firmwares = get_uefi_files(conn, use_cache=False)

    report = []
    report.append("=" * 80)
    report.append("FIRMWARE DEBUG REPORT")
    report.append("=" * 80)
    report.append(f"Total firmware options: {len(firmwares)}\n")
    for i, fw in enumerate(firmwares, 1):
        report.append(f"[{i}] {fw.executable}")
        report.append(f"    Description: {fw.description}")
        report.append(f"    Device: {fw.device}")
        report.append(f"    Architectures: {fw.architectures}")
        report.append(f"    Machines: {fw.machines if fw.machines else 'Any'}")
        report.append(f"    Interfaces: {fw.interfaces}")
        report.append(f"    Features: {fw.features}")
        report.append(f"    NVRAM Template: {fw.nvram_template if fw.nvram_template else 'None'}")
        report.append("")

    # Summary by architecture
    report.append("\nSUMMARY BY ARCHITECTURE:")
    report.append("-" * 80)
    architectures = {}
    for fw in firmwares:
        for arch in fw.architectures:
            if arch not in architectures:
                architectures[arch] = []
            architectures[arch].append(fw)

    for arch in sorted(architectures.keys()):
        arch_fw = architectures[arch]
        with_nvram = sum(1 for fw in arch_fw if fw.nvram_template)
        with_flash = sum(1 for fw in arch_fw if fw.device == "flash")
        secure_boot = sum(1 for fw in arch_fw if "secure-boot" in fw.features)
        report.append(
            f"{arch:15} : {len(arch_fw):2} total | "
            f"{with_nvram:2} with NVRAM | {with_flash:2} with flash | {secure_boot:2} secure-boot"
        )

    report.append("\n" + "=" * 80)

    return "\n".join(report)

