"""Arch Linux OS Provider for VirtUI Manager.

This module provides Arch Linux-specific functionality for VM provisioning,
including ISO management and archinstall automation file generation.
"""

import logging
import os
import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..os_provider import OSProvider, OSType, OSVersion, hash_password


class ArchLinuxDistro(Enum):
    """Arch Linux distribution types."""

    LATEST = "Latest"
    PREVIOUS = "Previous"
    CUSTOM = "Custom ISO"


class ArchLinuxProvider(OSProvider):
    """Provider for Arch Linux distributions."""

    # Arch Linux mirror ISO directory
    BASE_URL = "https://mirror.rackspace.com/archlinux/iso/"

    def __init__(self, host_arch: str = "x86_64"):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.host_arch = host_arch

    @property
    def os_type(self) -> OSType:
        """Return the OS type for Arch Linux."""
        return OSType.ARCHLINUX

    @property
    def preferred_boot_uefi(self) -> bool:
        """Arch Linux ISOs often have issues with UEFI/Secure Boot in some environments."""
        return False

    def get_supported_versions(self) -> List[OSVersion]:
        """Get list of supported Arch Linux versions (Rolling)."""
        versions = []
        # Since it's rolling, we just provide generic labels
        # but the ISO fetcher will get the actual dated ISOs
        versions.append(
            OSVersion(
                os_type=OSType.ARCHLINUX,
                version_id="latest",
                display_name="Latest Release (Rolling)",
                architecture=self.host_arch,
            )
        )
        return versions

    def get_iso_sources(self, version: OSVersion) -> List[str]:
        """Get ISO download sources for Arch Linux."""
        return [f"{self.BASE_URL}latest/"]

    def get_iso_list(self, version: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of available Arch Linux ISOs."""
        # Arch usually has a /latest/ directory and dated directories
        url = f"{self.BASE_URL}latest/"
        return self.get_iso_list_from_url(url, arch=self.host_arch)

    def generate_automation_file(
        self,
        version: Optional[OSVersion],
        vm_name: str,
        user_config: Dict[str, Any],
        output_path: Path,
        template_name: str | None = None,
    ) -> Path:
        """Generate Arch Linux archinstall JSON file."""
        # Use default template if not provided
        if not template_name:
            template_name = "archinstall-basic.json"

        self.logger.info(f"Generating Arch Linux automation file with template: {template_name}")

        # Merge defaults and user config
        config = user_config.copy()
        config["vm_name"] = vm_name
        
        # Ensure default values are present for archinstall
        defaults = {
            "username": "archuser",
            "user_password": "",
            "root_password": "",
            "timezone": "UTC",
            "locale": "en_US",
            "keyboard": "us",
        }
        for key, value in defaults.items():
            if key not in config:
                config[key] = value

        # Hash passwords for security (archinstall supports hashed passwords in JSON)
        # Strip whitespace that may come from config files with newlines
        if "user_password" in config:
            config["user_password"] = hash_password(str(config["user_password"]).strip())
        if "password" in config:
            # support both for compatibility
            config["password"] = hash_password(str(config["password"]).strip())
        if "root_password" in config:
            config["root_password"] = hash_password(str(config["root_password"]).strip())
        template_path = self._find_template_file(template_name)
        if not template_path or not template_path.exists():
            # Generate minimal JSON if template not found
            self.logger.warning(f"Arch template not found: {template_name}, using basic JSON")
            automation_content = self._generate_basic_json(config)
        else:
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()
            
            # Substitute variables
            automation_content = self._substitute_variables(template_content, config)

        output_file = output_path / "archinstall.json"
        with open(os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(automation_content)

        return output_file

    def _substitute_variables(self, content: str, config: Dict[str, Any]) -> str:
        """Substitute variables in template content."""
        # Clean and hash passwords for security if they are present and look like plaintext
        # (plaintext passwords don't start with $6$)
        substitutions = config.copy()
        
        if "user_password" in substitutions:
            pwd = str(substitutions["user_password"]).strip()
            if not pwd.startswith("$6$"):
                substitutions["user_password"] = hash_password(pwd)

        if "password" in substitutions:
            pwd = str(substitutions["password"]).strip()
            if not pwd.startswith("$6$"):
                substitutions["password"] = hash_password(pwd)
                
        if "root_password" in substitutions:
            rpwd = str(substitutions["root_password"]).strip()
            if not rpwd.startswith("$6$"):
                substitutions["root_password"] = hash_password(rpwd)

        result = content
        for key, value in substitutions.items():
            placeholder = f"{{{key}}}"
            result = result.replace(placeholder, str(value))
            # Also support ${key} format
            placeholder = f"${{{key}}}"
            result = result.replace(placeholder, str(value))
        return result

    def _find_template_file(self, template_name: str) -> Optional[Path]:
        """Find template file in templates directory."""
        current_dir = Path(__file__).parent
        templates_dir = current_dir.parent / "templates"

        # Try exact match first
        template_path = templates_dir / template_name
        if template_path.exists():
            return template_path

        # Try without extension and add common extensions
        base_name = Path(template_name).stem
        for ext in [".json", ".yaml"]:
            template_path = templates_dir / f"{base_name}{ext}"
            if template_path.exists():
                return template_path

        return None

    def _generate_basic_json(self, config: Dict[str, Any]) -> str:
        """Generate a basic archinstall JSON configuration."""
        # Hash passwords for security
        user_pwd = config.get("user_password", "")
        hashed_password = hash_password(str(user_pwd).strip())
        hashed_root_password = hash_password(str(config.get("root_password", "")).strip())
        
        arch_config = {
            "hostname": config["vm_name"],
            "keyboard-layout": config.get("keyboard", "us"),
            "locale": config.get("locale", "en_US"),
            "timezone": config.get("timezone", "UTC"),
            "users": [
                {
                    "username": config.get("username", "archuser"),
                    "password": hashed_password,
                    "sudo": True
                }
            ],
            "root-password": hashed_root_password,
            "disk_layouts": [
                {
                    "device": "/dev/vda",
                    "wipe": True,
                    "partitions": [
                        {
                            "type": "primary",
                            "start": "1MiB",
                            "size": "100%",
                            "boot": True,
                            "mountpoint": "/",
                            "filesystem": "ext4"
                        }
                    ]
                }
            ],
            "bootloader": "grub-install",
            "packages": ["openssh", "qemu-guest-agent"],
            "services": ["sshd", "qemu-guest-agent"]
        }
        return json.dumps(arch_config, indent=4)

    def validate_template_content(self, content: str, template_name: str) -> bool:
        """Validate Arch Linux template content (JSON)."""
        try:
            json.loads(content)
            return True
        except:
            return False

    def generate_setup_script(self, json_url: str, creds_url: str) -> str:
        """Generate Arch Linux auto-installation setup script.

        This script orchestrates the auto-installation process by:
        1. Ensuring network is online
        2. Downloading the archinstall JSON configuration
        3. Running archinstall with the downloaded config
        4. Rebooting after successful installation

        Args:
            json_url: URL of the archinstall JSON configuration file

        Returns:
            The setup script content as a string
        """
        setup_script_content = f"""#!/bin/bash
# Arch Linux Auto-Install Setup Script
# Generated by VirtUI Manager

set -e  # Exit on error

echo "Starting Arch Linux auto-installation setup..."

# Ensure network is online
echo "Waiting for network to be online..."
systemctl start systemd-networkd-wait-online.service || true
systemctl start network-online.target || true

# Wait a bit more to ensure network is fully ready
sleep 3

# Download the archinstall configuration
echo "Downloading archinstall configuration from {json_url}..."
curl -f -o /root/auto.json "{json_url}"
echo "Downloading archinstall creds configuration from {creds_url}..."
curl -f -o /root/creds.json "{creds_url}"

if [ $? -ne 0 ]; then
    echo "ERROR: Failed to download archinstall or creds configuration!"
    echo "Press any key to get a shell for debugging..."
    read -n 1
    exec /bin/bash
fi

echo "Configuration downloaded successfully"

# Run archinstall with the downloaded configuration
echo "Starting archinstall..."
archinstall --config /root/auto.json --creds /root/creds.json --silent

if [ $? -ne 0 ]; then
    echo "ERROR: archinstall failed!"
    echo "Press any key to get a shell for debugging..."
    read -n 1
    exec /bin/bash
fi

echo "Installation complete! Rebooting..."
reboot
"""
        return setup_script_content
