"""
SSH authorized keys management modal (QGA guest-ssh-* commands).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import libvirt
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label

from ..constants import (
    ButtonLabels,
    DialogMessages,
    ErrorMessages,
    StaticText,
    SuccessMessages,
    WarningMessages,
)
from ..guest_agent import (
    GuestAgentError,
    ga_ssh_add_authorized_keys,
    ga_ssh_get_authorized_keys,
    ga_ssh_remove_authorized_keys,
    is_available,
)
from .base_modals import BaseModal
from .utils_modals import (
    ConfirmationDialog,
    FileSelectionModal,
    _confirm_message,
    show_guest_agent_error,
)

# Keys are long; show a shortened form in the table and confirmation prompts.
KEY_DISPLAY_LIMIT = 80


def _shorten_key(key: str, limit: int = KEY_DISPLAY_LIMIT) -> str:
    if len(key) <= limit:
        return key
    return key[:limit] + "..."


class GuestSSHKeysModal(BaseModal[None]):
    """Modal to list, add and remove SSH authorized keys inside the guest."""

    def __init__(self, domain: libvirt.virDomain, vm_name: str) -> None:
        super().__init__()
        self.domain = domain
        self.vm_name = vm_name
        self._loaded_keys: list[str] = []
        self._loaded_user: str | None = None
        self._selected_key: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="guest-ssh-dialog"):
            yield Label(StaticText.GUEST_SSH_TITLE_TEMPLATE.format(vm_name=self.vm_name))
            yield Label(StaticText.GUEST_SSH_USER_LABEL)
            yield Input(value="root", id="guest-ssh-user-input")
            with Horizontal(id="guest-ssh-load-row"):
                yield Button(ButtonLabels.LOAD_KEYS, id="guest-ssh-load")
                yield Button(
                    ButtonLabels.REMOVE_KEY, variant="error", id="guest-ssh-remove", disabled=True
                )
            yield DataTable(id="guest-ssh-keys-table", cursor_type="row")
            yield Label(StaticText.GUEST_SSH_KEY_LABEL)
            with Horizontal(id="guest-ssh-key-row"):
                yield Input(id="guest-ssh-key-input")
                yield Button(ButtonLabels.BROWSE, id="guest-ssh-browse")
            with Horizontal(id="guest-ssh-buttons"):
                yield Button(ButtonLabels.ADD_KEY, variant="primary", id="guest-ssh-add")
                yield Button(ButtonLabels.CANCEL, variant="error", id="guest-ssh-cancel")

    def on_mount(self) -> None:
        table = self.query_one("#guest-ssh-keys-table", DataTable)
        table.add_column(StaticText.GUEST_SSH_KEY_COLUMN)
        table.add_row(StaticText.GUEST_SSH_KEYS_EMPTY)

    @on(DataTable.RowSelected, "#guest-ssh-keys-table")
    def on_key_row_selected(self, event: DataTable.RowSelected) -> None:
        row_key = event.row_key.value
        self._selected_key = row_key if isinstance(row_key, str) else None
        remove_button = self.query_one("#guest-ssh-remove", Button)
        remove_button.disabled = self._selected_key not in self._loaded_keys

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "guest-ssh-cancel":
            self.dismiss()
        elif event.button.id == "guest-ssh-load":
            self._load_keys()
        elif event.button.id == "guest-ssh-add":
            self._add_key()
        elif event.button.id == "guest-ssh-browse":
            self._browse_key_file()
        elif event.button.id == "guest-ssh-remove":
            self._remove_selected_key()

    def _get_user(self) -> str:
        return self.query_one("#guest-ssh-user-input", Input).value.strip()

    def _load_keys(self) -> None:
        if not is_available():
            self.app.show_error_message(ErrorMessages.GUEST_AGENT_NOT_AVAILABLE)
            return

        user = self._get_user()
        if not user:
            self.app.show_error_message(ErrorMessages.GUEST_SSH_USER_REQUIRED)
            return

        def worker() -> None:
            try:
                keys = ga_ssh_get_authorized_keys(self.domain, user)
            except GuestAgentError as exc:
                logging.error("Failed to load SSH keys in %s: %s", self.vm_name, exc)
                error = exc  # captured: 'exc' is deleted when the except block ends

                def on_error() -> None:
                    show_guest_agent_error(self.app, error)

                self.app.call_from_thread(on_error)
                return

            def on_done() -> None:
                if not self.is_mounted:
                    return
                self._loaded_keys = keys
                self._loaded_user = user
                self._populate_table(keys)

            self.app.call_from_thread(on_done)

        self.app.worker_manager.run(worker, name=f"guest_ssh_load_{self.vm_name}")

    def _populate_table(self, keys: list[str]) -> None:
        table = self.query_one("#guest-ssh-keys-table", DataTable)
        table.clear()
        self._selected_key = None
        self.query_one("#guest-ssh-remove", Button).disabled = True
        if not keys:
            table.add_row(StaticText.GUEST_SSH_KEYS_EMPTY)
            return
        for key in keys:
            table.add_row(_shorten_key(key), key=key)

    def _add_key(self) -> None:
        if not is_available():
            self.app.show_error_message(ErrorMessages.GUEST_AGENT_NOT_AVAILABLE)
            return

        user = self._get_user()
        if not user:
            self.app.show_error_message(ErrorMessages.GUEST_SSH_USER_REQUIRED)
            return

        new_key = self.query_one("#guest-ssh-key-input", Input).value.strip()
        if not new_key:
            self.app.show_error_message(ErrorMessages.GUEST_SSH_KEY_REQUIRED)
            return

        # Reject keys that are already present (only meaningful if the list
        # was loaded for the same user)
        if self._loaded_user == user and new_key in self._loaded_keys:
            self.app.show_warning_message(WarningMessages.GUEST_SSH_KEY_ALREADY_PRESENT)
            return

        def worker() -> None:
            try:
                ga_ssh_add_authorized_keys(self.domain, user, [new_key])
            except GuestAgentError as exc:
                logging.error("Failed to add SSH key in %s: %s", self.vm_name, exc)
                error = exc  # captured: 'exc' is deleted when the except block ends

                def on_error() -> None:
                    show_guest_agent_error(self.app, error)

                self.app.call_from_thread(on_error)
                return

            def on_done() -> None:
                if not self.is_mounted:
                    return
                self.app.show_success_message(
                    SuccessMessages.GUEST_SSH_KEY_ADDED_TEMPLATE.format(user=user)
                )
                self.query_one("#guest-ssh-key-input", Input).value = ""
                self._load_keys()

            self.app.call_from_thread(on_done)

        self.app.worker_manager.run(worker, name=f"guest_ssh_add_{self.vm_name}")

    def _browse_key_file(self) -> None:
        """Open the file browser to load a public key file into the input."""
        start_dir = os.path.expanduser("~/.ssh")
        if not os.path.isdir(start_dir):
            start_dir = os.path.expanduser("~")

        def on_file_selected(path: str | None) -> None:
            if not path or not self.is_mounted:
                return
            try:
                content = Path(path).read_text()
            except OSError as exc:
                error = exc  # captured: 'exc' is deleted when the except block ends
                self.app.show_error_message(
                    ErrorMessages.GUEST_SSH_FILE_READ_ERROR_TEMPLATE.format(error=error)
                )
                return
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if not lines:
                self.app.show_error_message(ErrorMessages.GUEST_SSH_FILE_EMPTY)
                return
            self.query_one("#guest-ssh-key-input", Input).value = lines[0]
            if len(lines) > 1:
                self.app.show_warning_message(
                    WarningMessages.GUEST_SSH_FILE_MULTI_KEY_TEMPLATE.format(count=len(lines))
                )

        self.app.push_screen(FileSelectionModal(path=start_dir), on_file_selected)

    def _remove_selected_key(self) -> None:
        key = self._selected_key
        if key is None or key not in self._loaded_keys:
            self.app.show_warning_message(ErrorMessages.GUEST_SSH_KEY_NOT_FOUND)
            return
        # Use the user the keys were loaded for (the input may have changed since)
        user = self._loaded_user or self._get_user()

        def on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return

            def worker() -> None:
                try:
                    ga_ssh_remove_authorized_keys(self.domain, user, [key])
                except GuestAgentError as exc:
                    logging.error("Failed to remove SSH key in %s: %s", self.vm_name, exc)
                    error = exc  # captured: 'exc' is deleted when the except block ends

                    def on_error() -> None:
                        show_guest_agent_error(self.app, error)

                    self.app.call_from_thread(on_error)
                    return

                def on_done() -> None:
                    if not self.is_mounted:
                        return
                    self.app.show_success_message(
                        SuccessMessages.GUEST_SSH_KEY_REMOVED_TEMPLATE.format(user=user)
                    )
                    self._load_keys()

                self.app.call_from_thread(on_done)

            self.app.worker_manager.run(worker, name=f"guest_ssh_remove_{self.vm_name}")

        prompt = _confirm_message(
            DialogMessages.CONFIRM_SSH_KEY_REMOVE_TEMPLATE.format(
                user=user, key=_shorten_key(key, 160)
            )
        )
        self.app.push_screen(ConfirmationDialog(prompt), on_confirm)
