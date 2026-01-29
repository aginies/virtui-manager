"""
Main interface
"""
from textual.app import ComposeResult, on
from textual.widgets import (
        Button, Label,
        Checkbox, Select
        )
from textual.containers import Horizontal, Vertical, Grid
from textual.screen import ModalScreen

from .base_modals import BaseModal
from .utils_modals import LoadingModal
from ..connection_manager import ConnectionManager
from ..constants import ErrorMessages, StaticText, ButtonLabels


class SelectServerModal(BaseModal[None]):
    """Screen to select servers to connect to."""

    def __init__(self, servers, active_uris, connection_manager: ConnectionManager):
        super().__init__()
        self.servers = servers
        self.active_uris = active_uris
        self.id_to_uri_map = {}
        self.connection_manager = connection_manager

    def compose(self) -> ComposeResult:
        with Vertical(id="select-server-container", classes="info-details"):
            yield Label(StaticText.SELECT_SERVERS_TO_DISPLAY)
            
            checkboxes = []
            for i, server in enumerate(self.servers):
                is_active = server['uri'] in self.active_uris
                cb_id = f"server_cb_{i}"
                self.id_to_uri_map[cb_id] = server['uri']
                cb = Checkbox(server['name'], value=is_active, id=cb_id, tooltip=server['uri'])
                cb.styles.border = ("solid", server.get('color', "white"))
                checkboxes.append(cb)

            grid = Grid(*checkboxes, id="server-checkboxes-grid")
            grid.styles.grid_size_columns = 2
            grid.styles.height = "auto"
            grid.styles.grid_gutter_horizontal = 1
            yield grid

            with Horizontal(classes="button-details"):
                yield Button(ButtonLabels.DONE, id="done-servers", variant="primary", classes="done-button")
                yield Button(ButtonLabels.CANCEL, id="cancel-servers", classes="cancel-button")

    @on(Checkbox.Changed)
    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Handle checkbox changes to connect or disconnect from servers."""
        checkbox_id = str(event.checkbox.id)
        uri = self.id_to_uri_map.get(checkbox_id)

        if not uri:
            return

        if event.value:  # If checkbox is checked
            loading_modal = LoadingModal(ErrorMessages.CONNECTING_TO_SERVER_TEMPLATE.format(uri=uri))
            self.app.push_screen(loading_modal)

            def connect_and_update():
                conn = self.app.vm_service.connect(uri)
                self.app.call_from_thread(loading_modal.dismiss)
                if conn is None:
                    self.app.call_from_thread(
                        self.app.show_error_message,
                        ErrorMessages.FAILED_TO_CONNECT_TO_SERVER_TEMPLATE.format(uri=uri)
                    )
                    # Revert checkbox state on failure
                    checkbox = self.query(f"#{checkbox_id}").first()
                    self.app.call_from_thread(setattr, checkbox, "value", False)
                else:
                    if uri not in self.active_uris:
                        self.active_uris.append(uri)

            self.app.worker_manager.run(
                connect_and_update, name=f"connect_server_{uri}", exclusive=True
            )
        else:  # If checkbox is unchecked
            # Disconnect from the server
            self.connection_manager.disconnect(uri)
            # Remove URI from active_uris if it exists
            if uri in self.active_uris:
                self.active_uris.remove(uri)

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "done-servers":
            self.dismiss(self.active_uris)
        elif event.button.id == "cancel-servers":
            self.dismiss(None)

class SelectOneServerModal(BaseModal[str]):
    def __init__(self, servers: list[dict], title: str = "Select a server", button_label: str = "Launch"):
        super().__init__()
        self.servers = servers
        self.server_options = [(s['name'], s['uri']) for s in servers]
        self.title_text = title
        self.button_label = button_label

    def compose(self) -> ComposeResult:
        with Vertical(id="select-one-server-container"):
            yield Label(self.title_text)
            yield Select(self.server_options, prompt="Select server...", id="server-select")
            yield Label(StaticText.EMPTY_LABEL)
            with Horizontal():
                yield Button(self.button_label, id="launch-btn", variant="primary", disabled=True)
                yield Button(ButtonLabels.CANCEL, id="cancel-btn")

    @on(Select.Changed, "#server-select")
    def on_server_select_changed(self, event: Select.Changed) -> None:
        self.query_one("#launch-btn", Button).disabled = not event.value

    @on(Button.Pressed, "#launch-btn")
    def on_launch_button_pressed(self) -> None:
        select = self.query_one("#server-select", Select)
        if select.value:
            self.dismiss(select.value)

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel_button_pressed(self) -> None:
        self.dismiss()
