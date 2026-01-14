"""
Modals for input device configuration.
"""
import re
from textual.widgets import Select, Button, Label, Input
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from modals.base_modals import BaseModal

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
                yield Button("OK", variant="primary", id="ok-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

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
            yield Label("Input Device")
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
                    yield Button("Add", variant="primary", id="add-input")
                    yield Button("Cancel", variant="default", id="cancel-input")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add":
            input_type = self.query_one("#input-type-select", Select).value
            input_bus = self.query_one("#input-bus-select", Select).value
            if input_type and input_bus:
                self.dismiss({"type": input_type, "bus": input_bus})
            else:
                self.dismiss()
        else:
            self.dismiss()

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
