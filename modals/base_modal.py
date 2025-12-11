from typing import TypeVar

from textual.app import ComposeResult
from textual.widgets import Button, Label, Input
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen

T = TypeVar("T")

class BaseModal(ModalScreen[T]):
    BINDINGS = [("escape", "cancel_modal", "Cancel")]

    def action_cancel_modal(self) -> None:
        self.dismiss(None)
