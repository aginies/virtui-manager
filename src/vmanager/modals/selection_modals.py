"""
Modals for VM selection.
"""
import logging
import re
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll, Grid
from textual.widgets import Button, Input, Label, DataTable, Checkbox
from textual import on
from .base_modals import BaseModal
from ..constants import StaticText, ButtonLabels

class PatternSelectModal(BaseModal[set[str] | None]):
    """Modal for selecting VMs by pattern across servers."""

    BINDINGS = [("ctrl+u", "unselect_all", "Unselect All")]

    def __init__(self, available_vms: list[dict], available_servers: list[dict], selected_servers: list[str]) -> None:
        super().__init__()
        self.available_vms = available_vms
        self.available_servers = available_servers
        self.selected_servers = selected_servers
        self.matching_uuids: set[str] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="pattern-select-container", classes="modal-container"):
            yield Label(StaticText.SELECT_VMS_BY_PATTERN, id="pattern-select-title")

            with Horizontal(classes="pattern-input-row"):
                yield Input(
                    placeholder="Search pattern (e.g. web-*)", 
                    id="pattern-input",
                    restrict=r"[a-zA-Z0-9_\-\*\?\.\^\|\$\( \[ \] \+\{\}\\]*"
                )
                yield Checkbox(StaticText.REGEX, id="regex-checkbox")

            if self.available_servers:
                #yield Label("Search in Servers:")
                checkboxes = []
                for i, server in enumerate(self.available_servers):
                    is_checked = server['uri'] in self.selected_servers
                    cb = Checkbox(server['name'], value=is_checked, id=f"server_cb_{i}", name=server['uri'])
                    cb.styles.border = ("solid", server.get('color', "white"))
                    checkboxes.append(cb)

                grid = Grid(*checkboxes, id="server-checkboxes-grid")
                grid.styles.grid_size_columns = 3
                grid.styles.height = "auto"
                grid.styles.grid_gutter_horizontal = 0
                yield grid

            yield Button(ButtonLabels.SEARCH_VMS, variant="primary", id="search-vms-btn")

            yield Label(StaticText.MATCHING_VMS, id="results-label")
            with VerticalScroll(id="results-container"):
                yield DataTable(id="results-table", cursor_type="row")

            with Horizontal(id="pattern-action-buttons"):
                yield Button(ButtonLabels.SELECT_MATCHING, variant="success", id="select-btn", disabled=True)
                yield Button(ButtonLabels.CANCEL, variant="error", id="cancel-btn")

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.add_column("Select", key="select")
        table.add_column("VM Name", key="vm_name")
        table.add_column("Server", key="server")
        self.query_one("#pattern-input").focus()

    @on(Button.Pressed, "#search-vms-btn")
    def on_search_pressed(self) -> None:
        pattern = self.query_one("#pattern-input").value
        use_regex = self.query_one("#regex-checkbox").value

        selected_uris = set()
        for checkbox in self.query("#server-checkboxes-grid Checkbox"):
            if checkbox.value:
                selected_uris.add(checkbox.name)

        table = self.query_one("#results-table", DataTable)
        table.clear()
        self.matching_uuids.clear()

        try:
            if use_regex:
                regex = re.compile(pattern, re.IGNORECASE)
                match_func = lambda name: regex.search(name)
            else:
                # Simple wildcard support: * -> .* , ? -> .
                simple_pattern = re.escape(pattern).replace(r'\*', '.*').replace(r'\?', '.')
                regex = re.compile(f"^{simple_pattern}$", re.IGNORECASE)
                # If no wildcards, do a simple substring match
                if '*' not in pattern and '?' not in pattern:
                    match_func = lambda name: pattern.lower() in name.lower()
                else:
                    match_func = lambda name: regex.match(name)

            for vm in self.available_vms:
                if vm['uri'] in selected_uris and match_func(vm['name']):
                    server_name = next((s['name'] for s in self.available_servers if s['uri'] == vm['uri']), vm['uri'])
                    table.add_row(" [X]", vm['name'], server_name, key=vm['uuid'])
                    self.matching_uuids.add(vm['uuid'])

            count = len(self.matching_uuids)
            self.query_one("#results-label").update(f"Selected VMs ({count}):")
            self.query_one("#select-btn").disabled = count == 0

        except Exception as e:
            logging.error(f"Error in pattern search: {e}")
            self.query_one("#results-label").update(f"Invalid pattern: {e}")

    @on(DataTable.RowSelected, "#results-table")
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        row_key = event.row_key.value
        table = self.query_one("#results-table", DataTable)
        if row_key in self.matching_uuids:
            self.matching_uuids.remove(row_key)
            table.update_cell(row_key, "select", " [ ]")
        else:
            self.matching_uuids.add(row_key)
            table.update_cell(row_key, "select", " [X]")

        count = len(self.matching_uuids)
        self.query_one("#results-label").update(f"Selected VMs ({count}):")
        self.query_one("#select-btn").disabled = count == 0

    def action_unselect_all(self) -> None:
        table = self.query_one("#results-table", DataTable)
        self.matching_uuids.clear()
        for row_key in table.rows:
            table.update_cell(row_key, "select", " [ ]")

        self.query_one("#results-label").update(f"Selected VMs (0):")
        self.query_one("#select-btn").disabled = True

    @on(Button.Pressed, "#select-btn")
    def on_select_pressed(self) -> None:
        self.dismiss(self.matching_uuids)

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel_pressed(self) -> None:
        self.dismiss(None)
