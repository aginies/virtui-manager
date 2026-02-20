"""
Modals for AutoYaST Template Management.

Provides a unified interface for managing installation templates including:
- Viewing all templates (built-in and user-defined)
- Creating new templates
- Editing existing templates
- Viewing template content (read-only)
- Deleting user templates
- Exporting templates to files
"""

import logging
from pathlib import Path

from textual import on
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, DataTable, Input, Label, Select, Static

from ..constants import ButtonLabels, ErrorMessages, StaticText
from .base_modals import BaseModal
from .utils_modals import FileSelectionModal


class TemplateNameModal(BaseModal[dict | None]):
    """
    Modal for entering template name, description, and OS type.

    Returns dict with 'name', 'description', and 'os_name' keys if submitted,
    None if cancelled.
    """

    # Supported OS types for template organization
    SUPPORTED_OS = [
        ("openSUSE", "openSUSE"),
        ("SLES", "SUSE Linux Enterprise Server"),
        ("Ubuntu", "Ubuntu"),
        ("Debian", "Debian"),
        ("Fedora", "Fedora"),
        ("RHEL", "Red Hat Enterprise Linux"),
        ("CentOS", "CentOS"),
        ("Windows", "Windows"),
        ("Generic", "Generic/Other"),
    ]

    def __init__(
        self,
        initial_name: str = "",
        initial_description: str = "",
        initial_os: str = "openSUSE",
    ):
        """
        Initialize the template name modal.

        Args:
            initial_name: Initial template name
            initial_description: Initial template description
            initial_os: Initial OS selection
        """
        super().__init__()
        self.initial_name = initial_name
        self.initial_description = initial_description
        self.initial_os = initial_os

    def compose(self):
        """Compose the modal UI."""
        with Container(id="template-name-container"):
            yield Label(StaticText.TEMPLATE_NAME_TITLE, classes="title")

            yield Label(StaticText.TEMPLATE_NAME_LABEL, classes="label")
            yield Input(
                value=self.initial_name,
                placeholder=StaticText.TEMPLATE_NAME_PLACEHOLDER,
                id="template-name-input",
            )

            yield Label(StaticText.TEMPLATE_OS_LABEL, classes="label")
            yield Select(
                options=[(label, value) for value, label in self.SUPPORTED_OS],
                value=self.initial_os,
                id="template-os-select",
            )

            yield Label(StaticText.TEMPLATE_DESCRIPTION_LABEL, classes="label")
            yield Input(
                value=self.initial_description,
                placeholder=StaticText.TEMPLATE_DESCRIPTION_PLACEHOLDER,
                id="template-description-input",
            )

            with Horizontal(classes="buttons"):
                yield Button(ButtonLabels.OK, id="ok-btn", variant="primary")
                yield Button(ButtonLabels.CANCEL, id="cancel-btn")

    def on_mount(self):
        """Focus the name input when mounted."""
        self.query_one("#template-name-input", Input).focus()

    @on(Button.Pressed, "#ok-btn")
    def submit_name(self, event: Button.Pressed):
        """Submit the template name, OS, and description."""
        name_input = self.query_one("#template-name-input", Input)
        os_select = self.query_one("#template-os-select", Select)
        description_input = self.query_one("#template-description-input", Input)

        name = name_input.value.strip()
        os_name = os_select.value if os_select.value != Select.BLANK else "Generic"
        description = description_input.value.strip()

        if not name:
            self.notify(StaticText.TEMPLATE_NAME_REQUIRED, severity="error")
            name_input.focus()
            return

        self.dismiss({"name": name, "os_name": os_name, "description": description})

    @on(Button.Pressed, "#cancel-btn")
    def cancel_name(self, event: Button.Pressed):
        """Cancel template naming."""
        self.dismiss(None)


class TemplateManagementModal(BaseModal[bool]):
    """
    Modal for managing AutoYaST templates.

    Displays a list of all available templates (built-in and user-defined)
    with actions: Create New, Edit, View, Delete, Export.

    Uses Option A design: Template list + action buttons.
    """

    def __init__(self, template_manager):
        """
        Initialize the template management modal.

        Args:
            template_manager: AutoYaSTTemplateManager instance
        """
        super().__init__()
        self.template_manager = template_manager
        self.templates = []
        self.selected_template_id = None

    def compose(self):
        """Compose the modal UI."""
        with ScrollableContainer(id="template-management-container"):
            yield Label(StaticText.MANAGE_TEMPLATES_TITLE, classes="title")
            yield Label(StaticText.TEMPLATE_LIST_HEADER, classes="label")

            # Template list table
            yield DataTable(id="template-table", cursor_type="row", zebra_stripes=True)

            # Action buttons
            with Horizontal(classes="buttons"):
                yield Button(
                    ButtonLabels.CREATE,
                    id="create-new-template-btn",
                    variant="primary",
                )
                yield Button(
                    StaticText.IMPORT_BUTTON,
                    id="import-template-btn",
                    variant="success",
                )
                yield Button(
                    ButtonLabels.EDIT,
                    id="edit-template-btn",
                    disabled=True,
                )
                yield Button(
                    ButtonLabels.VIEW,
                    id="view-template-btn",
                    disabled=True,
                )
                yield Button(
                    ButtonLabels.DELETE,
                    id="delete-template-btn",
                    disabled=True,
                )
                yield Button(
                    "Export",
                    id="export-template-btn",
                    disabled=True,
                )
                yield Button(
                    ButtonLabels.CLOSE,
                    id="close-template-mgmt-btn",
                    variant="default",
                )

    def on_mount(self):
        """Called when modal is mounted - load and display templates."""
        self._load_templates()

    def _load_templates(self):
        """Load all templates and populate the table."""
        try:
            # Get all templates from manager
            self.templates = self.template_manager.get_all_templates()

            # Set up table columns
            table = self.query_one("#template-table", DataTable)
            table.clear(columns=True)

            table.add_column(StaticText.TEMPLATE_NAME_COLUMN, key="name")
            table.add_column(StaticText.TEMPLATE_TYPE_COLUMN, key="type")
            table.add_column(StaticText.TEMPLATE_DESCRIPTION_COLUMN, key="description")

            # Add rows
            if not self.templates:
                # Show empty state
                table.add_row(
                    StaticText.NO_TEMPLATES_AVAILABLE,
                    "",
                    "",
                    key="empty",
                )
            else:
                for template in self.templates:
                    # Use template_id as key for file-based templates, filename for built-in
                    row_key = template.get("template_id", template["filename"])
                    table.add_row(
                        template["display_name"],
                        template["type"],
                        template.get("description", ""),
                        key=row_key,
                    )

            logging.info(f"Loaded {len(self.templates)} templates")

        except Exception as e:
            logging.error(f"Error loading templates: {e}")
            self.notify(f"Error loading templates: {e}", severity="error")

    @on(DataTable.RowSelected, "#template-table")
    def on_template_selected(self, event: DataTable.RowSelected):
        """Handle template selection in the table."""
        if event.row_key.value == "empty":
            return

        # Store selected template ID (filename)
        self.selected_template_id = str(event.row_key.value)

        # Enable/disable buttons based on selection
        self._update_button_states()

    def _update_button_states(self):
        """Update button enabled/disabled states based on current selection."""
        try:
            has_selection = self.selected_template_id is not None

            # Edit and View always enabled if something is selected
            self.query_one("#edit-template-btn", Button).disabled = not has_selection
            self.query_one("#view-template-btn", Button).disabled = not has_selection
            self.query_one("#export-template-btn", Button).disabled = not has_selection

            # Delete only enabled for user templates
            if has_selection:
                is_user_template = self.template_manager.is_user_template(self.selected_template_id)
                self.query_one("#delete-template-btn", Button).disabled = not is_user_template
            else:
                self.query_one("#delete-template-btn", Button).disabled = True

        except Exception as e:
            logging.error(f"Error updating button states: {e}")

    @on(Button.Pressed, "#create-new-template-btn")
    def create_new_template(self, event: Button.Pressed):
        """Create a new template using external editor."""
        try:
            # Show name dialog first
            def on_name_dismiss(result: dict | None):
                if result:
                    self._edit_template_with_editor(
                        None,
                        is_new=True,
                        name=result["name"],
                        description=result["description"],
                        os_name=result["os_name"],
                    )

            self.app.push_screen(TemplateNameModal(), on_name_dismiss)
        except Exception as e:
            logging.error(f"Error creating template: {e}")
            self.notify("Error creating template", severity="error")

    @on(Button.Pressed, "#edit-template-btn")
    def edit_template(self, event: Button.Pressed):
        """Edit the selected template using external editor."""
        if not self.selected_template_id:
            self.notify(StaticText.SELECT_TEMPLATE_TO_EDIT, severity="warning")
            return

        try:
            # Get current template info
            template = self.template_manager.get_template(self.selected_template_id)
            if not template:
                self.notify("Template not found", severity="error")
                return

            current_name = (
                template["display_name"]
                .replace(" (User)", "")
                .replace(f" ({template.get('os_name', 'Generic')})", "")
            )
            current_description = template.get("description", "")
            current_os = template.get("os_name", "openSUSE")

            # Show name dialog for editing (allow renaming)
            def on_name_dismiss(result: dict | None):
                if result:
                    self._edit_template_with_editor(
                        self.selected_template_id,
                        is_new=False,
                        name=result["name"],
                        description=result["description"],
                        os_name=result["os_name"],
                    )

            self.app.push_screen(
                TemplateNameModal(
                    initial_name=current_name,
                    initial_description=current_description,
                    initial_os=current_os,
                ),
                on_name_dismiss,
            )
        except Exception as e:
            logging.error(f"Error editing template: {e}")
            self.notify("Error editing template", severity="error")

    @on(Button.Pressed, "#view-template-btn")
    def view_template(self, event: Button.Pressed):
        """View the selected template using external viewer (tmux/EDITOR)."""
        if not self.selected_template_id:
            self.notify(StaticText.SELECT_TEMPLATE_TO_VIEW, severity="warning")
            return

        try:
            # Get template content
            content = self.template_manager.get_template_content(self.selected_template_id)
            if not content:
                self.notify("Could not load template content", severity="error")
                return

            # Check if tmux is available
            can_view, error = self.template_manager.can_edit_externally()
            if not can_view:
                # Fallback to custom modal if tmux not available
                template = self.template_manager.get_template(self.selected_template_id)
                self.app.push_screen(
                    TemplateViewModal(
                        template_name=template["display_name"],
                        content=content,
                    )
                )
                return

            # Use tmux/EDITOR for viewing
            def on_close():
                self.notify("Template viewer closed")

            def on_error(error_msg: str):
                self.notify(error_msg, severity="error")

            self.template_manager.view_template_in_tmux(
                content=content,
                on_close=on_close,
                on_error=on_error,
            )

        except Exception as e:
            logging.error(f"Error viewing template: {e}")
            self.notify("Error viewing template", severity="error")

    @on(Button.Pressed, "#delete-template-btn")
    def delete_template(self, event: Button.Pressed):
        """Delete the selected user template."""
        if not self.selected_template_id:
            self.notify(StaticText.SELECT_TEMPLATE_TO_DELETE, severity="warning")
            return

        try:
            # Check if it's a user template
            if not self.template_manager.is_user_template(self.selected_template_id):
                self.notify(StaticText.CANNOT_DELETE_BUILTIN_TEMPLATE, severity="warning")
                return

            # Get template name for display
            template = self.template_manager.get_template(self.selected_template_id)
            template_name = (
                template["display_name"].replace(" (User)", "") if template else "template"
            )

            # Extract template ID and delete
            template_id = (
                self.selected_template_id.replace("user_", "")
                if self.selected_template_id.startswith("user_")
                else self.selected_template_id
            )

            if self.template_manager.delete_template(template_id):
                self.notify(
                    StaticText.TEMPLATE_DELETED_SUCCESSFULLY.format(template_name=template_name)
                )
                # Reload templates
                self.selected_template_id = None
                self._load_templates()
                self._update_button_states()
            else:
                self.notify("Error deleting template", severity="error")

        except Exception as e:
            logging.error(f"Error deleting template: {e}")
            self.notify("Error deleting template", severity="error")

    @on(Button.Pressed, "#export-template-btn")
    def export_template(self, event: Button.Pressed):
        """Export the selected template to a file."""
        if not self.selected_template_id:
            self.notify(StaticText.SELECT_TEMPLATE_TO_EXPORT, severity="warning")
            return

        try:
            # Export to user's home directory
            export_dir = Path.home()
            success, exported_path = self.template_manager.export_template(
                self.selected_template_id, export_dir
            )

            if success:
                self.notify(StaticText.TEMPLATE_EXPORTED_TO.format(path=exported_path))
            else:
                self.notify("Error exporting template", severity="error")

        except Exception as e:
            logging.error(f"Error exporting template: {e}")
            self.notify("Error exporting template", severity="error")

    @on(Button.Pressed, "#import-template-btn")
    def import_template(self, event: Button.Pressed):
        """Import a template from a file using file browser."""
        try:
            # Step 1: Show file browser to select XML file
            def on_file_selected(file_path: str | None):
                if not file_path:
                    return

                # Validate file extension
                if not file_path.endswith(".xml"):
                    self.notify("Please select an XML file", severity="error")
                    return

                # Step 2: Import the file
                success, content, error = self.template_manager.import_template(Path(file_path))
                if not success:
                    self.notify(f"Import failed: {error}", severity="error")
                    return

                # Step 3: Show naming dialog with pre-filled name from filename
                filename = Path(file_path).stem  # Get filename without extension

                def on_name_dismiss(result: dict | None):
                    if not result:
                        return

                    # Save imported template with user-provided name and OS
                    save_success, saved_id = self.template_manager.save_template(
                        name=result["name"],
                        content=content,
                        description=result.get("description", "Imported template"),
                        os_name=result.get("os_name", "Generic"),
                    )

                    if save_success:
                        self.notify(
                            StaticText.TEMPLATE_IMPORTED_SUCCESSFULLY.format(
                                template_name=result["name"]
                            )
                        )
                        self.selected_template_id = saved_id
                        self._load_templates()
                        self._update_button_states()
                    else:
                        self.notify("Failed to save imported template", severity="error")

                # Show naming dialog with filename as initial name
                self.app.push_screen(TemplateNameModal(initial_name=filename), on_name_dismiss)

            # Show file browser (start at home directory)
            self.app.push_screen(FileSelectionModal(path=str(Path.home())), on_file_selected)

        except Exception as e:
            logging.error(f"Error importing template: {e}")
            self.notify("Error importing template", severity="error")

    @on(Button.Pressed, "#close-template-mgmt-btn")
    def close_modal(self, event: Button.Pressed):
        """Close the modal."""
        self.dismiss(True)

    def _edit_template_with_editor(
        self,
        template_id: str | None,
        is_new: bool,
        name: str = "",
        description: str = "",
        os_name: str = "openSUSE",
    ):
        """
        Edit a template using external editor via tmux.

        Args:
            template_id: Template identifier (None for new template)
            is_new: True if creating new template, False if editing existing
            name: Template name (for new templates)
            description: Template description (for new templates)
            os_name: Operating system name (for directory organization)
        """
        # Check tmux availability
        can_edit, error = self.template_manager.can_edit_externally()
        if not can_edit:
            self.notify(error or ErrorMessages.TMUX_REQUIRED_FOR_TEMPLATE_EDITING, severity="error")
            return

        # Get initial content
        if is_new:
            content = self.template_manager.get_skeleton_template()
        else:
            content = self.template_manager.get_template_content(template_id)
            if not content:
                self.notify("Could not load template content", severity="error")
                return

        # Define callbacks
        def on_save(edited_content: str):
            """Handle template save."""
            # Validate XML
            is_valid, error_msg = self.template_manager.validate_xml(edited_content)
            if not is_valid:
                self.app.show_error_message(f"Invalid XML: {error_msg}")
                return

            # Get template name and description
            if is_new:
                template_name = (
                    name
                    if name
                    else f"Custom Template {len([t for t in self.templates if t['type'] == 'user']) + 1}"
                )
                template_description = description if description else "User-defined template"
                template_os = os_name
            else:
                # Use provided name/description/os when editing
                template_name = name if name else "template"
                template_description = description if description else "User-defined template"
                template_os = os_name

            # Save template
            success, saved_id = self.template_manager.save_template(
                name=template_name,
                content=edited_content,
                description=template_description,
                template_id=template_id if not is_new else None,
                os_name=template_os,
            )

            if success:
                action = "created" if is_new else "updated"
                self.app.show_success_message(f"Template '{template_name}' {action} successfully!")
                # Reload templates
                self.selected_template_id = saved_id  # saved_id is now the file path
                self._load_templates()
                self._update_button_states()
            else:
                self.app.show_error_message("Failed to save template")

        def on_cancel():
            """Handle template edit cancellation."""
            self.notify("Template editing cancelled")

        def on_error(error: str):
            """Handle template edit error."""
            self.notify(error, severity="error")

        # Open editor
        self.template_manager.edit_template_in_tmux(
            content=content,
            on_save=on_save,
            on_cancel=on_cancel,
            on_error=on_error,
        )


class TemplateViewModal(BaseModal[None]):
    """
    Read-only modal for viewing template content.
    """

    def __init__(self, template_name: str, content: str):
        """
        Initialize the template view modal.

        Args:
            template_name: Name of the template being viewed
            content: Template XML content
        """
        super().__init__()
        self.template_name = template_name
        self.content = content

    def compose(self):
        """Compose the modal UI."""
        with ScrollableContainer(id="template-view-container"):
            yield Label(
                StaticText.VIEW_TEMPLATE_TITLE.format(template_name=self.template_name),
                classes="title",
            )

            # Display content in a Static widget (read-only)
            with Container(id="template-content-container"):
                yield Static(self.content, id="template-content")

            # Close button
            with Horizontal(classes="buttons"):
                yield Button(ButtonLabels.CLOSE, id="close-view-btn", variant="primary")

    @on(Button.Pressed, "#close-view-btn")
    def close_modal(self, event: Button.Pressed):
        """Close the view modal."""
        self.dismiss(None)
