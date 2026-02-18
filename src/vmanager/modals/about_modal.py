"""
About modal to display GPL license information
"""

import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, TextArea

from ..constants import AppInfo, ButtonLabels
from .base_modals import BaseModal


class AboutModal(BaseModal[None]):
    """Modal Screen to show About information and GPL license"""

    def __init__(self) -> None:
        super().__init__()
        self.title = "About"

    def compose(self) -> ComposeResult:
        with Vertical(id="about-modal"):
            yield Label("About", id="title")
            text_area = TextArea()
            text_area.load_text(self._get_license_text())
            text_area.read_only = True
            yield text_area
        with Horizontal():
            yield Button(
                ButtonLabels.CLOSE, variant="default", id="close-btn", classes="Buttonpage"
            )

    def _get_license_text(self) -> str:
        """Get the GPL license text with copyright information"""
        current_year = datetime.datetime.now().year

        license_text = f"""Copyright (C) {current_year} {AppInfo.author}

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>

Application Information:
Name: {AppInfo.namecase}
Version: {AppInfo.version}
"""
        return license_text

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss(None)
