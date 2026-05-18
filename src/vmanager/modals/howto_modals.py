"""
Modal to show how-to documentation for various topics.
"""

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Markdown

from ..constants import ButtonLabels
from .base_modals import BaseModal


class HowToModal(BaseModal[None]):
    """A modal to display how-to documentation."""

    def __init__(self, doc_name: str) -> None:
        super().__init__()
        self.doc_name = doc_name  # e.g. "disk", "network", "ssh"

    def compose(self) -> ComposeResult:
        docs_path = Path(__file__).parent.parent / "appdocs" / f"howto_{self.doc_name}.md"
        try:
            with open(docs_path) as f:
                content = f.read()
        except FileNotFoundError:
            content = "# Error: Documentation file not found."

        with Vertical(id="howto-dialog"):
            with ScrollableContainer(id="howto-content"):
                yield Markdown(content, id="howto-markdown")
        with Horizontal(id="dialog-buttons"):
            yield Button(ButtonLabels.CLOSE, id="close-btn", variant="primary")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()
