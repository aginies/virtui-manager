"""
AutoYaST Template Manager
Handles all AutoYaST template operations including CRUD, validation, and external editing.
Specific to openSUSE/SUSE Linux Enterprise automated installations.
"""

import logging
import os
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from ..config import (
    delete_user_autoyast_template,
    get_user_autoyast_template,
    get_user_autoyast_templates,
    save_user_autoyast_template,
)
from ..constants import ErrorMessages
from ..utils import is_inside_tmux


class AutoYaSTTemplateManager:
    """
    Service layer for AutoYaST template management.

    Handles all template operations including:
    - CRUD operations for user templates
    - Template validation (quick XML and comprehensive)
    - External editor launching via tmux
    - Template discovery (built-in and user templates)
    """

    SKELETON_TEMPLATE_FILENAME = "autoyast-skeleton.xml"
    TEMPLATES_DIR = Path(__file__).parent / "templates"

    def __init__(self, provisioner=None):
        """
        Initialize the AutoYaSTTemplateManager.

        Args:
            provisioner: Optional VMProvisioner instance for comprehensive validation
        """
        self.provisioner = provisioner
        self.logger = logging.getLogger(__name__)

    # -------------------------------------------------------------------------
    # Template Discovery
    # -------------------------------------------------------------------------

    def get_all_templates(self) -> list[dict]:
        """
        Get all available templates (built-in and user templates).

        Returns:
            List of template dicts with keys:
            - filename: Template identifier
            - display_name: Human-readable name
            - description: Template description
            - path: Path for built-in templates
            - content: Content for user templates
            - type: "built-in" or "user"
            - template_id: UUID for user templates
        """
        templates = []

        # Get built-in templates
        templates.extend(self.get_builtin_templates())

        # Get user templates
        templates.extend(self.get_user_templates())

        # Sort: built-in first, then by display name
        templates.sort(key=lambda x: (x["type"] != "built-in", x["display_name"]))

        return templates

    def get_builtin_templates(self) -> list[dict]:
        """
        Get built-in AutoYaST templates from the templates directory.

        Returns:
            List of built-in template dicts
        """
        templates = []

        try:
            # Look for autoyast-*.xml files, excluding the skeleton
            for template_file in self.TEMPLATES_DIR.glob("autoyast-*.xml"):
                if template_file.name == self.SKELETON_TEMPLATE_FILENAME:
                    continue  # Skip skeleton template

                template_name = template_file.stem
                display_name, description = self._get_builtin_template_info(template_name)

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
            self.logger.error(f"Error scanning built-in templates: {e}")

        return templates

    def get_user_templates(self) -> list[dict]:
        """
        Get user-defined templates from configuration.

        Returns:
            List of user template dicts
        """
        templates = []

        try:
            user_templates = get_user_autoyast_templates()

            for template_id, template_data in user_templates.items():
                templates.append(
                    {
                        "filename": f"user_{template_id}",
                        "display_name": f"{template_data['name']} (User)",
                        "description": template_data.get("description", "User-defined template"),
                        "content": template_data["content"],
                        "type": "user",
                        "template_id": template_id,
                    }
                )
        except Exception as e:
            self.logger.error(f"Error loading user templates: {e}")

        return templates

    def get_template(self, template_id: str) -> dict | None:
        """
        Get a specific template by ID.

        Args:
            template_id: Template identifier (filename for built-in, UUID for user)

        Returns:
            Template dict or None if not found
        """
        # Check user templates first
        user_template = get_user_autoyast_template(template_id)
        if user_template:
            return {
                "filename": f"user_{template_id}",
                "display_name": f"{user_template['name']} (User)",
                "description": user_template.get("description", ""),
                "content": user_template["content"],
                "type": "user",
                "template_id": template_id,
            }

        # Check built-in templates
        for template in self.get_builtin_templates():
            if template["filename"] == template_id:
                return template

        return None

    def is_user_template(self, template_id: str) -> bool:
        """
        Check if a template is a user-defined template.

        Args:
            template_id: Template identifier

        Returns:
            True if user template, False if built-in
        """
        return (
            template_id.startswith("user_") or get_user_autoyast_template(template_id) is not None
        )

    # -------------------------------------------------------------------------
    # Template CRUD Operations
    # -------------------------------------------------------------------------

    def save_template(
        self,
        name: str,
        content: str,
        description: str = "",
        template_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        Save a template (create new or update existing).

        Args:
            name: Template name
            content: Template XML content
            description: Template description
            template_id: Existing template ID for updates, None for new templates

        Returns:
            Tuple of (success: bool, template_id: str)
        """
        try:
            if template_id is None:
                template_id = str(uuid.uuid4())

            save_user_autoyast_template(template_id, name, content, description)
            self.logger.info(f"Saved template '{name}' with ID {template_id}")
            return True, template_id

        except Exception as e:
            self.logger.error(f"Error saving template: {e}")
            return False, ""

    def delete_template(self, template_id: str) -> bool:
        """
        Delete a user template.

        Args:
            template_id: Template UUID to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        # Extract actual ID if prefixed
        actual_id = (
            template_id.replace("user_", "") if template_id.startswith("user_") else template_id
        )

        try:
            result = delete_user_autoyast_template(actual_id)
            if result:
                self.logger.info(f"Deleted template {actual_id}")
            return result
        except Exception as e:
            self.logger.error(f"Error deleting template: {e}")
            return False

    def export_template(self, template_id: str, destination: Path) -> tuple[bool, str]:
        """
        Export a template to a file.

        Args:
            template_id: Template identifier
            destination: Directory or file path to export to

        Returns:
            Tuple of (success: bool, exported_path: str)
        """
        try:
            template = self.get_template(template_id)
            if not template:
                return False, ""

            # Get content
            if template["type"] == "user":
                content = template["content"]
            else:
                with open(template["path"], "r", encoding="utf-8") as f:
                    content = f.read()

            # Determine output path
            if destination.is_dir():
                # Generate filename from template name
                safe_name = (
                    template["display_name"].replace(" ", "_").replace("(", "").replace(")", "")
                )
                output_path = destination / f"{safe_name}.xml"
            else:
                output_path = destination

            # Write file
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)

            self.logger.info(f"Exported template to {output_path}")
            return True, str(output_path)

        except Exception as e:
            self.logger.error(f"Error exporting template: {e}")
            return False, ""

    # -------------------------------------------------------------------------
    # Template Content
    # -------------------------------------------------------------------------

    def get_skeleton_template(self) -> str:
        """
        Get the skeleton template content for creating new templates.

        Returns:
            Skeleton template XML content
        """
        skeleton_path = self.TEMPLATES_DIR / self.SKELETON_TEMPLATE_FILENAME

        try:
            with open(skeleton_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            self.logger.error(f"Error reading skeleton template: {e}")
            # Return minimal fallback
            return self._get_fallback_skeleton()

    def get_template_content(self, template_id: str) -> str | None:
        """
        Get the content of a template.

        Args:
            template_id: Template identifier

        Returns:
            Template content or None if not found
        """
        template = self.get_template(template_id)
        if not template:
            return None

        if template["type"] == "user":
            return template.get("content")
        else:
            try:
                with open(template["path"], "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                self.logger.error(f"Error reading template content: {e}")
                return None

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def validate_xml(self, content: str) -> tuple[bool, str | None]:
        """
        Perform quick XML validation.

        Args:
            content: XML content to validate

        Returns:
            Tuple of (is_valid: bool, error_message: str | None)
        """
        try:
            ET.fromstring(content)
            return True, None
        except ET.ParseError as e:
            return False, str(e)

    def validate_template(self, content: str) -> dict:
        """
        Perform comprehensive template validation.

        Args:
            content: Template XML content

        Returns:
            Dict with keys:
            - valid: bool
            - error: str | None (critical error)
            - warnings: list[str]
        """
        result = {"valid": True, "error": None, "warnings": []}

        # Quick XML check first
        is_valid_xml, xml_error = self.validate_xml(content)
        if not is_valid_xml:
            result["valid"] = False
            result["error"] = f"Invalid XML: {xml_error}"
            return result

        # Use provider for comprehensive validation if available
        if self.provisioner:
            try:
                provider = self.provisioner.get_provider("opensuse")
                if provider and hasattr(provider, "validate_template_content"):
                    is_valid, errors = provider.validate_template_content(content)
                    if not is_valid:
                        # Find the serious error
                        serious = [
                            e
                            for e in errors
                            if "invalid xml" in e.lower() or "missing required" in e.lower()
                        ]
                        if serious:
                            result["valid"] = False
                            result["error"] = serious[0]
                        result["warnings"] = [e for e in errors if e not in serious]
                    else:
                        result["warnings"] = errors
                    return result
            except Exception as e:
                self.logger.error(f"Error in comprehensive validation: {e}")

        # Fallback: basic checks
        result["warnings"] = self._basic_template_checks(content)
        return result

    # -------------------------------------------------------------------------
    # External Editor Operations
    # -------------------------------------------------------------------------

    def can_edit_externally(self) -> tuple[bool, str | None]:
        """
        Check if external editing is available.

        Returns:
            Tuple of (can_edit: bool, error_message: str | None)
        """
        if not is_inside_tmux():
            return False, ErrorMessages.TMUX_REQUIRED_FOR_TEMPLATE_EDITING
        return True, None

    def edit_template_in_tmux(
        self,
        content: str,
        on_save: Callable[[str], None],
        on_cancel: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> bool:
        """
        Open template content in external editor via tmux.

        This method blocks until the editor is closed.

        Args:
            content: Template content to edit
            on_save: Callback with edited content when saved (content changed)
            on_cancel: Optional callback when cancelled (content unchanged)
            on_error: Optional callback with error message on failure

        Returns:
            True if editing was initiated, False if tmux not available
        """
        # Check tmux availability
        can_edit, error = self.can_edit_externally()
        if not can_edit:
            if on_error and error:
                on_error(error)
            return False

        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".xml", delete=False, encoding="utf-8"
            ) as tmp_file:
                tmp_file.write(content)
                tmp_file_path = tmp_file.name

            try:
                # Get editor from environment
                editor = os.environ.get("EDITOR", "vi")

                # Create unique signal name for this edit session
                signal_name = f"virtui-edit-{os.getpid()}-{uuid.uuid4().hex[:8]}"

                # Launch editor in new tmux window
                subprocess.run(
                    [
                        "tmux",
                        "new-window",
                        "-n",
                        "template-editor",
                        f"{editor} {tmp_file_path}; tmux wait-for -S {signal_name}",
                    ],
                    check=True,
                )

                # Wait for signal (blocks until editor closes)
                subprocess.run(["tmux", "wait-for", signal_name], check=True)

                # Read edited content
                with open(tmp_file_path, "r", encoding="utf-8") as f:
                    edited_content = f.read()

                # Check if content changed
                if edited_content != content:
                    on_save(edited_content)
                elif on_cancel:
                    on_cancel()

                return True

            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_file_path)
                except OSError:
                    pass

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Editor process failed: {e}")
            if on_error:
                on_error(ErrorMessages.EDITOR_CANCELLED_OR_FAILED)
            return False
        except Exception as e:
            self.logger.error(f"Error in template editor: {e}")
            if on_error:
                on_error(str(e))
            return False

    def create_new_template(
        self,
        on_save: Callable[[str], None],
        on_cancel: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> bool:
        """
        Create a new template using external editor with skeleton content.

        Args:
            on_save: Callback with template content when saved
            on_cancel: Optional callback when cancelled
            on_error: Optional callback with error message on failure

        Returns:
            True if editor was launched, False otherwise
        """
        skeleton = self.get_skeleton_template()
        return self.edit_template_in_tmux(skeleton, on_save, on_cancel, on_error)

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _get_builtin_template_info(self, template_name: str) -> tuple[str, str]:
        """Get display name and description for built-in templates."""
        info_map = {
            "autoyast-basic": ("Basic Server", "Basic server installation with essential packages"),
            "autoyast-minimal": ("Minimal System", "Minimal installation with only core packages"),
            "autoyast-desktop": (
                "Desktop Environment",
                "Full desktop environment with GNOME and applications",
            ),
            "autoyast-development": (
                "Development Workstation",
                "Development environment with programming tools and IDE",
            ),
            "autoyast-server": (
                "Full Server",
                "Server installation with web, database, and mail services",
            ),
        }

        if template_name in info_map:
            return info_map[template_name]

        # Custom template - generate from filename
        display_name = template_name.replace("autoyast-", "").replace("-", " ").title()
        return display_name, f"Custom template: {template_name}"

    def _basic_template_checks(self, content: str) -> list[str]:
        """Perform basic template validation checks."""
        warnings = []

        # Check namespace
        if 'xmlns="http://www.suse.com/1.0/yast2ns"' not in content:
            warnings.append("Missing AutoYaST namespace declaration")

        # Check required sections
        required_sections = ["<profile", "<general>", "<software>", "<users>"]
        for section in required_sections:
            if section not in content:
                warnings.append(f"Missing section: {section.strip('<>')}")

        # Check recommended variables
        recommended_vars = ["root_password", "user_name", "user_password", "hostname"]
        missing_vars = [v for v in recommended_vars if f"{{{v}}}" not in content]
        if missing_vars:
            warnings.append(f"Missing recommended variables: {', '.join(missing_vars)}")

        return warnings

    def _get_fallback_skeleton(self) -> str:
        """Return minimal fallback skeleton template."""
        return """<?xml version="1.0"?>
<!DOCTYPE profile>
<profile xmlns="http://www.suse.com/1.0/yast2ns" 
         xmlns:config="http://www.suse.com/1.0/configns">
  <general>
    <mode>
      <confirm config:type="boolean">false</confirm>
    </mode>
  </general>

  <software>
    <patterns config:type="list">
      <pattern>base</pattern>
    </patterns>
  </software>

  <users config:type="list">
    <user>
      <username>root</username>
      <user_password>{root_password}</user_password>
      <encrypted config:type="boolean">false</encrypted>
    </user>
  </users>
</profile>"""
