"""Generic OS Provider for VirtUI Manager.

This module provides a generic OS provider that can use any available template
with a custom ISO (local path or URL).
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..os_provider import OSProvider, OSType, OSVersion
from ..provider_registry import get_registry


class GenericProvider(OSProvider):
    """Provider for any OS using custom ISOs and all available templates."""

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)

    @property
    def os_type(self) -> OSType:
        """Return the OS type for Generic/Custom ISO."""
        return OSType.GENERIC

    def get_supported_versions(self) -> List[OSVersion]:
        """Get list of supported generic versions."""
        return [
            OSVersion(
                os_type=OSType.GENERIC,
                version_id="custom",
                display_name="Custom ISO (Generic)",
            )
        ]

    def get_iso_list(self, version: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return empty list, as user will provide path/URL."""
        return []

    def generate_automation_file(
        self,
        version: Optional[OSVersion],
        vm_name: str,
        user_config: Dict[str, Any],
        output_path: Path,
        template_name: str | None = None,
    ) -> Path:
        """Generate automation file by delegating to the appropriate provider."""
        if not template_name:
            raise Exception("No template selected for automated installation")

        self.logger.info(f"Generic provider generating automation file using template: {template_name}")

        # Map template prefix/extension to OSType
        # This mapping helps find the provider that knows how to generate the specific format
        target_os = self._guess_os_type_from_template(template_name)

        if target_os and target_os != OSType.GENERIC:
            provider = get_registry().get_provider(target_os)
            if provider:
                return provider.generate_automation_file(
                    None, vm_name, user_config, output_path, template_name
                )

        # Fallback: simple variable substitution if no specific provider matches or it is generic
        return self._basic_substitution(template_name, vm_name, user_config, output_path)

    def _guess_os_type_from_template(self, template_name: str) -> Optional[OSType]:
        """Guess the OS type based on template name or extension."""
        name_lower = template_name.lower()

        # Prefixes
        if any(k in name_lower for k in ["autoyast", "agama"]):
            return OSType.LINUX  # OpenSUSE uses LINUX
        if "autoinstall" in name_lower:
            return OSType.UBUNTU
        if "preseed" in name_lower:
            # Could be Debian or Ubuntu, default to Debian for preseed
            return OSType.DEBIAN
        if any(k in name_lower for k in ["kickstart", "ks-"]):
            return OSType.FEDORA
        if any(k in name_lower for k in ["arch", "archinstall"]):
            return OSType.ARCHLINUX
        if "alpine" in name_lower:
            return OSType.ALPINE

        # Extensions
        if template_name.endswith(".xml"):
            return OSType.LINUX
        if template_name.endswith(".json"):
            return OSType.ARCHLINUX if "arch" in name_lower else OSType.LINUX
        if template_name.endswith((".yaml", ".yml")):
            return OSType.UBUNTU
        if template_name.endswith(".cfg"):
            return OSType.DEBIAN

        return None

    def _basic_substitution(
        self, template_name: str, vm_name: str, user_config: Dict[str, Any], output_path: Path
    ) -> Path:
        """Basic variable substitution for unknown templates."""
        from ..templates.auto_template_manager import AutoYaSTTemplateManager

        tm = AutoYaSTTemplateManager()
        content = tm.get_template_content(template_name)
        if not content:
            raise Exception(f"Template not found: {template_name}")

        config = user_config.copy()
        config["vm_name"] = vm_name
        config["hostname"] = vm_name

        # Ensure some defaults for basic substitution
        if "username" not in config:
            config["username"] = "user"
        if "password" not in config:
            config["password"] = "password"

        # Simple substitution
        result = content
        for key, value in config.items():
            result = result.replace(f"{{{key}}}", str(value))
            result = result.replace(f"${{{key}}}", str(value))

        output_file = output_path / Path(template_name).name
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result)

        return output_file
