"""
Modal to show how to use VirtIO-FS.
"""
import os
from pathlib import Path
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.widgets import Button, Markdown
from textual import on
from .base_modals import BaseModal
from ..constants import ButtonLabels

class HowToVirtIOFSModal(BaseModal[None]):
    """A modal to display instructions for using VirtIO-FS."""

    def compose(self) -> ComposeResult:
        # Load markdown from external file
        docs_path = Path(__file__).parent.parent / "appdocs" / "howto_virtiofs.md"
        try:
            with open(docs_path, "r") as f:
                content = f.read()
        except FileNotFoundError:
            content = "# Error: Documentation file not found."

        with Vertical(id="howto-virtiofs-dialog"):
            with ScrollableContainer(id="howto-virtiofs-content"):
                yield Markdown(content, id="howto-virtiofs-markdown")
        with Horizontal(id="dialog-buttons"):
            yield Button(ButtonLabels.CLOSE, id="close-btn", variant="primary")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        self.dismiss()
