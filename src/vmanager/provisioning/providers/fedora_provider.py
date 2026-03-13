"""Fedora OS Provider for VirtUI Manager.

This module provides Fedora-specific functionality for VM provisioning,
including ISO management and Kickstart automation file generation.
"""

import logging
import os
import re
import urllib.request
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..os_provider import OSProvider, OSType, OSVersion, hash_password


class FedoraDistro(Enum):
    """Fedora distribution types."""

    FEDORA_43 = "43"
    FEDORA_42 = "42"
    FEDORA_41 = "41"
    CUSTOM = "Custom ISO"


class FedoraProvider(OSProvider):
    """Provider for Fedora distributions."""

    # Fedora distribution base URLs
    BASE_URL = "https://download.fedoraproject.org/pub/fedora/linux/releases/"

    def __init__(self, host_arch: str = "x86_64"):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.host_arch = host_arch

    @property
    def os_type(self) -> OSType:
        """Return the OS type for Fedora."""
        return OSType.FEDORA

    def get_supported_versions(self) -> List[OSVersion]:
        """Get list of supported Fedora versions."""
        versions = []
        # Support latest 3 major versions
        for ver in ["43", "42", "41"]:
            versions.append(
                OSVersion(
                    os_type=OSType.FEDORA,
                    version_id=ver,
                    display_name=f"Fedora {ver}",
                    architecture=self.host_arch,
                )
            )
        return versions

    def get_iso_sources(self, version: OSVersion) -> List[str]:
        """Get ISO download sources for a Fedora version."""
        # Fedora has a specific structure for ISOs
        # https://download.fedoraproject.org/pub/fedora/linux/releases/41/Workstation/x86_64/iso/
        # https://download.fedoraproject.org/pub/fedora/linux/releases/41/Server/x86_64/iso/
        # https://download.fedoraproject.org/pub/fedora/linux/releases/41/Spins/x86_64/iso/
        
        sources = []
        for variant in ["Server", "Workstation", "Spins"]:
            sources.append(f"{self.BASE_URL}{version.version_id}/{variant}/{version.architecture}/iso/")
        return sources

    def get_iso_list(self, version: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of available Fedora ISOs for multiple variants."""
        if version is None:
            version = "43"
        
        # Handle if full display name is passed
        if " " in version:
            version = version.split(" ")[-1]

        all_isos = []
        # Fetch from major variants
        for variant in ["Server", "Workstation", "Spins"]:
            url = f"{self.BASE_URL}{version}/{variant}/{self.host_arch}/iso/"
            all_isos.extend(self.get_iso_list_from_url(url, name_prefix=f"[{variant}] ", arch=self.host_arch))
            
        return all_isos

    def generate_automation_file(
        self,
        version: Optional[OSVersion],
        vm_name: str,
        user_config: Dict[str, Any],
        output_path: Path,
        template_name: str | None = None,
    ) -> Path:
        """Generate Fedora Kickstart file."""
        # Use default template if not provided
        if not template_name:
            template_name = "kickstart-basic.cfg"

        self.logger.info(f"Generating Fedora Kickstart file with template: {template_name}")

        # Merge defaults and user config
        config = user_config.copy()
        config["vm_name"] = vm_name
        
        # Ensure default values are present
        defaults = {
            "username": "fedorauser",
            "user_password": "",
            "root_password": "",
            "timezone": "UTC",
            "locale": "en_US.UTF-8",
            "keyboard": "us",
            "network_interface": "link",
        }
        for key, value in defaults.items():
            if key not in config:
                config[key] = value

        # Load template
        template_path = self._find_template_file(template_name)
        if not template_path or not template_path.exists():
            # Fallback to basic Kickstart if template not found
            self.logger.warning(f"Fedora template not found: {template_name}, using basic Kickstart")
            ks_content = self._generate_basic_kickstart(config)
        else:
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()
            
            # Substitute variables
            ks_content = self._substitute_variables(template_content, config)

        # Write to file
        output_file = output_path / "ks.cfg"
        with open(os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(ks_content)

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
        for ext in [".cfg", ".ks"]:
            template_path = templates_dir / f"{base_name}{ext}"
            if template_path.exists():
                return template_path

        return None

    def _generate_basic_kickstart(self, config: Dict[str, Any]) -> str:
        """Generate a basic Fedora Kickstart file."""
        # Hash passwords for security
        user_pwd = config.get("user_password", config.get("password", ""))
        hashed_password = hash_password(str(user_pwd).strip())
        hashed_root_password = hash_password(str(config.get("root_password", "")).strip())

        return f"""# Fedora Kickstart configuration
# Generated by VirtUI Manager

# Use text mode install
text

# Language and keyboard
lang {config.get('locale', 'en_US.UTF-8')}
keyboard {config.get('keyboard', 'us')}

# Timezone
timezone {config.get('timezone', 'UTC')} --isUtc

# Root password
rootpw --iscrypted {hashed_root_password}

# User setup
user --name={config.get('username', 'fedorauser')} --password={hashed_password} --iscrypted --groups=wheel

# Network configuration
network --bootproto=dhcp --device={config.get('network_interface', 'link')} --activate --hostname={config['vm_name']}

# Partitioning
ignoredisk --only-use=vda
clearpart --all --initlabel
autopart

# Bootloader
bootloader --location=mbr

# Repository
url --mirrorlist=https://mirrors.fedoraproject.org/mirrorlist?repo=fedora-$releasever&arch=$basearch

# Packages
%packages
@core
openssh-server
qemu-guest-agent
%end

# Services
services --enabled=sshd,qemu-guest-agent

# Reboot after installation
reboot
"""

    def validate_template_content(self, content: str, template_name: str) -> bool:
        """Validate Fedora Kickstart template content."""
        # Simple validation: should have some common Kickstart directives
        required_keywords = ["%packages", "%end", "rootpw"]
        for kw in required_keywords:
            if kw not in content:
                return False
        return True
