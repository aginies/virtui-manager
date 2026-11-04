"""
Declarative deployment configuration for ``vmc --config``.

Loads and validates a JSON file that describes a *single* VM deployment in
auto-install mode. Today only openSUSE / SLES is supported (AutoYaST and Agama
auto-install, with optional SUSE Customer Center registration).

This module only *describes* the deployment: it performs no libvirt operation
and never touches the network. The resolved values are handed straight through
to ``VMProvisioner.provision_vm`` by the CLI, so all the real work reuses the
existing provisioning path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .vm_provisioner import VMType, VM_TYPE_DEFAULTS


class DeployConfigError(Exception):
    """Raised when a deployment config file is missing, malformed or invalid."""


# Allowed values for the ``iso.source`` field.
ISO_SOURCES = ("url", "pool", "cached", "repository")

# ``vm_type`` may be one of the VMType profile names (lowercase) or this value,
# which lets the user supply memory/disk/vCPU manually instead of a profile.
CUSTOM_VM_TYPE = "custom"

# Map of lowercase profile name -> VMType (e.g. "desktop" -> VMType.DESKTOP).
VM_TYPE_BY_NAME = {t.name.lower(): t for t in VMType}


def resolve_vm_resources(cfg: Dict[str, Any]):
    """Resolve ``(VMType, memory_mb, vcpu, disk_size_gb)`` for a validated config.

    A profile (non-custom) takes memory/vCPU from that profile's defaults in
    ``VM_TYPE_DEFAULTS`` (any explicit config value takes precedence); disk is
    left as ``None`` so ``provision_vm`` applies ``DEFAULT_DISK_SIZE_GB`` (single
    source of truth for disk). "custom" has no dedicated hardware profile, so it
    uses the config values directly and SERVER as a neutral base.
    """
    vm_type_raw = str(cfg["vm_type"])
    if vm_type_raw == CUSTOM_VM_TYPE:
        return VMType.SERVER, cfg["memory_mb"], cfg["vcpu"], cfg["disk_size_gb"]
    vm_type = VM_TYPE_BY_NAME[vm_type_raw.lower()]
    defaults = VM_TYPE_DEFAULTS[vm_type]
    memory_mb = cfg.get("memory_mb") or defaults["memory_mb"]
    vcpu = cfg.get("vcpu") or defaults["vcpu"]
    # No explicit disk -> None -> provision_vm uses DEFAULT_DISK_SIZE_GB[vm_type].
    disk_size_gb = cfg.get("disk_size_gb")
    return vm_type, memory_mb, vcpu, disk_size_gb


def load_deploy_config(path) -> Dict[str, Any]:
    """Load and validate a deployment config JSON file.

    Returns a normalized dict ready to be consumed by the CLI deploy path.
    Raises :class:`DeployConfigError` with a human-readable message on any
    problem (missing file, bad JSON, missing/invalid fields).
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise DeployConfigError(f"Config file not found: {p}")
    if not p.is_file():
        raise DeployConfigError(f"Config path is not a file: {p}")

    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        raise DeployConfigError(f"Invalid JSON in {p}: {e}") from e

    if not isinstance(cfg, dict):
        raise DeployConfigError("Config root must be a JSON object")

    _validate(cfg)
    return cfg


def _validate(cfg: Dict[str, Any]) -> None:
    """Validate and normalize ``cfg`` in place. Raises DeployConfigError."""
    # --- required scalar fields ---
    for key in ("server", "vm_name", "vm_type"):
        if not cfg.get(key):
            raise DeployConfigError(f"Missing required field: '{key}'")

    # --- vm_type must be a known profile or "custom" ---
    vm_type_raw = str(cfg["vm_type"])
    is_custom = vm_type_raw == CUSTOM_VM_TYPE
    if not is_custom and vm_type_raw.lower() not in VM_TYPE_BY_NAME:
        valid = ", ".join(sorted(VM_TYPE_BY_NAME)) + ", custom"
        raise DeployConfigError(f"Invalid vm_type '{vm_type_raw}' (valid: {valid})")

    # --- hardware ---
    # For "custom" the user must supply all three; for a profile they are
    # optional overrides (absent values fall back to the profile defaults).
    if is_custom:
        cfg["memory_mb"] = _required_positive_int(cfg, "memory_mb")
        cfg["vcpu"] = _required_positive_int(cfg, "vcpu")
        cfg["disk_size_gb"] = _required_positive_int(cfg, "disk_size_gb")
    else:
        cfg["memory_mb"] = _positive_int(cfg, "memory_mb", None)
        cfg["vcpu"] = _positive_int(cfg, "vcpu", None)
        cfg["disk_size_gb"] = _positive_int(cfg, "disk_size_gb", None)
    if cfg.get("disk_format") is not None and not isinstance(cfg["disk_format"], str):
        raise DeployConfigError("'disk_format' must be a string (e.g. 'qcow2')")

    # --- boot / graphics / network / pool defaults ---
    cfg["boot_uefi"] = bool(cfg.get("boot_uefi", True))
    cfg["graphics"] = cfg.get("graphics", "spice")
    cfg["network"] = cfg.get("network", "default")
    cfg["storage_pool"] = cfg.get("storage_pool", "default")
    cfg["start"] = bool(cfg.get("start", True))

    # --- iso ---
    iso = cfg.get("iso")
    if not isinstance(iso, dict):
        raise DeployConfigError("Missing required object: 'iso'")
    source = iso.get("source", "url")
    if source not in ISO_SOURCES:
        raise DeployConfigError(
            f"iso.source must be one of {', '.join(ISO_SOURCES)} (got '{source}')"
        )
    if source == "url" and not iso.get("url"):
        raise DeployConfigError("iso.url is required when iso.source is 'url'")
    if source == "pool" and not (iso.get("pool") and iso.get("volume")):
        raise DeployConfigError("iso.pool and iso.volume are required when iso.source is 'pool'")
    if source == "cached" and not iso.get("volume"):
        raise DeployConfigError("iso.volume is required when iso.source is 'cached'")
    if source == "repository" and not iso.get("version"):
        raise DeployConfigError("iso.version is required when iso.source is 'repository'")

    # --- autoinstall (optional) ---
    ai = cfg.get("autoinstall")
    if ai is not None and not isinstance(ai, dict):
        raise DeployConfigError("'autoinstall' must be an object")
    ai_enabled = ai is not None and ai.get("enabled", True)

    # --- post_install (optional) ---
    # Post-install commands are baked into the auto-install template's
    # post-install script (AutoYaST <scripts><post> / Agama scripts.post), so
    # they only make sense when auto-install is enabled.
    pi = cfg.get("post_install")
    if pi is not None:
        if not isinstance(pi, dict):
            raise DeployConfigError("'post_install' must be an object")
        cmds = pi.get("commands")
        if cmds is not None and (
            not isinstance(cmds, list) or not all(isinstance(c, str) for c in cmds)
        ):
            raise DeployConfigError("post_install.commands must be a list of strings")
        if not ai_enabled:
            raise DeployConfigError(
                "post_install requires autoinstall to be enabled "
                "(commands are baked into the auto-install template)"
            )

    if ai is None:
        cfg["autoinstall"] = None
        return
    if ai_enabled and not ai.get("template"):
        raise DeployConfigError(
            "autoinstall.template is required when autoinstall.enabled is true"
        )
    pkgs = ai.get("packages")
    if pkgs is not None:
        if not isinstance(pkgs, list) or not all(isinstance(p, str) for p in pkgs):
            raise DeployConfigError("autoinstall.packages must be a list of strings")
    sc = ai.get("serial_console")
    if sc is not None and not isinstance(sc, bool):
        raise DeployConfigError("autoinstall.serial_console must be a boolean")
    scc = ai.get("scc")
    if scc is not None and not isinstance(scc, dict):
        raise DeployConfigError("autoinstall.scc must be an object")


def _positive_int(cfg: Dict[str, Any], key: str, default, minimum: int = 1):
    """Return cfg[key] as an int >= ``minimum``, applying ``default`` when absent."""
    if key not in cfg or cfg[key] is None:
        return default
    try:
        val = int(cfg[key])
    except (TypeError, ValueError) as e:
        raise DeployConfigError(f"'{key}' must be an integer (got {cfg[key]!r})") from e
    if val < minimum:
        raise DeployConfigError(f"'{key}' must be >= {minimum}")
    return val


def _required_positive_int(cfg: Dict[str, Any], key: str) -> int:
    """Return cfg[key] as an int >= 1, raising when it is absent (custom VMs)."""
    if key not in cfg or cfg[key] is None:
        raise DeployConfigError(f"'{key}' is required when vm_type is 'custom'")
    return _positive_int(cfg, key, 1)
