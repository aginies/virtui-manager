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
from ..os_provider import OSProvider, OSType


class UbuntuDistro(Enum):
    """Ubuntu distribution types."""

    UBUNTU_24_04_LTS = "24.04 LTS (Noble Numbat)"
    UBUNTU_22_04_LTS = "22.04 LTS (Jammy Jellyfish)"
    UBUNTU_20_04_LTS = "20.04 LTS (Focal Fossa)"
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

    def get_supported_versions(self) -> List[str]:
        """Get list of supported Ubuntu versions."""
        return [
            "24.04 LTS (Noble Numbat)",
            "22.04 LTS (Jammy Jellyfish)",
            "20.04 LTS (Focal Fossa)",
            "24.10 (Oracular Oriole)",
            "23.10 (Mantic Minotaur)",
            "23.04 (Lunar Lobster)",
        ]

    def get_iso_sources(self) -> Dict[str, str]:
        """Get ISO download sources for Ubuntu."""
        return {
            "Ubuntu Official": "http://releases.ubuntu.com/",
            "Ubuntu Cloud Images": "https://cloud-images.ubuntu.com/",
            "Ubuntu Daily Builds": "http://cdimage.ubuntu.com/daily-live/current/",
        }

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

    def get_iso_list_from_url(self, url: str) -> List[Dict[str, Any]]:
        """Get ISO list from a specific URL."""
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            # Parse the HTML to extract ISO links
            # This is a simplified implementation
            iso_list = []
            content = response.text.lower()

            if "ubuntu" in content and (".iso" in content):
                # Basic parsing - in a real implementation, you'd use BeautifulSoup
                iso_list.append(
                    {
                        "name": "Ubuntu Server ISO",
                        "url": url,
                        "size": "Unknown",
                        "arch": "amd64",
                        "type": "server",
                    }
                )

            return iso_list

        except Exception as e:
            self.logger.error(f"Error fetching ISOs from URL {url}: {e}")
            return []

    def generate_automation_file(
        self, template_name: str, config: Dict[str, Any], output_dir: Path
    ) -> Optional[Path]:
        """Generate Ubuntu automation file (cloud-init or preseed)."""
        try:
            self.logger.info(f"Generating Ubuntu automation file with template: {template_name}")

            # Determine if this is autoinstall or preseed
            if template_name.endswith(".yaml") or "autoinstall" in template_name.lower():
                return self._generate_autoinstall_file(template_name, config, output_dir)
            elif template_name.endswith(".cfg") or "preseed" in template_name.lower():
                return self._generate_preseed_file(template_name, config, output_dir)
            else:
                self.logger.error(f"Unknown Ubuntu template type: {template_name}")
                return None

        except Exception as e:
            self.logger.error(f"Error generating Ubuntu automation file: {e}")
            return None

    def validate_template_content(self, content: str, template_name: str) -> bool:
        """Validate Ubuntu template content."""
        try:
            if template_name.endswith(".yaml") or "autoinstall" in template_name.lower():
                # Validate cloud-init autoinstall YAML
                import yaml

                data = yaml.safe_load(content)

                # Check for required autoinstall structure
                if not isinstance(data, dict):
                    return False

                # Should have autoinstall section for Ubuntu autoinstall
                if "autoinstall" not in data:
                    self.logger.warning(
                        "Cloud-init autoinstall template missing 'autoinstall' section"
                    )
                    return False

                return True

            elif template_name.endswith(".cfg") or "preseed" in template_name.lower():
                # Validate preseed content
                if not content.strip():
                    return False

                # Check for at least one valid preseed directive
                lines = content.split("\n")
                valid_lines = [
                    line.strip()
                    for line in lines
                    if line.strip() and not line.strip().startswith("#")
                ]

                return len(valid_lines) > 0

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
        iso_list = []

        try:
            # Ubuntu releases URL pattern
            base_url = f"http://releases.ubuntu.com/{version_number}/"

            # First, try to get the directory listing to find actual files
            try:
                response = requests.get(base_url, timeout=10)
                if response.status_code == 200:
                    # Parse HTML to find ISO files
                    import re

                    iso_files = re.findall(r'href="([^"]*\.iso)"', response.text)

                    # Filter for common ISO types we want to show
                    server_isos = [
                        f for f in iso_files if "server" in f.lower() and f.endswith(".iso")
                    ]
                    desktop_isos = [
                        f for f in iso_files if "desktop" in f.lower() and f.endswith(".iso")
                    ]

                    # Get the latest version of each type (usually the highest point release)
                    iso_candidates = []

                    if server_isos:
                        # Sort by filename to get the latest point release
                        server_isos.sort(reverse=True)
                        iso_candidates.append(
                            {
                                "filename": server_isos[0],
                                "type": "server",
                                "name": f"Ubuntu {version_number} Server (amd64)",
                            }
                        )

                    if desktop_isos:
                        desktop_isos.sort(reverse=True)
                        iso_candidates.append(
                            {
                                "filename": desktop_isos[0],
                                "type": "desktop",
                                "name": f"Ubuntu {version_number} Desktop (amd64)",
                            }
                        )

                    # Now get detailed info for each file
                    for candidate in iso_candidates:
                        iso_url = base_url + candidate["filename"]
                        date_str = ""
                        size_str = "Unknown"

                        try:
                            # Get file metadata
                            head_response = requests.head(iso_url, timeout=10)
                            if head_response.status_code == 200:
                                # Get file size
                                content_length = head_response.headers.get("content-length")
                                if content_length and content_length.isdigit():
                                    size_mb = int(content_length) // (1024 * 1024)
                                    size_str = f"{size_mb} MB"

                                # Get last modified date
                                last_modified = head_response.headers.get("last-modified")
                                if last_modified:
                                    try:
                                        from email.utils import parsedate_to_datetime

                                        dt = parsedate_to_datetime(last_modified)
                                        # Format to match OpenSUSE format: "YYYY-MM-DD HH:MM"
                                        date_str = dt.strftime("%Y-%m-%d %H:%M")
                                    except Exception as e:
                                        self.logger.debug(
                                            f"Could not parse date from {last_modified}: {e}"
                                        )

                        except Exception as e:
                            self.logger.debug(f"Could not fetch metadata for {iso_url}: {e}")

                        iso_list.append(
                            {
                                "name": candidate["name"],
                                "url": iso_url,
                                "size": size_str,
                                "arch": "amd64",
                                "type": candidate["type"],
                                "description": f"Ubuntu {candidate['type'].title()} Live installer",
                                "version": version_display,
                                "date": date_str,
                            }
                        )

                else:
                    self.logger.warning(
                        f"Could not fetch directory listing from {base_url}: {response.status_code}"
                    )

            except Exception as e:
                self.logger.warning(f"Error fetching Ubuntu directory listing: {e}")

            # Fallback: if we couldn't get real files, provide static entries
            if not iso_list:
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
    ) -> Optional[Path]:
        """Generate Ubuntu cloud-init autoinstall file."""
        try:
            # Load the autoinstall template
            template_path = self._find_template_file(template_name)
            if not template_path or not template_path.exists():
                self.logger.error(f"Ubuntu autoinstall template not found: {template_name}")
                return None

            # Read template content
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()

            # Generate autoinstall YAML with variable substitution
            autoinstall_content = self._generate_autoinstall_yaml(template_content, config)

            # Write the autoinstall file
            output_file = output_dir / "user-data"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(autoinstall_content)

            # Also create meta-data file (required for cloud-init)
            meta_data_file = output_dir / "meta-data"
            with open(meta_data_file, "w", encoding="utf-8") as f:
                f.write(f"instance-id: {config.get('vm_name', 'ubuntu-vm')}\n")
                f.write(
                    f"local-hostname: {config.get('hostname', config.get('vm_name', 'ubuntu-vm'))}\n"
                )

            self.logger.info(f"Generated Ubuntu autoinstall files: {output_file}, {meta_data_file}")
            return output_file

        except Exception as e:
            self.logger.error(f"Error generating Ubuntu autoinstall file: {e}")
            return None

    def _generate_preseed_file(
        self, template_name: str, config: Dict[str, Any], output_dir: Path
    ) -> Optional[Path]:
        """Generate Ubuntu preseed file."""
        try:
            # Load the preseed template
            template_path = self._find_template_file(template_name)
            if not template_path or not template_path.exists():
                # Generate basic preseed if template not found
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

            # Write the preseed file
            output_file = output_dir / "preseed.cfg"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(preseed_content)

            self.logger.info(f"Generated Ubuntu preseed file: {output_file}")
            return output_file

        except Exception as e:
            self.logger.error(f"Error generating Ubuntu preseed file: {e}")
            return None

    def _generate_autoinstall_yaml(self, template_content: str, config: Dict[str, Any]) -> str:
        """Generate autoinstall YAML with variable substitution."""
        import yaml

        try:
            # Parse template as YAML
            template_data = yaml.safe_load(template_content)

            # Substitute variables in the template data
            substituted_data = self._substitute_variables_recursive(template_data, config)

            # Convert back to YAML
            return yaml.dump(substituted_data, default_flow_style=False, sort_keys=False)

        except Exception as e:
            self.logger.error(f"Error processing autoinstall YAML: {e}")
            # Fallback to simple string substitution
            return self._substitute_variables(template_content, config)

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
        # Get substitution values with defaults
        substitutions = {
            "vm_name": config.get("vm_name", "ubuntu-vm"),
            "hostname": config.get("hostname", config.get("vm_name", "ubuntu-vm")),
            "username": config.get("username", "user"),
            "password": config.get("password", "password"),
            "timezone": config.get("timezone", "UTC"),
            "locale": config.get("locale", "en_US.UTF-8"),
            "keyboard": config.get("keyboard", "us"),
            "disk_device": config.get("disk_device", "/dev/vda"),
            "network_interface": config.get("network_interface", "enp1s0"),
        }

        # Perform variable substitution
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
        username = config.get("username", "user")
        password = config.get("password", "password")
        hostname = config.get("hostname", config.get("vm_name", "ubuntu-vm"))

        return f"""# Basic Ubuntu Preseed Configuration
# Generated by VirtUI Manager

# Locale and keyboard
d-i debian-installer/locale string en_US.UTF-8
d-i keyboard-configuration/xkb-keymap select us

# Network configuration
d-i netcfg/choose_interface select auto
d-i netcfg/get_hostname string {hostname}
d-i netcfg/get_domain string localdomain

# User setup
d-i passwd/user-fullname string {username}
d-i passwd/username string {username}
d-i passwd/user-password password {password}
d-i passwd/user-password-again password {password}
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
        # Get the directory where this provider is located
        current_dir = Path(__file__).parent
        templates_dir = current_dir.parent / "templates"

        # Try exact match first
        template_path = templates_dir / template_name
        if template_path.exists():
            return template_path

        # Try without extension and add common extensions
        base_name = Path(template_name).stem
        for ext in [".yaml", ".cfg"]:
            template_path = templates_dir / f"{base_name}{ext}"
            if template_path.exists():
                return template_path

        return None
