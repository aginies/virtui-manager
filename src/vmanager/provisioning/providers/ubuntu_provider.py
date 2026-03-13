"""Ubuntu OS Provider for VirtUI Manager.

This module provides Ubuntu-specific functionality for VM provisioning,
including ISO management, template handling, and automation file generation.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum

import requests
import yaml
from ..os_provider import OSProvider, OSType, OSVersion


class UbuntuDistro(Enum):
    """Ubuntu distribution types."""

    UBUNTU_24_04_LTS = "24.04 LTS (Noble Numbat)"
    UBUNTU_22_04_LTS = "22.04 LTS (Jammy Jellyfish)"
    UBUNTU_20_04_LTS = "20.04 LTS (Focal Fossa)"
    UBUNTU_25_10 = "25.10 (Questing Quokka)"
    UBUNTU_24_10 = "24.10 (Oracular Oriole)"
    UBUNTU_23_10 = "23.10 (Mantic Minotaur)"
    UBUNTU_23_04 = "23.04 (Lunar Lobster)"
    CUSTOM = "Custom ISO"


class UbuntuProvider(OSProvider):
    """Provider for Ubuntu distributions."""

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)

    @property
    def os_type(self) -> OSType:
        """Return the OS type for Ubuntu."""
        return OSType.UBUNTU

    def get_supported_versions(self) -> List[OSVersion]:
        """Get list of supported Ubuntu versions."""
        versions = []
        distributions = [
            ("24.04", "24.04 LTS (Noble Numbat)"),
            ("22.04", "22.04 LTS (Jammy Jellyfish)"),
            ("20.04", "20.04 LTS (Focal Fossa)"),
            ("25.10", "25.10 (Questing Quokka)"),
            ("24.10", "24.10 (Oracular Oriole)"),
            ("23.10", "23.10 (Mantic Minotaur)"),
            ("23.04", "23.04 (Lunar Lobster)"),
        ]

        for version_id, display_name in distributions:
            versions.append(
                OSVersion(
                    os_type=OSType.UBUNTU,
                    version_id=version_id,
                    display_name=display_name,
                    architecture="amd64",
                )
            )

        return versions

    def get_iso_sources(self, version: OSVersion) -> List[str]:
        """Get ISO download sources for Ubuntu version."""
        # Use primary release URL for all versions as they follow same structure
        return [f"http://releases.ubuntu.com/{version.version_id}/"]

    def get_cached_isos(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get cached Ubuntu ISO information."""
        # In a real implementation, this would return cached data
        # For now, return empty dict to force fresh fetching
        return {}

    def get_iso_list(self, version: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of available Ubuntu ISOs for a specific version."""
        try:
            if version is None:
                # Default to latest LTS
                version = "24.04 LTS (Noble Numbat)"

            # Extract version number from the display name
            version_number = self._extract_version_number(version)
            if not version_number:
                self.logger.error(f"Could not extract version number from: {version}")
                return []

            # Get ISO list for the version
            return self._get_iso_list_for_version(version_number, version)

        except Exception as e:
            self.logger.error(f"Error fetching Ubuntu ISO list: {e}")
            return []

    def generate_automation_file(
        self,
        version: Optional[OSVersion],
        vm_name: str,
        user_config: Dict[str, Any],
        output_path: Path,
        template_name: str | None = None,
    ) -> Path:
        """Generate Ubuntu automation file (autoinstall or preseed)."""
        if not template_name:
            template_name = "autoinstall-basic.yaml"

        self.logger.info(f"Generating Ubuntu automation file with template: {template_name}")

        config = user_config.copy()
        config["vm_name"] = vm_name
        output_dir = output_path

        is_autoinstall = (
            template_name.startswith("autoinstall")
            and (template_name.endswith(".yaml") or template_name.endswith(".yml"))
            or "autoinstall" in template_name.lower()
            and (template_name.endswith(".yaml") or template_name.endswith(".yml"))
        )

        is_preseed = (
            template_name.startswith("preseed")
            and template_name.endswith(".cfg")
            or "preseed" in template_name.lower()
            and template_name.endswith(".cfg")
        )

        result = None
        if is_autoinstall:
            result = self._generate_autoinstall_file(template_name, config, output_dir)
        elif is_preseed:
            result = self._generate_preseed_file(template_name, config, output_dir)
        else:
            # Fallback: try to determine from file extension
            if template_name.endswith((".yaml", ".yml")):
                self.logger.info(f"Treating {template_name} as autoinstall YAML file")
                result = self._generate_autoinstall_file(template_name, config, output_dir)
            elif template_name.endswith(".cfg"):
                self.logger.info(f"Treating {template_name} as preseed config file")
                result = self._generate_preseed_file(template_name, config, output_dir)
            else:
                self.logger.error(f"Unknown Ubuntu template type: {template_name}")
                raise Exception(f"Unknown Ubuntu template type: {template_name}")

        if result is None:
            raise Exception(
                f"Failed to generate Ubuntu automation file with template: {template_name}"
            )

        return result

    def validate_template_content(self, content: str, template_name: str) -> bool:
        """Validate Ubuntu template content."""
        try:
            # Check for Ubuntu autoinstall YAML files (autoinstall*.yaml)
            if (
                (template_name.startswith("autoinstall") and template_name.endswith(".yaml"))
                or "autoinstall" in template_name.lower()
                and template_name.endswith(".yaml")
            ):
                data = yaml.safe_load(content)
                if not isinstance(data, dict):
                    return False

                if "autoinstall" not in data:
                    self.logger.warning("Ubuntu autoinstall template missing 'autoinstall' section")
                    return False

                return True

            elif (
                (template_name.startswith("preseed") and template_name.endswith(".cfg"))
                or "preseed" in template_name.lower()
                and template_name.endswith(".cfg")
            ):
                if not content.strip():
                    return False

                lines = content.split("\n")
                valid_lines = [
                    line.strip()
                    for line in lines
                    if line.strip() and not line.strip().startswith("#")
                ]

                return len(valid_lines) > 0

            elif template_name.endswith(".yaml") or template_name.endswith(".yml"):
                try:
                    yaml.safe_load(content)
                    return True
                except yaml.YAMLError:
                    return False

            elif template_name.endswith(".cfg"):
                return bool(content.strip())

            return False

        except Exception as e:
            self.logger.error(f"Error validating Ubuntu template content: {e}")
            return False

    def _extract_version_number(self, version_display: str) -> Optional[str]:
        """Extract version number from display string."""
        import re

        # Extract version number like "24.04", "22.04", etc.
        match = re.search(r"(\d+\.\d+)", version_display)
        return match.group(1) if match else None

    def _get_iso_list_for_version(
        self, version_number: str, version_display: str
    ) -> List[Dict[str, Any]]:
        """Get ISO list for specific Ubuntu version."""
        try:
            base_url = f"http://releases.ubuntu.com/{version_number}/"
            iso_list = self.get_iso_list_from_url(base_url, arch="amd64")
            filtered_list = []
            for iso in iso_list:
                filename = iso["name"].lower()
                if "live-server" in filename:
                    iso["type"] = "server"
                    iso["name"] = f"Ubuntu {version_number} Live Server (amd64) - {iso['name']}"
                elif "desktop" in filename:
                    iso["type"] = "desktop"
                    iso["name"] = f"Ubuntu {version_number} Desktop (amd64) - {iso['name']}"
                else:
                    iso["type"] = "other"

                iso["description"] = f"Ubuntu {iso['type'].title()} Live installer"
                iso["version"] = version_display
                filtered_list.append(iso)

            # Fallback: if we couldn't get real files, provide static entries
            if not filtered_list:
                self.logger.info("Falling back to static Ubuntu ISO entries")
                static_variants = [
                    {
                        "name": f"Ubuntu {version_number} Server (amd64)",
                        "filename": f"ubuntu-{version_number}-live-server-amd64.iso",
                        "type": "server",
                    },
                    {
                        "name": f"Ubuntu {version_number} Desktop (amd64)",
                        "filename": f"ubuntu-{version_number}-desktop-amd64.iso",
                        "type": "desktop",
                    },
                ]

                for variant in static_variants:
                    iso_list.append(
                        {
                            "name": variant["name"],
                            "url": base_url + variant["filename"],
                            "size": "Unknown",
                            "arch": "amd64",
                            "type": variant["type"],
                            "description": f"Ubuntu {variant['type'].title()} Live installer",
                            "version": version_display,
                            "date": "",
                        }
                    )

            return iso_list

        except Exception as e:
            self.logger.error(f"Error getting ISO list for Ubuntu {version_number}: {e}")
            return []

    def _generate_autoinstall_file(
        self, template_name: str, config: Dict[str, Any], output_dir: Path
    ) -> Path:
        """Generate Ubuntu cloud-init autoinstall file."""
        template_path = self._find_template_file(template_name)
        if not template_path or not template_path.exists():
            raise Exception(f"Ubuntu autoinstall template not found: {template_name}")

        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()

        autoinstall_content = self._generate_autoinstall_yaml(template_content, config)
        output_file = output_dir / "user-data"
        with open(os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(autoinstall_content)

        # Also create meta-data file (required for cloud-init)
        meta_data_file = output_dir / "meta-data"
        with open(os.open(meta_data_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(f"instance-id: {config.get('vm_name', 'ubuntu-vm')}\n")
            f.write(
                f"local-hostname: {config.get('hostname', config.get('vm_name', 'ubuntu-vm'))}\n"
            )

        self.logger.info(f"Generated Ubuntu autoinstall files: {output_file}, {meta_data_file}")
        return output_file

    def _generate_preseed_file(
        self, template_name: str, config: Dict[str, Any], output_dir: Path
    ) -> Path:
        """Generate Ubuntu preseed file."""
        template_path = self._find_template_file(template_name)
        if not template_path or not template_path.exists():
            self.logger.warning(
                f"Ubuntu preseed template not found: {template_name}, using basic preseed"
            )
            preseed_content = self._generate_basic_preseed(config)
        else:
            # Read template content
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()

            # Generate preseed with variable substitution
            preseed_content = self._substitute_variables(template_content, config)

        # Write the preseed file with restrictive permissions
        output_file = output_dir / "preseed.cfg"
        with open(os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(preseed_content)

        self.logger.info(f"Generated Ubuntu preseed file: {output_file}")
        return output_file

    def _generate_autoinstall_yaml(self, template_content: str, config: Dict[str, Any]) -> str:
        """Generate autoinstall YAML with variable substitution."""
        # First, do string substitution on the template content
        # This must be done BEFORE YAML parsing because curly braces like {network_interface}
        # would be interpreted as YAML set literals
        substituted_content = self._substitute_variables(template_content, config)

        try:
            yaml.safe_load(substituted_content)
            return substituted_content
        except Exception as e:
            self.logger.error(f"Error validating autoinstall YAML after substitution: {e}")
            return substituted_content

    def _substitute_variables_recursive(self, data: Any, config: Dict[str, Any]) -> Any:
        """Recursively substitute variables in data structure."""
        if isinstance(data, dict):
            return {
                key: self._substitute_variables_recursive(value, config)
                for key, value in data.items()
            }
        elif isinstance(data, list):
            return [self._substitute_variables_recursive(item, config) for item in data]
        elif isinstance(data, str):
            return self._substitute_variables(data, config)
        else:
            return data

    def _substitute_variables(self, content: str, config: Dict[str, Any]) -> str:
        """Substitute variables in template content."""
        from ..os_provider import hash_password

        # Get passwords and hash them for autoinstall (identity section requires hashed passwords)
        # Strip whitespace that may come from config files with newlines
        user_password = config.get(
            "password", config.get("user_password", config.get("user_pw", ""))
        ).strip()
        root_password = config.get(
            "root_password", config.get("root_pw", "")
        ).strip()

        # Default to safe fallbacks if empty
        if not user_password:
            user_password = "password"  # Emergency fallback
        if not root_password:
            root_password = user_password

        # Check if we are generating for autoinstall (YAML) or preseed (CFG)
        # Autoinstall ALWAYS requires hashed passwords in identity
        hashed_user_password = hash_password(user_password)
        hashed_root_password = hash_password(root_password)

        logging.info("Ubuntu autoinstall uses hashed passwords in identity section")

        # Get substitution values with defaults
        # Support both Ubuntu-style and OpenSUSE-style key names for compatibility
        substitutions = {
            "vm_name": config.get("vm_name", "ubuntu-vm"),
            "hostname": config.get("hostname", config.get("vm_name", "ubuntu-vm")),
            # Support both 'username' and 'user_name' for compatibility
            "username": config.get("username", config.get("user_name", "user")),
            # Use hashed passwords for autoinstall (replaces plaintext for improved security)
            "password": hashed_user_password,
            "root_password": hashed_root_password,
            "timezone": config.get("timezone", "UTC"),
            # Support both 'locale' and 'language' for compatibility
            "locale": config.get("locale", config.get("language", "en_US.UTF-8")),
            "keyboard": config.get("keyboard", "us"),
            "disk_device": config.get("disk_device", "/dev/vda"),
            "network_interface": config.get("network_interface", "enp1s0"),
        }

        result = content
        for key, value in substitutions.items():
            placeholder = f"{{{key}}}"
            result = result.replace(placeholder, str(value))
            # Also support ${key} format
            placeholder = f"${{{key}}}"
            result = result.replace(placeholder, str(value))

        return result

    def _generate_basic_preseed(self, config: Dict[str, Any]) -> str:
        """Generate basic preseed configuration."""
        from ..os_provider import hash_password

        username = config.get("username", config.get("user_name", "user"))
        password = config.get(
            "password", config.get("user_password", config.get("user_pw", "linux"))
        ).strip()
        hostname = config.get("hostname", config.get("vm_name", "ubuntu-vm"))
        locale = config.get("locale", config.get("language", "en_US.UTF-8"))
        keyboard = config.get("keyboard", "us")

        hashed_password = hash_password(password)

        return f"""# Basic Ubuntu Preseed Configuration
# Generated by VirtUI Manager

# Locale and keyboard
d-i debian-installer/locale string {locale}
d-i keyboard-configuration/xkb-model select pc105
d-i keyboard-configuration/xkb-layout select {keyboard}
d-i keyboard-configuration/xkb-variant select
d-i keyboard-configuration/xkb-options select

# Network configuration
d-i netcfg/choose_interface select auto
d-i netcfg/get_hostname string {hostname}
d-i netcfg/get_domain string localdomain

# User setup
d-i passwd/user-fullname string {username}
d-i passwd/username string {username}
d-i passwd/user-password-crypted password {hashed_password}
d-i user-setup/allow-password-weak boolean true

# Disk partitioning
d-i partman-auto/method string regular
d-i partman-auto/choose_recipe select atomic
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true

# Package selection
tasksel tasksel/first multiselect ubuntu-server
d-i pkgsel/include string openssh-server

# Boot loader
d-i grub-installer/only_debian boolean true

# Finish installation
d-i finish-install/reboot_in_progress note
"""

    def _find_template_file(self, template_name: str) -> Optional[Path]:
        """Find template file in templates directory."""
        current_dir = Path(__file__).parent
        templates_dir = current_dir.parent / "templates"
        template_path = templates_dir / template_name
        if template_path.exists():
            return template_path

        base_name = Path(template_name).stem
        for ext in [".yaml", ".cfg"]:
            template_path = templates_dir / f"{base_name}{ext}"
            if template_path.exists():
                return template_path

        return None
