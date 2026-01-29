"""
Dialog box for VMcard
"""

from datetime import datetime
from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Horizontal, Vertical, Grid
from textual.widgets import (
        Button, Label, Checkbox, Select, Input,
        Switch, Markdown, DataTable
        )
from .input_modals import _sanitize_input
from .utils_modals import (
    BaseDialog
)
from ..config import load_config, save_config
from ..constants import ButtonLabels, ErrorMessages, SuccessMessages, WarningMessages, StaticText
from ..vm_queries import is_qemu_agent_running

class DeleteVMConfirmationDialog(BaseDialog[tuple[bool, bool]]):
    """A dialog to confirm VM deletion with an option to delete storage."""

    def __init__(self, vm_name: str) -> None:
        super().__init__()
        self.vm_name = vm_name

    def compose(self):
        yield Vertical(
            Markdown(f"Are you sure you want to delete VM '{self.vm_name}'?", id="question"),
            Checkbox(StaticText.DELETE_STORAGE_VOLUMES, id="delete-storage-checkbox", value=True),
            Label(""),
            Horizontal(
                Button(ButtonLabels.YES, variant="error", id="yes", classes="dialog-buttons"),
                Button(ButtonLabels.NO, variant="primary", id="no", classes="dialog-buttons"),
                id="dialog-buttons",
            ),
            id="delete-vm-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
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
            yield Label(StaticText.SELECT_INTERFACE_AND_NETWORK)
            yield Select(interface_options, id="interface-select")
            yield Select(network_options, id="network-select")
            with Horizontal(id="dialog-buttons"):
                yield Button(ButtonLabels.CHANGE, variant="success", id="change")
                yield Button(ButtonLabels.CANCEL, variant="error", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "change":
            interface_select = self.query_one("#interface-select", Select)
            network_select = self.query_one("#network-select", Select)

            mac_address = interface_select.value
            new_network = network_select.value

            if mac_address is Select.BLANK or new_network is Select.BLANK:
                self.app.show_error_message(ErrorMessages.PLEASE_SELECT_INTERFACE_AND_NETWORK)
                return

            self.dismiss({"mac_address": mac_address, "new_network": new_network})
        else:
            self.dismiss(None)

class AdvancedCloneDialog(BaseDialog[dict | None]):
    """A dialog to ask for a new VM name and number of clones."""

    def compose(self):
        yield Grid(
            Label(StaticText.ENTER_BASE_NAME),
            Input(placeholder="new_vm_base_name", id="base_name_input", restrict=r"[a-zA-Z0-9_-]*"),
            Label(StaticText.SUFFIX_FOR_CLONE_NAMES),
            Input(placeholder="e.g., -clone", id="clone_suffix_input", restrict=r"[a-zA-Z0-9_-]*"),
            Label(StaticText.NUMBER_OF_CLONES_TO_CREATE),
            Input(value="1", id="clone_count_input", type="integer"),
            Label(StaticText.DO_NOT_CLONE_STORAGE),
            Checkbox("", id="skip_storage_checkbox", value=False),
            Button(ButtonLabels.CLONE, variant="success", id="clone"),
            Button(ButtonLabels.CANCEL, variant="error", id="cancel"),
            id="clone-dialog"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "clone":
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
                    self.app.show_success_message(SuccessMessages.BASE_NAME_SANITIZED_TEMPLATE.format(original=base_name_raw, sanitized=base_name))
            except ValueError as e:
                self.app.show_error_message(ErrorMessages.SANITIZATION_ERROR_TEMPLATE.format(error=e))
                return

            if not base_name:
                self.app.show_error_message(ErrorMessages.BASE_NAME_EMPTY)
                return
            
            # Sanitize suffix only if it's provided, otherwise keep it empty string
            clone_suffix = ""
            if clone_suffix_raw:
                try:
                    clone_suffix, suffix_modified = _sanitize_input(clone_suffix_raw)
                    if suffix_modified:
                        self.app.show_success_message(SuccessMessages.INPUT_SANITIZED_TEMPLATE.format(original=clone_suffix_raw, sanitized=clone_suffix))
                except ValueError as e:
                    self.app.show_error_message(ErrorMessages.INVALID_CHARS_IN_SUFFIX.format(error=e))
                    return

            try:
                clone_count = int(clone_count_str)
                if clone_count < 1:
                    raise ValueError()
            except ValueError:
                self.app.show_error_message(ErrorMessages.CLONE_COUNT_POSITIVE_INTEGER)
                return

            if clone_count > 1 and not clone_suffix:
                self.app.show_error_message(ErrorMessages.SUFFIX_MANDATORY_FOR_MULTIPLE_CLONES)
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
            Label(StaticText.CURRENT_NAME.format(current_name=self.current_name)),
            Label(StaticText.ENTER_NEW_VM_NAME, id="question"),
            Input(placeholder="new_vm_name", restrict=r"[a-zA-Z0-9_-]*"),
            Horizontal(
                Button(ButtonLabels.RENAME, variant="success", id="rename-button"),
                Button(ButtonLabels.CANCEL, variant="error", id="cancel"),
                id="dialog-buttons",
            ),
            id="dialog",
            classes="info-container",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename-button":
            input_widget = self.query_one(Input)
            new_name_raw = input_widget.value

            try:
                new_name, was_modified = _sanitize_input(new_name_raw)
            except ValueError as e:
                self.app.show_error_message(ErrorMessages.SANITIZATION_ERROR_TEMPLATE.format(error=e))
                return

            if was_modified:
                self.app.show_success_message(SuccessMessages.INPUT_SANITIZED_TEMPLATE.format(original=new_name_raw, sanitized=new_name))

            if not new_name:
                self.app.show_error_message(ErrorMessages.VM_NAME_CANNOT_BE_EMPTY_RENAME)
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
            Button(ButtonLabels.CANCEL, variant="error", id="cancel"),
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
        if event.button.id == "cancel":
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
            self.app.show_warning_message(ErrorMessages.QEMU_GUEST_AGENT_RECOMMENDATION)

        yield Vertical(
            Label(StaticText.CURRENT_TIME.format(now=now), id="timestamp-label"),
            Label(StaticText.ENTER_SNAPSHOT_NAME, id="question"),
            Input(value=default_name, placeholder="snapshot_name", id="name-input", restrict=r"[a-zA-Z0-9_-]*"),
            Label(StaticText.DESCRIPTION_OPTIONAL),
            Input(placeholder="snapshot description", id="description-input"),
            Checkbox(StaticText.QUIESCE_GUEST,
                     value=agent_running,
                     disabled=not agent_running,
                     id="quiesce-checkbox",
                     tooltip="Pause the guest filesystem to ensure a clean snapshot. Requires QEMU Guest Agent to be running in the VM."),
            Horizontal(
                Button(ButtonLabels.CREATE, variant="success", id="create"),
                Button(ButtonLabels.CANCEL, variant="error", id="cancel"),
                id="dialog-buttons",
            ),
            id="dialog",
            classes="info-container",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            name_input = self.query_one("#name-input", Input)
            description_input = self.query_one("#description-input", Input)
            quiesce_checkbox = self.query_one("#quiesce-checkbox", Checkbox)

            snapshot_name_raw = name_input.value
            description = description_input.value.strip()
            quiesce = quiesce_checkbox.value

            try:
                snapshot_name, was_modified = _sanitize_input(snapshot_name_raw)
            except ValueError as e:
                self.app.show_error_message(ErrorMessages.SANITIZATION_ERROR_TEMPLATE.format(error=e))
                return

            if was_modified:
                self.app.show_success_message(SuccessMessages.INPUT_SANITIZED_TEMPLATE.format(original=snapshot_name_raw, sanitized=snapshot_name))

            if not snapshot_name:
                self.app.show_error_message(ErrorMessages.SNAPSHOT_NAME_CANNOT_BE_EMPTY)
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
            Markdown("**Web Console** is running at:"),
            Markdown(self.url),
            Markdown("Wesockify will handle a **single WebSocket** connection and exit. So it will be possible to **connect only ONE** time. If you disconnect you need to restart a new Web Console."),
            Label(""),
            Horizontal(
                Button(ButtonLabels.STOP, variant="error", id="stop"),
                Button(ButtonLabels.CLOSE, variant="primary", id="close"),
                id="dialog-buttons",
            ),
            id="webconsole-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "stop":
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
        with Vertical(id="webconsole-config-dialog"):
            yield Label(StaticText.WEB_CONSOLE_CONFIGURATION, id="webconsole-config-title")

            if self.is_remote:
                remote_console_enabled = self.config.get('REMOTE_WEBCONSOLE', False)
                label_text = self.text_remote if remote_console_enabled else "Run Web console on local machine"
                yield Markdown(label_text, id="console-location-label")
                with Vertical():
                    switch_widget = Switch(value=remote_console_enabled, id="remote-console-switch")
                    if remote_console_enabled:
                        switch_widget.add_class("switch-on")
                    else:
                        switch_widget.add_class("switch-off")

                    yield Grid(
                        Label(StaticText.REMOTE),
                        switch_widget,
                        id="grid-remote-local"
                        )

                with Vertical(id="remote-options") as remote_opts:
                    remote_opts.display = remote_console_enabled

                    quality_options = [(str(i), i) for i in range(10)]
                    compression_options = [(str(i), i) for i in range(10)]

                    yield Grid(
                        Label("VNC Quality (0=low, 9=high)"),
                        Select(quality_options, value=self.config.get('VNC_QUALITY', 0), id="quality-select"),
                        Label("VNC Compression (0=none, 9=max)"),
                        Select(compression_options, value=self.config.get('VNC_COMPRESSION', 9), id="compression-select"),
                        id="grid-vnc-config"
                        )
            else:
                yield Markdown(StaticText.WEBCONSOLE_LOCAL_RUN)

            yield Button(ButtonLabels.START, variant="primary", id="start")
            yield Button(ButtonLabels.CANCEL, variant="default", id="cancel")

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.control.id == "remote-console-switch":
            markdown = self.query_one("#console-location-label", Markdown)
            remote_opts = self.query_one("#remote-options")
            switch = event.control
            if event.value:
                switch.add_class("switch-on")
                switch.remove_class("switch-off")
                markdown.update(self.text_remote)
                remote_opts.display = True
            else:
                switch.remove_class("switch-on")
                switch.add_class("switch-off")
                markdown.update("Run Web console on local machine")
                remote_opts.display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
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
        elif event.button.id == "cancel":
            self.dismiss(False)
