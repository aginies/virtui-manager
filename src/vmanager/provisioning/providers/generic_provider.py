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
