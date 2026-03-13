"""
OpenSUSE OS Provider Implementation

This module provides OpenSUSE-specific provisioning capabilities including:
- OpenSUSE Leap, Tumbleweed, Slowroll distributions
- ISO fetching and validation from official mirrors
- Linux-optimized VM configurations
- Support for custom repositories and cached ISOs
"""

import hashlib
import logging
import os
import re
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..os_provider import AutomationConfig, DriverInfo, OSProvider, OSType, OSVersion, hash_password


class OpenSUSEDistro(Enum):
    LEAP = "OpenSUSE Leap"
    TUMBLEWEED = "OpenSUSE Tumbleweed"
    SLOWROLL = "OpenSUSE Slowroll"
    STABLE = "OpenSUSE Stable (Leap)"
    CUSTOM = "Custom ISO"


class OpenSUSEProvider(OSProvider):
    """Provider for OpenSUSE operating systems."""

    # OpenSUSE distribution base URLs
    DISTRO_BASE_URLS = {
        OpenSUSEDistro.LEAP: "https://download.opensuse.org/distribution/leap/",
        OpenSUSEDistro.TUMBLEWEED: "https://download.opensuse.org/tumbleweed/iso/",
        OpenSUSEDistro.SLOWROLL: "https://download.opensuse.org/slowroll/iso/",
        OpenSUSEDistro.STABLE: "https://download.opensuse.org/distribution/openSUSE-stable/offline/",
    }

    def __init__(self, host_arch: str = "x86_64", cache_dir: Optional[Path] = None):
        self.logger = logging.getLogger(__name__)
        self.host_arch = host_arch

        # Set up cache directory
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "virtui-manager" / "opensuse"

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Subdirectories for organization
        self.iso_cache = self.cache_dir / "isos"
        self.template_cache = self.cache_dir / "templates"

        for cache in [self.iso_cache, self.template_cache]:
            cache.mkdir(exist_ok=True)

    @property
    def os_type(self) -> OSType:
        return OSType.LINUX

    def get_supported_versions(self) -> List[OSVersion]:
        """Return supported OpenSUSE versions."""
        versions = []

        # Add major distributions
        distributions = [
            ("leap-15.6", "openSUSE Leap 15.6", False),
            ("leap-15.5", "openSUSE Leap 15.5", False),
            ("tumbleweed", "openSUSE Tumbleweed", False),
            ("slowroll", "openSUSE Slowroll", False),
        ]

        for version_id, display_name, is_eval in distributions:
            versions.append(
                OSVersion(
                    os_type=OSType.LINUX,
                    version_id=version_id,
                    display_name=display_name,
                    architecture=self.host_arch,
                    is_evaluation=is_eval,
                )
            )

        return versions

    def get_iso_sources(self, version: OSVersion) -> List[str]:
        """Return ISO download URLs for an OpenSUSE version."""
        # Map version IDs to distribution types
        distro_mapping = {
            "leap-15.6": OpenSUSEDistro.LEAP,
            "leap-15.5": OpenSUSEDistro.LEAP,
            "tumbleweed": OpenSUSEDistro.TUMBLEWEED,
            "slowroll": OpenSUSEDistro.SLOWROLL,
        }

        distro = distro_mapping.get(version.version_id)
        if not distro:
            return []

        try:
            iso_list = self._get_iso_list_for_distro(distro)
            return [iso["url"] for iso in iso_list]
        except Exception as e:
            self.logger.error(f"Failed to get ISO sources for {version.version_id}: {e}")
            return []

    def get_default_vm_settings(self, version: OSVersion, vm_type: str) -> Dict[str, Any]:
        """Return Linux-optimized VM settings."""

        # Base Linux settings optimized for performance
        base_settings = {
            "disk_bus": "virtio",
            "disk_format": "qcow2",
            "disk_cache": "none",
            "machine": "pc-q35-10.1",
            "cpu_model": "host-passthrough",
            "network_model": "virtio",
            "video_model": "virtio",
            "sound_model": "ich9",
            "boot_uefi": True,
            "secure_boot": False,
            "tpm": False,
            "watchdog": False,
            "suspend_to_mem": "on",
            "suspend_to_disk": "on",
            "virtio_channels": ["org.qemu.guest_agent.0"],
        }

        # VM type specific adjustments
        if vm_type in ["DESKTOP", "LINUX_DESKTOP"]:
            base_settings.update(
                {
                    "cpu": 4,
                    "memory": 4096,
                    "disk_size": 30,
                    "video_model": "virtio",
                    "sound_model": "ich9",
                    "mem_backing": "memfd",
                }
            )
        elif vm_type in ["SERVER", "LINUX_SERVER"]:
            base_settings.update(
                {
                    "cpu": 6,
                    "memory": 4096,
                    "disk_size": 18,
                    "sound_model": None,
                    "video_model": "virtio",
                    "mem_backing": False,
                }
            )
        elif vm_type == "COMPUTATION":
            base_settings.update(
                {
                    "cpu": 4,
                    "memory": 8192,
                    "disk_size": 8,
                    "disk_cache": "unsafe",
                    "disk_format": "raw",
                    "video_model": "qxl",
                    "network_model": "virtio",
                    "iothreads": 4,
                    "mem_backing": "memfd",
                    "watchdog": True,
                }
            )
        elif vm_type == "SECURE":
            base_settings.update(
                {
                    "cpu": 2,
                    "memory": 4096,
                    "disk_size": 8,
                    "disk_cache": "writethrough",
                    "video_model": "qxl",
                    "network_model": "e1000",
                    "tpm": True,
                    "sev": True,
                    "input_bus": "ps2",
                    "mem_backing": False,
                }
            )

        return base_settings

    def get_automation_config(self, version: OSVersion) -> Optional[AutomationConfig]:
        """Return automation configuration for OpenSUSE."""
        return AutomationConfig(
            template_name="autoyast.xml",
            variables={
                "root_password": "linux",
                "user_name": "user",
                "user_password": "user",
                "timezone": "UTC",
                "language": "en_US",
                "keyboard": "us",
                "hostname": "opensuse-vm",
                "scc_email": "",
                "scc_reg_code": "",
                "scc_product_arch": "",
            },
            supports_custom_user=True,
            supports_network_config=True,
        )

    def get_available_templates(self) -> List[Dict[str, Any]]:
        """Scan and return available AutoYaST templates (built-in + user templates)."""
        # Use AutoYaSTTemplateManager to get all templates (built-in + user)
        try:
            from ..templates.auto_template_manager import AutoYaSTTemplateManager

            template_manager = AutoYaSTTemplateManager()
            return template_manager.get_all_templates()
        except Exception as e:
            self.logger.error(f"Error loading templates from AutoYaSTTemplateManager: {e}")

            # Fallback: scan built-in templates manually
            templates_dir = Path(__file__).parent.parent / "templates"
            templates = []

            try:
                # Look for built-in autoyast-*.xml and agama-*.json files
                patterns = ["autoyast-*.xml", "agama-*.json"]
                for pattern in patterns:
                    for template_file in templates_dir.glob(pattern):
                        template_name = template_file.stem

                        # Create display name from filename using centralized template info
                        try:
                            from ..templates.auto_template_manager import (
                                AutoYaSTTemplateManager,
                            )

                            display_name, description = (
                                AutoYaSTTemplateManager.get_template_info_for_name(template_name)
                            )
                        except ImportError:
                            # Fallback if import fails - use basic template name generation
                            if template_name.startswith("agama-"):
                                display_name = (
                                    template_name.replace("agama-", "").replace("-", " ").title()
                                    + " (Agama)"
                                )
                                description = f"Agama-based template: {template_file.name}"
                            else:
                                display_name = (
                                    template_name.replace("autoyast-", "").replace("-", " ").title()
                                    + " (AutoYaST)"
                                )
                                description = f"AutoYaST template: {template_file.name}"

                        templates.append(
                            {
                                "filename": template_file.name,
                                "display_name": display_name,
                                "description": description,
                                "path": template_file,
                                "type": "built-in",
                            }
                        )

            except Exception as e:
                self.logger.error(f"Error scanning AutoYaST templates: {e}")
                # Fall back to basic template if scanning fails
                basic_template = templates_dir / "autoyast-basic.xml"
                if basic_template.exists():
                    # Use centralized template info for consistency
                    try:
                        from ..templates.auto_template_manager import AutoYaSTTemplateManager

                        display_name, description = (
                            AutoYaSTTemplateManager.get_template_info_for_name("autoyast-basic")
                        )
                        description += " (fallback)"
                    except ImportError:
                        display_name = "Basic Server (AutoYaST)"
                        description = "Basic server installation (fallback)"

                    templates.append(
                        {
                            "filename": "autoyast-basic.xml",
                            "display_name": display_name,
                            "description": description,
                            "path": basic_template,
                            "type": "built-in",
                        }
                    )

            # Sort by type (built-in first) then by display name
            templates.sort(key=lambda x: (x["type"] != "built-in", x["display_name"]))
            return templates

    def validate_template(self, template_path: Path) -> bool:
        """Validate an AutoYaST template file."""
        try:
            # Check if file exists
            if not template_path.exists():
                self.logger.error(f"Template file does not exist: {template_path}")
                return False

            # Check if file is readable
            if not template_path.is_file():
                self.logger.error(f"Template path is not a file: {template_path}")
                return False

            # Try to parse as XML
            import xml.etree.ElementTree as ET

            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Check if it contains required template variables
                required_variables = [
                    "language",
                    "keyboard",
                    "timezone",
                    "root_password",
                    "user_name",
                    "user_password",
                    "hostname",
                ]
                missing_variables = []

                for var in required_variables:
                    if f"{{{var}}}" not in content:
                        missing_variables.append(var)

                if missing_variables:
                    self.logger.warning(
                        f"Template {template_path.name} missing variables: {missing_variables}"
                    )
                    # Don't fail validation for missing variables, just log warning

                # Try to parse XML
                ET.fromstring(content)

                # Check if it's a valid AutoYaST profile
                if 'xmlns="http://www.suse.com/1.0/yast2ns"' not in content:
                    self.logger.warning(
                        f"Template {template_path.name} may not be a valid AutoYaST profile (missing namespace)"
                    )

                self.logger.debug(f"Template validation passed: {template_path.name}")
                return True

            except ET.ParseError as e:
                self.logger.error(f"Template {template_path.name} is not valid XML: {e}")
                return False

        except Exception as e:
            self.logger.error(f"Error validating template {template_path}: {e}")
            return False

    def validate_template_content(self, template_content: str) -> tuple[bool, list[str]]:
        """
        Validate AutoYaST template content directly.

        Returns:
            tuple[bool, list[str]]: (is_valid, list_of_errors)
        """
        errors = []

        try:
            # Try to parse as XML
            import xml.etree.ElementTree as ET

            try:
                ET.fromstring(template_content)
            except ET.ParseError as e:
                errors.append(f"Invalid XML: {e}")
                return False, errors

            # Check if it's a valid AutoYaST profile
            if 'xmlns="http://www.suse.com/1.0/yast2ns"' not in template_content:
                errors.append("Missing AutoYaST namespace declaration")

            # Check for required XML structure
            required_sections = ["<profile", "<general>", "<software>", "<users"]
            missing_sections = []

            for section in required_sections:
                if section not in template_content:
                    missing_sections.append(section.strip("<"))

            if missing_sections:
                errors.append(f"Missing required sections: {', '.join(missing_sections)}")
            # Check for template variables (optional - warn but don't fail)
            recommended_variables = [
                "language",
                "keyboard",
                "timezone",
                "root_password",
                "user_name",
                "user_password",
                "hostname",
            ]
            missing_variables = []

            for var in recommended_variables:
                if f"{{{var}}}" not in template_content:
                    missing_variables.append(var)

            if missing_variables:
                errors.append(
                    f"Recommended variables missing (will use defaults): {', '.join(missing_variables)}"
                )

            # Check for dangerous or forbidden content
            dangerous_patterns = ["rm -rf", "format c:", "dd if=", "mkfs", "> /dev/"]
            found_dangerous = []

            for pattern in dangerous_patterns:
                if pattern in template_content.lower():
                    found_dangerous.append(pattern)

            if found_dangerous:
                errors.append(f"Potentially dangerous commands found: {', '.join(found_dangerous)}")

            # If we have errors but they're only warnings, consider it valid
            serious_errors = [
                e
                for e in errors
                if any(x in e.lower() for x in ["invalid xml", "missing required"])
            ]
            is_valid = len(serious_errors) == 0

            return is_valid, errors

        except Exception as e:
            errors.append(f"Validation error: {e}")
            return False, errors

    def generate_automation_file(
        self,
        version: Optional[OSVersion],
        vm_name: str,
        user_config: Dict[str, Any],
        output_path: Path,
        template_name: str | None = None,
    ) -> Path:
        """Generate AutoYaST XML file for OpenSUSE automated installation."""

        # Get automation config with defaults
        config = self.get_automation_config(version)
        if not config:
            version_name = version.display_name if version else "OpenSUSE"
            raise Exception(f"No automation config available for {version_name}")
        variables = config.variables.copy()

        # Override with user-provided values
        variables.update(user_config)

        # Hash passwords for security before substitution
        # Strip whitespace from passwords (can come from config files with newlines)
        if "root_password" in variables and variables["root_password"]:
            variables["root_password"] = hash_password(variables["root_password"].strip())

        if "user_password" in variables and variables["user_password"]:
            variables["user_password"] = hash_password(variables["user_password"].strip())

        # Alias username to user_name for compatibility between Agama and AutoYaST
        if "username" in user_config and "user_name" not in user_config:
            variables["user_name"] = user_config["username"]
        elif "user_name" in user_config and "username" not in user_config:
            variables["username"] = user_config["user_name"]

        # Ensure hostname is VM name (cleaned)
        clean_vm_name = re.sub(r"[^a-zA-Z0-9-]", "", vm_name)[:63]  # hostname limit
        variables["hostname"] = clean_vm_name or "opensuse-vm"

        # Use specified template or default to basic
        template_filename = template_name if template_name else "autoyast-basic.xml"

        # Generate content based on template extension
        if template_filename.endswith(".json"):
            content = self._generate_agama_json(version, variables, template_filename)
            output_filename = "agama.json"
        else:
            content = self._generate_autoyast_xml(version, variables, template_filename)
            output_filename = "autoyast.xml"

        # Write to output file with restrictive permissions
        output_file = output_path / output_filename
        with open(
            os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(content)

        version_name = version.display_name if version else "OpenSUSE"
        self.logger.info(
            f"Generated {output_filename} using template {template_filename} for {version_name} at {output_file}"
        )
        return output_file

    def _get_opensuse_product_name(self, version: OSVersion) -> str:
        """
        Map OSVersion to Agama product ID.

        Returns the appropriate product ID for Agama based on the version:
        - Tumbleweed: "Tumbleweed"
        - Slowroll: "Slowroll" (Note: Slowroll uses AutoYaST, not Agama - this is a fallback)
        - Leap: "openSUSE_Leap" (all Leap versions use the same product name)
        - Leap Micro: "openSUSE_Leap_Micro"
        - Default: "Tumbleweed" (safest fallback)

        Note: Agama product names don't include version numbers for Leap.
        """
        version_id = version.version_id.lower()

        if "tumbleweed" in version_id:
            return "Tumbleweed"
        elif "slowroll" in version_id:
            return "Slowroll"
        elif "leap" in version_id and "micro" in version_id:
            return "openSUSE_Leap_Micro"
        elif "leap" in version_id:
            return "openSUSE_Leap"
        elif "microos" in version_id or "micro" in version_id:
            # Regular MicroOS (not Leap Micro)
            return "openSUSE Micro OS"
        else:
            # Default fallback
            self.logger.warning(f"Unknown version {version_id}, defaulting to Tumbleweed product")
            return "Tumbleweed"

    def _generate_agama_json(
        self,
        version: OSVersion,
        variables: Dict[str, Any],
        template_filename: str,
    ) -> str:
        """Generate Agama JSON content for automated OpenSUSE installation."""
        template = None

        # Check if template_filename is a full path (user template from file system)
        template_path = Path(template_filename)
        if template_path.is_absolute() and template_path.exists():
            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    template = f.read()
                self.logger.info(f"Using user JSON template: {template_path.name}")
            except Exception as e:
                self.logger.error(f"Error reading user JSON template {template_path}: {e}")

        # If not a user template, load built-in template from file
        if template is None:
            builtin_path = Path(__file__).parent.parent / "templates" / template_filename
            try:
                with open(builtin_path, "r", encoding="utf-8") as f:
                    template = f.read()
                self.logger.info(f"Using built-in JSON template: {template_filename}")
            except Exception as e:
                self.logger.error(f"Error reading built-in JSON template: {e}")
                raise Exception(f"Failed to read JSON template: {e}")

        # Add OpenSUSE product name for Agama based on version
        if "OPENSUSE_PRODUCT_NAME" not in variables:
            if version:
                variables["OPENSUSE_PRODUCT_NAME"] = self._get_opensuse_product_name(version)
                self.logger.info(f"Set Agama product ID to: {variables['OPENSUSE_PRODUCT_NAME']}")
            else:
                # Default to Tumbleweed if no version information available
                variables["OPENSUSE_PRODUCT_NAME"] = "Tumbleweed"
                self.logger.warning(
                    "No version information available, defaulting Agama product to Tumbleweed"
                )

        # Use replace instead of format() to avoid conflicts with JSON curly braces
        for key, value in variables.items():
            if value is None:
                value = ""
            template = template.replace(f"{{{key}}}", str(value))

        return template

    def _get_iso_list_for_distro(self, distro: OpenSUSEDistro) -> List[Dict[str, Any]]:
        """Get ISO list for a specific OpenSUSE distribution."""
        base_url = self.DISTRO_BASE_URLS.get(distro)
        if not base_url:
            return []

        self.logger.info(f"Fetching ISO list from {base_url} for arch {self.host_arch}")

        iso_urls = []

        try:
            if distro == OpenSUSEDistro.LEAP:
                # Use hardcoded versions
                versions15 = ["15.5", "15.6"]
                versions16 = ["16.0"]
                for ver in versions15 + versions16:
                    if ver in versions15:
                        ver_iso_url = f"{base_url}{ver}/iso/"
                        iso_urls.extend(self.get_iso_list_from_url(ver_iso_url, arch=self.host_arch))
                    if ver in versions16:
                        ver_iso_url = f"{base_url}{ver}/offline/"
                        iso_urls.extend(self.get_iso_list_from_url(ver_iso_url, arch=self.host_arch))
            else:
                # Direct ISO directories
                iso_urls.extend(self.get_iso_list_from_url(base_url, arch=self.host_arch))

            # Deduplicate results by URL
            seen_urls = set()
            unique_isos = []
            for iso in iso_urls:
                if iso["url"] not in seen_urls:
                    seen_urls.add(iso["url"])
                    unique_isos.append(iso)

            # Sort by name descending
            unique_isos.sort(key=lambda x: x["name"], reverse=True)
            return unique_isos

        except Exception as e:
            self.logger.error(f"Failed to fetch ISO list: {e}")
            return []

    def _generate_autoyast_xml(
        self,
        version: OSVersion,
        variables: Dict[str, Any],
        template_filename: str = "autoyast-basic.xml",
    ) -> str:
        """Generate AutoYaST XML content for automated OpenSUSE installation."""

        template = None

        # Check if template_filename is a full path (user template from file system)
        template_path = Path(template_filename)
        if template_path.is_absolute() and template_path.exists():
            # This is a user template with full path
            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    template = f.read()
                self.logger.info(f"Using user template: {template_path.name}")
            except Exception as e:
                self.logger.error(f"Error reading user template {template_path}: {e}")

        # If not a user template or user template failed, load built-in template from file
        if template is None:
            builtin_path = Path(__file__).parent.parent / "templates" / template_filename

            try:
                with open(builtin_path, "r", encoding="utf-8") as f:
                    template = f.read()
                self.logger.info(f"Using built-in template: {template_filename}")
            except FileNotFoundError:
                self.logger.error(f"AutoYaST template not found at {builtin_path}")
                # Try to fall back to basic template
                fallback_path = Path(__file__).parent.parent / "templates" / "autoyast-basic.xml"
                try:
                    with open(fallback_path, "r", encoding="utf-8") as f:
                        template = f.read()
                    self.logger.warning(f"Using fallback template: autoyast-basic.xml")
                except FileNotFoundError:
                    raise Exception(
                        f"Neither template {template_filename} nor fallback autoyast-basic.xml found"
                    )
            except Exception as e:
                self.logger.error(f"Error reading AutoYaST template: {e}")
                raise Exception(f"Failed to read AutoYaST template: {e}")

        # Use replace instead of format() to avoid conflicts with potential curly braces
        for key, value in variables.items():
            if value is None:
                value = ""
            template = template.replace(f"{{{key}}}", str(value))

        return template

    def get_cached_isos(self) -> List[Dict[str, Any]]:
        """Retrieve a list of ISOs already present in the local cache directory."""
        if not self.iso_cache.exists():
            return []

        isos = []
        try:
            for f in self.iso_cache.glob("*.iso"):
                # Use stats for date
                mtime = f.stat().st_mtime
                dt_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                isos.append(
                    {
                        "name": f.name,
                        "url": f.name,  # Use filename as URL for local detection logic
                        "date": f"{dt_str} (Cached)",
                    }
                )
        except Exception as e:
            self.logger.error(f"Error reading cached ISOs: {e}")

        return isos

    def get_iso_list(self, distro: OpenSUSEDistro | str) -> List[Dict[str, Any]]:
        """
        Get list of available ISOs for a specific OpenSUSE distribution.

        Args:
            distro: OpenSUSE distribution type (enum) or custom repository URL (string)

        Returns:
            List of ISO dictionaries with 'name', 'url', and 'date' keys
        """
        # Handle string arguments (custom repositories, cached ISOs, etc.)
        if isinstance(distro, str):
            if distro == "cached":
                return self.get_cached_isos()
            elif distro == "pool_volumes":
                return []  # Pool volumes are handled elsewhere
            else:
                # Treat as custom repository URL
                return self.get_iso_list_from_url(distro, arch=self.host_arch)

        # Handle OpenSUSEDistro enum values
        if distro == OpenSUSEDistro.CUSTOM:
            return []

        return self._get_iso_list_for_distro(distro)

    def get_filtered_templates_by_distribution(
        self, distro, all_templates: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Filter templates based on distribution requirements.

        Template filtering rules:
        - "Cached ISOs" → Show ALL templates (AutoYaST + Agama)
        - "OpenSUSE Leap" → Show ONLY AutoYaST templates
        - "OpenSUSE Tumbleweed" → Show BOTH AutoYaST + Agama templates
        - "OpenSUSE Slowroll" → Show ONLY AutoYaST templates (Slowroll uses AutoYaST, not Agama)
        - "OpenSUSE Stable (Leap)" → Show ONLY Agama templates
        - Custom repositories → Show ALL templates

        Args:
            distro: Distribution type (OpenSUSEDistro enum or string)
            all_templates: List of all available templates. If None, will fetch them.

        Returns:
            List of filtered templates
        """
        # Get all templates if not provided
        if all_templates is None:
            all_templates = self.get_available_templates()

        try:
            # Check if it's an OpenSUSEDistro enum by checking its value
            if hasattr(distro, "__class__") and "OpenSUSEDistro" in str(type(distro)):
                # Handle OpenSUSEDistro enum values
                if distro == OpenSUSEDistro.LEAP:
                    # OpenSUSE Leap → ONLY AutoYaST templates
                    show_autoyast = True
                    show_agama = False
                elif distro == OpenSUSEDistro.SLOWROLL:
                    # OpenSUSE Slowroll → ONLY AutoYaST templates (Slowroll uses AutoYaST, not Agama)
                    show_autoyast = True
                    show_agama = False
                elif distro == OpenSUSEDistro.TUMBLEWEED:
                    # OpenSUSE Tumbleweed → BOTH AutoYaST + Agama templates
                    show_autoyast = True
                    show_agama = True
                elif distro == OpenSUSEDistro.STABLE:
                    # OpenSUSE Stable (Leap) → ONLY Agama templates
                    show_autoyast = False
                    show_agama = True
                else:
                    # Other OpenSUSE distributions → show all (fallback)
                    show_autoyast = True
                    show_agama = True

                filtered_templates = []
                for template in all_templates:
                    filename = template.get("filename", "")
                    is_agama = filename.endswith(".json") or "(Agama)" in template["display_name"]
                    is_autoyast = (
                        filename.endswith(".xml") or "(AutoYaST)" in template["display_name"]
                    )

                    # Apply filtering based on distribution requirements
                    should_include = False
                    if show_autoyast and is_autoyast:
                        should_include = True
                    elif show_agama and is_agama:
                        should_include = True

                    if should_include:
                        filtered_templates.append(template)

                return filtered_templates

            elif isinstance(distro, str):
                if distro in ["cached", "pool_volumes"]:
                    # Cached ISOs or pool volumes → Show ALL templates
                    return all_templates
                else:
                    # Custom repository URL → Show ALL templates for flexibility
                    return all_templates
            else:
                # Fallback - show all templates
                return all_templates
        except Exception as e:
            self.logger.warning(f"Error filtering templates for distribution {distro}: {e}")
            # Fallback - show all templates
            return all_templates

    def get_custom_repos(self) -> List[Dict[str, str]]:
        """Get custom repository configuration from config."""
        try:
            # Import here to avoid circular imports
            from ...config import load_config

            config = load_config()
            return config.get("custom_ISO_repo", [])
        except Exception as e:
            self.logger.warning(f"Failed to load custom repositories: {e}")
            return []
