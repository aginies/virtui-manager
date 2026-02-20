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
    get_user_templates_dir,
    get_user_templates_dir_for_os,
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
        Get user-defined templates from file system.

        Scans ~/.config/virtui-manager/templates/ and subdirectories for XML files.
        Also checks legacy YAML config for backward compatibility.

        Returns:
            List of user template dicts
        """
        templates = []

        try:
            # Scan file system for user templates
            base_dir = get_user_templates_dir()

            # Recursively find all XML files in templates directory
            for xml_file in base_dir.rglob("*.xml"):
                try:
                    # Get OS name from directory structure
                    # e.g., templates/openSUSE/mytemplate.xml -> os_name = "openSUSE"
                    relative_path = xml_file.relative_to(base_dir)
                    os_name = relative_path.parts[0] if len(relative_path.parts) > 1 else "Generic"

                    # Read template content
                    with open(xml_file, "r", encoding="utf-8") as f:
                        content = f.read()

                    # Try to read metadata from companion .meta file
                    meta_file = xml_file.with_suffix(".xml.meta")
                    description = "User-defined template"
                    if meta_file.exists():
                        try:
                            import json

                            with open(meta_file, "r", encoding="utf-8") as f:
                                meta = json.load(f)
                                description = meta.get("description", description)
                        except Exception as e:
                            self.logger.warning(f"Could not read metadata for {xml_file}: {e}")

                    template_name = xml_file.stem  # Filename without extension
                    display_name = f"{template_name} ({os_name})"

                    templates.append(
                        {
                            "filename": xml_file.name,
                            "display_name": display_name,
                            "description": description,
                            "path": xml_file,
                            "content": content,
                            "type": "user",
                            "template_id": str(xml_file),  # Use full path as ID
                            "os_name": os_name,
                        }
                    )
                except Exception as e:
                    self.logger.error(f"Error reading user template {xml_file}: {e}")

            # Backward compatibility: check legacy YAML config
            legacy_templates = get_user_autoyast_templates()
            if legacy_templates:
                self.logger.info(f"Found {len(legacy_templates)} legacy templates in YAML config")
                for template_id, template_data in legacy_templates.items():
                    templates.append(
                        {
                            "filename": f"user_{template_id}",
                            "display_name": f"{template_data['name']} (Legacy)",
                            "description": template_data.get(
                                "description", "User-defined template"
                            ),
                            "content": template_data["content"],
                            "type": "user",
                            "template_id": template_id,
                            "os_name": "openSUSE",  # Legacy templates are AutoYaST (openSUSE)
                            "is_legacy": True,
                        }
                    )

        except Exception as e:
            self.logger.error(f"Error loading user templates: {e}")

        return templates

    def get_template(self, template_id: str) -> dict | None:
        """
        Get a specific template by ID.

        Args:
            template_id: Template identifier (filename for built-in, path for user files, UUID for legacy)

        Returns:
            Template dict or None if not found
        """
        # If template_id is a file path, read it directly
        template_path = Path(template_id)
        if template_path.exists() and template_path.suffix == ".xml":
            try:
                with open(template_path, "r", encoding="utf-8") as f:
                    content = f.read()

                base_dir = get_user_templates_dir()
                relative_path = template_path.relative_to(base_dir)
                os_name = relative_path.parts[0] if len(relative_path.parts) > 1 else "Generic"

                # Read metadata
                meta_file = template_path.with_suffix(".xml.meta")
                description = "User-defined template"
                if meta_file.exists():
                    try:
                        import json

                        with open(meta_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                            description = meta.get("description", description)
                    except Exception:
                        pass

                return {
                    "filename": template_path.name,
                    "display_name": f"{template_path.stem} ({os_name})",
                    "description": description,
                    "content": content,
                    "type": "user",
                    "template_id": template_id,
                    "path": template_path,
                    "os_name": os_name,
                }
            except Exception as e:
                self.logger.error(f"Error reading template file {template_id}: {e}")

        # Check legacy YAML config
        user_template = get_user_autoyast_template(template_id)
        if user_template:
            return {
                "filename": f"user_{template_id}",
                "display_name": f"{user_template['name']} (Legacy)",
                "description": user_template.get("description", ""),
                "content": user_template["content"],
                "type": "user",
                "template_id": template_id,
                "os_name": "openSUSE",
                "is_legacy": True,
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
        # Check if it's a file path in user templates directory
        template_path = Path(template_id)
        if template_path.exists():
            try:
                base_dir = get_user_templates_dir()
                template_path.relative_to(base_dir)
                return True
            except ValueError:
                # Not in user templates directory
                pass

        # Check legacy YAML config
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
        os_name: str = "openSUSE",
    ) -> tuple[bool, str]:
        """
        Save a template to file system (create new or update existing).

        Args:
            name: Template name (used as filename)
            content: Template XML content
            description: Template description
            template_id: Existing template path for updates, None for new templates
            os_name: OS/distribution name (used for directory organization)

        Returns:
            Tuple of (success: bool, template_path: str)
        """
        try:
            # Get or create template file path
            if template_id and Path(template_id).exists():
                # Update existing file
                template_path = Path(template_id)
            else:
                # Create new file
                # Sanitize filename
                safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
                safe_name = safe_name.replace(" ", "_")

                # Get OS-specific directory
                os_dir = get_user_templates_dir_for_os(os_name)
                template_path = os_dir / f"{safe_name}.xml"

                # Handle name conflicts
                counter = 1
                while template_path.exists():
                    template_path = os_dir / f"{safe_name}_{counter}.xml"
                    counter += 1

            # Write template content
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Write metadata to companion .meta file
            meta_file = template_path.with_suffix(".xml.meta")
            import json

            meta_data = {
                "name": name,
                "description": description,
                "created_at": str(template_path.stat().st_mtime) if template_path.exists() else "",
                "os_name": os_name,
            }
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, indent=2)

            self.logger.info(f"Saved template '{name}' to {template_path}")
            return True, str(template_path)

        except Exception as e:
            self.logger.error(f"Error saving template: {e}")
            return False, ""

    def delete_template(self, template_id: str) -> bool:
        """
        Delete a user template file.

        Args:
            template_id: Template path or UUID (legacy) to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            # Check if it's a file path
            template_path = Path(template_id)
            if template_path.exists() and template_path.suffix == ".xml":
                # Delete XML file
                template_path.unlink()

                # Delete companion .meta file if exists
                meta_file = template_path.with_suffix(".xml.meta")
                if meta_file.exists():
                    meta_file.unlink()

                self.logger.info(f"Deleted template {template_path}")
                return True

            # Legacy: try to delete from YAML config
            actual_id = (
                template_id.replace("user_", "") if template_id.startswith("user_") else template_id
            )
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

    def import_template(self, file_path: Path) -> tuple[bool, str, str]:
        """
        Import a template from a file.

        Args:
            file_path: Path to template file to import

        Returns:
            Tuple of (success: bool, content: str, error_message: str)
        """
        try:
            # Read file
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Validate XML
            is_valid, error = self.validate_xml(content)
            if not is_valid:
                return False, "", f"Invalid XML: {error}"

            self.logger.info(f"Imported template from {file_path}")
            return True, content, ""

        except FileNotFoundError:
            error = f"File not found: {file_path}"
            self.logger.error(error)
            return False, "", error
        except Exception as e:
            error = f"Error importing template: {e}"
            self.logger.error(error)
            return False, "", error

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

    def view_template_in_tmux(
        self,
        content: str,
        on_close: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> bool:
        """
        Open template content in external editor via tmux in read-only mode.

        Args:
            content: Template content to view
            on_close: Optional callback when viewer closes
            on_error: Optional callback with error message on failure

        Returns:
            True if viewing was initiated, False if tmux not available
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

                # Determine read-only flag based on editor
                if "vim" in editor or "vi" in editor:
                    readonly_flag = "-R"
                elif "nano" in editor:
                    readonly_flag = "-v"
                elif "emacs" in editor:
                    readonly_flag = "--eval '(setq buffer-read-only t)'"
                else:
                    # Fallback to less for viewing
                    editor = "less"
                    readonly_flag = ""

                # Create unique signal name
                signal_name = f"virtui-view-{os.getpid()}-{uuid.uuid4().hex[:8]}"

                # Launch editor in read-only mode in new tmux window
                cmd = f"{editor} {readonly_flag} {tmp_file_path}; tmux wait-for -S {signal_name}"
                subprocess.run(
                    ["tmux", "new-window", "-n", "template-viewer", cmd],
                    check=True,
                )

                # Wait for signal (blocks until viewer closes)
                subprocess.run(["tmux", "wait-for", signal_name], check=True)

                if on_close:
                    on_close()

                return True

            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_file_path)
                except OSError:
                    pass

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Viewer process failed: {e}")
            if on_error:
                on_error(ErrorMessages.EDITOR_CANCELLED_OR_FAILED)
            return False
        except Exception as e:
            self.logger.error(f"Error in template viewer: {e}")
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
        required_sections = ["<profile", "<general>", "<software>", "<users"]
        for section in required_sections:
            if section not in content:
                warnings.append(f"Missing section: {section.strip('<')}")

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
