"""Debian OS Provider for VirtUI Manager.

This module provides Debian-specific functionality for VM provisioning,
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


class DebianDistro(Enum):
    """Debian distribution types."""

    DEBIAN_12_BOOKWORM = "12 (Bookworm)"
    DEBIAN_11_BULLSEYE = "11 (Bullseye)"
    DEBIAN_10_BUSTER = "10 (Buster)"
    DEBIAN_TESTING = "Testing"
    DEBIAN_UNSTABLE = "Unstable (Sid)"
    CUSTOM = "Custom ISO"


class DebianProvider(OSProvider):
    """Provider for Debian distributions."""

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)

    @property
    def os_type(self) -> OSType:
        """Return the OS type for Debian."""
        return OSType.DEBIAN

    def get_supported_versions(self) -> List[OSVersion]:
        """Get list of supported Debian versions."""
        versions = []
        distributions = [
            ("12", "12 (Bookworm)"),
            ("11", "11 (Bullseye)"),
            ("10", "10 (Buster)"),
            ("testing", "Testing"),
            ("unstable", "Unstable (Sid)"),
        ]

        for version_id, display_name in distributions:
            versions.append(
                OSVersion(
                    os_type=OSType.DEBIAN,
                    version_id=version_id,
                    display_name=display_name,
                    architecture="amd64",
                )
            )

        return versions

    def get_iso_sources(self, version: OSVersion) -> List[str]:
        """Get ISO download sources for Debian version."""
        # Map version IDs to distribution URLs
        if version.version_id == "testing":
            return ["https://cdimage.debian.org/cdimage/weekly-builds/amd64/iso-cd/"]
        elif version.version_id == "unstable":
            return ["https://cdimage.debian.org/cdimage/weekly-builds/amd64/iso-cd/"]
        else:
            # Stable releases (12, 11, 10)
            return ["https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/"]

    def get_cached_isos(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get cached Debian ISO information."""
        # In a real implementation, this would return cached data
        # For now, return empty dict to force fresh fetching
        return {}

    def get_iso_list(self, version: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of available Debian ISOs for a specific version."""
        try:
            if version is None:
                # Default to latest stable
                version = "12 (Bookworm)"

            # Extract version number from the display name
            version_number = self._extract_version_number(version)
            if not version_number:
                self.logger.error(f"Could not extract version number from: {version}")
                return []

            # Get ISO list for the version
            return self._get_iso_list_for_version(version_number, version)

        except Exception as e:
            self.logger.error(f"Error fetching Debian ISO list: {e}")
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

            if "debian" in content and (".iso" in content):
                # Basic parsing - in a real implementation, you'd use BeautifulSoup
                iso_list.append(
                    {
                        "name": "Debian Server ISO",
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
        self,
        version: Optional[OSVersion],
        vm_name: str,
        user_config: Dict[str, Any],
        output_path: Path,
        template_name: str | None = None,
    ) -> Path:
        """Generate Debian automation file (preseed or cloud-init)."""
        # Use default template if not provided
        if not template_name:
            template_name = "preseed-basic.cfg"

        self.logger.info(f"Generating Debian automation file with template: {template_name}")

        # Merge vm_name into config for variable substitution
        config = user_config.copy()
        config["vm_name"] = vm_name

        # Use output_path as output_dir for compatibility
        output_dir = output_path

        # Determine if this is cloud-init or preseed based on filename patterns
        is_cloud_init = (
            template_name.startswith("cloud-init")
            and (template_name.endswith(".yaml") or template_name.endswith(".yml"))
            or "cloud-init" in template_name.lower()
            and (template_name.endswith(".yaml") or template_name.endswith(".yml"))
        )

        is_preseed = (
            template_name.startswith("preseed")
            and template_name.endswith(".cfg")
            or "preseed" in template_name.lower()
            and template_name.endswith(".cfg")
        )

        result = None
        if is_cloud_init:
            result = self._generate_cloud_init_file(template_name, config, output_dir)
        elif is_preseed:
            result = self._generate_preseed_file(template_name, config, output_dir)
        else:
            # Fallback: try to determine from file extension
            if template_name.endswith((".yaml", ".yml")):
                self.logger.info(f"Treating {template_name} as cloud-init YAML file")
                result = self._generate_cloud_init_file(template_name, config, output_dir)
            elif template_name.endswith(".cfg"):
                self.logger.info(f"Treating {template_name} as preseed config file")
                result = self._generate_preseed_file(template_name, config, output_dir)
            else:
                self.logger.error(f"Unknown Debian template type: {template_name}")
                raise Exception(f"Unknown Debian template type: {template_name}")

        if result is None:
            raise Exception(
                f"Failed to generate Debian automation file with template: {template_name}"
            )

        return result

    def validate_template_content(self, content: str, template_name: str) -> bool:
        """Validate Debian template content."""
        try:
            # Check for Debian cloud-init YAML files
            if (
                (template_name.startswith("cloud-init") and template_name.endswith(".yaml"))
                or "cloud-init" in template_name.lower()
                and template_name.endswith(".yaml")
            ):
                # Validate cloud-init YAML
                data = yaml.safe_load(content)

                # Check for required cloud-init structure
                if not isinstance(data, dict):
                    return False

                # Should have cloud-init sections
                return True

            # Check for Debian preseed files (preseed*.cfg)
            elif (
                (template_name.startswith("preseed") and template_name.endswith(".cfg"))
                or "preseed" in template_name.lower()
                and template_name.endswith(".cfg")
            ):
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

            # Generic YAML check for other Debian automation files
            elif template_name.endswith(".yaml") or template_name.endswith(".yml"):
                try:
                    yaml.safe_load(content)
                    return True
                except yaml.YAMLError:
                    return False

            # Generic config file check
            elif template_name.endswith(".cfg"):
                # Basic check for non-empty config files
                return bool(content.strip())

            return False

        except Exception as e:
            self.logger.error(f"Error validating Debian template content: {e}")
            return False

    def _extract_version_number(self, version_display: str) -> Optional[str]:
        """Extract version number from display string."""
        import re

        # Handle special cases for Testing and Unstable
        version_lower = version_display.lower()
        if "testing" in version_lower:
            return "testing"
        elif "unstable" in version_lower or "sid" in version_lower:
            return "unstable"

        # Extract version number like "12", "11", "10", etc.
        match = re.search(r"(\d+)", version_display)
        return match.group(1) if match else None

    def _get_iso_list_for_version(
        self, version_number: str, version_display: str
    ) -> List[Dict[str, Any]]:
        """Get ISO list for specific Debian version."""
        iso_list = []

        try:
            # Debian releases URL pattern
            if version_number in ["12", "11", "10"]:
                # Stable releases
                base_url = "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/"
            elif version_number == "testing":
                # Testing builds
                base_url = "https://cdimage.debian.org/cdimage/weekly-builds/amd64/iso-cd/"
            elif version_number == "unstable":
                # Unstable/Sid builds (use testing weekly builds as unstable doesn't have separate ISOs)
                base_url = "https://cdimage.debian.org/cdimage/weekly-builds/amd64/iso-cd/"
            else:
                # Fallback to current
                base_url = "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/"

            # First, try to get the directory listing to find actual files
            try:
                response = requests.get(base_url, timeout=10)
                if response.status_code == 200:
                    # Parse HTML to find ISO files
                    import re

                    iso_files = re.findall(r'href="([^"]*\.iso)"', response.text)

                    # Filter for common ISO types we want to show
                    netinst_isos = [
                        f for f in iso_files if "netinst" in f.lower() and f.endswith(".iso")
                    ]
                    dvd_isos = [
                        f
                        for f in iso_files
                        if ("dvd" in f.lower() or "cd" in f.lower()) and f.endswith(".iso")
                    ]

                    # Get the latest version of each type
                    iso_candidates = []

                    if netinst_isos:
                        # Sort by filename to get the latest
                        netinst_isos.sort(reverse=True)
                        iso_candidates.append(
                            {
                                "filename": netinst_isos[0],
                                "type": "netinst",
                                "name": f"Debian {version_number} NetInstall (amd64)",
                            }
                        )

                    if dvd_isos:
                        dvd_isos.sort(reverse=True)
                        iso_candidates.append(
                            {
                                "filename": dvd_isos[0],
                                "type": "dvd",
                                "name": f"Debian {version_number} DVD (amd64)",
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
                                "description": f"Debian {candidate['type'].upper()} installer",
                                "version": version_display,
                                "date": date_str,
                            }
                        )

                else:
                    self.logger.warning(
                        f"Could not fetch directory listing from {base_url}: {response.status_code}"
                    )

            except Exception as e:
                self.logger.warning(f"Error fetching Debian directory listing: {e}")

            # Fallback: if we couldn't get real files, provide static entries
            if not iso_list:
                self.logger.info("Falling back to static Debian ISO entries")

                # Determine naming for static variants
                if version_number == "testing":
                    display_version = "Testing"
                    filename_prefix = "debian-testing"
                elif version_number == "unstable":
                    display_version = "Unstable"
                    filename_prefix = "debian-testing"  # Unstable uses testing builds
                else:
                    display_version = version_number
                    filename_prefix = f"debian-{version_number}"

                static_variants = [
                    {
                        "name": f"Debian {display_version} NetInstall (amd64)",
                        "filename": f"{filename_prefix}-amd64-netinst.iso",
                        "type": "netinst",
                    },
                    {
                        "name": f"Debian {display_version} DVD-1 (amd64)",
                        "filename": f"{filename_prefix}-amd64-DVD-1.iso",
                        "type": "dvd",
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
                            "description": f"Debian {variant['type'].upper()} installer",
                            "version": version_display,
                            "date": "",
                        }
                    )

            return iso_list

        except Exception as e:
            self.logger.error(f"Error getting ISO list for Debian {version_number}: {e}")
            return []

    def _generate_cloud_init_file(
        self, template_name: str, config: Dict[str, Any], output_dir: Path
    ) -> Path:
        """Generate Debian cloud-init file."""
        # Load the cloud-init template
        template_path = self._find_template_file(template_name)
        if not template_path or not template_path.exists():
            raise Exception(f"Debian cloud-init template not found: {template_name}")

        # Read template content
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()

        # Generate cloud-init YAML with variable substitution
        cloud_init_content = self._generate_cloud_init_yaml(template_content, config)

        # Write the cloud-init file with restrictive permissions
        output_file = output_dir / "user-data"
        with open(os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(cloud_init_content)

        # Also create meta-data file (required for cloud-init)
        meta_data_file = output_dir / "meta-data"
        with open(os.open(meta_data_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(f"instance-id: {config.get('vm_name', 'debian-vm')}\n")
            f.write(
                f"local-hostname: {config.get('hostname', config.get('vm_name', 'debian-vm'))}\n"
            )

        self.logger.info(f"Generated Debian cloud-init files: {output_file}, {meta_data_file}")
        return output_file

    def _generate_preseed_file(
        self, template_name: str, config: Dict[str, Any], output_dir: Path
    ) -> Path:
        """Generate Debian preseed file."""
        # Load the preseed template
        template_path = self._find_template_file(template_name)
        if not template_path or not template_path.exists():
            # Generate basic preseed if template not found
            self.logger.warning(
                f"Debian preseed template not found: {template_name}, using basic preseed"
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

        self.logger.info(f"Generated Debian preseed file: {output_file}")
        return output_file

    def _generate_cloud_init_yaml(self, template_content: str, config: Dict[str, Any]) -> str:
        """Generate cloud-init YAML with variable substitution."""
        # First, do string substitution on the template content
        substituted_content = self._substitute_variables(template_content, config)

        try:
            # Parse the substituted content as YAML to validate it
            yaml.safe_load(substituted_content)
            # If parsing succeeds, return the substituted content
            return substituted_content
        except Exception as e:
            self.logger.error(f"Error validating cloud-init YAML after substitution: {e}")
            # Return it anyway - the installer might be more lenient
            return substituted_content

    def _substitute_variables(self, content: str, config: Dict[str, Any]) -> str:
        """Substitute variables in template content."""
        from ..os_provider import hash_password

        # Get passwords and hash them for security (Preseed supports crypted passwords)
        # Strip whitespace that may come from config files with newlines
        user_password = config.get(
            "password", config.get("user_password", config.get("user_pw", ""))
        ).strip()
        root_password = config.get(
            "root_password", config.get("root_pw", "")
        ).strip()

        # Default to a safe fallback if no password provided (though UI should prevent this)
        if not user_password:
            user_password = "password"  # Emergency fallback if somehow empty
        if not root_password:
            root_password = user_password

        # Hash passwords for security
        hashed_user_password = hash_password(user_password)
        hashed_root_password = hash_password(root_password)

        logging.info("Debian automation uses hashed passwords for improved security")

        # Get substitution values with defaults
        substitutions = {
            "vm_name": config.get("vm_name", "debian-vm"),
            "hostname": config.get("hostname", config.get("vm_name", "debian-vm")),
            # Support both 'username' and 'user_name' for compatibility
            "username": config.get("username", config.get("user_name", "user")),
            # Use hashed passwords (templates should use -crypted keys)
            "password": hashed_user_password,
            "root_password": hashed_root_password,
            "timezone": config.get("timezone", "UTC"),
            # Support both 'locale' and 'language' for compatibility
            "locale": config.get("locale", config.get("language", "en_US.UTF-8")),
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
        # Support both key name styles for compatibility
        username = config.get("username", config.get("user_name", "user"))
        # Strip whitespace from password (can come from config files with newlines)
        password = config.get(
            "password", config.get("user_password", config.get("user_pw", "password"))
        ).strip()
        hostname = config.get("hostname", config.get("vm_name", "debian-vm"))
        locale = config.get("locale", config.get("language", "en_US.UTF-8"))
        keyboard = config.get("keyboard", "us")

        return f"""# Basic Debian Preseed Configuration
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

# Mirror settings
d-i mirror/country string manual
d-i mirror/http/hostname string deb.debian.org
d-i mirror/http/directory string /debian
d-i mirror/http/proxy string

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
tasksel tasksel/first multiselect standard, ssh-server
d-i pkgsel/include string openssh-server

# Boot loader
d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string default

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
