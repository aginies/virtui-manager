"""
Dialog box for VMcard
"""

from datetime import datetime
from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Horizontal, Vertical, Grid
from textual.widgets import (
        Button, Label, Checkbox, Select, Input, ListView, ListItem,
        Switch, Markdown, DataTable
        )
from modals.utils_modals import (
    BaseModal, BaseDialog, show_warning_message
)
from config import load_config, save_config
from constants import ButtonLabels, ButtonIds
from vm_queries import is_qemu_agent_running
from modals.input_modals import _sanitize_input

class DeleteVMConfirmationDialog(BaseDialog[tuple[bool, bool]]):
    """A dialog to confirm VM deletion with an option to delete storage."""

    def __init__(self, vm_name: str) -> None:
        super().__init__()
        self.vm_name = vm_name

    def compose(self):
        yield Vertical(
            Markdown(f"Are you sure you want to delete VM '{self.vm_name}'?", id="question"),
            Checkbox("Delete storage volumes", id="delete-storage-checkbox", value=True),
            Label(""),
            Horizontal(
                Button(ButtonLabels.YES, variant="error", id=ButtonIds.YES, classes="dialog-buttons"),
                Button(ButtonLabels.NO, variant="primary", id=ButtonIds.NO, classes="dialog-buttons"),
                id="dialog-buttons",
            ),
            id="delete-vm-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == ButtonIds.YES:
            delete_storage = self.query_one("#delete-storage-checkbox", Checkbox).value
            self.dismiss((True, delete_storage))
        else:
            self.dismiss((False, False))

    def action_cancel_modal(self) -> None:
        """Cancel the modal."""
        self.dismiss((False, False))

class ChangeNetworkDialog(BaseDialog[dict | None]):
    """A dialog to change a VM's network interface."""

    def __init__(self, interfaces: list[dict], networks: list[str]) -> None:
        super().__init__()
        self.interfaces = interfaces
        self.networks = networks

    def compose(self):
        interface_options = [(f"{iface['mac']} ({iface['network']})", iface['mac']) for iface in self.interfaces]
        network_options = [(str(net), str(net)) for net in self.networks]

        with Vertical(id="dialog"):
            yield Label("Select interface and new network")
            yield Select(interface_options, id="interface-select")
            yield Select(network_options, id="network-select")
            with Horizontal(id="dialog-buttons"):
                yield Button(ButtonLabels.CHANGE, variant="success", id=ButtonIds.CHANGE)
                yield Button(ButtonLabels.CANCEL, variant="error", id=ButtonIds.CANCEL)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == ButtonIds.CHANGE:
            interface_select = self.query_one("#interface-select", Select)
            network_select = self.query_one("#network-select", Select)

            mac_address = interface_select.value
            new_network = network_select.value

            if mac_address is Select.BLANK or new_network is Select.BLANK:
                self.app.show_error_message("Please select an interface and a network.")
                return

            self.dismiss({"mac_address": mac_address, "new_network": new_network})
        else:
            self.dismiss(None)

class AdvancedCloneDialog(BaseDialog[dict | None]):
    """A dialog to ask for a new VM name and number of clones."""

    def compose(self):
        yield Grid(
            Label("Enter base name for new VM(s)"),
            Input(placeholder="new_vm_base_name", id="base_name_input", restrict=r"[a-zA-Z0-9_-]*"),
            Label("Suffix for clone names (e.g., _C)"),
            Input(placeholder="e.g., -clone", id="clone_suffix_input", restrict=r"[a-zA-Z0-9_-]*"),
            Label("Number of clones to create"),
            Input(value="1", id="clone_count_input", type="integer"),
            Label("Do Not Clone storage"),
            Checkbox("", id="skip_storage_checkbox", value=False),
            Button(ButtonLabels.CLONE, variant="success", id=ButtonIds.CLONE),
            Button(ButtonLabels.CANCEL, variant="error", id=ButtonIds.CANCEL),
            id="clone-dialog"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == ButtonIds.CLONE:
            base_name_input = self.query_one("#base_name_input", Input)
            clone_count_input = self.query_one("#clone_count_input", Input)
            clone_suffix_input = self.query_one("#clone_suffix_input", Input)
            skip_storage_checkbox = self.query_one("#skip_storage_checkbox", Checkbox)

            base_name_raw = base_name_input.value
            clone_count_str = clone_count_input.value.strip()
            clone_suffix_raw = clone_suffix_input.value

            try:
                base_name, base_name_modified = _sanitize_input(base_name_raw)
                if base_name_modified:
                    self.app.show_success_message(f"Base name sanitized: '{base_name_raw}' changed to '{base_name}'")
            except ValueError as e:
                self.app.show_error_message(str(e))
                return

            if not base_name:
                self.app.show_error_message("Base name cannot be empty.")
                return
            
            # Sanitize suffix only if it's provided, otherwise keep it empty string
            clone_suffix = ""
            if clone_suffix_raw:
                try:
                    clone_suffix, suffix_modified = _sanitize_input(clone_suffix_raw)
                    if suffix_modified:
                        self.app.show_success_message(f"Suffix sanitized: '{clone_suffix_raw}' changed to '{clone_suffix}'")
                except ValueError as e:
                    self.app.show_error_message(f"Invalid characters in suffix: {e}")
                    return

            try:
                clone_count = int(clone_count_str)
                if clone_count < 1:
                    raise ValueError()
            except ValueError:
                self.app.show_error_message("Number of clones must be a positive integer.")
                return

            if clone_count > 1 and not clone_suffix:
                self.app.show_error_message("Suffix is mandatory when creating multiple clones.")
                return

            clone_storage = not skip_storage_checkbox.value

            self.dismiss({
                "base_name": base_name,
                "count": clone_count,
                "suffix": clone_suffix,
                "clone_storage": clone_storage
                })
        else:
            self.dismiss(None)


class RenameVMDialog(BaseDialog[str | None]):
    """A dialog to ask for a new VM name when renaming."""

    def __init__(self, current_name: str) -> None:
        super().__init__()
        self.current_name = current_name

    def compose(self):
        yield Vertical(
            Label(f"Current name: {self.current_name}"),
            Label("Enter new VM name", id="question"),
            Input(placeholder="new_vm_name", restrict=r"[a-zA-Z0-9_-]*"),
            Horizontal(
                Button(ButtonLabels.RENAME, variant="success", id=ButtonIds.RENAME_BUTTON),
                Button(ButtonLabels.CANCEL, variant="error", id=ButtonIds.CANCEL),
                id="dialog-buttons",
            ),
            id="dialog",
            classes="info-container",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == ButtonIds.RENAME_BUTTON:
            input_widget = self.query_one(Input)
            new_name_raw = input_widget.value

            try:
                new_name, was_modified = _sanitize_input(new_name_raw)
            except ValueError as e:
                self.app.show_error_message(str(e))
                return

            if was_modified:
                self.app.show_success_message(f"Input sanitized: '{new_name_raw}' changed to '{new_name}'")

            if not new_name:
                self.app.show_error_message("VM name cannot be empty.")
                return

            error = self.validate_name(new_name)
            if error:
                self.app.show_error_message(error)
                return

            self.dismiss(new_name)
        else:
            self.dismiss(None)

class SelectSnapshotDialog(BaseDialog[str | None]):
    """A dialog to select a snapshot from a list."""

    def __init__(self, snapshots: list[dict], prompt: str) -> None:
        super().__init__()
        self.snapshots = snapshots
        self.prompt = prompt

    def compose(self):
        yield Vertical(
            Label(self.prompt, id="prompt-label"),
            DataTable(id="snapshot-table"),
            Button(ButtonLabels.CANCEL, variant="error", id=ButtonIds.CANCEL),
            id="dialog",
            classes="snapshot-select-dialog"
        )

    def on_mount(self) -> None:
        table = self.query_one("#snapshot-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Name", "State", "Created", "Description")
        
        for snap in self.snapshots:
            table.add_row(
                snap['name'],
                snap.get('state', 'N/A'),
                snap['creation_time'],
                snap['description'],
                key=snap['name']
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == ButtonIds.CANCEL:
            self.dismiss(None)

class SnapshotNameDialog(BaseDialog[dict | None]):
    """A dialog to ask for a snapshot name."""

    def __init__(self, domain=None) -> None:
        super().__init__()
        self.domain = domain

    def compose(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        default_name = datetime.now().strftime("snap_%Y%m%d_%H%M%S")
        agent_running = is_qemu_agent_running(self.domain) if self.domain else False

        if not agent_running and self.domain:
            show_warning_message(self.app, "QEMU Guest Agent not detected. It is recommended to pause the VM before taking a snapshot.")

        yield Vertical(
            Label(f"Current time: {now}", id="timestamp-label"),
            Label("Enter snapshot name", id="question"),
            Input(value=default_name, placeholder="snapshot_name", id="name-input", restrict=r"[a-zA-Z0-9_-]*"),
            Label("Description (optional)"),
            Input(placeholder="snapshot description", id="description-input"),
            Checkbox("Quiesce guest (requires agent)",
                     value=agent_running,
                     disabled=not agent_running,
                     id="quiesce-checkbox",
                     tooltip="Pause the guest filesystem to ensure a clean snapshot. Requires QEMU Guest Agent to be running in the VM."),
            Horizontal(
                Button(ButtonLabels.CREATE, variant="success", id=ButtonIds.CREATE),
                Button(ButtonLabels.CANCEL, variant="error", id=ButtonIds.CANCEL),
                id="dialog-buttons",
            ),
            id="dialog",
            classes="info-container",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == ButtonIds.CREATE:
            name_input = self.query_one("#name-input", Input)
            description_input = self.query_one("#description-input", Input)
            quiesce_checkbox = self.query_one("#quiesce-checkbox", Checkbox)
            
            snapshot_name_raw = name_input.value
            description = description_input.value.strip()
            quiesce = quiesce_checkbox.value

            try:
                snapshot_name, was_modified = _sanitize_input(snapshot_name_raw)
            except ValueError as e:
                self.app.show_error_message(str(e))
                return

            if was_modified:
                self.app.show_success_message(f"Input sanitized: '{snapshot_name_raw}' changed to '{snapshot_name}'")

            if not snapshot_name:
                self.app.show_error_message("Snapshot name cannot be empty.")
                return

            error = self.validate_name(snapshot_name)
            if error:
                self.app.show_error_message(error)
                return

            self.dismiss({"name": snapshot_name, "description": description, "quiesce": quiesce})
        else:
            self.dismiss(None)

class WebConsoleDialog(BaseDialog[str | None]):
    """A dialog to show the web console URL."""

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def compose(self):
        yield Vertical(
            Markdown("**Web Console** is running at: (ctrl+click to open)"),
            Markdown(self.url),
            #Link("Open Link To a Browser", url=self.url),
            Label(""),
            Horizontal(
                Button(ButtonLabels.STOP, variant="error", id=ButtonIds.STOP),
                Button(ButtonLabels.CLOSE, variant="primary", id=ButtonIds.CLOSE),
                id="dialog-buttons",
            ),
            id="webconsole-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == ButtonIds.STOP:
            self.dismiss("stop")
        else:
            self.dismiss(None)

class WebConsoleConfigDialog(BaseDialog[bool]):
    """A dialog to configure and start the web console."""

    def __init__(self, is_remote: bool) -> None:
        super().__init__()
        self.is_remote = is_remote
        self.config = load_config()
        self.text_remote = "Run Web console on remote server. This will use a **LOT** of network bandwidth. It is recommended to **reduce quality** and enable **max compression**."

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="webconsole-config-dialog"):
            yield Label("Web Console Configuration", id="webconsole-config-title")

            if self.is_remote:
                remote_console_enabled = self.config.get('REMOTE_WEBCONSOLE', False)
                label_text = self.text_remote if remote_console_enabled else "Run Web console on local machine"
                yield Markdown(label_text, id="console-location-label")
                yield Switch(value=remote_console_enabled, id="remote-console-switch")

                with Vertical(id="remote-options") as remote_opts:
                    remote_opts.display = remote_console_enabled

                    quality_options = [(str(i), i) for i in range(10)]
                    compression_options = [(str(i), i) for i in range(10)]

                    yield Label("VNC Quality (0=low, 9=high)")
                    yield Select(quality_options, value=self.config.get('VNC_QUALITY', 0), id="quality-select")

                    yield Label("VNC Compression (0=none, 9=max)")
                    yield Select(compression_options, value=self.config.get('VNC_COMPRESSION', 9), id="compression-select")
            else:
                yield Markdown("Web console will run locally.")

            yield Button(ButtonLabels.START, variant="primary", id=ButtonIds.START)
            yield Button(ButtonLabels.CANCEL, variant="default", id=ButtonIds.CANCEL)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.control.id == "remote-console-switch":
            markdown = self.query_one("#console-location-label", Markdown)
            remote_opts = self.query_one("#remote-options")
            if event.value:
                markdown.update(self.text_remote)
                remote_opts.display = True
            else:
                markdown.update("Run Web console on local machine")
                remote_opts.display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == ButtonIds.START:
            config_changed = False
            if self.is_remote:
                remote_switch = self.query_one("#remote-console-switch", Switch)
                new_remote_value = remote_switch.value
                if self.config.get('REMOTE_WEBCONSOLE') != new_remote_value:
                    self.config['REMOTE_WEBCONSOLE'] = new_remote_value
                    config_changed = True

                if new_remote_value:
                    quality_select = self.query_one("#quality-select", Select)
                    new_quality_value = quality_select.value
                    if new_quality_value is not Select.BLANK and self.config.get('VNC_QUALITY') != new_quality_value:
                        self.config['VNC_QUALITY'] = new_quality_value
                        config_changed = True

                    compression_select = self.query_one("#compression-select", Select)
                    new_compression_value = compression_select.value
                    if new_compression_value is not Select.BLANK and self.config.get('VNC_COMPRESSION') != new_compression_value:
                        self.config['VNC_COMPRESSION'] = new_compression_value
                        config_changed = True
            else:
                # Not remote, so webconsole must be local
                if self.config.get('REMOTE_WEBCONSOLE') is not False:
                    self.config['REMOTE_WEBCONSOLE'] = False
                    config_changed = True

            if config_changed:
                save_config(self.config)
            self.dismiss(True)
        elif event.button.id == ButtonIds.CANCEL:
            self.dismiss(False)
