"""
XML Display and Edit Modal
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, TextArea
from textual.widgets.text_area import LanguageDoesNotExist

from ..constants import ButtonLabels, ErrorMessages, StaticText
from .base_modals import BaseModal
from .utils_modals import DirectorySelectionModal


class XMLDisplayModal(BaseModal[str | None]):
    """A modal screen for displaying and editing XML."""

    def __init__(self, xml_content: str, read_only: bool = False, vm_name: str | None = None):
        super().__init__()
        self.xml_content = xml_content
        self.read_only = read_only
        self.vm_name = vm_name

    def compose(self) -> ComposeResult:
        with Vertical(id="xml-display-dialog"):
            if self.vm_name:
                yield Label(self.vm_name, id="xml-display-title")
            text_area = TextArea(
                self.xml_content,
                show_line_numbers=True,
                read_only=self.read_only,
                theme="monokai",
                id="xml-textarea",
            )
            try:
                text_area.language = "xml"
            except LanguageDoesNotExist:
                text_area.language = None
            yield text_area
            with Horizontal(id="xml-buttons"):
                if not self.read_only:
                    yield Button(ButtonLabels.SAVE, variant="primary", id="save-btn")
                yield Button(ButtonLabels.EXPORT, id="export-btn")
                yield Button(ButtonLabels.CLOSE, id="close-btn")

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    @on(Button.Pressed, "#export-btn")
    def export_xml(self) -> None:
        """Export the current XML content to a file."""

        def on_dir_selected(path: str | None) -> None:
            if not path:
                return

            try:
                date_str = datetime.now().strftime("%Y%m%d")
                filename = (
                    f"{self.vm_name}_{date_str}.xml"
                    if self.vm_name
                    else f"config_export_{date_str}.xml"
                )
                export_path = Path(path) / filename

                # If file exists, add a suffix
                count = 1
                while export_path.exists():
                    name = Path(filename).stem
                    export_path = Path(path) / f"{name}_{count}.xml"
                    count += 1

                with open(export_path, "w", encoding="utf-8") as f:
                    f.write(self.query_one("#xml-textarea", TextArea).text)

                self.app.notify(StaticText.XML_EXPORTED_TO.format(path=export_path))
            except Exception as e:
                logging.error(f"Error exporting XML: {e}")
                self.app.notify(
                    ErrorMessages.ERROR_EXPORTING_XML_TEMPLATE.format(error=str(e)), severity="error"
                )

        self.app.push_screen(DirectorySelectionModal(), on_dir_selected)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            textarea = self.query_one("#xml-textarea", TextArea)
            self.dismiss(textarea.text)
        elif event.button.id == "close-btn":
            self.dismiss(None)
