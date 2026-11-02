"""
Module for managing firmware-related information and operations.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import subprocess
import xml.etree.ElementTree as ET

import libvirt

from .libvirt_utils import get_host_domain_capabilities
from .utils import get_ssh_host_from_uri, log_function_call, run_command, sanitize_credentials

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
                logging.debug("Firmware device type: %s", self.device)

            if (
                "executable" in jsondata["mapping"]
                and "filename" in jsondata["mapping"]["executable"]
            ):
                self.executable = jsondata["mapping"]["executable"]["filename"]
                logging.debug("Firmware executable: %s", self.executable)
            elif "filename" in jsondata["mapping"]:
                self.executable = jsondata["mapping"]["filename"]
                logging.debug("Firmware executable (alt): %s", self.executable)
            if (
                "nvram-template" in jsondata["mapping"]
                and "filename" in jsondata["mapping"]["nvram-template"]
            ):
                self.nvram_template = jsondata["mapping"]["nvram-template"]["filename"]
                logging.debug("Firmware NVRAM: %s", self.nvram_template)

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
                    "Firmware architectures: %s, machines: %s", self.architectures, self.machines
                )

        if not self.architectures:
            logging.debug("Firmware rejected: no architectures")
            return False

        if "description" in jsondata:
            self.description = jsondata["description"]

        if "tags" in jsondata:
            self.tags = jsondata.get("tags", [])

        logging.debug("Firmware loaded successfully: %s", self.executable)
        return True

    def __repr__(self):
        return f"<Firmware(executable='{self.executable}', archs={self.architectures})>"


def get_uefi_files(conn: libvirt.virConnect | None = None, use_cache: bool = True):
    """
    Retrieves available UEFI firmware configurations from the hypervisor.

    Firmware metadata is read from the QEMU firmware JSON files
    (``/usr/share/qemu/firmware/*.json``), which enumerate every supported
    architecture, so all architectures (x86_64, aarch64, ...) are discovered.

    - Local connection (``conn`` is None): read the JSON files from the local
      filesystem.
    - Remote ``qemu+ssh://`` connection: SSH into the hypervisor and read the
      same JSON files there, so remote hosts get the same rich metadata
      (NVRAM templates, secure-boot/SEV features, all architectures).

    Args:
        conn: libvirt connection object. If None, reads from local filesystem.
        use_cache: If True, use cached results when available.

    Returns:
        List of Firmware objects with available configurations.
    """
    # Compute the SSH host once (used for both the cache key and the remote read).
    ssh_host = get_ssh_host_from_uri(conn.getURI()) if conn is not None else None

    # Generate cache key based on connection. For remote connections, key by the
    # sanitized SSH host so each remote host has its own cache entry (firmware
    # metadata is genuinely host-specific).
    if conn is None:
        cache_key = "local"
    elif ssh_host:
        cache_key = f"remote:{sanitize_credentials(ssh_host)}"
    else:
        cache_key = "remote"

    # Check cache first
    if use_cache and cache_key in _firmware_cache:
        logging.debug("Using cached firmware list for %s system", cache_key)
        return _firmware_cache[cache_key]

    uefi_files = []

    if conn is None:
        # Case 1: local connection, read local firmware JSON metadata files.
        logging.debug("Reading local firmware JSON metadata files")
        _load_firmware_from_files(uefi_files)
    elif ssh_host:
        # Case 2: remote connection. Read the firmware JSON metadata from the
        # hypervisor over SSH so we get the full per-architecture metadata.
        logging.debug("Reading remote firmware JSON metadata files from %s", ssh_host)
        json_map = _fetch_remote_firmware_files(ssh_host)
        _load_firmware_from_json_map(json_map, uefi_files)
    else:
        # Defensive fallback: unsupported connection type without an SSH
        # host. Read local JSON as a best-effort source.
        logging.warning(
            "No SSH host available for %s; falling back to local firmware JSON files",
            sanitize_credentials(conn.getURI()),
        )
        _load_firmware_from_files(uefi_files)

    # Cache the result
    _firmware_cache[cache_key] = uefi_files
    return uefi_files


def _fetch_remote_firmware_files(ssh_host: str) -> dict:
    """
    Read firmware JSON metadata files from a remote host over SSH.

    Args:
        ssh_host: SSH host string (user@host).

    Returns:
        Dictionary mapping firmware JSON filename to its text content.
        Empty dict if the directory is missing, unreadable, or SSH fails.
    """
    json_map: dict = {}

    # A single remote shell command lists and reads every firmware JSON file.
    # The remote shell performs the glob expansion, so no filename is ever
    # interpolated into the command string (safe against special characters).
    # Files are separated by an ASCII record separator (\x1e) that cannot appear
    # in JSON text. The command is passed as a single argument so ssh sends it
    # to the remote shell intact.
    remote_cmd = (
        "for f in "
        f"{FIRMWARE_META_BASE_DIR}*.json; do "
        '[ -f "$f" ] || continue; '
        'printf "\\036%s\\n" "$(basename "$f")"; '
        'cat "$f"; '
        'printf "\\n"; '
        "done"
    )

    try:
        result = run_command(
            [remote_cmd],
            remote_host=ssh_host,
            check=False,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logging.warning("Could not read firmware JSON files on %s: %s", ssh_host, e)
        return json_map

    if result.returncode != 0:
        logging.warning(
            "Could not read firmware JSON files on %s (rc=%s)",
            ssh_host,
            result.returncode,
        )
        return json_map

    # Split the combined output on the record separator. The first chunk is
    # empty (before the first separator); each remaining chunk is
    # "<filename>\n<content>\n".
    for chunk in result.stdout.split("\x1e"):
        if not chunk:
            continue
        nl = chunk.find("\n")
        if nl == -1:
            continue
        filename = chunk[:nl]
        content = chunk[nl + 1 :]
        if filename and content.strip():
            json_map[filename] = content

    return json_map


def _load_firmware_from_json_map(json_map: dict, uefi_files: list) -> tuple:
    """
    Parse firmware JSON text into Firmware objects.

    Args:
        json_map: Mapping of firmware JSON filename to its text content.
        uefi_files: List to append Firmware objects to.

    Returns:
        Tuple of (loaded, rejected) counts.
    """
    loaded = 0
    rejected = 0

    for filename, json_text in json_map.items():
        try:
            jsondata = json.loads(json_text)
            firmware = Firmware()
            if firmware.load_from_json(jsondata):
                uefi_files.append(firmware)
                has_nvram = "✓" if firmware.nvram_template else "✗"
                logging.debug(
                    "Loaded firmware from %s: %s (%s NVRAM)",
                    filename,
                    firmware.executable,
                    has_nvram,
                )
                loaded += 1
            else:
                logging.debug("Firmware rejected from %s: load_from_json returned False", filename)
                rejected += 1
        except (json.JSONDecodeError, ValueError) as e:
            logging.debug("Failed to parse firmware JSON from %s: %s", filename, e)
            rejected += 1
            continue

    logging.info(
        "Firmware loading complete: %s loaded, %s rejected",
        loaded,
        rejected,
    )
    return loaded, rejected


def clear_firmware_cache(cache_key: str | None = None):
    """
    Clear the firmware cache. Useful when the system firmware might have changed.

    Args:
        cache_key: Specific cache key to clear ("local" or "remote").
                  If None, clears all cache entries.
    """
    if cache_key is None:
        logging.debug("Clearing all firmware cache")
        _firmware_cache.clear()
    elif cache_key in _firmware_cache:
        logging.debug("Clearing firmware cache for %s", cache_key)
        del _firmware_cache[cache_key]


def _load_firmware_from_files(uefi_files: list):
    """
    Load firmware information from local JSON metadata files.

    Args:
        uefi_files: List to append Firmware objects to.
    """
    if not os.path.isdir(FIRMWARE_META_BASE_DIR):
        logging.debug("Firmware directory not found: %s", FIRMWARE_META_BASE_DIR)
        return

    files = os.listdir(FIRMWARE_META_BASE_DIR)
    json_files = [f for f in files if f.endswith(".json")]
    logging.info(
        "Loading firmware from JSON metadata: found %s JSON files in %s",
        len(json_files),
        FIRMWARE_META_BASE_DIR,
    )

    # Build a {filename: json_text} map and reuse the shared parser.
    json_map: dict = {}
    for file in json_files:
        full_path = os.path.join(FIRMWARE_META_BASE_DIR, file)
        try:
            with open(full_path, encoding="utf-8") as f:
                json_map[file] = f.read()
        except (OSError, UnicodeDecodeError) as e:
            # ignore malformed or unreadable files
            logging.debug("Failed to read firmware JSON from %s: %s", file, e)

    _load_firmware_from_json_map(json_map, uefi_files)


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
            "No firmware found for architecture %s. Available: %s",
            architecture,
            [fw.architectures for fw in firmwares],
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
                "No firmware found for machine type %s. "
                "Using architecture-compatible firmware anyway.",
                machine_type,
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
            "Firmware %s: score=%s, features=%s, has_nvram=%s",
            fw.executable,
            score,
            fw.features,
            fw.nvram_template is not None,
        )

        if score > best_score:
            best_score = score
            best_fw = fw

    if best_fw:
        logging.info(
            "Selected firmware: %s (score=%s, arch=%s)",
            best_fw.executable,
            best_score,
            architecture,
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
        "secure-boot": 50
        if secure_boot_required
        else -50,  # High value if required, strong penalty if NOT required
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
