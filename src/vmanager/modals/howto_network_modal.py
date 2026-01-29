"""
Modal to show how to configure networks.
"""
import os
from pathlib import Path
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.widgets import Button, Markdown
from textual import on
from ..constants import ButtonLabels
from .base_modals import BaseModal

class HowToNetworkModal(BaseModal[None]):
    """A modal to display instructions for network configuration."""

    def compose(self) -> ComposeResult:
        # Load markdown from external file
        docs_path = Path(__file__).parent.parent / "appdocs" / "howto_network.md"
        try:
            with open(docs_path, "r") as f:
                content = f.read()
        except FileNotFoundError:
            content = "# Error: Documentation file not found."

        with Vertical(id="howto-network-dialog"):
            with ScrollableContainer(id="howto-network-content"):
                yield Markdown(content, id="howto-network-markdown")
        with Horizontal(id="dialog-buttons"):
            yield Button(ButtonLabels.CLOSE, id="close-btn", variant="primary")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        self.dismiss()
