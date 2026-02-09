"""
Modal to show how overlay disks work.
"""
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Markdown

from ..constants import ButtonLabels
from .base_modals import BaseModal


class HowToOverlayModal(BaseModal[None]):
    """A modal to display instructions for disk overlays."""

    def compose(self) -> ComposeResult:
        # Load markdown from external file
        docs_path = Path(__file__).parent.parent / "appdocs" / "howto_overlay.md"
        try:
            with open(docs_path) as f:
                content = f.read()
        except FileNotFoundError:
            content = "# Error: Documentation file not found."

        with Vertical(id="howto-overlay-dialog", classes="howto-dialog"):
            with ScrollableContainer(id="howto-overlay-content"):
                yield Markdown(content, id="howto-overlay-markdown")
        with Horizontal(id="dialog-buttons"):
            yield Button(ButtonLabels.CLOSE, id="close-btn", variant="primary")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        self.dismiss()
