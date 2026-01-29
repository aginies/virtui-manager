"""
Modals for input device configuration and all Input dialog
"""
import re
from textual.widgets import Select, Button, Label, Input
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual import on
from .base_modals import BaseModal
from ..constants import ButtonLabels, StaticText

class InputModal(BaseModal[str | None]):
    """A generic modal for getting text input from the user."""
    def __init__(self, prompt: str, initial_value: str = "", restrict: str | None = None):
        super().__init__()
        self.prompt = prompt
        self.initial_value = initial_value
        self.restrict = restrict

    def compose(self) -> ComposeResult:
        with Vertical(id="add-input-container"):
            yield Label(self.prompt)
            yield Input(value=self.initial_value, id="text-input", restrict=self.restrict)
            with Horizontal():
                yield Button(ButtonLabels.OK, variant="primary", id="ok-btn")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok-btn":
            self.dismiss(self.query_one(Input).value)
        else:
            self.dismiss(None)

class AddInputDeviceModal(BaseModal[None]):
    """A modal for adding a new input device."""

    def __init__(self, available_types: list, available_buses: list):
        super().__init__()
        self.available_types = available_types
        self.available_buses = available_buses

    def compose(self) -> ComposeResult:
        with Vertical(id="add-input-container"):
            yield Label(StaticText.INPUT_DEVICE)
            yield Select(
                [(t, t) for t in self.available_types],
                prompt="Input Type",
                id="input-type-select",
            )
            yield Select(
                [(b, b) for b in self.available_buses],
                prompt="Bus",
                id="input-bus-select",
            )
            with Vertical():
                with Horizontal():
                    yield Button(ButtonLabels.ADD, variant="primary", id="add-input", disabled=True)
                    yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-input")

    @on(Select.Changed)
    def on_select_changed(self) -> None:
        type_select = self.query_one("#input-type-select", Select)
        bus_select = self.query_one("#input-bus-select", Select)
        
        is_type_selected = type_select.value != Select.BLANK
        is_bus_selected = bus_select.value != Select.BLANK
        
        self.query_one("#add-input", Button).disabled = not (is_type_selected and is_bus_selected)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-input":
            input_type = self.query_one("#input-type-select", Select).value
            input_bus = self.query_one("#input-bus-select", Select).value
            if input_type and input_bus:
                self.dismiss({"type": input_type, "bus": input_bus})
            else:
                self.dismiss()
        else:
            self.dismiss()

class AddChannelModal(BaseModal[dict | None]):
    """A modal for adding a new channel device."""

    def compose(self) -> ComposeResult:
        with Vertical(id="add-channel-container"):
            yield Label(StaticText.ADD_CHANNEL_DEVICE)
            yield Select(
                [("unix", "unix"), ("virtio", "virtio"), ("spicevmc", "spicevmc")],
                prompt="Channel Type",
                id="channel-type-select",
                value="unix"
            )
            yield Label(StaticText.STANDARD_TARGET_NAMES)
            yield Select(
                [],
                id="target-preset-select",
                prompt="Select a standard target or type below",
                value=Select.BLANK
            )
            yield Label(StaticText.TARGET_NAME)
            yield Input(placeholder="Target Name (e.g. org.qemu.guest_agent.0)", id="target-name-input")

            with Horizontal():
                yield Button(ButtonLabels.ADD, variant="primary", id="add-channel-btn")
                yield Button(ButtonLabels.CANCEL, variant="default", id="cancel-channel-btn")

    def on_mount(self) -> None:
        # Initialize presets for default type (unix)
        self._update_presets("unix")

    def _update_presets(self, channel_type: str) -> None:
        preset_select = self.query_one("#target-preset-select", Select)
        target_input = self.query_one("#target-name-input", Input)

        options = []
        default_val = ""

        if channel_type == "unix":
            options = [("org.qemu.guest_agent.0", "org.qemu.guest_agent.0")]
            default_val = "org.qemu.guest_agent.0"
        elif channel_type == "spicevmc":
            options = [("com.redhat.spice.0", "com.redhat.spice.0")]
            default_val = "com.redhat.spice.0"
        elif channel_type == "virtio":
            options = [
                ("org.qemu.guest_agent.0", "org.qemu.guest_agent.0"),
                ("org.libguestfs.channel.0", "org.libguestfs.channel.0")
            ]
            default_val = "org.qemu.guest_agent.0"

        preset_select.set_options(options)
        preset_select.value = Select.BLANK 
        target_input.value = default_val

    @on(Select.Changed, "#channel-type-select")
    def on_channel_type_changed(self, event: Select.Changed) -> None:
        self._update_presets(event.value)

    @on(Select.Changed, "#target-preset-select")
    def on_target_preset_changed(self, event: Select.Changed) -> None:
        if event.value and event.value != Select.BLANK:
            self.query_one("#target-name-input", Input).value = event.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-channel-btn":
            channel_type = self.query_one("#channel-type-select", Select).value
            target_name = self.query_one("#target-name-input", Input).value
            if channel_type and target_name:
                self.dismiss({"type": channel_type, "target_name": target_name})
            else:
                pass 
        elif event.button.id == "cancel-channel-btn":
            self.dismiss(None)

def _sanitize_input(input_string: str) -> tuple[str, bool]:
    """
    Sanitise input to alphanumeric, underscore, hyphen only, period.
    Returns a tuple: (sanitized_string, was_modified).
    `was_modified` is True if any characters were removed/changed or input was empty.
    """
    original_stripped = input_string.strip()
    was_modified = False

    if not original_stripped:
        return "", True # Empty input is considered modified

    sanitized = re.sub(r'[^a-zA-Z0-9.-_]', '', original_stripped)

    if len(sanitized) > 64:
        raise ValueError("Sanitized input is too long (max 64 characters)")

    if sanitized != original_stripped:
        was_modified = True

    return sanitized, was_modified

def _sanitize_domain_name(input_string: str) -> tuple[str, bool]:
    """
    Sanitise domain name input to alphanumeric, hyphen, and period only.
    Returns a tuple: (sanitized_string, was_modified).
    `was_modified` is True if any characters were removed/changed or input was empty.
    """
    original_stripped = input_string.strip()
    was_modified = False

    if not original_stripped:
        return "", True # Empty input is considered modified

    # Allow alphanumeric, hyphens, and periods
    sanitized = re.sub(r'[^a-zA-Z0-9.-]', '', original_stripped)

    if len(sanitized) > 64: # Common domain name length limit
        raise ValueError("Sanitized domain name is too long (max 64 characters)")

    if sanitized != original_stripped:
        was_modified = True

    return sanitized, was_modified
