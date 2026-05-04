"""
Base Modal stuff
"""

import re
from typing import Any, TypeVar

from textual.screen import ModalScreen, Screen
from textual.widgets import ListItem

from ..constants import StaticText

T = TypeVar("T")


class ValueListItem(ListItem):
    """ListItem that holds a value."""

    def __init__(self, *children, value: Any = None, **kwargs) -> None:
        super().__init__(*children, **kwargs)
        self.value = value


class BaseModal(ModalScreen[T]):
    """Base class for all modal screens in the application."""

    BINDINGS = [("escape", "cancel_modal", "Cancel")]

    def action_cancel_modal(self) -> None:
        """Cancel and close the modal."""
        try:
            if self.app.screen is self:
                self.dismiss(None)
        except Exception:
            pass

    def on_click(self, event) -> None:
        """Dismiss the modal when clicking outside the content."""
        if getattr(event, "control", None) is self:
            self.action_cancel_modal()


class BaseDialog(Screen[T]):
    """A base class for dialogs with a cancel binding."""

    BINDINGS = [("escape", "cancel_modal", "Cancel")]

    def action_cancel_modal(self) -> None:
        """Cancel the modal dialog."""
        try:
            if self.app.screen is self:
                self.dismiss(None)
        except Exception:
            pass

    @staticmethod
    def validate_name(name: str) -> str | None:
        """
        Validates a name to be alphanumeric with underscores, not hyphens.
        Returns an error message string if invalid, otherwise None.
        """
        if not name:
            return StaticText.NAME_CANNOT_BE_EMPTY
        if not re.fullmatch(r"^[a-zA-Z0-9_]+$", name):
            return StaticText.NAME_MUST_BE_ALPHANUMERIC
        return None
