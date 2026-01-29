"""
Server management 
"""
import logging
from typing import Tuple
from textual.app import ComposeResult
from textual.widgets import Button, Label, DataTable, Input, Checkbox
from textual.containers import ScrollableContainer, Horizontal, Vertical
from ..vmcard import ConfirmationDialog
from .howto_ssh_modal import HowToSSHModal
from .base_modals import BaseModal

from ..config import save_config
from ..constants import ErrorMessages, SuccessMessages, ButtonLabels, StaticText

class ConnectionModal(BaseModal[str | None]):

    def compose(self) -> ComposeResult:
        with Vertical(id="connection-dialog"):
            yield Label(StaticText.ENTER_QEMU_CONNECTION_URI)
            yield Input(
                placeholder="qemu+ssh://user@host/system or qemu:///system",
                id="uri-input",
            )
            with Horizontal():
                yield Button(ButtonLabels.CONNECT, variant="primary", id="connect-btn", classes="Buttonpage")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn", classes="Buttonpage")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "connect-btn":
            uri_input = self.query_one("#uri-input", Input)
            self.dismiss(uri_input.value)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)


class AddServerModal(BaseModal[Tuple[str, str] | None]):
    """Modal for adding a new server with autoconnect option."""
    def compose(self) -> ComposeResult:
        with Vertical(id="add-server-dialog"):
            yield Label(StaticText.ADD_NEW_SERVER)
            yield Input(placeholder="Server Name", id="server-name-input")
            yield Input(placeholder="qemu+ssh://user@host/system", id="server-uri-input")
            yield Label(StaticText.EMPTY_LABEL)
            yield Checkbox(StaticText.AUTOCONNECT_AT_STARTUP, id="autoconnect-checkbox", value=False)
            with Horizontal():
                yield Button(ButtonLabels.SAVE, variant="primary", id="save-btn", classes="Buttonpage")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn", classes="Buttonpage")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            name_input = self.query_one("#server-name-input", Input)
            uri_input = self.query_one("#server-uri-input", Input)
            autoconnect_checkbox = self.query_one("#autoconnect-checkbox", Checkbox)
            self.dismiss((
                name_input.value,
                uri_input.value,
                autoconnect_checkbox.value,
                ))
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

class EditServerModal(BaseModal[Tuple[str, str, bool] | None]):

    def __init__(self, server_name: str, server_uri: str, autoconnect: bool = False) -> None:
        super().__init__()
        self.server_name = server_name
        self.server_uri = server_uri
        self.autoconnect = autoconnect

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-server-dialog"):
            yield Label(StaticText.EDIT_SERVER)
            yield Input(value=self.server_name, id="server-name-input")
            yield Input(value=self.server_uri, id="server-uri-input")
            yield Label(StaticText.EMPTY_LABEL)
            yield Checkbox(StaticText.AUTOCONNECT_AT_STARTUP, id="autoconnect-checkbox", value=self.autoconnect)
            with Horizontal():
                yield Button(ButtonLabels.SAVE, variant="primary", id="save-btn", classes="Buttonpage")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn", classes="Buttonpage")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            name_input = self.query_one("#server-name-input", Input)
            uri_input = self.query_one("#server-uri-input", Input)
            autoconnect_checkbox = self.query_one("#autoconnect-checkbox", Checkbox)
            self.dismiss((
                name_input.value,
                uri_input.value,
                autoconnect_checkbox.value
                ))
        elif event.button.id == "cancel-btn":
            self.dismiss(None)


class ServerManagementModal(BaseModal [str | None]):
    """Modal screen for managing servers."""

    def __init__(self, servers: list) -> None:
        super().__init__()
        self.servers = servers
        self.selected_row = None

    def compose(self) -> ComposeResult:
        with Vertical(id="server-management-dialog"): #, classes="info-details"):
            yield Label(StaticText.SERVER_LIST_MANAGEMENT) #, id="server-list-title")
            with ScrollableContainer(classes="info-details"):
                yield DataTable(id="server-table", classes="server-list")
            with Vertical(classes="server-list"):
                with Horizontal():
                    yield Button(ButtonLabels.ADD, id="add-server-btn", classes="add-button", variant="success")
                    yield Button(ButtonLabels.EDIT, id="edit-server-btn", disabled=True, classes="edit-button")
                    yield Button(ButtonLabels.DELETE, id="delete-server-btn", disabled=True,)
                with Horizontal():
                    yield Button(ButtonLabels.CONNECT, id="select-btn", variant="primary", disabled=True, classes="Buttonpage")
                    yield Button(ButtonLabels.CUSTOM_URL, id="custom-conn-btn", classes="Buttonpage")
                    yield Button(ButtonLabels.SSH_HELP, id="ssh-help-btn", classes="Buttonpage")
             #yield Button("Close", id="close-btn", classes="close-button")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_column("Name", key="name")
        table.add_column("URI", key="uri")
        table.add_column("Autoconnect", key="autoconnect")
        for idx, server in enumerate(self.servers):
            autoconnect_display = "✓" if server.get('autoconnect', False) else ""
            table.add_row(server['name'], server['uri'], autoconnect_display, key=str(idx))
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_row = event.cursor_row
        self.query_one("#edit-server-btn").disabled = False
        self.query_one("#delete-server-btn").disabled = False
        self.query_one("#select-btn").disabled = False

    def _reload_table(self):
        table = self.query_one(DataTable)
        table.clear()
        for idx, server in enumerate(self.servers):
            autoconnect_display = "✓" if server.get('autoconnect', False) else ""
            table.add_row(server['name'], server['uri'], autoconnect_display, key=str(idx))
        table.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss(self.servers)
        elif event.button.id == "select-btn":
            if self.selected_row is not None:
                server_uri = self.servers[self.selected_row]['uri']
                self.dismiss(server_uri)
        elif event.button.id == "add-server-btn":
            def add_server_callback(result):
                if result:
                    name, uri, autoconnect = result
                    self.servers.append({
                        'name': name,
                        'uri': uri,
                        'autoconnect': autoconnect
                        })
                    self.app.config['servers'] = self.servers
                    save_config(self.app.config)
                    self._reload_table()
            self.app.push_screen(AddServerModal(), add_server_callback)
        elif event.button.id == "edit-server-btn" and self.selected_row is not None:
            server_to_edit = self.servers[self.selected_row]
            def edit_server_callback(result):
                if result:
                    new_name, new_uri, autoconnect = result
                    self.servers[self.selected_row]['name'] = new_name
                    self.servers[self.selected_row]['uri'] = new_uri
                    self.servers[self.selected_row]['autoconnect'] = autoconnect
                    self.app.config['servers'] = self.servers
                    save_config(self.app.config)
                    self._reload_table()
            self.app.push_screen(EditServerModal(
                server_to_edit['name'],
                server_to_edit['uri'],
                server_to_edit.get('autoconnect', False)
                ),
                edit_server_callback)
        elif event.button.id == "delete-server-btn" and self.selected_row is not None:
            server_to_delete = self.servers[self.selected_row]
            server_name_to_delete = server_to_delete['name']

            def on_confirm(confirmed: bool) -> None:
                if confirmed:
                    try:
                        del self.servers[self.selected_row]
                        self.app.config['servers'] = self.servers
                        save_config(self.app.config)
                        self._reload_table()
                        self.selected_row = None
                        self.query_one("#edit-server-btn").disabled = True
                        self.query_one("#delete-server-btn").disabled = True
                        self.query_one("#select-btn").disabled = True
                        self.app.show_success_message(SuccessMessages.SERVER_DELETED_TEMPLATE.format(server_name=server_name_to_delete))
                        logging.info(f"Successfully deleted Server '{server_name_to_delete}'")
                    except Exception as e:
                        self.app.show_error_message(ErrorMessages.ERROR_DELETING_SERVER_TEMPLATE.format(server_name=server_name_to_delete, error=e))

            self.app.push_screen(
                ConfirmationDialog(ErrorMessages.DELETE_SERVER_CONFIRMATION_TEMPLATE.format(server_name=server_name_to_delete)), on_confirm)

        elif event.button.id == "custom-conn-btn":
            def connection_callback(uri: str | None):
                if uri:
                    self.dismiss(uri)
            self.app.push_screen(ConnectionModal(), connection_callback)

        elif event.button.id == "ssh-help-btn":
            self.app.push_screen(HowToSSHModal())


    def action_close_modal(self) -> None:
        """Close the modal."""
        self.dismiss(self.servers)
