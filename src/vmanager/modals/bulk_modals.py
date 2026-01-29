"""
Modal for bulk VM operations
"""
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual import on
from textual.widgets import Label, Button, Markdown, Static, RadioSet, RadioButton, Checkbox

from .base_modals import BaseModal
from ..constants import ErrorMessages, StaticText, ButtonLabels

class BulkActionModal(BaseModal[None]):
    """Modal screen for performing bulk actions on selected VMs."""

    def __init__(self, vm_names: list[str]) -> None:
        super().__init__()
        self.vm_names = vm_names

    def compose(self) -> ComposeResult:
        with Vertical(id="bulk-action-dialog"):
            yield Label(StaticText.SELECTED_VMS_BULK)
            yield Static(classes="button-separator")
            with ScrollableContainer():
                all_vms = ", ".join(self.vm_names)
                yield Markdown(all_vms, id="selected-vms-list")

            yield Label(StaticText.CHOOSE_ACTION)
            with RadioSet(id="bulk-action-radioset"):
                yield RadioButton(StaticText.START_VMS, id="action_start")
                yield RadioButton(StaticText.STOP_VMS_GRACEFUL, id="action_stop")
                yield RadioButton(StaticText.FORCE_OFF_VMS, id="action_force_off")
                yield RadioButton(StaticText.PAUSE_VMS, id="action_pause")
                yield RadioButton(StaticText.DELETE_VMS, id="action_delete")
                yield RadioButton(StaticText.EDIT_CONFIGURATION, id="action_edit_config")

            yield Checkbox(StaticText.DELETE_ASSOCIATED_STORAGE, id="delete-storage-checkbox")

            with Horizontal():
                yield Button(ButtonLabels.EXECUTE, variant="primary", id="execute-action-btn", classes="button-container")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn", classes="button-container")

    def on_mount(self) -> None:
        """Called when the modal is mounted to initially hide the checkbox."""
        self.query_one("#delete-storage-checkbox").display = False

    @on(RadioSet.Changed, "#bulk-action-radioset")
    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Shows/hides the delete storage checkbox based on radio button selection."""
        checkbox = self.query_one("#delete-storage-checkbox")
        checkbox.display = event.pressed.id == "action_delete"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "execute-action-btn":
            radioset = self.query_one(RadioSet)
            selected_action_button = radioset.pressed_button
            if selected_action_button:
                action = selected_action_button.id.replace("action_", "")
                result = {'action': action}
                if action == 'delete':
                    checkbox = self.query_one("#delete-storage-checkbox")
                    result['delete_storage'] = checkbox.value
                self.dismiss(result)
            else:
                self.app.show_error_message(ErrorMessages.PLEASE_SELECT_ACTION)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)
